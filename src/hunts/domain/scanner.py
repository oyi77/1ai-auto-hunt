"""Domain Scanner — discovers high-DA expired/available domains.

Uses RDAP (Registration Data Access Protocol) to verify domain availability
and the Moz Links API to pull Domain Authority, Page Authority, and spam
score metrics.

Workflow
--------
1. Generate candidate domain names for the requested TLD (wordlist + mutations).
2. Query RDAP to filter to only *available* (unregistered) domains.
3. Query Moz to get DA/PA/spam metrics for each available domain.
4. Apply filters (``min_da``, ``max_price``, ``max_spam_score``).
5. Return the qualifying list as ``DomainResult`` dataclasses.

All HTTP I/O is async (``aiohttp``) with per-request timeouts and retries.
"""

from __future__ import annotations

import asyncio
import logging
import random
import string
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from urllib.parse import quote

import aiohttp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------

RDAP_BOOTSTRAP_URL = "https://www.iana.org/rdap/bootstrap.json"
MOZ_API_URL = "https://lsapi.seomoz.com/v2/url_metrics"
MOZ_SPAM_URL = "https://lsapi.seomoz.com/v2/spam_score"

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 1.5  # exponential back-off base in seconds
CONCURRENCY_LIMIT = 20  # max parallel outbound requests

# Common TLD → RDAP base URL overrides (avoids bootstrap lookup per call)
_RDAP_SERVERS: dict[str, str] = {
    ".com": "https://rdap.verisign.com/com/v1/domain/",
    ".net": "https://rdap.verisign.com/net/v1/domain/",
    ".org": "https://rdap.pir.org/domain/",
    ".io": "https://rdap.nic.io/domain/",
    ".co": "https://rdap.nic.co/domain/",
    ".dev": "https://rdap.nic.google/domain/",
    ".xyz": "https://rdap.nic.xyz/domain/",
    ".ai": "https://rdap.nic.ai/domain/",
    ".me": "https://rdap.nic.me/domain/",
    ".app": "https://rdap.nic.google/domain/",
}

# Word fragments used to generate candidate names
_PREFIXES = [
    "get", "try", "use", "my", "go", "the", "pro", "top", "best", "new",
    "fast", "smart", "easy", "quick", "super", "max", "ultra", "next",
    "open", "blue", "red", "gold", "sky", "sun", "star", "wave", "core",
    "hub", "zen", "nova", "flux", "bolt", "edge", "peak", "vox", "neo",
]
_SUFFIXES = [
    "ly", "io", "fy", "hub", "lab", "dev", "app", "now", "go", "up",
    "box", "kit", "bit", "net", "dot", "jet", "zen", "one", "pro", "ai",
    "hq", "co", "ify", "base", "link", "flow", "sync", "mind", "craft",
    "wave", "ping", "core", "bolt", "dash", "snap", "drop", "glow", "rise",
]


# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class DomainResult:
    """Immutable result for a single qualifying domain."""

    domain: str
    tld: str
    da: int
    pa: int
    spam_score: float
    backlinks: int
    price: Optional[float]
    registered_at: Optional[datetime] = None
    available: bool = True
    scan_duration_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "tld": self.tld,
            "da": self.da,
            "pa": self.pa,
            "spam_score": self.spam_score,
            "backlinks": self.backlinks,
            "price": self.price,
            "registered_at": self.registered_at.isoformat() if self.registered_at else None,
            "available": self.available,
            "scan_duration_ms": self.scan_duration_ms,
        }


@dataclass(slots=True)
class ScanParams:
    """Scan parameters — all filters combined."""

    tld: str = ".com"
    min_da: int = 15
    min_pa: int = 10
    max_price: Optional[float] = None
    max_spam_score: float = 5.0
    max_backlinks: int = 0  # 0 = no upper limit
    candidate_limit: int = 200  # max candidates to generate before filtering


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

