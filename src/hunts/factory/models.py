"""SQLAlchemy ORM models for the Account Factory.

Tables
------
- ``accounts``      — Individual social-media / e-commerce accounts.
- ``account_orders`` — Purchase orders from buyers.

All models inherit from ``src.core.db.Base`` so ``init_db()`` auto-creates
them.
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
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship,
)

from src.core.db import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Platform(str, enum.Enum):
    """Supported account platforms."""
    GMAIL = "gmail"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    TWITTER = "twitter"
    SHOPEE = "shopee"


class AccountStatus(str, enum.Enum):
    """Lifecycle states for a manufactured account."""
    CREATING = "creating"       # Browser session in progress
    VERIFYING = "verifying"     # Waiting for phone / email code
    CAPTCHA = "captcha"         # Blocked on captcha
    FRESH = "fresh"             # Created, not yet aged
    AGING = "aging"             # Currently being aged
    READY = "ready"             # Aged and listed for sale
    SOLD = "sold"               # Sold to a buyer
    BANNED = "banned"           # Permanently banned
    FAILED = "failed"           # Creation failed (retry or discard)


class OrderStatus(str, enum.Enum):
    """Lifecycle states for an account order."""
    PENDING = "pending"
    PAID = "paid"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------

class Account(Base):
    """A single manufactured social-media or e-commerce account.

    Tracks every credential, fingerprint, and metric needed for resale.
    """

    __tablename__ = "accounts"

    # -- Identity -----------------------------------------------------------
    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, native_enum=False, length=20), nullable=False, index=True
    )
    username: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    password: Mapped[str] = mapped_column(String(512), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # -- Fingerprint --------------------------------------------------------
    proxy_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    device_serial: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # -- Lifecycle ----------------------------------------------------------
    status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus, native_enum=False, length=20),
        nullable=False,
        default=AccountStatus.CREATING,
        index=True,
    )
    age_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # -- Engagement metrics -------------------------------------------------
    followers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # -- Commerce -----------------------------------------------------------
    sell_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # -- Timestamps ---------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    sold_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # -- Relationships ------------------------------------------------------
    order_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("account_orders.id"), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<Account id={self.id} platform={self.platform.value} "
            f"user={self.username!r} status={self.status.value}>"
        )

    def to_dict(self) -> dict:
        """Serializable snapshot for API responses."""
        return {
            "id": self.id,
            "platform": self.platform.value,
            "username": self.username,
            "email": self.email,
            "phone": self.phone,
            "proxy_ip": self.proxy_ip,
            "device_serial": self.device_serial,
            "status": self.status.value,
            "age_days": self.age_days,
            "followers": self.followers,
            "sell_price": self.sell_price,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "sold_at": self.sold_at.isoformat() if self.sold_at else None,
            "order_id": self.order_id,
        }


# ---------------------------------------------------------------------------
# Account Order
# ---------------------------------------------------------------------------

class AccountOrder(Base):
    """A purchase order for one or more accounts.

    Created when a buyer selects accounts from the store inventory.
    """

    __tablename__ = "account_orders"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    accounts: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )  # JSON list of account IDs
    total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, native_enum=False, length=20),
        nullable=False,
        default=OrderStatus.PENDING,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"<AccountOrder id={self.id} user={self.user_id!r} "
            f"total=${self.total:.2f} status={self.status.value}>"
        )

    def to_dict(self) -> dict:
        """Serializable snapshot for API responses."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "accounts": self.accounts,
            "total": self.total,
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
