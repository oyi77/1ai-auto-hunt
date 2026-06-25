"""Shopee checkout engine.

Implements the Shopee checkout flow: validate → checkout_get → place_order.
Handles anti-bot header rotation, exponential backoff retry, and structured
error reporting.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import aiohttp

from .anti_bot import ShopeeAntiBot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SHOPEE_BASE: str = "https://shopee.co.id"
_API_V4: str = f"{_SHOPEE_BASE}/api/v4"

_DEFAULT_TIMEOUT: float = 10.0
_MAX_RETRIES: int = 5
_BASE_DELAY: float = 0.3       # seconds
_MAX_DELAY: float = 10.0       # seconds
_BACKOFF_FACTOR: float = 2.0
_JITTER_RANGE: float = 0.2     # ±20% jitter


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CheckoutRequest:
    """Parameters for a Shopee checkout."""
    item_id: str
    shop_id: str
    model_id: str
    quantity: int = 1
    payment_method: str = "online"  # cod | online
    address_id: str | None = None


@dataclass
class CheckoutResponse:
    """Result of a Shopee checkout attempt."""
    success: bool
    checkout_id: str | None = None
    price: float | None = None
    error: str | None = None
    raw: dict[str, Any] | None = None
    attempts: int = 0
    elapsed_sec: float = 0.0


# ---------------------------------------------------------------------------
# ShopeeEngine
# ---------------------------------------------------------------------------

class ShopeeEngine:
    """Async Shopee checkout engine.

    Flow: validate item → get checkout info → place order.

    Usage::

        async with ShopeeEngine(cookies=my_cookies) as engine:
            result = await engine.checkout(
                item_id="123456",
                shop_id="789",
                model_id="101112",
            )
            print(result)

    Parameters
    ----------
    cookies : dict
        Shopee session cookies (``SPC_EC``, ``SPC_F``, etc.).
    ua_category : str
        User-Agent pool: ``"pc"``, ``"iphone"``, ``"android"``, ``"random"``.
    proxy : str, optional
        HTTP proxy URL (``http://user:pass@host:port``).
    timeout : float
        Per-request timeout in seconds.
    max_retries : int
        Maximum retry attempts per request.
    """

    def __init__(
        self,
        cookies: dict[str, str] | None = None,
        ua_category: str = "pc",
        proxy: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _MAX_RETRIES,
    ) -> None:
        self._cookies = cookies or {}
        self._proxy = proxy
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._max_retries = max_retries
        self._anti_bot = ShopeeAntiBot(ua_category=ua_category)
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> ShopeeEngine:
        self._session = aiohttp.ClientSession(
            cookies=self._cookies,
            timeout=self._timeout,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    # -- internal HTTP with retry -------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send an HTTP request with exponential backoff retry.

        Returns the parsed JSON body.

        Raises
        ------
        ShopeeRequestError
            If all retries are exhausted.
        """
        assert self._session is not None, "Use 'async with ShopeeEngine(...)'"

        last_error: str | None = None
        for attempt in range(1, self._max_retries + 1):
            headers_obj = self._anti_bot.generate_headers(
                method=method, path=path,
                body=kwargs.get("data", "") or "",
            )
            headers = headers_obj.as_dict()

            try:
                async with self._session.request(
                    method,
                    url,
                    headers=headers,
                    proxy=self._proxy,
                    **kwargs,
                ) as resp:
                    body = await resp.json(content_type=None)

                    # Success path
                    if resp.status == 200 and body.get("error") is None:
                        return body

                    # Rate-limited → back off
                    if resp.status == 429:
                        last_error = f"Rate limited (429)"
                        logger.warning(
                            "Attempt %d/%d: rate limited, backing off",
                            attempt, self._max_retries,
                        )
                    elif resp.status in (500, 502, 503, 504):
                        last_error = f"Server error ({resp.status})"
                        logger.warning(
                            "Attempt %d/%d: server error %d",
                            attempt, self._max_retries, resp.status,
                        )
                    else:
                        # Application-level error
                        err = body.get("error") or body.get("error_message") or str(body)
                        return body  # Return to caller for application-level handling

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = str(exc)
                logger.warning(
                    "Attempt %d/%d: %s",
                    attempt, self._max_retries, last_error,
                )

            # Exponential backoff with jitter
            if attempt < self._max_retries:
                delay = min(
                    _BASE_DELAY * (_BACKOFF_FACTOR ** (attempt - 1)),
                    _MAX_DELAY,
                )
                jitter = delay * random.uniform(-_JITTER_RANGE, _JITTER_RANGE)
                await asyncio.sleep(delay + jitter)
                self._anti_bot.rotate_device()

        raise ShopeeRequestError(
            f"Request failed after {self._max_retries} attempts: {last_error}"
        )

    # -- checkout flow steps ------------------------------------------------

    async def validate_item(
        self, item_id: str, shop_id: str, model_id: str,
    ) -> dict[str, Any]:
        """Validate that the item exists and is in stock.

        Returns the item detail response.
        """
        path = f"/api/v4/item/get?itemid={item_id}&shopid={shop_id}"
        url = f"{_SHOPEE_BASE}{path}"
        result = await self._request("GET", url, path)
        logger.info("Validate item %s/%s → ok", shop_id, item_id)
        return result

    async def checkout_get(
        self,
        item_id: str,
        shop_id: str,
        model_id: str,
        quantity: int = 1,
    ) -> dict[str, Any]:
        """Initiate checkout session and retrieve pricing / shipping info.

        This is the ``/api/v4/checkout/get`` call.
        """
        path = "/api/v4/checkout/get"
        url = f"{_SHOPEE_BASE}{path}"
        payload = json.dumps({
            "shoporders": [{
                "shopid": int(shop_id),
                "items": [{
                    "itemid": int(item_id),
                    "modelid": int(model_id),
                    "quantity": quantity,
                }],
            }],
        })
        result = await self._request(
            "POST", url, path,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        logger.info("Checkout get for %s/%s model=%s → ok", shop_id, item_id, model_id)
        return result

    async def place_order(
        self,
        item_id: str,
        shop_id: str,
        model_id: str,
        quantity: int = 1,
        payment_method: str = "online",
        address_id: str | None = None,
    ) -> dict[str, Any]:
        """Place the final order.

        This is the ``/api/v4/checkout/place_order`` call.
        """
        path = "/api/v4/checkout/place_order"
        url = f"{_SHOPEE_BASE}{path}"

        order_body: dict[str, Any] = {
            "shoporders": [{
                "shopid": int(shop_id),
                "items": [{
                    "itemid": int(item_id),
                    "modelid": int(model_id),
                    "quantity": quantity,
                }],
            }],
            "payment_channel": 0 if payment_method == "cod" else 1,
            "platform_voucher": "",
            "shop_vouchers": [],
        }
        if address_id:
            order_body["address_id"] = address_id

        payload = json.dumps(order_body)
        result = await self._request(
            "POST", url, path,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        logger.info("Place order %s/%s → %s", shop_id, item_id, "ok" if result else "fail")
        return result

    # -- public API ---------------------------------------------------------

    async def checkout(
        self,
        item_id: str,
        shop_id: str,
        model_id: str,
        quantity: int = 1,
        payment_method: str = "online",
        address_id: str | None = None,
    ) -> CheckoutResponse:
        """Execute the full Shopee checkout flow.

        Steps:
        1. Validate item is available.
        2. Get checkout info (pricing, shipping).
        3. Place the order.

        Returns
        -------
        CheckoutResponse
            Structured result with success flag, checkout_id, or error.
        """
        t0 = time.monotonic()
        total_attempts = 0

        # Step 1: Validate
        try:
            await self.validate_item(item_id, shop_id, model_id)
            total_attempts += 1
        except ShopeeRequestError as exc:
            return CheckoutResponse(
                success=False,
                error=f"Validation failed: {exc}",
                attempts=total_attempts,
                elapsed_sec=time.monotonic() - t0,
            )

        # Step 2: Checkout get
        try:
            checkout_info = await self.checkout_get(
                item_id, shop_id, model_id, quantity,
            )
            total_attempts += 1
        except ShopeeRequestError as exc:
            return CheckoutResponse(
                success=False,
                error=f"Checkout GET failed: {exc}",
                attempts=total_attempts,
                elapsed_sec=time.monotonic() - t0,
            )

        # Extract price from checkout info if available
        price: float | None = None
        try:
            orders = checkout_info.get("data", {}).get("order", {}).get("shoporders", [])
            if orders:
                price = float(orders[0].get("items", [{}])[0].get("price", 0))
        except (IndexError, TypeError, ValueError):
            pass

        # Step 3: Place order
        try:
            order_result = await self.place_order(
                item_id, shop_id, model_id, quantity, payment_method, address_id,
            )
            total_attempts += 1
        except ShopeeRequestError as exc:
            return CheckoutResponse(
                success=False,
                price=price,
                error=f"Place order failed: {exc}",
                attempts=total_attempts,
                elapsed_sec=time.monotonic() - t0,
            )

        # Parse result
        err = order_result.get("error")
        checkout_id = str(order_result.get("data", {}).get("orderid", ""))

        return CheckoutResponse(
            success=err is None and bool(checkout_id),
            checkout_id=checkout_id or None,
            price=price,
            error=str(err) if err else None,
            raw=order_result,
            attempts=total_attempts,
            elapsed_sec=time.monotonic() - t0,
        )


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ShopeeRequestError(Exception):
    """A Shopee HTTP request failed after all retries."""
    pass
