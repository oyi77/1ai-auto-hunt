"""1ai-affiliate adapter — REST client (port 3001).

Wraps the ClickServer for click tracking, conversion tracking, and smartlink
generation. Used by: flash sale (affiliate fallback when can't buy directly).
"""

from __future__ import annotations

import httpx
from typing import Any

from src.core.logger import get_logger

log = get_logger(__name__)

DEFAULT_BASE = "http://localhost:3001"


class AffiliateClient:
    """Async client for 1ai-affiliate ClickServer."""

    def __init__(self, base_url: str = DEFAULT_BASE, api_key: str | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._http: httpx.AsyncClient | None = None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self._base,
                headers=self._headers,
                timeout=httpx.Timeout(15.0),
            )
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    # ── Smartlinks ─────────────────────────────────────────────────────
    async def create_smartlink(
        self,
        destination: str,
        tags: list[str] | None = None,
        campaign: str | None = None,
    ) -> dict[str, Any]:
        """Create a tracked smartlink (short URL).

        Args:
            destination: target URL
            tags: list of tags for categorization
            campaign: campaign name
        """
        c = await self._client()
        body: dict[str, Any] = {"destination": destination}
        if tags:
            body["tags"] = tags
        if campaign:
            body["campaign"] = campaign
        resp = await c.post("/api/smartlinks", json=body)
        resp.raise_for_status()
        return resp.json()

    async def list_smartlinks(self, campaign: str | None = None) -> list[dict]:
        c = await self._client()
        params = {}
        if campaign:
            params["campaign"] = campaign
        resp = await c.get("/api/smartlinks", params=params)
        resp.raise_for_status()
        return resp.json()

    # ── Tracking ───────────────────────────────────────────────────────
    async def track_click(self, smartlink_id: str, metadata: dict | None = None) -> dict:
        c = await self._client()
        body: dict[str, Any] = {"smartlink_id": smartlink_id}
        if metadata:
            body["metadata"] = metadata
        resp = await c.post("/api/track/click", json=body)
        resp.raise_for_status()
        return resp.json()

    async def track_conversion(
        self,
        smartlink_id: str,
        value: float = 0,
        currency: str = "IDR",
    ) -> dict:
        c = await self._client()
        resp = await c.post("/api/track/conversion", json={
            "smartlink_id": smartlink_id,
            "value": value,
            "currency": currency,
        })
        resp.raise_for_status()
        return resp.json()

    # ── Analytics ──────────────────────────────────────────────────────
    async def get_stats(self, smartlink_id: str) -> dict[str, Any]:
        c = await self._client()
        resp = await c.get(f"/api/stats/{smartlink_id}")
        resp.raise_for_status()
        return resp.json()

    async def health(self) -> dict:
        c = await self._client()
        resp = await c.get("/health")
        resp.raise_for_status()
        return resp.json()
