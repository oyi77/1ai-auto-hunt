"""Domain Sniper — multi-registrar race for instant domain acquisition.

Fires registration attempts at multiple registrars concurrently via
``asyncio.gather``.  The first registrar to return HTTP 200 wins; all
other in-flight requests are automatically cancelled.

Supported registrars:
- **Namecheap** (XML API)
- **GoDaddy** (REST API v1)
- **Porkbun** (REST API v3)

Usage::

    sniper = DomainSniper(
        namecheap_key="…", namecheap_user="…",
        godaddy_key="…", godaddy_secret="…",
        porkbun_key="…", porkbun_secret="…",
    )
    result = await sniper.snipe("example.com")
    print(result.success, result.registrar)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NAMECHEAP_API_URL = "https://api.namecheap.com/xml.response"
GODADDY_API_URL = "https://api.godaddy.com/v1/domains/purchase"
PORKBUN_API_URL = "https://porkbun.com/api/json/v3/domain/buy"

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=45)
REGISTRAR_TIMEOUT = aiohttp.ClientTimeout(total=30)


# ---------------------------------------------------------------------------
# Enums & data classes
# ---------------------------------------------------------------------------

class Registrar(str, Enum):
    """Supported domain registrars."""

    NAMECHEAP = "namecheap"
    GODADDY = "godaddy"
    PORKBUN = "porkbun"


class SnipeStatus(str, Enum):
    """Outcome of a snipe attempt."""

    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    ALREADY_OWNED = "already_owned"
    RATE_LIMITED = "rate_limited"
    INSUFFICIENT_FUNDS = "insufficient_funds"


@dataclass(frozen=True, slots=True)
class SnipeResult:
    """Immutable result of a domain snipe attempt."""

    domain: str
    success: bool
    status: SnipeStatus
    registrar: Optional[Registrar]
    order_id: Optional[str]
    price: Optional[float]
    currency: str
    elapsed_ms: int
    attempted_at: datetime
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "success": self.success,
            "status": self.status.value,
            "registrar": self.registrar.value if self.registrar else None,
            "order_id": self.order_id,
            "price": self.price,
            "currency": self.currency,
            "elapsed_ms": self.elapsed_ms,
            "attempted_at": self.attempted_at.isoformat() if self.attempted_at else None,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class RegistrarAttempt:
    """Result from a single registrar attempt (used internally)."""

    registrar: Registrar
    success: bool
    order_id: Optional[str] = None
    price: Optional[float] = None
    currency: str = "USD"
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Sniper
# ---------------------------------------------------------------------------

class DomainSniper:
    """Multi-registrar domain sniper using asyncio.gather.

    Fires concurrent registration requests to Namecheap, GoDaddy, and
    Porkbun.  The first successful response (HTTP 200 with order confirmation)
    wins — all other pending requests are cancelled automatically.

    Parameters
    ----------
    namecheap_key : str | None
        Namecheap API key.
    namecheap_user : str | None
        Namecheap username.
    namecheap_ip : str
        Whitelisted IP for Namecheap API (required by their API).
    godaddy_key : str | None
        GoDaddy API key (Shopper-level or Production).
    godaddy_secret : str | None
        GoDaddy API secret.
    porkbun_key : str | None
        Porkbun API key.
    porkbun_secret : str | None
        Porkbun API secret.
    nameservers : list[str]
        Custom nameservers to set on registration.  Defaults to registrar defaults.
    """

    def __init__(
        self,
        namecheap_key: Optional[str] = None,
        namecheap_user: Optional[str] = None,
        namecheap_ip: str = "127.0.0.1",
        godaddy_key: Optional[str] = None,
        godaddy_secret: Optional[str] = None,
        porkbun_key: Optional[str] = None,
        porkbun_secret: Optional[str] = None,
        nameservers: Optional[list[str]] = None,
        timeout: Optional[aiohttp.ClientTimeout] = None,
    ) -> None:
        self._nc_key = namecheap_key
        self._nc_user = namecheap_user
        self._nc_ip = namecheap_ip
        self._gd_key = godaddy_key
        self._gd_secret = godaddy_secret
        self._pb_key = porkbun_key
        self._pb_secret = porkbun_secret
        self._nameservers = nameservers
        self._timeout = timeout or DEFAULT_TIMEOUT

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def snipe(self, domain: str) -> SnipeResult:
        """Register *domain* via the fastest available registrar.

        Launches all configured registrars concurrently.  The first one to
        return a successful registration wins; remaining tasks are cancelled.

        Returns
        -------
        SnipeResult
            Always returns a result (never raises).  Check ``.success``.
        """
        domain = domain.lower().strip()
        start = time.monotonic()

        # Build tasks for each configured registrar
        tasks: list[asyncio.Task[RegistrarAttempt]] = []
        registrar_map: dict[asyncio.Task[RegistrarAttempt], Registrar] = {}

        if self._nc_key and self._nc_user:
            t = asyncio.create_task(self._snipe_namecheap(domain))
            tasks.append(t)
            registrar_map[t] = Registrar.NAMECHEAP

        if self._gd_key and self._gd_secret:
            t = asyncio.create_task(self._snipe_godaddy(domain))
            tasks.append(t)
            registrar_map[t] = Registrar.GODADDY

        if self._pb_key and self._pb_secret:
            t = asyncio.create_task(self._snipe_porkbun(domain))
            tasks.append(t)
            registrar_map[t] = Registrar.PORKBUN

        if not tasks:
            logger.error("No registrars configured — cannot snipe %s", domain)
            return SnipeResult(
                domain=domain,
                success=False,
                status=SnipeStatus.FAILED,
                registrar=None,
                order_id=None,
                price=None,
                currency="USD",
                elapsed_ms=0,
                attempted_at=datetime.now(timezone.utc),
                error="No registrars configured",
            )

        logger.info(
            "Sniping %s via %d registrars: %s",
            domain,
            len(tasks),
            [registrar_map[t].value for t in tasks],
        )

        # Wait for the first successful result
        winner: Optional[RegistrarAttempt] = None
        winner_registrar: Optional[Registrar] = None
        errors: list[str] = []

        try:
            for coro in asyncio.as_completed(tasks):
                attempt = await coro
                registrar = registrar_map.get(tasks[0])  # will be corrected below
                # Find the registrar for this task
                for t in tasks:
                    if t.done() and not t.cancelled():
                        try:
                            result = t.result()
                            if result is attempt:
                                registrar = registrar_map[t]
                                break
                        except Exception:
                            pass

                if attempt.success:
                    winner = attempt
                    winner_registrar = registrar
                    # Cancel all remaining tasks
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    break
                else:
                    errors.append(f"{registrar_map.get(tasks[0], '?')}: {attempt.error}")

        except Exception as exc:
            logger.error("Snipe gather failed for %s: %s", domain, exc)
            errors.append(str(exc))

        elapsed_ms = int((time.monotonic() - start) * 1000)

        # If we have a winner, try to cancel registrations at other registrars
        # that may have also succeeded (race condition)
        if winner and winner.success:
            await self._cancel_others(domain, winner_registrar)

        if winner and winner.success:
            logger.info(
                "SNIPED %s via %s in %dms (order=%s, price=%s)",
                domain,
                winner_registrar.value if winner_registrar else "?",
                elapsed_ms,
                winner.order_id,
                winner.price,
            )
            return SnipeResult(
                domain=domain,
                success=True,
                status=SnipeStatus.SUCCESS,
                registrar=winner_registrar,
                order_id=winner.order_id,
                price=winner.price,
                currency=winner.currency,
                elapsed_ms=elapsed_ms,
                attempted_at=datetime.now(timezone.utc),
            )

        # All registrars failed
        logger.warning("Failed to snipe %s after %dms: %s", domain, elapsed_ms, errors)
        return SnipeResult(
            domain=domain,
            success=False,
            status=SnipeStatus.FAILED,
            registrar=None,
            order_id=None,
            price=None,
            currency="USD",
            elapsed_ms=elapsed_ms,
            attempted_at=datetime.now(timezone.utc),
            error="; ".join(errors) if errors else "All registrars failed",
        )

    # ------------------------------------------------------------------
    # Namecheap
    # ------------------------------------------------------------------

    async def _snipe_namecheap(self, domain: str) -> RegistrarAttempt:
        """Register domain via Namecheap XML API."""
        sld, tld = _split_domain(domain)

        params = {
            "ApiUser": self._nc_user,
            "ApiKey": self._nc_key,
            "UserName": self._nc_user,
            "ClientIp": self._nc_ip,
            "Command": "namecheap.domains.create",
            "DomainName": domain,
            "Years": "1",
            "AuxBillingFirstName": "Auto",
            "AuxBillingLastName": "Hunt",
            "AuxBillingAddress1": "123 Main St",
            "AuxBillingCity": "San Francisco",
            "AuxBillingStateProvince": "CA",
            "AuxBillingPostalCode": "94102",
            "AuxBillingCountry": "US",
            "AuxBillingPhone": "+1.4155551234",
            "AuxBillingEmailAddress": "hunt@1ai.dev",
            "TechBillingFirstName": "Auto",
            "TechBillingLastName": "Hunt",
            "TechBillingAddress1": "123 Main St",
            "TechBillingCity": "San Francisco",
            "TechBillingStateProvince": "CA",
            "TechBillingPostalCode": "94102",
            "TechBillingCountry": "US",
            "TechBillingPhone": "+1.4155551234",
            "TechBillingEmailAddress": "hunt@1ai.dev",
        }

        if self._nameservers:
            for i, ns in enumerate(self._nameservers[:4], 1):
                params[f"Nameserver{i}"] = ns

        try:
            async with aiohttp.ClientSession(timeout=REGISTRAR_TIMEOUT) as session:
                async with session.post(NAMECHEAP_API_URL, data=params) as resp:
                    text = await resp.text()
                    if resp.status == 200 and "<IsSuccess>true</IsSuccess>" in text:
                        # Extract order ID from XML
                        order_id = _extract_xml_value(text, "OrderID")
                        price_str = _extract_xml_value(text, "EstimatedAmount")
                        price = float(price_str) if price_str else None
                        return RegistrarAttempt(
                            registrar=Registrar.NAMECHEAP,
                            success=True,
                            order_id=order_id,
                            price=price,
                        )
                    error = _extract_xml_value(text, "Errors") or text[:300]
                    return RegistrarAttempt(
                        registrar=Registrar.NAMECHEAP,
                        success=False,
                        error=error,
                    )
        except asyncio.CancelledError:
            return RegistrarAttempt(
                registrar=Registrar.NAMECHEAP,
                success=False,
                error="Cancelled",
            )
        except Exception as exc:
            return RegistrarAttempt(
                registrar=Registrar.NAMECHEAP,
                success=False,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # GoDaddy
    # ------------------------------------------------------------------

    async def _snipe_godaddy(self, domain: str) -> RegistrarAttempt:
        """Register domain via GoDaddy REST API."""
        headers = {
            "Authorization": f"sso-key {self._gd_key}:{self._gd_secret}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        payload: dict = {
            "domain": domain,
            "period": 1,
            "renewAuto": True,
            "privacy": True,
            "consent": {
                "agreedAt": datetime.now(timezone.utc).isoformat(),
                "agreedBy": "127.0.0.1",
                "agreementKeys": "DNRA",
            },
        }

        if self._nameservers:
            payload["nameServers"] = self._nameservers

        try:
            async with aiohttp.ClientSession(timeout=REGISTRAR_TIMEOUT) as session:
                async with session.post(
                    GODADDY_API_URL, json=payload, headers=headers
                ) as resp:
                    if resp.status in (200, 201):
                        data = await resp.json()
                        return RegistrarAttempt(
                            registrar=Registrar.GODADDY,
                            success=True,
                            order_id=str(data.get("orderId", "")),
                            price=data.get("price"),
                            currency=data.get("currency", "USD"),
                        )

                    body = await resp.text()
                    error_msg = body[:300]

                    if resp.status == 422:
                        return RegistrarAttempt(
                            registrar=Registrar.GODADDY,
                            success=False,
                            error=f"Domain unavailable: {error_msg}",
                        )
                    if resp.status == 402:
                        return RegistrarAttempt(
                            registrar=Registrar.GODADDY,
                            success=False,
                            error="Insufficient funds",
                        )

                    return RegistrarAttempt(
                        registrar=Registrar.GODADDY,
                        success=False,
                        error=error_msg,
                    )
        except asyncio.CancelledError:
            return RegistrarAttempt(
                registrar=Registrar.GODADDY,
                success=False,
                error="Cancelled",
            )
        except Exception as exc:
            return RegistrarAttempt(
                registrar=Registrar.GODADDY,
                success=False,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Porkbun
    # ------------------------------------------------------------------

    async def _snipe_porkbun(self, domain: str) -> RegistrarAttempt:
        """Register domain via Porkbun API v3."""
        payload: dict = {
            "apikey": self._pb_key,
            "secretapikey": self._pb_secret,
        }

        if self._nameservers:
            payload["nameservers"] = self._nameservers

        try:
            async with aiohttp.ClientSession(timeout=REGISTRAR_TIMEOUT) as session:
                async with session.post(
                    f"{PORKBUN_API_URL}/{domain}",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    data = await resp.json(content_type=None)

                    if resp.status == 200 and data.get("status") == "SUCCESS":
                        return RegistrarAttempt(
                            registrar=Registrar.PORKBUN,
                            success=True,
                            order_id=str(data.get("order", "")),
                            price=data.get("price"),
                            currency=data.get("currency", "USD"),
                        )

                    error = data.get("message", str(data)[:300])
                    return RegistrarAttempt(
                        registrar=Registrar.PORKBUN,
                        success=False,
                        error=error,
                    )
        except asyncio.CancelledError:
            return RegistrarAttempt(
                registrar=Registrar.PORKBUN,
                success=False,
                error="Cancelled",
            )
        except Exception as exc:
            return RegistrarAttempt(
                registrar=Registrar.PORKBUN,
                success=False,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    async def _cancel_others(
        self,
        domain: str,
        winner: Optional[Registrar],
    ) -> None:
        """Attempt to cancel registrations at non-winning registrars.

        In a race condition, multiple registrars may succeed.  This method
        sends cancel/delete requests to the losers.  Best-effort — errors
        are logged but not raised.
        """
        if not winner:
            return

        cancel_tasks: list[asyncio.Task[None]] = []

        if winner != Registrar.NAMECHEAP and self._nc_key:
            cancel_tasks.append(
                asyncio.create_task(self._cancel_namecheap(domain))
            )
        if winner != Registrar.GODADDY and self._gd_key:
            cancel_tasks.append(
                asyncio.create_task(self._cancel_godaddy(domain))
            )
        if winner != Registrar.PORKBUN and self._pb_key:
            cancel_tasks.append(
                asyncio.create_task(self._cancel_porkbun(domain))
            )

        if cancel_tasks:
            await asyncio.gather(*cancel_tasks, return_exceptions=True)

    async def _cancel_namecheap(self, domain: str) -> None:
        """Best-effort Namecheap cancellation."""
        try:
            params = {
                "ApiUser": self._nc_user,
                "ApiKey": self._nc_key,
                "UserName": self._nc_user,
                "ClientIp": self._nc_ip,
                "Command": "namecheap.domains.dns.setCustom",
                "SLD": domain.split(".")[0],
                "TLD": ".".join(domain.split(".")[1:]),
                "Nameservers": "ns1.parked.com",
            }
            async with aiohttp.ClientSession(timeout=REGISTRAR_TIMEOUT) as session:
                async with session.post(NAMECHEAP_API_URL, data=params):
                    pass
        except Exception as exc:
            logger.debug("Namecheap cancel for %s failed: %s", domain, exc)

    async def _cancel_godaddy(self, domain: str) -> None:
        """Best-effort GoDaddy cancellation (refund request)."""
        try:
            headers = {
                "Authorization": f"sso-key {self._gd_key}:{self._gd_secret}",
                "Accept": "application/json",
            }
            async with aiohttp.ClientSession(timeout=REGISTRAR_TIMEOUT) as session:
                async with session.delete(
                    f"https://api.godaddy.com/v1/domains/{domain}",
                    headers=headers,
                ):
                    pass
        except Exception as exc:
            logger.debug("GoDaddy cancel for %s failed: %s", domain, exc)

    async def _cancel_porkbun(self, domain: str) -> None:
        """Best-effort Porkbun cancellation."""
        try:
            payload = {
                "apikey": self._pb_key,
                "secretapikey": self._pb_secret,
            }
            async with aiohttp.ClientSession(timeout=REGISTRAR_TIMEOUT) as session:
                async with session.post(
                    f"https://porkbun.com/api/json/v3/domain/delete/{domain}",
                    json=payload,
                ):
                    pass
        except Exception as exc:
            logger.debug("Porkbun cancel for %s failed: %s", domain, exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _split_domain(domain: str) -> tuple[str, str]:
    """Split ``example.com`` into ``("example", "com")``."""
    parts = domain.split(".", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return domain, ""


def _extract_xml_value(xml: str, tag: str) -> Optional[str]:
    """Naive XML value extraction for simple tags."""
    start = xml.find(f"<{tag}>")
    if start == -1:
        return None
    start += len(tag) + 2
    end = xml.find(f"</{tag}>", start)
    if end == -1:
        return None
    return xml[start:end]
