"""Tokopedia checkout engine.

Implements the Tokopedia checkout flow: validate → checkout session → place order.
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

_TOKPED_BASE: str = "https://www.tokopedia.com"
_GQL_BASE: str = "https://gql.tokopedia.com"

_DEFAULT_TIMEOUT: float = 10.0
_MAX_RETRIES: int = 5
_BASE_DELAY: float = 0.3
_MAX_DELAY: float = 10.0
_BACKOFF_FACTOR: float = 2.0
_JITTER_RANGE: float = 0.2

# Tokopedia GraphQL operations
_OP_VALIDATE_PRODUCT = "ValidateProduct"
_OP_INIT_CHECKOUT = "InitCheckout"
_OP_PLACE_ORDER = "PlaceOrder"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TokpedCheckoutRequest:
    """Parameters for a Tokopedia checkout."""
    product_id: str
    shop_id: str
    variant_id: str | None = None
    quantity: int = 1
    payment_method: str = "ovo"  # ovo | gopay | bank_transfer | cod
    address_id: str | None = None


@dataclass
class TokpedCheckoutResponse:
    """Result of a Tokopedia checkout attempt."""
    success: bool
    order_id: str | None = None
    checkout_id: str | None = None
    price: float | None = None
    error: str | None = None
    raw: dict[str, Any] | None = None
    attempts: int = 0
    elapsed_sec: float = 0.0


# ---------------------------------------------------------------------------
# Tokopedia anti-bot (Tokopedia-compatible wrapper)
# ---------------------------------------------------------------------------

class _TokpedAntiBot:
    """Generate Tokopedia-compatible headers.

    Tokopedia uses a similar but distinct header set compared to Shopee.
    This wraps the base anti-bot logic with Tokopedia-specific fields.
    """

    def __init__(self) -> None:
        self._base = ShopeeAntiBot(ua_category="random")

    def rotate_device(self) -> None:
        self._base.rotate_device()

    def generate_headers(
        self,
        method: str = "GET",
        path: str = "/",
        body: str = "",
    ) -> dict[str, str]:
        generated = self._base.generate_headers(method=method, path=path, body=body)
        headers = generated.as_dict()

        # Override Tokopedia-specific fields
        headers["Referer"] = "https://www.tokopedia.com/"
        headers["Origin"] = "https://www.tokopedia.com"
        headers["X-Device"] = "desktop"
        headers["X-Source"] = "tokopedia-web"
        headers["X-Entry-Source"] = "osp"
        headers.pop("X-Sap-Access-T", None)
        headers.pop("X-Sap-Access-F", None)
        headers.pop("X-Sap-Access-S", None)
        headers.pop("X-Sap-Access-N", None)
        headers.pop("X-Api-Source", None)

        # Tokopedia uses x-tkpd-akamai style headers
        headers["X-Tkpd-Lite"] = "0"
        headers["X-Tkpd-Original-Syntax"] = "1"

        return headers


# ---------------------------------------------------------------------------
# TokopediaEngine
# ---------------------------------------------------------------------------

class TokopediaEngine:
    """Async Tokopedia checkout engine.

    Flow: validate product → init checkout → place order.

    Usage::

        async with TokopediaEngine(cookies=my_cookies) as engine:
            result = await engine.checkout(
                product_id="123456",
                shop_id="789",
            )
            print(result)

    Parameters
    ----------
    cookies : dict
        Tokopedia session cookies.
    proxy : str, optional
        HTTP proxy URL.
    timeout : float
        Per-request timeout in seconds.
    max_retries : int
        Maximum retry attempts per request.
    """

    def __init__(
        self,
        cookies: dict[str, str] | None = None,
        proxy: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _MAX_RETRIES,
    ) -> None:
        self._cookies = cookies or {}
        self._proxy = proxy
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._max_retries = max_retries
        self._anti_bot = _TokpedAntiBot()
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> TokopediaEngine:
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

    async def _gql_request(
        self,
        operation: str,
        variables: dict[str, Any],
        query: str,
    ) -> dict[str, Any]:
        """Send a GraphQL request with exponential backoff retry.

        Returns the parsed JSON body.
        """
        assert self._session is not None, "Use 'async with TokopediaEngine(...)'"

        url = f"{_GQL_BASE}/graphql/{operation}"
        path = f"/graphql/{operation}"
        payload = json.dumps({
            "operationName": operation,
            "variables": variables,
            "query": query,
        })

        last_error: str | None = None
        for attempt in range(1, self._max_retries + 1):
            headers = self._anti_bot.generate_headers(
                method="POST", path=path, body=payload,
            )
            headers["Content-Type"] = "application/json"

            try:
                async with self._session.post(
                    url,
                    headers=headers,
                    data=payload,
                    proxy=self._proxy,
                ) as resp:
                    body = await resp.json(content_type=None)

                    if resp.status == 200 and "errors" not in body:
                        return body

                    if resp.status == 429:
                        last_error = "Rate limited (429)"
                    elif resp.status in (500, 502, 503, 504):
                        last_error = f"Server error ({resp.status})"
                    else:
                        errors = body.get("errors", [])
                        if errors:
                            last_error = errors[0].get("message", str(errors))
                        else:
                            return body  # let caller parse

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = str(exc)

            logger.warning(
                "Attempt %d/%d for %s: %s",
                attempt, self._max_retries, operation, last_error,
            )

            if attempt < self._max_retries:
                delay = min(
                    _BASE_DELAY * (_BACKOFF_FACTOR ** (attempt - 1)),
                    _MAX_DELAY,
                )
                jitter = delay * random.uniform(-_JITTER_RANGE, _JITTER_RANGE)
                await asyncio.sleep(delay + jitter)
                self._anti_bot.rotate_device()

        raise TokpedRequestError(
            f"{operation} failed after {self._max_retries} attempts: {last_error}"
        )

    # -- checkout flow steps ------------------------------------------------

    async def validate_product(
        self, product_id: str, shop_id: str, variant_id: str | None = None,
    ) -> dict[str, Any]:
        """Validate that the product exists and check stock."""
        query = """
        query ValidateProduct($pid: String!, $sid: String!) {
            productDetail(pid: $pid, sid: $sid) {
                basic {
                    name
                    price { value }
                    stock { value }
                    status
                }
            }
        }
        """
        variables = {"pid": product_id, "sid": shop_id}
        result = await self._gql_request(_OP_VALIDATE_PRODUCT, variables, query)
        logger.info("Validate product %s/%s → ok", shop_id, product_id)
        return result

    async def init_checkout(
        self,
        product_id: str,
        shop_id: str,
        variant_id: str | None = None,
        quantity: int = 1,
    ) -> dict[str, Any]:
        """Initialize checkout session and get pricing."""
        query = """
        mutation InitCheckout($input: CheckoutInitInput!) {
            initCheckout(input: $input) {
                checkoutId
                totalPrice
                shippingOptions { name cost estimated }
            }
        }
        """
        variables = {
            "input": {
                "shopId": shop_id,
                "items": [{
                    "productId": product_id,
                    "variantId": variant_id or product_id,
                    "quantity": quantity,
                }],
            },
        }
        result = await self._gql_request(_OP_INIT_CHECKOUT, variables, query)
        logger.info("Init checkout %s → ok", product_id)
        return result

    async def place_order(
        self,
        product_id: str,
        shop_id: str,
        variant_id: str | None = None,
        quantity: int = 1,
        payment_method: str = "ovo",
        address_id: str | None = None,
        checkout_id: str | None = None,
    ) -> dict[str, Any]:
        """Place the final order."""
        query = """
        mutation PlaceOrder($input: PlaceOrderInput!) {
            placeOrder(input: $input) {
                orderId
                status
                redirectUrl
            }
        }
        """
        variables = {
            "input": {
                "checkoutId": checkout_id or "",
                "shopId": shop_id,
                "items": [{
                    "productId": product_id,
                    "variantId": variant_id or product_id,
                    "quantity": quantity,
                }],
                "paymentMethod": payment_method,
                "addressId": address_id or "",
            },
        }
        result = await self._gql_request(_OP_PLACE_ORDER, variables, query)
        logger.info("Place order %s → %s", product_id, "ok" if result else "fail")
        return result

    # -- public API ---------------------------------------------------------

    async def checkout(
        self,
        product_id: str,
        shop_id: str,
        variant_id: str | None = None,
        quantity: int = 1,
        payment_method: str = "ovo",
        address_id: str | None = None,
    ) -> TokpedCheckoutResponse:
        """Execute the full Tokopedia checkout flow.

        Steps:
        1. Validate product is available.
        2. Init checkout session.
        3. Place the order.

        Returns
        -------
        TokpedCheckoutResponse
            Structured result with success flag, order_id, or error.
        """
        t0 = time.monotonic()
        total_attempts = 0

        # Step 1: Validate
        try:
            validate_data = await self.validate_product(
                product_id, shop_id, variant_id,
            )
            total_attempts += 1
        except TokpedRequestError as exc:
            return TokpedCheckoutResponse(
                success=False,
                error=f"Validation failed: {exc}",
                attempts=total_attempts,
                elapsed_sec=time.monotonic() - t0,
            )

        # Extract price
        price: float | None = None
        try:
            detail = validate_data.get("data", {}).get("productDetail", {})
            price_val = detail.get("basic", {}).get("price", {}).get("value")
            if price_val is not None:
                price = float(price_val)
        except (TypeError, ValueError):
            pass

        # Step 2: Init checkout
        try:
            checkout_data = await self.init_checkout(
                product_id, shop_id, variant_id, quantity,
            )
            total_attempts += 1
            checkout_id = (
                checkout_data.get("data", {})
                .get("initCheckout", {})
                .get("checkoutId", "")
            )
        except TokpedRequestError as exc:
            return TokpedCheckoutResponse(
                success=False,
                price=price,
                error=f"Init checkout failed: {exc}",
                attempts=total_attempts,
                elapsed_sec=time.monotonic() - t0,
            )

        # Step 3: Place order
        try:
            order_data = await self.place_order(
                product_id, shop_id, variant_id, quantity,
                payment_method, address_id, checkout_id,
            )
            total_attempts += 1
        except TokpedRequestError as exc:
            return TokpedCheckoutResponse(
                success=False,
                price=price,
                error=f"Place order failed: {exc}",
                attempts=total_attempts,
                elapsed_sec=time.monotonic() - t0,
            )

        # Parse result
        order_result = order_data.get("data", {}).get("placeOrder", {})
        order_id = order_result.get("orderId")
        status = order_result.get("status", "")

        return TokpedCheckoutResponse(
            success=bool(order_id),
            order_id=order_id,
            checkout_id=checkout_id,
            price=price,
            error=None if order_id else f"Order placement returned no ID (status={status})",
            raw=order_data,
            attempts=total_attempts,
            elapsed_sec=time.monotonic() - t0,
        )


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class TokpedRequestError(Exception):
    """A Tokopedia request failed after all retries."""
    pass
