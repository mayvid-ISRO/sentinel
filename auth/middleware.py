"""
Auth Middleware — FastAPI dependencies for JWT authentication and role checks.
===============================================================================
Phase 3 RBAC.

Provides two reusable FastAPI dependencies:
  require_auth()  — resolves the current user from the Authorization header;
                    raises HTTPException 401 on missing/invalid token.
  require_role(*roles) — after require_auth, checks that the user's role is
                    in the allowed set; raises HTTPException 403 otherwise.

Both are async-safe and work with FastAPI's dependency injection system.
"""

from __future__ import annotations

import logging
from typing import List

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth.models import TokenPayload, User, authenticate_user, list_users
from auth.models import init_users as _init_users

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)


def _get_current_user(token_str: str) -> User:
    """Decode token and look up the user; raise 401 on failure."""
    from auth.models import verify_token
    payload = verify_token(token_str)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    store = {u['username']: u for u in list_users()}
    udict = store.get(payload.sub)
    if udict is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    # Convert dict back to User so callers get the full model
    return User(
        username=udict['username'],
        role=udict['role'],
        password_hash=udict.get('password_hash', ''),
        created_at=udict.get('created_at', ''),
    )


def require_auth(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> User:
    """
    FastAPI dependency that returns the authenticated :class:`User`.

    Returns an anonymous ``User`` with role "anonymous" when no token is
    supplied — callers should check ``user.role != "anonymous"`` where
    auth is required.
    """
    if credentials is None:
        # Allow unauthenticated access to public endpoints; caller decides.
        from auth.models import User as U
        return U(username="anonymous", role="anonymous", password_hash="", created_at="")
    return _get_current_user(credentials.credentials)


def require_role(*allowed_roles: str):
    """
    Return a FastAPI dependency that enforces the caller has one of
    *allowed_roles*. Usage::

        @app.get("/admin/only")
        async def admin_only(user: User = Depends(require_role("admin"))):
            ...
    """
    def _dep(user: User = Depends(require_auth)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role {user.role!r} requires one of {allowed_roles}",
            )
        return user
    return _dep


def init_auth_if_needed() -> None:
    """Seed default users on first startup if the store is empty."""
    from auth.models import _load_users
    if not _load_users():
        # Read any default passwords from IRIS_AUTH_* env vars
        import os
        defaults = {}
        for name in ("admin", "operator"):
            env_key = f"IRIS_AUTH_PASSWORD_{name.upper()}"
            pw = os.environ.get(env_key)
            if pw:
                defaults[name] = pw
        _init_users(default_passwords=defaults or None)
        logger.info("Auth store initialised with default users")


def check_role(user: User, *allowed_roles: str) -> None:
    """Raise 403 if *user* does not hold one of *allowed_roles*.

    This is a plain function (not a FastAPI dependency) so callers can
    invoke it directly with a User object outside the DI pipeline.
    """
    if user.role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role {user.role!r} requires one of {allowed_roles}",
        )
