import json

import httpx
import numpy as np
import pytest
from pydantic import BaseModel

from tutanak.llm import LLMError, LLMUnavailable, LocalLLM, NoLLM, ScriptedLLM
from tutanak.netguard import RemoteEndpointBlocked

ASK = [{"role": "user", "content": "Bütçe ne kadar?"}]


class Budget(BaseModel):
    amount: float
    currency: str = "TL"


class FakeServer:
    """Records what the client sends and answers from a queue or a function."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer(request) if callable(answer) else answer

    @property
    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


def reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})


def client(server, **kwargs) -> LocalLLM:
    kwargs.setdefault("embed_model", "bge-m3")
    return LocalLLM(
        "http://127.0.0.1:11434/v1/", "gemma3:4b", transport=httpx.MockTransport(server), **kwargs
    )


# ----------------------------------------------------------------------------- endpoint


def test_a_remote_endpoint_is_refused_at_construction():
    with pytest.raises(RemoteEndpointBlocked):
        LocalLLM("http://192.168.1.5:11434/v1", "gemma3:4b")


def test_a_remote_endpoint_needs_explicit_consent():
    llm = LocalLLM("http://192.168.1.5:11434/v1", "gemma3:4b", allow_remote=True)
    assert llm.base_url == "http://192.168.1.5:11434/v1"


# ----------------------------------------------------------------------------- chat


def test_chat_json_asks_for_the_schema_and_parses_the_reply():
    server = FakeServer(reply('{"amount": 2.4}'))
    assert client(server).chat_json(ASK, Budget) == Budget(amount=2.4)

    request, body = server.requests[0], server.bodies[0]
    assert request.url == "http://127.0.0.1:11434/v1/chat/completions"
    assert body["model"] == "gemma3:4b"
    assert body["messages"] == ASK
    assert body["stream"] is False
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == Budget.model_json_schema()


def test_structured_output_steps_down_when_the_server_rejects_it():
    def picky(request: httpx.Request) -> httpx.Response:
        if "response_format" in json.loads(request.content):
            return httpx.Response(400, json={"error": {"message": "response_format is not supported"}})
        return reply('{"amount": 2.4}')

    server = FakeServer(picky)
    llm = client(server)
    assert llm.chat_json(ASK, Budget).amount == 2.4
    modes = [body.get("response_format", {}).get("type") for body in server.bodies]
    assert modes == ["json_schema", "json_object", None]

    # The server's limit is remembered: the next call does not probe again.
    assert llm.chat_json(ASK, Budget).amount == 2.4
    assert len(server.requests) == 4
    assert "response_format" not in server.bodies[-1]


def test_an_invalid_reply_gets_one_repair_round():
    server = FakeServer(reply("Bütçe 2,4 milyon."), reply('{"amount": 2.4}'))
    assert client(server).chat_json(ASK, Budget).amount == 2.4

    first, repair = server.bodies
    assert repair["messages"][: len(ASK)] == ASK
    assert repair["messages"][-2] == {"role": "assistant", "content": "Bütçe 2,4 milyon."}
    assert repair["messages"][-1]["role"] == "user"
    assert repair["temperature"] == 0.0


def test_a_reply_that_breaks_the_schema_is_repaired_too():
    server = FakeServer(reply('{"amount": "çok"}'), reply('```json\n{"amount": 2.4}\n```'))
    assert client(server).chat_json(ASK, Budget).amount == 2.4
    assert len(server.requests) == 2


def test_two_invalid_replies_raise_llm_error():
    server = FakeServer(reply("hayır"), reply("yine hayır"))
    with pytest.raises(LLMError) as failed:
        client(server).chat_json(ASK, Budget)
    assert not isinstance(failed.value, LLMUnavailable)
    assert len(server.requests) == 2


def test_chat_strips_reasoning_blocks():
    server = FakeServer(reply("<think>Kullanıcı bütçeyi soruyor.</think>\n2,4 milyon TL."))
    assert client(server).chat(ASK) == "2,4 milyon TL."
    assert "response_format" not in server.bodies[0]


def test_an_empty_reply_is_an_empty_string():
    assert client(FakeServer(reply(""))).chat(ASK) == ""
    null_content = httpx.Response(200, json={"choices": [{"message": {"content": None}}]})
    assert client(FakeServer(null_content)).chat(ASK) == ""


# ----------------------------------------------------------------------------- failures


def test_a_missing_model_means_unavailable():
    server = FakeServer(httpx.Response(404, json={"error": {"message": "model 'gemma3:4b' not found"}}))
    with pytest.raises(LLMUnavailable, match="not found"):
        client(server).chat(ASK)


def test_a_server_error_is_an_llm_error_with_the_servers_message():
    server = FakeServer(httpx.Response(500, json={"error": "out of memory"}))
    with pytest.raises(LLMError, match="out of memory") as failed:
        client(server).chat(ASK)
    assert not isinstance(failed.value, LLMUnavailable)


def test_an_unexpected_body_is_an_llm_error():
    with pytest.raises(LLMError):
        client(FakeServer(httpx.Response(200, json={"choices": []}))).chat(ASK)
    with pytest.raises(LLMError):
        client(FakeServer(httpx.Response(200, text="<html>proxy</html>"))).chat(ASK)


def test_a_timeout_is_an_llm_error_and_does_not_start_the_back_off():
    server = FakeServer(httpx.ReadTimeout("slow"), reply("tamam"))
    llm = client(server)
    with pytest.raises(LLMError) as failed:
        llm.chat(ASK)
    assert not isinstance(failed.value, LLMUnavailable)
    assert llm.chat(ASK) == "tamam"


def test_a_refused_connection_means_unavailable_and_the_next_call_fails_fast():
    server = FakeServer(httpx.ConnectError("connection refused"))
    llm = client(server)
    with pytest.raises(LLMUnavailable):
        llm.chat(ASK)
    assert len(server.requests) == 1

    with pytest.raises(LLMUnavailable):
        llm.chat_json(ASK, Budget)
    with pytest.raises(LLMUnavailable):
        llm.embed(["bütçe"])
    assert len(server.requests) == 1  # backing off: the server was not tried again


def test_a_successful_health_check_ends_the_back_off():
    def models(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "gemma3:4b"}]})

    server = FakeServer(httpx.ConnectError("connection refused"), models, reply("tamam"))
    llm = client(server)
    with pytest.raises(LLMUnavailable):
        llm.chat(ASK)
    assert llm.health()["reachable"]
    assert llm.chat(ASK) == "tamam"


# ----------------------------------------------------------------------------- embeddings


def embeddings(request: httpx.Request) -> httpx.Response:
    texts = json.loads(request.content)["input"]
    # Returned out of order on purpose: "index" says which input each row belongs to.
    data = [{"index": i, "embedding": [float(len(text)), 0.0, 0.0]} for i, text in enumerate(texts)]
    return httpx.Response(200, json={"data": data[::-1]})


def test_embed_returns_normalised_float32_rows_in_input_order():
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"index": 1, "embedding": [0, 0, 5]}, {"index": 0, "embedding": [3, 4, 0]}]},
        )

    server = FakeServer(answer)
    vectors = client(server).embed(["bütçe", "geçiş"])
    assert vectors.dtype == np.float32
    assert np.allclose(vectors, [[0.6, 0.8, 0.0], [0.0, 0.0, 1.0]])
    assert server.requests[0].url.path == "/v1/embeddings"
    assert server.bodies[0] == {"model": "bge-m3", "input": ["bütçe", "geçiş"]}


def test_embed_sends_batches_of_32():
    server = FakeServer(embeddings)
    texts = ["x" * (i + 1) for i in range(70)]
    vectors = client(server).embed(texts)
    assert [len(body["input"]) for body in server.bodies] == [32, 32, 6]
    assert vectors.shape == (70, 3)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)
    assert [body["input"][0] for body in server.bodies] == [texts[0], texts[32], texts[64]]


def test_a_zero_vector_stays_zero_instead_of_becoming_nan():
    zero = httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.0, 0.0]}]})
    assert np.array_equal(client(FakeServer(zero)).embed(["x"]), [[0.0, 0.0]])


def test_embed_is_off_without_an_embedding_model():
    server = FakeServer(embeddings)
    assert client(server, embed_model="").embed(["bütçe"]) is None
    assert client(server).embed([]) is None
    assert server.requests == []


def test_a_missing_embedding_model_means_unavailable():
    server = FakeServer(httpx.Response(404, json={"error": {"message": "model 'bge-m3' not found"}}))
    with pytest.raises(LLMUnavailable, match="bge-m3"):
        client(server).embed(["bütçe"])


# ----------------------------------------------------------------------------- health


def test_health_reports_which_models_are_installed():
    listing = httpx.Response(200, json={"data": [{"id": "gemma3:4b"}, {"id": "bge-m3:latest"}]})
    server = FakeServer(listing)
    health = client(server).health()
    assert server.requests[0].url.path == "/v1/models"
    assert health["reachable"] is True
    assert health["model_available"] is True
    assert health["embed_available"] is True  # "bge-m3" matches "bge-m3:latest"
    assert health["models"] == ["gemma3:4b", "bge-m3:latest"]
    assert (health["base_url"], health["model"]) == ("http://127.0.0.1:11434/v1", "gemma3:4b")


def test_health_reports_missing_models():
    listing = httpx.Response(200, json={"data": [{"id": "gemma3:12b"}]})
    health = client(FakeServer(listing), embed_model="").health()
    assert health["reachable"] is True
    assert health["model_available"] is False
    assert health["embed_available"] is False


def test_health_is_unreachable_when_nothing_listens():
    health = client(FakeServer(httpx.ConnectError("connection refused"))).health()
    assert health["reachable"] is False
    assert health["model_available"] is False
    assert health["models"] == []


# ----------------------------------------------------------------------------- stand-ins


def test_scripted_llm_validates_like_the_real_client():
    llm = ScriptedLLM(lambda messages, schema: '{"amount": 2.4}' if schema else "<think>…</think>2,4 milyon")
    assert llm.chat_json(ASK, Budget).amount == 2.4
    assert llm.chat(ASK) == "2,4 milyon"
    assert llm.calls == [ASK, ASK]
    assert llm.embed(["bütçe"]) is None
    assert llm.health()["embed_available"] is False


def test_scripted_llm_raises_llm_error_on_bad_json():
    llm = ScriptedLLM(lambda messages, schema: "JSON değil")
    with pytest.raises(LLMError) as failed:
        llm.chat_json(ASK, Budget)
    assert not isinstance(failed.value, LLMUnavailable)

    wrong_shape = ScriptedLLM(lambda messages, schema: '{"currency": "TL"}')
    with pytest.raises(LLMError):
        wrong_shape.chat_json(ASK, Budget)


def test_scripted_llm_normalises_its_embeddings():
    llm = ScriptedLLM(lambda messages, schema: "", embedder=lambda texts: np.full((len(texts), 4), 2.0))
    vectors = llm.embed(["a", "b"])
    assert vectors.dtype == np.float32
    assert np.allclose(vectors, 0.5)


def test_no_llm_reports_unavailability_everywhere():
    llm = NoLLM()
    with pytest.raises(LLMUnavailable):
        llm.chat(ASK)
    with pytest.raises(LLMUnavailable):
        llm.chat_json(ASK, Budget)
    assert llm.embed(["bütçe"]) is None
    assert llm.health()["reachable"] is False
