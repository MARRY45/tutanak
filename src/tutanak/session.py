"""A live recording: capture, transcription, slides and notes running side by side.

Threads, and what hands work to what:

    microphone ─┐
                ├─ mixer ──► audio file (always) ──► chunker ──► ASR worker ──► note-taker
    system  ────┘
    screen ──► slide detector ──► OCR worker

Audio goes to disk before anything else touches it, so a slow model or a failing
component can delay the notes but never lose the recording.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from .audio import SAMPLE_RATE
from .audio.capture import AudioCapture
from .audio.chunker import Chunk, Chunker
from .audio.files import PcmWriter, finish_recording
from .notes import NoteTaker, output_language
from .pipeline import ChunkTranscriber, Services, describe_asr_error, finalize
from .screen.capture import ScreenCapture

log = logging.getLogger(__name__)


class SessionError(RuntimeError):
    pass


class RecordingSession:
    def __init__(
        self,
        services: Services,
        *,
        title: str | None = None,
        microphone: str | None = "default",
        system: str | None = "default",
        screen: bool = False,
        monitor: int = 1,
        language: str | None = None,
        capture_factory: Callable[..., Any] = AudioCapture,
    ):
        if not microphone and not system:
            raise SessionError("nothing to record: both microphone and system audio are off")
        self.services = services
        settings = services.settings
        started = datetime.now()
        self.meeting_id = services.store.create_meeting(
            title.strip() if title and title.strip() else started.strftime("%d.%m.%Y %H:%M"),
            "live",
            started_at=started,
            title_locked=bool(title and title.strip()),
        )
        self.folder: Path = services.cfg.meeting_dir(self.meeting_id)
        self._both_sources = bool(microphone and system)
        self._pcm = PcmWriter(self.folder / "audio.pcm")
        self._chunker = Chunker()
        self._asr_queue: queue.Queue[Chunk | None] = queue.Queue()
        self._ocr_queue: queue.Queue[dict | None] = queue.Queue()
        self._queued_s = 0.0
        self._levels = {"mic": 0.0, "system": 0.0}
        self._last_level = 0.0
        self.warnings: list[str] = []
        self.model_state = "idle"
        self._stopped = threading.Event()
        self.done = threading.Event()

        self._transcriber = ChunkTranscriber(
            services,
            self.meeting_id,
            language=language or settings.language,
            on_segment=self._on_segment,
        )
        self._notes = NoteTaker(
            services.store,
            services.llm,
            self.meeting_id,
            interval_s=settings.note_interval_s,
            language=self._notes_language,
            on_note=lambda note: services.bus.publish("note", id=self.meeting_id, note=note),
        )
        self._capture = capture_factory(
            self._on_audio, microphone=microphone, system=system, on_error=self._on_capture_error
        )
        self._screen: ScreenCapture | None = None
        if screen:
            self._screen = ScreenCapture(
                self.folder / "slides",
                self._on_slide,
                monitor=monitor,
                interval_s=settings.screen_interval_s,
                clock=lambda: self._capture.elapsed,
            )
        self._asr_thread = threading.Thread(
            target=self._asr_worker, name="tutanak-asr", daemon=True
        )
        self._ocr_thread = threading.Thread(
            target=self._ocr_worker, name="tutanak-ocr", daemon=True
        )

    # ------------------------------------------------------------------ control

    def start(self) -> None:
        try:
            self._capture.start()
            if self._screen:
                try:
                    self._screen.start()
                except Exception as exc:
                    self._screen = None
                    self._warn(f"screen capture is off: {exc}")
        except Exception:
            self._pcm.close()
            self._notes.close()
            self.services.store.delete_meeting(self.meeting_id)
            raise
        self._asr_thread.start()
        self._ocr_thread.start()
        self.services.bus.publish("recording", id=self.meeting_id, state="recording")

    def stop(self) -> None:
        """Stop capturing. Remaining transcription and the notes finish in the background."""
        if self._stopped.is_set():
            return
        self._stopped.set()
        duration = self._capture.elapsed
        self._capture.stop()
        if self._screen:
            self._screen.stop()
        tail = self._chunker.flush()
        if tail:
            self._enqueue(tail)
        self._asr_queue.put(None)
        self.services.store.update_meeting(self.meeting_id, status="processing", duration=duration)
        self.services.bus.publish("recording", id=self.meeting_id, state="stopped")
        threading.Thread(target=self._finish, name="tutanak-finish", daemon=True).start()

    def _finish(self) -> None:
        services, meeting_id = self.services, self.meeting_id
        try:
            services.set_progress(meeting_id, "transcribing")
            self._asr_thread.join()
            self._ocr_queue.put(None)
            self._ocr_thread.join()
            self._notes.close()
            self._pcm.close()
            audio = finish_recording(self.folder / "audio.pcm")
            duration = self._pcm.samples / SAMPLE_RATE
            services.store.update_meeting(
                meeting_id, audio_file=audio.name if audio else None, duration=duration
            )
            finalize(services, meeting_id)
            if self.warnings and not (services.store.get_meeting(meeting_id) or {}).get("error"):
                services.store.update_meeting(meeting_id, error="; ".join(self.warnings))
        except Exception:
            log.exception("finishing meeting %s failed", meeting_id)
        finally:
            self.done.set()

    @property
    def active(self) -> bool:
        return not self._stopped.is_set()

    @property
    def elapsed(self) -> float:
        return self._capture.elapsed

    def state(self) -> dict[str, Any]:
        return {
            "id": self.meeting_id,
            "active": self.active,
            "elapsed": self.elapsed,
            "screen": self._screen is not None,
            "model_state": self.model_state,
            "backlog_s": round(self._queued_s, 1),
            "notes_available": self._notes.available,
            "levels": dict(self._levels),
            "warnings": list(self.warnings),
        }

    def bookmark(self, label: str = "") -> dict[str, Any]:
        mark = self.services.store.add_bookmark(self.meeting_id, round(self.elapsed, 1), label)
        self.services.bus.publish("bookmark", id=self.meeting_id, bookmark=mark)
        return mark

    # ------------------------------------------------------------------ audio path

    def _notes_language(self) -> str:
        return output_language(self.services.settings.notes_language, self._transcriber.language)

    def _warn(self, message: str) -> None:
        log.warning(message)
        if message not in self.warnings:
            self.warnings.append(message)
            self.services.bus.publish("warning", id=self.meeting_id, message=message)

    def _on_capture_error(self, source: str, message: str) -> None:
        label = "microphone" if source == "mic" else "system audio"
        self._warn(f"{label}: {message}")

    def _enqueue(self, chunk: Chunk) -> None:
        self._queued_s += chunk.duration
        self._asr_queue.put(chunk)

    def _on_audio(self, mic: np.ndarray, system: np.ndarray) -> None:
        mix = np.clip(mic + system, -1.0, 1.0)
        self._pcm.write(mix)
        self._levels["mic"] = max(self._levels["mic"] * 0.6, float(np.abs(mic).max(initial=0.0)))
        self._levels["system"] = max(
            self._levels["system"] * 0.6, float(np.abs(system).max(initial=0.0))
        )
        now = time.monotonic()
        if now - self._last_level > 0.5:
            self._last_level = now
            self.services.bus.publish(
                "level",
                id=self.meeting_id,
                mic=round(self._levels["mic"], 3),
                system=round(self._levels["system"], 3),
                elapsed=round(self.elapsed, 1),
                backlog_s=round(self._queued_s, 1),
            )
        if self._both_sources:
            chunks = self._chunker.feed(mix, mic, system)
        else:
            chunks = self._chunker.feed(mix)
        for chunk in chunks:
            self._enqueue(chunk)

    def _asr_worker(self) -> None:
        services = self.services
        try:
            self.model_state = "loading"
            services.bus.publish("model", id=self.meeting_id, state="loading")
            load = getattr(services.transcriber, "load", None)
            if load:
                load()
            self.model_state = "ready"
            services.bus.publish("model", id=self.meeting_id, state="ready")
        except Exception as exc:
            self.model_state = "failed"
            self._warn(describe_asr_error(exc))
        while (chunk := self._asr_queue.get()) is not None:
            if self.model_state == "ready":
                try:
                    self._transcriber.process(chunk)
                except Exception as exc:
                    self._warn(describe_asr_error(exc))
            self._queued_s = max(0.0, self._queued_s - chunk.duration)

    def _on_segment(self, segment: dict) -> None:
        self.services.bus.publish("segment", id=self.meeting_id, segment=segment)
        self._notes.add_segment(segment)

    # ------------------------------------------------------------------ slides

    def _on_slide(self, t: float, path: Path) -> None:
        slide = self.services.store.add_slide(self.meeting_id, round(t, 1), path.name)
        self.services.bus.publish("slide", id=self.meeting_id, slide=slide)
        self._ocr_queue.put({**slide, "path": path})

    def _ocr_worker(self) -> None:
        while (slide := self._ocr_queue.get()) is not None:
            ocr = self.services.ocr
            if ocr is None:
                continue
            try:
                text = ocr.read(slide["path"])
            except Exception as exc:
                log.warning("OCR failed for %s: %s", slide["path"], exc)
                continue
            self.services.store.set_slide_text(slide["id"], text)
            self.services.bus.publish(
                "slide_text", id=self.meeting_id, slide_id=slide["id"], text=text
            )


class SessionManager:
    """At most one live recording at a time."""

    def __init__(self, services: Services, capture_factory: Callable[..., Any] = AudioCapture):
        self.services = services
        self._capture_factory = capture_factory
        self._lock = threading.Lock()
        self.current: RecordingSession | None = None

    def start(self, **options: Any) -> RecordingSession:
        with self._lock:
            if self.current and self.current.active:
                raise SessionError("a recording is already running")
            session = RecordingSession(
                self.services, capture_factory=self._capture_factory, **options
            )
            session.start()
            self.current = session
            return session

    def stop(self) -> RecordingSession:
        with self._lock:
            if not self.current or not self.current.active:
                raise SessionError("no recording is running")
            session = self.current
        session.stop()
        return session

    def active(self) -> RecordingSession | None:
        session = self.current
        return session if session and session.active else None
