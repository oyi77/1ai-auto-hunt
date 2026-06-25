"""Unit tests for Boost Service pricing engine."""
import pytest
from decimal import Decimal
from src.hunts.boost.pricing import PricingEngine


class TestBoostPricing:
    def setup_method(self):
        self.engine = PricingEngine()

    def test_basic_price_positive(self):
        """Price should always be positive."""
        result = self.engine.calculate(
            platform="instagram", action="followers", quantity=1000
        )
        assert result.total_cost > 0

    def test_bulk_discount(self):
        """Larger orders should get lower per-unit price."""
        small = self.engine.calculate(
            platform="instagram", action="followers", quantity=1000
        )
        large = self.engine.calculate(
            platform="instagram", action="followers", quantity=10000
        )
        small_per_unit = small.effective_price_per_unit
        large_per_unit = large.effective_price_per_unit
        assert large_per_unit < small_per_unit

    def test_fast_speed_costs_more(self):
        """Fast speed should cost more than normal."""
        normal = self.engine.calculate(
            platform="instagram", action="likes", quantity=1000
        )
        fast = self.engine.calculate(
            platform="instagram", action="likes", quantity=1000, speed="fast"
        )
        assert fast.total_cost > normal.total_cost

    def test_result_has_all_fields(self):
        """PricingResult should contain all expected fields."""
        result = self.engine.calculate(
            platform="tiktok", action="views", quantity=5000
        )
        assert hasattr(result, "total_cost")
        assert hasattr(result, "effective_price_per_unit")
        assert hasattr(result, "bulk_discount")
        assert hasattr(result, "speed_multiplier")
        assert hasattr(result, "savings_pct")
