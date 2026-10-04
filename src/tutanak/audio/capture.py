"""Live capture of the microphone and the system audio (what the others say).

Two device threads fill one ``Track`` each; a mixer thread hands out equally long
blocks of both. Tracks are pinned to the wall clock: a device that starts late,
stalls or disappears is padded with silence, so "second 754 of the meeting" means the
same moment in the microphone track, the system track and the slide timeline.

System audio is recorded through loopback (WASAPI on Windows, a monitor source on
Linux). No virtual audio device and no meeting bot are needed.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import warnings
from collections import deque
from collections.abc import Callable
from typing import Any

import numpy as np

from . import SAMPLE_RATE

log = logging.getLogger(__name__)

BLOCK_S = 0.1
TICK_S = 0.25
STALL_S = 0.6


class CaptureUnavailable(RuntimeError):
    """Audio capture cannot run (missing dependency or no device)."""


def _soundcard() -> Any:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import soundcard
    except Exception as exc:  # ImportError, or no audio backend on this system
        raise CaptureUnavailable(
            f"audio capture is not available ({exc}). Install it with: pip install 'tutanak[capture]'"
        ) from exc
    return soundcard


def _init_com() -> None:
    """Each thread that touches WASAPI must have COM initialised."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED


def preload() -> bool:
    """Import the audio backend on the calling (main) thread. True when available."""
    try:
        _soundcard()
        return True
    except CaptureUnavailable:
        return False


def list_devices() -> dict[str, Any]:
    """Microphones and speakers that can be recorded."""
    sc = _soundcard()
    _init_com()

    def safe(getter: Callable[[], Any]) -> str | None:
        try:
            return str(getter().id)
        except Exception:
            return None

    return {
        "microphones": [
            {"id": str(m.id), "name": m.name} for m in sc.all_microphones(include_loopback=False)
        ],
        "speakers": [{"id": str(s.id), "name": s.name} for s in sc.all_speakers()],
        "default_microphone": safe(sc.default_microphone),
        "default_speaker": safe(sc.default_speaker),
    }


class Track:
    """Samples from one device, kept aligned to the wall clock."""

    def __init__(self) -> None:
        self._chunks: deque[np.ndarray] = deque()
        self._lock = threading.Lock()
        self.written = 0
        self.read_pos = 0

    @property
    def available(self) -> int:
        return self.written - self.read_pos

    def append(self, samples: np.ndarray) -> None:
        if len(samples):
            with self._lock:
                self._chunks.append(samples)
                self.written += len(samples)

    def pad_to(self, position: int) -> None:
        """Fill with silence up to ``position`` samples since the start."""
        missing = position - self.written
        if missing > 0:
            self.append(np.zeros(missing, dtype=np.float32))

    def read(self, count: int) -> np.ndarray:
        out = np.empty(count, dtype=np.float32)
        filled = 0
        with self._lock:
            while filled < count:
                head = self._chunks[0]
                take = min(len(head), count - filled)
                out[filled : filled + take] = head[:take]
                if take == len(head):
                    self._chunks.popleft()
                else:
                    self._chunks[0] = head[take:]
                filled += take
            self.read_pos += count
        return out


