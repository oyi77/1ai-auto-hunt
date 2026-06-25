"""Dynamic sell-price calculation for manufactured accounts.

Pricing factors:

1. **Age tier** — older accounts are exponentially more valuable.
2. **Follower count** — linear engagement multiplier.
3. **Platform multiplier** — market-adjusted per platform.

Tier matrix::

    fresh (0d)   = $0.50
    7-day        = $5.00
    30-day       = $20.00
    90-day       = $50.00
    180-day+     = $100.00+
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.core.logger import get_logger

if TYPE_CHECKING:
    from src.hunts.factory.models import Account

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Price tiers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PriceTier:
    """A single age-based price bracket."""
    min_days: int
    base_price: float

    def matches(self, age_days: int) -> bool:
        return age_days >= self.min_days


TIERS: list[PriceTier] = [
    PriceTier(min_days=0, base_price=0.50),
    PriceTier(min_days=7, base_price=5.00),
    PriceTier(min_days=14, base_price=10.00),
    PriceTier(min_days=30, base_price=20.00),
    PriceTier(min_days=60, base_price=35.00),
    PriceTier(min_days=90, base_price=50.00),
    PriceTier(min_days=180, base_price=100.00),
    PriceTier(min_days=365, base_price=200.00),
]


# ---------------------------------------------------------------------------
# Platform multipliers
# ---------------------------------------------------------------------------

PLATFORM_MULTIPLIERS: dict[str, float] = {
    "gmail": 1.0,
    "instagram": 1.5,
    "tiktok": 1.3,
    "twitter": 1.2,
    "shopee": 0.8,
}


# ---------------------------------------------------------------------------
# Follower pricing
# ---------------------------------------------------------------------------

FOLLOWER_PRICE_PER_100 = 0.25  # $0.25 per 100 followers


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class PricingEngine:
    """Calculate the sell price for a manufactured account.

    Usage::

        engine = PricingEngine()
        price = engine.calculate_sell_price(account)
    """

    def __init__(
        self,
        tiers: list[PriceTier] | None = None,
        platform_multipliers: dict[str, float] | None = None,
        follower_rate: float | None = None,
    ) -> None:
        self._tiers = tiers or TIERS
        self._platform_multipliers = platform_multipliers or PLATFORM_MULTIPLIERS
        self._follower_rate = (
            follower_rate if follower_rate is not None else FOLLOWER_PRICE_PER_100
        )

    def calculate_sell_price(self, account: Account) -> float:
        """Compute the current market price for *account*.

        The formula::

            price = (base_tier + (followers / 100) * follower_rate)
                  × platform_multiplier

        Returns a float rounded to 2 decimal places.
        """
        # 1) Age-based base price
        base = self._base_for_age(account.age_days)

        # 2) Follower premium
        follower_premium = (account.followers / 100.0) * self._follower_rate

        # 3) Platform multiplier
        platform_key = (
            account.platform.value
            if hasattr(account.platform, "value")
            else str(account.platform)
        )
        multiplier = self._platform_multipliers.get(platform_key, 1.0)

        raw_price = (base + follower_premium) * multiplier
        final = round(max(raw_price, 0.0), 2)

        logger.debug(
            "price_calculated",
            account_id=getattr(account, "id", "?"),
            age_days=account.age_days,
            followers=account.followers,
            platform=platform_key,
            base=base,
            follower_premium=round(follower_premium, 2),
            multiplier=multiplier,
            final=final,
        )
        return final

    def batch_price(self, accounts: list[Account]) -> dict[int, float]:
        """Price multiple accounts in one pass.  Returns ``{id: price}``."""
        return {
            account.id: self.calculate_sell_price(account)
            for account in accounts
            if hasattr(account, "id")
        }

    def _base_for_age(self, age_days: int) -> float:
        """Return the highest-matching tier base price."""
        matched = 0.0
        for tier in self._tiers:
            if tier.matches(age_days):
                matched = tier.base_price
        return matched
