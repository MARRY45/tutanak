"""What live recording and file import share: services, transcription, finalising."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .asr import AsrUnavailable, Transcriber, WhisperTranscriber
from .audio.chunker import Chunk, is_silent
from .audio.files import finish_recording, pcm_duration
from .config import Config, Settings
from .events import EventBus
from .llm import LLM, LLMError, LocalLLM, NoLLM
from .netguard import RemoteEndpointBlocked
from .notes import fill_missing_blocks, output_language, summarize
from .screen.ocr import OcrEngine, create_ocr
from .search import index_meeting
from .store import Store

log = logging.getLogger(__name__)

_UNSET: Any = object()


class Services:
    """Everything the app needs, built from the settings. Tests inject fakes."""

    def __init__(
        self,
        cfg: Config,
        *,
        store: Store | None = None,
        bus: EventBus | None = None,
        transcriber: Transcriber | None = None,
        llm: LLM | None = None,
        ocr: OcrEngine | None = _UNSET,
    ):
        self.cfg = cfg
        self.store = store or Store(cfg.db_path)
        self.bus = bus or EventBus()
        self._fixed_transcriber = transcriber
        self._fixed_llm = llm
        self._fixed_ocr = ocr
        self._transcriber: Transcriber | None = transcriber
        self._llm: LLM | None = llm
        self._ocr: OcrEngine | None = None if ocr is _UNSET else ocr
        self._ocr_built = ocr is not _UNSET
        self.llm_error: str | None = None
        self.progress: dict[str, dict[str, Any]] = {}
        self.jobs = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tutanak-job")
        self._lock = threading.Lock()

    @property
    def settings(self) -> Settings:
        return self.cfg.settings

    @property
    def transcriber(self) -> Transcriber:
        with self._lock:
            if self._transcriber is None:
                self._transcriber = WhisperTranscriber(
                    self.settings.whisper_model, self.settings.whisper_device
                )
            return self._transcriber

    @property
    def llm(self) -> LLM:
        with self._lock:
            if self._llm is None:
                s = self.settings
                try:
                    self._llm = LocalLLM(
                        s.llm_base_url,
                        s.llm_model,
                        s.embed_model,
                        allow_remote=s.allow_remote_llm,
                        timeout=s.llm_timeout_s,
                    )
                    self.llm_error = None
                except RemoteEndpointBlocked as exc:
                    self.llm_error = str(exc)
                    self._llm = NoLLM()
            return self._llm

    @property
    def ocr(self) -> OcrEngine | None:
        with self._lock:
            if not self._ocr_built:
                language = None if self.settings.language == "auto" else self.settings.language
                self._ocr = create_ocr(self.settings.ocr_engine, language)
                self._ocr_built = True
            return self._ocr

    def apply_settings(self, settings: Settings) -> None:
        """Save new settings and rebuild whatever depends on them."""
        self.cfg.save(settings)
        with self._lock:
            self._llm = self._fixed_llm
            self._transcriber = self._fixed_transcriber
            if self._fixed_ocr is _UNSET:
                self._ocr, self._ocr_built = None, False

    def set_progress(
        self, meeting_id: str, stage: str | None, fraction: float | None = None
    ) -> None:
        if stage is None:
            self.progress.pop(meeting_id, None)
        else:
            self.progress[meeting_id] = {"stage": stage, "fraction": fraction}
        self.bus.publish("progress", id=meeting_id, stage=stage, fraction=fraction)


class ChunkTranscriber:
    """Turns audio chunks into stored transcript segments for one meeting."""

    def __init__(
        self,
        services: Services,
        meeting_id: str,
        *,
        language: str | None,
        on_segment: Callable[[dict], None] | None = None,
    ):
        self.services = services
        self.meeting_id = meeting_id
        # None = detect; the first confident detection is kept for the whole meeting,
        # otherwise short or noisy chunks would flip between languages.
        self.language = None if language in (None, "auto") else language
        self._on_segment = on_segment
        self._tail = ""

    def process(self, chunk: Chunk) -> list[dict]:
        if is_silent(chunk.audio):
            return []
        segs, detected = self.services.transcriber.transcribe(
            chunk.audio, language=self.language, prompt=self._tail or None
        )
        store = self.services.store
        if detected and self.language is None:
            self.language = detected
        if self.language and segs:
            meeting = store.get_meeting(self.meeting_id)
            if meeting and not meeting["language"]:
                store.update_meeting(self.meeting_id, language=self.language)
        rows = []
        for seg in segs:
            row = store.add_segment(
                self.meeting_id,
                round(chunk.start + seg.start, 2),
                round(chunk.start + seg.end, 2),
                seg.text,
                chunk.speaker(seg.start, seg.end),
            )
            rows.append(row)
            if self._on_segment:
                self._on_segment(row)
        if segs:
            self._tail = (self._tail + " " + " ".join(s.text for s in segs))[-200:].strip()
        return rows


def finalize(services: Services, meeting_id: str) -> None:
    """Write the notes that are still missing, the summary and the search index."""
    store, settings = services.store, services.settings
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        return
    store.update_meeting(meeting_id, status="processing", error=None)
    services.bus.publish("meeting", id=meeting_id, status="processing")
    language = output_language(settings.notes_language, meeting["language"])
    warning: str | None = None
    try:
        services.set_progress(meeting_id, "notes")
        fill_missing_blocks(
            store,
            services.llm,
            meeting_id,
            interval_s=settings.note_interval_s,
            language=language,
            on_note=lambda note: services.bus.publish("note", id=meeting_id, note=note),
        )
        services.set_progress(meeting_id, "summary")
        summarize(store, services.llm, meeting_id, language=language)
    except LLMError as exc:
        warning = services.llm_error or str(exc)
        log.info("notes for %s not written: %s", meeting_id, warning)
    except Exception as exc:
        log.exception("notes for %s crashed", meeting_id)
        warning = f"{type(exc).__name__}: {exc}"
    try:
        services.set_progress(meeting_id, "index")
        index_meeting(store, services.llm, meeting_id)
    except Exception:
        log.exception("indexing %s failed", meeting_id)
    store.update_meeting(meeting_id, status="ready", error=warning)
    services.set_progress(meeting_id, None)
    services.bus.publish("meeting", id=meeting_id, status="ready")


def fail(services: Services, meeting_id: str, message: str) -> None:
    services.store.update_meeting(meeting_id, status="failed", error=message)
    services.set_progress(meeting_id, None)
    services.bus.publish("meeting", id=meeting_id, status="failed")


def describe_asr_error(exc: Exception) -> str:
    if isinstance(exc, AsrUnavailable):
        return str(exc)
    return f"speech recognition failed: {type(exc).__name__}: {exc}"


def recover_interrupted(services: Services) -> list[str]:
    """Pick up meetings that were cut off by a crash or shutdown.

    Audio is written continuously and segments are stored as they arrive, so
    everything up to the interruption is still there and can be finalised.
    """
    store = services.store
    recovered = []
    for meeting in store.meetings_with_status("recording", "processing"):
        meeting_id = meeting["id"]
        folder = services.cfg.meeting_dir(meeting_id)
        fields: dict[str, Any] = {}
        pcm = folder / "audio.pcm"
        if pcm.exists():
            seconds = pcm_duration(pcm)
            audio = finish_recording(pcm)
            if audio:
                fields["audio_file"] = audio.name
                fields["duration"] = max(seconds, meeting["duration"])
        segments = store.segments(meeting_id)
        if segments and not fields.get("duration") and not meeting["duration"]:
            fields["duration"] = segments[-1]["end"]
        if fields:
            store.update_meeting(meeting_id, **fields)
        if not segments and not fields.get("audio_file") and not meeting["audio_file"]:
            fail(services, meeting_id, "interrupted before anything was recorded")
            continue
        recovered.append(meeting_id)
        services.jobs.submit(finalize, services, meeting_id)
    return recovered
