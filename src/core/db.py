"""SQLAlchemy async database setup.

Provides the shared ``engine``, ``SessionLocal`` factory, declarative
``Base``, and a FastAPI-compatible ``get_db`` dependency.

The engine is created lazily on first use so importing this module has no
side-effects until code actually touches the database.

Typical usage in a FastAPI route::

    from src.core.db import get_db

    @router.get("/items")
    async def list_items(db: AsyncSession = Depends(get_db)):
        ...

Typical usage in standalone scripts / workers::

    from src.core.db import engine, Base

    async def init_db():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger(__name__)


class Base(DeclarativeBase):
    """Declarative base for all ORM models.

    Subclass this in ``src.hunts.*.models`` to define tables.  All models
    share the same metadata so ``Base.metadata.create_all`` works in one
    shot.
    """

    pass


def _build_engine() -> AsyncEngine:
    """Create the async SQLAlchemy engine from current settings."""
    settings = get_settings()
    connect_args: dict[str, object] = {}

    # SQLite needs a special arg for async access
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    eng = create_async_engine(
        settings.database_url,
        echo=(settings.log_level == "DEBUG"),
        connect_args=connect_args,
        pool_pre_ping=True,
    )
    logger.info(
        "db_engine_created",
        url=settings.database_url.split("@")[-1] if "@" in settings.database_url else "***",
    )
    return eng


def _build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build a session factory bound to *engine*."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


# ── Module-level singletons (lazy) ───────────────────────────────────

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_engine() -> AsyncEngine:
    global _engine  # noqa: PLW0603
    if _engine is None:
        _engine = _build_engine()
    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory  # noqa: PLW0603
    if _session_factory is None:
        _session_factory = _build_session_factory(_get_engine())
    return _session_factory


# ── Public accessors ─────────────────────────────────────────────────


def engine() -> AsyncEngine:
    """Return the lazily-created async engine singleton."""
    return _get_engine()


def SessionLocal() -> AsyncSession:  # noqa: N802
    """Create a new ``AsyncSession`` from the factory.

    The caller is responsible for closing / using as a context manager.
    """
    return _get_session_factory()()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a scoped ``AsyncSession``.

    Commits on success, rolls back on exception, always closes.
    """
    session = _get_session_factory()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def init_db() -> None:
    """Create all tables registered on ``Base.metadata``.

    Call once at application startup (or in a migration script).
    """
    async with _get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("db_tables_created")


async def close_db() -> None:
    """Dispose the engine connection pool.

    Call at application shutdown.
    """
    global _engine, _session_factory  # noqa: PLW0603
    if _engine is not None:
        await _engine.dispose()
        logger.info("db_engine_disposed")
        _engine = None
        _session_factory = None

# ── Type alias used by hunt modules ──────────────────────────────────
from sqlalchemy.ext.asyncio import AsyncSession
Database = AsyncSession  # hunt modules use "Database" as the session type


# ── Type alias used by hunt modules ──────────────────────────────────


# ── Type alias used by hunt modules ──────────────────────────────────


# ── Type alias used by hunt modules ──────────────────────────────────

