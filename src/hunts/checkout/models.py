"""SQLAlchemy models for the Flash Sale Bot hunt module.

Tables:
    FlashSaleItem  — tracked product on a platform (Shopee / Tokopedia).
    CheckoutResult — one completed or attempted checkout per item.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    """Shared declarative base for all checkout models."""
    pass


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Platform(str, enum.Enum):
    SHOPEE = "shopee"
    TOKOPEDIA = "tokopedia"
    LAZADA = "lazada"


class ItemStatus(str, enum.Enum):
    MONITORING = "monitoring"   # actively polling price / stock
    SNIPING = "sniping"         # price reached threshold, attempting checkout
    CHECKED_OUT = "checked_out" # successfully purchased
    FAILED = "failed"           # checkout attempt failed
    CANCELLED = "cancelled"     # user cancelled tracking
    RESELLING = "reselling"     # purchased, now listed for resale


class CheckoutStatus(str, enum.Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    PAID = "paid"
    SHIPPED = "shipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# FlashSaleItem
# ---------------------------------------------------------------------------

class FlashSaleItem(Base):
    """A product we are tracking or have checked out.

    Columns mirror the Shopee/Tokopedia product identifier triple
    (item_id × shop_id × model_id) plus bookkeeping fields.
    """

    __tablename__ = "flash_sale_items"

    id: Mapped[int] = mapped_column(
            Integer, primary_key=True, autoincrement=True
        )

    # --- platform identifiers ---
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, native_enum=False, length=20), nullable=False
    )
    item_id: Mapped[str] = mapped_column(String(64), nullable=False)
    shop_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    model_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # --- tracking metadata ---
    target_price: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[ItemStatus] = mapped_column(
        Enum(ItemStatus, native_enum=False, length=20),
        default=ItemStatus.MONITORING,
        nullable=False,
    )
    last_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_checked: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- timestamps ---
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # --- relationships ---
    checkout_results: Mapped[list[CheckoutResult]] = relationship(
        "CheckoutResult", back_populates="item", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_fsi_platform_item", "platform", "item_id"),
        Index("ix_fsi_status", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<FlashSaleItem platform={self.platform.value} "
            f"item_id={self.item_id} status={self.status.value} "
            f"target={self.target_price:,.0f}>"
        )


# ---------------------------------------------------------------------------
# CheckoutResult
# ---------------------------------------------------------------------------

class CheckoutResult(Base):
    """One checkout attempt (successful or otherwise) for a tracked item."""

    __tablename__ = "checkout_results"

    id: Mapped[int] = mapped_column(
            Integer, primary_key=True, autoincrement=True
        )

    item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("flash_sale_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    checkout_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, doc="Platform order/checkout identifier"
    )
    price: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[CheckoutStatus] = mapped_column(
        Enum(CheckoutStatus, native_enum=False, length=20),
        default=CheckoutStatus.PENDING,
        nullable=False,
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # --- relationship ---
    item: Mapped[FlashSaleItem] = relationship(
        "FlashSaleItem", back_populates="checkout_results"
    )

    __table_args__ = (
        Index("ix_cr_item_id", "item_id"),
        Index("ix_cr_status", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<CheckoutResult id={self.id} checkout_id={self.checkout_id} "
            f"price={self.price:,.0f} status={self.status.value}>"
        )


# ---------------------------------------------------------------------------
# Engine helpers
# ---------------------------------------------------------------------------

def create_db(url: str = "sqlite:///checkout.db") -> sessionmaker[Session]:
    """Create the engine, ensure tables exist, return a session factory."""
    engine = create_engine(url, echo=False, future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
