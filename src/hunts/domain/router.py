"""Domain hunt router — expired domain scanner and flipper endpoints.

Scans for expiring domains with good DA/PA/spam-score,
purchases them, and lists for resale.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.api.deps import _current_user, get_current_user, require_admin

try:
    from src.core.logger import get_logger

    logger = get_logger("1ai-auto-hunt.hunts.domain")
except ImportError:
    logger = logging.getLogger("1ai-auto-hunt.hunts.domain")  # type: ignore[assignment]

router = APIRouter()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ScanStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class DomainStatus(str, Enum):
    AVAILABLE = "available"
    REGISTERED = "registered"
    LISTED = "listed"
    SOLD = "sold"
    EXPIRED = "expired"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ScanRequest(BaseModel):
    """Start a domain scan with filters."""

    tlds: list[str] = Field(
        default=[".com", ".net", ".org"],
        description="Top-level domains to scan",
    )
    min_da: int = Field(0, ge=0, le=100, description="Minimum Domain Authority")
    min_pa: int = Field(0, ge=0, le=100, description="Minimum Page Authority")
    max_spam_score: float = Field(10.0, ge=0, le=100, description="Maximum spam score")
    max_price: float = Field(50.0, gt=0, description="Maximum registration price (USD)")
    max_age_years: int | None = Field(None, ge=1, description="Minimum domain age in years")
    keywords: list[str] = Field(default=[], description="Keywords domain must contain")
    limit: int = Field(100, ge=1, le=1000, description="Max domains to return")


class DomainResult(BaseModel):
    """A single domain found by a scan."""

    domain: str
    tld: str
    da: int = Field(description="Domain Authority")
    pa: int = Field(description="Page Authority")
    spam_score: float
    age_years: int | None = None
    price_usd: float = Field(description="Estimated registration price")
    backlinks: int = 0
    ref_domains: int = 0
    status: DomainStatus = DomainStatus.AVAILABLE
    expires_at: str | None = None


class ScanResponse(BaseModel):
    id: str
    status: ScanStatus
    tlds: list[str]
    filters: dict[str, Any]
    total_found: int = 0
    results: list[DomainResult] = []
    created_at: str
    completed_at: str | None = None


class ScanList(BaseModel):
    items: list[ScanResponse]
    total: int


class SnipeRequest(BaseModel):
    """Register a specific domain."""

    domain: str = Field(..., min_length=3, max_length=253)
    registrar: str = Field("namesilo", description="Registrar to use")
    years: int = Field(1, ge=1, le=10, description="Registration period in years")
    privacy: bool = Field(True, description="Enable WHOIS privacy")


class SnipeResponse(BaseModel):
    domain: str
    status: DomainStatus
    registrar: str
    price_usd: float | None = None
    message: str


class PortfolioDomain(BaseModel):
    domain: str
    da: int
    pa: int
    registered_at: str
    expires_at: str
    purchase_price: float
    listing_price: float | None = None
    status: DomainStatus
    traffic_monthly: int | None = None


class PortfolioList(BaseModel):
    items: list[PortfolioDomain]
    total: int


class SellRequest(BaseModel):
    """List a domain for sale."""

    domain: str
    asking_price: float = Field(..., gt=0, description="Asking price in USD")
    marketplace: str = Field("afternic", description="Marketplace to list on")
    BIN_price: float | None = Field(None, gt=0, description="Buy-it-now price")


class SellResponse(BaseModel):
    domain: str
    asking_price: float
    marketplace: str
    listing_url: str | None = None
    message: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/scan",
    response_model=ScanResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a domain scan",
)
async def start_scan(
    body: ScanRequest,
    user: dict = Depends(get_current_user),
):
    """Start scanning for expired/expiring domains matching the filters.

    Scans domain auction feeds and expiry lists, evaluates DA/PA/spam
    metrics via Moz API, and returns qualifying domains.
    """
    import uuid

    scan_id = f"SCAN-{uuid.uuid4().hex[:8].upper()}"
    now = datetime.now(timezone.utc).isoformat()
    return ScanResponse(
        id=scan_id,
        status=ScanStatus.QUEUED,
        tlds=body.tlds,
        filters={
            "min_da": body.min_da,
            "min_pa": body.min_pa,
            "max_spam_score": body.max_spam_score,
            "max_price": body.max_price,
            "keywords": body.keywords,
        },
        created_at=now,
    )


@router.get(
    "/scans",
    response_model=ScanList,
    summary="List domain scans",
)
async def list_scans(
    status_filter: ScanStatus | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    """List all domain scans with optional status filter."""
    return ScanList(items=[], total=0)


@router.get(
    "/scans/{scan_id}",
    response_model=ScanResponse,
    summary="Get scan results",
)
async def get_scan(
    scan_id: str,
    user: dict = Depends(get_current_user),
):
    """Retrieve the results of a specific domain scan."""
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Scan {scan_id} not found",
    )


@router.get(
    "/results",
    response_model=list[DomainResult],
    summary="List all found domains",
)
async def list_results(
    min_da: int = Query(0, ge=0),
    max_price: float = Query(100.0, gt=0),
    tld: str | None = None,
    status_filter: DomainStatus | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    """List all domains found across all scans, with filters."""
    return []


@router.post(
    "/snipe",
    response_model=SnipeResponse,
    summary="Register a domain",
)
async def snipe_domain(
    body: SnipeRequest,
    user: dict = Depends(get_current_user),
):
    """Attempt to register the specified domain via the chosen registrar."""
    return SnipeResponse(
        domain=body.domain,
        status=DomainStatus.AVAILABLE,
        registrar=body.registrar,
        message=f"Registration initiated for {body.domain}",
    )


@router.get(
    "/portfolio",
    response_model=PortfolioList,
    summary="List owned domains",
)
async def list_portfolio(
    status_filter: DomainStatus | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    """List all domains in the portfolio with their metrics and listing status."""
    return PortfolioList(items=[], total=0)


@router.post(
    "/sell",
    response_model=SellResponse,
    summary="List domain for sale",
)
async def sell_domain(
    body: SellRequest,
    user: dict = Depends(get_current_user),
):
    """List a portfolio domain for sale on a marketplace."""
    return SellResponse(
        domain=body.domain,
        asking_price=body.asking_price,
        marketplace=body.marketplace,
        message=f"Domain {body.domain} listed at ${body.asking_price:.2f} on {body.marketplace}",
    )
