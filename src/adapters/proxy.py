"""1proxy adapter — REST client (port 8000).

Wraps the 1proxy aggregation platform for proxy rotation and quality scoring.
Used by: all hunt modules that need IP rotation.
"""

from __future__ import annotations

import httpx
from typing import Any

from src.core.config import get_settings
from src.core.logger import get_logger

log = get_logger(__name__)

DEFAULT_BASE = "http://localhost:8000"


class ProxyClient:
    """Async client for 1proxy REST API."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self._base = (base_url or settings.proxy_api_url).rstrip("/")
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._http: httpx.AsyncClient | None = None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self._base,
                headers=self._headers,
                timeout=httpx.Timeout(10.0),
            )
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    async def get_proxy(
        self,
        protocol: str = "http",
        min_quality: int = 50,
        country: str | None = None,
    ) -> str:
        """Get next proxy URL from the pool.

        Returns: proxy URL string like "http://user:pass@host:port"
        """
        c = await self._client()
        params: dict[str, Any] = {"protocol": protocol, "min_quality": min_quality}
        if country:
            params["country"] = country
        resp = await c.get("/api/proxy/get", params=params)
        resp.raise_for_status()
        data = resp.json()
        return data["proxy_url"]

    async def report_bad(self, proxy_url: str) -> dict:
        """Report a proxy as bad/unresponsive."""
        c = await self._client()
        resp = await c.post("/api/proxy/report", json={"url": proxy_url})
        resp.raise_for_status()
        return resp.json()

    async def list_proxies(self, protocol: str = "http", limit: int = 50) -> list[dict]:
        c = await self._client()
        resp = await c.get("/api/proxy/list", params={"protocol": protocol, "limit": limit})
        resp.raise_for_status()
        return resp.json()

    async def health(self) -> dict:
        c = await self._client()
        resp = await c.get("/health")
        resp.raise_for_status()
        return resp.json()
