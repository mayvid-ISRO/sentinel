"""
Event service — assembles the event subsystem and binds it to the backend.
===========================================================================
start_event_service(app) is called from backend/main.py startup when
events.enabled=true. It:
  1. Creates the shared EventBus (events/bus.BUS).
  2. Subscribes: DB persister (events table), WebSocket broadcaster
     (/api/ws/events channel), and the EventEngine (rules processing).
  3. Builds + starts every enabled adapter from config (events.* sections).
  4. Injects create_event_task into the engine: a callback that the
     BACKEND supplies (imported late to avoid a circular import) which
     reuses the same run_task path as user-submitted tasks.

Files that depend on this module:
  - backend/main.py (startup hook + create_event_task + approve endpoint)
  - events/* (everything it wires)
"""

import asyncio
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from events import adapters as adapter_registry
from events.bus import BUS, EventBus
from events.engine import EventEngine, attach_engine
from events.rules import DEFAULT_RULES_PATH, RuleBook
from events.schema import Event
from iris_config import get as cfg_get, PROJECT_ROOT

logger = logging.getLogger(__name__)

DB_DIR = PROJECT_ROOT / "backend" / "logs"
DB_PATH = DB_DIR / "iris_tasks.db"

# module state (single event subsystem per process)
_engine: Optional[EventEngine] = None
_rules: Optional[RuleBook] = None
_started = False


# ── DB persistence ─────────────────────────────────────────────────────
def init_events_db() -> None:
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                source TEXT, source_type TEXT, domain TEXT, severity TEXT,
                entity TEXT, value REAL, message TEXT,
                details_json TEXT,
                rule TEXT,                 -- rule that matched (nullable)
                outcome TEXT,              -- logged|task_created|awaiting_approval|shadow
                task_id TEXT               -- task created from this event
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_sev ON events(severity)")
        conn.commit()
    finally:
        conn.close()


def persist_event(event: Event, rule: str = None, outcome: str = "logged") -> None:
    import json
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT INTO events (timestamp, source, source_type, domain, "
                "severity, entity, value, message, details_json, rule, outcome) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (event.timestamp, event.source, event.source_type, event.domain,
                 event.severity, event.entity, event.value, event.message,
                 json.dumps(event.details, default=str), rule, outcome),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        logger.exception("failed to persist event")


def recent_events(limit: int = 100, severity: str = None) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        if severity:
            rows = conn.execute(
                "SELECT * FROM events WHERE severity = ? ORDER BY id DESC LIMIT ?",
                (severity, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                import json
                d["details"] = json.loads(d.pop("details_json") or "{}")
            except Exception:
                d["details"] = {}
            out.append(d)
        return out
    finally:
        conn.close()


# ── WebSocket broadcast channel ────────────────────────────────────────
EVENT_WS_CLIENTS: set = set()


async def broadcast_event(event: Event) -> None:
    """Push a live event to every connected /api/ws/events socket."""
    import json
    if not EVENT_WS_CLIENTS:
        return
    payload = json.dumps({"type": "event", **event.to_dict()})
    dead = []
    for ws in list(EVENT_WS_CLIENTS):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        EVENT_WS_CLIENTS.discard(ws)


# ── engine wiring ──────────────────────────────────────────────────────
def _make_create_task(create_task_cb):
    """Wrap the backend's task creator so the engine gets a simple
    (text, max_steps, mode, event) -> task_id callback."""
    def create_event_task(task_text: str, max_steps: int, mode: str,
                          event: Event):
        return create_task_cb(task_text, max_steps, mode, event)
    return create_event_task


async def start_event_service(app) -> EventEngine:
    """Build + start the whole event subsystem. Idempotent."""
    global _engine, _rules, _started
    if _started:
        return _engine
    _started = True

    init_events_db()

    # late import avoids circular: backend.main defines create_event_task
    from backend.main import create_event_task as backend_create_task

    _rules = RuleBook(DEFAULT_RULES_PATH)

    # subscribers: persistence + UI broadcast get EVERY event;
    # the engine subscribes and decides what becomes a task.
    async def persist_and_broadcast(event: Event) -> None:
        persist_event(event)
        await broadcast_event(event)

    BUS.subscribe(persist_and_broadcast)

    from agent.llm import call_llm
    _engine = attach_engine(
        BUS, _rules,
        _make_create_task(backend_create_task),
        llm_call=call_llm,
    )
    await BUS.start()

    # ── start enabled adapters ─────────────────────────────────────────
    started_adapters = []
    for key, builder in adapter_registry.BUILDERS.items():
        section = cfg_get("events", key, {})
        if not isinstance(section, dict) or not section.get("enabled", False):
            continue
        try:
            adapter = builder(BUS, section)
            await adapter.start(asyncio.get_event_loop())
            started_adapters.append(key)
        except Exception:
            logger.exception("failed to start adapter '%s'", key)

    logger.info("event service started: adapters=%s", started_adapters)
    return _engine


async def stop_event_service() -> None:
    global _started
    await BUS.stop()
    _started = False


def get_engine() -> Optional[EventEngine]:
    return _engine
