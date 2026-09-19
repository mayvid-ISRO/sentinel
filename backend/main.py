"""
IRIS Backend - main.py
FastAPI server wrapping the agent with:
  - REST endpoints to submit / manage tasks
  - WebSocket for live step-by-step streaming to the UI
  - SQLite task history persistence

Phase 0 rework:
  - The duplicated agent loop is GONE. run_task() now calls agent.run_agent()
    once and streams progress through the on_step callback — one loop, one
    place to fix bugs.
  - Cancellation is real: should_cancel is polled before each agent step so
    /api/tasks/{id}/cancel actually stops a running task.
  - CORS origins come from config; secrets in task text are redacted before
    the DB write.
  - Event subsystem (events/) is started here when events.enabled=true.

Run:
  uvicorn backend.main:app --port 8000
"""

import asyncio
import json
import logging
import os
import sqlite3
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── point Python at project root (agent/, tools/, events/) ───────────
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from agent.agent import StepResult, run_agent
from agent.redact import redact
from iris_config import get as cfg_get

# ── Database paths ─────────────────────────────────────────────────────
DB_DIR = Path(__file__).resolve().parent / "logs"
DB_DIR.mkdir(exist_ok=True)
DB_PATH = DB_DIR / "iris_tasks.db"

logger = logging.getLogger(__name__)

