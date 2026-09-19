"""Scheduler adapter — time-based (cron-like) event source.

Each entry in events.scheduler.tasks:
    {"name": "nightly-health-report",
     "every_seconds": 86400,        # or "at": "02:30" for a daily time
     "task": "Run a health check...",   # task text OR
     "event": true}                      # publish event instead of task

Simplest possible scheduler that covers the common cases (interval,
daily-at-time). NOT a full cron expression engine — deliberately, for
auditability. Tasks created this way follow the rule mode (default
"auto" for scheduled maintenance, but see config).

Files: events/service.py builds from config; tasks flow through the same
create_event_task path as rule tasks.
"""

import asyncio
import datetime as dt

from events.adapters.base import EventAdapter
from events.schema import Event


class SchedulerAdapter(EventAdapter):
    name = "scheduler"

    def __init__(self, bus, tasks):
        super().__init__(bus)
        self.tasks = tasks  # list of dicts from config
        self._next_run: dict = {}

    def _compute_next(self, spec: dict) -> float:
        every = spec.get("every_seconds")
        if every:
            if spec["name"] not in self._next_run:
                return asyncio.get_event_loop().time()  # run immediately once
            return self._next_run[spec["name"]] + every

        at = spec.get("at")  # "HH:MM" daily
        if at:
            hh, mm = at.split(":")
            now = dt.datetime.now()
            target = now.replace(hour=int(hh), minute=int(mm),
                                second=0, microsecond=0)
            if target <= now:
                target += dt.timedelta(days=1)
            return target.timestamp()

        return float("inf")

    async def run(self) -> None:
        loop = asyncio.get_event_loop()
        # initialize schedules
        for spec in self.tasks:
            self._next_run[spec["name"]] = self._compute_next(spec)
        while True:
            now = loop.time()
            for spec in self.tasks:
                name = spec.get("name", "unnamed")
                due = self._next_run.get(name)
                if due is not None and now >= due:
                    # recompute BEFORE publishing so failures don't loop hot
                    if spec.get("at"):
                        tomorrow = dt.datetime.now() + dt.timedelta(days=1)
                        hh, mm = spec["at"].split(":")
                        target = tomorrow.replace(hour=int(hh), minute=int(mm),
                                                  second=0, microsecond=0)
                        self._next_run[name] = target.timestamp()
                    else:
                        self._next_run[name] = now + spec.get("every_seconds", 3600)

                    if spec.get("event"):
                        self._publish(Event(
                            source="scheduler", source_type="scheduled",
                            domain="schedule", severity="info",
                            message=spec.get("message", f"Scheduled event: {name}"),
                            entity=name,
                            details={"spec": spec}))
                    else:
                        self._publish(Event(
                            source="scheduler", source_type="scheduled_task",
                            domain="schedule", severity="info",
                            message=spec.get("task", f"Scheduled task: {name}"),
                            entity=name,
                            details={"spec": spec, "is_task": True,
                                     "mode": spec.get("mode", "auto"),
                                     "max_steps": spec.get("max_steps", 10)}))
            await asyncio.sleep(min(30, 5))
