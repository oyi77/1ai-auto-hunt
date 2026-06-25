"""Checkout hunt router — flash sale auto-checkout endpoints.

Targets: Shopee, Tokped, Lazada.
Monitors product pages for price drops and auto-purchases at threshold.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, HttpUrl

from src.api.deps import _current_user, get_current_user, require_admin

try:
    from src.core.logger import get_logger

    logger = get_logger("1ai-auto-hunt.hunts.checkout")
except ImportError:
    logger = logging.getLogger("1ai-auto-hunt.hunts.checkout")  # type: ignore[assignment]

router = APIRouter()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Platform(str, Enum):
    SHOPEE = "shopee"
    TOKPED = "tokped"
    LAZADA = "lazada"


class MonitorStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    TRIGGERED = "triggered"
    EXPIRED = "expired"


class OrderStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class MonitorCreate(BaseModel):
    """Create a price monitor for a product."""

    url: HttpUrl
    platform: Platform
    budget: float = Field(..., gt=0, description="Maximum price to pay (in IDR)")
    threshold: float | None = Field(
        None, gt=0,
        description="Trigger purchase when price drops below this (in IDR)",
    )
    quantity: int = Field(1, ge=1, le=10)
    auto_checkout: bool = Field(True, description="Auto-checkout when threshold hit")


class MonitorResponse(BaseModel):
    id: str
    url: str
    platform: Platform
    budget: float
    threshold: float | None
    quantity: int
    auto_checkout: bool
    status: MonitorStatus
    current_price: float | None = None
    created_at: str
    updated_at: str


class MonitorList(BaseModel):
    items: list[MonitorResponse]
    total: int


class SnipeRequest(BaseModel):
    """Immediately attempt to purchase a product."""

    url: HttpUrl
    platform: Platform
    budget: float = Field(..., gt=0)
    quantity: int = Field(1, ge=1, le=10)
    payment_method: str = Field("wallet", description="Payment method identifier")


class SnipeResponse(BaseModel):
    order_id: str
    status: OrderStatus
    total_price: float | None = None
    message: str


class OrderResponse(BaseModel):
    id: str
    product_url: str
    platform: Platform
    status: OrderStatus
    quantity: int
    total_price: float | None = None
    payment_method: str
    created_at: str
    completed_at: str | None = None
    error: str | None = None


class OrderList(BaseModel):
    items: list[OrderResponse]
    total: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/monitors",
    response_model=MonitorResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a price monitor",
)
async def create_monitor(
    body: MonitorCreate,
    user: dict = Depends(get_current_user),
):
    """Register a new price monitor for a product page.

    The monitor polls the product URL periodically and triggers an
    auto-checkout when the price drops below the threshold (or budget).
    """
    import uuid

    monitor_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    return MonitorResponse(
        id=monitor_id,
        url=str(body.url),
        platform=body.platform,
        budget=body.budget,
        threshold=body.threshold,
        quantity=body.quantity,
        auto_checkout=body.auto_checkout,
        status=MonitorStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )


@router.get(
    "/monitors",
    response_model=MonitorList,
    summary="List price monitors",
)
async def list_monitors(
    platform: Platform | None = None,
    status_filter: MonitorStatus | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    """List all active price monitors, optionally filtered by platform or status."""
    return MonitorList(items=[], total=0)


@router.delete(
    "/monitors/{monitor_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a price monitor",
)
async def delete_monitor(
    monitor_id: str,
    user: dict = Depends(get_current_user),
):
    """Remove a price monitor by ID."""
    return None


@router.post(
    "/snipe",
    response_model=SnipeResponse,
    summary="Snipe a product immediately",
)
async def snipe_product(
    body: SnipeRequest,
    user: dict = Depends(get_current_user),
):
    """Attempt an immediate purchase of the specified product.

    The bot navigates to the product page, checks stock and price,
    and completes checkout if the price is within budget.
    """
    import uuid

    order_id = f"CHK-{uuid.uuid4().hex[:8].upper()}"
    return SnipeResponse(
        order_id=order_id,
        status=OrderStatus.PENDING,
        message=f"Snipe initiated for {body.url}",
    )


@router.get(
    "/orders",
    response_model=OrderList,
    summary="List checkout orders",
)
async def list_orders(
    platform: Platform | None = None,
    status_filter: OrderStatus | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    """List all checkout orders with optional filters."""
    return OrderList(items=[], total=0)


@router.get(
    "/orders/{order_id}",
    response_model=OrderResponse,
    summary="Get order status",
)
async def get_order(
    order_id: str,
    user: dict = Depends(get_current_user),
):
    """Retrieve the status and details of a specific checkout order."""
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Order {order_id} not found",
    )
