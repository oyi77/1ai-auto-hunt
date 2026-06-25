"""Residential proxy management via 1proxy API.

Provides ``ProxyManager`` — a thin async wrapper around the 1proxy
residential-proxy service.  Supports fetching fresh proxies by protocol,
reporting dead endpoints, and bulk rotation.

Typical usage::

    async with ProxyManager() as pm:
        proxy_url = await pm.get_proxy("http")
        # use proxy_url with httpx / playwright …
        await pm.report_bad(proxy_url)
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Literal

import httpx

from src.core.config import get_settings
from src.core.exceptions import ProxyError
from src.core.logger import get_logger

logger = get_logger(__name__)

Protocol = Literal["http", "https", "socks5"]


@dataclass
class _ProxyEntry:
    """Internal bookkeeping for a single proxy endpoint."""

    url: str
    protocol: str
    last_used: float = 0.0
    fail_count: int = 0


@dataclass
class ProxyManager:
    """Async context-manager that manages a pool of rotating proxies.

    Lifecycle::

        async with ProxyManager() as pm:
            url = await pm.get_proxy("socks5")

    All methods are safe to call concurrently from multiple tasks — an
    internal ``asyncio.Lock`` serialises pool mutations.
    """

    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)
    _pool: list[_ProxyEntry] = field(default_factory=list, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def __aenter__(self) -> "ProxyManager":
        settings = get_settings()
        self._client = httpx.AsyncClient(
            base_url=settings.proxy_api_url,
            headers={"Authorization": f"Bearer {settings.proxy_api_key_plain}"},
            timeout=httpx.Timeout(settings.request_timeout),
        )
        logger.info("proxy_manager_started", api_url=settings.proxy_api_url)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("proxy_manager_stopped", pool_size=len(self._pool))

    # ── Public API ────────────────────────────────────────────────────

    async def get_proxy(self, protocol: Protocol = "http") -> str:
        """Return a proxy URL suitable for ``httpx`` / ``playwright``.

        Reuses healthy proxies from the internal pool; refreshes from the
        1proxy API when the pool is empty or stale.

        Returns:
            Proxy URL string, e.g. ``http://user:pass@host:port``.

        Raises:
            ProxyError: The API returned no proxies or the request failed.
        """
        async with self._lock:
            # Try a cached healthy proxy first
            entry = self._pick_healthy(protocol)
            if entry:
                entry.last_used = time.monotonic()
                logger.debug("proxy_reused", url=_mask(entry.url), protocol=protocol)
                return entry.url

            # Pool empty or all bad — refresh
            await self._refresh_pool(protocol)
            entry = self._pick_healthy(protocol)
            if entry:
                entry.last_used = time.monotonic()
                logger.debug("proxy_fresh", url=_mask(entry.url), protocol=protocol)
                return entry.url

            raise ProxyError(
                f"No healthy proxies available for protocol={protocol!r}",
                context={"protocol": protocol, "pool_size": len(self._pool)},
            )

    async def report_bad(self, url: str) -> None:
        """Mark *url* as failing and optionally report it upstream.

        After three consecutive failures the proxy is evicted from the
        local pool and reported to 1proxy as dead.
        """
        async with self._lock:
            entry = self._find(url)
            if entry is None:
                logger.warning("proxy_report_bad_unknown", url=_mask(url))
                return

            entry.fail_count += 1
            logger.info(
                "proxy_report_bad",
                url=_mask(url),
                fail_count=entry.fail_count,
            )

            if entry.fail_count >= 3:
                self._pool.remove(entry)
                await self._report_upstream(url)
                logger.warning("proxy_evicted", url=_mask(url))

    async def rotate(self) -> str:
        """Force-expire the entire pool and return a fresh proxy.

        Convenience wrapper: calls ``get_proxy`` after clearing the pool
        so the next request always hits the upstream API.
        """
        async with self._lock:
            old_size = len(self._pool)
            self._pool.clear()
            logger.info("proxy_pool_rotated", old_size=old_size)
        # get_proxy will refresh outside the lock
        settings = get_settings()
        return await self.get_proxy(settings.proxy_default_protocol)  # type: ignore[arg-type]

    @property
    def pool_size(self) -> int:
        """Current number of cached proxies (approximate, no lock)."""
        return len(self._pool)

    # ── Internals ─────────────────────────────────────────────────────

    def _pick_healthy(self, protocol: str) -> _ProxyEntry | None:
        """Return the least-recently-used healthy entry, or ``None``."""
        candidates = [
            e
            for e in self._pool
            if e.protocol == protocol and e.fail_count < 3
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda e: e.last_used)

    def _find(self, url: str) -> _ProxyEntry | None:
        for e in self._pool:
            if e.url == url:
                return e
        return None

    async def _refresh_pool(self, protocol: str) -> None:
        """Fetch a batch of proxies from the upstream API."""
        assert self._client is not None, "ProxyManager not entered"
        try:
            resp = await self._client.get(
                "/proxy/list",
                params={"protocol": protocol, "limit": 20},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise ProxyError(
                f"Failed to fetch proxies: {exc}",
                context={"protocol": protocol},
            ) from exc

        proxies: list[dict[str, str]] = data.get("proxies", [])
        if not proxies:
            raise ProxyError(
                "1proxy returned an empty proxy list",
                context={"protocol": protocol, "response_keys": list(data.keys())},
            )

        new_entries = [
            _ProxyEntry(url=p["url"], protocol=p.get("protocol", protocol))
            for p in proxies
            if "url" in p
        ]
        # Shuffle to spread load
        random.shuffle(new_entries)
        self._pool.extend(new_entries)
        logger.info("proxy_pool_refreshed", added=len(new_entries), total=len(self._pool))

    async def _report_upstream(self, url: str) -> None:
        """Tell 1proxy that *url* was bad so they can recycle the IP."""
        assert self._client is not None
        try:
            resp = await self._client.post(
                "/proxy/report",
                json={"url": url, "reason": "connection_failed"},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            # Non-fatal — we already evicted locally
            logger.warning("proxy_report_upstream_failed", url=_mask(url), error=str(exc))


def _mask(url: str) -> str:
    """Mask credentials in a proxy URL for safe logging."""
    if "@" in url:
        scheme_rest = url.split("://", 1)
        if len(scheme_rest) == 2:
            scheme, rest = scheme_rest
            creds_host = rest.split("@", 1)
            if len(creds_host) == 2:
                return f"{scheme}://***@{creds_host[1]}"
    return url
