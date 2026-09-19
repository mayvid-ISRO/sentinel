# Agent Loop

**Feature**: The two-phase agent (plan → act/observe) with error recovery,
progress callbacks, and cancellation.
**Status**: Phase 0 rework of the original loop.
**Files**: `agent/agent.py` (loop + parser + validator), `agent/llm.py`
(provider wrapper), `agent/prompt.py`, `agent/pre_prompt.py`,
`agent/dispatcher.py`, `agent/redact.py`. Consumers: `main.py` (CLI),
`backend/main.py` (run_task), `events/engine.py` (LLM triage).

## What changed in Phase 0 (and why)

1. **Error budget instead of abort-on-first-failure.**
   The old loop `break`-ed out of the run the moment a tool raised — the
   paper claims observation-feedback recovery, but the code never let the
   LLM see a failure and retry. Now a tool failure is appended to the
   observations (so the next prompt contains it), and the loop only stops
   after `agent.error_budget` (default 3) **consecutive failed steps**.
   Any successful tool execution resets the counter. NOTE: a successful
   LLM response does NOT reset it — otherwise a tool that crashes while
   the LLM keeps re-proposing it would loop forever.
2. **`on_step` callback.** The backend used to contain a *duplicated copy*
   of the whole agent loop (with its own parsing + dispatch) so it could
   stream progress. Now `run_agent(..., on_step=cb)` emits each
   `StepResult` as it happens; backend's callback pushes WebSocket frames
   + persists to SQLite. One loop, one place for fixes.
3. **`should_cancel` callback.** `/api/tasks/{id}/cancel` used to only
   flip a DB status the running agent never checked. The loop now polls
   `should_cancel()` before every step and exits cleanly with a
   `cancelled` StepResult.
4. **Redaction everywhere.** Every step result, observation, and log line
   passes through `agent/redact.py` (see SAFETY.md).
5. **LLM retries.** `agent/llm.py` reads url/model/timeout/retries from
   config and retries with exponential backoff instead of crashing on one
   refused connection.

## Loop anatomy (agent/agent.py, run_agent)

```
pre-prompt (build_pre_prompt) ─► call_llm ─► steps_plan
for step 1..max_steps:
    poll should_cancel()            → clean exit
    build_prompt(task, plan)
        + previous observations      → the feedback channel
    call_llm                         → retry/backoff
    parse_llm_response               → 3 formats: ```json block,
                                       ``` block, raw JSON object
    validate_action                  → {tool: str, args: dict}
    tool == "done"                   → record + break (success)
    _dispatch(action)                → TOOLS registry (or test seam)
        ok   → observation "Step N: <result>", reset error counter
        err  → observation with error, ++consecutive_errors
                 (break if >= error_budget)
    sleep(agent.step_delay_seconds)
```

Final status semantics (backend maps them):
- last step `done` → task done
- `cancelled` → task cancelled
- anything else / budget exhausted → failed

## Test seam: TOOLS_REF

`agent/agent.py` exposes `TOOLS_REF = {}`. When non-empty, `_dispatch()`
routes through it instead of the real registry. Tests (and only tests)
monkeypatch this dict to inject stub tools without importing playwright /
netapp / pyautogui. Do not use it in production code paths.

## Adding behaviour

- **New step outcome**: extend `StepResult.status` ('ok','error','done',
  'cancelled' today) and mirror in backend status mapping.
- **Change retry policy**: config `llm.max_retries` /
  `llm.retry_backoff_seconds` — no code change.
- **New observation format for LLM**: edit how observations are appended
  (search `observations.append`) — the observation text IS the prompt
  context; keep it terse or you'll eat the context window.

## Tests

`tests/test_parser.py` (all LLM response formats, action validation),
`tests/test_agent_recovery.py` (failure → recovery → done; budget
exhaustion; on_step; cancellation). All run against a stub LLM — zero
network, zero real tools.
