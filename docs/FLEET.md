# Fleet & Multi-Agent Parallelism (Phase 5)

**Feature**: Manage multiple remote IRIS nodes (iris_node services) from a central server, with automatic health polling and parallel task dispatch across the fleet.

**Status**: Phase 5, shipped. 23 new tests (141 total).

**Files**:
- `fleet/__init__.py` — Package exports
- `fleet/registry.py` — `FleetRegistry`: in-memory node store with RLock-based thread safety, periodic health ping via background thread, JSON persistence to `.iris/nodes.json`
- `agent/parallel.py` — `ParallelDispatcher`: async fan-out of a user task to all healthy fleet nodes
- Backend: `backend/main.py` (`/api/fleet/*` endpoints)
- Frontend: Fleet tab panel (node list with live health status)

## Architecture

```
Config (nodes section) ──► FleetRegistry.__init__
                              │
                              ├─ load_initial_nodes() → .iris/nodes.json
                              │
                              ├─ start_polling() ──► background thread
                              │                        │
                              │                        ├─ every POLL_INTERVAL seconds:
                              │                        │   for each node:
                              │                        │     POST /health → mark_healthy / mark_unhealthy
                              │                        │   _cleanup_stale() → remove nodes older than HEALTH_TTL
                              │
                              └─ add_node(name, host, port, token_env)
                                 remove_node(name)
                                 list_nodes() / get_node(name)
                                 get_token(name) ← reads env var
```

### Key = node name

Each entry in the fleet is identified by a human-readable name (e.g. `workstation-01`). The registry stores the connection endpoint `(host, port)` and an optional `token_env` pointing to the environment variable holding the node's auth token.

## Health Model

| State | Meaning |
|-------|---------|
| `unknown` | Node registered but never probed |
| `healthy`  | Last `/health` ping returned 200 within `HEALTH_TTL` seconds |
| `unhealthy` | Last probe failed or was too old |

Stale nodes (no health ping for `HEALTH_TTL = 120s`) are automatically removed from the registry to keep the fleet clean.

## Configuration

Add nodes to `config.json`:

```json
"nodes": [
  {
    "name": "workstation-01",
    "host": "192.168.1.50",
    "port": 9000,
    "token_env": "IRIS_NODE_TOKEN_W01"
  }
]
```

The node's auth token is **never stored in config**. It is read at request time from the named environment variable (`IRIS_NODE_TOKEN_W01` in this example). This means you can rotate tokens without touching the config file.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/fleet/nodes` | List all nodes with current health state |
| POST | `/api/fleet/nodes` | Add a node `{name, host, port, token_env}` |
| DELETE | `/api/fleet/nodes/{name}` | Remove a node by name |
| POST | `/api/fleet/run` | Dispatch a task to all healthy nodes `{task, max_steps}` |
| GET | `/api/fleet/health` | Lightweight summary `{total, healthy, unhealthy, unknown}` |

## ParallelDispatcher

`ParallelDispatcher` takes a user task string and fans it out to every healthy node concurrently. Results are gathered and returned as:

```python
{
  "summary": {"total": N, "healthy": M, "ok": K, "failed": L},
  "results": {
    "node-name-1": {"status": "ok", "output": "..."},
    "node-name-2": {"status": "error", "error": "..."},
    ...
  }
}
```

Currently each dispatched task runs a simple terminal echo command on the target node. In future phases, the dispatcher will route through the full agent loop per node.

## Thread Safety

`FleetRegistry` uses `threading.RLock` (reentrant lock) because internal methods call each other while holding the lock (e.g., `add_node()` calls `save_nodes()` which acquires `_lock` again). A regular `threading.Lock` would deadlock in this pattern.

## Zero External Dependencies

This module uses only Python stdlib: `json`, `threading`, `time`, `pathlib`, `dataclasses`, `logging`, `os`. For health polling it uses `urllib.request` (stdlib). No aiohttp, no httpx, no external HTTP client — suitable for air-gap deployment.

If the `fleet/` package is absent, the backend gracefully falls back: all fleet endpoints return empty lists and the fleet tab shows "NO NODES REGISTERED".

## Testing

```bash
python -m pytest tests/test_fleet.py -v     # 23 fleet tests
python -m pytest tests/ -q                   # 141 total
```

Tests cover:
- Registry CRUD: add, list, update, remove
- Health state: mark_healthy, mark_unhealthy
- Persistence: save/load round-trip from disk
- Stale cleanup: auto-remove after HEALTH_TTL
- Token resolution: reads env var correctly
- Parallel dispatch: empty fleet, single healthy node, single failed node
- API endpoints: list, add, delete, health summary, run, validation errors
