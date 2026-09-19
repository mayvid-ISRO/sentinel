"""
Tests for Phase 3 RBAC — authentication, JWT, risk policy, approvals.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest


# ══ helpers ═══════════════════════════════════════════════════════════════


def _tmp_store(monkeypatch, tmp_path: Path):
    """Point the user store at a temp file so tests don't touch real data."""
    import auth.models as m
    store_path = tmp_path / "users.json"
    # Patch the function directly so every call site uses the new path
    monkeypatch.setattr(m, "_user_store_path", lambda: store_path)
    # Re-seed init_users to use patched path immediately
    m._load_users.cache_clear() if hasattr(m._load_users, "cache_clear") else None


# ══ auth/models ═══════════════════════════════════════════════════════════


class TestAuthModels:
    def test_init_users_seeds_default(self, monkeypatch, tmp_path):
        _tmp_store(monkeypatch, tmp_path)
        from auth.models import init_users, _load_users
        users = init_users({"admin": "admin123", "operator": "ops456"})
        assert "admin" in users
        assert "operator" in users
        assert users["admin"].role == "admin"
        assert users["operator"].role == "operator"

    def test_init_users_skips_when_exists(self, monkeypatch, tmp_path):
        _tmp_store(monkeypatch, tmp_path)
        from auth.models import init_users, _load_users
        init_users({"admin": "x", "operator": "y"})
        # Second call should not overwrite
        before = {u.username: u.password_hash for u in _load_users().values()}
        init_users({"admin": "changed"})
        after = {u.username: u.password_hash for u in _load_users().values()}
        assert before == after  # unchanged

    def test_authenticate_correct_credentials(self, monkeypatch, tmp_path):
        _tmp_store(monkeypatch, tmp_path)
        from auth.models import init_users, authenticate_user, create_user
        init_users()                     # seed default admins
        create_user("alice", "secret")   # add test user
        user = authenticate_user("alice", "secret")
        assert user is not None
        assert user.username == "alice"

    def test_authenticate_wrong_password(self, monkeypatch, tmp_path):
        _tmp_store(monkeypatch, tmp_path)
        from auth.models import init_users, authenticate_user, create_user
        init_users()
        create_user("bob", "pass")
        assert authenticate_user("bob", "wrong") is None

    def test_authenticate_missing_user(self, monkeypatch, tmp_path):
        _tmp_store(monkeypatch, tmp_path)
        from auth.models import authenticate_user
        assert authenticate_user("nobody", "x") is None

    def test_create_user_duplicate_raises(self, monkeypatch, tmp_path):
        _tmp_store(monkeypatch, tmp_path)
        from auth.models import init_users, create_user
        init_users()
        create_user("carol", "p")
        with pytest.raises(ValueError, match="already exists"):
            create_user("carol", "newpass")

    def test_create_user_invalid_role_raises(self, monkeypatch, tmp_path):
        _tmp_store(monkeypatch, tmp_path)
        from auth.models import init_users, create_user
        init_users()
        with pytest.raises(ValueError, match="Invalid role"):
            create_user("dave", "p", role="superadmin")

    def test_list_users(self, monkeypatch, tmp_path):
        _tmp_store(monkeypatch, tmp_path)
        from auth.models import init_users, list_users, create_user
        init_users()
        create_user("eve", "e")
        names = [u["username"] for u in list_users()]
        assert "eve" in names
        assert "admin" in names

    def test_delete_user(self, monkeypatch, tmp_path):
        _tmp_store(monkeypatch, tmp_path)
        from auth.models import init_users, delete_user, create_user
        init_users()
        create_user("frank", "f")
        assert delete_user("frank") is True
        assert delete_user("frank") is False  # second delete fails
        from auth.models import authenticate_user
        assert authenticate_user("frank", "f") is None