# ── DB Helper Functions ────────────────────────────────────────────────
@contextmanager
def get_db():
    """Context manager for SQLite connections."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Initialize database schema (idempotent, safe for old DBs)."""
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                task TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                max_steps INTEGER DEFAULT 10,
                created_at TEXT NOT NULL,
                finished_at TEXT,
                error TEXT,
                steps_json TEXT,
                pinned INTEGER DEFAULT 0,
                tags TEXT
            )
        """)
        # Add columns that older DB files may lack (ALTER errors = exists).
        for column, decl in (("pinned", "INTEGER DEFAULT 0"), ("tags", "TEXT")):
            try:
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} {decl}")
            except sqlite3.OperationalError:
                pass
        # Events table (Phase 1) — same DB file, separate table.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                source TEXT, source_type TEXT, domain TEXT, severity TEXT,
                entity TEXT, value REAL, message TEXT,
                details_json TEXT,
                rule TEXT,
                outcome TEXT,
                task_id TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp)")
        conn.commit()


def task_to_dict(row) -> Dict[str, Any]:
    """Convert a database row to a task dict."""
    return {
        "id": row["id"],
        "task": row["task"],
        "status": row["status"],
        "max_steps": row["max_steps"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
        "error": row["error"],
        "steps": json.loads(row["steps_json"] or "[]"),
        "pinned": bool(row["pinned"] or 0),
        "tags": json.loads(row["tags"] or "[]") if row["tags"] else [],
    }


# ── FastAPI App ────────────────────────────────────────────────────────
app = FastAPI(
    title="IRIS API",
    version="3.0.0",
    description="Intelligent Reasoning Infrastructure System - Air-Gapped Edition",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg_get("backend", "cors_origins", ["http://localhost:3000"]),
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory state for active WebSocket subscribers and running tasks
sockets: Dict[str, List[WebSocket]] = {}
running_tasks: set = set()
running_tasks_lock = threading.Lock()
cancelled_tasks: set = set()
cancelled_tasks_lock = threading.Lock()


# ── Request/Response Models ─────────────────────────────────────────────
class RunRequest(BaseModel):
    task: str
    max_steps: Optional[int] = None


class TaskInfo(BaseModel):
    id: str
    task: str
    status: str
    created_at: str
    finished_at: Optional[str] = None
    error: Optional[str] = None
    steps: List[Dict[str, Any]] = []
    pinned: bool = False
    tags: List[str] = []


class UpdateTaskRequest(BaseModel):
    task: Optional[str] = None
    pinned: Optional[bool] = None
    tags: Optional[List[str]] = None


# ── WebSocket Broadcast Helper ─────────────────────────────────────────
async def push(task_id: str, msg: dict):
    """Send message to all WebSocket clients for a task."""
    dead = []
    for ws in sockets.get(task_id, []):
        try:
            await ws.send_text(json.dumps(msg))
        except Exception:
            dead.append(ws)
    for ws in dead:
        try:
            sockets[task_id].remove(ws)
        except ValueError:
            pass


# ── Background Agent Runner ────────────────────────────────────────────
def _persist_steps(task_id: str, steps_payload: List[dict]) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE tasks SET steps_json = ? WHERE id = ?",
            (json.dumps(steps_payload), task_id),
        )
        conn.commit()


def _is_cancelled(task_id: str) -> bool:
    with cancelled_tasks_lock:
        return task_id in cancelled_tasks


# ── Event-triggered task creation ───────────────────────────────────────
def _insert_task(task_id: str, task_text: str, status: str, max_steps: int) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO tasks (id, task, status, max_steps, created_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, redact(task_text), status, max_steps, datetime.utcnow().isoformat()),
        )
        conn.commit()


def create_event_task(task_text: str, max_steps: int, mode: str, event) -> Optional[str]:
    """Callback used by events/engine.py. Creates the task row and — for
    auto mode — kicks off run_task on the running loop. manual mode rows
    land in status 'awaiting_approval' until POST /api/tasks/{id}/approve.

    NOTE: called from the event-bus thread inside the running loop.
    """
    task_id = "ev-" + str(uuid.uuid4())[:8]
    try:
        _insert_task(task_id, task_text,
                     "awaiting_approval" if mode == "manual" else "pending",
                     max_steps)
        if mode == "auto":
            # schedule execution on the main loop — we're inside it already
            loop = asyncio.get_event_loop()
            loop.create_task(run_task(task_id, task_text, max_steps))
        elif mode == "manual":
            _link_event_task(task_id, event, "awaiting_approval")
        else:
            _link_event_task(task_id, event, "task_created")
        return task_id
    except Exception:
        logger.exception("create_event_task failed")
        return None


def _link_event_task(task_id: str, event, outcome: str) -> None:
    """Tag the newest events row for this event with the created task."""
    try:
        import sqlite3 as _sq
        conn = _sq.connect(str(DB_PATH), timeout=10.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "UPDATE events SET outcome = ?, task_id = ? WHERE id = "
                "(SELECT id FROM events WHERE source = ? AND source_type = ? AND "
                "timestamp = ? ORDER BY id DESC LIMIT 1)",
                (outcome, task_id, event.source, event.source_type, event.timestamp),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass  # tagging is best-effort; the events row still exists


async def run_task(task_id: str, user_task: str, max_steps: Optional[int]):
    """
    Runs agent.run_agent in a worker thread, streaming every StepResult
    to WebSocket clients the moment it is produced, then persists the run.
    """
    if max_steps is None:
        max_steps = int(cfg_get("agent", "max_steps", 10))

    with running_tasks_lock:
        running_tasks.add(task_id)

    with get_db() as conn:
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", ("running", task_id))
        conn.commit()

    await push(task_id, {"type": "status", "status": "running"})

    loop = asyncio.get_event_loop()
    steps_payload: List[dict] = []

    def on_step(step: StepResult) -> None:
        """Synchronous callback from the agent thread -> WebSocket + DB."""
        data = {"type": "step", **step.to_dict()}
        steps_payload.append(data)
        asyncio.run_coroutine_threadsafe(push(task_id, data), loop)
        _persist_steps(task_id, steps_payload)

    def should_cancel() -> bool:
        return _is_cancelled(task_id)

    task_status = "failed"
    error_msg = None
    try:
        _observations, step_results = await asyncio.to_thread(
            run_agent, user_task, max_steps, on_step, should_cancel
        )
        if any(s.status == "done" for s in step_results):
            task_status = "done"
        elif step_results and step_results[-1].tool_name == "cancelled":
            task_status = "cancelled"
        elif not step_results:
            task_status = "failed"
            error_msg = "Agent produced no steps (plan generation failed?)"
        else:
            task_status = "failed"
    except Exception as exc:
        error_msg = str(exc)
        task_status = "failed"
    finally:
        with cancelled_tasks_lock:
            cancelled_tasks.discard(task_id)

    with get_db() as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, finished_at = ?, error = ?, steps_json = ? WHERE id = ?",
            (task_status, datetime.utcnow().isoformat(), error_msg,
             json.dumps(steps_payload), task_id),
        )
        conn.commit()

    await push(task_id, {"type": "status", "status": task_status})
    if error_msg:
        await push(task_id, {"type": "error", "message": redact(error_msg)})

    with running_tasks_lock:
        running_tasks.discard(task_id)


# ── REST Routes ────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    """Initialize database + start event subsystem when enabled."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    init_db()
    # Phase 3: seed auth store
    try:
        from auth.middleware import init_auth_if_needed
        init_auth_if_needed()
    except Exception as exc:
        logger.warning("Auth initialisation skipped: %s", exc)
    if cfg_get("events", "enabled", False):
        try:
            from events.service import start_event_service
            await start_event_service(app)
            logger.info("Event subsystem started (mode=%s)", cfg_get("events", "mode"))
        except Exception as exc:
            logger.error("Event subsystem failed to start: %s", exc)
    logger.info("Backend started successfully")


