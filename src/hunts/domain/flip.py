"""Domain Flip — list acquired domains for sale across marketplaces.

Supports simultaneous listing on:
- **Dan.com** (REST API) — modern domain marketplace with built-in escrow.
- **GoDaddy Aftermarket** (REST API) — largest domain aftermarket.
- **Sedo** (XML API) — European-focused marketplace.

Auto-negotiation: incoming offers >= 70% of asking price are accepted
automatically.  Lower offers trigger a counter-offer at 90% of asking.

Usage::

    flip = DomainFlip(
        dan_api_token="…",
        godaddy_key="…", godaddy_secret="…",
        sedo_partner_id="…",
    )
    listing = await flip.list_for_sale("example.com", price=500.0)
    print(listing.listings)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DAN_API_URL = "https://api.dan.com/v2"
GODADDY_AFTERMARKET_URL = "https://api.godaddy.com/v1/listings"
SEDO_API_URL = "https://api.sedo.com/api/v1"

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)
MARKETPLACE_TIMEOUT = aiohttp.ClientTimeout(total=20)

AUTO_ACCEPT_THRESHOLD = 0.70  # accept if offer >= 70% of asking
COUNTER_OFFER_RATIO = 0.90   # counter at 90% of asking


# ---------------------------------------------------------------------------
# Enums & data classes
# ---------------------------------------------------------------------------

class Marketplace(str, Enum):
    """Supported domain marketplaces."""

    DAN = "dan"
    GODADDY = "godaddy"
    SEDO = "sedo"


class ListingStatus(str, Enum):
    """Status of a marketplace listing."""

    ACTIVE = "active"
    PENDING = "pending"
    FAILED = "failed"
    EXPIRED = "expired"
    SOLD = "sold"


class OfferAction(str, Enum):
    """Auto-negotiation action taken on an offer."""

    ACCEPTED = "accepted"
    COUNTERED = "countered"
    REJECTED = "rejected"
    PENDING_REVIEW = "pending_review"


@dataclass(frozen=True, slots=True)
class MarketplaceListing:
    """A single listing on one marketplace."""

    marketplace: Marketplace
    listing_id: Optional[str]
    status: ListingStatus
    url: Optional[str]  # listing URL for the domain
    price: float
    currency: str
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "marketplace": self.marketplace.value,
            "listing_id": self.listing_id,
            "status": self.status.value,
            "url": self.url,
            "price": self.price,
            "currency": self.currency,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class FlipResult:
    """Result of listing a domain for sale across all marketplaces."""

    domain: str
    asking_price: float
    currency: str
    listings: tuple[MarketplaceListing, ...]
    listed_at: datetime
    elapsed_ms: int

    @property
    def active_count(self) -> int:
        """Number of successfully active listings."""
        return sum(1 for l in self.listings if l.status == ListingStatus.ACTIVE)

    @property
    def all_listed(self) -> bool:
        """True if every marketplace listing is active."""
        return all(l.status == ListingStatus.ACTIVE for l in self.listings)

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "asking_price": self.asking_price,
            "currency": self.currency,
            "listings": [l.to_dict() for l in self.listings],
            "active_count": self.active_count,
            "all_listed": self.all_listed,
            "listed_at": self.listed_at.isoformat(),
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass(frozen=True, slots=True)
class OfferResult:
    """Result of processing an incoming offer."""

    domain: str
    offer_amount: float
    asking_price: float
    action: OfferAction
    counter_amount: Optional[float]
    marketplace: Marketplace
    processed_at: datetime

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "offer_amount": self.offer_amount,
            "asking_price": self.asking_price,
            "action": self.action.value,
            "counter_amount": self.counter_amount,
            "marketplace": self.marketplace.value,
            "processed_at": self.processed_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class _MarketplaceAttempt:
    """Internal result from a single marketplace listing attempt."""

    marketplace: Marketplace
    listing: Optional[MarketplaceListing] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Flip
# ---------------------------------------------------------------------------

class DomainFlip:
    """List domains for sale across multiple marketplaces with auto-negotiation.

    Parameters
    ----------
    dan_api_token : str | None
        Dan.com API bearer token.
    godaddy_key : str | None
        GoDaddy API key.
    godaddy_secret : str | None
        GoDaddy API secret.
    sedo_partner_id : str | None
        Sedo partner ID.
    sedo_api_key : str | None
        Sedo API key.
    auto_accept_threshold : float
        Minimum ratio of asking price to auto-accept (default 0.70).
    counter_offer_ratio : float
        Ratio of asking price used for counter-offers (default 0.90).
    """

    def __init__(
        self,
        dan_api_token: Optional[str] = None,
        godaddy_key: Optional[str] = None,
        godaddy_secret: Optional[str] = None,
        sedo_partner_id: Optional[str] = None,
        sedo_api_key: Optional[str] = None,
        auto_accept_threshold: float = AUTO_ACCEPT_THRESHOLD,
        counter_offer_ratio: float = COUNTER_OFFER_RATIO,
        timeout: Optional[aiohttp.ClientTimeout] = None,
    ) -> None:
        self._dan_token = dan_api_token
        self._gd_key = godaddy_key
        self._gd_secret = godaddy_secret
        self._sedo_partner = sedo_partner_id
        self._sedo_key = sedo_api_key
        self._accept_threshold = auto_accept_threshold
        self._counter_ratio = counter_offer_ratio
        self._timeout = timeout or DEFAULT_TIMEOUT

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def list_for_sale(
        self,
        domain: str,
        price: float,
        currency: str = "USD",
        *,
        description: Optional[str] = None,
        category: Optional[str] = None,
        min_offer: Optional[float] = None,
    ) -> FlipResult:
        """List *domain* for sale on all configured marketplaces.

        Each marketplace listing is attempted concurrently.  Failures on
        one marketplace do not prevent listing on others.

        Parameters
        ----------
        domain : str
            Fully qualified domain name.
        price : float
            Asking price in the given currency.
        currency : str
            ISO 4217 currency code (default ``"USD"``).
        description : str | None
            Optional listing description.
        category : str | None
            Optional marketplace category.
        min_offer : float | None
            Minimum acceptable offer.  Defaults to ``price * auto_accept_threshold``.

        Returns
        -------
        FlipResult
            Contains per-marketplace listing status.
        """
        domain = domain.lower().strip()
        start = time.monotonic()

        if min_offer is None:
            min_offer = round(price * self._accept_threshold, 2)

        listing_desc = description or f"Premium domain: {domain}"

        # Fire all marketplace listings concurrently
        tasks: list[asyncio.Task[_MarketplaceAttempt]] = []

        if self._dan_token:
            tasks.append(
                asyncio.create_task(
                    self._list_dan(domain, price, currency, listing_desc, min_offer)
                )
            )

        if self._gd_key and self._gd_secret:
            tasks.append(
                asyncio.create_task(
                    self._list_godaddy(domain, price, currency, listing_desc)
                )
            )

        if self._sedo_partner and self._sedo_key:
            tasks.append(
                asyncio.create_task(
                    self._list_sedo(domain, price, currency, listing_desc)
                )
            )

        if not tasks:
            logger.warning("No marketplaces configured for listing %s", domain)
            return FlipResult(
                domain=domain,
                asking_price=price,
                currency=currency,
                listings=(),
                listed_at=datetime.now(timezone.utc),
                elapsed_ms=0,
            )

        attempts = await asyncio.gather(*tasks, return_exceptions=True)

        listings: list[MarketplaceListing] = []
        for result in attempts:
            if isinstance(result, Exception):
                logger.error("Marketplace listing task failed: %s", result)
                continue
            if result.listing:
                listings.append(result.listing)
            else:
                listings.append(
                    MarketplaceListing(
                        marketplace=result.marketplace,
                        listing_id=None,
                        status=ListingStatus.FAILED,
                        url=None,
                        price=price,
                        currency=currency,
                        error=result.error,
                    )
                )

        elapsed_ms = int((time.monotonic() - start) * 1000)

        flip_result = FlipResult(
            domain=domain,
            asking_price=price,
            currency=currency,
            listings=tuple(listings),
            listed_at=datetime.now(timezone.utc),
            elapsed_ms=elapsed_ms,
        )

        logger.info(
            "Listed %s for %s %s on %d/%d marketplaces (%dms)",
            domain,
            currency,
            price,
            flip_result.active_count,
            len(listings),
            elapsed_ms,
        )

        return flip_result

    async def process_offer(
        self,
        domain: str,
        offer_amount: float,
        asking_price: float,
        marketplace: Marketplace,
    ) -> OfferResult:
        """Evaluate and respond to an incoming offer using auto-negotiation rules.

        Rules:
        - Offer >= ``auto_accept_threshold`` × asking → **accept**
        - Offer >= ``counter_offer_ratio`` × asking × 0.5 → **counter** at ``counter_offer_ratio`` × asking
        - Otherwise → **reject**

        Parameters
        ----------
        domain : str
            The domain the offer is for.
        offer_amount : float
            The incoming offer amount.
        asking_price : float
            The current asking price.
        marketplace : Marketplace
            Which marketplace the offer came from.

        Returns
        -------
        OfferResult
            The action taken and any counter-offer amount.
        """
        accept_min = round(asking_price * self._accept_threshold, 2)
        counter_price = round(asking_price * self._counter_ratio, 2)
        reject_below = round(counter_price * 0.50, 2)

        if offer_amount >= accept_min:
            action = OfferAction.ACCEPTED
            counter = None
            logger.info(
                "AUTO-ACCEPTED offer of %s on %s (asking=%s, threshold=%s)",
                offer_amount, domain, asking_price, accept_min,
            )
        elif offer_amount >= reject_below:
            action = OfferAction.COUNTERED
            counter = counter_price
            logger.info(
                "COUNTERED offer of %s on %s with %s (asking=%s)",
                offer_amount, domain, counter_price, asking_price,
            )
        else:
            action = OfferAction.REJECTED
            counter = None
            logger.info(
                "REJECTED offer of %s on %s (too low, asking=%s)",
                offer_amount, domain, asking_price,
            )

        return OfferResult(
            domain=domain,
            offer_amount=offer_amount,
            asking_price=asking_price,
            action=action,
            counter_amount=counter,
            marketplace=marketplace,
            processed_at=datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Dan.com
    # ------------------------------------------------------------------

    async def _list_dan(
        self,
        domain: str,
        price: float,
        currency: str,
        description: str,
        min_offer: float,
    ) -> _MarketplaceAttempt:
        """List domain on Dan.com."""
        headers = {
            "Authorization": f"Bearer {self._dan_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        payload = {
            "domain_name": domain,
            "buy_now_price": price,
            "currency": currency,
            "minimum_offer_amount": min_offer,
            "category_id": "general",
            "description": description,
            "for_sale": True,
            "make_offer": True,
        }

        try:
            async with aiohttp.ClientSession(timeout=MARKETPLACE_TIMEOUT) as session:
                async with session.post(
                    f"{DAN_API_URL}/domains",
                    json=payload,
                    headers=headers,
                ) as resp:
                    if resp.status in (200, 201):
                        data = await resp.json()
                        domain_data = data.get("data", {})
                        listing_id = str(domain_data.get("id", ""))
                        listing_url = domain_data.get("url", f"https://dan.com/buy-domain/{domain}")
                        return _MarketplaceAttempt(
                            marketplace=Marketplace.DAN,
                            listing=MarketplaceListing(
                                marketplace=Marketplace.DAN,
                                listing_id=listing_id,
                                status=ListingStatus.ACTIVE,
                                url=listing_url,
                                price=price,
                                currency=currency,
                            ),
                        )

                    body = await resp.text()
                    error = body[:300]
                    logger.warning("Dan.com listing failed for %s: HTTP %d: %s", domain, resp.status, error)
                    return _MarketplaceAttempt(
                        marketplace=Marketplace.DAN,
                        error=error,
                    )

        except Exception as exc:
            logger.error("Dan.com API error for %s: %s", domain, exc)
            return _MarketplaceAttempt(
                marketplace=Marketplace.DAN,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # GoDaddy Aftermarket
    # ------------------------------------------------------------------

    async def _list_godaddy(
        self,
        domain: str,
        price: float,
        currency: str,
        description: str,
    ) -> _MarketplaceAttempt:
        """List domain on GoDaddy Aftermarket."""
        headers = {
            "Authorization": f"sso-key {self._gd_key}:{self._gd_secret}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        payload = {
            "domain": domain,
            "listingPrice": {
                "amount": price,
                "currency": currency,
            },
            "description": description,
            "category": "General",
            "isBuyNow": True,
            "acceptOffers": True,
        }

        try:
            async with aiohttp.ClientSession(timeout=MARKETPLACE_TIMEOUT) as session:
                async with session.post(
                    GODADDY_AFTERMARKET_URL,
                    json=payload,
                    headers=headers,
                ) as resp:
                    if resp.status in (200, 201):
                        data = await resp.json()
                        listing_id = str(data.get("listingId", ""))
                        listing_url = f"https://www.afternic.com/domain/{domain}"
                        return _MarketplaceAttempt(
                            marketplace=Marketplace.GODADDY,
                            listing=MarketplaceListing(
                                marketplace=Marketplace.GODADDY,
                                listing_id=listing_id,
                                status=ListingStatus.ACTIVE,
                                url=listing_url,
                                price=price,
                                currency=currency,
                            ),
                        )

                    body = await resp.text()
                    error = body[:300]
                    logger.warning(
                        "GoDaddy listing failed for %s: HTTP %d: %s",
                        domain, resp.status, error,
                    )
                    return _MarketplaceAttempt(
                        marketplace=Marketplace.GODADDY,
                        error=error,
                    )

        except Exception as exc:
            logger.error("GoDaddy API error for %s: %s", domain, exc)
            return _MarketplaceAttempt(
                marketplace=Marketplace.GODADDY,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Sedo
    # ------------------------------------------------------------------

    async def _list_sedo(
        self,
        domain: str,
        price: float,
        currency: str,
        description: str,
    ) -> _MarketplaceAttempt:
        """List domain on Sedo marketplace."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Sedo-Partner-Id": self._sedo_partner or "",  # type: ignore[union-attr]
            "Authorization": f"Bearer {self._sedo_key}",
        }

        payload = {
            "domain": domain,
            "price": {
                "amount": price,
                "currency": currency,
            },
            "description": description,
            "category": "domains",
            "saleType": "buy_now",
            "acceptOffers": True,
            "minimumOfferPercentage": round(self._accept_threshold * 100),
        }

        try:
            async with aiohttp.ClientSession(timeout=MARKETPLACE_TIMEOUT) as session:
                async with session.post(
                    f"{SEDO_API_URL}/listings",
                    json=payload,
                    headers=headers,
                ) as resp:
                    if resp.status in (200, 201):
                        data = await resp.json(content_type=None)
                        listing_id = str(data.get("listingId", ""))
                        listing_url = f"https://sedo.com/search/details/?partnerid=&domain={domain}"
                        return _MarketplaceAttempt(
                            marketplace=Marketplace.SEDO,
                            listing=MarketplaceListing(
                                marketplace=Marketplace.SEDO,
                                listing_id=listing_id,
                                status=ListingStatus.ACTIVE,
                                url=listing_url,
                                price=price,
                                currency=currency,
                            ),
                        )

                    body = await resp.text()
                    error = body[:300]
                    logger.warning(
                        "Sedo listing failed for %s: HTTP %d: %s",
                        domain, resp.status, error,
                    )
                    return _MarketplaceAttempt(
                        marketplace=Marketplace.SEDO,
                        error=error,
                    )

        except Exception as exc:
            logger.error("Sedo API error for %s: %s", domain, exc)
            return _MarketplaceAttempt(
                marketplace=Marketplace.SEDO,
                error=str(exc),
            )
