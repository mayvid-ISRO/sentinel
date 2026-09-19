# Backend

**Feature**: FastAPI server — task CRUD, live streaming, event API,
approval workflow.
**Status**: Phase 0 refactor + Phase 1 event integration.
**Files**: `backend/main.py` (everything; intentionally one file until
Phase 3 RBAC forces a split), `frontend/index.html` (client).

## What changed in Phase 0

The backend previously contained a **duplicate of the entire agent loop**
(plan → prompt → parse → dispatch) so it could stream progress. Two
copies of the loop = two places for bugs, and they had already diverged
(one had retries, the other didn't). Now `run_task` calls
`agent.run_agent(user_task, max_steps, on_step, should_cancel)` once:

- `on_step` fires per StepResult from the agent's worker thread → pushes
  a WebSocket frame (via `asyncio.run_coroutine_threadsafe`) AND
  persists steps so far to SQLite (crash-resilient history).
- `should_cancel` checks an in-memory `cancelled_tasks` set — the
  `/api/tasks/{id}/cancel` endpoint adds to it; the agent loop polls it
  before each step and exits with a `cancelled` step.

CORS origins come from config (`backend.cors_origins`, default localhost:3000
— no more `*`). Task text is redacted before the DB write.

## Endpoints

| Method | Path | Notes |
|--------|------|-------|
| POST | `/api/tasks` | submit; returns id; agent runs via BackgroundTasks |
| GET | `/api/tasks?status&limit&offset` | list/filter history |
| GET | `/api/tasks/{id}` / `…/steps` | detail |
| PATCH | `/api/tasks/{id}` | edit text / pin / tags |
| DELETE | `/api/tasks/{id}` | remove |
| POST | `/api/tasks/{id}/cancel` | real cancel (polled by loop) |
| POST | `/api/tasks/{id}/approve` `/reject` | Phase 1 approval workflow |
| GET | `/api/tools` | tool registry for UI |
| GET | `/api/stats` / `/api/health` | health incl. llm url/model + events state |
| GET | `/api/events*` , WS `/api/ws/events` | see EVENTS.md |
| GET | `/api/db/clear?confirm=true` | wipe task history |

## WebSocket protocol (`/api/ws/{task_id}`)

Join anytime — server replays stored steps + current status first
(page-reload resilience), then live frames:

```json
{"type":"status","status":"pending|running|cancelling|done|failed|cancelled|rejected"}
{"type":"step","step_number":1,"tool_name":"…","tool_args":{…},"result":"…",
 "status":"ok|error|done","timestamp":"…"}
{"type":"error","message":"…"}   {"type":"ping"}
```

## Event-task creation (Phase 1)

`create_event_task(task_text, max_steps, mode, event)` is the callback the
event engine calls. `manual` → row status `awaiting_approval` (shows in
UI approval queue). `auto` → row + `loop.create_task(run_task(...))`
immediately. IDs are `ev-XXXXXXXX` to distinguish event tasks at a
glance. `_link_event_task` best-effort tags the originating events row
with the created task id (audit trail: event → task).

## DB schema (logs/iris_tasks.db, SQLite WAL)

```sql
tasks(id PK, task, status, max_steps, created_at, finished_at,
      error, steps_json, pinned, tags)
events(id PK AUTOINCREMENT, timestamp, source, source_type, domain,
       severity, entity, value, message, details_json, rule, outcome, task_id)
```

`init_db()` is idempotent and migrates old DBs (ALTER ADD for
pinned/tags). Statuses: `pending → running → done|failed|cancelled`,
plus `awaiting_approval`/`rejected` (event tasks) and transient
`cancelling`.

## Startup sequence

`startup_event`: logging config → `init_db()` → if `events.enabled`,
`events/service.start_event_service(app)` (builds bus, engine, adapters —
see EVENTS.md). Failures in the event subsystem log but never block the
API.

## Gotchas for future changes

- **DB path is `backend/logs/`** — `DB_DIR` is relative to `backend/`, not
  repo root. (The repo-root `logs/` holds agent run logs + the events DB
  written by events/service.py — both point at the same file only when
  CWD layout matches; service.py deliberately uses the repo-root path so
  CLI and backend share one DB.)
- `run_task` runs the agent in `asyncio.to_thread` — anything it touches
  from the thread (steps_payload list) is append-only by design; keep it
  that way or add a lock.
- Status mapping lives at the END of run_task; new agent step statuses
  must be reflected there and in the frontend filter buttons.
