"""Domain Hunter — expired domain scanner, vetting, sniper, and flipper.

Submodules
----------
models   : SQLAlchemy ORM models (Domain, DomainScan).
scanner  : DomainScanner — discover high-DA expired/available domains.
vet      : DomainVet — pre-acquisition due diligence (backlinks, history, trademarks).
sniper   : DomainSniper — multi-registrar race for instant acquisition.
flip     : DomainFlip — list on Dan.com + GoDaddy Aftermarket + Sedo with auto-negotiation.

Quick start::

    from src.hunts.domain import DomainScanner, DomainVet, DomainSniper, DomainFlip

    scanner = DomainScanner(moz_access_id="…", moz_secret_key="…")
    results = await scanner.scan(tld=".com", min_da=20, max_price=15.0)

    vet = DomainVet(moz_access_id="…", moz_secret_key="…")
    report = await vet.vet("example.com")

    sniper = DomainSniper(namecheap_key="…", godaddy_key="…", porkbun_key="…")
    result = await sniper.snipe("example.com")

    flip = DomainFlip(dan_api_token="…", godaddy_key="…", sedo_partner_id="…")
    listing = await flip.list_for_sale("example.com", price=500.0)
"""

from __future__ import annotations

from src.hunts.domain.flip import (
    DomainFlip,
    FlipResult,
    Marketplace,
    MarketplaceListing,
    OfferAction,
    OfferResult,
)
from src.hunts.domain.models import Base, Domain, DomainScan, DomainStatus
from src.hunts.domain.scanner import DomainResult, DomainScanner, ScanParams
from src.hunts.domain.sniper import DomainSniper, Registrar, SnipeResult, SnipeStatus
from src.hunts.domain.vet import DomainVet, SubScore, Verdict, VetReport

__all__ = [
    # Models
    "Base",
    "Domain",
    "DomainScan",
    "DomainStatus",
    # Scanner
    "DomainScanner",
    "DomainResult",
    "ScanParams",
    # Vet
    "DomainVet",
    "VetReport",
    "SubScore",
    "Verdict",
    # Sniper
    "DomainSniper",
    "SnipeResult",
    "SnipeStatus",
    "Registrar",
    # Flip
    "DomainFlip",
    "FlipResult",
    "MarketplaceListing",
    "Marketplace",
    "OfferAction",
    "OfferResult",
]
