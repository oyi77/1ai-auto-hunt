"""Flash Sale Bot — hunt checkout module.

Automated flash-sale checkout for Shopee, Tokopedia, and Lazada.
Includes price sniper, inventory monitor, auto-resell, and anti-bot
header generation.

Quick start::

    from hunts.checkout import ShopeeEngine, PriceSniper, FlashSaleItem, Platform

    item = FlashSaleItem(
        platform=Platform.SHOPEE,
        item_id="123456",
        shop_id="789",
        model_id="101112",
        target_price=100_000,
    )

    async with ShopeeEngine(cookies=cookies) as engine:
        sniper = PriceSniper(engine)
        result = await sniper.wait_and_buy(item, timeout=300)
        print(result.status)
"""

from __future__ import annotations

from .anti_bot import GeneratedHeaders, ShopeeAntiBot
from .models import (
    Base,
    CheckoutResult,
    CheckoutStatus,
    FlashSaleItem,
    ItemStatus,
    Platform,
    create_db,
)
from .monitor import (
    BatchFetcher,
    InventoryMonitor,
    MonitorCallback,
    MonitorEvent,
    MonitorEventType,
)
from .resell import (
    AutoResell,
    ListingStatus,
    OLXResellAdapter,
    PurchaseRecord,
    ResellListing,
    ResellPlatform,
    ResellResult,
    TokopediaResellAdapter,
)
from .shopee import CheckoutRequest, CheckoutResponse, ShopeeEngine, ShopeeRequestError
from .sniper import PriceSniper, SniperResult, SniperStatus
from .timer import (
    FlashSaleTimer,
    TimerSnapshot,
    format_hms,
    format_hms_ms,
    get_ntp_offset,
)
from .tokped import (
    TokopediaEngine,
    TokpedCheckoutRequest,
    TokpedCheckoutResponse,
    TokpedRequestError,
)

__all__ = [
    # Anti-bot
    "GeneratedHeaders",
    "ShopeeAntiBot",
    # Models
    "Base",
    "CheckoutResult",
    "CheckoutStatus",
    "FlashSaleItem",
    "ItemStatus",
    "Platform",
    "create_db",
    # Monitor
    "BatchFetcher",
    "InventoryMonitor",
    "MonitorCallback",
    "MonitorEvent",
    "MonitorEventType",
    # Resell
    "AutoResell",
    "ListingStatus",
    "OLXResellAdapter",
    "PurchaseRecord",
    "ResellListing",
    "ResellPlatform",
    "ResellResult",
    "TokopediaResellAdapter",
    # Shopee
    "CheckoutRequest",
    "CheckoutResponse",
    "ShopeeEngine",
    "ShopeeRequestError",
    # Sniper
    "PriceSniper",
    "SniperResult",
    "SniperStatus",
    # Timer
    "FlashSaleTimer",
    "TimerSnapshot",
    "format_hms",
    "format_hms_ms",
    "get_ntp_offset",
    # Tokopedia
    "TokopediaEngine",
    "TokpedCheckoutRequest",
    "TokpedCheckoutResponse",
    "TokpedRequestError",
]
