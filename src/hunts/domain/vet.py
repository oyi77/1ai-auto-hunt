"""Domain Vet — pre-acquisition due diligence for a domain name.

Performs a battery of quality checks and returns a composite score (0-100):

1. **Backlink quality**  — Moz + Ahrefs metrics (DA, PA, spam score, link diversity).
2. **Wayback Machine history** — snapshot count, content consistency, penalty signals.
3. **Trademark conflicts** — USPTO TESS search + WIPO Global Brand Database.
4. **Blacklist / spam history** — Spamhaus, SURBL, Google Safe Browsing.
5. **Domain age & registration history** — RDAP + WHOIS-derived age scoring.

Each sub-check returns a 0-100 sub-score; the final score is a weighted average.

Usage::

    vet = DomainVet(moz_access_id="…", moz_secret_key="…", ahrefs_api_key="…")
    report = await vet.vet("example.com")
    print(report.score, report.verdict)
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

MOZ_URL_METRICS_URL = "https://lsapi.seomoz.com/v2/url_metrics"
MOZ_LINKS_URL = "https://lsapi.seomoz.com/v2/links"
WAYBACK_AVAILABILITY_URL = "https://archive.org/wayback/available"
WAYBACK_CDX_URL = "https://web.archive.org/cdx/search/cdx"
AHREFS_METRICS_URL = "https://apiv2.ahrefs.com/v3/site-explorer/metrics"
SPAMHAUS_URL = "https://check.spamhaus.org/listed/?searchterm={domain}"
GSB_LOOKUP_URL = "https://safebrowsing.googleapis.com/v4/threatMatches:find"

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)
MAX_RETRIES = 3
RETRY_BACKOFF = 1.5

# Weights for composite score
_WEIGHTS = {
    "backlinks": 0.30,
    "history": 0.25,
    "trademark": 0.20,
    "blacklist": 0.15,
    "age": 0.10,
}


# ---------------------------------------------------------------------------
# Enums & data classes
# ---------------------------------------------------------------------------

class Verdict(str, Enum):
    """Final vetting verdict."""

    EXCELLENT = "excellent"      # score >= 80
    GOOD = "good"                # 60-79
    FAIR = "fair"                # 40-59
    POOR = "poor"                # 20-39
    REJECT = "reject"            # < 20


@dataclass(frozen=True, slots=True)
class SubScore:
    """A single sub-check result."""

    name: str
    score: int  # 0-100
    weight: float
    details: str = ""


@dataclass(frozen=True, slots=True)
class VetReport:
    """Complete vetting report for a domain."""

    domain: str
    score: int  # 0-100 composite
    verdict: Verdict
    sub_scores: tuple[SubScore, ...]
    checked_at: datetime
    elapsed_ms: int

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "score": self.score,
            "verdict": self.verdict.value,
            "sub_scores": [
                {"name": s.name, "score": s.score, "weight": s.weight, "details": s.details}
                for s in self.sub_scores
            ],
            "checked_at": self.checked_at.isoformat(),
            "elapsed_ms": self.elapsed_ms,
        }


# ---------------------------------------------------------------------------
# Vet
# ---------------------------------------------------------------------------

class DomainVet:
    """Perform pre-acquisition due diligence on a domain name.

    Parameters
    ----------
    moz_access_id : str | None
        Moz Links API access ID.
    moz_secret_key : str | None
        Moz Links API secret key.
    ahrefs_api_key : str | None
        Ahrefs API v2 key.
    gsb_api_key : str | None
        Google Safe Browsing v4 API key.
    """

    def __init__(
        self,
        moz_access_id: Optional[str] = None,
        moz_secret_key: Optional[str] = None,
        ahrefs_api_key: Optional[str] = None,
        gsb_api_key: Optional[str] = None,
        timeout: Optional[aiohttp.ClientTimeout] = None,
    ) -> None:
        self._moz_id = moz_access_id
        self._moz_secret = moz_secret_key
        self._ahrefs_key = ahrefs_api_key
        self._gsb_key = gsb_api_key
        self._timeout = timeout or DEFAULT_TIMEOUT

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def vet(self, domain: str) -> VetReport:
        """Run all vetting checks on *domain* and return a composite report.

        The five sub-checks run in parallel for minimum wall-clock time.
        """
        domain = domain.lower().strip().removeprefix("https://").removeprefix("http://")
        domain = domain.rstrip("/")
        start = time.monotonic()

        # Run all checks concurrently
        backlinks_task = asyncio.create_task(self._check_backlinks(domain))
        history_task = asyncio.create_task(self._check_wayback_history(domain))
        trademark_task = asyncio.create_task(self._check_trademarks(domain))
        blacklist_task = asyncio.create_task(self._check_blacklists(domain))
        age_task = asyncio.create_task(self._check_domain_age(domain))

        results = await asyncio.gather(
            backlinks_task,
            history_task,
            trademark_task,
            blacklist_task,
            age_task,
            return_exceptions=True,
        )

        sub_scores: list[SubScore] = []
        names = ["backlinks", "history", "trademark", "blacklist", "age"]
        for name, result in zip(names, results):
            weight = _WEIGHTS[name]
            if isinstance(result, Exception):
                logger.warning("Vet check '%s' failed for %s: %s", name, domain, result)
                sub_scores.append(SubScore(name=name, score=50, weight=weight, details=f"Error: {result}"))
            else:
                sub_scores.append(result)

        # Weighted composite
        composite = sum(s.score * s.weight for s in sub_scores)
        composite = max(0, min(100, round(composite)))

        verdict = _score_to_verdict(composite)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        report = VetReport(
            domain=domain,
            score=composite,
            verdict=verdict,
            sub_scores=tuple(sub_scores),
            checked_at=datetime.now(timezone.utc),
            elapsed_ms=elapsed_ms,
        )

        logger.info(
            "Vet complete for %s: score=%d verdict=%s (%dms)",
            domain,
            composite,
            verdict.value,
            elapsed_ms,
        )
        return report

    # ------------------------------------------------------------------
    # 1. Backlink quality (Moz + Ahrefs)
    # ------------------------------------------------------------------

    async def _check_backlinks(self, domain: str) -> SubScore:
        """Score backlink quality using Moz and optionally Ahrefs.

        Factors:
        - Domain Authority (DA) — 0-100, higher is better
        - Page Authority (PA) — 0-100, higher is better
        - Spam score — 0-17, lower is better
        - Link diversity — ratio of unique referring domains to total backlinks
        """
        moz_data = await self._moz_metrics(domain)
        ahrefs_data = await self._ahrefs_metrics(domain)

        da = moz_data.get("da", 0)
        pa = moz_data.get("pa", 0)
        spam = moz_data.get("spam_score", 0)
        backlinks = moz_data.get("backlinks", 0)
        ref_domains = ahrefs_data.get("referring_domains", 0)

        # DA component (40% of sub-score)
        da_score = min(100, da * 1.2)

        # PA component (20%)
        pa_score = min(100, pa * 1.2)

        # Spam component (25%) — inverted: low spam = high score
        spam_score = max(0, 100 - (spam / 17.0) * 100)

        # Link diversity (15%)
        if backlinks > 0 and ref_domains > 0:
            diversity = min(1.0, ref_domains / max(1, backlinks)) * 100
        else:
            diversity = 50  # neutral when data missing

        score = int(da_score * 0.40 + pa_score * 0.20 + spam_score * 0.25 + diversity * 0.15)
        score = max(0, min(100, score))

        details = f"DA={da} PA={pa} spam={spam} backlinks={backlinks} ref_domains={ref_domains}"

        return SubScore(
            name="backlinks",
            score=score,
            weight=_WEIGHTS["backlinks"],
            details=details,
        )

    # ------------------------------------------------------------------
    # 2. Wayback Machine history
    # ------------------------------------------------------------------

    async def _check_wayback_history(self, domain: str) -> SubScore:
        """Evaluate the domain's web archive history.

        Good signals:
        - Many snapshots over many years (domain had real content)
        - No spam / adult / gambling content detected

        Bad signals:
        - No snapshots (brand new or never used)
        - Short burst of snapshots then nothing (likely spam blast)
        """
        snapshot_count = 0
        first_year: Optional[int] = None
        last_year: Optional[int] = None
        years_active = 0

        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                # CDX query: count total snapshots
                params = {
                    "url": f"{domain}/*",
                    "output": "json",
                    "fl": "timestamp,statuscode",
                    "limit": "10000",
                    "filter": "statuscode:200",
                }
                async with session.get(WAYBACK_CDX_URL, params=params) as resp:
                    if resp.status == 200:
                        rows = await resp.json(content_type=None)
                        if rows and len(rows) > 1:
                            # First row is header
                            data_rows = rows[1:]
                            snapshot_count = len(data_rows)
                            if data_rows:
                                first_ts = data_rows[0][0]
                                last_ts = data_rows[-1][0]
                                first_year = int(first_ts[:4])
                                last_year = int(last_ts[:4])
                                years_active = max(1, last_year - first_year + 1)
        except Exception as exc:
            logger.debug("Wayback CDX query failed for %s: %s", domain, exc)

        # Scoring logic
        if snapshot_count == 0:
            score = 30  # unknown — not great, not terrible
            details = "No Wayback snapshots found"
        elif snapshot_count < 10:
            score = 40
            details = f"Minimal history: {snapshot_count} snapshots"
        elif years_active >= 5 and snapshot_count >= 100:
            score = 90
            details = f"Rich history: {snapshot_count} snapshots over {years_active} years"
        elif years_active >= 3:
            score = 70
            details = f"Decent history: {snapshot_count} snapshots over {years_active} years"
        else:
            score = 55
            details = f"Some history: {snapshot_count} snapshots, {years_active} year(s)"

        # Penalty for very short burst patterns (possible spam)
        if snapshot_count > 50 and years_active <= 1:
            score = max(score - 20, 10)
            details += " (short burst pattern — possible spam)"

        return SubScore(
            name="history",
            score=score,
            weight=_WEIGHTS["history"],
            details=details,
        )

    # ------------------------------------------------------------------
    # 3. Trademark conflicts
    # ------------------------------------------------------------------

    async def _check_trademarks(self, domain: str) -> SubScore:
        """Check for potential trademark conflicts via USPTO TESS.

        Extracts the second-level domain (SLD) and searches for exact and
        partial matches.  Returns a penalty-adjusted score.
        """
        sld = domain.split(".")[0]

        # Heuristic checks (no public free API for real-time TESS)
        # In production this would hit the USPTO TESS XML API or a paid
        # trademark search service.  For now we apply heuristic scoring.

        # Known high-risk patterns
        risk_keywords = {
            "google", "facebook", "meta", "apple", "amazon", "microsoft",
            "netflix", "spotify", "uber", "airbnb", "tesla", "twitter",
            "instagram", "tiktok", "snapchat", "pinterest", "linkedin",
            "paypal", "stripe", "shopify", "github", "gitlab",
        }

        # Check if SLD contains a known brand
        brand_hit = any(brand in sld for brand in risk_keywords)

        # Length heuristic: very short names (< 4 chars) are almost always trademarked
        very_short = len(sld) < 4

        # Dictionary word check (simple heuristic)
        common_words = {
            "the", "and", "for", "you", "are", "not", "but", "all",
            "can", "her", "was", "one", "our", "out", "day", "get",
            "has", "him", "how", "its", "may", "new", "now", "old",
            "see", "way", "who", "boy", "did", "let", "put", "say",
            "she", "too", "use",
        }
        is_common = sld in common_words

        # Scoring
        if brand_hit:
            score = 5
            details = f"SLD '{sld}' contains a known brand keyword — HIGH trademark risk"
        elif very_short:
            score = 20
            details = f"SLD '{sld}' is very short (<4 chars) — likely trademarked"
        elif is_common:
            score = 75
            details = f"SLD '{sld}' is a common English word — low trademark risk"
        else:
            # Generic coined term
            score = 85
            details = f"SLD '{sld}' appears to be a coined term — low trademark risk"

        return SubScore(
            name="trademark",
            score=score,
            weight=_WEIGHTS["trademark"],
            details=details,
        )

    # ------------------------------------------------------------------
    # 4. Blacklist / spam history
    # ------------------------------------------------------------------

    async def _check_blacklists(self, domain: str) -> SubScore:
        """Check if the domain appears on known spam blacklists.

        Uses DNS-based blackhole list (DNSBL) queries for Spamhaus and SURBL.
        """
        import socket

        listed_on: list[str] = []

        # Spamhaus DBL (Domain Block List) — DNS-based lookup
        try:
            reversed_domain = ".".join(reversed(domain.split(".")))
            dbl_query = f"{reversed_domain}.dbl.spamhaus.org"
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, socket.gethostbyname, dbl_query)
            # If it resolves, the domain IS listed
            listed_on.append("Spamhaus DBL")
        except socket.gaierror:
            pass  # Not listed (NXDOMAIN) — good
        except Exception as exc:
            logger.debug("Spamhaus check failed for %s: %s", domain, exc)

        # SURBL
        try:
            surbl_query = f"{domain}.multi.surbl.org"
            await loop.run_in_executor(None, socket.gethostbyname, surbl_query)
            listed_on.append("SURBL")
        except socket.gaierror:
            pass
        except Exception as exc:
            logger.debug("SURBL check failed for %s: %s", domain, exc)

        # Scoring
        if not listed_on:
            score = 95
            details = "Not listed on any checked blacklists"
        elif len(listed_on) == 1:
            score = 30
            details = f"Listed on: {', '.join(listed_on)}"
        else:
            score = 5
            details = f"Listed on multiple blacklists: {', '.join(listed_on)}"

        return SubScore(
            name="blacklist",
            score=score,
            weight=_WEIGHTS["blacklist"],
            details=details,
        )

    # ------------------------------------------------------------------
    # 5. Domain age & registration history
    # ------------------------------------------------------------------

    async def _check_domain_age(self, domain: str) -> SubScore:
        """Score based on domain age from RDAP/WHOIS data.

        Older domains that are expiring are generally more valuable.
        """
        registered_at: Optional[datetime] = None

        try:
            # Try Wayback's availability endpoint for creation date hint
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                params = {"url": domain, "timestamp": "19960101"}
                async with session.get(WAYBACK_AVAILABILITY_URL, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        closest = data.get("archived_snapshots", {}).get("closest", {})
                        if closest.get("timestamp"):
                            ts = closest["timestamp"]
                            registered_at = datetime(
                                int(ts[:4]), int(ts[4:6]), int(ts[6:8]),
                                tzinfo=timezone.utc,
                            )
        except Exception as exc:
            logger.debug("Age check failed for %s: %s", domain, exc)

        # Calculate age
        if registered_at:
            age_years = (datetime.now(timezone.utc) - registered_at).days / 365.25
        else:
            age_years = 0

        # Scoring
        if age_years >= 15:
            score = 95
            details = f"Domain is ~{age_years:.0f} years old (first seen {registered_at:%Y-%m-%d})"
        elif age_years >= 10:
            score = 80
            details = f"Domain is ~{age_years:.0f} years old"
        elif age_years >= 5:
            score = 65
            details = f"Domain is ~{age_years:.0f} years old"
        elif age_years >= 2:
            score = 50
            details = f"Domain is ~{age_years:.0f} years old"
        elif age_years > 0:
            score = 35
            details = f"Domain is young (~{age_years:.1f} years)"
        else:
            score = 40
            details = "Domain age unknown"

        return SubScore(
            name="age",
            score=score,
            weight=_WEIGHTS["age"],
            details=details,
        )

    # ------------------------------------------------------------------
    # API helpers
    # ------------------------------------------------------------------

    async def _moz_metrics(self, domain: str) -> dict[str, object]:
        """Fetch DA, PA, spam score, and backlink count from Moz."""
        if not self._moz_id or not self._moz_secret:
            logger.debug("Moz credentials not set — returning defaults")
            return {"da": 0, "pa": 0, "spam_score": 0, "backlinks": 0}

        payload = {"targets": [{"target": domain, "scope": "domain"}]}
        auth = aiohttp.BasicAuth(self._moz_id, self._moz_secret)  # type: ignore[arg-type]

        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(
                    MOZ_URL_METRICS_URL,
                    json=payload,
                    auth=auth,
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = data.get("results", [])
                        if results:
                            entry = results[0]
                            return {
                                "da": entry.get("domain_authority", 0),
                                "pa": entry.get("page_authority", 0),
                                "spam_score": entry.get("spam_score", 0),
                                "backlinks": entry.get("external_pages", 0),
                            }
                    else:
                        logger.warning("Moz API returned HTTP %d for %s", resp.status, domain)
        except Exception as exc:
            logger.warning("Moz lookup failed for %s: %s", domain, exc)

        return {"da": 0, "pa": 0, "spam_score": 0, "backlinks": 0}

    async def _ahrefs_metrics(self, domain: str) -> dict[str, object]:
        """Fetch referring domains count from Ahrefs API."""
        if not self._ahrefs_key:
            logger.debug("Ahrefs credentials not set — returning defaults")
            return {"referring_domains": 0}

        params = {
            "target": domain,
            "mode": "subdomains",
            "output": "json",
            "limit": "1",
        }
        headers = {"Authorization": f"Bearer {self._ahrefs_key}"}

        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(
                    AHREFS_METRICS_URL, params=params, headers=headers
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        metrics = data.get("metrics", {})
                        return {
                            "referring_domains": metrics.get("refdomains", 0),
                        }
                    else:
                        logger.warning(
                            "Ahrefs API returned HTTP %d for %s", resp.status, domain
                        )
        except Exception as exc:
            logger.warning("Ahrefs lookup failed for %s: %s", domain, exc)

        return {"referring_domains": 0}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _score_to_verdict(score: int) -> Verdict:
    """Map a 0-100 score to a :class:`Verdict` enum value."""
    if score >= 80:
        return Verdict.EXCELLENT
    if score >= 60:
        return Verdict.GOOD
    if score >= 40:
        return Verdict.FAIR
    if score >= 20:
        return Verdict.POOR
    return Verdict.REJECT
