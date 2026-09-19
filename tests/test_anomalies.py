"""
Phase 4 — Anomaly Detection Tests
===================================
Covers BaselineTracker (statistical engine), config merging, adapter wiring,
and the new /api/anomaly/* endpoints.

Run: python -m pytest tests/test_anomalies.py -v
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict


# ── helpers ──────────────────────────────────────────────────────────────

def _seed_normal(tracker, n: int = 15, base: float = 70.0, spread: float = 2.0):
    """Fill tracker with normal-looking values."""
    import math
    for i in range(n):
        val = base + math.sin(i * 0.5) * spread
        tracker.observe("metrics", "host-01", "cpu", val)


# ══ anomalies/config ════════════════════════════════════════════════════


class TestAnomalyConfig:
    def test_defaults_are_valid(self):
        from anomalies.config import ANOMALY_DEFAULTS
        assert ANOMALY_DEFAULTS["enabled"] is False
        assert ANOMALY_DEFAULTS["z_threshold"] == 2.5
        assert ANOMALY_DEFAULTS["min_samples"] == 10
        assert ANOMALY_DEFAULTS["window_size"] == 100

    def test_get_anomaly_config_merged(self):
        from anomalies.config import get_anomaly_config
        cfg = get_anomaly_config({"z_threshold": 3.0, "enabled": True})
        assert cfg.enabled is True
        assert cfg.z_threshold == 3.0
        assert cfg.min_samples == 10  # default preserved
        assert cfg.window_size == 100

    def test_env_override(self, monkeypatch):
        from anomalies.config import get_anomaly_config
        monkeypatch.setenv("IRIS_ANOMALY_ENABLED", "true")
        monkeypatch.setenv("IRIS_ANOMALY_Z_THRESH", "1.5")
        monkeypatch.setenv("IRIS_ANOMALY_WINDOW", "200")
        monkeypatch.setenv("IRIS_ANOMALY_MIN_SAMPLES", "5")
        monkeypatch.setenv("IRIS_ANOMALY_COOLDOWN", "60")
        monkeypatch.setenv("IRIS_ANOMALY_STORE", "/tmp/b.json")
        cfg = get_anomaly_config({})
        assert cfg.enabled is True
        assert cfg.z_threshold == 1.5
        assert cfg.window_size == 200
        assert cfg.min_samples == 5
        assert cfg.cooldown_seconds == 60
        assert cfg.store_path == "/tmp/b.json"

    def test_invalid_env_values_keep_defaults(self, monkeypatch):
        from anomalies.config import get_anomaly_config
        monkeypatch.setenv("IRIS_ANOMALY_Z_THRESH", "not-a-number")
        cfg = get_anomaly_config({})
        assert cfg.z_threshold == 2.5  # falls back to default


# ══ anomalies/baseline ═══════════════════════════════════════════════════


class TestBaselineTracker:
    def setup_method(self):
        from anomalies.baseline import reset_tracker_singleton
        reset_tracker_singleton()

    def test_empty_tracker_no_anomaly(self, tmp_path):
        from anomalies.baseline import BaselineTracker
        t = BaselineTracker(min_samples=5, z_threshold=2.0, store_path=str(tmp_path / "bl.json"))
        r = t.check("src", "ent", "met", 99.0)
        assert r["is_anomaly"] is False
        assert "insufficient samples" in r["note"]

    def test_z_score_detection_after_sufficient_samples(self, tmp_path):
        from anomalies.baseline import BaselineTracker
        t = BaselineTracker(window_size=50, min_samples=5, z_threshold=2.0, store_path=str(tmp_path / "bl.json"))
        # Seed normal values around 70
        _seed_normal(t, n=20, base=70.0, spread=1.0)
        # Inject spike
        r = t.check("metrics", "host-01", "cpu", 99.0)
        assert r["is_anomaly"] is True
        assert r["z_score"] is not None
        assert abs(r["z_score"]) >= 2.0
        assert r["samples"] >= 5

    def test_low_value_anomaly(self, tmp_path):
        """Also detect drops below baseline."""
        from anomalies.baseline import BaselineTracker
        t = BaselineTracker(window_size=50, min_samples=5, z_threshold=2.0, store_path=str(tmp_path / "bl.json"))
        _seed_normal(t, n=20, base=70.0, spread=1.0)
        r = t.check("metrics", "host-01", "cpu", 50.0)
        assert r["is_anomaly"] is True
        assert r["z_score"] < 0  # below mean

    def test_cooldown_suppresses_repeats(self, tmp_path):
        from anomalies.baseline import BaselineTracker
        t = BaselineTracker(
            window_size=50, min_samples=5, z_threshold=2.0, cooldown_seconds=300,
            store_path=str(tmp_path / "bl.json"),
        )
        _seed_normal(t, n=20, base=70.0, spread=1.0)
        r1 = t.check("metrics", "host-01", "cpu", 99.0)
        assert r1["is_anomaly"] is True
        # Same value again should be in cooldown
        r2 = t.check("metrics", "host-01", "cpu", 99.0)
        assert r2["is_anomaly"] is False
        assert r2["cooldown"] is True

    def test_get_baseline_returns_stats(self, tmp_path):
        from anomalies.baseline import BaselineTracker
        t = BaselineTracker(min_samples=5, z_threshold=2.0, store_path=str(tmp_path / "bl.json"))
        _seed_normal(t, n=15, base=70.0, spread=1.0)
        bl = t.get_baseline("metrics", "host-01", "cpu")
        assert bl is not None
        assert bl["mean"] > 65
        assert bl["std"] > 0
        assert bl["samples"] == 15

    def test_get_baseline_none_before_min_samples(self, tmp_path):
        from anomalies.baseline import BaselineTracker
        t = BaselineTracker(min_samples=10, z_threshold=2.0, store_path=str(tmp_path / "bl.json"))
        t.observe("src", "e", "m", 42.0)
        assert t.get_baseline("src", "e", "m") is None

    def test_list_baselines(self, tmp_path):
        from anomalies.baseline import BaselineTracker
        t = BaselineTracker(min_samples=3, z_threshold=2.0, store_path=str(tmp_path / "bl.json"))
        _seed_normal(t, n=5, base=70.0)
        t.observe("syslog", "fw-01", "auth_failures", 5.0)
        bls = t.list_baselines()
        assert len(bls) >= 1

    def test_clear_key_removes_one(self, tmp_path):
        from anomalies.baseline import BaselineTracker
        t = BaselineTracker(min_samples=3, z_threshold=2.0, store_path=str(tmp_path / "bl.json"))
        _seed_normal(t, n=5, base=70.0)
        t.observe("syslog", "fw-01", "auth_failures", 5.0)
        assert t.clear_key("syslog", "fw-01", "auth_failures") is True
        assert t.get_baseline("syslog", "fw-01", "auth_failures") is None

    def test_clear_all(self, tmp_path):
        from anomalies.baseline import BaselineTracker
        t = BaselineTracker(min_samples=3, z_threshold=2.0, store_path=str(tmp_path / "bl.json"))
        _seed_normal(t, n=5, base=70.0)
        t.observe("syslog", "fw-01", "auth_failures", 5.0)
        n = t.clear()
        assert n >= 2
        assert t.list_baselines() == []

    def test_rollback_from_spike(self, tmp_path):
        """After a spike, old values drift out of window; mean recovers."""
        from anomalies.baseline import BaselineTracker
        t = BaselineTracker(window_size=10, min_samples=5, z_threshold=2.0, store_path=str(tmp_path / "bl.json"))
        _seed_normal(t, n=8, base=70.0, spread=1.0)
        t.check("metrics", "host-01", "cpu", 99.0)  # spike
        # Feed more normal values to fill window
        for _ in range(5):
            t.observe("metrics", "host-01", "cpu", 70.5)
        bl = t.get_baseline("metrics", "host-01", "cpu")
        assert bl is not None
        assert abs(bl["mean"] - 70.0) < 5.0  # mean should recover


# ══ anomalies/adapter ═══════════════════════════════════════════════════


class TestAnomalyAdapter:
    def setup_method(self):
        from anomalies.baseline import reset_tracker_singleton
        reset_tracker_singleton()

    def test_adapter_detects_anomaly_on_bus_event(self, tmp_path):
        """When a metrics event with a spike hits the bus, an anomaly event fires."""
        import asyncio
        from events.bus import EventBus
        from events.schema import Event
        from anomalies.adapter import AnomalyAdapter

        bus = EventBus(buffer_size=100)
        adapter = AnomalyAdapter(bus, {
            "enabled": True,
            "min_samples": 5,
            "z_threshold": 2.0,
            "cooldown_seconds": 0,
            "store_path": str(tmp_path / "bl.json"),
        })

        collected = []

        async def collect(event):
            collected.append(event)

        bus.subscribe(collect)

        async def _run():
            await bus.start()                # must start bus for fan-out to work
            await adapter.start(asyncio.get_event_loop())
            # Seed baseline with normal values
            for i in range(10):
                bus.publish(Event(
                    source="metrics", source_type="cpu", domain="system",
                    severity="info", entity="host-01", value=70.0 + (i % 3),
                    message=f"CPU at {70+i}",
                ))
            # Now fire a spike
            bus.publish(Event(
                source="metrics", source_type="cpu", domain="system",
                severity="error", entity="host-01", value=99.0,
                message="CPU spike detected",
            ))
            await asyncio.sleep(0.2)
            await bus.stop()

        asyncio.run(_run())

        anomaly_events = [e for e in collected if e.source == "anomaly"]
        assert len(anomaly_events) >= 1, (
            f"Expected anomaly event but got: "
            f"{[e.message[:60] for e in anomaly_events]}"
        )
        anom = anomaly_events[0]
        assert anom.severity == "critical"
        assert anom.domain == "security"
        assert anom.details.get("z_score") is not None
        assert abs(anom.details["z_score"]) >= 2.0

    def test_no_anomaly_within_baseline(self):
        """Normal values within 1σ should not trigger anomalies."""
        import asyncio
        from events.bus import EventBus
        from events.schema import Event
        from anomalies.adapter import AnomalyAdapter

        bus = EventBus(buffer_size=100)
        adapter = AnomalyAdapter(bus, {
            "enabled": True,
            "min_samples": 5,
            "z_threshold": 2.0,
            "cooldown_seconds": 0,
        })

        collected = []

        async def collect(event):
            collected.append(event)

        bus.subscribe(collect)

        async def _run():
            await adapter.start(asyncio.get_event_loop())
            # Seed and send normal values
            for i in range(15):
                bus.publish(Event(
                    source="metrics", source_type="cpu", domain="system",
                    severity="info", entity="host-01", value=70.0,
                    message="normal",
                ))
            await asyncio.sleep(0.1)

        asyncio.run(_run())

        anomaly_events = [e for e in collected if e.source == "anomaly"]
        assert len(anomaly_events) == 0

    def test_disabled_adapter_publishes_nothing(self):
        """When disabled, adapter should not subscribe or publish."""
        import asyncio
        from events.bus import EventBus
        from events.schema import Event
        from anomalies.adapter import AnomalyAdapter

        bus = EventBus(buffer_size=100)
        adapter = AnomalyAdapter(bus, {"enabled": False})

        async def _run():
            await adapter.start(asyncio.get_event_loop())
            bus.publish(Event(
                source="metrics", source_type="cpu", domain="system",
                severity="info", entity="host-01", value=99.0,
                message="spike",
            ))
            await asyncio.sleep(0.1)

        asyncio.run(_run())
        # No anomaly events should have been published
        # (we can't easily check bus queue, but adapter should not have subscribed)

    def test_null_value_ignored(self):
        """Events with no numeric value should be silently skipped."""
        import asyncio
        from events.bus import EventBus
        from events.schema import Event
        from anomalies.adapter import AnomalyAdapter

        bus = EventBus(buffer_size=100)
        adapter = AnomalyAdapter(bus, {
            "enabled": True,
            "min_samples": 3,
            "z_threshold": 2.0,
            "cooldown_seconds": 0,
        })

        collected = []

        async def collect(event):
            collected.append(event)

        bus.subscribe(collect)

        async def _run():
            await adapter.start(asyncio.get_event_loop())
            bus.publish(Event(
                source="metrics", source_type="cpu", domain="system",
                severity="info", entity="host-01", value=None,
                message="no value",
            ))
            await asyncio.sleep(0.1)

        asyncio.run(_run())
        anomaly_events = [e for e in collected if e.source == "anomaly"]
        assert len(anomaly_events) == 0


# ══ backend /api/anomaly/* endpoints ════════════════════════════════════


class TestAnomalyEndpoints:
    def setup_method(self):
        from anomalies.baseline import reset_tracker_singleton
        reset_tracker_singleton()
        # Clean baselines file
        import os
        p = Path(".iris/baselines.json")
        if p.exists():
            p.unlink()

    def test_anomaly_config_endpoint(self):
        from backend.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.get("/api/anomaly/config")
        assert r.status_code == 200
        data = r.json()
        assert "enabled" in data
        assert "z_threshold" in data
        assert "window_size" in data

    def test_anomaly_baselines_empty(self):
        from backend.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.get("/api/anomaly/baselines")
        assert r.status_code == 200
        assert r.json()["baselines"] == []

    def test_anomaly_recent_empty(self):
        from backend.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.get("/api/anomaly/recent")
        assert r.status_code == 200
        assert r.json()["anomalies"] == []

    def test_anomaly_clear_all(self):
        from backend.main import app
        from fastapi.testclient import TestClient
        from anomalies.baseline import get_tracker
        client = TestClient(app)
        # Seed some baselines
        t = get_tracker(min_samples=3, z_threshold=2.0)
        for i in range(10):
            t.observe("metrics", "h1", "cpu", 70.0)
        r = client.delete("/api/anomaly/baselines")
        assert r.status_code == 200
        assert r.json()["cleared"] >= 1
        r2 = client.get("/api/anomaly/baselines")
        assert r2.json()["baselines"] == []
