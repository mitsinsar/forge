"""Fan-out of asset events to WebSocket clients.

``publish`` can be called from the event loop or from the worker thread.
Each subscriber gets its own queue, so a slow client never blocks others.
"""

from __future__ import annotations

import asyncio
import threading


class EventHub:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    def _deliver(self, event: dict) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for queue in subscribers:
            if queue.full():  # drop the oldest event rather than stall
                queue.get_nowait()
            queue.put_nowait(event)

    def publish(self, event: dict) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._deliver(event)
        else:
            loop.call_soon_threadsafe(self._deliver, event)
