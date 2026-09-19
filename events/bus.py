"""
Event bus — in-process pub/sub for normalized events.
=====================================================
Central asyncio queue with fan-out to subscribers. Adapters publish;
the rules engine, the persistence layer (DB), and the WebSocket
broadcaster (UI live feed) subscribe.

Deliberately simple: no external broker (Kafka/Redis) — this is an
air-gapped, single-host deployment; SQLite + in-process queue is the
right size. The subscribe/publish API is broker-shaped, so swapping in
a real broker later touches only this file.

Guardrails built in:
  - bounded queue (drops loudest events when full instead of OOM-ing)
  - redaction applied to message/details before fan-out

Files that depend on this module:
  - events/adapters/*  (publish)
  - events/engine.py   (subscribes: rules processing)
  - events/service.py  (subscribes: DB persist + WebSocket broadcast)
  - tests/test_events.py
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable, List, Optional

from agent.redact import redact
from events.schema import Event

logger = logging.getLogger(__name__)

Subscriber = Callable[[Event], Awaitable[None]]


class EventBus:
    def __init__(self, buffer_size: int = 1000):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=buffer_size)
        self._subscribers: List[Subscriber] = []
        self._task: Optional[asyncio.Task] = None
        self.dropped: int = 0  # events dropped when the buffer overflowed

    def subscribe(self, callback: Subscriber) -> None:
        self._subscribers.append(callback)

    def publish(self, event: Event) -> bool:
        """Thread-safe publish (adapters may run in worker threads).
        Returns False when the buffer is full and the event was dropped
        (lowest severity is dropped first — critical events get the last slot)."""
        event.message = redact(event.message)
        try:
            self._queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            # Evict the least severe queued event to make room.
            try:
                items: List[Event] = []
                while True:
                    items.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                pass
            if items:
                worst = max(range(len(items)), key=lambda i: items[i].severity_rank())
                # swap-in the new event in place of the least important one
                if items[worst].severity_rank() >= event.severity_rank():
                    self.dropped += 1
                    for it in reversed(items):
                        self._queue.put_nowait(it)
                    return False
                items[worst] = event
                for it in reversed(items):
                    self._queue.put_nowait(it)
                self.dropped += 1
                return True
            return False

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="event-bus")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            event = await self._queue.get()
            for callback in self._subscribers:
                try:
                    await callback(event)
                except Exception:
                    logger.exception("event subscriber failed for %s", event.source)


# Single shared bus for the process. events/service.py wires it up.
BUS = EventBus()
