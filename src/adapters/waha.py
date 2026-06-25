"""WAHA adapter — REST client (port 3010).

Wraps the WhatsApp HTTP API for bulk messaging and customer notifications.
Used by: boost (customer notifications), flash sale (deal alerts).
"""

from __future__ import annotations

import httpx
from typing import Any

from src.core.logger import get_logger

log = get_logger(__name__)

DEFAULT_BASE = "http://localhost:3010"


class WhatsAppClient:
    """Async client for WAHA (WhatsApp HTTP API)."""

    def __init__(self, base_url: str = DEFAULT_BASE, session: str = "default") -> None:
        self._base = base_url.rstrip("/")
        self._session = session
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        self._http: httpx.AsyncClient | None = None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self._base,
                headers=self._headers,
                timeout=httpx.Timeout(30.0),
            )
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    # ── Session ────────────────────────────────────────────────────────
    async def start_session(self, session: str = "default") -> dict:
        c = await self._client()
        resp = await c.post(f"/api/sessions/start", json={"name": session})
        resp.raise_for_status()
        return resp.json()

    async def stop_session(self, session: str = "default") -> dict:
        c = await self._client()
        resp = await c.post(f"/api/sessions/stop", json={"name": session})
        resp.raise_for_status()
        return resp.json()

    # ── Messaging ──────────────────────────────────────────────────────
    async def send_text(self, chat_id: str, text: str) -> dict[str, Any]:
        """Send a text message.

        Args:
            chat_id: phone number (e.g. "6281234567890@c.us") or group ID
            text: message body
        """
        c = await self._client()
        resp = await c.post(f"/api/sendText", json={
            "session": self._session,
            "chatId": chat_id,
            "text": text,
        })
        resp.raise_for_status()
        return resp.json()

    async def send_image(self, chat_id: str, url: str, caption: str = "") -> dict:
        c = await self._client()
        resp = await c.post(f"/api/sendImage", json={
            "session": self._session,
            "chatId": chat_id,
            "file": {"url": url},
            "caption": caption,
        })
        resp.raise_for_status()
        return resp.json()

    # ── Groups ─────────────────────────────────────────────────────────
    async def create_group(self, name: str, participants: list[str]) -> dict:
        c = await self._client()
        resp = await c.post(f"/api/groups/create", json={
            "session": self._session,
            "name": name,
            "participants": participants,
        })
        resp.raise_for_status()
        return resp.json()

    async def get_groups(self) -> list[dict]:
        c = await self._client()
        resp = await c.get(f"/api/groups", params={"session": self._session})
        resp.raise_for_status()
        return resp.json()

    # ── Contacts ───────────────────────────────────────────────────────
    async def get_contacts(self) -> list[dict]:
        c = await self._client()
        resp = await c.get(f"/api/contacts", params={"session": self._session})
        resp.raise_for_status()
        return resp.json()

    # ── Health ─────────────────────────────────────────────────────────
    async def health(self) -> dict:
        c = await self._client()
        resp = await c.get("/ping")
        resp.raise_for_status()
        return resp.json()
