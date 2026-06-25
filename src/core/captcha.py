"""Captcha solving via the 2captcha API.

Provides ``CaptchaSolver`` — an async client that delegates reCAPTCHA v2,
hCaptcha (and more) solving to the 2captcha crowdsourced solver network.

Typical usage::

    async with CaptchaSolver() as solver:
        token = await solver.solve_recaptcha_v2(
            site_url="https://example.com/login",
            site_key="6Le-wvkSAAAAAPBMRTvw0Q4Muexq9bi0DJwx_mJ-",
        )
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx

from src.core.config import get_settings
from src.core.exceptions import CaptchaError
from src.core.logger import get_logger

logger = get_logger(__name__)

# 2captcha endpoints
_BASE_URL = "https://2captcha.com"
_IN = f"{_BASE_URL}/in.php"
_RES = f"{_BASE_URL}/res.php"

# Polling
_POLL_INITIAL_DELAY = 5.0
_POLL_BACKOFF = 1.5
_POLL_MAX_DELAY = 15.0


@dataclass
class CaptchaSolver:
    """Async context-manager wrapping the 2captcha API.

    Supports reCAPTCHA v2 and hCaptcha.  All public methods are safe to
    call from multiple concurrent tasks.
    """

    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def __aenter__(self) -> "CaptchaSolver":
        settings = get_settings()
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.captcha_timeout + 30),
        )
        logger.info("captcha_solver_started")
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("captcha_solver_stopped")

    # ── Public API ────────────────────────────────────────────────────

    async def solve_recaptcha_v2(
        self,
        site_url: str,
        site_key: str,
        *,
        invisible: bool = False,
    ) -> str:
        """Solve a Google reCAPTCHA v2 challenge.

        Args:
            site_url: Full URL of the page containing the captcha.
            site_key: The ``data-sitekey`` value from the captcha div.
            invisible: Set ``True`` for invisible reCAPTCHA variants.

        Returns:
            The ``g-recaptcha-response`` token to inject into the form.

        Raises:
            CaptchaError: Solving failed or timed out.
        """
        logger.info(
            "captcha_solve_recaptcha_v2",
            site_url=site_url,
            site_key=site_key[:8] + "…",
            invisible=invisible,
        )
        task: dict[str, str | int] = {
            "method": "userrecaptcha",
            "googlekey": site_key,
            "pageurl": site_url,
        }
        if invisible:
            task["invisible"] = 1
        return await self._solve(task)

    async def solve_hcaptcha(
        self,
        site_url: str,
        site_key: str,
    ) -> str:
        """Solve an hCaptcha challenge.

        Args:
            site_url: Full URL of the page containing the captcha.
            site_key: The ``data-sitekey`` value from the hCaptcha widget.

        Returns:
            The ``h-captcha-response`` token to inject into the form.

        Raises:
            CaptchaError: Solving failed or timed out.
        """
        logger.info(
            "captcha_solve_hcaptcha",
            site_url=site_url,
            site_key=site_key[:8] + "…",
        )
        task: dict[str, str] = {
            "method": "hcaptcha",
            "sitekey": site_key,
            "pageurl": site_url,
        }
        return await self._solve(task)

    # ── Internals ─────────────────────────────────────────────────────

    async def _solve(self, task: dict[str, str | int]) -> str:
        """Submit a task and poll until 2captcha returns a solution."""
        assert self._client is not None, "CaptchaSolver not entered"

        settings = get_settings()
        api_key = settings.captcha_api_key_plain
        if not api_key:
            raise CaptchaError("HUNT_CAPTCHA_API_KEY is not configured")

        task_id = await self._submit(api_key, task)
        logger.info("captcha_task_submitted", task_id=task_id)

        token = await self._poll_result(api_key, task_id)
        logger.info("captcha_solved", task_id=task_id, token_len=len(token))
        return token

    async def _submit(self, api_key: str, task: dict[str, str | int]) -> str:
        """POST the task to 2captcha's ``in.php`` and return the task ID."""
        assert self._client is not None
        payload = {"key": api_key, "json": 1, **task}

        try:
            resp = await self._client.post(_IN, data=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise CaptchaError(
                f"2captcha submission request failed: {exc}",
                context={"site_url": str(task.get("pageurl", ""))},
            ) from exc

        data = resp.json()
        if data.get("status") != 1 or not data.get("request"):
            raise CaptchaError(
                f"2captcha submission rejected: {data.get('request', 'unknown')}",
                context={"response": data},
            )
        return str(data["request"])

    async def _poll_result(self, api_key: str, task_id: str) -> str:
        """Poll ``res.php`` until the solution is ready or timeout."""
        assert self._client is not None
        settings = get_settings()
        deadline = asyncio.get_event_loop().time() + settings.captcha_timeout
        delay = _POLL_INITIAL_DELAY

        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(delay)
            delay = min(delay * _POLL_BACKOFF, _POLL_MAX_DELAY)

            try:
                resp = await self._client.get(
                    _RES,
                    params={"key": api_key, "action": "get", "id": task_id, "json": 1},
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("captcha_poll_http_error", task_id=task_id, error=str(exc))
                continue

            data = resp.json()
            status = data.get("status")

            if status == 1:
                return str(data["request"])

            if status == 0 and str(data.get("request")) == "CAPCHA_NOT_READY":
                logger.debug("captcha_not_ready", task_id=task_id)
                continue

            # Any other response is a hard error
            raise CaptchaError(
                f"2captcha returned error: {data.get('request', 'unknown')}",
                context={"task_id": task_id, "response": data},
            )

        raise CaptchaError(
            f"2captcha timed out after {settings.captcha_timeout}s",
            context={"task_id": task_id},
        )
