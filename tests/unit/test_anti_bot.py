"""Unit tests for Shopee anti-bot header generation."""
import pytest
from src.hunts.checkout.anti_bot import ShopeeAntiBot


class TestShopeeAntiBot:
    def setup_method(self):
        self.bot = ShopeeAntiBot()

    def test_headers_as_dict(self):
        """generate_headers returns GeneratedHeaders with as_dict()."""
        h = self.bot.generate_headers()
        d = h.as_dict()
        assert isinstance(d, dict)
        assert "X-Csrftoken" in d

    def test_csrf_token_length(self):
        """CSRF token should be at least 16 chars."""
        d = self.bot.generate_headers().as_dict()
        assert len(d["X-Csrftoken"]) >= 16

    def test_headers_unique(self):
        """Two calls should produce different CSRF tokens."""
        d1 = self.bot.generate_headers().as_dict()
        d2 = self.bot.generate_headers().as_dict()
        assert d1["X-Csrftoken"] != d2["X-Csrftoken"]

    def test_user_agent_rotation(self):
        """User-Agent should vary across calls."""
        agents = set()
        for _ in range(20):
            d = self.bot.generate_headers().as_dict()
            agents.add(d.get("User-Agent", ""))
        assert len(agents) >= 2, "User-Agent should rotate"

    def test_required_headers_present(self):
        """Key anti-bot headers should be present."""
        d = self.bot.generate_headers().as_dict()
        assert "X-Csrftoken" in d
        assert "User-Agent" in d
