"""Live updates from the recording threads to the browser (server-sent events)."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any


class EventBus:
    """Fan-out of small JSON events. ``publish`` is safe to call from any thread."""

    def __init__(self) -> None:
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._listeners: list[Callable[[dict], None]] = []
        self._lock = threading.Lock()

    def add_listener(self, listener: Callable[[dict], None]) -> None:
        """Call ``listener(event)`` synchronously on the publishing thread (CLI, tests)."""
        with self._lock:
            self._listeners.append(listener)

    def subscribe(self) -> asyncio.Queue:
        """Must be called from the event loop that will consume the queue."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.append((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers = [(loop, q) for loop, q in self._subscribers if q is not queue]

    def publish(self, kind: str, **data: Any) -> None:
        event = {"type": kind, **data}
        with self._lock:
            subscribers = list(self._subscribers)
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:  # a broken listener must not break the recording
                pass
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(self._offer, queue, event)
            except RuntimeError:  # loop already closed
                self.unsubscribe(queue)

    @staticmethod
    def _offer(queue: asyncio.Queue, event: dict) -> None:
        if queue.full():  # a stalled browser tab must not grow memory without bound
            queue.get_nowait()
        queue.put_nowait(event)
