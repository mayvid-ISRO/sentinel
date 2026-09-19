"""
IRIS Authentication — Phase 3 RBAC
==================================
User model, password hashing (bcrypt), JWT issuance/validation, and an
in-memory user registry seeded from config.

Public API:
  from auth.models import create_user, authenticate_user, verify_token
  from auth.policy    import tool_risk, requires_approvals
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import bcrypt
import jwt

logger = logging.getLogger(__name__)

# ── Defaults ───────────────────────────────────────────────────────────────

DEFAULT_SECRET = "iris-local-dev-secret-change-for-production"
DEFAULT_ALGORITHM = "HS256"
DEFAULT_EXPIRE_MINUTES = 480  # 8 hours

# Roles and their capabilities
ROLE_VIEWER   = "viewer"
ROLE_OPERATOR = "operator"
ROLE_ADMIN    = "admin"

# Built-in users created on first init_db() if none exist
DEFAULT_USERS = {
    "admin": {"role": ROLE_ADMIN,    "password_hash": None},  # set on init
    "operator": {"role": ROLE_OPERATOR, "password_hash": None},
}


# ── Data classes ───────────────────────────────────────────────────────────

@dataclass
class User:
    username: str
    role: str
    password_hash: str          # bcrypt hash bytes, stored as string
    created_at: str             # ISO timestamp

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "role": self.role,
            "password_hash": self.password_hash,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "User":
        # Handle legacy dicts that may lack password_hash (defensive migration)
        if "password_hash" not in d:
            d = {**d, "password_hash": ""}
        return cls(**d)


@dataclass
class TokenPayload:
    sub: str                    # username
    role: str
    exp: datetime
    iat: datetime

    def to_dict(self) -> dict:
        return asdict(self)


# ── Persistence (SQLite via same DB as tasks/events) ─────────────────────

USERS_DB_KEY = "users"       # key in iris_config for user store path


def _user_store_path() -> Path:
    """Return the file used to persist the user registry."""
    from iris_config import PROJECT_ROOT
    return PROJECT_ROOT / ".iris" / "users.json"


def _load_users() -> Dict[str, User]:
    """Load users from disk; return empty dict if absent."""
    p = _user_store_path()
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return {k: User.from_dict(v) for k, v in raw.items()}
    except Exception as exc:
        logger.warning("Could not parse user store (%s); starting fresh", exc)
        return {}


def _save_users(users: Dict[str, User]) -> None:
    p = _user_store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({k: v.to_dict() for k, v in users.items()}, indent=2),
        encoding="utf-8",
    )
    # Restrict permissions on POSIX systems
    try:
        p.chmod(0o600)
    except OSError:
        pass


def init_users(default_passwords: Optional[Dict[str, str]] = None) -> Dict[str, User]:
    """
    Seed the user store with DEFAULT_USERS if none exist.
    *default_passwords* maps usernames → plain-text passwords to hash.
    Returns the merged user dict.
    """
    store = _load_users()
    if store:
        return store

    import iris_config
    secret_cfg = iris_config.get("auth", "secret", default=DEFAULT_SECRET)
    expire_cfg = iris_config.get("auth", "token_expire_minutes", default=DEFAULT_EXPIRE_MINUTES)
    logger.info(
        "Seeding %d default user(s) (secret=%s…%d chars, expiry=%d min)",
        len(DEFAULT_USERS), secret_cfg[:4], len(secret_cfg), expire_cfg,
    )
    hashed = {}
    for username, info in DEFAULT_USERS.items():
        pw = (default_passwords or {}).get(username, "change-me")
        hashed[username] = User(
            username=username,
            role=info["role"],
            password_hash=_hash_password(pw),
            created_at=datetime.utcnow().isoformat(),
        )
    _save_users(hashed)
    return hashed


# ── Password helpers ───────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def _check_password(password: str, hash_str: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hash_str.encode("utf-8"))


# ── CRUD ──────────────────────────────────────────────────────────────────

def create_user(username: str, password: str, role: str = ROLE_OPERATOR) -> User:
    """Create a new user; raises ValueError on duplicate or invalid role."""
    valid_roles = {ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN}
    if role not in valid_roles:
        raise ValueError(f"Invalid role {role!r}; must be one of {valid_roles}")
    store = _load_users()
    if username in store:
        raise ValueError(f"User {username!r} already exists")
    user = User(
        username=username,
        role=role,
        password_hash=_hash_password(password),
        created_at=datetime.utcnow().isoformat(),
    )
    store[username] = user
    _save_users(store)
    return user


def authenticate_user(username: str, password: str) -> Optional[User]:
    """Return the User if credentials match, else None."""
    store = _load_users()
    user = store.get(username)
    if user is None:
        return None
    if not _check_password(password, user.password_hash):
        return None
    return user


def update_user_password(username: str, new_password: str) -> bool:
    store = _load_users()
    if username not in store:
        return False
    store[username].password_hash = _hash_password(new_password)
    _save_users(store)
    return True


def list_users() -> List[dict]:
    return [u.to_dict() for u in _load_users().values()]


def delete_user(username: str) -> bool:
    store = _load_users()
    if username not in store:
        return False
    del store[username]
    _save_users(store)
    return True


# ── JWT ───────────────────────────────────────────────────────────────────

def _jwt_secret() -> str:
    from iris_config import get as cfg_get
    return cfg_get("auth", "secret", default=DEFAULT_SECRET)


def _jwt_expire_minutes() -> int:
    from iris_config import get as cfg_get
    return int(cfg_get("auth", "token_expire_minutes", default=DEFAULT_EXPIRE_MINUTES))


def issue_token(username: str, role: str) -> str:
    """Create a signed JWT for *username* with the given *role*."""
    now = datetime.utcnow()
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=_jwt_expire_minutes()),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=DEFAULT_ALGORITHM)


def verify_token(token: str) -> Optional[TokenPayload]:
    """Validate and decode a JWT; returns None on any failure."""
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=[DEFAULT_ALGORITHM])
        return TokenPayload(
            sub=payload["sub"],
            role=payload["role"],
            exp=datetime.fromtimestamp(payload["exp"]),
            iat=datetime.fromtimestamp(payload["iat"]),
        )
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, KeyError):
        return None
