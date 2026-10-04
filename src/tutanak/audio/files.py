"""Audio on disk: crash-safe recording, and ffmpeg for import and compression."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import wave
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from . import SAMPLE_RATE

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class FFmpegMissing(RuntimeError):
    pass


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def _require(tool: str = "ffmpeg") -> str:
    path = shutil.which(tool)
    if not path:
        raise FFmpegMissing(f"{tool} was not found on PATH (needed to import recordings)")
    return path


class PcmWriter:
    """Appends 16-bit mono PCM to a file as the meeting runs.

    Raw PCM has no header to finalise, so a crash or power cut loses nothing that
    was already written.
    """

    def __init__(self, path: Path):
        self.path = path
        self._file = open(path, "ab")
        self.samples = 0

    def write(self, audio: np.ndarray) -> None:
        pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
        self._file.write(pcm.tobytes())
        self.samples += len(pcm)

    def close(self) -> None:
        if not self._file.closed:
            self._file.flush()
            self._file.close()


def pcm_duration(path: Path) -> float:
    return path.stat().st_size / 2 / SAMPLE_RATE if path.exists() else 0.0


def finish_recording(pcm: Path) -> Path | None:
    """Turn the raw recording into a playable file; returns it (or None if empty).

    With ffmpeg the result is a small AAC file (about 15 MB per hour); without it, a
    plain WAV. The raw file is removed once the playable one exists.
    """
    if not pcm.exists() or pcm.stat().st_size == 0:
        pcm.unlink(missing_ok=True)
        return None
    ffmpeg = ffmpeg_path()
    if ffmpeg:
        out = pcm.with_suffix(".m4a")
        cmd = [
            ffmpeg, "-y", "-loglevel", "error", "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1",
            "-i", str(pcm), "-c:a", "aac", "-b:a", "32k", "-movflags", "+faststart", str(out),
        ]  # fmt: skip
        done = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW)
        if done.returncode == 0 and out.exists() and out.stat().st_size > 0:
            pcm.unlink(missing_ok=True)
            return out
        out.unlink(missing_ok=True)
    out = pcm.with_suffix(".wav")
    with wave.open(str(out), "wb") as wav, open(pcm, "rb") as raw:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        while data := raw.read(1 << 20):
            wav.writeframes(data)
    pcm.unlink(missing_ok=True)
    return out


def probe(path: Path) -> dict:
    """Duration and stream kinds of a media file."""
    cmd = [
        _require("ffprobe"), "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,codec_name,avg_frame_rate:stream_disposition=attached_pic",
        "-of", "json", str(path),
    ]  # fmt: skip
    done = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW)
    if done.returncode != 0:
        raise ValueError(f"not a readable media file: {done.stderr.strip()[:200]}")
    info = json.loads(done.stdout or "{}")
    streams = info.get("streams", [])
    video = [
        s
        for s in streams
        if s.get("codec_type") == "video"
        and not s.get("disposition", {}).get("attached_pic")  # cover art is not a video
        and s.get("codec_name") not in {"mjpeg", "png"}
    ]
    return {
        "duration": float(info.get("format", {}).get("duration") or 0.0),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "has_video": bool(video),
    }


def decode_audio(path: Path, block_s: float = 1.0) -> Iterator[np.ndarray]:
    """Stream a media file as 16 kHz mono float32 blocks."""
    cmd = [
        _require(), "-loglevel", "error", "-i", str(path), "-vn", "-f", "s16le",
        "-ar", str(SAMPLE_RATE), "-ac", "1", "-",
    ]  # fmt: skip
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW
    )
    assert proc.stdout is not None
    size = int(block_s * SAMPLE_RATE) * 2
    try:
        while True:
            data = proc.stdout.read(size)
            if not data:
                break
            data = data[: len(data) - len(data) % 2]
            yield np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    finally:
        proc.stdout.close()
        proc.kill() if proc.poll() is None else None
        proc.wait()


def encode_audio(source: Path, out: Path) -> Path:
    """Store the audio of an imported file as mono AAC for playback."""
    cmd = [
        _require(), "-y", "-loglevel", "error", "-i", str(source), "-vn", "-ac", "1",
        "-ar", "24000", "-c:a", "aac", "-b:a", "40k", "-movflags", "+faststart", str(out),
    ]  # fmt: skip
    done = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW)
    if done.returncode != 0:
        raise ValueError(f"could not read audio: {done.stderr.strip()[:200]}")
    return out


def extract_frames(source: Path, out_dir: Path, every_s: float = 2.0, width: int = 1600) -> float:
    """Write one frame every ``every_s`` seconds as ``%06d.jpg``; returns the interval."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        _require(), "-y", "-loglevel", "error", "-i", str(source), "-an",
        "-vf", f"fps=1/{every_s},scale='min({width},iw)':-2", "-q:v", "4",
        str(out_dir / "%06d.jpg"),
    ]  # fmt: skip
    done = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW)
    if done.returncode != 0:
        raise ValueError(f"could not read video: {done.stderr.strip()[:200]}")
    return every_s
