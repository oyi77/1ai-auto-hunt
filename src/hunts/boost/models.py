"""
Boost Service — SQLAlchemy models.

Tables:
    boost_orders    — Customer boost orders with lifecycle tracking.
    pricing_tiers   — Per-platform/action/speed base pricing.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return f"BOOST-{uuid.uuid4().hex[:12].upper()}"


# ---------------------------------------------------------------------------
# Order status enum (string-based for portability)
# ---------------------------------------------------------------------------

class OrderStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    PARTIAL = "partial"          # some quantity delivered, rest failed
    CANCELLED = "cancelled"
    FAILED = "failed"
    REFILLING = "refilling"      # retention guarantee active

    ALL = {PENDING, PROCESSING, IN_PROGRESS, COMPLETED, PARTIAL, CANCELLED, FAILED, REFILLING}
    ACTIVE = {PENDING, PROCESSING, IN_PROGRESS, REFILLING}


# ---------------------------------------------------------------------------
# Supported platforms & actions
# ---------------------------------------------------------------------------

PLATFORMS = {
    "instagram",
    "tiktok",
    "youtube",
    "twitter",
    "facebook",
    "telegram",
    "threads",
    "spotify",
    "shopee",
    "twitch",
}

PLATFORM_ACTIONS: dict[str, list[str]] = {
    "instagram": ["followers", "likes", "views", "comments", "saves", "reels_views", "story_views"],
    "tiktok":    ["followers", "likes", "views", "comments", "shares"],
    "youtube":   ["subscribers", "likes", "views", "comments", "watch_hours"],
    "twitter":   ["followers", "likes", "retweets", "comments", "views"],
    "facebook":  ["followers", "likes", "views", "comments", "shares", "page_likes"],
    "telegram":  ["members", "views", "reactions"],
    "threads":   ["followers", "likes", "views"],
    "spotify":   ["plays", "followers", "saves"],
    "shopee":    ["followers", "views", "cart_adds"],
    "twitch":    ["followers", "views", "chatters"],
}

SPEED_OPTIONS = {"slow", "normal", "fast"}


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------


class BoostOrder(Base):
    """A customer boost order."""

    __tablename__ = "boost_orders"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=_new_id
    )
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    speed: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=OrderStatus.PENDING
    )
    completed_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Drip-feed config
    drip_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    drip_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Retention
    retention_guarantee: Mapped[bool] = mapped_column(default=True)
    refill_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=_utcnow, onupdate=_utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Extra metadata (JSON string for flexibility)
    meta: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_boost_orders_user_status", "user_id", "status"),
        Index("ix_boost_orders_platform_action", "platform", "action"),
    )

    def __repr__(self) -> str:
        return (
            f"<BoostOrder {self.id} {self.platform}/{self.action} "
            f"x{self.quantity} status={self.status}>"
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "platform": self.platform,
            "action": self.action,
            "target_url": self.target_url,
            "quantity": self.quantity,
            "speed": self.speed,
            "cost": str(self.cost),
            "status": self.status,
            "completed_quantity": self.completed_quantity,
            "drip_days": self.drip_days,
            "drip_per_day": self.drip_per_day,
            "retention_guarantee": self.retention_guarantee,
            "refill_count": self.refill_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class PricingTier(Base):
    """Base price per unit for a platform/action/speed combination."""

    __tablename__ = "pricing_tiers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    speed: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    price_per_unit: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)

    __table_args__ = (
        Index(
            "ix_pricing_tiers_unique",
            "platform",
            "action",
            "speed",
            unique=True,
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<PricingTier {self.platform}/{self.action}/{self.speed} "
            f"${self.price_per_unit}/unit>"
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "platform": self.platform,
            "action": self.action,
            "speed": self.speed,
            "price_per_unit": str(self.price_per_unit),
        }
