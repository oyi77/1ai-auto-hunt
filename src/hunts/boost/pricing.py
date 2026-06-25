"""
Boost Service — PricingEngine.

Calculates order cost from:
  1. Base price per unit (platform + action)
  2. Bulk discount tiers
  3. Speed multiplier
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from src.hunts.boost.models import PLATFORMS, PLATFORM_ACTIONS, SPEED_OPTIONS


# ---------------------------------------------------------------------------
# Bulk discount tiers — (minimum_quantity, multiplier)
# ---------------------------------------------------------------------------

BULK_DISCOUNTS: list[tuple[int, Decimal]] = [
    (50_000, Decimal("0.70")),
    (10_000, Decimal("0.80")),
    (5_000, Decimal("0.90")),
    (1_000, Decimal("1.00")),
]

SPEED_MULTIPLIERS: dict[str, Decimal] = {
    "slow":   Decimal("0.80"),   # 20% cheaper
    "normal": Decimal("1.00"),
    "fast":   Decimal("1.50"),   # 50% premium
}


# ---------------------------------------------------------------------------
# Base price table (USD per unit) — market-rate estimates
# ---------------------------------------------------------------------------

_BASE_PRICES: dict[str, dict[str, Decimal]] = {
    "instagram": {
        "followers":   Decimal("0.005"),
        "likes":       Decimal("0.002"),
        "views":       Decimal("0.0005"),
        "comments":    Decimal("0.02"),
        "saves":       Decimal("0.003"),
        "reels_views": Decimal("0.0005"),
        "story_views": Decimal("0.0008"),
    },
    "tiktok": {
        "followers": Decimal("0.006"),
        "likes":     Decimal("0.002"),
        "views":     Decimal("0.0003"),
        "comments":  Decimal("0.025"),
        "shares":    Decimal("0.008"),
    },
    "youtube": {
        "subscribers":  Decimal("0.015"),
        "likes":        Decimal("0.003"),
        "views":        Decimal("0.001"),
        "comments":     Decimal("0.04"),
        "watch_hours":  Decimal("0.08"),
    },
    "twitter": {
        "followers": Decimal("0.008"),
        "likes":     Decimal("0.002"),
        "retweets":  Decimal("0.005"),
        "comments":  Decimal("0.03"),
        "views":     Decimal("0.0004"),
    },
    "facebook": {
        "followers":  Decimal("0.006"),
        "likes":      Decimal("0.002"),
        "views":      Decimal("0.0005"),
        "comments":   Decimal("0.025"),
        "shares":     Decimal("0.008"),
        "page_likes": Decimal("0.005"),
    },
    "telegram": {
        "members":   Decimal("0.01"),
        "views":     Decimal("0.0003"),
        "reactions": Decimal("0.004"),
    },
    "threads": {
        "followers": Decimal("0.007"),
        "likes":     Decimal("0.002"),
        "views":     Decimal("0.0005"),
    },
    "spotify": {
        "plays":     Decimal("0.002"),
        "followers": Decimal("0.01"),
        "saves":     Decimal("0.005"),
    },
    "shopee": {
        "followers": Decimal("0.008"),
        "views":     Decimal("0.0004"),
        "cart_adds": Decimal("0.012"),
    },
    "twitch": {
        "followers": Decimal("0.01"),
        "views":     Decimal("0.002"),
        "chatters":  Decimal("0.03"),
    },
}


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PricingResult:
    """Immutable pricing breakdown."""

    platform: str
    action: str
    quantity: int
    speed: str
    base_price_per_unit: Decimal
    bulk_discount: Decimal        # multiplier, e.g. 0.8
    speed_multiplier: Decimal     # e.g. 1.5
    effective_price_per_unit: Decimal
    total_cost: Decimal
    savings_pct: Decimal          # vs. list price at qty=1, speed=normal

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "action": self.action,
            "quantity": self.quantity,
            "speed": self.speed,
            "base_price_per_unit": str(self.base_price_per_unit),
            "bulk_discount": str(self.bulk_discount),
            "speed_multiplier": str(self.speed_multiplier),
            "effective_price_per_unit": str(self.effective_price_per_unit),
            "total_cost": str(self.total_cost),
            "savings_pct": str(self.savings_pct),
        }


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class PricingEngine:
    """Stateless pricing calculator.

    Usage::

        engine = PricingEngine()
        result = engine.calculate("instagram", "followers", 10_000, "fast")
        print(result.total_cost)
    """

    def __init__(
        self,
        base_prices: dict[str, dict[str, Decimal]] | None = None,
        bulk_discounts: list[tuple[int, Decimal]] | None = None,
        speed_multipliers: dict[str, Decimal] | None = None,
    ) -> None:
        self._base_prices = base_prices or _BASE_PRICES
        self._bulk_discounts = bulk_discounts or BULK_DISCOUNTS
        self._speed_multipliers = speed_multipliers or SPEED_MULTIPLIERS

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def validate(platform: str, action: str, quantity: int, speed: str) -> None:
        """Raise ValueError with a clear message on bad input."""
        if platform not in PLATFORMS:
            raise ValueError(
                f"Unknown platform '{platform}'. "
                f"Supported: {sorted(PLATFORMS)}"
            )
        valid_actions = PLATFORM_ACTIONS.get(platform, [])
        if action not in valid_actions:
            raise ValueError(
                f"Unknown action '{action}' for {platform}. "
                f"Supported: {valid_actions}"
            )
        if speed not in SPEED_OPTIONS:
            raise ValueError(
                f"Unknown speed '{speed}'. Supported: {sorted(SPEED_OPTIONS)}"
            )
        if quantity < 1:
            raise ValueError(f"Quantity must be >= 1, got {quantity}")
        if quantity > 1_000_000:
            raise ValueError(f"Quantity exceeds max 1,000,000")

    # ------------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------------

    def get_base_price(self, platform: str, action: str) -> Decimal:
        """Return base USD per-unit price. Raises KeyError if missing."""
        return self._base_prices[platform][action]

    def get_bulk_discount(self, quantity: int) -> Decimal:
        """Return the bulk discount multiplier for *quantity*."""
        for threshold, multiplier in self._bulk_discounts:
            if quantity >= threshold:
                return multiplier
        return Decimal("1.00")

    def get_speed_multiplier(self, speed: str) -> Decimal:
        return self._speed_multipliers.get(speed, Decimal("1.00"))

    # ------------------------------------------------------------------
    # Main calculation
    # ------------------------------------------------------------------

    def calculate(
        self,
        platform: str,
        action: str,
        quantity: int,
        speed: str = "normal",
    ) -> PricingResult:
        """Calculate full pricing breakdown.

        Args:
            platform: e.g. ``"instagram"``
            action:   e.g. ``"followers"``
            quantity: number of units
            speed:    ``"slow"`` / ``"normal"`` / ``"fast"``

        Returns:
            :class:`PricingResult` with all pricing details.

        Raises:
            ValueError: on invalid platform, action, speed, or quantity.
        """
        self.validate(platform, action, quantity, speed)

        base = self.get_base_price(platform, action)
        bulk = self.get_bulk_discount(quantity)
        spd = self.get_speed_multiplier(speed)

        effective = (base * bulk * spd).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )
        total = (effective * quantity).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # Savings vs. list price (qty=1, speed=normal)
        list_total = base * quantity
        savings = Decimal("0.00")
        if list_total > 0:
            savings = ((list_total - total) / list_total * 100).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        return PricingResult(
            platform=platform,
            action=action,
            quantity=quantity,
            speed=speed,
            base_price_per_unit=base,
            bulk_discount=bulk,
            speed_multiplier=spd,
            effective_price_per_unit=effective,
            total_cost=total,
            savings_pct=savings,
        )

    # ------------------------------------------------------------------
    # Convenience: full pricing table for a platform
    # ------------------------------------------------------------------

    def pricing_table(self, platform: str | None = None) -> list[dict]:
        """Return pricing table rows for API consumption."""
        rows: list[dict] = []
        platforms = [platform] if platform else sorted(self._base_prices.keys())
        for plat in platforms:
            actions = self._base_prices.get(plat, {})
            for act, base_price in sorted(actions.items()):
                for speed in sorted(SPEED_OPTIONS):
                    result = self.calculate(plat, act, 1000, speed)
                    rows.append({
                        "platform": plat,
                        "action": act,
                        "speed": speed,
                        "price_per_unit": str(result.effective_price_per_unit),
                        "min_qty": 1000,
                    })
        return rows
