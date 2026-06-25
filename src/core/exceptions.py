"""Custom exception hierarchy for 1ai-auto-hunt.

Every hunt-level error inherits from ``HuntError`` so callers can
catch the broad category or a specific leaf.

Hierarchy::

    HuntError
    ├── AccountCreationError
    │   └── CaptchaError
    ├── BoostError
    ├── CheckoutError
    ├── PhoneVerificationError
    ├── ProxyError
    └── ConfigurationError

Each exception carries an optional ``context`` dict for structured
logging / Sentry breadcrumbs.
"""

from __future__ import annotations

from typing import Any


class HuntError(Exception):
    """Base class for all 1ai-auto-hunt errors.

    Attributes:
        message: Human-readable description.
        context: Structured metadata for logging / debugging.
    """

    def __init__(
        self,
        message: str = "",
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.message = message
        self.context = context or {}
        super().__init__(message)

    def __repr__(self) -> str:
        ctx = f", context={self.context}" if self.context else ""
        return f"{self.__class__.__name__}({self.message!r}{ctx})"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a dict suitable for JSON logging / Sentry extras."""
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            **self.context,
        }


class AccountCreationError(HuntError):
    """Failed to create an account on a target platform.

    Covers registration form errors, email verification failures,
    and any platform-specific anti-bot blocks.
    """

    pass


class CaptchaError(HuntError):
    """Failed to solve a captcha challenge.

    Subclass of :class:`AccountCreationError` because captchas most
    often appear during account creation.
    """

    pass


class BoostError(HuntError):
    """Failed to boost engagement (likes, follows, views, etc.).

    Covers rate-limits, bans, and service-side errors from the social
    API.
    """

    pass


class CheckoutError(HuntError):
    """Failed to complete a flash-sale or checkout flow.

    Covers cart add failures, payment declines, stock-outs detected
    mid-checkout, and anti-bot blocks at payment time.
    """

    pass


class PhoneVerificationError(HuntError):
    """Failed to obtain or use a virtual phone number.

    Covers sms-activate errors, timeouts waiting for codes, and
    number recycling issues.
    """

    pass


class ProxyError(HuntError):
    """Failed to fetch, rotate, or use a proxy.

    Covers 1proxy API errors, pool exhaustion, and connection
    failures attributed to a bad proxy.
    """

    pass


class ConfigurationError(HuntError):
    """Missing or invalid configuration.

    Raised when required settings are absent or inconsistent.
    """

    pass
