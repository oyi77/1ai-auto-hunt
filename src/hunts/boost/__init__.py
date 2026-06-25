"""
Boost Service — Social media boost as a service.

Provides followers, likes, views, comments, and more across 10 platforms
with drip-feed delivery, retention guarantees, and anti-detection.

Quick Start::

    from src.hunts.boost.order_api import router as boost_router
    # app.include_router(boost_router, prefix="/boost", tags=["boost"])

    from src.hunts.boost.pricing import PricingEngine
    engine = PricingEngine()
    quote = engine.calculate("instagram", "followers", 10_000, "fast")

    from src.hunts.boost.fulfillment import BoostFulfillment
    fulfillment = BoostFulfillment()
    result = await fulfillment.fulfill(order)
"""

from src.hunts.boost.models import (
    BoostOrder,
    OrderStatus,
    PLATFORMS,
    PLATFORM_ACTIONS,
    SPEED_OPTIONS,
    PricingTier,
)
from src.hunts.boost.pricing import PricingEngine, PricingResult
from src.hunts.boost.fulfillment import BoostFulfillment
from src.hunts.boost.anti_detect import AntiDetectEngine
from src.hunts.boost.order_api import router

__all__ = [
    # Models
    "BoostOrder",
    "PricingTier",
    "OrderStatus",
    # Constants
    "PLATFORMS",
    "PLATFORM_ACTIONS",
    "SPEED_OPTIONS",
    # Engines
    "PricingEngine",
    "PricingResult",
    "BoostFulfillment",
    "AntiDetectEngine",
    # Router
    "router",
]