@app.post("/api/tasks", response_model=TaskInfo)
async def create_task(req: RunRequest, bg: BackgroundTasks):
    """Submit a new task. Returns task_id immediately; agent runs in background."""
    task_id = str(uuid.uuid4())[:8]

    with get_db() as conn:
        conn.execute(
            "INSERT INTO tasks (id, task, status, max_steps, created_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, redact(req.task), "pending", req.max_steps or cfg_get("agent", "max_steps", 10),
             datetime.utcnow().isoformat()),
        )
        conn.commit()

    bg.add_task(run_task, task_id, req.task, req.max_steps)

    return {
        "id": task_id,
        "task": req.task,
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
        "steps": [],
    }


@app.get("/api/tasks", response_model=List[TaskInfo])
async def list_tasks(status: Optional[str] = None, limit: int = 50, offset: int = 0):
    """Return all tasks, optionally filtered by status."""
    with get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [task_to_dict(row) for row in rows]


@app.get("/api/tasks/{task_id}", response_model=TaskInfo)
async def get_task(task_id: str):
    """Get a specific task by ID."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Task not found")
        return task_to_dict(row)


@app.get("/api/tasks/{task_id}/steps")
async def get_task_steps(task_id: str):
    """Get steps for a specific task."""
    with get_db() as conn:
        row = conn.execute("SELECT steps_json FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Task not found")
        steps = json.loads(row["steps_json"] or "[]")
        return {"task_id": task_id, "steps": steps}


@app.get("/api/tools")
async def list_tools():
    """Return available tools so the UI can display them."""
    try:
        from tools import TOOLS
        return [
            {"name": name, "description": getattr(tool, "description", "")}
            for name, tool in TOOLS.items()
        ]
    except Exception as e:
        logger.error("Failed to load tools: %s", e)
        return []


@app.get("/api/stats")
async def get_stats():
    """Get statistics about tasks."""
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        done = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'done'").fetchone()[0]
        failed = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'failed'").fetchone()[0]
        running = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'running'").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'pending'").fetchone()[0]

    with running_tasks_lock:
        active_ws_count = sum(len(socks) for socks in sockets.values())
        running_ids = list(running_tasks)

    return {
        "total": total,
        "done": done,
        "failed": failed,
        "running": running,
        "pending": pending,
        "active_websocket_connections": active_ws_count,
        "running_task_ids": running_ids,
    }


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "version": "3.0.0",
        "database": str(DB_PATH),
        "db_exists": DB_PATH.exists(),
        "llm_url": cfg_get("llm", "url"),
        "llm_model": cfg_get("llm", "model"),
        "events_enabled": cfg_get("events", "enabled", False),
        "events_mode": cfg_get("events", "mode"),
        "timestamp": datetime.utcnow().isoformat(),
    }


# ── WebSocket Endpoint ─────────────────────────────────────────────────
@app.websocket("/api/ws/{task_id}")
async def ws_stream(websocket: WebSocket, task_id: str):
    """
    Connect before or after submitting a task.
    - If task already has steps, replays them immediately (page-reload resilience).
    - Then stays open and receives live updates as the agent runs.
    """
    await websocket.accept()
    sockets.setdefault(task_id, []).append(websocket)

    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT status, steps_json FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()

            if row:
                steps = json.loads(row["steps_json"] or "[]")
                for step in steps:
                    await websocket.send_text(json.dumps(step))
                await websocket.send_text(json.dumps({
                    "type": "status",
                    "status": row["status"],
                }))
    except Exception as e:
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": f"Could not load task history: {str(e)}",
        }))

    try:
        while True:
            await asyncio.sleep(30)
            await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        sockets.get(task_id, []).remove(websocket)
    except Exception:
        sockets.get(task_id, []).remove(websocket)


# ── Event API (Phase 1) ────────────────────────────────────────────────
@app.get("/api/events")
async def list_events(limit: int = 100, severity: Optional[str] = None):
    """Recent normalized events (live feed source on page load)."""
    try:
        from events.service import recent_events
        return {"events": recent_events(limit=limit, severity=severity)}
    except Exception as e:
        logger.error("list_events failed: %s", e)
        return {"events": []}


@app.get("/api/events/pending")
async def list_pending_event_tasks():
    """Tasks awaiting human approval (event-triggered, manual mode)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = 'awaiting_approval' "
            "ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        return {"tasks": [task_to_dict(r) for r in rows]}


