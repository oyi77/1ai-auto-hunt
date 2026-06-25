"""Phone number verification via sms-activate.org API.

Provides ``PhoneVerifier`` — an async client for renting virtual phone
numbers, polling for SMS codes, and cancelling unused activations.

Typical usage::

    async with PhoneVerifier() as pv:
        number_id, phone = await pv.get_number("go")
        code = await pv.get_code(number_id, timeout=120)
        # use phone + code during account registration …
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx

from src.core.config import get_settings
from src.core.exceptions import PhoneVerificationError
from src.core.logger import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://api.sms-activate.org/stubs/handler_api.php"

# Polling
_POLL_INTERVAL = 5.0
_POLL_BACKOFF = 1.2
_POLL_MAX_INTERVAL = 12.0


@dataclass
class NumberInfo:
    """Returned by :meth:`PhoneVerifier.get_number`."""

    activation_id: str
    phone: str


@dataclass
class PhoneVerifier:
    """Async context-manager for sms-activate.org.

    All public methods are safe to call from multiple concurrent tasks.
    """

    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def __aenter__(self) -> "PhoneVerifier":
        settings = get_settings()
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            timeout=httpx.Timeout(settings.request_timeout),
        )
        logger.info("phone_verifier_started")
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("phone_verifier_stopped")

    # ── Public API ────────────────────────────────────────────────────

    async def get_number(self, service: str) -> NumberInfo:
        """Rent a virtual phone number for *service*.

        Args:
            service: sms-activate service code (e.g. ``"go"`` for Google,
                ``"wa"`` for WhatsApp, ``"tg"`` for Telegram).

        Returns:
            ``NumberInfo`` with ``activation_id`` and raw phone number.

        Raises:
            PhoneVerificationError: No numbers available or API error.
        """
        settings = get_settings()
        country = settings.sms_default_country
        logger.info("phone_get_number", service=service, country=country)

        data = await self._api_call(
            {
                "api_key": settings.sms_activate_key_plain,
                "action": "getNumber",
                "service": service,
                "country": country,
            }
        )

        # Expected response: STATUS_GET_NUMBER → activation_id:phone
        if "ACCESS_NUMBER" not in data:
            raise PhoneVerificationError(
                f"Unexpected getNumber response: {data!r}",
                context={"service": service, "country": country},
            )

        parts = data.split(":")
        if len(parts) != 3:
            raise PhoneVerificationError(
                f"Malformed getNumber response: {data!r}",
                context={"service": service},
            )

        _, activation_id, phone = parts
        logger.info(
            "phone_number_rented",
            activation_id=activation_id,
            phone=phone[:4] + "***",
            service=service,
        )
        return NumberInfo(activation_id=activation_id, phone=phone)

    async def get_code(
        self,
        activation_id: str,
        *,
        timeout: int = 180,
    ) -> str:
        """Poll until an SMS code arrives for *activation_id*.

        Args:
            activation_id: The ID returned by :meth:`get_number`.
            timeout: Max seconds to wait.

        Returns:
            The SMS verification code as a string.

        Raises:
            PhoneVerificationError: Timeout or API error.
        """
        settings = get_settings()
        logger.info("phone_waiting_for_code", activation_id=activation_id, timeout=timeout)
        deadline = asyncio.get_event_loop().time() + timeout
        interval = _POLL_INTERVAL

        while asyncio.get_event_loop().time() < deadline:
            data = await self._api_call(
                {
                    "api_key": settings.sms_activate_key_plain,
                    "action": "getStatus",
                    "id": activation_id,
                }
            )

            if data.startswith("STATUS_OK"):
                code = data.split(":")[-1]
                logger.info(
                    "phone_code_received",
                    activation_id=activation_id,
                    code_len=len(code),
                )
                return code

            if data == "STATUS_WAIT_CODE":
                logger.debug("phone_code_pending", activation_id=activation_id)
                await asyncio.sleep(interval)
                interval = min(interval * _POLL_BACKOFF, _POLL_MAX_INTERVAL)
                continue

            # STATUS_CANCEL or anything unexpected
            raise PhoneVerificationError(
                f"Unexpected getStatus response: {data!r}",
                context={"activation_id": activation_id},
            )

        raise PhoneVerificationError(
            f"SMS code timed out after {timeout}s",
            context={"activation_id": activation_id},
        )

    async def cancel(self, activation_id: str) -> None:
        """Cancel an unused activation so the number can be recycled.

        Safe to call even if the activation was already completed or
        cancelled — the API treats that as a no-op.
        """
        settings = get_settings()
        logger.info("phone_cancel", activation_id=activation_id)

        data = await self._api_call(
            {
                "api_key": settings.sms_activate_key_plain,
                "action": "setStatus",
                "status": 8,  # cancel
                "id": activation_id,
            }
        )
        logger.info("phone_cancel_done", activation_id=activation_id, response=data)

    # ── Internals ─────────────────────────────────────────────────────

    async def _api_call(self, params: dict[str, str | int]) -> str:
        """Make a GET request to the sms-activate handler API.

        Returns the raw response text (the API doesn't use JSON).

        Raises:
            PhoneVerificationError: HTTP failure or empty response.
        """
        assert self._client is not None, "PhoneVerifier not entered"

        try:
            resp = await self._client.get("", params=params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise PhoneVerificationError(
                f"sms-activate request failed: {exc}",
                context={"action": str(params.get("action", ""))},
            ) from exc

        text = resp.text.strip()
        if not text:
            raise PhoneVerificationError(
                "sms-activate returned empty response",
                context={"action": str(params.get("action", ""))},
            )

        # The API returns ERROR_XXX for known error codes
        if text.startswith("ERROR"):
            raise PhoneVerificationError(
                f"sms-activate error: {text}",
                context={"action": str(params.get("action", ""))},
            )

        return text
