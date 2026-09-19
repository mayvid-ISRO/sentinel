"""
Event adapters — turn raw infra signals into normalized Events.
================================================================
Each adapter is a small class with start(loop)/stop() that publishes to the
shared EventBus. All adapters are config-gated (events.* sections in
config.json) and failure-isolated: one adapter crashing never takes down
the bus or the others (service.py restarts them with backoff).

Adding a new event source:
  1. Create events/adapters/<name>.py implementing EventAdapter.
  2. Register it in events/adapters/__init__.py BUILDERS.
  3. Add an events.<name> section to iris_config.DEFAULTS + config.example.json.

Files that depend on this package:
  - events/service.py (builds & starts adapters)
  - tests/test_events.py (adapter unit tests publish synthetic data)
"""

import asyncio
import logging
from typing import Optional

from events.bus import EventBus
from events.schema import Event

logger = logging.getLogger(__name__)


class EventAdapter:
    """Base class: an adapter owns one event source."""

    name = "base"

    def __init__(self, bus: EventBus):
        self.bus = bus
        self._task: Optional[asyncio.Task] = None

    async def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Spawn the adapter's worker task(s)."""
        self._task = asyncio.create_task(self.run(), name=f"adapter-{self.name}")
        logger.info("adapter '%s' started", self.name)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def run(self) -> None:
        """Override: loop forever, calling self._publish(...) as needed."""
        await asyncio.Event().wait()  # default: do nothing

    def _publish(self, event: Event) -> None:
        """Publish with redaction + drop accounting handled by the bus."""
        ok = self.bus.publish(event)
        if not ok:
            logger.warning("adapter '%s': bus dropped event (buffer full)", self.name)
