"""Speech recognition with faster-whisper, on this machine.

The model is downloaded once on first use (the only time Tutanak needs the network);
after that recognition is fully offline. A GPU is used when its runtime libraries are
present, otherwise the CPU.
"""

from __future__ import annotations

import ctypes
import logging
import os
import site
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from .textutil import fold

log = logging.getLogger(__name__)

GPU_MODEL = "large-v3-turbo"
CPU_MODEL = "small"

# Approximate download sizes, shown before the first (one-time) download.
MODEL_SIZE_MB = {
    "tiny": 75, "base": 145, "small": 485, "medium": 1530, "large-v3": 3090, "large-v3-turbo": 1620,
}  # fmt: skip

# Whisper invents these when it is fed silence or noise (they come from subtitle
# credits in its training data). Compared after folding and stripping punctuation.
_HALLUCINATIONS = (
    "altyazi m k",
    "izlediginiz icin tesekkur ederim",
    "izlediginiz icin tesekkurler",
    "abone olmayi unutmayin",
    "thanks for watching",
    "thank you for watching",
    "subtitles by the amara org community",
    "please subscribe",
)


@dataclass
class Seg:
    start: float
    end: float
    text: str


class Transcriber(Protocol):
    def transcribe(
        self, audio: np.ndarray, *, language: str | None = None, prompt: str | None = None
    ) -> tuple[list[Seg], str | None]:
        """Returns the segments and, when ``language`` was None, the detected language."""
        ...

    def info(self) -> dict[str, Any]: ...


class AsrUnavailable(RuntimeError):
    pass


def is_hallucination(text: str) -> bool:
    plain = " ".join("".join(c if c.isalnum() else " " for c in fold(text)).split())
    return not plain or any(plain == h or plain.startswith(h) for h in _HALLUCINATIONS)


def _nvidia_dirs() -> list[Path]:
    roots = [Path(p) for p in site.getsitepackages()]
    user = site.getusersitepackages()
    if isinstance(user, str):
        roots.append(Path(user))
    sub = "bin" if sys.platform == "win32" else "lib"
    return [d for root in roots for d in (root / "nvidia").glob(f"*/{sub}") if d.is_dir()]


def cuda_ready() -> bool:
    """True when a CUDA device exists and cuBLAS + cuDNN can actually be loaded.

    CTranslate2 aborts the whole process if it selects CUDA and a library is
    missing, so this is checked up front instead of trying and catching.
    """
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() < 1:
            return False
    except Exception:
        return False
    dirs = _nvidia_dirs()
    try:
        if sys.platform == "win32":
            for d in dirs:
                os.add_dll_directory(str(d))
                os.environ["PATH"] = f"{d}{os.pathsep}{os.environ.get('PATH', '')}"
            ctypes.WinDLL("cublas64_12.dll")
            ctypes.WinDLL("cudnn_ops64_9.dll")
        else:
            for name in ("libcublas.so.12", "libcudnn.so.9"):
                try:
                    ctypes.CDLL(name, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    found = next((d / name for d in dirs if (d / name).exists()), None)
                    if not found:
                        raise
                    ctypes.CDLL(str(found), mode=ctypes.RTLD_GLOBAL)
        return True
    except OSError:
        return False


def model_cached(name: str) -> bool:
    """True when the model is already on disk, i.e. loading it needs no network."""
    if os.path.isdir(name):
        return True
    try:
        from faster_whisper.utils import download_model

        download_model(name, local_files_only=True)
        return True
    except Exception:
        return False


class WhisperTranscriber:
    def __init__(self, model: str = "auto", device: str = "auto"):
        self._wanted_model = model
        self._wanted_device = device
        self._model: Any = None
        self._lock = threading.Lock()
        self.model_name: str | None = None
        self.device: str | None = None
        self.error: str | None = None

    def _resolve(self) -> tuple[str, str, str]:
        device = self._wanted_device
        if device == "auto":
            device = "cuda" if cuda_ready() else "cpu"
        elif device == "cuda" and not cuda_ready():
            log.warning("CUDA requested but its libraries are missing; using the CPU")
            device = "cpu"
        model = self._wanted_model
        if model == "auto":
            model = GPU_MODEL if device == "cuda" else CPU_MODEL
        return model, device, "float16" if device == "cuda" else "int8"

    def load(self) -> None:
        """Load the model (downloads it the first time)."""
        with self._lock:
            if self._model is not None:
                return
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise AsrUnavailable(
                    "speech recognition is not installed. Install it with: pip install 'tutanak[asr]'"
                ) from exc
            model, device, compute = self._resolve()
            try:
                self._model = WhisperModel(model, device=device, compute_type=compute)
            except Exception as exc:
                if device != "cuda":
                    self.error = str(exc)
                    raise AsrUnavailable(f"could not load Whisper model {model!r}: {exc}") from exc
                log.warning("GPU model load failed (%s); falling back to the CPU", exc)
                device, compute = "cpu", "int8"
                if self._wanted_model == "auto":
                    model = CPU_MODEL
                self._model = WhisperModel(model, device=device, compute_type=compute)
            self.model_name, self.device = model, device

    def transcribe(
        self, audio: np.ndarray, *, language: str | None = None, prompt: str | None = None
    ) -> tuple[list[Seg], str | None]:
        self.load()
        with self._lock:
            segments, info = self._model.transcribe(
                np.ascontiguousarray(audio, dtype=np.float32),
                language=language,
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 400},
                condition_on_previous_text=False,
                initial_prompt=prompt or None,
            )
            out = [
                Seg(float(s.start), float(s.end), s.text.strip())
                for s in segments
                if not is_hallucination(s.text)
            ]
        detected = None
        if language is None and out and info.language_probability >= 0.5:
            detected = info.language
        return out, detected

    def info(self) -> dict[str, Any]:
        if self._model is not None:
            return {
                "available": True,
                "loaded": True,
                "cached": True,
                "model": self.model_name,
                "device": self.device,
                "size_mb": MODEL_SIZE_MB.get(self.model_name or ""),
            }
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return {
                "available": False,
                "loaded": False,
                "cached": False,
                "model": None,
                "device": None,
            }
        model, device, _ = self._resolve()
        return {
            "available": True,
            "loaded": False,
            "cached": model_cached(model),
            "model": model,
            "device": device,
            "size_mb": MODEL_SIZE_MB.get(model),
        }


class ScriptedTranscriber:
    """Test double: returns prepared segments for each chunk it is given."""

    def __init__(self, script: list[list[Seg]] | None = None, language: str = "tr"):
        self._script = list(script or [])
        self.language = language
        self.calls = 0

    def transcribe(
        self, audio: np.ndarray, *, language: str | None = None, prompt: str | None = None
    ) -> tuple[list[Seg], str | None]:
        self.calls += 1
        segs = self._script.pop(0) if self._script else []
        return segs, (self.language if language is None and segs else None)

    def info(self) -> dict[str, Any]:
        return {
            "available": True,
            "loaded": True,
            "cached": True,
            "model": "scripted",
            "device": "cpu",
            "size_mb": None,
        }
