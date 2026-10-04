"""Client for a local OpenAI-compatible model server (Ollama, LM Studio, llama.cpp).

The endpoint must be on this machine unless the user explicitly allows otherwise;
see ``netguard``. Proxy environment variables are ignored on purpose so requests
to localhost can never be routed through a proxy.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol, TypeVar

import httpx
import numpy as np
from pydantic import BaseModel, ValidationError

from .netguard import assert_local
from .textutil import extract_json, strip_reasoning

T = TypeVar("T", bound=BaseModel)
Messages = list[dict[str, str]]
RETRY_AFTER_S = 10.0


class LLMError(RuntimeError):
    """The model answered, but not with something usable."""


class LLMUnavailable(LLMError):
    """No model server, or the configured model is not installed."""


class LLM(Protocol):
    model: str

    def chat(self, messages: Messages, *, temperature: float = 0.2) -> str: ...

    def chat_json(self, messages: Messages, schema: type[T]) -> T: ...

    def embed(self, texts: list[str]) -> np.ndarray | None: ...

    def health(self) -> dict[str, Any]: ...


def _normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1.0, norms)


def parse_reply(reply: str, schema: type[T]) -> T:
    return schema.model_validate(extract_json(reply))


class LocalLLM:
    _JSON_MODES = ("json_schema", "json_object", "none")

    def __init__(
        self,
        base_url: str,
        model: str,
        embed_model: str = "",
        *,
        allow_remote: bool = False,
        timeout: float = 180.0,
        transport: httpx.BaseTransport | None = None,
    ):
        assert_local(base_url, allow_remote=allow_remote)
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.embed_model = embed_model
        self._json_mode = 0
        self._down_until = 0.0
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=3.0),
            transport=transport,
            trust_env=False,
            headers={"Authorization": "Bearer local"},
        )

    # ------------------------------------------------------------------ http

    def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        if time.monotonic() < self._down_until:
            raise LLMUnavailable(f"no model server at {self.base_url}")
        try:
            return self._client.post(path, json=payload)
        except httpx.ConnectError as exc:
            # A refused connection can take seconds to report; do not pay that on
            # every call while the server is simply not running.
            self._down_until = time.monotonic() + RETRY_AFTER_S
            raise LLMUnavailable(f"no model server at {self.base_url}") from exc
        except httpx.TimeoutException as exc:
            raise LLMError(f"model server timed out ({exc.__class__.__name__})") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"no model server at {self.base_url}") from exc

    @staticmethod
    def _error_text(response: httpx.Response) -> str:
        try:
            error = response.json().get("error")
            if isinstance(error, dict):
                return str(error.get("message", error))
            return str(error or response.text)
        except ValueError:
            return response.text[:300]

    def _complete(
        self, messages: Messages, temperature: float, schema: type[BaseModel] | None
    ) -> str:
        while True:
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "stream": False,
            }
            mode = self._JSON_MODES[self._json_mode] if schema else "none"
            if mode == "json_schema":
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
                }
            elif mode == "json_object":
                payload["response_format"] = {"type": "json_object"}
            response = self._post("/chat/completions", payload)
            if response.status_code == 404:
                raise LLMUnavailable(
                    f"model {self.model!r} is not available: {self._error_text(response)}"
                )
            if response.status_code == 400 and mode != "none":
                # This server does not support the structured-output mode; step down.
                self._json_mode += 1
                continue
            if response.status_code >= 400:
                raise LLMError(
                    f"model server error {response.status_code}: {self._error_text(response)}"
                )
            try:
                content = response.json()["choices"][0]["message"]["content"]
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise LLMError("unexpected reply from model server") from exc
            return content or ""

    # ------------------------------------------------------------------ api

    def chat(self, messages: Messages, *, temperature: float = 0.2) -> str:
        return strip_reasoning(self._complete(messages, temperature, None))

    def chat_json(self, messages: Messages, schema: type[T]) -> T:
        """Ask for a JSON object matching ``schema``; one repair round on failure."""
        reply = self._complete(messages, 0.1, schema)
        try:
            return parse_reply(reply, schema)
        except (ValueError, ValidationError) as first:
            repair = [
                *messages,
                {"role": "assistant", "content": reply},
                {
                    "role": "user",
                    "content": f"That reply was not valid ({str(first)[:300]}). "
                    "Reply again with only the corrected JSON object.",
                },
            ]
            reply = self._complete(repair, 0.0, schema)
            try:
                return parse_reply(reply, schema)
            except (ValueError, ValidationError) as second:
                raise LLMError(f"model did not return valid JSON: {str(second)[:200]}") from second

    def embed(self, texts: list[str]) -> np.ndarray | None:
        if not self.embed_model or not texts:
            return None
        vectors: list[list[float]] = []
        for i in range(0, len(texts), 32):
            response = self._post(
                "/embeddings", {"model": self.embed_model, "input": texts[i : i + 32]}
            )
            if response.status_code >= 400:
                raise LLMUnavailable(
                    f"embedding model {self.embed_model!r}: {self._error_text(response)}"
                )
            try:
                data = sorted(response.json()["data"], key=lambda d: d.get("index", 0))
                vectors.extend(d["embedding"] for d in data)
            except (ValueError, KeyError, TypeError) as exc:
                raise LLMError("unexpected embeddings reply") from exc
        return _normalize(np.asarray(vectors, dtype=np.float32))

    def health(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "base_url": self.base_url,
            "model": self.model,
            "embed_model": self.embed_model,
            "reachable": False,
            "model_available": False,
            "embed_available": False,
            "models": [],
        }
        try:
            response = self._client.get("/models", timeout=3.0)
            names = [m["id"] for m in response.json().get("data", [])]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return info
        self._down_until = 0.0

        def has(name: str) -> bool:
            return bool(name) and any(n == name or n == f"{name}:latest" for n in names)

        info.update(
            reachable=True,
            models=names,
            model_available=has(self.model),
            embed_available=has(self.embed_model),
        )
        return info


class ScriptedLLM:
    """Deterministic stand-in used by the tests and the offline demo.

    ``responder`` receives the messages and the expected schema (``None`` for free
    text) and returns the reply text, exactly as a model server would.
    """

    def __init__(
        self,
        responder: Callable[[Messages, type[BaseModel] | None], str],
        *,
        embedder: Callable[[list[str]], np.ndarray] | None = None,
        model: str = "scripted",
    ):
        self.model = model
        self._responder = responder
        self._embedder = embedder
        self.calls: list[Messages] = []

    def chat(self, messages: Messages, *, temperature: float = 0.2) -> str:
        self.calls.append(messages)
        return strip_reasoning(self._responder(messages, None))

    def chat_json(self, messages: Messages, schema: type[T]) -> T:
        self.calls.append(messages)
        try:
            return parse_reply(self._responder(messages, schema), schema)
        except (ValueError, ValidationError) as exc:
            raise LLMError(f"model did not return valid JSON: {exc}") from exc

    def embed(self, texts: list[str]) -> np.ndarray | None:
        if not self._embedder or not texts:
            return None
        return _normalize(np.asarray(self._embedder(texts), dtype=np.float32))

    def health(self) -> dict[str, Any]:
        return {
            "base_url": "scripted",
            "model": self.model,
            "embed_model": "scripted" if self._embedder else "",
            "reachable": True,
            "model_available": True,
            "embed_available": self._embedder is not None,
            "models": [self.model],
        }


class NoLLM:
    """Used when no model server is configured; every call reports unavailability."""

    model = ""

    def chat(self, messages: Messages, *, temperature: float = 0.2) -> str:
        raise LLMUnavailable("no model server configured")

    def chat_json(self, messages: Messages, schema: type[T]) -> T:
        raise LLMUnavailable("no model server configured")

    def embed(self, texts: list[str]) -> np.ndarray | None:
        return None

    def health(self) -> dict[str, Any]:
        return {
            "base_url": "",
            "model": "",
            "embed_model": "",
            "reachable": False,
            "model_available": False,
            "embed_available": False,
            "models": [],
        }
