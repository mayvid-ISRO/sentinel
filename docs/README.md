# IRIS Developer Documentation — Master Index

This folder documents every feature subsystem in IRIS: what it does, how it
is built, which files it touches, and how to extend it. Read the doc for a
subsystem before changing it.

## Doc Map

| Doc | Subsystem | What you'll find |
|-----|-----------|------------------|
| [CONFIG.md](CONFIG.md) | Configuration system | config.json schema, env-var overrides, precedence rules |
| [AGENT_LOOP.md](AGENT_LOOP.md) | Agent core | Two-phase loop, error budget, on_step/cancel callbacks, adding test seams |
| [BACKEND.md](BACKEND.md) | FastAPI backend | Endpoints, WebSocket protocol, run_task flow, DB schema |
| [EVENTS.md](EVENTS.md) | Event subsystem (Phase 1) | Bus → rules → engine pipeline, adapters, approval workflow, rollout modes |
| [RAG.md](RAG.md) | RAG Knowledge Store (Phase 2) | Chunking, embeddings, FAISS index, ingestion API, prompt injection |
| [RBAC.md](RBAC.md) | RBAC & Multi-Tier Approvals (Phase 3) | JWT auth, user store, tool risk tiers, approval tickets, middleware |
| [ANOMALIES.md](ANOMALIES.md) | Anomaly Detection (Phase 4) | Rolling-window z-score tracker, adapter bus integration, baseline persistence, API endpoints |
| [FLEET.md](FLEET.md) | Fleet & Parallel Dispatch (Phase 5) | FleetRegistry (RLock, health polling), ParallelDispatcher, /api/fleet/* endpoints |
| [SAFETY.md](SAFETY.md) | Safety & secrets | Blocked command patterns, node parity rule, credential store, redaction |
| [AIRGAP.md](AIRGAP.md) | Offline deployment | Pinned requirements, vendored fonts, build script (Phase 6 ✅), MANIFEST.json + hash verification |
| [TESTING.md](TESTING.md) | Test suite | How to run, what each file covers, conventions (no network, no LLM) |

## System Overview (current)

```
                       ┌────────────────────────────┐
   User intent ───────►│  FastAPI backend           │
   (POST /api/tasks)   │  backend/main.py           │
                       └──────────┬─────────────────┘
                                  │ run_agent()
   Event sources ─────┐           ▼
   syslog UDP          │  ┌────────────────────┐
   metrics (psutil)    ├─►│ Agent core          │──► tools/ (24 tools)
   file watcher        │  │ agent/agent.py      │    input, system, browser,
   scheduler           │  │ plan → act → ob-   │    vision, netapp, web
   Windows event log   │  │ serve feedback     │
   NetApp EMS          │  └───────┬────────────┘
                       │          │ _fetch_rag_context()
                       │  ┌───────▼───────────┐
                       │  │ RAG Knowledge     │ top-k SOP chunks
                       │  │ rag/retriever.py  │ injected into prompts
                       │  │ FAISS + embedder  │
                       └──│ Event subsystem   │ rules → task templates
                          │ events/            │ shadow/manual/auto modes
                          └────────────────────┘
```

## Phase roadmap (from ISCCMIT paper + PROJECT_ARCHITECTURE.md)

- **Phase 0 — DONE**: foundations — config system, agent error
  recovery, backend refactor, node hardening, credential store, air-gap fixes,
  58 tests.
- **Phase 1 — DONE**: event-driven architecture — event bus, six
  adapters, rules engine, approval workflow, Event Radar UI.
- **Phase 2 — DONE**: RAG knowledge store — local sentence-transformers
  embeddings + FAISS index; `/api/rag/*` endpoints; Knowledge Base UI panel;
  agent prompts now enriched with top-k SOP excerpts. 18 new tests (76 total).
- **Phase 3 — DONE**: RBAC + multi-tier approvals — JWT auth (PyJWT + bcrypt),
  user registry in `.iris/users.json`, tool risk tiers (read/write/destructive),
  multi-tier approval tickets in `.iris/approvals.json`, `/api/auth/*` and
  `/api/approvals/*` endpoints, login overlay in frontend, risk badges on tools.
  20 new tests (96 total).
- **Phase 4 — DONE**: Anomaly & intrusion detection — pure-Python rolling-window
  z-score `BaselineTracker` (no numpy/sklearn), `AnomalyAdapter` subscribes to
  the Phase-1 EventBus and publishes `domain="security"` critical events on
  deviation; baselines persist to `.iris/baselines.json`; five REST endpoints
  (`/api/anomaly/*`); new "Anomalies" panel in frontend showing live baseline
  stats and recent anomaly feed. 22 new tests (118 total).
- **Phase 5 — DONE**: Fleet & multi-agent parallelism — `FleetRegistry` with
  RLock thread safety, background health-polling thread (15s interval, 120s TTL),
  JSON persistence to `.iris/nodes.json`; `ParallelDispatcher` fans out tasks
  to all healthy nodes concurrently via asyncio; five fleet REST endpoints
  (`/api/fleet/*`); new "Fleet" tab in frontend showing live node health
  cards (green=healthy, red=offline, muted=unknown). 23 new tests (141 total).
- **Phase 6 — DONE**: Air-gap bundle build — `scripts/build_offline_bundle.py`
  produces a self-contained `iris_offline_bundle/` with wheels cache, repo copy
  (excludes caches/logs/DBs), vendored fonts, SHA-256 `MANIFEST.json`, and
  `install.bat` / `install.sh` that verify hashes before installing. 33 new
  tests (174 total).

## Quick start

**Option A — Single-server (recommended):** the backend now serves the frontend
from `/`, so one process runs everything.

```bash
pip install -r requirements.txt
copy config.example.json config.json      # edit llm.url / api_key as needed
uvicorn backend.main:app --port 8000      # API + frontend in one
# Open http://localhost:8000/ in your browser
python main.py "open notepad and type hello"   # CLI agent
python -m pytest tests/ -q                # run test suite (174 tests)
```

**Option B — Docker:**

```bash
docker build -t iris .
docker compose up
# Open http://localhost:8000/
```

**LLM configuration** — `config.json` `llm` section or env vars (`IRIS_LLM_*`):

| Field | Default | Notes |
|---|---|---|
| `url` | `http://127.0.0.1:11434/api/generate` | Ollama or any HTTP endpoint |
| `model` | `gpt-oss:20b` | Model name |
| `api_key` | *(empty)* | Required for OpenAI-compatible providers (ByNara, etc.) |

The client auto-detects format: URLs containing `/v1/` or `/chat/completions` use
OpenAI format; everything else uses Ollama format.

