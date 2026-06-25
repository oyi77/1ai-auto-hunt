"""PhoneFarm adapter — REST client to 1ai-phonefarm (port 8889).

Wraps the PhoneFarm Cloud API for device management, template execution,
and screen operations. Used by: factory (account aging), boost (device
views), stream (playback), checkout (multi-device).
"""

from __future__ import annotations

import httpx
from typing import Any

from src.core.config import get_settings
from src.core.logger import get_logger

log = get_logger(__name__)

DEFAULT_BASE = "http://localhost:8889"


class PhoneFarmClient:
    """Async client for 1ai-phonefarm REST API."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self._base = (base_url or settings.phonefarm_url).rstrip("/")
        # PhoneFarm uses JWT auth, not bare API key — pass via header
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
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

    # ── Devices ────────────────────────────────────────────────────────
    async def list_devices(self, status: str = "online") -> list[dict[str, Any]]:
        c = await self._client()
        resp = await c.get("/api/devices", params={"status": status})
        resp.raise_for_status()
        return resp.json()

    async def device_info(self, serial: str) -> dict[str, Any]:
        c = await self._client()
        resp = await c.get(f"/api/devices/{serial}")
        resp.raise_for_status()
        return resp.json()

    async def device_screenshot(self, serial: str) -> bytes:
        c = await self._client()
        resp = await c.get(f"/api/devices/{serial}/screenshot")
        resp.raise_for_status()
        return resp.content

    # ── Device Control ─────────────────────────────────────────────────
    async def tap(self, serial: str, x: int, y: int) -> dict:
        c = await self._client()
        resp = await c.post(f"/api/devices/{serial}/tap", json={"x": x, "y": y})
        resp.raise_for_status()
        return resp.json()

    async def swipe(self, serial: str, x1: int, y1: int, x2: int, y2: int) -> dict:
        c = await self._client()
        resp = await c.post(f"/api/devices/{serial}/swipe",
                            json={"x1": x1, "y1": y1, "x2": x2, "y2": y2})
        resp.raise_for_status()
        return resp.json()

    async def type_text(self, serial: str, text: str) -> dict:
        c = await self._client()
        resp = await c.post(f"/api/devices/{serial}/type", json={"text": text})
        resp.raise_for_status()
        return resp.json()

    async def press_key(self, serial: str, key: str) -> dict:
        c = await self._client()
        resp = await c.post(f"/api/devices/{serial}/key", json={"key": key})
        resp.raise_for_status()
        return resp.json()

    async def launch_app(self, serial: str, package: str) -> dict:
        c = await self._client()
        resp = await c.post(f"/api/devices/{serial}/launch", json={"package": package})
        resp.raise_for_status()
        return resp.json()

    async def shell(self, serial: str, command: str) -> dict:
        c = await self._client()
        resp = await c.post(f"/api/devices/{serial}/shell", json={"command": command})
        resp.raise_for_status()
        return resp.json()

    # ── Templates ──────────────────────────────────────────────────────
    async def create_template(self, name: str, steps: list[dict]) -> dict[str, Any]:
        c = await self._client()
        resp = await c.post("/api/templates", json={"name": name, "steps": steps})
        resp.raise_for_status()
        return resp.json()

    async def list_templates(self) -> list[dict[str, Any]]:
        c = await self._client()
        resp = await c.get("/api/templates")
        resp.raise_for_status()
        return resp.json()

    async def run_template(self, template_id: str, device_serial: str | None = None) -> dict:
        c = await self._client()
        body: dict[str, Any] = {}
        if device_serial:
            body["device_serial"] = device_serial
        resp = await c.post(f"/api/templates/{template_id}/run", json=body)
        resp.raise_for_status()
        return resp.json()

    async def delete_template(self, template_id: str) -> dict:
        c = await self._client()
        resp = await c.delete(f"/api/templates/{template_id}")
        resp.raise_for_status()
        return resp.json()

    # ── Rentals ────────────────────────────────────────────────────────
    async def start_rental(self, listing_id: str) -> dict:
        c = await self._client()
        resp = await c.post("/api/rentals/start", json={"listing_id": listing_id})
        resp.raise_for_status()
        return resp.json()

    async def stop_rental(self, rental_id: str) -> dict:
        c = await self._client()
        resp = await c.post(f"/api/rentals/{rental_id}/stop")
        resp.raise_for_status()
        return resp.json()

    # ── Wallet ─────────────────────────────────────────────────────────
    async def get_balance(self) -> dict[str, Any]:
        c = await self._client()
        resp = await c.get("/api/wallet")
        resp.raise_for_status()
        return resp.json()

    async def get_transactions(self) -> list[dict]:
        c = await self._client()
        resp = await c.get("/api/wallet/transactions")
        resp.raise_for_status()
        return resp.json()
