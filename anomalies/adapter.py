"""
AnomalyAdapter — subscribes to EventBus, runs statistical checks on every
numeric event, and publishes anomaly events when deviations exceed the
configured z-threshold.

Design:
  - Listens to ALL bus events (not just metrics) so it can detect anomalies
    in auth-failure rates, network latencies, etc.
  - For each event, extracts a numeric `value` field; if present, runs
    BaselineTracker.check(). If anomalous, emits a new Event(domain="security",
    severity="critical", message=...) onto the bus.
  - Also handles rate-based metrics (e.g. "auth failures in last N seconds")
    by accepting explicit metric keys from adapter config.
  - Persistence: baselines saved to .iris/baselines.json, survives restarts.

Wired into event service via events/adapters/__init__.py BUILDERS["anomaly"].
Configured via [events.anomaly] in config.json or IRIS_ANOMALY_* env vars.

Files that depend on this:
  - events/service.py   (adapter startup)
  - backend/main.py     (GET/POST /api/anomaly/* endpoints)
  - frontend/index.html (Anomalies panel, Phase 4)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from events.adapters.base import EventAdapter
from events.bus import BUS, EventBus
from events.schema import Event
from iris_config import get as cfg_get

from anomalies.baseline import BaselineTracker, get_tracker, reset_tracker_singleton
from anomalies.config import get_anomaly_config

logger = logging.getLogger(__name__)

# Metric keys the adapter watches per source_type/entity.
# Populated automatically from adapter configs; extended by admin API.
_OBSERVE_KEYS: Dict[str, Dict[str, list]] = {
    # source_type -> {entity: [metric_name, ...]}
    "metrics": {},
    "syslog":  {},
}

# Default metric names per source_type
_SOURCE_METRICS: Dict[str, list] = {
    "metrics": ["cpu", "memory", "disk"],
    "syslog":  ["log_rate"],
    "winlog":  ["event_rate"],
    "netapp_ems": ["ems_rate"],
}


class AnomalyAdapter(EventAdapter):
    """Subscribe to all events, run z-score anomaly detection, publish alerts."""

    name = "anomaly"

    def __init__(self, bus: EventBus, cfg_section: Dict[str, Any]) -> None:
        super().__init__(bus)
        self.cfg = get_anomaly_config(cfg_section)
        self._tracker: Optional[BaselineTracker] = None
        self._running = False

    def _ensure_tracker(self) -> BaselineTracker:
        if self._tracker is None:
            self._tracker = get_tracker(
                window_size=self.cfg.window_size,
                min_samples=self.cfg.min_samples,
                z_threshold=self.cfg.z_threshold,
                cooldown_seconds=self.cfg.cooldown_seconds,
                store_path=self.cfg.store_path,
            )
        return self._tracker

    async def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if not self.cfg.enabled:
            logger.debug("anomaly adapter disabled — skipping")
            return
        self._ensure_tracker()
        self._running = True
        self._setup_watches()
        # Subscribe to every event on the bus
        self.bus.subscribe(self._on_event)
        logger.info(
            "anomaly adapter started: window=%d z=%.2f min_samples=%d cooldown=%ds",
            self.cfg.window_size, self.cfg.z_threshold,
            self.cfg.min_samples, self.cfg.cooldown_seconds,
        )

    def _setup_watches(self) -> None:
        """Register default metric keys per source_type."""
        for src, metrics in _SOURCE_METRICS.items():
            obs = _OBSERVE_KEYS.setdefault(src, {})
            for entity_pattern in list(obs.keys()):
                pass  # already registered from previous run
            # Register default entities if none yet
            if not obs:
                obs["*"] = metrics  # catch-all for unknown entities

    async def _on_event(self, event: Event) -> None:
        """Called for every bus event. Run anomaly check if value is numeric."""
        if not self._running:
            return
        if event.value is None:
            return
        try:
            value = float(event.value)
        except (TypeError, ValueError):
            return
        if not math.isfinite(value):
            return

        tracker = self._ensure_tracker()
        entity = event.entity or "unknown"
        result = tracker.check(event.source, entity, event.source_type or "generic", value)

        if result["is_anomaly"]:
            direction = "above" if result["z_score"] and result["z_score"] > 0 else "below"
            msg = (
                f"ANOMALY [{result['z_score']:.2f}σ {direction} mean] "
                f"{event.source}/{entity}/{event.source_type}: "
                f"value={result['value']}, baseline μ={result['mean']}, σ={result['std']} "
                f"(n={result['samples']})"
            )
            anomaly_event = Event(
                source="anomaly",
                source_type=event.source_type or "generic",
                domain="security",
                severity="critical",
                entity=event.entity,
                value=value,
                message=msg,
                details={
                    "z_score": result["z_score"],
                    "baseline_mean": result["mean"],
                    "baseline_std": result["std"],
                    "samples": result["samples"],
                    "direction": direction,
                    "original_source": event.source,
                },
            )
            self._publish(anomaly_event)
            logger.warning("anomaly detected: %s", msg)

    @classmethod
    def get_tracker(cls) -> Optional[BaselineTracker]:
        """Access the singleton tracker (for API endpoints)."""
        return get_tracker()

    @classmethod
    def reset_singleton(cls) -> None:
        """For tests only."""
        reset_tracker_singleton()


# ── Math helper (avoid importing at top level for test seams) ───────────

import math  # noqa: E402  — imported here to keep top-level clean for mocking
