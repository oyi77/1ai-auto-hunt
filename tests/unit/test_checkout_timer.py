"""Unit tests for Flash Sale timer."""
import pytest
from datetime import datetime, timezone, timedelta
from src.hunts.checkout.timer import format_hms, format_hms_ms, FlashSaleTimer


class TestFormatHms:
    def test_basic(self):
        assert format_hms(5545.0) == "01:32:25"

    def test_zero(self):
        assert format_hms(0.0) == "00:00:00"

    def test_negative(self):
        result = format_hms(-60.0)
        assert result.startswith("-")
        assert "00:01:00" in result

    def test_exact_hour(self):
        assert format_hms(3600.0) == "01:00:00"


class TestFlashSaleTimer:
    def test_sub_ms_offset(self):
        """sub_ms should subtract from target time."""
        target = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
        timer = FlashSaleTimer(target, sub_ms=200)
        assert timer._effective_target < target

    def test_naive_target_gets_utc(self):
        """Naive datetime should be assumed UTC."""
        target = datetime(2026, 7, 1, 12, 0, 0)
        timer = FlashSaleTimer(target)
        assert timer._target_utc.tzinfo is not None
