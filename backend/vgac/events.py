"""WebSocket event broadcaster for /api/ws/events.

publish() is safe from the event loop; publish_threadsafe() from worker threads. Each connected socket has its own
queue, so a slow client never blocks the workers (its queue drops the oldest events when full).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("vgac.events")


class EventBus:
    def __init__(self, maxsize: int = 500) -> None:
        self._queues: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.maxsize = maxsize

    def bind(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop or asyncio.get_running_loop()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self.maxsize)
        self._queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._queues.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        for q in list(self._queues):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def publish_threadsafe(self, event: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self.publish(event)
        else:
            loop.call_soon_threadsafe(self.publish, event)

    @property
    def subscribers(self) -> int:
        return len(self._queues)


bus = EventBus()
