"""Cuts a continuous audio stream into utterance-sized chunks for speech recognition.

Whisper works on pieces of audio, not on a stream. Cutting at fixed intervals would
split words, so the chunker waits for a pause: once at least ``min_s`` seconds have
piled up it cuts in the middle of the first silence it sees, and if nobody pauses
it cuts at the quietest moment before ``max_s``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import SAMPLE_RATE

FRAME_S = 0.03
SILENCE_FLOOR = 0.0025


@dataclass
class Chunk:
    start: float  # seconds from the start of the meeting
    audio: np.ndarray  # mono float32, what gets transcribed
    mic: np.ndarray | None = None  # the same span from the microphone only
    system: np.ndarray | None = None  # ... and from the system audio only

    @property
    def duration(self) -> float:
        return len(self.audio) / SAMPLE_RATE

    def speaker(self, start: float, end: float) -> str | None:
        """Who spoke in [start, end) of this chunk: "me" (microphone) or "others"."""
        if self.mic is None or self.system is None:
            return None
        a, b = int(start * SAMPLE_RATE), max(int(end * SAMPLE_RATE), int(start * SAMPLE_RATE) + 1)
        mic, system = _rms(self.mic[a:b]), _rms(self.system[a:b])
        if max(mic, system) < SILENCE_FLOOR:
            return None
        return "me" if mic > system else "others"


def _rms(samples: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64)))) if len(samples) else 0.0


def frame_rms(samples: np.ndarray, frame: int) -> np.ndarray:
    count = len(samples) // frame
    if count == 0:
        return np.zeros(0, dtype=np.float32)
    frames = samples[: count * frame].reshape(count, frame)
    return np.sqrt(np.mean(np.square(frames, dtype=np.float32), axis=1))


class Chunker:
    def __init__(
        self,
        *,
        min_s: float = 6.0,
        max_s: float = 28.0,
        silence_s: float = 0.5,
        sample_rate: int = SAMPLE_RATE,
    ):
        self.sr = sample_rate
        self.min_s, self.max_s, self.silence_s = min_s, max_s, silence_s
        self._frame = int(FRAME_S * sample_rate)
        self._mix: list[np.ndarray] = []
        self._mic: list[np.ndarray] = []
        self._sys: list[np.ndarray] = []
        self._buffered = 0  # samples currently held
        self._consumed = 0  # samples already cut off, i.e. the buffer's start position
        self._has_tracks = False

    def feed(
        self, mix: np.ndarray, mic: np.ndarray | None = None, system: np.ndarray | None = None
    ) -> list[Chunk]:
        """Add audio; returns the chunks that are now complete."""
        self._mix.append(np.asarray(mix, dtype=np.float32))
        if mic is not None and system is not None:
            self._has_tracks = True
            self._mic.append(np.asarray(mic, dtype=np.float32))
            self._sys.append(np.asarray(system, dtype=np.float32))
        self._buffered += len(mix)
        chunks: list[Chunk] = []
        while (cut := self._find_cut()) is not None:
            chunks.append(self._take(cut))
        return chunks

    def flush(self) -> Chunk | None:
        """Return whatever is left (call when the recording ends)."""
        if self._buffered < int(0.3 * self.sr):
            return None
        return self._take(self._buffered)

    # ------------------------------------------------------------------

    def _find_cut(self) -> int | None:
        if self._buffered < int(self.min_s * self.sr):
            return None
        mix = self._joined(self._mix)
        rms = frame_rms(mix, self._frame)
        loud = float(np.percentile(rms, 90)) if len(rms) else 0.0
        threshold = max(SILENCE_FLOOR, 0.12 * loud)
        quiet = rms < threshold
        need = max(1, int(round(self.silence_s / FRAME_S)))
        first = int(self.min_s / FRAME_S)

        # First pause that ends after min_s: cut in its middle.
        run = 0
        for i, is_quiet in enumerate(quiet):
            run = run + 1 if is_quiet else 0
            if run >= need and i >= first:
                # Extend to the end of this silence so the cut sits in its middle.
                j = i
                while j + 1 < len(quiet) and quiet[j + 1]:
                    j += 1
                if j + 1 == len(quiet) and self._buffered < int(self.max_s * self.sr):
                    # Silence may still be going on; cutting now is fine, but keep a
                    # little of it as lead-in for the next chunk.
                    return (i + 1) * self._frame
                begin = i - run + 1
                return ((begin + j + 1) // 2) * self._frame

        if self._buffered >= int(self.max_s * self.sr):
            # Nobody paused: cut at the quietest spot in the last few seconds.
            window = int(8.0 / FRAME_S)
            limit = int(self.max_s / FRAME_S)
            lo = max(first, limit - window)
            smooth = np.convolve(rms[:limit], np.ones(5) / 5, mode="same")
            return (lo + int(np.argmin(smooth[lo:limit])) + 1) * self._frame
        return None

    def _joined(self, parts: list[np.ndarray]) -> np.ndarray:
        if len(parts) > 1:
            parts[:] = [np.concatenate(parts)]
        return parts[0] if parts else np.zeros(0, dtype=np.float32)

    def _take(self, samples: int) -> Chunk:
        samples = min(samples, self._buffered)
        start = self._consumed / self.sr

        def split(parts: list[np.ndarray]) -> np.ndarray:
            joined = self._joined(parts)
            head, tail = joined[:samples], joined[samples:]
            parts[:] = [tail] if len(tail) else []
            return head

        audio = split(self._mix)
        mic = split(self._mic) if self._has_tracks else None
        system = split(self._sys) if self._has_tracks else None
        self._buffered -= samples
        self._consumed += samples
        return Chunk(start=start, audio=audio, mic=mic, system=system)


def is_silent(audio: np.ndarray) -> bool:
    """True when a chunk holds no speech-level sound at all (skip recognition)."""
    rms = frame_rms(audio, int(FRAME_S * SAMPLE_RATE))
    return len(rms) == 0 or float(np.percentile(rms, 98)) < SILENCE_FLOOR
