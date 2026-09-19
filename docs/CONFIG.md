# Configuration System

**Feature**: Central runtime configuration for all of IRIS.
**Status**: Phase 0, shipped.
**Files**: `iris_config.py` (loader), `config.example.json` (template),
`config.json` (live, gitignored), `docs/CONFIG.md`.

## What it does

Before this, the LLM URL/model were hardcoded in `agent/llm.py` (with
commented-out API keys in source), CORS was `*`, and every subsystem had
its own scattered constants. Now there is exactly one place to configure
IRIS, and it never requires a code change.

## Precedence (highest wins)

1. **Environment variables** — `IRIS_<SECTION>_<KEY>` (e.g.
   `IRIS_LLM_URL`, `IRIS_LLM_MODEL`, `IRIS_BACKEND_PORT`,
   `IRIS_EVENTS_ENABLED=true`). Values are type-coerced to match the
   config value they replace.
2. **`config.json`** in project root — deep-merged over defaults.
3. **Defaults in `iris_config.DEFAULTS`** — always offline-safe values.

## API

```python
from iris_config import get, get_config, PROJECT_ROOT

get("llm", "url")           # one key: "http://10.61.241.249:11434/api/generate"
get("llm")                  # whole section dict
get_config()                # full merged config (cached)
```

- Config is loaded once and cached (`_Config` singleton). Tests can point
  `iris_config.CONFIG_PATH` at a tmp file and construct a fresh `_Config()`.
- A broken `config.json` (bad JSON) falls back to defaults instead of
  crashing the system — health endpoint surfaces the values in use.

## Schema (top-level sections)

| Section | Keys | Used by |
|---------|------|---------|
| `llm` | `url`, `model`, `timeout_seconds`, `max_retries`, `retry_backoff_seconds` | agent/llm.py |
| `agent` | `max_steps`, `error_budget`, `step_delay_seconds` | agent/agent.py, backend |
| `backend` | `host`, `port`, `cors_origins` | backend/main.py |
| `security` | `redact_patterns` (regex list), `redact_placeholder` | agent/redact.py |
| `credentials` | `store_path` (default `.iris/credentials.json`) | tools/credentials.py |
| `nodes` | list of `{name, host, port, token_env}` — remote iris_node fleet | tools/__init__.py, tools/web/node_tools.py |
| `events` | `enabled`, `mode`, `bus_buffer`, `syslog{}`, `metrics{}`, `watcher{}`, `scheduler{}`, `winlog{}`, `netapp_ems{}`, `rules_file` | events/ (see EVENTS.md) |

## How to add a new setting

1. Add it (with a safe default) to `DEFAULTS` in `iris_config.py`.
2. Add it to `config.example.json` with a comment-by-example value.
3. Consume with `get("section", "key")` — never `os.environ` or a literal
   anywhere else.
4. If it's a secret (token/password): it does NOT go here. Use an env var
   read at point-of-use (see node `token_env`) or tools/credentials.py.

## Env-var override mechanics (for maintainers)

`_apply_env()` splits `IRIS_BACKEND_PORT` into the longest section/key
combination that actually exists in the merged config, so new sections
work automatically. Boolean parsing accepts `1/true/yes`; ints/floats are
coerced; malformed values are ignored (file value kept).

## Testing

`tests/test_config_redact_credentials.py::TestConfigLoader` — file
override, env override, deep-merge keeps siblings, broken-file fallback.
