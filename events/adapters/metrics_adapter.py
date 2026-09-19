"""Metrics adapter — periodic host health polling via psutil.

Samples CPU / memory / per-disk usage every events.metrics.interval_seconds
and publishes threshold-crossing events (warning >= threshold, error at
threshold+5). Only crossings are published — a host sitting at 91% emits
one warning, not one per poll. Values are also stashed in details for the
anomaly detector (Phase 4).

Files: events/service.py builds this from config; events/rules.json
high-cpu/high-memory/disk-near-full rules consume its events.
"""

import platform

import psutil

from events.adapters.base import EventAdapter
from events.schema import Event


class MetricsAdapter(EventAdapter):
    name = "metrics"

    def __init__(self, bus, interval: int = 30,
                 cpu_threshold: float = 90.0, mem_threshold: float = 90.0,
                 disk_threshold: float = 90.0):
        super().__init__(bus)
        self.interval = interval
        self.cpu_threshold = cpu_threshold
        self.mem_threshold = mem_threshold
        self.disk_threshold = disk_threshold
        self.hostname = platform.node() or "localhost"
        # last published state per (source_type, entity) — for crossing detect
        self._alerting: dict = {}

    def _maybe_publish(self, source_type: str, entity: str, value: float,
                       threshold: float, message: str, domain: str = "system") -> None:
        """Publish on rising crossing of threshold; clear state when back below."""
        key = f"{source_type}|{entity}"
        if value > threshold:
            severity = "error" if value > threshold + 5 else "warning"
            if self._alerting.get(key) is None:  # crossing edge only
                self._publish(Event(
                    source="metrics", source_type=source_type, domain=domain,
                    severity=severity, message=message, entity=entity, value=value,
                    details={"threshold": threshold},
                ))
                self._alerting[key] = severity
            elif severity == "error" and self._alerting[key] == "warning":
                # escalation edge warning -> error
                self._publish(Event(
                    source="metrics", source_type=source_type, domain=domain,
                    severity="error", message=message, entity=entity, value=value,
                    details={"threshold": threshold, "escalated": True},
                ))
                self._alerting[key] = "error"
        else:
            self._alerting.pop(key, None)  # recovered — allow future re-fire

    def sample(self) -> None:
        cpu = psutil.cpu_percent(interval=1)
        self._maybe_publish(
            "cpu", self.hostname, cpu, self.cpu_threshold,
            f"CPU usage on {self.hostname} is {cpu:.1f}% "
            f"(threshold {self.cpu_threshold}%)")

        mem = psutil.virtual_memory().percent
        self._maybe_publish(
            "memory", self.hostname, mem, self.mem_threshold,
            f"Memory usage on {self.hostname} is {mem:.1f}% "
            f"(threshold {self.mem_threshold}%)")

        for part in psutil.disk_partitions(all=False):
            if "cdrom" in part.opts or not part.fstype:
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint).percent
            except (PermissionError, OSError):
                continue
            self._maybe_publish(
                "disk", f"{self.hostname}:{part.mountpoint}", usage,
                self.disk_threshold,
                f"Disk {part.mountpoint} on {self.hostname} is {usage:.1f}% full "
                f"(threshold {self.disk_threshold}%)")

    async def run(self) -> None:
        while True:
            try:
                self.sample()
            except Exception:
                import logging
                logging.getLogger(__name__).exception("metrics sample failed")
            await __import__("asyncio").sleep(self.interval)
