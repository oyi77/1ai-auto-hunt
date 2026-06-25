"""1ai-social adapter — REST (port 8200) + MCP (port 8766).

Two transport modes:
  - REST: direct HTTP calls for blast, engagement, DM, growth (SocialClient)
  - MCP:  fastmcp tool calls via 1ai-social's MCP server (SocialMCPClient)

Used by: boost (fulfillment).
"""

from __future__ import annotations

import json
import subprocess
import httpx
from typing import Any

from src.core.config import get_settings
from src.core.logger import get_logger

log = get_logger(__name__)

REST_BASE = "http://localhost:8200"
MCP_BASE = "http://localhost:8766"


# ── REST Client (original) ─────────────────────────────────────────────

class SocialClient:
    """Async REST client for 1ai-social FastAPI server."""

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

    async def create_blast(
        self,
        platform: str,
        action: str,
        target: str,
        accounts: list[dict[str, str]],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        c = await self._client()
        body = {"platform": platform, "action": action, "target": target, "accounts": accounts}
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

    async def engagement_action(self, platform: str, action: str, target: str, account: dict[str, str]) -> dict[str, Any]:
        c = await self._client()
        resp = await c.post("/api/engagement/action", json={
            "platform": platform, "action": action, "target": target, "account": account,
        })
        resp.raise_for_status()
        return resp.json()

    async def send_dm(self, platform: str, recipient: str, message: str, account: dict[str, str]) -> dict[str, Any]:
        c = await self._client()
        resp = await c.post("/api/dm/send", json={
            "platform": platform, "recipient": recipient, "message": message, "account": account,
        })
        resp.raise_for_status()
        return resp.json()

    async def auto_like(self, platform: str, target: str, count: int) -> dict:
        return await self.create_blast(platform, "like", target, [], config={"count": count})

    async def auto_follow(self, platform: str, target: str, count: int) -> dict:
        return await self.create_blast(platform, "follow", target, [], config={"count": count})

    async def list_accounts(self) -> list[dict[str, Any]]:
        c = await self._client()
        resp = await c.get("/api/accounts")
        resp.raise_for_status()
        return resp.json()

    async def health(self) -> dict[str, Any]:
        c = await self._client()
        resp = await c.get("/health")
        resp.raise_for_status()
        return resp.json()


# ── MCP Client (1ai-social MCP server on port 8766) ────────────────────

class SocialMCPClient:
    """Client for 1ai-social MCP server via HTTP.

    Connects to the fastmcp server that 1ai-social exposes on port 8766.
    Use this for operations that benefit from MCP tool discovery
    and schema validation.
    """

    def __init__(self, mcp_url: str = MCP_BASE) -> None:
        self._base = mcp_url.rstrip("/")
        self._http: httpx.AsyncClient | None = None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self._base,
                headers={"Content-Type": "application/json"},
                timeout=httpx.Timeout(60.0),
            )
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] = None) -> Any:
        """Call an MCP tool on 1ai-social.

        Args:
            tool_name: MCP tool name (e.g. 'automation_list_flows')
            arguments: tool arguments
        """
        c = await self._client()
        resp = await c.post("/mcp", json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments or {}},
            "id": 1,
        })
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"MCP error: {data['error']}")
        return data.get("result", {}).get("content", [{}])[0].get("text", "")

    async def list_tools(self) -> list[dict]:
        """List available MCP tools on 1ai-social."""
        c = await self._client()
        resp = await c.post("/mcp", json={
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": 1,
        })
        resp.raise_for_status()
        return resp.json().get("result", {}).get("tools", [])

    async def health(self) -> dict[str, Any]:
        c = await self._client()
        resp = await c.get("/health")
        resp.raise_for_status()
        return resp.json()
