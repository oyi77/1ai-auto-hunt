"""Inventory monitor — watch items for restock and price changes.

Detects restock events (stock 0→N) and price drops, alerting via
an async callback. Supports batch price-check optimization for
monitoring many items with minimal API calls.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional, Protocol

from .models import FlashSaleItem, Platform

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

class MonitorEventType(str, Enum):
    RESTOCK = "restock"         # stock went from 0 to N
    PRICE_DROP = "price_drop"   # price decreased
    PRICE_UP = "price_up"       # price increased
    OUT_OF_STOCK = "out_of_stock"  # item went OOS
    ERROR = "error"             # fetch failed


@dataclass
class MonitorEvent:
    """An event detected by the inventory monitor."""
    event_type: MonitorEventType
    item_id: str
    platform: Platform
    old_price: float | None = None
    new_price: float | None = None
    old_stock: int | None = None
    new_stock: int | None = None
    message: str = ""
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    @property
    def price_change_pct(self) -> float | None:
        """Percentage price change (negative = drop)."""
        if self.old_price and self.new_price and self.old_price > 0:
            return ((self.new_price - self.old_price) / self.old_price) * 100
        return None


# ---------------------------------------------------------------------------
# Internal snapshot
# ---------------------------------------------------------------------------

@dataclass
class _ItemSnapshot:
    """Cached state for a monitored item."""
    item: FlashSaleItem
    price: float | None = None
    stock: int | None = None  # None = unknown, 0 = OOS, >0 = in stock
    last_error: str | None = None


# ---------------------------------------------------------------------------
# Protocol for price/stock fetching
# ---------------------------------------------------------------------------

class ItemFetcher(Protocol):
    """Protocol for lightweight item state fetchers."""

    async def fetch_item_state(
        self, item: FlashSaleItem,
    ) -> tuple[float | None, int | None]:
        """Return (price, stock) for the item. stock=None if unknown."""
        ...


# ---------------------------------------------------------------------------
# Batch fetcher
# ---------------------------------------------------------------------------

class BatchFetcher:
    """Fetch item states concurrently with a concurrency limit.

    This wraps multiple :class:`ItemFetcher` calls with bounded parallelism
    to avoid overwhelming the platform API.

    Parameters
    ----------
    fetcher : ItemFetcher
        The underlying fetcher.
    concurrency : int
        Max concurrent fetches. Default 10.
    """

    def __init__(self, fetcher: ItemFetcher, concurrency: int = 10) -> None:
        self._fetcher = fetcher
        self._semaphore = asyncio.Semaphore(concurrency)

    async def fetch_batch(
        self, items: list[FlashSaleItem],
    ) -> list[tuple[FlashSaleItem, float | None, int | None]]:
        """Fetch states for all items with bounded concurrency.

        Returns a list of ``(item, price, stock)`` tuples.
        """
        async def _fetch_one(
            item: FlashSaleItem,
        ) -> tuple[FlashSaleItem, float | None, int | None]:
            async with self._semaphore:
                try:
                    price, stock = await self._fetcher.fetch_item_state(item)
                    return item, price, stock
                except Exception as exc:
                    logger.warning("Fetch failed for %s: %s", item.item_id, exc)
                    return item, None, None

        tasks = [_fetch_one(item) for item in items]
        return await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# InventoryMonitor
# ---------------------------------------------------------------------------

# Type alias for the callback
MonitorCallback = Callable[[MonitorEvent], Any]


class InventoryMonitor:
    """Watch a list of items for restock events and price changes.

    Detects:
    - **Restock**: stock transitions from 0 (or unknown) to N > 0.
    - **Price drop**: price decreases compared to last observed price.
    - **Out of stock**: item that was in stock goes to 0.

    Alerts are dispatched via an async callback.

    Usage::

        async def on_event(event: MonitorEvent):
            if event.event_type == MonitorEventType.RESTOCK:
                print(f"RESTOCK: {event.item_id} stock={event.new_stock}")

        monitor = InventoryMonitor(fetcher=my_fetcher)
        await monitor.watch(items=[...], callback=on_event, interval=5.0)

    Parameters
    ----------
    fetcher : ItemFetcher
        Object that can fetch (price, stock) for an item.
    concurrency : int
        Max parallel fetches per batch. Default 10.
    """

    def __init__(
        self,
        fetcher: ItemFetcher,
        concurrency: int = 10,
    ) -> None:
        self._fetcher = fetcher
        self._batch_fetcher = BatchFetcher(fetcher, concurrency=concurrency)
        self._snapshots: dict[str, _ItemSnapshot] = {}
        self._running = False

    def add_item(self, item: FlashSaleItem) -> None:
        """Register an item for monitoring."""
        if item.item_id not in self._snapshots:
            self._snapshots[item.item_id] = _ItemSnapshot(item=item)
            logger.info("Monitoring item %s (platform=%s)", item.item_id, item.platform.value)

    def remove_item(self, item_id: str) -> None:
        """Stop monitoring an item."""
        self._snapshots.pop(item_id, None)

    def stop(self) -> None:
        """Signal the monitor loop to stop."""
        self._running = False

    def _detect_events(
        self,
        snap: _ItemSnapshot,
        new_price: float | None,
        new_stock: int | None,
    ) -> list[MonitorEvent]:
        """Compare old snapshot against new values and emit events."""
        events: list[MonitorEvent] = []

        # --- restock detection ---
        old_stock = snap.stock
        if new_stock is not None:
            was_oos = old_stock is None or old_stock == 0
            is_in_stock = new_stock > 0
            if was_oos and is_in_stock:
                events.append(MonitorEvent(
                    event_type=MonitorEventType.RESTOCK,
                    item_id=snap.item.item_id,
                    platform=snap.item.platform,
                    old_stock=old_stock or 0,
                    new_stock=new_stock,
                    old_price=snap.price,
                    new_price=new_price,
                    message=f"Restocked: 0 → {new_stock}",
                ))
            elif old_stock is not None and old_stock > 0 and new_stock == 0:
                events.append(MonitorEvent(
                    event_type=MonitorEventType.OUT_OF_STOCK,
                    item_id=snap.item.item_id,
                    platform=snap.item.platform,
                    old_stock=old_stock,
                    new_stock=0,
                    old_price=snap.price,
                    new_price=new_price,
                    message=f"Out of stock: {old_stock} → 0",
                ))

        # --- price change detection ---
        old_price = snap.price
        if new_price is not None and old_price is not None:
            if new_price < old_price:
                events.append(MonitorEvent(
                    event_type=MonitorEventType.PRICE_DROP,
                    item_id=snap.item.item_id,
                    platform=snap.item.platform,
                    old_price=old_price,
                    new_price=new_price,
                    old_stock=old_stock,
                    new_stock=new_stock,
                    message=f"Price drop: {old_price:,.0f} → {new_price:,.0f}",
                ))
            elif new_price > old_price:
                events.append(MonitorEvent(
                    event_type=MonitorEventType.PRICE_UP,
                    item_id=snap.item.item_id,
                    platform=snap.item.platform,
                    old_price=old_price,
                    new_price=new_price,
                    old_stock=old_stock,
                    new_stock=new_stock,
                    message=f"Price increase: {old_price:,.0f} → {new_price:,.0f}",
                ))

        return events

    async def check_once(
        self,
        items: list[FlashSaleItem],
        callback: MonitorCallback | None = None,
    ) -> list[MonitorEvent]:
        """Perform a single batch check of all items.

        Useful for external event loops that want to drive the polling
        themselves rather than using :meth:`watch`.

        Returns all events detected.
        """
        # Ensure all items are registered
        for item in items:
            self.add_item(item)

        # Batch fetch
        results = await self._batch_fetcher.fetch_batch(items)
        all_events: list[MonitorEvent] = []

        for item, price, stock in results:
            snap = self._snapshots.get(item.item_id)
            if snap is None:
                continue

            events = self._detect_events(snap, price, stock)
            all_events.extend(events)

            # Update snapshot
            if price is not None:
                snap.price = price
            if stock is not None:
                snap.stock = stock

            # Update item bookkeeping
            if price is not None:
                item.last_price = price
                item.last_checked = datetime.now(timezone.utc)

        # Dispatch events
        if callback:
            for event in all_events:
                try:
                    result = callback(event)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    logger.exception(
                        "Callback error for event %s on %s",
                        event.event_type.value, event.item_id,
                    )

        return all_events

    async def watch(
        self,
        items: list[FlashSaleItem],
        callback: MonitorCallback,
        interval: float = 5.0,
    ) -> None:
        """Continuously monitor items until :meth:`stop` is called.

        Parameters
        ----------
        items : list[FlashSaleItem]
            Items to monitor.
        callback : MonitorCallback
            Async or sync callable invoked for each event.
        interval : float
            Seconds between batch polls. Default 5.0.
        """
        self._running = True
        logger.info(
            "Monitor started: %d items, interval=%.1fs",
            len(items), interval,
        )

        while self._running:
            events = await self.check_once(items, callback)

            if events:
                logger.info(
                    "Monitor cycle: %d events detected across %d items",
                    len(events), len(items),
                )

            await asyncio.sleep(interval)

        logger.info("Monitor stopped")