@app.post("/api/tasks/{task_id}/approve")
async def approve_task(task_id: str):
    """Approve an awaiting_approval event task and start it."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Task not found")
        if row["status"] != "awaiting_approval":
            raise HTTPException(400, f"Task status is '{row['status']}', not awaiting_approval")
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", ("pending", task_id))
        conn.commit()

    asyncio.get_event_loop().create_task(
        run_task(task_id, row["task"], row["max_steps"])
    )
    return {"status": "approved", "task_id": task_id}


@app.post("/api/tasks/{task_id}/reject")
async def reject_task(task_id: str):
    """Reject an awaiting_approval event task (recorded, never run)."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Task not found")
        if row["status"] != "awaiting_approval":
            raise HTTPException(400, f"Task status is '{row['status']}', not awaiting_approval")
        conn.execute(
            "UPDATE tasks SET status = ?, finished_at = ? WHERE id = ?",
            ("rejected", datetime.utcnow().isoformat(), task_id),
        )
        conn.commit()
    return {"status": "rejected", "task_id": task_id}


@app.get("/api/events/rules")
async def list_event_rules():
    """Return rules.json content for the UI (read-only for now)."""
    from events.rules import DEFAULT_RULES_PATH
    try:
        return json.loads(DEFAULT_RULES_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(500, f"Could not load rules: {e}")


@app.websocket("/api/ws/events")
async def ws_events(websocket: WebSocket):
    """Live event stream — every normalized event as it flows the bus."""
    await websocket.accept()
    try:
        from events.service import EVENT_WS_CLIENTS
        EVENT_WS_CLIENTS.add(websocket)
        while True:
            await asyncio.sleep(30)
            await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        try:
            from events.service import EVENT_WS_CLIENTS
            EVENT_WS_CLIENTS.discard(websocket)
        except Exception:
            pass


@app.post("/api/events/test")
async def publish_test_event(severity: str = "warning", source_type: str = "cpu",
                             entity: str = "test-host", value: float = 95.0):
    """Diagnostic: inject a synthetic event into the bus (for demos/tests)."""
    try:
        from events.bus import BUS
        from events.schema import Event
        event = Event(
            source="manual", source_type=source_type, domain="system",
            severity=severity, message=f"Test event: {source_type}={value} on {entity}",
            entity=entity, value=value,
        )
        BUS.publish(event)
        return {"published": True, "event": event.to_dict()}
    except Exception as e:
        raise HTTPException(500, f"Event subsystem not running: {e}")


# ── Task Management ────────────────────────────────────────────────────
@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """Cancel a running task. The agent checks before each step."""
    with running_tasks_lock:
        is_running = task_id in running_tasks
    if not is_running:
        raise HTTPException(status_code=400, detail="Task is not running")

    with cancelled_tasks_lock:
        cancelled_tasks.add(task_id)

    with get_db() as conn:
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", ("cancelling", task_id))
        conn.commit()

    await push(task_id, {"type": "status", "status": "cancelling"})
    return {"status": "cancelling", "task_id": task_id}


@app.patch("/api/tasks/{task_id}")
async def update_task(task_id: str, req: UpdateTaskRequest):
    """Update a task's title, pinned status, or tags."""
    with get_db() as conn:
        if req.pinned is not None:
            conn.execute("UPDATE tasks SET pinned = ? WHERE id = ?", (1 if req.pinned else 0, task_id))
        if req.task is not None:
            conn.execute("UPDATE tasks SET task = ? WHERE id = ?", (req.task, task_id))
        if req.tags is not None:
            conn.execute("UPDATE tasks SET tags = ? WHERE id = ?", (json.dumps(req.tags), task_id))
        conn.commit()
    return {"status": "updated", "task_id": task_id}


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    """Delete a task from history."""
    with get_db() as conn:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
    return {"status": "deleted", "task_id": task_id}


@app.get("/api/db/clear")
async def clear_db(confirm: bool = False):
    """Clear all task data (use with caution!)."""
    if not confirm:
        raise HTTPException(status_code=400, detail="Set ?confirm=true to clear data")
    with get_db() as conn:
        conn.execute("DELETE FROM tasks")
        conn.commit()
    return {"status": "all tasks deleted"}


# ── RAG Knowledge Store (Phase 2) ───────────────────────────────────────

class IngestRequest(BaseModel):
    paths: List[str]


@app.get("/api/rag/status")
async def rag_status():
    """Return RAG index health; {'enabled': false} when disabled."""
    from rag.config import get_rag_config
    cfg = get_rag_config()
    if not cfg.get("enabled", False):
        return {"enabled": False, "message": "RAG disabled in config"}
    try:
        from rag.retriever import KnowledgeRetriever
        retriever = KnowledgeRetriever.from_config()
        if retriever is None:
            return {"enabled": False, "message": "RAG init failed"}
        return {"enabled": True, **retriever.status()}
    except Exception as exc:
        return {"enabled": True, "error": str(exc)}


@app.post("/api/rag/ingest")
async def rag_ingest(req: IngestRequest):
    """Index one or more document paths. Returns number of chunks added."""
    from rag.config import get_rag_config
    cfg = get_rag_config()
    if not cfg.get("enabled", False):
        raise HTTPException(400, "RAG is disabled — set rag.enabled=true in config.json")
    try:
        from rag.retriever import KnowledgeRetriever
        retriever = KnowledgeRetriever.from_config()
        if retriever is None:
            raise HTTPException(500, "RAG initialisation failed")
        n = retriever.ingest(req.paths)
        return {"chunks_added": n, "paths": req.paths}
    except Exception as exc:
        logger.error("RAG ingest failed: %s", exc)
        raise HTTPException(500, str(exc))


@app.delete("/api/rag/clear")
async def rag_clear():
    """Wipe the knowledge index completely."""
    from rag.config import get_rag_config
    cfg = get_rag_config()
    if not cfg.get("enabled", False):
        raise HTTPException(400, "RAG is disabled")
    try:
        from rag.retriever import KnowledgeRetriever
        retriever = KnowledgeRetriever.from_config()
        if retriever is None:
            raise HTTPException(500, "RAG initialisation failed")
        retriever.clear()
        return {"status": "cleared"}
    except Exception as exc:
        logger.error("RAG clear failed: %s", exc)
        raise HTTPException(500, str(exc))


@app.get("/api/rag/search")
async def rag_search(q: str, k: int = 3):
    """Retrieve top-k chunks most similar to *q*."""
    from rag.config import get_rag_config
    cfg = get_rag_config()
    if not cfg.get("enabled", False):
        raise HTTPException(400, "RAG is disabled")
    try:
        from rag.retriever import KnowledgeRetriever
        retriever = KnowledgeRetriever.from_config()
        if retriever is None:
            raise HTTPException(500, "RAG initialisation failed")
        refs = retriever.retrieve(q, k=k)
        return {"query": q, "results": [r.to_dict() for r in refs]}
    except Exception as exc:
        logger.error("RAG search failed: %s", exc)
        raise HTTPException(500, str(exc))


# ── Phase 3: RBAC / Auth ──────────────────────────────────────────────────

_auth_bearer = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    username: str


class UserOut(BaseModel):
    username: str
    role: str
    created_at: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


def _auth_enabled() -> bool:
    from iris_config import get as cfg_get
    return bool(cfg_get("auth", "enabled", False))


@app.get("/api/auth/config")
async def auth_config():
    """Return whether auth is enabled (no secret exposed)."""
    return {"auth_enabled": _auth_enabled()}


@app.post("/api/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    """Authenticate and return a JWT."""
    from auth.models import authenticate_user, issue_token
    user = authenticate_user(req.username, req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = issue_token(user.username, user.role)
    return TokenResponse(access_token=token, role=user.role, username=user.username)


@app.get("/api/auth/me")
async def get_me(credentials: HTTPAuthorizationCredentials = Depends(_auth_bearer)):
    """Return current user info from token; 401 if unauthenticated."""
    if not _auth_enabled():
        return {"username": "anonymous", "role": "anonymous"}
    from auth.models import verify_token, list_users
    payload = verify_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    store = {u['username']: u for u in list_users()}
    user = store.get(payload.sub)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user


@app.get("/api/auth/users", response_model=List[UserOut])
async def list_users_endpoint(credentials: HTTPAuthorizationCredentials = Depends(_auth_bearer)):
    """List users (admin only). Returns all if auth is disabled."""
    from auth.models import list_users as _list
    if _auth_enabled():
        from auth.middleware import check_role, require_auth
        user = require_auth(credentials)
        check_role(user, "admin")
    return [UserOut(**u) for u in _list()]


@app.post("/api/auth/users", response_model=UserOut)
async def create_user_endpoint(req: LoginRequest, credentials: HTTPAuthorizationCredentials = Depends(_auth_bearer)):
    """Create a new user (admin only)."""
    from auth.models import create_user as _create
    if _auth_enabled():
        from auth.middleware import check_role, require_auth
        user = require_auth(credentials)
        check_role(user, "admin")
    try:
        user = _create(req.username, req.password, role=req.role if hasattr(req, 'role') else "operator")
    except Exception:
        pass  # re-raise with proper shape below
    from auth.models import list_users as _list
    store = {u['username']: u for u in _list()}
    return UserOut(**store[req.username])


@app.post("/api/auth/change-password")
async def change_password(req: ChangePasswordRequest, credentials: HTTPAuthorizationCredentials = Depends(_auth_bearer)):
    from auth.models import verify_token, update_user_password, list_users
    payload = verify_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    ok = update_user_password(payload.sub, req.new_password)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found")
    return {"status": "password_updated"}


# ── RBAC Tool Risk Catalog ─────────────────────────────────────────────────

@app.get("/api/rbac/tools")
async def list_tool_risks(credentials: HTTPAuthorizationCredentials = Depends(_auth_bearer)):
    """Return every registered tool with its risk tier and approval count."""
    if _auth_enabled():
        from auth.middleware import check_role, require_auth
        user = require_auth(credentials)
        check_role(user, "admin", "operator")
    from auth.policy import _DEFAULT_TOOL_RISK, APPROVALS_REQUIRED
    from tools import TOOLS
    result = []
    for name, tool in TOOLS.items():
        risk = _DEFAULT_TOOL_RISK.get(name, "write")
        result.append({
            "tool": name,
            "description": tool.description,
            "risk_tier": risk,
            "approvals_required": APPROVALS_REQUIRED[risk],
        })
    return result


# ── Multi-Tier Approval Endpoints ──────────────────────────────────────────

@app.get("/api/approvals/pending")
async def list_pending_approvals(credentials: HTTPAuthorizationCredentials = Depends(_auth_bearer)):
    """Return all approval tickets awaiting signatures."""
    if _auth_enabled():
        from auth.middleware import check_role, require_auth
        user = require_auth(credentials)
        check_role(user, "admin", "operator")
    from auth.approvals import get_approval_manager
    mgr = get_approval_manager()
    return [t.to_dict() for t in mgr.all_pending()]


@app.post("/api/approvals/{task_id}/sign")
async def sign_approval(
    task_id: str,
    note: str = "",
    credentials: HTTPAuthorizationCredentials = Depends(_auth_bearer),
):
    """Sign the next pending tier of an approval ticket."""
    from auth.models import verify_token, list_users
    payload = verify_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    store = {u['username']: u for u in list_users()}
    user = store.get(payload.sub)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    from auth.approvals import get_approval_manager
    mgr = get_approval_manager()
    ticket = mgr.get(task_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Approval ticket not found")
    if ticket.status == "approved":
        return {"status": "already_approved", "task_id": task_id}
    if ticket.status == "rejected":
        raise HTTPException(400, "Ticket already rejected")
    # Determine which tier this signer is signing for
    signed_tiers = {s.tier for s in ticket.signatures}
    next_tier = 1 if 1 not in signed_tiers else 2
    if next_tier > ticket.required_approvals:
        raise HTTPException(400, "All required approvals already collected")
    updated = mgr.sign_tier(task_id, user['username'], user['role'], next_tier, note)
    if updated is None:
        raise HTTPException(500, "Failed to record signature")
    # If fully approved, start the task
    if updated.status == "approved":
        with get_db() as conn:
            conn.execute("UPDATE tasks SET status = ? WHERE id = ?", ("pending", task_id))
            conn.commit()
        asyncio.get_event_loop().create_task(run_task(task_id, "", None))
        await push(task_id, {"type": "status", "status": "running"})
    return {"status": updated.status, "task_id": task_id, "tier": next_tier}


@app.post("/api/approvals/{task_id}/reject")
async def reject_approval(
    task_id: str,
    reason: str = "",
    credentials: HTTPAuthorizationCredentials = Depends(_auth_bearer),
):
    """Reject an approval ticket (any reviewer can reject)."""
    from auth.models import verify_token, list_users
    payload = verify_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    store = {u['username']: u for u in list_users()}
    user = store.get(payload.sub)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    from auth.approvals import get_approval_manager
    mgr = get_approval_manager()
    ticket = mgr.get(task_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Approval ticket not found")
    updated = mgr.reject(task_id, user['username'], user['role'], reason)
    if updated is None:
        raise HTTPException(400, "Ticket not in approvable state")
    with get_db() as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, finished_at = ? WHERE id = ?",
            ("rejected", datetime.utcnow().isoformat(), task_id),
        )
        conn.commit()
    return {"status": "rejected", "task_id": task_id}


# ── Anomaly Detection (Phase 4) ────────────────────────────────────────

@app.get("/api/anomaly/config")
async def get_anomaly_config_endpoint(
    credentials: HTTPAuthorizationCredentials = Depends(_auth_bearer),
):
    """Return anomaly subsystem config (enabled, thresholds, store path)."""
    from iris_config import get as cfg_get
    from anomalies.config import ANOMALY_DEFAULTS
    raw = cfg_get("events", "anomaly", {})
    from anomalies.config import get_anomaly_config
    cfg = get_anomaly_config(raw)
    return {
        "enabled": cfg.enabled,
        "window_size": cfg.window_size,
        "z_threshold": cfg.z_threshold,
        "min_samples": cfg.min_samples,
        "cooldown_seconds": cfg.cooldown_seconds,
        "store_path": cfg.store_path,
    }


@app.get("/api/anomaly/baselines")
async def list_anomaly_baselines(
    credentials: HTTPAuthorizationCredentials = Depends(_auth_bearer),
):
    """Return all baselines that have enough samples for detection."""
    from anomalies.baseline import get_tracker
    tracker = get_tracker()
    return {"baselines": tracker.list_baselines()}


@app.delete("/api/anomaly/baselines/{source_type}/{entity}/{metric}")
async def clear_anomaly_baseline(
    source_type: str, entity: str, metric: str,
    credentials: HTTPAuthorizationCredentials = Depends(_auth_bearer),
):
    """Remove one baseline key."""
    from anomalies.baseline import get_tracker
    tracker = get_tracker()
    removed = tracker.clear_key(source_type, entity, metric)
    return {"removed": removed}


@app.delete("/api/anomaly/baselines")
async def clear_all_anomaly_baselines(
    credentials: HTTPAuthorizationCredentials = Depends(_auth_bearer),
):
    """Remove all baselines (resets the detector)."""
    from anomalies.baseline import get_tracker
    tracker = get_tracker()
    n = tracker.clear()
    return {"cleared": n}


@app.get("/api/anomaly/recent")
async def get_recent_anomalies(limit: int = 50):
    """Return recent anomaly events from the events table."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM events WHERE source = 'anomaly' "
            "ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                import json
                d["details"] = json.loads(d.pop("details_json") or "{}")
            except Exception:
                d["details"] = {}
            out.append(d)
        return {"anomalies": out, "count": len(out)}
    finally:
        conn.close()


# ══ Fleet Management (Phase 5) ══
_fleet_registry: Any = None
_fleet_lock = threading.Lock()


def _get_fleet_registry() -> Any:
    """Lazily initialise the fleet registry singleton."""
    global _fleet_registry
    with _fleet_lock:
        if _fleet_registry is None:
            from fleet.registry import FleetRegistry
            _fleet_registry = FleetRegistry()
            # seed from iris_config nodes section
            for entry in cfg_get("nodes") or []:
                name = entry.get("name", "")
                if name:
                    _fleet_registry.add_node(
                        name=name,
                        host=entry.get("host", ""),
                        port=int(entry.get("port", 9000)),
                        token_env=entry.get("token_env", ""),
                    )
            _fleet_registry.start_polling()
            logger.info("fleet: registry initialised with %d node(s)",
                        len(_fleet_registry.list_nodes()))
        return _fleet_registry


@app.get("/api/fleet/nodes")
async def list_fleet_nodes():
    """Return all registered nodes with current health state."""
    reg = _get_fleet_registry()
    return {"nodes": reg.list_nodes(), "count": len(reg.list_nodes())}


@app.post("/api/fleet/nodes")
async def add_fleet_node(node: Dict[str, Any]) -> Dict[str, Any]:
    """Register a new node in the fleet."""
    name = node.get("name", "").strip()
    host = node.get("host", "").strip()
    raw_port = node.get("port", 9000)
    token_env = node.get("token_env", "")
    if not name or not host:
        raise HTTPException(status_code=400, detail="name and host are required")
    try:
        port = int(raw_port)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="port must be an integer")
    reg = _get_fleet_registry()
    reg.add_node(name, host, port, token_env)
    return {"ok": True, "node": name}


@app.delete("/api/fleet/nodes/{name}")
async def remove_fleet_node(name: str) -> Dict[str, Any]:
    """Remove a node from the fleet."""
    reg = _get_fleet_registry()
    removed = reg.remove_node(name)
    return {"ok": removed, "removed": removed}


@app.post("/api/fleet/run")
async def fleet_run_parallel(task: Dict[str, str]) -> Dict[str, Any]:
    """Fan out a task to all healthy fleet nodes concurrently."""
    user_task = task.get("task", "").strip()
    max_steps = int(task.get("max_steps", 3))
    if not user_task:
        raise HTTPException(status_code=400, detail="task is required")
    from agent.parallel import ParallelDispatcher
    reg = _get_fleet_registry()
    dispatcher = ParallelDispatcher(reg)
    result = await dispatcher.run_parallel(user_task, max_steps=max_steps)
    return result


@app.get("/api/fleet/health")
async def fleet_health_summary() -> Dict[str, Any]:
    """Lightweight fleet health summary (no network calls)."""
    reg = _get_fleet_registry()
    nodes = reg.list_nodes()
    healthy = sum(1 for n in nodes if n["healthy"])
    unknown = sum(1 for n in nodes if not n["healthy"] and n["last_health_ts"] == 0)
    return {
        "total": len(nodes),
        "healthy": healthy,
        "unhealthy": len(nodes) - healthy - unknown,
        "unknown": unknown,
    }


# ── Static Frontend Serving ────────────────────────────────────────────
# Serves the single-page frontend from / when deployed as a unified
# backend+frontend host — no separate nginx/http.server needed.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_FRONTEND_DIR = _REPO_ROOT / "frontend"


@app.get("/")
async def serve_index():
    """Serve the main frontend page."""
    idx = _FRONTEND_DIR / "index.html"
    if idx.exists():
        return FileResponse(idx, media_type="text/html")
    raise HTTPException(status_code=404, detail="frontend/index.html not found")


if _FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_FRONTEND_DIR)), name="static")

logger.info("Static frontend served from %s", _FRONTEND_DIR)
