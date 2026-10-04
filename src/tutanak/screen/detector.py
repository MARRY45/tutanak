"""Decides which screenshots are new slides.

Saving a screenshot every two seconds would bury the handful of slides in
thousands of near-identical images. The detector compares small grayscale
thumbnails block by block:

- a *new slide* is a frame in which a large share of blocks differs from the last
  kept slide, **and** which then holds still for one more frame;
- frames that keep changing (a video, an animation, scrolling) are never kept until
  they settle;
- a small region that changes all the time (a webcam tile, a blinking cursor) stays
  under the block threshold and is ignored.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

THUMB = (128, 72)
GRID = (16, 9)


def thumbnail(image: Image.Image) -> np.ndarray:
    """Small grayscale version of a frame, values 0..1."""
    small = image.convert("L").resize(THUMB, Image.Resampling.BILINEAR)
    return np.asarray(small, dtype=np.float32) / 255.0


def changed_fraction(a: np.ndarray, b: np.ndarray, block_threshold: float = 0.02) -> float:
    """Share of grid blocks whose mean brightness difference exceeds the threshold.

    The threshold is low on purpose. Two text slides on the same template differ only
    where the text is, and a line of text is thin: at 0.05 such a change touched just
    10-12% of blocks and was missed, at 0.02 it touches 24-29%. Lossless screenshots
    and re-encoded video frames of an unchanged slide both measure 0.
    """
    diff = np.abs(a - b)
    rows, cols = GRID[1], GRID[0]
    blocks = diff.reshape(rows, THUMB[1] // rows, cols, THUMB[0] // cols).mean(axis=(1, 3))
    return float((blocks > block_threshold).mean())


@dataclass
class Keyframe:
    t: float
    frame: Any  # whatever the caller passed in (an image, a path, ...)


class SlideDetector:
    # A webcam tile covering a ninth of the screen changes about 12% of blocks, so
    # both limits sit above that: the tile neither looks like a new slide nor keeps
    # a real slide change from settling.
    def __init__(self, *, new_fraction: float = 0.15, stable_fraction: float = 0.13):
        self.new_fraction = new_fraction
        self.stable_fraction = stable_fraction
        self._kept: np.ndarray | None = None
        self._pending: tuple[float, np.ndarray] | None = None

    def feed(self, t: float, thumb: np.ndarray, frame: Any = None) -> Keyframe | None:
        """Offer the frame captured at ``t``; returns a keyframe when a slide settled."""
        if self._pending is not None:
            pending_t, pending_thumb = self._pending
            if changed_fraction(thumb, pending_thumb) <= self.stable_fraction:
                # The change held still: it is a slide. Keep the settled (current)
                # frame, which is fully rendered, at the time the change began.
                self._kept = thumb
                self._pending = None
                return Keyframe(pending_t, frame)
            if self._differs(thumb):
                self._pending = (t, thumb)  # still moving; restart the wait
            else:
                self._pending = None  # went back to the slide we already have
            return None
        if self._differs(thumb):
            self._pending = (t, thumb)
        return None

    def _differs(self, thumb: np.ndarray) -> bool:
        if self._kept is None:
            return True
        return changed_fraction(thumb, self._kept) >= self.new_fraction
