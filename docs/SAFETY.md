# Safety & Secrets

**Feature**: Command safety policy, node parity, credential store, secret
redaction.
**Status**: Phase 0 (fork-bomb regex bug fixed; node hardening shipped).
**Files**: `tools/safety.py`, `iris_node/main.py` (mirrored policy),
`tools/credentials.py`, `agent/redact.py`, `tools/netapp/netapp.py`
(credential auto-fill), `tools/system/terminal.py`, `tests/test_safety.py`,
`tests/test_config_redact_credentials.py`.

## Command safety (tools/safety.py)

`SafetyChecker.is_command_safe(command)` → `(bool, reason)`; the
terminal tool refuses blocked commands. Blocked patterns (regex,
case-insensitive):

```
rm -rf / | rm -rf /* | del /f /q X: | format X: | shutdown
rmdir /s | dd if=…of=/dev/ | fork bomb | mkfs.* | (suspicious keywords)
```

**Historical bug (fixed this drop, caught by tests)**: the fork-bomb
pattern was written `r':()\{\s*:\|:&\s*\};:'` — the unescaped `()` is an
*empty capture group*, so the pattern matched nothing and every fork bomb
passed. Correct pattern:
`r':\(\)\s*\{\s*:\|:&\s*\}\s*;\s*:'`. If you touch this list, run
`pytest tests/test_safety.py` — it asserts the pattern actually matches.

Path safety: `is_path_safe` allowlist (C:\Users, C:\Temp, /tmp, /home) vs
forbidden (System32, /etc, /bin, /usr/bin).

### Node parity rule (critical invariant)

`iris_node/main.py` carries the SAME `BLOCKED_PATTERNS` list (plus Windows
additions: `reg delete /f HKLM`, `diskpart`, `cipher /w`). Rationale: the
node is a remote shell; if the server blocks `format C:` but a node
doesn't, an agent (or an attacker who reaches the node port) bypasses
server-side safety by just targeting a node.
`tests/test_safety.py::TestNodeSafetyParity` imports the node module and
asserts **every server pattern exists verbatim in the node list** — if you
add a pattern to `tools/safety.py` you MUST add it to `iris_node/main.py`
or CI fails.

### Node auth (mandatory)

IRIS Node refuses to start without `IRIS_NODE_TOKEN` (generate:
`python -c "import secrets; print(secrets.token_hex(32))"`). The server
side reads the token from the `token_env` variable of each node entry in
config.json — tokens never live in the config file.

## Credential store (tools/credentials.py)

Persists per-domain credentials to `.iris/credentials.json` (path from
config `credentials.store_path`; chmod 600 on POSIX).

```python
import tools.credentials as cred
cred.save("netapp", {"cluster": …, "api_user": …, "api_pass": …})
cred.load("netapp")     # {} if missing
cred.clear("netapp")    # or clear() for all
```

Design decision: values are plain JSON on the trusted air-gapped host,
protected by filesystem permissions — never logged, never returned by
status endpoints. The tradeoff (no keychain dependency offline) is
deliberate; revisit if IRIS ever runs on a shared host.

### NetApp auto-fill (tools/netapp/netapp.py)

`_wrap_netapp_tool` wraps every `netapp_*` tool: fresh credentials passed
by the LLM are saved on first use; later calls with missing connection
args are auto-filled from the store; if still missing, the tool returns
`{"status": "prompt_required", "missing_fields": [...]}` telling the agent
to ask the user — instead of crashing. Two extra tools:
`netapp_save_credentials`, `netapp_status` (reports saved-ness, never the
password).

## Redaction (agent/redact.py)

`redact(text)` applies `security.redact_patterns` regexes (default:
`(password|passwd|api_pass|secret|token)\s*[:=]\s*(\S+)`) keeping the key
name and masking the value with `«REDACTED»`. Applied at:
- agent step results + observations (agent/agent.py)
- task text before DB insert (backend/main.py)
- event messages on bus entry (events/bus.py)

Config reloads drop the compiled-pattern cache — `redact.clear_cache()`
exists for tests.

## Extending the policy

1. Add the regex to BOTH `tools/safety.py` and `iris_node/main.py`
   (or a Windows-only concern may go node-only — the parity test only
   checks server ⊆ node).
2. Add a test to `tests/test_safety.py` proving the pattern matches what
   it claims to block (the fork-bomb lesson).
3. For new redaction patterns: `security.redact_patterns` in config; add
   a leak-assertion test like `test_password_pattern_redacted`.