class TestJWT:
    def test_issue_and_verify(self, monkeypatch):
        import iris_config
        monkeypatch.setattr(iris_config, "DEFAULTS", {
            **iris_config.DEFAULTS,
            "auth": {"enabled": True, "secret": "test-secret", "token_expire_minutes": 60},
        })
        iris_config._CONFIG._loaded = False
        from auth.models import issue_token, verify_token
        token = issue_token("admin", "admin")
        payload = verify_token(token)
        assert payload is not None
        assert payload.sub == "admin"
        assert payload.role == "admin"

    def test_verify_invalid_token_returns_none(self):
        from auth.models import verify_token
        assert verify_token("garbage.token.here") is None

    def test_expired_token_returns_none(self, monkeypatch):
        import jwt as _jwt
        import iris_config
        monkeypatch.setattr(iris_config, "DEFAULTS", {
            **iris_config.DEFAULTS,
            "auth": {"enabled": True, "secret": "s", "token_expire_minutes": 0},
        })
        iris_config._CONFIG._loaded = False
        from auth.models import issue_token, verify_token
        token = issue_token("admin", "admin")
        # force expiry by decoding and manually changing exp
        decoded = _jwt.decode(token, "s", algorithms=["HS256"], options={"verify_exp": False})
        decoded["exp"] = decoded["exp"] - 10000
        expired = _jwt.encode(decoded, "s", algorithm="HS256")
        assert verify_token(expired) is None


# ══ auth/policy ═══════════════════════════════════════════════════════════


class TestPolicy:
    def test_known_tools_have_tiers(self):
        from auth.policy import tool_risk, APPROVALS_REQUIRED
        assert tool_risk("browser_dom") == "read"
        assert tool_risk("click_tool") == "write"
        assert tool_risk("netapp_delete_volume") == "write"

    def test_unknown_tool_fails_closed(self):
        from auth.policy import tool_risk
        assert tool_risk("totally_fake_tool_xyz") == "write"

    def test_approvals_required_matches_tier(self):
        from auth.policy import APPROVALS_REQUIRED
        assert APPROVALS_REQUIRED["read"] == 0
        assert APPROVALS_REQUIRED["write"] == 1
        assert APPROVALS_REQUIRED["destructive"] == 2

    def test_classify_action(self):
        from auth.policy import classify_action
        risk, n = classify_action({"tool": "browser_dom", "args": {}})
        assert risk == "read"
        assert n == 0
        risk2, n2 = classify_action({"tool": "netapp_delete_volume", "args": {}})
        assert risk2 == "write"
        assert n2 == 1


# ══ auth/approvals ════════════════════════════════════════════════════════


class TestApprovalManager:
    def test_create_and_sign_single_tier(self, tmp_path):
        from auth.approvals import ApprovalManager
        mgr = ApprovalManager(tmp_path / "t1.json")
        ticket = mgr.create_ticket("task-1", "type_tool", "write", 1, "alice")
        assert ticket.status == "pending"
        updated = mgr.sign_tier("task-1", "bob", "operator", 1)
        assert updated is not None
        assert updated.status == "approved"
        assert len(updated.signatures) == 1

    def test_two_tier_approval(self, tmp_path):
        from auth.approvals import ApprovalManager
        mgr = ApprovalManager(tmp_path / "t2.json")
        mgr.create_ticket("task-2", "delete_volume", "destructive", 2, "alice")
        # tier 1
        t = mgr.sign_tier("task-2", "operator1", "operator", 1)
        assert t.status == "tier1_done"
        # tier 2
        t2 = mgr.sign_tier("task-2", "operator2", "operator", 2)
        assert t2.status == "approved"

    def test_reject(self, tmp_path):
        from auth.approvals import ApprovalManager
        mgr = ApprovalManager(tmp_path / "t3.json")
        mgr.create_ticket("task-3", "click", "write", 1, "alice")
        t = mgr.reject("task-3", "bob", "operator", "not safe")
        assert t is not None
        assert t.status == "rejected"

    def test_double_sign_same_tier_ignored(self, tmp_path):
        from auth.approvals import ApprovalManager
        mgr = ApprovalManager(tmp_path / "t4.json")
        mgr.create_ticket("task-4", "click", "write", 1, "alice")
        mgr.sign_tier("task-4", "bob", "operator", 1)
        # same person signing again is a no-op
        before_count = len(mgr.get("task-4").signatures)
        mgr.sign_tier("task-4", "bob", "operator", 1)
        after_count = len(mgr.get("task-4").signatures)
        assert before_count == after_count == 1
