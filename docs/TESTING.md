# Testing

**Feature**: The regression suite — 58 tests, zero network, zero LLM,
zero real infra.
**Files**: `tests/conftest.py` (path setup), `test_safety.py`,
`test_parser.py`, `test_config_redact_credentials.py`,
`test_agent_recovery.py`, `test_events.py`.

## Run

```bash
python -m pytest tests/ -q          # whole suite (~3 s)
python -m pytest tests/test_events.py::TestRules -q   # one class
python -m pytest tests/test_agent_recovery.py::TestErrorRecovery::test_tool_failure_does_not_abort
```

## Conventions (keep these when adding tests)

1. **No network.** `call_llm` is monkeypatched with a scripted stub; tools
   are injected via the `TOOLS_REF` seam in agent/agent.py (see
   AGENT_LOOP.md). A test that needs the internet is testing nothing.
2. **No real infra.** NetApp/SSH/browser tools are never imported into
   tests — the safety-parity test reads iris_node's pattern list without
   starting FastAPI services.
3. **Assertion-driven bug documentation.** The fork-bomb test asserts the
   pattern actually matches (it documents the bug that shipped before);
   the node-parity test pins server ⊆ node blocked-patterns. When you fix
   a bug, add the test that would have caught it.
4. **Async helpers**: event-bus tests use `_drain(bus, received)` which
   starts the bus loop so queued events reach subscribers — remember
   `bus.publish()` only enqueues; subscribers fire from `_run()`.

## What each file covers

| File | Covers |
|------|-------|
| test_safety.py | blocked patterns actually block; allowed commands pass; iris_node pattern parity; fork-bomb regression |
| test_parser.py | parse_llm_response (```json blocks, bare blocks, raw JSON, garbage) + validate_action contract |
| test_config_redact_credentials.py | config file/env override precedence, deep-merge, broken-file fallback; redaction patterns + no-leak; credential store roundtrip/merge/clear/corruption |
| test_agent_recovery.py | tool failure → recovery → done (the Phase 0 headline behaviour); error budget exhaustion; on_step callback; cancellation |
| test_events.py | Event schema validation/roundtrip; rule matching, thresholds, severity gates, cooldown, template interpolation; bus delivery, redaction-at-entry, overflow drop-lowest-severity; engine modes (manual/shadow/unmatched); adapter behaviour (metrics edge-detection, watcher, syslog classification, scheduler parse) |

## Bugs this suite caught in development (case studies)

1. **Fork-bomb regex never matched anything** — unescaped `()` in the
   pattern = empty capture group. Every fork bomb passed safety. Caught
   by `test_blocks_fork_bomb`.
2. **Error budget never exhausted** — counter reset after every
   successful LLM *response*, so a crashing tool re-proposed forever.
   Caught by `test_error_budget_stops_run` (asserted 3, got 10).
3. **iris_node missing logger import** — adapter crashed at runtime;
   caught during backend end-to-end boot.
4. **Test helper double-subscription** — `_drain` re-subscribed on each
   call, duplicating events; fixed with an idempotent subscribe. (Yes, a
   test-infra bug — included as a reminder that test helpers need the
   same scrutiny.)

## CI / pre-push checklist

```bash
python -m pytest tests/ -q        # 58 passed
python -c "import backend.main"   # backend still imports (catches circulars)
```
