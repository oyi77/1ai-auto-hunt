"""
Boost Service — FastAPI router.

Endpoints:
    POST /boost/order          — Create a boost order
    GET  /boost/order/{id}     — Get order status
    GET  /boost/orders         — List orders (filterable)
    GET  /boost/pricing        — Pricing table
    GET  /boost/platforms      — Supported platforms & actions
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import get_db
from src.core.exceptions import BoostError
from src.core.logger import get_logger
from src.hunts.boost.fulfillment import BoostFulfillment
from src.hunts.boost.models import (
    PLATFORMS,
    PLATFORM_ACTIONS,
    SPEED_OPTIONS,
    BoostOrder,
    OrderStatus,
    PricingTier,
)
from src.hunts.boost.pricing import PricingEngine

logger = get_logger(__name__)
router = APIRouter()

# Singleton instances — injected lazily
_pricing_engine = PricingEngine()
_fulfillment_engine: BoostFulfillment | None = None


def _get_fulfillment() -> BoostFulfillment:
    global _fulfillment_engine
    if _fulfillment_engine is None:
        _fulfillment_engine = BoostFulfillment()
    return _fulfillment_engine


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class CreateOrderRequest(BaseModel):
    """POST /boost/order body."""

    user_id: str = Field(..., min_length=1, max_length=128, description="Customer user ID")
    platform: str = Field(..., description="Target platform (e.g. instagram)")
    action: str = Field(..., description="Boost action (e.g. followers)")
    target_url: str = Field(..., min_length=1, description="Target profile/post URL")
    quantity: int = Field(..., ge=1, le=1_000_000, description="Number of units")
    speed: str = Field("normal", description="Delivery speed: slow/normal/fast")
    drip_days: int | None = Field(None, ge=1, le=90, description="Spread delivery over N days")
    retention_guarantee: bool = Field(True, description="Auto-refill on >15% drop")

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in PLATFORMS:
            raise ValueError(f"Unsupported platform '{v}'. Supported: {sorted(PLATFORMS)}")
        return v

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        return v.lower().strip()

    @field_validator("speed")
    @classmethod
    def validate_speed(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in SPEED_OPTIONS:
            raise ValueError(f"Invalid speed '{v}'. Supported: {sorted(SPEED_OPTIONS)}")
        return v


class OrderResponse(BaseModel):
    """Single order response."""

    id: str
    user_id: str
    platform: str
    action: str
    target_url: str
    quantity: int
    speed: str
    cost: str
    status: str
    completed_quantity: int
    drip_days: int | None
    drip_per_day: int | None
    retention_guarantee: bool
    refill_count: int
    created_at: str | None
    updated_at: str | None
    completed_at: str | None


class OrderListResponse(BaseModel):
    """Paginated order list."""

    orders: list[OrderResponse]
    total: int
    page: int
    page_size: int


class PricingRow(BaseModel):
    platform: str
    action: str
    speed: str
    price_per_unit: str
    min_qty: int


class PlatformsResponse(BaseModel):
    platforms: dict[str, list[str]]
    speeds: list[str]
    quantity_range: dict[str, int]


class CreateOrderResponse(BaseModel):
    order: OrderResponse
    pricing: dict[str, Any]
    fulfillment: dict[str, Any]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/order", response_model=CreateOrderResponse, status_code=201)
async def create_order(
    req: CreateOrderRequest,
    db: AsyncSession = Depends(get_db),
) -> CreateOrderResponse:
    """Create a new boost order.

    Calculates pricing, persists the order, and kicks off fulfillment.
    """
    # Validate action against platform
    valid_actions = PLATFORM_ACTIONS.get(req.platform, [])
    if req.action not in valid_actions:
        raise HTTPException(
            status_code=422,
            detail=f"Action '{req.action}' not supported for {req.platform}. "
                   f"Valid actions: {valid_actions}",
        )

    # Calculate price
    pricing = _pricing_engine.calculate(
        req.platform, req.action, req.quantity, req.speed
    )

    # Compute drip per-day
    drip_per_day = None
    if req.drip_days and req.drip_days > 1:
        drip_per_day = req.quantity // req.drip_days

    # Create order
    order = BoostOrder(
        user_id=req.user_id,
        platform=req.platform,
        action=req.action,
        target_url=req.target_url,
        quantity=req.quantity,
        speed=req.speed,
        cost=pricing.total_cost,
        status=OrderStatus.PENDING,
        drip_days=req.drip_days,
        drip_per_day=drip_per_day,
        retention_guarantee=req.retention_guarantee,
    )

    db.add(order)
    await db.commit()
    await db.refresh(order)

    logger.info("Created order %s for user %s ($%s)", order.id, req.user_id, order.cost)

    # Kick off fulfillment (fire-and-forget; status updates happen in background)
    try:
        fulfillment = _get_fulfillment()
        order.status = OrderStatus.PROCESSING
        await db.commit()

        result = await fulfillment.fulfill(order)
    except BoostError as exc:
        logger.error("Fulfillment error for %s: %s", order.id, exc)
        order.status = OrderStatus.FAILED
        await db.commit()
        result = {"status": "failed", "error": exc.message}
    except Exception as exc:
        logger.error("Unexpected fulfillment error for %s: %s", order.id, exc)
        order.status = OrderStatus.FAILED
        await db.commit()
        result = {"status": "failed", "error": str(exc)}

    return CreateOrderResponse(
        order=OrderResponse(**order.to_dict()),
        pricing=pricing.to_dict(),
        fulfillment=result,
    )


@router.get("/order/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: str,
    db: AsyncSession = Depends(get_db),
) -> OrderResponse:
    """Get a single order by ID."""
    result = await db.execute(
        select(BoostOrder).where(BoostOrder.id == order_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    return OrderResponse(**order.to_dict())


@router.get("/orders", response_model=OrderListResponse)
async def list_orders(
    user_id: str | None = Query(None, description="Filter by user ID"),
    platform: str | None = Query(None, description="Filter by platform"),
    status: str | None = Query(None, description="Filter by status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
) -> OrderListResponse:
    """List orders with optional filters and pagination."""
    query = select(BoostOrder)
    count_query = select(func.count(BoostOrder.id))

    if user_id:
        query = query.where(BoostOrder.user_id == user_id)
        count_query = count_query.where(BoostOrder.user_id == user_id)
    if platform:
        query = query.where(BoostOrder.platform == platform.lower())
        count_query = count_query.where(BoostOrder.platform == platform.lower())
    if status:
        if status not in OrderStatus.ALL:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status '{status}'. Valid: {sorted(OrderStatus.ALL)}",
            )
        query = query.where(BoostOrder.status == status)
        count_query = count_query.where(BoostOrder.status == status)

    # Total count
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Paginated results
    offset = (page - 1) * page_size
    query = query.order_by(BoostOrder.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    orders = [OrderResponse(**o.to_dict()) for o in result.scalars().all()]

    return OrderListResponse(orders=orders, total=total, page=page, page_size=page_size)


@router.get("/pricing", response_model=list[PricingRow])
async def get_pricing(
    platform: str | None = Query(None, description="Filter by platform"),
    action: str | None = Query(None, description="Filter by action"),
    quantity: int = Query(1000, ge=1, le=1_000_000, description="Quote quantity"),
) -> list[PricingRow]:
    """Get pricing table.

    Returns per-unit pricing for all platform/action/speed combos,
    or filtered by platform/action. Prices reflect bulk discounts at
    the requested quantity.
    """
    rows: list[PricingRow] = []
    platforms = [platform.lower()] if platform else sorted(PLATFORMS)

    for plat in platforms:
        if plat not in PLATFORMS:
            raise HTTPException(status_code=422, detail=f"Unknown platform '{plat}'")
        actions = PLATFORM_ACTIONS.get(plat, [])
        if action:
            if action.lower() not in actions:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unknown action '{action}' for {plat}. Valid: {actions}",
                )
            actions = [action.lower()]

        for act in sorted(actions):
            for spd in sorted(SPEED_OPTIONS):
                result = _pricing_engine.calculate(plat, act, quantity, spd)
                rows.append(PricingRow(
                    platform=plat,
                    action=act,
                    speed=spd,
                    price_per_unit=str(result.effective_price_per_unit),
                    min_qty=quantity,
                ))

    return rows


@router.get("/platforms", response_model=PlatformsResponse)
async def get_platforms() -> PlatformsResponse:
    """Get supported platforms, their actions, and configuration."""
    return PlatformsResponse(
        platforms=dict(sorted(PLATFORM_ACTIONS.items())),
        speeds=sorted(SPEED_OPTIONS),
        quantity_range={"min": 1, "max": 1_000_000},
    )
