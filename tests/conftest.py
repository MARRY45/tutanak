from __future__ import annotations

import json
import zlib

import numpy as np
import pytest
from pydantic import BaseModel

from tutanak.asr import ScriptedTranscriber
from tutanak.audio import SAMPLE_RATE
from tutanak.config import Config, Settings
from tutanak.llm import Messages, ScriptedLLM
from tutanak.pipeline import Services
from tutanak.schemas import BlockNotes, CatchUp, Overview
from tutanak.textutil import fold, words

REPLIES = {
    BlockNotes: {
        "topic": "Bütçe görüşmesi",
        "points": [{"text": "Bütçe 2,4 milyon TL olarak açıklandı.", "ref": 1}],
        "decisions": [{"text": "Geçişe 20 Ekim'de başlanacak.", "ref": 2}],
        "actions": [{"task": "Raporu hazırlamak", "owner": "Mehmet", "due": "Cuma", "ref": 2}],
        "questions": [],
    },
    Overview: {
        "title": "Bütçe ve Geçiş Toplantısı",
        "tldr": "Bütçe açıklandı ve geçiş tarihi belirlendi.",
        "key_points": ["Bütçe 2,4 milyon TL.", "Geçiş 20 Ekim'de başlıyor."],
    },
    CatchUp: {
        "headline": "Bütçe konuşuluyor.",
        "points": [{"text": "Bütçe açıklandı.", "ref": 1}, {"text": "Uydurma satır.", "ref": 99}],
    },
}


def default_responder(messages: Messages, schema: type[BaseModel] | None) -> str:
    if schema is None:
        return "Geçiş 20 Ekim'de başlıyor [1]. Bu ikinci bilgi uydurma [42]."
    return json.dumps(REPLIES[schema], ensure_ascii=False)


def bag_of_words(texts: list[str]) -> np.ndarray:
    """A tiny deterministic 'embedding': hashed word counts."""
    out = np.zeros((len(texts), 64), dtype=np.float32)
    for row, text in enumerate(texts):
        for word in words(fold(text)):
            out[row, zlib.crc32(word[:5].encode()) % 64] += 1.0
    return out


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(tmp_path / "home", Settings())


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM(default_responder, embedder=bag_of_words)


@pytest.fixture
def services(cfg, llm):
    svc = Services(cfg, transcriber=ScriptedTranscriber(), llm=llm, ocr=None)
    yield svc
    svc.jobs.shutdown(wait=True)
    svc.store.close()


@pytest.fixture
def make_services(cfg, llm):
    """Services around a transcriber (and LLM) chosen by the test."""
    made: list[Services] = []

    def make(transcriber=None, llm=llm) -> Services:
        svc = Services(cfg, transcriber=transcriber or ScriptedTranscriber(), llm=llm, ocr=None)
        made.append(svc)
        return svc

    yield make
    for svc in made:
        svc.jobs.shutdown(wait=True)
        svc.store.close()


def add_meeting(services: Services, segments: list[tuple], **kwargs) -> str:
    """Create a meeting with (start, end, text[, speaker]) segments."""
    meeting_id = services.store.create_meeting(
        kwargs.pop("title", "Test"), "import", status="processing", **kwargs
    )
    for seg in segments:
        services.store.add_segment(meeting_id, seg[0], seg[1], seg[2], seg[3] if len(seg) > 3 else None)
    services.store.update_meeting(meeting_id, duration=segments[-1][1] if segments else 0)
    return meeting_id


def wait_for_jobs(services: Services, timeout: float = 30.0) -> None:
    """Block until everything queued on the single job thread has run."""
    services.jobs.submit(lambda: None).result(timeout=timeout)


def noise(seconds: float, amplitude: float = 0.1, seed: int = 0) -> np.ndarray:
    """Stands in for speech: the chunker only looks at loudness."""
    rng = np.random.default_rng(seed)
    return rng.uniform(-amplitude, amplitude, int(seconds * SAMPLE_RATE)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


class FakeCapture:
    """Stands in for ``AudioCapture``: no device, no thread, no clock.

    Audio arrives only when the test calls ``push``; ``elapsed`` is the audio pushed
    so far. ``FakeCapture.last`` is the instance the session under test created.
    """

    last: FakeCapture | None = None

    def __init__(self, on_audio, *, microphone, system, on_error=None):
        self._on_audio = on_audio
        self.microphone, self.system, self.on_error = microphone, system, on_error
        self.samples = 0
        self.running = False
        FakeCapture.last = self

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    @property
    def elapsed(self) -> float:
        return self.samples / SAMPLE_RATE

    def push(self, mic: np.ndarray, system: np.ndarray) -> None:
        self.samples += len(mic)
        self._on_audio(mic, system)