class DomainScanner:
    """Discover high-DA expired/available domains for a given TLD.

    Usage::

        scanner = DomainScanner(moz_access_id="…", moz_secret_key="…")
        results = await scanner.scan(tld=".com", min_da=20, max_price=15.0)
        for r in results:
            print(r.domain, r.da, r.price)
    """

    def __init__(
        self,
        moz_access_id: Optional[str] = None,
        moz_secret_key: Optional[str] = None,
        rdap_servers: Optional[dict[str, str]] = None,
        timeout: Optional[aiohttp.ClientTimeout] = None,
        concurrency: int = CONCURRENCY_LIMIT,
    ) -> None:
        self._moz_id = moz_access_id
        self._moz_secret = moz_secret_key
        self._rdap_servers = rdap_servers or dict(_RDAP_SERVERS)
        self._timeout = timeout or DEFAULT_TIMEOUT
        self._semaphore = asyncio.Semaphore(concurrency)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def scan(
        self,
        tld: str = ".com",
        min_da: int = 15,
        max_price: Optional[float] = None,
        *,
        min_pa: int = 10,
        max_spam_score: float = 5.0,
        candidate_limit: int = 200,
    ) -> list[DomainResult]:
        """Run a full scan and return qualifying domains.

        Parameters
        ----------
        tld : str
            Top-level domain including the dot (e.g. ``".com"``).
        min_da : int
            Minimum Moz Domain Authority (0-100).
        max_price : float | None
            Maximum acquisition price in USD.  ``None`` = no price filter.
        min_pa : int
            Minimum Moz Page Authority.
        max_spam_score : float
            Maximum Moz spam score (0-17 scale).
        candidate_limit : int
            Maximum number of candidate names to generate.

        Returns
        -------
        list[DomainResult]
            Domains that pass every filter, sorted by DA descending.
        """
        tld = tld if tld.startswith(".") else f".{tld}"
        params = ScanParams(
            tld=tld,
            min_da=min_da,
            min_pa=min_pa,
            max_price=max_price,
            max_spam_score=max_spam_score,
            candidate_limit=candidate_limit,
        )

        start = time.monotonic()

        # 1. Generate candidates
        candidates = self._generate_candidates(tld, candidate_limit)
        logger.info("Generated %d candidates for TLD %s", len(candidates), tld)

        # 2. Check RDAP availability in parallel
        available = await self._filter_available(candidates)
        logger.info("%d of %d candidates are available", len(available), len(candidates))

        if not available:
            return []

        # 3. Fetch Moz metrics for available domains
        metrics = await self._fetch_moz_metrics(available)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        # 4. Apply filters and build results
        results: list[DomainResult] = []
        for name, m in metrics.items():
            da = m.get("da", 0)
            pa = m.get("pa", 0)
            spam = m.get("spam_score", 0.0)
            bl = m.get("backlinks", 0)
            price = m.get("price")

            if da < params.min_da:
                continue
            if pa < params.min_pa:
                continue
            if spam > params.max_spam_score:
                continue
            if params.max_price is not None and price is not None and price > params.max_price:
                continue

            results.append(
                DomainResult(
                    domain=name,
                    tld=tld,
                    da=da,
                    pa=pa,
                    spam_score=spam,
                    backlinks=bl,
                    price=price,
                    registered_at=m.get("registered_at"),
                    available=True,
                    scan_duration_ms=elapsed_ms,
                )
            )

        results.sort(key=lambda r: r.da, reverse=True)
        logger.info(
            "Scan complete: %d qualifying domains (elapsed=%dms)",
            len(results),
            elapsed_ms,
        )
        return results

    # ------------------------------------------------------------------
    # Candidate generation
    # ------------------------------------------------------------------

    def _generate_candidates(self, tld: str, limit: int) -> list[str]:
        """Build a list of plausible domain names for the given TLD.

        Strategy (mixed to avoid boring patterns):
        - prefix + suffix combos
        - random short words (4-7 chars)
        - keyword + number combos
        """
        seen: set[str] = set()
        candidates: list[str] = []

        # prefix + suffix
        for p in _PREFIXES:
            for s in _SUFFIXES:
                name = f"{p}{s}"
                if name not in seen:
                    seen.add(name)
                    candidates.append(f"{name}{tld}")
                if len(candidates) >= limit:
                    return candidates

        # Random short words
        rng = random.Random(42)  # deterministic seed for reproducibility
        vowels = "aeiou"
        consonants = "".join(c for c in string.ascii_lowercase if c not in vowels)
        for _ in range(limit * 2):
            length = rng.randint(4, 7)
            chars: list[str] = []
            for i in range(length):
                chars.append(rng.choice(vowels) if i % 2 else rng.choice(consonants))
            name = "".join(chars)
            if name not in seen:
                seen.add(name)
                candidates.append(f"{name}{tld}")
            if len(candidates) >= limit:
                break

        return candidates[:limit]

    # ------------------------------------------------------------------
    # RDAP availability check
    # ------------------------------------------------------------------

    async def _filter_available(self, domains: list[str]) -> list[str]:
        """Return only domains whose RDAP lookup indicates *not found*."""
        rdap_base = self._rdap_servers.get(domains[0].split(".")[-1] if domains else "")
        if not rdap_base:
            # Try to resolve via bootstrap
            rdap_base = await self._resolve_rdap_server(domains[0] if domains else "")
        if not rdap_base:
            logger.warning("No RDAP server found; returning all candidates unfiltered")
            return domains

        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            tasks = [
                self._check_rdap(session, rdap_base, d) for d in domains
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        available: list[str] = []
        for domain, result in zip(domains, results):
            if isinstance(result, Exception):
                logger.debug("RDAP error for %s: %s", domain, result)
                continue
            if result:
                available.append(domain)

        return available

    async def _check_rdap(
        self,
        session: aiohttp.ClientSession,
        rdap_base: str,
        domain: str,
    ) -> bool:
        """Return ``True`` if the domain is *not registered* (available)."""
        url = f"{rdap_base}{quote(domain)}"
        for attempt in range(MAX_RETRIES):
            try:
                async with self._semaphore:
                    async with session.get(url) as resp:
                        if resp.status == 404:
                            # Not found → domain is available
                            return True
                        if resp.status == 200:
                            # Found → domain is registered
                            return False
                        if resp.status == 429:
                            wait = RETRY_BACKOFF_BASE ** (attempt + 1)
                            await asyncio.sleep(wait)
                            continue
                        logger.debug(
                            "RDAP %s returned HTTP %d for %s",
                            rdap_base,
                            resp.status,
                            domain,
                        )
                        return False
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt == MAX_RETRIES - 1:
                    logger.debug("RDAP timeout for %s: %s", domain, exc)
                    return False
                await asyncio.sleep(RETRY_BACKOFF_BASE ** (attempt + 1))

        return False

    async def _resolve_rdap_server(self, domain: str) -> str:
        """Resolve the RDAP bootstrap server for a TLD via IANA."""
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(RDAP_BOOTSTRAP_URL) as resp:
                    if resp.status != 200:
                        return ""
                    data = await resp.json(content_type=None)
                    tld = "." + domain.rsplit(".", 1)[-1]
                    for entry in data.get("services", []):
                        tlds, urls = entry
                        if tld in tlds:
                            base = urls[0].rstrip("/")
                            if not base.endswith("/"):
                                base += "/"
                            self._rdap_servers[tld] = base
                            return base
        except Exception as exc:
            logger.warning("RDAP bootstrap failed: %s", exc)
        return ""

    # ------------------------------------------------------------------
    # Moz metrics
    # ------------------------------------------------------------------

    async def _fetch_moz_metrics(
        self, domains: list[str]
    ) -> dict[str, dict[str, object]]:
        """Fetch DA, PA, spam score, and backlink count from Moz Links API.

        Returns a dict keyed by domain name.  If the Moz API credentials are
        not configured, returns stub data so the rest of the pipeline still
        works (useful during development).
        """
        if not self._moz_id or not self._moz_secret:
            logger.warning("Moz credentials not set — returning stub metrics")
            return {d: self._stub_metrics(d) for d in domains}

        results: dict[str, dict[str, object]] = {}

        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            # Moz API accepts batch requests (up to 200 URLs)
            batch_size = 100
            for i in range(0, len(domains), batch_size):
                batch = domains[i : i + batch_size]
                batch_results = await self._moz_batch(session, batch)
                results.update(batch_results)

        return results

    async def _moz_batch(
        self,
        session: aiohttp.ClientSession,
        domains: list[str],
    ) -> dict[str, dict[str, object]]:
        """Fetch metrics for a batch of domains."""
        payload = {"targets": [{"target": d, "scope": "domain"} for d in domains]}
        auth = aiohttp.BasicAuth(self._moz_id, self._moz_secret)  # type: ignore[arg-type]

        for attempt in range(MAX_RETRIES):
            try:
                async with session.post(
                    MOZ_API_URL,
                    json=payload,
                    auth=auth,
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    if resp.status == 429:
                        wait = RETRY_BACKOFF_BASE ** (attempt + 1)
                        logger.warning("Moz rate-limited; retrying in %.1fs", wait)
                        await asyncio.sleep(wait)
                        continue

                    if resp.status != 200:
                        body = await resp.text()
                        logger.error("Moz API HTTP %d: %s", resp.status, body[:500])
                        return {d: self._stub_metrics(d) for d in domains}

                    data = await resp.json()
                    break

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt == MAX_RETRIES - 1:
                    logger.error("Moz batch failed after %d retries: %s", MAX_RETRIES, exc)
                    return {d: self._stub_metrics(d) for d in domains}
                await asyncio.sleep(RETRY_BACKOFF_BASE ** (attempt + 1))
        else:
            return {d: self._stub_metrics(d) for d in domains}

        # Parse results
        out: dict[str, dict[str, object]] = {}
        for entry in data.get("results", []):
            target = entry.get("target", "")
            out[target] = {
                "da": entry.get("domain_authority", 0),
                "pa": entry.get("page_authority", 0),
                "spam_score": entry.get("spam_score", 0),
                "backlinks": entry.get("external_pages", 0),
                "price": None,
                "registered_at": None,
            }
        return out

    @staticmethod
    def _stub_metrics(domain: str) -> dict[str, object]:
        """Return deterministic stub metrics for development/testing."""
        h = sum(ord(c) for c in domain)
        return {
            "da": h % 60 + 5,
            "pa": h % 50 + 3,
            "spam_score": (h % 8) + 1.0,
            "backlinks": (h % 5000) + 10,
            "price": None,
            "registered_at": None,
        }
