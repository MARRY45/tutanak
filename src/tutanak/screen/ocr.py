"""Reads the text on a slide, on this machine.

Two engines:

- ``windows``: the OCR built into Windows 10/11. Fast, no model download, and reads
  every language installed in Windows (Turkish diacritics included).
- ``rapidocr``: ONNX models shipped inside the pip package; works on any OS, but its
  default alphabet has no Turkish letters (ş, ğ, ı come out as s, g, i).

``auto`` prefers the Windows engine when it is available.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)


class OcrEngine(Protocol):
    name: str

    def read(self, image_path: Path) -> str: ...


class WindowsOcr:
    name = "windows"

    def __init__(self, language: str | None = None):
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine as WinOcr

        available = [lang.language_tag for lang in WinOcr.available_recognizer_languages]
        if not available:
            raise RuntimeError("Windows has no OCR language installed")
        engine = None
        if language and language != "auto":
            match = next((t for t in available if t.lower().startswith(language.lower())), None)
            if match:
                engine = WinOcr.try_create_from_language(Language(match))
        self._engine = engine or WinOcr.try_create_from_user_profile_languages()
        if self._engine is None:
            self._engine = WinOcr.try_create_from_language(Language(available[0]))
        if self._engine is None:
            raise RuntimeError("Windows OCR engine could not be created")
        self.max_dimension = int(WinOcr.max_image_dimension)
        self.languages = available
        self._lock = threading.Lock()

    async def _recognize(self, data: bytes) -> str:
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(data)
        await writer.store_async()
        await writer.flush_async()
        stream.seek(0)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        result = await self._engine.recognize_async(bitmap)
        return "\n".join(line.text for line in result.lines)

    def read(self, image_path: Path) -> str:
        with self._lock:
            return asyncio.run(self._recognize(Path(image_path).read_bytes()))


class RapidOcr:
    name = "rapidocr"

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR

        self._engine = RapidOCR()
        self._lock = threading.Lock()

    def read(self, image_path: Path) -> str:
        with self._lock:
            result, _ = self._engine(str(image_path))
        if not result:
            return ""
        # Each item is (box, text, score); order top-to-bottom, then left-to-right.
        lines = sorted(result, key=lambda item: (round(item[0][0][1] / 12), item[0][0][0]))
        return "\n".join(str(item[1]) for item in lines if float(item[2]) >= 0.5)


def create_ocr(engine: str = "auto", language: str | None = None) -> OcrEngine | None:
    """Build the configured OCR engine, or None when OCR is off or unavailable."""
    if engine == "off":
        return None
    order = {"auto": ["windows", "rapidocr"], "windows": ["windows"], "rapidocr": ["rapidocr"]}
    for name in order.get(engine, []):
        try:
            if name == "windows":
                if sys.platform != "win32":
                    continue
                return WindowsOcr(language)
            return RapidOcr()
        except Exception as exc:  # missing package or unsupported system
            log.info("OCR engine %s unavailable: %s", name, exc)
    return None
