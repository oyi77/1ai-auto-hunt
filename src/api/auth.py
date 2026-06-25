"""Authentication router — login, register, current-user profile.

Endpoints::

    POST /auth/register   — create a new account
    POST /auth/login      — obtain a JWT access token
    GET  /auth/me          — return the authenticated user profile
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from src.api.deps import _current_user, get_current_user  # noqa: F811

try:
    from src.core.logger import get_logger

    logger = get_logger("1ai-auto-hunt.api.auth")
except ImportError:
    logger = logging.getLogger("1ai-auto-hunt.api.auth")  # type: ignore[assignment]

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    """Payload for ``POST /auth/register``."""

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    name: str = Field(..., min_length=1, max_length=120)


class LoginRequest(BaseModel):
    """Payload for ``POST /auth/login``."""

    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """JWT token returned on successful authentication."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class UserResponse(BaseModel):
    """Public user profile."""

    id: str
    email: str
    name: str
    role: str
    created_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_settings():
    """Load settings from core config or environment."""
    try:
        from src.core.config import get_settings
        return get_settings()
    except ImportError:
        import os

        class _Fallback:
            secret_key: str = os.environ.get("HUNT_SECRET_KEY", "change-me-in-production")
            jwt_algorithm: str = "HS256"
            token_expire_hours: int = 24

        return _Fallback()


def _hash_password(password: str) -> str:
    """Hash a password using bcrypt via passlib."""
    try:
        from passlib.context import CryptContext

        _ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
        return _ctx.hash(password)
    except ImportError:
        import hashlib

        logger.warning("passlib not installed — using sha256 fallback (dev only)")
        return hashlib.sha256(password.encode()).hexdigest()


def _verify_password(plain: str, hashed: str) -> bool:
    """Verify a password against its hash."""
    try:
        from passlib.context import CryptContext

        _ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
        return _ctx.verify(plain, hashed)
    except ImportError:
        import hashlib

        return hashlib.sha256(plain.encode()).hexdigest() == hashed


def _create_access_token(user_id: str, email: str, role: str) -> tuple[str, int]:
    """Create a signed JWT. Returns ``(token, expires_in_seconds)``."""
    import jwt  # PyJWT

    settings = _get_settings()
    expires_delta = timedelta(hours=settings.token_expire_hours)
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "iat": now,
        "exp": now + expires_delta,
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, int(expires_delta.total_seconds())


# ---------------------------------------------------------------------------
# Persistence helpers (delegate to core.db when available)
# ---------------------------------------------------------------------------

async def _get_session():
    """Create a new async session from core.db.SessionLocal."""
    from src.core.db import SessionLocal

    return SessionLocal()


async def _find_user_by_email(email: str) -> dict[str, Any] | None:
    """Look up a user record by email. Returns dict or ``None``."""
    try:
        from sqlalchemy import text

        session = await _get_session()
        try:
            result = await session.execute(
                text(
                    "SELECT id, email, name, password_hash, role, created_at "
                    "FROM users WHERE email = :email"
                ),
                {"email": email},
            )
            row = result.mappings().first()
            return dict(row) if row else None
        finally:
            await session.close()
    except ImportError:
        logger.warning("Database unavailable — auth endpoints are stubs")
        return None


async def _create_user(email: str, password: str, name: str) -> dict[str, Any]:
    """Insert a new user row. Returns the created user dict."""
    import uuid

    hashed = _hash_password(password)
    user_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()

    try:
        from sqlalchemy import text

        session = await _get_session()
        try:
            await session.execute(
                text(
                    "INSERT INTO users (id, email, name, password_hash, role, created_at) "
                    "VALUES (:id, :email, :name, :hash, :role, :created)"
                ),
                {
                    "id": user_id,
                    "email": email,
                    "name": name,
                    "hash": hashed,
                    "role": "user",
                    "created": now,
                },
            )
            await session.commit()
        finally:
            await session.close()
    except ImportError:
        logger.warning("Database unavailable — user creation is a stub")

    return {
        "id": user_id,
        "email": email,
        "name": name,
        "role": "user",
        "created_at": now,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new account",
)
async def register(body: RegisterRequest):
    """Create a new user account with the given email, password, and name."""
    existing = await _find_user_by_email(body.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )
    user = await _create_user(body.email, body.password, body.name)
    return UserResponse(**user)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Obtain a JWT access token",
)
async def login(body: LoginRequest):
    """Authenticate with email + password and receive a bearer JWT."""
    user = await _find_user_by_email(body.email)
    if not user or not _verify_password(body.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    token, expires_in = _create_access_token(user["id"], user["email"], user["role"])
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user profile",
)
async def me(user: _current_user):
    """Return the authenticated user's profile."""
    return UserResponse(
        id=user["id"],
        email=user["email"],
        name=user.get("name", ""),
        role=user["role"],
        created_at=user.get("created_at", ""),
    )
