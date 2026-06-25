"""1ai-social adapter — REST client (port 8200).

Wraps the 1ai-social FastAPI server for blast operations, engagement,
DM pipeline, and growth engine. Used by: boost (fulfillment).
"""

from __future__ import annotations

import httpx
from typing import Any

from src.core.config import get_settings
from src.core.logger import get_logger

log = get_logger(__name__)

DEFAULT_BASE = "http://localhost:8200"


class SocialClient:
    """Async client for 1ai-social REST API."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self._base = (base_url or settings.social_api_url).rstrip("/")
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._http: httpx.AsyncClient | None = None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self._base,
                headers=self._headers,
                timeout=httpx.Timeout(60.0),
            )
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    # ── Blast Operations ───────────────────────────────────────────────
    async def create_blast(
        self,
        platform: str,
        action: str,
        target: str,
        accounts: list[dict[str, str]],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a blast (bulk engagement) across accounts.

        Args:
            platform: instagram, tiktok, twitter, facebook, etc.
            action: like, comment, follow, unfollow, dm, view
            target: URL or ID of the target (post, profile, etc.)
            accounts: list of {"username": ..., "password": ...} dicts
            config: optional blast config (delay, proxy, etc.)
        """
        c = await self._client()
        body = {
            "platform": platform,
            "action": action,
            "target": target,
            "accounts": accounts,
        }
        if config:
            body["config"] = config
        resp = await c.post("/api/blast", json=body)
        resp.raise_for_status()
        return resp.json()

    async def get_blast_status(self, blast_id: str) -> dict[str, Any]:
        c = await self._client()
        resp = await c.get(f"/api/blast/{blast_id}")
        resp.raise_for_status()
        return resp.json()

    # ── Engagement ─────────────────────────────────────────────────────
    async def engagement_action(
        self,
        platform: str,
        action: str,
        target: str,
        account: dict[str, str],
    ) -> dict[str, Any]:
        """Single engagement action via engagement client."""
        c = await self._client()
        resp = await c.post("/api/engagement/action", json={
            "platform": platform,
            "action": action,
            "target": target,
            "account": account,
        })
        resp.raise_for_status()
        return resp.json()

    # ── DM Pipeline ────────────────────────────────────────────────────
    async def send_dm(
        self,
        platform: str,
        recipient: str,
        message: str,
        account: dict[str, str],
    ) -> dict[str, Any]:
        c = await self._client()
        resp = await c.post("/api/dm/send", json={
            "platform": platform,
            "recipient": recipient,
            "message": message,
            "account": account,
        })
        resp.raise_for_status()
        return resp.json()

    # ── Growth Engine ──────────────────────────────────────────────────
    async def auto_like(self, platform: str, target: str, count: int) -> dict:
        return await self.create_blast(platform, "like", target, [], config={"count": count})

    async def auto_follow(self, platform: str, target: str, count: int) -> dict:
        return await self.create_blast(platform, "follow", target, [], config={"count": count})

    # ── Accounts ───────────────────────────────────────────────────────
    async def list_accounts(self) -> list[dict[str, Any]]:
        c = await self._client()
        resp = await c.get("/api/accounts")
        resp.raise_for_status()
        return resp.json()

    # ── Health ─────────────────────────────────────────────────────────
    async def health(self) -> dict[str, Any]:
        c = await self._client()
        resp = await c.get("/health")
        resp.raise_for_status()
        return resp.json()
