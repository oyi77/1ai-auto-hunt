"""Structured logging for 1ai-auto-hunt.

Uses ``structlog`` for structured key-value logging.  In production
(``log_format=json``) the output is one JSON object per line, making it
easy to ship to ELK / Loki / CloudWatch.  In development
(``log_format=console``) the output is colourised and human-readable.

All modules should acquire a logger via::

    from src.core.logger import get_logger
    log = get_logger(__name__)
"""

from __future__ import annotations

import logging
import sys

import structlog

from src.core.config import get_settings

_configured = False


def _setup_logging() -> None:
    """Configure structlog + stdlib logging once (idempotent)."""
    global _configured  # noqa: PLW0603
    if _configured:
        return
    _configured = True

    settings = get_settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # ── Shared processors ─────────────────────────────────────────────
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    # ── Renderer ──────────────────────────────────────────────────────
    if settings.log_format == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    # ── structlog configuration ───────────────────────────────────────
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # ── stdlib handler (for third-party libs that use logging.*) ──────
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger.

    Ensures logging is configured on first call, so no setup is needed
    at import time.

    Args:
        name: Usually ``__name__`` of the calling module.

    Returns:
        A ``structlog.stdlib.BoundLogger`` instance.
    """
    _setup_logging()
    return structlog.get_logger(name)  # type: ignore[return-value]
