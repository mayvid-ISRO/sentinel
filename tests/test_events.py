"""Event subsystem tests — schema, rules engine, bus, engine, adapters.

These tests avoid network, LLM, and real infra: everything runs against
synthetic events with stub callbacks.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from events.bus import EventBus
from events.engine import EventEngine
from events.rules import RuleBook
from events.schema import Event



async def _drain(bus, received, seconds=0.05):
    """Start bus, wait for queued events to reach subscribers, stop.

    Idempotent: registers the shared subscriber only once so multiple
    drains don't double-count events.
    """
    if not getattr(bus, "_test_subscribed", False):
        async def sub(event):
            received.append(event)
        bus.subscribe(sub)
        bus._test_subscribed = True
    await bus.start()
    await asyncio.sleep(seconds)
    await bus.stop()

def make_event(**kwargs) -> Event:
    defaults = dict(
        source="metrics", source_type="cpu", domain="system",
        severity="warning", message="CPU at 95%", entity="host-1", value=95.0,
    )
    defaults.update(kwargs)
    return Event(**defaults)


class TestSchema:
    def test_valid_event(self):
        assert make_event().validate()

    def test_bad_severity_invalid(self):
        assert not make_event(severity="catastrophic").validate()

    def test_bad_domain_invalid(self):
        assert not make_event(domain="kitchen").validate()

    def test_roundtrip_dict(self):
        e = make_event(details={"extra": 1})
        d = Event.from_dict(e.to_dict())
        assert d.message == e.message and d.value == e.value
        assert d.details["extra"] == 1

    def test_severity_rank(self):
        assert make_event(severity="critical").severity_rank() > \
               make_event(severity="error").severity_rank() > \
               make_event(severity="warning").severity_rank() > \
               make_event(severity="info").severity_rank()


class TestRules:
    def test_high_cpu_rule_matches(self):
        rb = RuleBook()
        match = rb.evaluate(make_event())
        assert match is not None
        assert match["rule"] == "high-cpu"
        assert "host-1" in match["task"]

    def test_below_threshold_no_match(self):
        rb = RuleBook()
        assert rb.evaluate(make_event(value=85.0)) is None

    def test_severity_gate(self):
        rb = RuleBook()
        assert rb.evaluate(make_event(severity="info")) is None

    def test_ssh_brute_rule(self):
        rb = RuleBook()
        match = rb.evaluate(make_event(
            source="syslog", source_type="auth_failure", domain="security",
            severity="error", value=None, message="Failed password for root"))
        assert match is not None and match["rule"] == "ssh-brute-force"

    def test_critical_catchall(self):
        rb = RuleBook()
        match = rb.evaluate(make_event(
            source_type="anything-obscure", severity="critical", value=None))
        assert match is not None and match["rule"] == "critical-anything"

    def test_cooldown_prevents_refire(self):
        rb = RuleBook()
        assert rb.evaluate(make_event()) is not None
        # second event same entity within cooldown -> suppressed
        assert rb.evaluate(make_event()) is None
        # different entity -> fires
        m = rb.evaluate(make_event(entity="host-2"))
        assert m is not None

    def test_task_template_interpolation(self):
        rb = RuleBook()
        match = rb.evaluate(make_event(value=96.5))
        assert "96.5" in match["task"] and "host-1" in match["task"]


class TestBus:
    def test_publish_subscribe_roundtrip(self):
        async def run():
            bus = EventBus(buffer_size=10)
            received = []

            async def sub(event):
                received.append(event)

            bus.subscribe(sub)
            await bus.start()
            assert bus.publish(make_event(message="hello bus"))
            await asyncio.sleep(0.05)
            await bus.stop()
            assert len(received) == 1
            assert received[0].message == "hello bus"

        asyncio.run(run())

    def test_bus_redacts_secrets(self):
        async def run():
            bus = EventBus()
            received = []
            bus.subscribe(lambda e: received.append(e) or asyncio.sleep(0))
            await bus.start()
            bus.publish(make_event(message="password: supersecret was used"))
            await asyncio.sleep(0.05)
            await bus.stop()
            assert "supersecret" not in received[0].message

        asyncio.run(run())

    def test_overflow_drops_lowest_severity(self):
        async def run():
            bus = EventBus(buffer_size=2)
            received = []
            bus.subscribe(lambda e: received.append(e))
            await bus.start()
            # overflow the buffer; info events should be evicted first
            for i in range(5):
                bus.publish(make_event(severity="info", message=f"info-{i}"))
            bus.publish(make_event(severity="critical", message="keep me"))
            await asyncio.sleep(0.05)
            await bus.stop()
            messages = [e.message for e in received]
            assert "keep me" in messages
            assert bus.dropped > 0

        asyncio.run(run())


class TestEngineModes:
    def _run_engine(self, mode, event):
        """Run the engine against a stub task creator; returns created tasks."""
        created = []

        def create_task(task_text, max_steps, m, ev):
            created.append((task_text, max_steps, m))
            return f"ev-test-{len(created)}"

        async def run():
            bus = EventBus()
            rules = RuleBook()
            engine = EventEngine(bus, rules, create_task, llm_call=None)
            bus.subscribe(engine.handle_event)
            await bus.start()
            # override matched rule mode by injecting a matching event and
            # monkey-level control: use rule mode via rules.json default (manual)
            bus.publish(event)
            await asyncio.sleep(0.1)
            await bus.stop()

        asyncio.run(run())
        return created

    def test_manual_mode_creates_awaiting_task(self):
        created = self._run_engine("manual", make_event())
        assert len(created) == 1
        task_text, max_steps, mode = created[0]
        assert mode == "manual"
        assert "host-1" in task_text

    def test_shadow_mode_creates_no_task(self):
        """shadow rule mode must never create a task."""
        created = []

        def create_task(text, steps, m, ev):
            created.append(text)
            return None

        async def run():
            bus = EventBus()
            rules = RuleBook()
            # force shadow via a crafted rulebook
            rules.rules = [{
                "name": "shadow-test",
                "match": {"source_type": "cpu"},
                "task_template": "shadow task {entity}",
                "mode": "shadow",
            }]
            engine = EventEngine(bus, rules, create_task, llm_call=None)
            bus.subscribe(engine.handle_event)
            await bus.start()
            bus.publish(make_event())
            await asyncio.sleep(0.1)
            await bus.stop()

        asyncio.run(run())
        assert created == [], "shadow mode must not create tasks"

    def test_unmatched_info_event_no_task(self):
        created = self._run_engine("manual", make_event(
            source_type="unknown", severity="info", value=None))
        assert created == []


class TestAdapters:
    def test_metrics_crossing_only(self):
        """Metrics adapter publishes on the rising EDGE, not every poll."""
        from events.adapters.metrics_adapter import MetricsAdapter
        bus = EventBus()
        received = []
        adapter = MetricsAdapter(bus, interval=1,
                                 cpu_threshold=90.0, mem_threshold=99.0,
                                 disk_threshold=99.0)

        async def scenario():
            # phase 1: crossing + no-refire + escalation
            adapter._maybe_publish("cpu", "h", 95.0, 90.0, "cpu high")
            adapter._maybe_publish("cpu", "h", 96.0, 90.0, "cpu high again")
            adapter._maybe_publish("cpu", "h", 97.0, 90.0, "cpu escalated")
            await _drain(bus, received)
            assert len(received) == 2  # warning edge + escalation edge
            assert received[0].severity == "warning"
            assert received[1].severity == "error"

            # phase 2: recovery, then re-crossing fires again
            adapter._maybe_publish("cpu", "h", 50.0, 90.0, "fine")
            adapter._maybe_publish("cpu", "h", 95.0, 90.0, "high again")
            await _drain(bus, received)
            assert len(received) == 3

        asyncio.run(scenario())

    def test_watcher_detects_modification(self):
        from events.adapters.watcher_adapter import WatcherAdapter
        bus = EventBus()
        received = []
        adapter = WatcherAdapter(bus, paths=[], interval=1)

        import tempfile, os
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            path = Path(f.name)
        try:
            adapter._state[str(path)] = (10, 1.0, "oldhash")
            # simulate new fingerprint
            adapter._state[str(path)] = (10, 1.0, "oldhash")
            # direct event path test via sample on a changed file
            path.write_text("changed content")
            adapter.paths = [path]
            adapter.sample()
            asyncio.run(_drain(bus, received))
            assert any(e.source_type == "file_modified" for e in received)
        finally:
            path.unlink(missing_ok=True)

    def test_syslog_classification(self):
        from events.adapters.syslog_adapter import SyslogAdapter
        bus = EventBus()
        received = []
        adapter = SyslogAdapter(bus)
        adapter.handle_datagram(b"sshd: Failed password for root from 10.0.0.9", ("10.0.0.9", 5000))
        adapter.handle_datagram(b"kernel: disk io error on sda1", ("10.0.0.10", 5000))
        adapter.handle_datagram(b"systemd: started service foo", ("10.0.0.11", 5000))
        asyncio.run(_drain(bus, received))
        kinds = [(e.source_type, e.severity, e.entity) for e in received]
        assert ("auth_failure", "error", "10.0.0.9") in kinds
        assert any(k[0] == "syslog_error" and k[2] == "10.0.0.10" for k in kinds)
        assert any(k[1] == "info" for k in kinds)

    def test_scheduler_daily_parse(self):
        from events.adapters.scheduler_adapter import SchedulerAdapter
        bus = EventBus()
        adapter = SchedulerAdapter(bus, tasks=[{
            "name": "nightly", "at": "23:59",
            "task": "nightly health check", "max_steps": 5}])
        # next-run computation must be a future timestamp
        due = adapter._compute_next({"name": "nightly", "at": "23:59"})
        import time as _t
        assert due > _t.time()
