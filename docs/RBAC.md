# RBAC & Multi-Tier Approvals (Phase 3)

**Feature**: Role-based access control with JWT authentication, tool risk tiers, and multi-tier human approval workflows for destructive operations.

**Status**: Phase 3, shipped. 20 new tests (96 total).

**Files**:
- `auth/models.py` — User dataclass, bcrypt password hashing, JWT issuance/validation, in-memory user registry persisted to `.iris/users.json`
- `auth/policy.py` — Tool → risk tier mapping (`read` / `write` / `destructive`) + approvals-required counts
- `auth/middleware.py` — FastAPI `HTTPBearer` dependency, `require_role(*roles)` helper, `init_auth_if_needed()` startup hook
- `auth/approvals.py` — `ApprovalManager` class: create/sign/reject tickets, multi-tier progression, disk persistence
- Backend: `backend/main.py` (`/api/auth/*`, `/api/rbac/tools`, `/api/approvals/*`)
- Frontend: login overlay, auth status badge in header, risk badges on tool list

## Architecture

```
POST /api/auth/login  {username, password}
        │
        ▼ bcrypt check ─► .iris/users.json
   issue_token(username, role)
        │
        ▼ HS256 signed JWT (exp 8h default)
   {"access_token": "eyJ...", "role": "operator"}

GET  /api/auth/me          ─► decode JWT ─► return {username, role}
GET  /api/auth/users       ─► admin only
POST /api/auth/change-password

GET  /api/rbac/tools       ─► {tool, description, risk_tier, approvals_required}

POST /api/approvals/{task_id}/sign  ─► next available tier signature
POST /api/approvals/{task_id}/reject ─► any reviewer can reject
GET  /api/approvals/pending         ─► list open tickets
```

### Risk Tiers

| Tier | Description | Auto-run? | Approvers Needed | Examples |
|------|-------------|-----------|------------------|----------|
| `read` | Query/list only | ✅ Yes | 0 | `browser_dom`, `netapp_status`, `take_screenshot_tool`, `workstation_01_system_info` |
| `write` | Modifies state | ❌ Needs approval | 1 | `browser_click`, `click_tool`, `type_tool`, `netapp_delete_volume`, `workstation_01_terminal` |
| `destructive` | Irreversible action | ❌ Dual approval | 2 | Reserved for future tools; currently no destructive tools in active registry |

Default mappings live in `auth/policy.py:_DEFAULT_TOOL_RISK`. Override per-tool via `rbac.tool_risk_overrides` in `config.json`.

Current distribution (32 tools): **10 read**, **22 write**, **0 destructive**.

Override per-tool via `rbac.tool_risk_overrides` in `config.json`.

### Multi-Tier Approval Flow

1. Agent proposes a `write` or `destructive` tool call
2. `create_task()` checks `auth.policy.approvals_required(tool_name)`
3. If > 0, task status becomes `awaiting_approval` instead of `pending`
4. An `ApprovalTicket` is created in `.iris/approvals.json`
5. Reviewers sign via `POST /api/approvals/{id}/sign` with their JWT
   - Tier 1 sign → status changes to `tier1_done`
   - Tier 2 sign (if needed) → status changes to `approved`
6. On full approval, task transitions to `pending` and runs automatically
7. Any reviewer can `reject` at any time

## Configuration

Add to `config.json`:

```json
"auth": {
  "enabled": true,
  "secret": "your-32-char-minimum-secret-here",
  "token_expire_minutes": 480
},
"rbac": {
  "tool_risk_overrides": {
    "ssh_disconnect_all_tool": "write"
  }
}
```

Default credentials (seeded on first run):
- `admin` / `admin` (role: admin — can manage users, view all)
- `operator` / `operator` (role: operator — can run tasks, sign approvals)

Override passwords via env vars: `IRIS_AUTH_PASSWORD_ADMIN=newpass`.

## Auth Flow (Frontend)

1. Page loads → calls `GET /api/auth/config`
2. If `auth_enabled: true`, shows login overlay
3. User enters credentials → `POST /api/auth/login`
4. JWT stored in `localStorage` as `iris_token`, `iris_role`, `iris_user`
5. Subsequent API calls include `Authorization: Bearer <token>`
6. Header shows current user; click to logout

## Testing

```bash
python -m pytest tests/test_rbac.py -v     # 20 RBAC tests
python -m pytest tests/ -q                  # 96 total
```

RAC tests cover user CRUD, password hashing, JWT encode/decode/expiry, tool risk classification, and multi-tier approval signing/rejection.

## Dependencies Added (requirements.txt)

```
pyjwt==2.14.0        # JWT token issuance and validation
bcrypt==5.0.0        # Password hashing (used by models.py)
```

Both are small, pure-Python-compatible wheels suitable for air-gap bundling.
