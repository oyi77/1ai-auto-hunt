"""Price sniper — wait until a product's price drops to a threshold, then buy.

Polls the product price at a configurable interval (default 500ms) and
triggers the checkout engine as soon as the price is at or below threshold.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional, Protocol

from .models import (
    CheckoutResult,
    CheckoutStatus,
    FlashSaleItem,
    ItemStatus,
    Platform,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocols (duck-typed checkout engines)
# ---------------------------------------------------------------------------

class CheckoutEngine(Protocol):
    """Protocol for checkout engines (ShopeeEngine, TokopediaEngine, etc.)."""

    async def checkout(
        self,
        item_id: str,
        shop_id: str,
        model_id: str,
        **kwargs: Any,
    ) -> Any:
        """Execute the checkout flow. Returns an object with .success, .price, .checkout_id, .error."""
        ...


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

class SniperStatus(str, Enum):
    IDLE = "idle"
    WAITING = "waiting"       # polling but price above threshold
    TRIGGERED = "triggered"   # threshold met, checkout in progress
    BOUGHT = "bought"         # checkout succeeded
    TIMED_OUT = "timed_out"   # timeout reached without meeting threshold
    FAILED = "failed"         # checkout attempted but failed
    CANCELLED = "cancelled"   # user cancelled


@dataclass
class SniperResult:
    """Outcome of a snipe attempt."""
    status: SniperStatus
    item_id: str
    threshold: float
    final_price: float | None = None
    checkout_id: str | None = None
    error: str | None = None
    polls: int = 0
    elapsed_sec: float = 0.0
    triggered_at: datetime | None = None


# ---------------------------------------------------------------------------
# PriceSniper
# ---------------------------------------------------------------------------

class PriceSniper:
    """Poll a product's price and auto-checkout when it drops to threshold.

    Usage::

        from .shopee import ShopeeEngine

        async with ShopeeEngine(cookies=cookies) as engine:
            sniper = PriceSniper(engine)
            result = await sniper.wait_and_buy(
                item=FlashSaleItem(
                    platform=Platform.SHOPEE,
                    item_id="123",
                    shop_id="456",
                    model_id="789",
                    target_price=100_000,
                ),
                threshold=100_000,
                timeout=300,
            )

    Parameters
    ----------
    engine : CheckoutEngine
        Checkout engine to use when threshold is met.
    poll_interval : float
        Seconds between price polls. Default 0.5 (500ms).
    on_price_check : callable, optional
        Called on every poll: ``(item_id, current_price, threshold) -> None``.
    on_trigger : callable, optional
        Called when threshold is met, before checkout: ``(item_id, price) -> None``.
    """

    def __init__(
        self,
        engine: CheckoutEngine,
        poll_interval: float = 0.5,
        on_price_check: Callable[[str, float, float], None] | None = None,
        on_trigger: Callable[[str, float], None] | None = None,
    ) -> None:
        self._engine = engine
        self._poll_interval = poll_interval
        self._on_price_check = on_price_check
        self._on_trigger = on_trigger
        self._cancelled = False

    def cancel(self) -> None:
        """Cancel an in-progress snipe."""
        self._cancelled = True

    async def _fetch_price(
        self, platform: Platform, item_id: str, shop_id: str | None,
    ) -> float | None:
        """Fetch the current price of an item from its platform.

        Uses a lightweight API call (validate / item detail) rather than
        a full checkout flow.
        """
        # We leverage the engine's validate method to get current price.
        # This is intentionally a thin facade — the engine handles its own
        # anti-bot and retry logic.
        try:
            if platform == Platform.SHOPEE:
                from .shopee import ShopeeEngine
                if isinstance(self._engine, ShopeeEngine):
                    result = await self._engine.validate_item(
                        item_id, shop_id or "", "",
                    )
                    data = result.get("data", {})
                    price_val = data.get("price", data.get("price_max"))
                    if price_val is not None:
                        # Shopee prices in the item API are often in cents
                        price = float(price_val)
                        if price > 1_000_000:
                            price = price / 100_000  # Shopee's unit is x100000
                        return price
            elif platform == Platform.TOKOPEDIA:
                from .tokped import TokopediaEngine
                if isinstance(self._engine, TokopediaEngine):
                    result = await self._engine.validate_product(
                        item_id, shop_id or "",
                    )
                    detail = result.get("data", {}).get("productDetail", {})
                    price_val = detail.get("basic", {}).get("price", {}).get("value")
                    if price_val is not None:
                        return float(price_val)
        except Exception as exc:
            logger.warning("Price fetch failed for %s: %s", item_id, exc)

        return None

    async def wait_and_buy(
        self,
        item: FlashSaleItem,
        threshold: float | None = None,
        timeout: float = 300.0,
    ) -> SniperResult:
        """Poll price every ``poll_interval`` and checkout when ≤ threshold.

        Parameters
        ----------
        item : FlashSaleItem
            The item to snipe.
        threshold : float, optional
            Maximum price to trigger at. Defaults to ``item.target_price``.
        timeout : float
            Maximum seconds to wait before giving up.

        Returns
        -------
        SniperResult
        """
        threshold = threshold or item.target_price
        t0 = time.monotonic()
        polls = 0
        self._cancelled = False

        logger.info(
            "Sniper started: item=%s threshold=%.0f timeout=%.0fs interval=%.3fs",
            item.item_id, threshold, timeout, self._poll_interval,
        )

        # --- polling loop ---
        while True:
            if self._cancelled:
                return SniperResult(
                    status=SniperStatus.CANCELLED,
                    item_id=item.item_id,
                    threshold=threshold,
                    polls=polls,
                    elapsed_sec=time.monotonic() - t0,
                )

            elapsed = time.monotonic() - t0
            if elapsed >= timeout:
                return SniperResult(
                    status=SniperStatus.TIMED_OUT,
                    item_id=item.item_id,
                    threshold=threshold,
                    polls=polls,
                    elapsed_sec=elapsed,
                )

            # Fetch current price
            current_price = await self._fetch_price(
                item.platform, item.item_id, item.shop_id,
            )
            polls += 1

            if current_price is not None:
                logger.debug(
                    "Poll #%d: item=%s price=%.0f threshold=%.0f",
                    polls, item.item_id, current_price, threshold,
                )

                if self._on_price_check:
                    self._on_price_check(item.item_id, current_price, threshold)

                # Threshold met → trigger checkout
                if current_price <= threshold:
                    logger.info(
                        "THRESHOLD MET: item=%s price=%.0f ≤ %.0f after %d polls (%.1fs)",
                        item.item_id, current_price, threshold, polls, elapsed,
                    )

                    if self._on_trigger:
                        self._on_trigger(item.item_id, current_price)

                    # Attempt checkout
                    checkout_result = await self._engine.checkout(
                        item_id=item.item_id,
                        shop_id=item.shop_id or "",
                        model_id=item.model_id or "",
                    )

                    if checkout_result.success:
                        return SniperResult(
                            status=SniperStatus.BOUGHT,
                            item_id=item.item_id,
                            threshold=threshold,
                            final_price=checkout_result.price or current_price,
                            checkout_id=checkout_result.checkout_id,
                            polls=polls,
                            elapsed_sec=time.monotonic() - t0,
                            triggered_at=datetime.now(timezone.utc),
                        )
                    else:
                        return SniperResult(
                            status=SniperStatus.FAILED,
                            item_id=item.item_id,
                            threshold=threshold,
                            final_price=current_price,
                            error=checkout_result.error,
                            polls=polls,
                            elapsed_sec=time.monotonic() - t0,
                        )

            # Wait before next poll
            await asyncio.sleep(self._poll_interval)
