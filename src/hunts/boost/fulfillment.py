"""
Boost Service — Fulfillment engine.

Routes boost orders to either:
  - ``_social_boost``: 1ai-social blast API (social platforms)
  - ``_device_boost``: 1ai-phonefarm template execution (device-based actions)

Supports drip-feed delivery over N days and retention guarantee
(auto-refill if follower/like drop exceeds 15%).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol

from src.core.config import get_settings
from src.core.exceptions import BoostError
from src.core.logger import get_logger
from src.hunts.boost.anti_detect import AntiDetectEngine
from src.hunts.boost.models import BoostOrder, OrderStatus

logger = get_logger(__name__)
settings = get_settings()


# ---------------------------------------------------------------------------
# External service protocols (duck-typed for testability)
# ---------------------------------------------------------------------------


class SocialBlastClient(Protocol):
    """Duck-type for 1ai-social blast API client."""

    async def blast(
        self,
        platform: str,
        action: str,
        target_url: str,
        quantity: int,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a social blast. Returns ``{"blast_id": ..., "status": ...}``."""
        ...

    async def get_blast_status(self, blast_id: str) -> dict[str, Any]:
        """Poll blast progress. Returns ``{"completed": int, "status": str}``."""
        ...


class PhonefarmClient(Protocol):
    """Duck-type for 1ai-phonefarm template execution client."""

    async def execute_template(
        self,
        template_name: str,
        params: dict[str, Any],
        device_count: int = 10,
    ) -> dict[str, Any]:
        """Run a phonefarm template. Returns ``{"job_id": ..., "status": ...}``."""
        ...

    async def get_job_status(self, job_id: str) -> dict[str, Any]:
        """Poll job progress. Returns ``{"completed": int, "status": str}``."""
        ...


# ---------------------------------------------------------------------------
# HTTP client wrappers for 1ai-social and 1ai-phonefarm
# ---------------------------------------------------------------------------


