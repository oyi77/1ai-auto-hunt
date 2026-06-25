"""Anti-bot header generation for Shopee and similar platforms.

Generates realistic browser fingerprints, rotating User-Agent strings,
CSRF tokens, and platform-specific security headers to avoid detection.
"""

from __future__ import annotations

import hashlib
import json
import random
import string
import time
import uuid
from dataclasses import dataclass, field
from typing import Final


# ---------------------------------------------------------------------------
# User-Agent pools
# ---------------------------------------------------------------------------

_PC_AGENTS: Final[list[str]] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) "
    "Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:126.0) "
    "Gecko/20100101 Firefox/126.0",
]

_IPHONE_AGENTS: Final[list[str]] = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5_1 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
    "Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/126.0.6478.72 "
    "Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_7_8 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
    "Mobile/15E148 Safari/604.1",
]

_ANDROID_AGENTS: Final[list[str]] = [
    "Mozilla/5.0 (Linux; Android 14; SM-S926B) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Pixel 8 Pro) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-A546B) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; 22081283G) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
]

ALL_AGENTS: Final[list[str]] = _PC_AGENTS + _IPHONE_AGENTS + _ANDROID_AGENTS

_AGENT_MAP: Final[dict[str, list[str]]] = {
    "pc": _PC_AGENTS,
    "iphone": _IPHONE_AGENTS,
    "android": _ANDROID_AGENTS,
}


# ---------------------------------------------------------------------------
# Fingerprint helpers
# ---------------------------------------------------------------------------

def _random_hex(length: int) -> str:
    """Return a random hex string of *length* characters."""
    return "".join(random.choices("0123456789abcdef", k=length))


def _random_token(length: int = 32) -> str:
    """Return a base64-ish CSRF token."""
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(random.choices(alphabet, k=length))


def _fingerprint_id() -> str:
    """Generate a plausible Shopee device fingerprint id (UUIDv4)."""
    return str(uuid.uuid4())


def _build_sap_signature(
    method: str,
    path: str,
    timestamp: int,
    body: str = "",
    secret: str = "",
) -> str:
    """Create a simple HMAC-style signature similar to Shopee SAP headers.

    The real algorithm is internal; this produces a plausible hex digest
    from the request parameters so it looks structurally correct.
    """
    payload = f"{method}{path}{timestamp}{body}{secret}"
    return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Dataclass result
# ---------------------------------------------------------------------------

@dataclass
class GeneratedHeaders:
    """Container for generated anti-bot headers."""
    headers: dict[str, str]
    device_id: str
    fingerprint: str
    user_agent: str
    category: str  # "pc" | "iphone" | "android"

    def as_dict(self) -> dict[str, str]:
        """Return plain headers dict."""
        return dict(self.headers)


# ---------------------------------------------------------------------------
# ShopeeAntiBot
# ---------------------------------------------------------------------------

class ShopeeAntiBot:
    """Generate anti-bot headers for Shopee requests.

    Usage::

        bot = ShopeeAntiBot()
        headers = bot.generate_headers(method="GET", path="/api/v4/item/get")
        requests.get(url, headers=headers.as_dict())

    Parameters
    ----------
    ua_category : str
        User-Agent pool to draw from: ``"pc"``, ``"iphone"``, ``"android"``,
        or ``"random"`` (mixed). Default ``"random"``.
    """

    def __init__(self, ua_category: str = "random") -> None:
        if ua_category != "random" and ua_category not in _AGENT_MAP:
            raise ValueError(
                f"Unknown ua_category={ua_category!r}; "
                f"expected one of: {', '.join(_AGENT_MAP)}, random"
            )
        self._ua_category = ua_category
        self._device_id: str | None = None

    # -- internal -----------------------------------------------------------

    def _pick_user_agent(self) -> tuple[str, str]:
        """Return (user_agent_string, category)."""
        if self._ua_category == "random":
            category = random.choice(list(_AGENT_MAP))
        else:
            category = self._ua_category
        ua = random.choice(_AGENT_MAP[category])
        return ua, category

    def _get_device_id(self) -> str:
        """Reuse the same device id within a session for consistency."""
        if self._device_id is None:
            self._device_id = _fingerprint_id()
        return self._device_id

    # -- public API ---------------------------------------------------------

    def rotate_device(self) -> None:
        """Force a new device identity for the next request."""
        self._device_id = None

    def generate_headers(
        self,
        method: str = "GET",
        path: str = "/",
        body: str = "",
        extra: dict[str, str] | None = None,
    ) -> GeneratedHeaders:
        """Build a full set of anti-bot headers.

        Parameters
        ----------
        method : str
            HTTP method (GET, POST, …).
        path : str
            URL path portion.
        body : str
            Serialized request body (for signature).
        extra : dict, optional
            Additional headers to merge in.

        Returns
        -------
        GeneratedHeaders
            Container with ``.headers`` dict and metadata.
        """
        ua, category = self._pick_user_agent()
        device_id = self._get_device_id()
        now_ts = int(time.time())
        csrf_token = _random_token(32)
        fingerprint = _random_hex(32)
        sap_ts = str(now_ts)
        sap_nonce = _random_hex(16)
        sap_sig = _build_sap_signature(method, path, now_ts, body)

        # Determine if mobile
        is_mobile = category in ("iphone", "android")

        headers: dict[str, str] = {
            # Browser identification
            "User-Agent": ua,
            "Accept": "application/json",
            "Accept-Language": random.choice([
                "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
                "en-US,en;q=0.9,id;q=0.8",
                "id-ID,id;q=0.9",
            ]),
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://shopee.co.id/",
            "Origin": "https://shopee.co.id",

            # CSRF
            "X-Csrftoken": csrf_token,
            "X-Requested-With": "XMLHttpRequest",

            # SAP security headers (Shopee Anti-fraud Platform)
            "X-Sap-Access-T": sap_ts,
            "X-Sap-Access-F": fingerprint,
            "X-Sap-Access-S": sap_sig,
            "X-Sap-Access-N": sap_nonce,

            # Device fingerprint
            "X-Device-Id": device_id,
            "X-Api-Source": "pc" if not is_mobile else "rweb",

            # Misc
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

        if is_mobile:
            headers["X-Api-Source"] = "rweb"
            headers["Viewport-Width"] = random.choice(["375", "390", "414"])

        if extra:
            headers.update(extra)

        return GeneratedHeaders(
            headers=headers,
            device_id=device_id,
            fingerprint=fingerprint,
            user_agent=ua,
            category=category,
        )

    def generate_for_checkout(self, item_id: str, shop_id: str) -> GeneratedHeaders:
        """Convenience wrapper that pre-fills the path for a Shopee checkout.

        Parameters
        ----------
        item_id : str
            Shopee item id.
        shop_id : str
            Shopee shop id.

        Returns
        -------
        GeneratedHeaders
        """
        path = f"/api/v4/checkout/get?itemid={item_id}&shopid={shop_id}"
        return self.generate_headers(method="POST", path=path)

    def batch_headers(self, count: int = 10) -> list[GeneratedHeaders]:
        """Generate *count* independent header sets (one per concurrent request).

        Useful for blasting the same endpoint from "different" identities.
        """
        results: list[GeneratedHeaders] = []
        for _ in range(count):
            self.rotate_device()
            results.append(self.generate_headers())
        return results
