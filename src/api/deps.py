"""FastAPI dependencies — database sessions, authentication, authorization.

Usage in route handlers::

    from src.api.deps import get_db, get_current_user, require_admin

    @router.get("/items")
    async def list_items(db = Depends(get_db)):
        ...

    @router.get("/secret")
    async def secret(user = Depends(require_admin)):
        ...
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

try:
    from src.core.logger import get_logger

    logger = get_logger("1ai-auto-hunt.api.deps")
except ImportError:
    import logging

    logger = logging.getLogger("1ai-auto-hunt.api.deps")  # type: ignore[assignment]

_bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Database session — delegates to core.db.get_db when available
# ---------------------------------------------------------------------------

async def get_db():
    """Yield an async database session.

    Delegates to ``src.core.db.get_db`` (the canonical FastAPI dependency)
    when the core module is available.  Otherwise raises 503 so that routes
    still resolve at import time.
    """
    try:
        from src.core.db import get_db as _core_get_db

        async for session in _core_get_db():
            yield session
    except ImportError:
        logger.warning("src.core.db not available — database dependency is a stub")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured",
        )


# ---------------------------------------------------------------------------
# Current user (JWT)
# ---------------------------------------------------------------------------

async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ] = None,
) -> dict[str, Any]:
    """Decode and validate the Bearer JWT, returning the user payload dict.

    Raises ``401`` if the token is missing, expired, or invalid.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # Resolve secret + algorithm from core config or environment
    try:
        from src.core.config import get_settings

        settings = get_settings()
        secret = settings.secret_key
        algorithm = getattr(settings, "jwt_algorithm", "HS256")
    except ImportError:
        import os

        secret = os.environ.get("HUNT_SECRET_KEY", "change-me-in-production")
        algorithm = "HS256"

    try:
        import jwt  # PyJWT

        payload: dict[str, Any] = jwt.decode(token, secret, algorithms=[algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject claim",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return {
        "id": user_id,
        "email": payload.get("email", ""),
        "role": payload.get("role", "user"),
        "payload": payload,
    }


# ---------------------------------------------------------------------------
# Role gates
# ---------------------------------------------------------------------------

_current_user = Annotated[dict[str, Any], Depends(get_current_user)]


async def require_admin(user: _current_user) -> dict[str, Any]:
    """Dependency that enforces the ``admin`` role.

    Use as ``Depends(require_admin)`` on protected endpoints.
    """
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


async def require_role(role: str):
    """Return a dependency that enforces a specific role.

    Usage::

        @router.get("/ops")
        async def ops(user = Depends(require_role("operator"))):
            ...
    """

    async def _gate(user: _current_user) -> dict[str, Any]:
        if user.get("role") != role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role}' required",
            )
        return user

    return _gate