class AudioCapture:
    """Records microphone and/or system audio and delivers aligned mono blocks.

    ``on_audio(mic, system)`` is called from the mixer thread with two float32 arrays
    of equal length at 16 kHz. A source that is switched off is delivered as silence.
    """

    def __init__(
        self,
        on_audio: Callable[[np.ndarray, np.ndarray], None],
        *,
        microphone: str | None = "default",
        system: str | None = "default",
        on_error: Callable[[str, str], None] | None = None,
        sample_rate: int = SAMPLE_RATE,
    ):
        if microphone is None and system is None:
            raise CaptureUnavailable("nothing to record: both microphone and system audio are off")
        self.sr = sample_rate
        self._on_audio = on_audio
        self._on_error = on_error
        self._sources = {"mic": microphone, "system": system}
        self._tracks = {name: Track() for name, device in self._sources.items() if device}
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._t0 = 0.0
        self.samples_out = 0

    # ------------------------------------------------------------------ control

    def start(self) -> None:
        sc = _soundcard()
        _init_com()
        # Fail early, on the caller's thread, when a device cannot be opened.
        for name in self._tracks:
            self._resolve(sc, name)
        self._t0 = time.monotonic()
        for name in self._tracks:
            thread = threading.Thread(
                target=self._record, args=(name,), name=f"tutanak-{name}", daemon=True
            )
            thread.start()
            self._threads.append(thread)
        mixer = threading.Thread(target=self._mix, name="tutanak-mixer", daemon=True)
        mixer.start()
        self._threads.append(mixer)

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=5)
        self._threads.clear()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._t0 if self._t0 else 0.0

    # ------------------------------------------------------------------ devices

    def _resolve(self, sc: Any, name: str) -> tuple[Any, str]:
        """Open the device for a source. Returns (device, id used to detect changes)."""
        wanted = self._sources[name]
        try:
            if name == "mic":
                device = (
                    sc.default_microphone() if wanted == "default" else sc.get_microphone(wanted)
                )
            else:
                speaker_id = str(sc.default_speaker().id) if wanted == "default" else wanted
                device = sc.get_microphone(speaker_id, include_loopback=True)
        except Exception as exc:
            label = "microphone" if name == "mic" else "system audio"
            raise CaptureUnavailable(f"cannot open {label} device: {exc}") from exc
        return device, str(device.id)

    def _default_changed(self, sc: Any, name: str, current_id: str) -> bool:
        if self._sources[name] != "default":
            return False
        try:
            default = sc.default_microphone() if name == "mic" else sc.default_speaker()
            return str(default.id) != current_id
        except Exception:
            return False

    def _record(self, name: str) -> None:
        """Device thread: keep recording, reopening when the default device changes."""
        _init_com()
        sc = _soundcard()
        track = self._tracks[name]
        block = int(BLOCK_S * self.sr)
        failures = 0
        while not self._stop.is_set():
            try:
                device, device_id = self._resolve(sc, name)
                channels = min(2, getattr(device, "channels", 1) or 1)
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    with device.recorder(samplerate=self.sr, channels=channels) as recorder:
                        last_check = time.monotonic()
                        first = True
                        while not self._stop.is_set():
                            data = recorder.record(numframes=block)
                            mono = data.mean(axis=1).astype(np.float32) if data.ndim > 1 else data
                            if first:
                                # Pin the first samples to "now" on the meeting clock.
                                now = int((time.monotonic() - self._t0) * self.sr)
                                track.pad_to(now - len(mono))
                                first = False
                            track.append(mono)
                            failures = 0
                            if time.monotonic() - last_check > 2.0:
                                last_check = time.monotonic()
                                if self._default_changed(sc, name, device_id):
                                    log.info("default %s device changed, reopening", name)
                                    break
            except Exception as exc:
                failures += 1
                log.warning("%s capture error: %s", name, exc)
                if failures == 1 and self._on_error:
                    self._on_error(name, str(exc))
                # The mixer pads this track with silence while we wait to retry.
                self._stop.wait(min(5.0, 0.5 * failures))

    # ------------------------------------------------------------------ mixing

    def _mix(self) -> None:
        tracks = self._tracks
        while True:
            stopping = self._stop.is_set()
            expected = int((time.monotonic() - self._t0) * self.sr)
            for track in tracks.values():
                if track.written < expected - int(STALL_S * self.sr):
                    track.pad_to(expected - int(0.2 * self.sr))
            count = min(track.available for track in tracks.values())
            if count > 0:
                silence = None
                blocks = {}
                for name in ("mic", "system"):
                    if name in tracks:
                        blocks[name] = tracks[name].read(count)
                    else:
                        silence = silence if silence is not None else np.zeros(count, np.float32)
                        blocks[name] = silence
                self.samples_out += count
                try:
                    self._on_audio(blocks["mic"], blocks["system"])
                except Exception:
                    log.exception("audio consumer failed")
            if stopping:
                return
            time.sleep(TICK_S)
