"""SQLAlchemy models for the Domain Hunter hunt.

Tables:
    domain         — Individual domain records with SEO metrics and pricing.
    domain_scan    — Log of each scan batch (TLD + filters + result count).
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    """Declarative base shared by every Domain Hunter model."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DomainStatus(str, enum.Enum):
    """Lifecycle states a domain record can occupy."""

    AVAILABLE = "available"
    SCANNED = "scanned"
    VETTED = "vetted"
    SNIPED = "sniped"
    LISTED = "listed"
    SOLD = "sold"
    EXPIRED = "expired"
    BLACKLISTED = "blacklisted"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class Domain(Base):
    """A single domain with SEO metrics, pricing, and lifecycle state.

    Columns
    -------
    id            : surrogate PK
    domain        : FQDN (e.g. ``example.com``)
    da            : Domain Authority (Moz, 0-100)
    pa            : Page Authority (Moz, 0-100)
    backlinks     : total backlink count
    spam_score    : Moz spam score (0-17)
    price         : acquisition or listing price in USD
    status        : current lifecycle state
    registered_at : when the domain was originally registered (RDAP)
    tld           : top-level domain extracted from ``domain``
    created_at    : row creation timestamp (server default)
    updated_at    : last modification timestamp (auto-updated)
    scan_id       : FK → ``domain_scan.id`` (nullable — domain may exist independently)

    Indexes
    -------
    ix_domain_domain    : unique index on ``domain``
    ix_domain_status    : lookup by status
    ix_domain_da        : sort/filter by DA
    ix_domain_tld_da    : composite for scan queries
    """

    __tablename__ = "domain"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    domain: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    tld: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    da: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pa: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    backlinks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    spam_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[DomainStatus] = mapped_column(
        Enum(DomainStatus, native_enum=False, length=20),
        nullable=False,
        default=DomainStatus.SCANNED,
    )

    registered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Foreign key to scan batch (optional)
    scan_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("domain_scan.id", ondelete="SET NULL"), nullable=True
    )

    # Relationship back to the scan that discovered this domain
    scan: Mapped[Optional["DomainScan"]] = relationship(back_populates="domains")

    __table_args__ = (
        CheckConstraint("da >= 0 AND da <= 100", name="ck_domain_da_range"),
        CheckConstraint("pa >= 0 AND pa <= 100", name="ck_domain_pa_range"),
        CheckConstraint("spam_score >= 0 AND spam_score <= 17", name="ck_domain_spam_range"),
        CheckConstraint("backlinks >= 0", name="ck_domain_backlinks_nonneg"),
        Index("ix_domain_status", "status"),
        Index("ix_domain_da", "da"),
        Index("ix_domain_tld_da", "tld", "da"),
    )

    def __repr__(self) -> str:
        status_val = self.status.value if self.status else "none"
        return (
            f"<Domain {self.domain} da={self.da} pa={self.pa} "
            f"spam={self.spam_score} status={status_val}>"
        )

    def to_dict(self) -> dict:
        """Serialise to a plain dict (JSON-safe)."""
        return {
            "id": self.id,
            "domain": self.domain,
            "tld": self.tld,
            "da": self.da,
            "pa": self.pa,
            "backlinks": self.backlinks,
            "spam_score": self.spam_score,
            "price": self.price,
            "status": self.status.value if self.status else None,
            "registered_at": self.registered_at.isoformat() if self.registered_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "scan_id": self.scan_id,
        }


class DomainScan(Base):
    """A single scan batch executed by the domain scanner.

    Records the TLD, minimum DA threshold, and how many results came back.
    Each discovered ``Domain`` row links back here via ``scan_id``.
    """

    __tablename__ = "domain_scan"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    tld: Mapped[str] = mapped_column(String(32), nullable=False)
    min_da: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    min_pa: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_spam_score: Mapped[float] = mapped_column(Float, nullable=False, default=5.0)

    results_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    # One scan → many domains
    domains: Mapped[list["Domain"]] = relationship(
        back_populates="scan", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("min_da >= 0 AND min_da <= 100", name="ck_scan_min_da"),
        CheckConstraint("results_count >= 0", name="ck_scan_results_nonneg"),
    )

    def __repr__(self) -> str:
        return (
            f"<DomainScan tld={self.tld!r} min_da={self.min_da} "
            f"results={self.results_count} at={self.scanned_at}>"
        )

    def to_dict(self) -> dict:
        """Serialise to a plain dict (JSON-safe)."""
        return {
            "id": self.id,
            "tld": self.tld,
            "min_da": self.min_da,
            "min_pa": self.min_pa,
            "max_price": self.max_price,
            "max_spam_score": self.max_spam_score,
            "results_count": self.results_count,
            "scanned_at": self.scanned_at.isoformat() if self.scanned_at else None,
        }
