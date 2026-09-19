# CLAUDE.md

Guidance for AI coding assistants working in this repository.

## Project

IRIS — agentic IT-infrastructure management for air-gapped ISRO
environments. Two-phase agent (plan → act/observe) with a tool registry,
FastAPI backend + web UI, an event-driven subsystem, a local RAG knowledge
store, and remote-node control. **Read `docs/README.md` first** — it
indexes per-feature developer docs (CONFIG, AGENT_LOOP, EVENTS, RAG,
SAFETY, BACKEND, AIRGAP, TESTING).

## Commands

```bash
# setup
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt

# run
uvicorn backend.main:app --port 8000     # API + UI backend + events + RAG
python main.py "open notepad and type hello"   # CLI agent
python -m http.server 3000                # serve frontend (from repo root!)

# test
python -m pytest tests/ -q          # 174 tests, no network/LLM needed
```

Config: `copy config.example.json config.json` — set `llm.url` to the
Ollama host; set `rag.enabled: true` and ingest documents via
`/api/rag/ingest` or the CLI helper. Never hardcode URLs/models/secrets.

## Architecture (post Phase-4)

```
agent/          run_agent loop, LLM wrapper, redaction, prompts, _fetch_rag_context
tools/          Tool registry: input/ system/ browser/ vision/ netapp/ web/
                + credentials.py (store), safety.py, base.py
events/         schema, bus, rules(.json), engine, adapters/, service.py
rag/            config, chunker, embedder (sentence-transformers), store (FAISS), retriever
auth/           models (User, bcrypt, JWT), policy (risk tiers), middleware (HTTPBearer),
                approvals (multi-tier tickets persisted to .iris/approvals.json)
anomalies/      BaselineTracker (rolling-window z-score), config, AnomalyAdapter (bus subscriber),
                persists baselines to .iris/baselines.json — zero external deps (no numpy/sklearn)
fleet/          FleetRegistry (RLock thread-safety, background health polling, JSON persistence),
                node CRUD + token resolution from env vars, stale-node auto-cleanup at HEALTH_TTL
agent/parallel.py ParallelDispatcher — async fan-out of a user task to all healthy fleet nodes,
                  gathers results into {node_name: result} dict with ok/error status per node
backend/main.py FastAPI: tasks, WebSocket, events API, approvals, auth, /api/rag/*, /api/anomaly/*, /api/fleet/*
frontend/       single-page UI (Event Radar + Knowledge Base + Anomalies + Fleet tabs + task console + login overlay)
iris_node/      remote-PC agent service (token-auth, own safety layer)
iris_config.py  THE config loader (config.json + IRIS_* env vars)
docs/           per-feature developer documentation
tests/          pytest suite (174 tests)
```

## Rules for changes

- **Config**: all settings via `iris_config.get(...)`; add new ones to
  `DEFAULTS` + `config.example.json`. Secrets go in env vars or
  `tools/credentials.py` — never config files or code.
- **Safety**: blocked-command patterns live in `tools/safety.py` AND must
  be mirrored in `iris_node/main.py` — parity is test-enforced. Any change
  to patterns requires a test that proves the pattern matches.
- **Agent loop**: error budget (`agent.error_budget`) governs consecutive
  failures; don't reset it on LLM response, only on tool success. Tests
  use the `TOOLS_REF` seam — don't remove it.
- **Events**: new sources = new adapter in `events/adapters/` + registry
  entry + config section. Rules are data (`events/rules.json`,
  hot-reloaded); modes shadow/manual/auto; LLM-triage tasks are always
  manual.
- **RAG**: new doc formats = add reader to `rag/chunker.py` READERS dict.
  Model weights are cached locally after first download — `models/` dir is
  the air-gap transfer target. New thresholds/edit config via `[rag]` in
  iris_config only.
- **RBAC/Auth**: new users = call `init_users()` on startup; passwords are
  bcrypt-hashed in `.iris/users.json`. Risk tiers live in `auth/policy.py`
  — override via `rbac.tool_risk_overrides` in config. Approval tickets go
  to `.iris/approvals.json`. New tool risk mappings must be added to both
  `auth/policy.py` AND its test expectations. JWT secret set via `[auth]`
  section or env var `IRIS_AUTH_SECRET`.
- **Air-gap**: no CDN links in frontend, no runtime downloads, pinned
  versions only in requirements.txt (see docs/AIRGAP.md checklist).
- **Anomalies**: new metrics = just ensure the source event carries a
  numeric `value` field; `BaselineTracker.check(source_type, entity, metric, value)`
  auto-keys off the `(source_type, entity, metric)` triple. Baselines live
  in `.iris/baselines.json`. Config via `[events.anomaly]` in iris_config
  and `IRIS_ANOMALY_*` env vars. Adapter wires into `events/adapters/__init__.py`
  BUILDERS — if `anomalies/` package is absent, it falls back to `None`
  silently. Anomaly events publish with `domain="security"`, `severity="critical"`
  into the same EventBus; rules.json can route them like any other event.
- **Fleet/Nodes**: new nodes added via `config.json "nodes"` array or POST
  `/api/fleet/nodes`. Node tokens come from env vars named by `token_env`,
  never stored in config. Health polling runs in a daemon thread every 15s;
  stale nodes (>120s since last ping) are auto-removed. To add a remote agent:
  deploy `iris_node/main.py` on the target PC, set `IRIS_NODE_TOKEN_*`, then
  register via config or the fleet API. The `ParallelDispatcher` fans tasks
  out to all healthy nodes concurrently using asyncio.gather.
- **Redaction**: anything user/LLM-provided that gets logged or stored
  passes through `agent/redact.py`.

## Testing rules

Tests run with no network, no LLM, no real infra. Monkeypatch `call_llm`;
inject tools via `TOOLS_REF`. Every bug fix ships with the test that
would have caught it (see docs/TESTING.md case studies).
