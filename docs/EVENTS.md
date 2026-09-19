# Event Subsystem (Phase 1)

**Feature**: Event-driven agent triggering — infra events (syslog,
metrics, file changes, schedules, Windows logs, NetApp EMS) flow through a
normalized bus, get matched by rules, and spawn agent tasks under a
governed shadow/manual/auto model.
**Status**: Phase 1, shipped.
**Files**:
- `events/schema.py` — normalized `Event` dataclass
- `events/bus.py` — in-process async pub/sub (`BUS` singleton)
- `events/rules.py` + `events/rules.json` — declarative rule book
- `events/engine.py` — bus subscriber: rules → LLM triage → task creation
- `events/adapters/` — `base.py`, `syslog_adapter.py`, `metrics_adapter.py`,
  `watcher_adapter.py`, `scheduler_adapter.py`, `winlog_adapter.py`,
  `netapp_ems_adapter.py`, registry in `adapters/__init__.py`
- `events/service.py` — assembles everything, wires to backend
- Backend side: `backend/main.py` (`create_event_task`, approve/reject,
  `/api/events*`, `/api/ws/events`)
- Frontend: Event Radar panel in `frontend/index.html`

## Pipeline

```
adapter (raw signal) ─► Event (normalized, redacted) ─► EventBus
                                                        │
                          ┌─────────────────────────────┼──────────────┐
                          ▼                             ▼              ▼
                   persist_event()               broadcast_event()  EventEngine
                   (SQLite events table)         (/api/ws/events)    handle_event()
                                                                           │
                                              RuleBook.evaluate(event) ─┘
                     no match + error/critical ──► optional LLM triage (manual mode only)
                     match ──► mode:
                        shadow  → log only
                        manual  → task row status 'awaiting_approval'
                        auto    → task row + run_task scheduled immediately
```

## The Event contract (events/schema.py)

Every source must produce: `source` (adapter id), `source_type` (e.g.
`cpu`, `auth_failure`, `volume_capacity`), `domain` (system|storage|
network|security|application|schedule), `severity`
(info|warning|error|critical), `message`, plus optional `entity`,
`value`, `details`. Adapters publish via `self._publish(Event(...))` —
the bus redacts `message` on entry.

## Rules (events/rules.json)

Declarative, hot-reloaded on every evaluate (mtime check) — tune
thresholds without restarts. Fields:

```json
{
  "name": "high-cpu",
  "match": {"source_type": "cpu", "min_severity": "warning", "domain": "system",
            "source": "metrics", "entity_regex": ".*prod.*"},
  "condition": {"field": "value", "op": ">", "threshold": 90},
  "task_template": "Investigate CPU on {entity} at {value}% — {message}",
  "max_steps": 5,
  "cooldown_seconds": 300,
  "mode": "manual"
}
```

- `condition.op` ∈ `> >= < <= == contains regex`; `field` is any Event
  attribute (falls back to `details[field]` for `value`).
- `{entity}`/`{value}`/`{message}`/`{source}` interpolate into the task.
- `cooldown_seconds` de-dupes per `rule|entity`.
- First matching rule wins.
- **Rule `mode` overrides the global `events.mode`** — run high-risk rules
  in manual while the global mode is auto (or vice versa).

## Rollout modes (PROJECT_ARCHITECTURE.md phased plan)

| mode | behaviour | use when |
|------|-----------|----------|
| shadow | everything logged, zero tasks | validating rules on real traffic |
| manual | tasks created `awaiting_approval`; UI approve/reject | default for anything mutating |
| auto | task runs immediately | whitelisted, read-only, well-tested rules |

LLM triage (unmatched error/critical events) ALWAYS lands in manual mode —
the LLM proposes the task; a human approves it.

## Adapters — how to add one

1. Subclass `events.adapters.base.EventAdapter`; implement
   `async run()` (a loop that publishes) or poll logic in a thread via
   `asyncio.to_thread` (see winlog/netapp_ems for the pattern).
2. Register in `events/adapters/__init__.py` `BUILDERS`:
   `"key": lambda bus, cfg: MyAdapter(bus, **cfg)`.
3. Gate on config: `events.<key>.enabled` — service.py only builds
   enabled adapters, and one crashed adapter never kills the others.
4. Map source severity conservatively — adapters are where raw noise
   becomes signal.

Adapter notes:
- **syslog**: UDP listener (default :5514); regex classifies auth
  failures / errors / warnings. Point rsyslog / ONTAP syslog forwarding
  at this port.
- **metrics**: psutil; publishes on the *rising edge* only (crossing a
  threshold, warning→error escalation, and re-cross after recovery) to
  avoid spamming.
- **watcher**: polls paths, SHA-256 fingerprints (files < 5 MB);
  publishes file_created/modified/deleted into the security domain.
- **scheduler**: `every_seconds` or daily `at: "HH:MM"` entries; emits
  `scheduled_task` events whose `details.is_task` the engine turns into
  direct tasks (mode from the spec, default auto).
- **winlog**: PowerShell `Get-WinEvent` polling — zero extra deps;
  maps 4625→auth_failure, 4720/4726→user_created/deleted,
  1102 (audit log cleared!)→critical.
- **netapp_ems**: polls ONTAP EMS via REST using the credential store;
  no-ops until `netapp_save_credentials` has been called. Volume-capacity
  EMS names map to `source_type=volume_capacity` with a parsed % value.

## Backend surface

| Endpoint | Purpose |
|----------|---------|
| `GET /api/events?limit&severity` | recent events (feed backfill) |
| `GET /api/events/pending` | tasks in `awaiting_approval` |
| `POST /api/tasks/{id}/approve` | approve + start an event task |
| `POST /api/tasks/{id}/reject` | reject (recorded, never run) |
| `GET /api/events/rules` | rules.json for UI |
| `WS /api/ws/events` | live event stream (frontend green dot) |
| `POST /api/events/test` | inject synthetic event (demos/diagnostics) |

`create_event_task` (backend/main.py) is the single funnel: event tasks
use the same `run_task` execution path as user tasks — one engine, two
front doors (intent + events).

## DB schema (same `logs/iris_tasks.db` file)

```sql
CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL, source TEXT, source_type TEXT,
  domain TEXT, severity TEXT, entity TEXT, value REAL,
  message TEXT, details_json TEXT,
  rule TEXT,            -- rule that matched (nullable)
  outcome TEXT,         -- logged | task_created | awaiting_approval | shadow
  task_id TEXT          -- task spawned from this event
);
```

## Frontend

Event Radar panel (left column): live feed via `/api/ws/events` with a
15 s polling fallback, severity filters (ALL / WARN+ / CRIT), critical
events toast, and the Approval Queue with APPROVE & RUN / REJECT buttons.
Green dot = live WS connected.

## Tests

`tests/test_events.py`: schema validation + roundtrip, rule matching /
thresholds / cooldown / interpolation, bus delivery + redaction + overflow
drop-lowest-severity, engine modes (manual creates task, shadow never
does, unmatched info ignored), adapter behaviour (metrics edge detection,
watcher modification, syslog classification, scheduler time parse).
