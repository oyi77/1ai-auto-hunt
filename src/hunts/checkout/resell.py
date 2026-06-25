"""Auto-resell engine — list purchased items on resale platforms.

After a successful flash-sale checkout, automatically list the item on
Tokopedia, OLX, or other resale platforms with a configurable markup.
Supports auto-repricing if the item hasn't sold within a specified window.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums / constants
# ---------------------------------------------------------------------------

class ResellPlatform(str, Enum):
    TOKOPEDIA = "tokopedia"
    OLX = "olx"
    SHOPEE = "shopee"


class ListingStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    SOLD = "sold"
    EXPIRED = "expired"
    DELISTED = "delisted"
    REPRICED = "repriced"


# Default auto-reprice: if not sold in 7 days, drop by 5%
_DEFAULT_REPRICE_DAYS: int = 7
_DEFAULT_REPRICE_DROP_PCT: float = 5.0
_DEFAULT_MIN_MARKUP_PCT: float = 10.0  # floor: never sell below cost + 10%


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PurchaseRecord:
    """Represents a completed purchase to be resold."""
    item_id: str
    platform: str               # original platform (shopee / tokopedia)
    shop_id: str | None = None
    model_id: str | None = None
    purchase_price: float = 0.0
    checkout_id: str | None = None
    product_name: str = ""
    image_url: str = ""
    quantity: int = 1
    purchased_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


@dataclass
class ResellListing:
    """A listing created on a resale platform."""
    listing_id: str
    platform: ResellPlatform
    purchase_ref: str            # item_id of the original purchase
    asking_price: float
    original_price: float
    markup_pct: float
    status: ListingStatus = ListingStatus.ACTIVE
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    price_history: list[tuple[datetime, float]] = field(default_factory=list)

    def record_price_change(self, new_price: float) -> None:
        """Record a price change in history."""
        self.price_history.append((datetime.now(timezone.utc), new_price))
        self.asking_price = new_price
        self.updated_at = datetime.now(timezone.utc)


@dataclass
class ResellResult:
    """Outcome of a resell listing creation."""
    success: bool
    listing: ResellListing | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Platform adapters (Protocol)
# ---------------------------------------------------------------------------

class ResellPlatformAdapter(Protocol):
    """Protocol for resale platform adapters."""

    async def create_listing(
        self,
        title: str,
        price: float,
        description: str,
        images: list[str] | None = None,
        category: str | None = None,
    ) -> str:
        """Create a listing and return the listing ID."""
        ...

    async def update_price(self, listing_id: str, new_price: float) -> bool:
        """Update the price of an existing listing."""
        ...

    async def delist(self, listing_id: str) -> bool:
        """Remove a listing."""
        ...

    async def check_sold(self, listing_id: str) -> bool:
        """Check if the listing has been sold."""
        ...


# ---------------------------------------------------------------------------
# Stub adapters
# ---------------------------------------------------------------------------

class TokopediaResellAdapter:
    """Tokopedia resale adapter (stub — requires API credentials)."""

    def __init__(self, cookies: dict[str, str] | None = None) -> None:
        self._cookies = cookies or {}

    async def create_listing(
        self, title: str, price: float, description: str,
        images: list[str] | None = None, category: str | None = None,
    ) -> str:
        """Create a Tokopedia listing. Stub — returns placeholder ID."""
        logger.info("Creating Tokopedia listing: %s @ %.0f", title, price)
        # TODO: Implement real Tokopedia seller API
        await asyncio.sleep(0.1)  # simulate API call
        listing_id = f"tkpd_{int(time.time())}"
        logger.info("Created Tokopedia listing %s", listing_id)
        return listing_id

    async def update_price(self, listing_id: str, new_price: float) -> bool:
        logger.info("Updating Tokopedia listing %s price to %.0f", listing_id, new_price)
        await asyncio.sleep(0.1)
        return True

    async def delist(self, listing_id: str) -> bool:
        logger.info("Delisting Tokopedia listing %s", listing_id)
        await asyncio.sleep(0.1)
        return True

    async def check_sold(self, listing_id: str) -> bool:
        await asyncio.sleep(0.1)
        return False


class OLXResellAdapter:
    """OLX resale adapter (stub)."""

    async def create_listing(
        self, title: str, price: float, description: str,
        images: list[str] | None = None, category: str | None = None,
    ) -> str:
        logger.info("Creating OLX listing: %s @ %.0f", title, price)
        await asyncio.sleep(0.1)
        return f"olx_{int(time.time())}"

    async def update_price(self, listing_id: str, new_price: float) -> bool:
        logger.info("Updating OLX listing %s price to %.0f", listing_id, new_price)
        await asyncio.sleep(0.1)
        return True

    async def delist(self, listing_id: str) -> bool:
        logger.info("Delisting OLX listing %s", listing_id)
        await asyncio.sleep(0.1)
        return True

    async def check_sold(self, listing_id: str) -> bool:
        await asyncio.sleep(0.1)
        return False


# ---------------------------------------------------------------------------
# AutoResell
# ---------------------------------------------------------------------------

class AutoResell:
    """Automatically list purchased items for resale with auto-repricing.

    Workflow:
    1. ``list_item()`` — create listing on target platform(s) with markup.
    2. ``monitor_listings()`` — periodically check if items sold.
    3. Auto-reprice: if not sold in ``reprice_after_days``, reduce price
       by ``reprice_drop_pct`` down to ``min_markup_pct`` above cost.

    Usage::

        reseller = AutoResell(
            adapters={ResellPlatform.TOKOPEDIA: TokopediaResellAdapter()},
            markup_pct=30.0,
        )
        purchase = PurchaseRecord(
            item_id="123", platform="shopee",
            purchase_price=50_000, product_name="Flash Sale Sneakers",
        )
        result = await reseller.list_item(purchase)
        await reseller.monitor_listings()  # auto-reprice loop

    Parameters
    ----------
    adapters : dict
        Mapping of platform → adapter instance.
    markup_pct : float
        Initial markup percentage above purchase price. Default 30%.
    reprice_after_days : int
        Days without a sale before auto-reprice. Default 7.
    reprice_drop_pct : float
        Percentage to drop on each reprice cycle. Default 5%.
    min_markup_pct : float
        Minimum markup floor — never price below cost + this %. Default 10%.
    default_platform : ResellPlatform
        Where to list if not specified. Default TOKOPEDIA.
    """

    def __init__(
        self,
        adapters: dict[ResellPlatform, Any] | None = None,
        markup_pct: float = 30.0,
        reprice_after_days: int = _DEFAULT_REPRICE_DAYS,
        reprice_drop_pct: float = _DEFAULT_REPRICE_DROP_PCT,
        min_markup_pct: float = _DEFAULT_MIN_MARKUP_PCT,
        default_platform: ResellPlatform = ResellPlatform.TOKOPEDIA,
    ) -> None:
        self._adapters = adapters or {
            ResellPlatform.TOKOPEDIA: TokopediaResellAdapter(),
            ResellPlatform.OLX: OLXResellAdapter(),
        }
        self._markup_pct = markup_pct
        self._reprice_after_days = reprice_after_days
        self._reprice_drop_pct = reprice_drop_pct
        self._min_markup_pct = min_markup_pct
        self._default_platform = default_platform
        self._listings: list[ResellListing] = []
        self._running = False

    @property
    def listings(self) -> list[ResellListing]:
        """All tracked listings."""
        return list(self._listings)

    def _calc_price(self, cost: float, markup_pct: float) -> float:
        """Calculate selling price from cost and markup."""
        return round(cost * (1 + markup_pct / 100))

    def _build_description(self, purchase: PurchaseRecord) -> str:
        """Generate a resale description."""
        lines = [
            purchase.product_name or f"Item {purchase.item_id}",
            "",
            f"Platform: {purchase.platform}",
            f"Condition: New (sealed)",
            f"Ready to ship!",
        ]
        return "\n".join(lines)

    async def list_item(
        self,
        purchase: PurchaseRecord,
        platform: ResellPlatform | None = None,
        markup_pct: float | None = None,
    ) -> ResellResult:
        """List a purchased item for resale.

        Parameters
        ----------
        purchase : PurchaseRecord
            The completed purchase to resell.
        platform : ResellPlatform, optional
            Target platform. Defaults to ``default_platform``.
        markup_pct : float, optional
            Override markup percentage.

        Returns
        -------
        ResellResult
        """
        platform = platform or self._default_platform
        markup = markup_pct if markup_pct is not None else self._markup_pct

        adapter = self._adapters.get(platform)
        if not adapter:
            return ResellResult(
                success=False,
                error=f"No adapter registered for platform={platform.value}",
            )

        asking_price = self._calc_price(purchase.purchase_price, markup)

        try:
            listing_id = await adapter.create_listing(
                title=purchase.product_name or f"Item {purchase.item_id}",
                price=asking_price,
                description=self._build_description(purchase),
                images=[purchase.image_url] if purchase.image_url else None,
            )
        except Exception as exc:
            return ResellResult(
                success=False,
                error=f"Listing creation failed: {exc}",
            )

        listing = ResellListing(
            listing_id=listing_id,
            platform=platform,
            purchase_ref=purchase.item_id,
            asking_price=asking_price,
            original_price=purchase.purchase_price,
            markup_pct=markup,
            status=ListingStatus.ACTIVE,
        )
        listing.price_history.append((listing.created_at, asking_price))
        self._listings.append(listing)

        logger.info(
            "Listed %s on %s: cost=%.0f ask=%.0f markup=%.0f%%",
            purchase.item_id, platform.value,
            purchase.purchase_price, asking_price, markup,
        )

        return ResellResult(success=True, listing=listing)

    async def _reprice_listing(self, listing: ResellListing) -> bool:
        """Check if a listing needs repricing and apply it.

        Returns True if repriced.
        """
        if listing.status != ListingStatus.ACTIVE:
            return False

        days_listed = (datetime.now(timezone.utc) - listing.created_at).days
        if days_listed < self._reprice_after_days:
            return False

        # Check how many reprice cycles have happened
        cycles = len(listing.price_history) - 1  # first entry is initial price
        max_cycles = days_listed // self._reprice_after_days
        if cycles >= max_cycles:
            return False

        # Calculate new price
        current = listing.asking_price
        new_price = round(current * (1 - self._reprice_drop_pct / 100))

        # Check minimum markup floor
        min_price = self._calc_price(listing.original_price, self._min_markup_pct)
        if new_price < min_price:
            logger.info(
                "Listing %s at min markup floor (%.0f), not dropping further",
                listing.listing_id, min_price,
            )
            return False

        # Apply price update
        adapter = self._adapters.get(listing.platform)
        if not adapter:
            return False

        try:
            success = await adapter.update_price(listing.listing_id, new_price)
            if success:
                listing.record_price_change(new_price)
                listing.status = ListingStatus.REPRICED
                logger.info(
                    "Repriced %s: %.0f → %.0f (-%.0f%%)",
                    listing.listing_id, current, new_price, self._reprice_drop_pct,
                )
                return True
        except Exception as exc:
            logger.warning("Reprice failed for %s: %s", listing.listing_id, exc)

        return False

    async def check_sold(self) -> list[ResellListing]:
        """Check all active listings for sales.

        Returns list of newly sold listings.
        """
        sold: list[ResellListing] = []
        for listing in self._listings:
            if listing.status != ListingStatus.ACTIVE:
                continue

            adapter = self._adapters.get(listing.platform)
            if not adapter:
                continue

            try:
                is_sold = await adapter.check_sold(listing.listing_id)
                if is_sold:
                    listing.status = ListingStatus.SOLD
                    listing.updated_at = datetime.now(timezone.utc)
                    sold.append(listing)
                    logger.info(
                        "SOLD: %s on %s for %.0f (cost=%.0f, profit=%.0f)",
                        listing.listing_id, listing.platform.value,
                        listing.asking_price, listing.original_price,
                        listing.asking_price - listing.original_price,
                    )
            except Exception as exc:
                logger.warning(
                    "Sold-check failed for %s: %s", listing.listing_id, exc,
                )

        return sold

    async def monitor_listings(
        self,
        check_interval: float = 3600.0,
    ) -> None:
        """Continuously monitor listings: auto-reprice and detect sales.

        Runs until :meth:`stop` is called.

        Parameters
        ----------
        check_interval : float
            Seconds between monitoring cycles. Default 3600 (1 hour).
        """
        self._running = True
        logger.info("Resell monitor started (%d listings)", len(self._listings))

        while self._running:
            # Reprice stale listings
            for listing in self._listings:
                await self._reprice_listing(listing)

            # Check for sales
            sold = await self.check_sold()
            if sold:
                logger.info("Monitor cycle: %d items sold", len(sold))

            await asyncio.sleep(check_interval)

        logger.info("Resell monitor stopped")

    def stop(self) -> None:
        """Stop the monitoring loop."""
        self._running = False

    def summary(self) -> dict[str, Any]:
        """Return a summary of all listings."""
        total_cost = sum(l.original_price for l in self._listings)
        total_ask = sum(l.asking_price for l in self._listings)
        sold = [l for l in self._listings if l.status == ListingStatus.SOLD]
        revenue = sum(l.asking_price for l in sold)
        profit = revenue - sum(l.original_price for l in sold)

        return {
            "total_listings": len(self._listings),
            "active": sum(1 for l in self._listings if l.status == ListingStatus.ACTIVE),
            "sold": len(sold),
            "total_cost": total_cost,
            "total_asking": total_ask,
            "revenue": revenue,
            "profit": profit,
        }
