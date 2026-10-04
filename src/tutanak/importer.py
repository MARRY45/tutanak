"""Turns an existing recording (audio or video) into a meeting.

Video files get the same treatment as a live screen: frames are sampled, new slides
are detected and read with OCR. The original file is not kept; Tutanak stores a
small audio track and the slide images.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from PIL import Image

from .audio.chunker import Chunker
from .audio.files import decode_audio, encode_audio, extract_frames, probe
from .pipeline import ChunkTranscriber, Services, describe_asr_error, fail, finalize
from .screen.capture import save_slide
from .screen.detector import SlideDetector, thumbnail

log = logging.getLogger(__name__)


def create_import(
    services: Services,
    source: Path,
    *,
    title: str | None = None,
    recorded_at: datetime | None = None,
) -> str:
    """Register the meeting row for a file about to be imported."""
    clean = title.strip() if title and title.strip() else ""
    return services.store.create_meeting(
        clean or source.stem.replace("_", " ").strip() or "Import",
        "import",
        status="processing",
        started_at=recorded_at,
        title_locked=bool(clean),
    )


def run_import(
    services: Services,
    meeting_id: str,
    source: Path,
    *,
    language: str | None = None,
    delete_source: bool = False,
) -> None:
    """Process the file. Runs on the job thread; never raises."""
    store = services.store
    folder = services.cfg.meeting_dir(meeting_id)
    try:
        info = probe(source)
        if not info["has_audio"]:
            raise ValueError("the file has no audio track")
        store.update_meeting(meeting_id, duration=info["duration"])

        services.set_progress(meeting_id, "audio")
        if source.resolve() != (folder / "audio.m4a").resolve():
            encode_audio(source, folder / "audio.m4a")
        store.update_meeting(meeting_id, audio_file="audio.m4a")

        _transcribe(services, meeting_id, source, info["duration"], language)
        if info["has_video"]:
            _slides(services, meeting_id, source, folder)
    except Exception as exc:
        log.exception("import of %s failed", source)
        fail(services, meeting_id, str(exc) or type(exc).__name__)
        return
    finally:
        if delete_source:
            Path(source).unlink(missing_ok=True)
    finalize(services, meeting_id)


def _transcribe(
    services: Services, meeting_id: str, source: Path, duration: float, language: str | None
) -> None:
    transcriber = ChunkTranscriber(
        services,
        meeting_id,
        language=language or services.settings.language,
        on_segment=lambda seg: services.bus.publish("segment", id=meeting_id, segment=seg),
    )
    chunker = Chunker()
    services.set_progress(meeting_id, "transcribing", 0.0)
    try:
        for block in decode_audio(source):
            for chunk in chunker.feed(block):
                transcriber.process(chunk)
                if duration > 0:
                    done = min(1.0, (chunk.start + chunk.duration) / duration)
                    services.set_progress(meeting_id, "transcribing", round(done, 3))
        tail = chunker.flush()
        if tail:
            transcriber.process(tail)
    except Exception as exc:
        raise RuntimeError(describe_asr_error(exc)) from exc


def _slides(services: Services, meeting_id: str, source: Path, folder: Path) -> None:
    services.set_progress(meeting_id, "slides")
    slides_dir = folder / "slides"
    slides_dir.mkdir(exist_ok=True)
    detector = SlideDetector()
    kept = 0
    with tempfile.TemporaryDirectory(prefix="tutanak-frames-") as tmp:
        try:
            every = extract_frames(source, Path(tmp), services.settings.screen_interval_s)
        except ValueError as exc:
            log.warning("no slides from %s: %s", source, exc)
            return
        for index, frame in enumerate(sorted(Path(tmp).glob("*.jpg"))):
            image = Image.open(frame)
            image.load()  # reads the pixels and releases the file handle
            keyframe = detector.feed(index * every, thumbnail(image), image)
            if not keyframe:
                continue
            kept += 1
            path = slides_dir / f"{kept:04d}.jpg"
            save_slide(keyframe.frame, path)
            slide = services.store.add_slide(meeting_id, round(keyframe.t, 1), path.name)
            ocr = services.ocr
            if ocr is not None:
                try:
                    services.store.set_slide_text(slide["id"], ocr.read(path))
                except Exception as exc:
                    log.warning("OCR failed for %s: %s", path, exc)
            services.bus.publish("slide", id=meeting_id, slide=slide)


def retranscribe(services: Services, meeting_id: str) -> None:
    """Redo the transcript and notes from the stored audio (e.g. after a model change)."""
    store = services.store
    meeting = store.get_meeting(meeting_id)
    if not meeting or not meeting["audio_file"]:
        return
    audio = services.cfg.meeting_dir(meeting_id) / meeting["audio_file"]
    store.update_meeting(meeting_id, status="processing", error=None, language=None)
    store.clear_segments(meeting_id)
    store.clear_notes(meeting_id)
    try:
        _transcribe(services, meeting_id, audio, meeting["duration"], None)
    except Exception as exc:
        fail(services, meeting_id, str(exc))
        return
    finalize(services, meeting_id)


def stage_upload(services: Services, filename: str) -> Path:
    """Where an uploaded file is kept while it is being imported."""
    suffix = Path(filename).suffix.lower()[:10] or ".bin"
    uploads = services.cfg.home / "uploads"
    uploads.mkdir(exist_ok=True)
    handle = tempfile.NamedTemporaryFile(dir=uploads, suffix=suffix, delete=False)
    handle.close()
    return Path(handle.name)


def copy_stream(source, target: Path) -> None:
    with open(target, "wb") as out:
        shutil.copyfileobj(source, out, length=1 << 20)