class SocialBlastAPI:
    """Production client for 1ai-social blast API.

    Expects ``1AI_SOCIAL_BASE_URL`` and ``1AI_SOCIAL_API_KEY`` in settings.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._base_url = (
            base_url
            or getattr(settings, "SOCIAL_BASE_URL", None)
            or "http://localhost:8100"
        )
        self._api_key = (
            api_key
            or getattr(settings, "SOCIAL_API_KEY", "")
        )
        self._client: Any = None  # lazily created httpx.AsyncClient

    async def _get_client(self) -> Any:
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=30.0,
            )
        return self._client

    async def blast(
        self,
        platform: str,
        action: str,
        target_url: str,
        quantity: int,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = await self._get_client()
        payload: dict[str, Any] = {
            "platform": platform,
            "action": action,
            "target_url": target_url,
            "quantity": quantity,
        }
        if params:
            payload["params"] = params
        resp = await client.post("/api/v1/blast", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def get_blast_status(self, blast_id: str) -> dict[str, Any]:
        client = await self._get_client()
        resp = await client.get(f"/api/v1/blast/{blast_id}")
        resp.raise_for_status()
        return resp.json()

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


class PhonefarmAPI:
    """Production client for 1ai-phonefarm template execution.

    Expects ``1AI_PHONEFARM_BASE_URL`` and ``1AI_PHONEFARM_API_KEY`` in settings.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._base_url = (
            base_url
            or getattr(settings, "PHONEFARM_BASE_URL", None)
            or "http://localhost:8200"
        )
        self._api_key = (
            api_key
            or getattr(settings, "PHONEFARM_API_KEY", "")
        )
        self._client: Any = None

    async def _get_client(self) -> Any:
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=30.0,
            )
        return self._client

    async def execute_template(
        self,
        template_name: str,
        params: dict[str, Any],
        device_count: int = 10,
    ) -> dict[str, Any]:
        client = await self._get_client()
        payload = {
            "template": template_name,
            "params": params,
            "device_count": device_count,
        }
        resp = await client.post("/api/v1/templates/execute", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def get_job_status(self, job_id: str) -> dict[str, Any]:
        client = await self._get_client()
        resp = await client.get(f"/api/v1/jobs/{job_id}")
        resp.raise_for_status()
        return resp.json()

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


# ---------------------------------------------------------------------------
# Platform → backend routing
# ---------------------------------------------------------------------------


class FulfillmentBackend(str, Enum):
    SOCIAL = "social"
    DEVICE = "device"


# Platforms handled by 1ai-social blast API
_SOCIAL_PLATFORMS = {
    "instagram", "tiktok", "youtube", "twitter",
    "facebook", "threads", "telegram",
}

# Platforms/actions that require device-level execution (phonefarm)
_DEVICE_PLATFORMS = {"shopee", "spotify", "twitch"}

# Specific actions routed to phonefarm even on social platforms
_DEVICE_ACTIONS = {"cart_adds", "watch_hours", "chatters"}


def _resolve_backend(platform: str, action: str) -> FulfillmentBackend:
    """Determine whether an order should go to social blast or phonefarm."""
    if action in _DEVICE_ACTIONS:
        return FulfillmentBackend.DEVICE
    if platform in _DEVICE_PLATFORMS:
        return FulfillmentBackend.DEVICE
    return FulfillmentBackend.SOCIAL


# Phonefarm template names per platform/action
_PHONEFARM_TEMPLATES: dict[str, dict[str, str]] = {
    "shopee": {
        "followers": "shopee_follow_shop",
        "views":     "shopee_view_product",
        "cart_adds": "shopee_add_to_cart",
    },
    "spotify": {
        "plays":     "spotify_stream_track",
        "followers": "spotify_follow_artist",
        "saves":     "spotify_save_track",
    },
    "twitch": {
        "followers": "twitch_follow_channel",
        "views":     "twitch_view_stream",
        "chatters":  "twitch_chat_message",
    },
    # Device-based actions on social platforms
    "youtube": {
        "watch_hours": "youtube_watch_session",
    },
}


# ---------------------------------------------------------------------------
# Drop detection thresholds
# ---------------------------------------------------------------------------

DROP_THRESHOLD_PCT = 0.15     # 15% drop triggers refill
REFILL_CHECK_INTERVAL_S = 3600  # check every hour
MAX_REFILLS = 3               # cap refills per order


# ---------------------------------------------------------------------------
# Fulfillment engine
# ---------------------------------------------------------------------------


class BoostFulfillment:
    """Routes boost orders to the appropriate backend, handles drip-feed
    delivery and retention guarantee (auto-refill on >15% drops).

    Usage::

        social = SocialBlastAPI()
        phonefarm = PhonefarmAPI()
        engine = BoostFulfillment(social_client=social, phonefarm_client=phonefarm)
        await engine.fulfill(order)
    """

    def __init__(
        self,
        social_client: SocialBlastAPI | None = None,
        phonefarm_client: PhonefarmAPI | None = None,
        anti_detect: AntiDetectEngine | None = None,
    ) -> None:
        self._social = social_client or SocialBlastAPI()
        self._phonefarm = phonefarm_client or PhonefarmAPI()
        self._anti_detect = anti_detect or AntiDetectEngine()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fulfill(self, order: BoostOrder) -> dict[str, Any]:
        """Main entry point. Routes, delivers, and returns result dict.

        For drip-feed orders, spawns a background delivery schedule.
        For single-batch orders, delivers immediately.
        """
        backend = _resolve_backend(order.platform, order.action)
        logger.info(
            "Fulfilling order %s via %s backend (%s/%s x%d)",
            order.id, backend.value, order.platform, order.action, order.quantity,
        )

        if order.drip_days and order.drip_days > 1:
            return await self._drip_fulfill(order, backend)
        return await self._direct_fulfill(order, backend)

    # ------------------------------------------------------------------
    # Direct fulfillment (single batch)
    # ------------------------------------------------------------------

    async def _direct_fulfill(
        self, order: BoostOrder, backend: FulfillmentBackend
    ) -> dict[str, Any]:
        """Deliver full quantity in one batch."""
        try:
            if backend == FulfillmentBackend.SOCIAL:
                result = await self._social_boost(
                    order.platform, order.action, order.target_url, order.quantity, order.speed
                )
            else:
                result = await self._device_boost(
                    order.platform, order.action, order.target_url, order.quantity, order.speed
                )

            completed = result.get("completed", order.quantity)
            logger.info(
                "Order %s delivered %d/%d units via %s",
                order.id, completed, order.quantity, backend.value,
            )

            return {
                "order_id": order.id,
                "backend": backend.value,
                "completed": completed,
                "quantity": order.quantity,
                "status": "completed" if completed >= order.quantity else "partial",
                "external_id": result.get("blast_id") or result.get("job_id"),
            }

        except Exception as exc:
            logger.error("Order %s fulfillment failed: %s", order.id, exc)
            raise BoostError(
                f"Fulfillment failed for order {order.id}: {exc}",
                context={"order_id": order.id, "backend": backend.value},
            ) from exc

    # ------------------------------------------------------------------
    # Drip-feed fulfillment
    # ------------------------------------------------------------------

    async def _drip_fulfill(
        self, order: BoostOrder, backend: FulfillmentBackend
    ) -> dict[str, Any]:
        """Deliver over N days using anti-detect warming schedule.

        Returns immediately with the drip plan; actual delivery runs
        as a background task.
        """
        days = order.drip_days or 1
        daily_plan = self._anti_detect.warming_plan(order.quantity, days)
        logger.info(
            "Drip plan for %s: %d days, daily quantities: %s",
            order.id, days, daily_plan,
        )

        # Launch background delivery
        asyncio.create_task(
            self._run_drip(order, backend, daily_plan),
            name=f"drip-{order.id}",
        )

        return {
            "order_id": order.id,
            "backend": backend.value,
            "mode": "drip_feed",
            "days": days,
            "daily_plan": daily_plan,
            "quantity": order.quantity,
            "status": "in_progress",
        }

    async def _run_drip(
        self,
        order: BoostOrder,
        backend: FulfillmentBackend,
        daily_plan: list[int],
    ) -> None:
        """Background task: deliver one batch per day."""
        total_delivered = 0

        for day_idx, day_qty in enumerate(daily_plan):
            if day_qty <= 0:
                continue

            logger.info(
                "Drip day %d/%d for %s: delivering %d units",
                day_idx + 1, len(daily_plan), order.id, day_qty,
            )

            try:
                if backend == FulfillmentBackend.SOCIAL:
                    result = await self._social_boost(
                        order.platform, order.action, order.target_url, day_qty, order.speed
                    )
                else:
                    result = await self._device_boost(
                        order.platform, order.action, order.target_url, day_qty, order.speed
                    )
                total_delivered += result.get("completed", day_qty)

            except Exception as exc:
                logger.error(
                    "Drip day %d for %s failed: %s", day_idx + 1, order.id, exc
                )

            # Wait until next day (unless last)
            if day_idx < len(daily_plan) - 1:
                await asyncio.sleep(86400)  # 24 hours

        logger.info(
            "Drip complete for %s: %d/%d delivered",
            order.id, total_delivered, order.quantity,
        )

        # Start retention monitoring if guaranteed
        if order.retention_guarantee:
            asyncio.create_task(
                self._retention_monitor(order, total_delivered),
                name=f"retention-{order.id}",
            )

    # ------------------------------------------------------------------
    # Retention guarantee
    # ------------------------------------------------------------------

    async def _retention_monitor(
        self, order: BoostOrder, baseline: int
    ) -> None:
        """Monitor order for drops and auto-refill if >15% loss detected.

        Runs up to MAX_REFILLS times, checking every REFILL_CHECK_INTERVAL_S.
        """
        refill_count = 0
        current = baseline

        while refill_count < MAX_REFILLS:
            await asyncio.sleep(REFILL_CHECK_INTERVAL_S)

            # Query current count from the platform via social API
            current_count = await self._get_current_count(order)
            if current_count is None:
                logger.warning("Could not get current count for %s", order.id)
                continue

            drop_pct = (current - current_count) / current if current > 0 else 0.0
            logger.info(
                "Retention check %s: baseline=%d current=%d drop=%.1f%%",
                order.id, current, current_count, drop_pct * 100,
            )

            if drop_pct > DROP_THRESHOLD_PCT:
                refill_qty = current - current_count
                logger.info(
                    "Refilling %s: %d units (drop %.1f%% > %.0f%%)",
                    order.id, refill_qty, drop_pct * 100, DROP_THRESHOLD_PCT * 100,
                )
                try:
                    await self._social_boost(
                        order.platform,
                        order.action,
                        order.target_url,
                        refill_qty,
                        order.speed,
                    )
                    refill_count += 1
                    current = current_count + refill_qty  # reset baseline
                except Exception as exc:
                    logger.error("Refill failed for %s: %s", order.id, exc)

        logger.info("Retention monitoring ended for %s after %d refills", order.id, refill_count)

    async def _get_current_count(self, order: BoostOrder) -> int | None:
        """Query current follower/like count from 1ai-social."""
        try:
            client = await self._social._get_client()
            resp = await client.get(
                f"/api/v1/count/{order.platform}",
                params={"target_url": order.target_url, "action": order.action},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("count")
        except Exception as exc:
            logger.debug("Count query failed for %s: %s", order.id, exc)
            return None

    # ------------------------------------------------------------------
    # Backend adapters
    # ------------------------------------------------------------------

    async def _social_boost(
        self,
        platform: str,
        action: str,
        target_url: str,
        quantity: int,
        speed: str,
    ) -> dict[str, Any]:
        """Route to 1ai-social blast API.

        Passes anti-detect params so the social engine can apply timing
        randomization and behavioral diversity.
        """
        anti_params = self._build_anti_detect_params(action, speed)

        logger.info(
            "Social blast: %s/%s x%d speed=%s",
            platform, action, quantity, speed,
        )

        result = await self._social.blast(
            platform=platform,
            action=action,
            target_url=target_url,
            quantity=quantity,
            params=anti_params,
        )

        blast_id = result.get("blast_id")
        if not blast_id:
            raise BoostError(
                "Social blast returned no blast_id",
                context={"platform": platform, "action": action},
            )

        # Poll until complete or timeout
        completed = await self._poll_blast(blast_id)
        return {"blast_id": blast_id, "completed": completed}

    async def _device_boost(
        self,
        platform: str,
        action: str,
        target_url: str,
        quantity: int,
        speed: str,
    ) -> dict[str, Any]:
        """Route to 1ai-phonefarm template execution."""
        template_map = _PHONEFARM_TEMPLATES.get(platform, {})
        template_name = template_map.get(action)

        if not template_name:
            raise BoostError(
                f"No phonefarm template for {platform}/{action}",
                context={"platform": platform, "action": action},
            )

        # Calculate device count based on quantity and speed
        device_count = self._calc_device_count(quantity, speed)
        delays = self._anti_detect.delays_sequence(quantity, speed)

        logger.info(
            "Device boost: template=%s x%d devices=%d",
            template_name, quantity, device_count,
        )

        result = await self._phonefarm.execute_template(
            template_name=template_name,
            params={
                "target_url": target_url,
                "quantity": quantity,
                "delays": delays[:100],  # first 100 for the farm to use
                "anti_detect": self._build_anti_detect_params(action, speed),
            },
            device_count=device_count,
        )

        job_id = result.get("job_id")
        if not job_id:
            raise BoostError(
                "Phonefarm returned no job_id",
                context={"template": template_name},
            )

        completed = await self._poll_phonefarm(job_id)
        return {"job_id": job_id, "completed": completed}

    # ------------------------------------------------------------------
    # Polling helpers
    # ------------------------------------------------------------------

    async def _poll_blast(
        self, blast_id: str, timeout_s: float = 7200, interval_s: float = 15.0
    ) -> int:
        """Poll social blast until done. Returns completed count."""
        elapsed = 0.0
        while elapsed < timeout_s:
            await asyncio.sleep(interval_s)
            elapsed += interval_s
            status = await self._social.get_blast_status(blast_id)
            blast_status = status.get("status", "")
            if blast_status in ("completed", "done", "finished"):
                return status.get("completed", 0)
            if blast_status in ("failed", "error"):
                raise BoostError(
                    f"Blast {blast_id} failed: {status.get('error', 'unknown')}",
                    context={"blast_id": blast_id},
                )
        raise BoostError(
            f"Blast {blast_id} timed out after {timeout_s}s",
            context={"blast_id": blast_id},
        )

    async def _poll_phonefarm(
        self, job_id: str, timeout_s: float = 7200, interval_s: float = 15.0
    ) -> int:
        """Poll phonefarm job until done. Returns completed count."""
        elapsed = 0.0
        while elapsed < timeout_s:
            await asyncio.sleep(interval_s)
            elapsed += interval_s
            status = await self._phonefarm.get_job_status(job_id)
            job_status = status.get("status", "")
            if job_status in ("completed", "done", "finished"):
                return status.get("completed", 0)
            if job_status in ("failed", "error"):
                raise BoostError(
                    f"Phonefarm job {job_id} failed: {status.get('error', 'unknown')}",
                    context={"job_id": job_id},
                )
        raise BoostError(
            f"Phonefarm job {job_id} timed out after {timeout_s}s",
            context={"job_id": job_id},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_anti_detect_params(self, action: str, speed: str) -> dict[str, Any]:
        """Build anti-detect parameters for backend consumption."""
        dist = self._anti_detect.compute_action_distribution(action, 100)
        return {
            "speed": speed,
            "timing_profile": speed,
            "action_distribution": dist,
            "min_delay_s": 5.0 if speed == "fast" else 10.0 if speed == "normal" else 20.0,
            "max_delay_s": 30.0 if speed == "fast" else 60.0 if speed == "normal" else 120.0,
        }

    @staticmethod
    def _calc_device_count(quantity: int, speed: str) -> int:
        """Calculate how many phonefarm devices to allocate."""
        if speed == "fast":
            return min(max(quantity // 50, 5), 100)
        elif speed == "normal":
            return min(max(quantity // 100, 3), 50)
        else:  # slow
            return min(max(quantity // 200, 1), 20)

    async def close(self) -> None:
        """Shut down HTTP clients."""
        await self._social.close()
        await self._phonefarm.close()
