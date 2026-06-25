"""FastAPI application factory with all routers, CORS, and error handling.

Usage::

    from src.api import create_app
    app = create_app()

    # or run directly:
    uvicorn src.api.app:create_app --factory --reload
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

logger = logging.getLogger("1ai-auto-hunt.api")

# Project root — used to resolve router module paths on disk.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hooks."""
    logger.info("1ai-auto-hunt API starting up")
    yield
    logger.info("1ai-auto-hunt API shutting down")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(
    *,
    title: str = "1ai-auto-hunt API",
    version: str = "0.1.0",
    cors_origins: list[str] | None = None,
    debug: bool = False,
) -> FastAPI:
    """Build and return the fully-configured FastAPI application."""

    app = FastAPI(
        title=title,
        version=version,
        description=(
            "Automated commerce hunting platform.\n\n"
            "Account factory, boost service, flash sale sniper, domain flipper, "
            "streaming farm, KDP publisher, deepfake media."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        debug=debug,
    )

    # -- CORS ---------------------------------------------------------------
    origins = cors_origins or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- Middleware: request-id + timing -------------------------------------
    @app.middleware("http")
    async def request_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-Id", uuid.uuid4().hex)
        request.state.request_id = request_id
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"
        logger.debug(
            "%s %s → %s (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response

    # -- Exception handlers -------------------------------------------------
    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )

    @app.exception_handler(PermissionError)
    async def permission_error_handler(request: Request, exc: PermissionError):
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": str(exc)},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )

    # -- Health check -------------------------------------------------------
    @app.get(
        "/health",
        tags=["system"],
        summary="Health check",
        response_model=dict[str, Any],
    )
    async def health():
        return {
            "status": "ok",
            "service": "1ai-auto-hunt",
            "version": version,
        }

    # -- Mount routers ------------------------------------------------------
    from src.api.auth import router as auth_router

    app.include_router(auth_router, prefix="/auth", tags=["auth"])

    # Hunt routers — each module exports `router`
    _mount_hunt_router(app, "checkout", "/hunts/checkout", "Checkout — Flash Sale")
    _mount_hunt_router(app, "domain", "/hunts/domain", "Domain — Expired Scanner")
    _mount_hunt_router(app, "stream", "/hunts/stream", "Stream — Streaming Farm")
    _mount_hunt_router(app, "kdp", "/hunts/kdp", "KDP — Book Factory")
    _mount_hunt_router(app, "media", "/hunts/media", "Media — Deepfake/AI")

    return app


def _mount_hunt_router(
    app: FastAPI,
    module_name: str,
    prefix: str,
    tag: str,
) -> None:
    """Lazily import and mount a hunt router, skipping if the module is absent.

    Uses direct file import (``spec_from_file_location``) to bypass
    ``__init__.py`` — other agents' init files may have additional
    dependencies that aren't needed for the router itself.
    """
    router_file = _PROJECT_ROOT / "src" / "hunts" / module_name / "router.py"
    if not router_file.exists():
        logger.debug("No router.py for %s — skipped", module_name)
        return

    try:
        module_path = f"src.hunts.{module_name}.router"
        spec = importlib.util.spec_from_file_location(
            module_path, str(router_file), submodule_search_locations=[],
        )
        if spec is None or spec.loader is None:
            logger.warning("Cannot create spec for %s — skipped", module_name)
            return
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_path] = mod  # register so Pydantic can resolve types
        spec.loader.exec_module(mod)
        router = getattr(mod, "router", None)
        if router is None:
            logger.warning("Module %s has no `router` export — skipped", module_name)
            return
        app.include_router(router, prefix=prefix, tags=[tag])
        logger.debug("Mounted %s router at %s", module_name, prefix)
    except Exception:
        logger.warning("Hunt module %s not importable — skipped", module_name, exc_info=True)
