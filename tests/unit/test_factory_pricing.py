"""Unit tests for Account Factory pricing engine."""
import pytest
from dataclasses import dataclass
from src.hunts.factory.pricing import PricingEngine


@dataclass
class _FakeAccount:
    """Lightweight stand-in for Account ORM model."""
    platform: str = "gmail"
    age_days: int = 0
    followers: int = 0


class TestFactoryPricingEngine:
    """Account Factory pricing — pure logic, no I/O."""

    def setup_method(self):
        self.engine = PricingEngine()

    def test_fresh_gmail_base_price(self):
        price = self.engine.calculate_sell_price(
            _FakeAccount(platform="gmail", age_days=0, followers=0)
        )
        assert price >= 0.50

    def test_aged_account_costs_more(self):
        fresh = self.engine.calculate_sell_price(
            _FakeAccount(platform="gmail", age_days=0, followers=0)
        )
        aged = self.engine.calculate_sell_price(
            _FakeAccount(platform="gmail", age_days=30, followers=0)
        )
        assert aged > fresh

    def test_followers_increase_price(self):
        no_follow = self.engine.calculate_sell_price(
            _FakeAccount(platform="instagram", age_days=0, followers=0)
        )
        with_follow = self.engine.calculate_sell_price(
            _FakeAccount(platform="instagram", age_days=0, followers=1000)
        )
        assert with_follow > no_follow

    def test_instagram_costs_more_than_gmail(self):
        ig = self.engine.calculate_sell_price(
            _FakeAccount(platform="instagram", age_days=0, followers=0)
        )
        gmail = self.engine.calculate_sell_price(
            _FakeAccount(platform="gmail", age_days=0, followers=0)
        )
        assert ig >= gmail

    def test_price_never_zero(self):
        for p in ["gmail", "instagram", "tiktok", "twitter", "shopee"]:
            price = self.engine.calculate_sell_price(
                _FakeAccount(platform=p, age_days=0, followers=0)
            )
            assert price > 0, f"Price for {p} should be > 0"
