"""Watches one monitor during a meeting and keeps a screenshot of each new slide."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image

from .detector import SlideDetector, thumbnail

log = logging.getLogger(__name__)

MAX_WIDTH = 1600


class ScreenUnavailable(RuntimeError):
    pass


def _mss() -> Any:
    try:
        import mss
    except ImportError as exc:
        raise ScreenUnavailable(
            "screen capture is not available. Install it with: pip install 'tutanak[capture]'"
        ) from exc
    return mss


def list_monitors() -> list[dict[str, Any]]:
    with _mss().MSS() as grabber:
        return [
            {
                "index": i,
                "width": m["width"],
                "height": m["height"],
                "primary": bool(m.get("is_primary", i == 1)),
            }
            for i, m in enumerate(grabber.monitors)
            if i > 0  # index 0 is the union of all monitors
        ]


def save_slide(image: Image.Image, path: Path) -> None:
    if image.width > MAX_WIDTH:
        height = round(image.height * MAX_WIDTH / image.width)
        image = image.resize((MAX_WIDTH, height), Image.Resampling.LANCZOS)
    image.convert("RGB").save(path, "JPEG", quality=82, optimize=True)


class ScreenCapture:
    """Grabs the monitor every ``interval_s`` seconds; calls back for each new slide.

    ``on_slide(t, path)`` runs on the capture thread with the meeting time at which
    the slide appeared and the saved JPEG.
    """

    def __init__(
        self,
        out_dir: Path,
        on_slide: Callable[[float, Path], None],
        *,
        monitor: int = 1,
        interval_s: float = 2.0,
        clock: Callable[[], float],
    ):
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._on_slide = on_slide
        self._monitor = monitor
        self._interval = interval_s
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._count = 0

    def start(self) -> None:
        monitors = list_monitors()  # raises ScreenUnavailable early, on the caller's thread
        if not any(m["index"] == self._monitor for m in monitors):
            self._monitor = 1
        self._thread = threading.Thread(target=self._run, name="tutanak-screen", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        detector = SlideDetector()
        try:
            with _mss().MSS() as grabber:
                region = grabber.monitors[self._monitor]
                while not self._stop.is_set():
                    began = time.monotonic()
                    try:
                        shot = grabber.grab(region)
                        image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                        keyframe = detector.feed(self._clock(), thumbnail(image), image)
                        if keyframe:
                            self._count += 1
                            path = self.out_dir / f"{self._count:04d}.jpg"
                            save_slide(keyframe.frame, path)
                            self._on_slide(keyframe.t, path)
                    except Exception:
                        log.exception("screen capture failed for one frame")
                    self._stop.wait(max(0.1, self._interval - (time.monotonic() - began)))
        except Exception:
            log.exception("screen capture stopped")
