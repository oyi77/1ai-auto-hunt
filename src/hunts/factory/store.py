"""Account inventory management + REST API.

``AccountStore`` provides the business-logic layer for CRUD operations on
accounts and orders.  ``router`` is a FastAPI ``APIRouter`` exposing the
same operations over HTTP.

Endpoints::

    POST   /factory/accounts          Create (register) an account in inventory
    GET    /factory/accounts          List accounts (filter by platform, status)
    GET    /factory/accounts/ready    List accounts ready for sale (auto-priced)
    GET    /factory/accounts/{id}     Get one account
    PUT    /factory/accounts/{id}     Update account fields

    GET    /factory/stats             Inventory statistics

    POST   /factory/orders            Place an order (buy accounts)
    GET    /factory/orders            List orders
    GET    /factory/orders/{id}       Get one order
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import get_db, SessionLocal
from src.core.logger import get_logger
from src.hunts.factory.models import (
    Account,
    AccountOrder,
    AccountStatus,
    OrderStatus,
    Platform,
)
from src.hunts.factory.pricing import PricingEngine

logger = get_logger(__name__)

router = APIRouter(prefix="/factory", tags=["factory"])


# ---------------------------------------------------------------------------
# Pydantic request / response schemas
# ---------------------------------------------------------------------------

class AccountCreateRequest(BaseModel):
    """Payload for registering a new account in inventory."""
    platform: str
    username: str
    password: str
    email: Optional[str] = None
    phone: Optional[str] = None
    proxy_ip: Optional[str] = None
    device_serial: Optional[str] = None


class AccountUpdateRequest(BaseModel):
    """Payload for updating an existing account."""
    username: Optional[str] = None
    password: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    status: Optional[str] = None
    age_days: Optional[int] = None
    followers: Optional[int] = None
    sell_price: Optional[float] = None


class OrderCreateRequest(BaseModel):
    """Payload for placing an order."""
    user_id: str
    account_ids: list[int] = Field(..., min_length=1)


class AccountResponse(BaseModel):
    """Serialized account for API responses."""
    id: int
    platform: str
    username: str
    email: Optional[str] = None
    phone: Optional[str] = None
    proxy_ip: Optional[str] = None
    device_serial: Optional[str] = None
    status: str
    age_days: int
    followers: int
    sell_price: float
    created_at: Optional[str] = None
    sold_at: Optional[str] = None
    order_id: Optional[int] = None


class OrderResponse(BaseModel):
    """Serialized order for API responses."""
    id: int
    user_id: str
    accounts: str
    total: float
    status: str
    created_at: Optional[str] = None


class StatsResponse(BaseModel):
    """Inventory statistics."""
    total_accounts: int
    ready_for_sale: int
    sold: int
    banned: int
    aging: int
    creating: int
    total_orders: int
    total_revenue: float
    avg_price: float


# ---------------------------------------------------------------------------
# Business logic
# ---------------------------------------------------------------------------

class AccountStore:
    """Inventory management for manufactured accounts.

    Usage::

        store = AccountStore()
        accounts = await store.list_ready(platform="instagram")
        order = await store.order(user_id="buyer_42", account_ids=[1, 2, 3])
    """

    def __init__(self, pricing_engine: PricingEngine | None = None) -> None:
        self._pricing = pricing_engine or PricingEngine()

    async def create(
        self,
        platform: str,
        username: str,
        password: str,
        email: str | None = None,
        phone: str | None = None,
        proxy_ip: str | None = None,
        device_serial: str | None = None,
    ) -> Account:
        """Register a new account in the inventory."""
        platform_enum = Platform(platform)
        session = SessionLocal()
        try:
            account = Account(
                platform=platform_enum,
                username=username,
                password=password,
                email=email,
                phone=phone,
                proxy_ip=proxy_ip,
                device_serial=device_serial,
                status=AccountStatus.FRESH,
                age_days=0,
                followers=0,
                sell_price=0.50,
            )
            session.add(account)
            await session.commit()
            await session.refresh(account)
            logger.info("account_created_in_store", account_id=account.id, platform=platform)
            return account
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def get(self, account_id: int) -> Account | None:
        """Fetch a single account by ID."""
        session = SessionLocal()
        try:
            result = await session.execute(
                select(Account).where(Account.id == account_id)
            )
            return result.scalar_one_or_none()
        finally:
            await session.close()

    async def list_ready(
        self,
        platform: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Account]:
        """List accounts filtered by platform and/or status.

        Defaults to accounts with status ``ready``.
        """
        session = SessionLocal()
        try:
            stmt = select(Account)
            if platform:
                stmt = stmt.where(Account.platform == Platform(platform))
            if status:
                stmt = stmt.where(Account.status == AccountStatus(status))
            else:
                stmt = stmt.where(Account.status == AccountStatus.READY)
            stmt = stmt.order_by(Account.created_at.desc()).limit(limit).offset(offset)
            result = await session.execute(stmt)
            accounts = list(result.scalars().all())

            # Update sell prices using the pricing engine
            for acct in accounts:
                acct.sell_price = self._pricing.calculate_sell_price(acct)

            return accounts
        finally:
            await session.close()

    async def list_all(
        self,
        platform: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Account]:
        """List all accounts with optional filters."""
        session = SessionLocal()
        try:
            stmt = select(Account)
            if platform:
                stmt = stmt.where(Account.platform == Platform(platform))
            if status:
                stmt = stmt.where(Account.status == AccountStatus(status))
            stmt = stmt.order_by(Account.created_at.desc()).limit(limit).offset(offset)
            result = await session.execute(stmt)
            return list(result.scalars().all())
        finally:
            await session.close()

    async def update(self, account_id: int, **kwargs) -> Account | None:
        """Update fields on an existing account."""
        session = SessionLocal()
        try:
            result = await session.execute(
                select(Account).where(Account.id == account_id)
            )
            account = result.scalar_one_or_none()
            if account is None:
                return None

            for key, value in kwargs.items():
                if key == "platform":
                    value = Platform(value)
                elif key == "status":
                    value = AccountStatus(value)
                if hasattr(account, key):
                    setattr(account, key, value)

            await session.commit()
            await session.refresh(account)
            return account
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def order(self, user_id: str, account_ids: list[int]) -> AccountOrder:
        """Place an order for the specified accounts.

        Validates that all accounts are ``ready``, calculates total,
        marks them as ``sold``, and creates the order record.
        """
        session = SessionLocal()
        try:
            # Fetch and validate accounts
            result = await session.execute(
                select(Account).where(
                    Account.id.in_(account_ids),
                    Account.status == AccountStatus.READY,
                )
            )
            accounts = list(result.scalars().all())

            if len(accounts) != len(account_ids):
                found_ids = {a.id for a in accounts}
                missing = set(account_ids) - found_ids
                raise ValueError(
                    f"Accounts not available: {missing}. "
                    f"Found {len(accounts)} of {len(account_ids)} requested."
                )

            # Calculate total
            total = sum(
                self._pricing.calculate_sell_price(acct) for acct in accounts
            )

            # Create order
            order = AccountOrder(
                user_id=user_id,
                accounts=json.dumps(account_ids),
                total=round(total, 2),
                status=OrderStatus.PAID,
            )
            session.add(order)
            await session.flush()

            # Mark accounts as sold
            for acct in accounts:
                acct.status = AccountStatus.SOLD
                acct.sold_at = datetime.now(timezone.utc)
                acct.order_id = order.id

            await session.commit()
            await session.refresh(order)

            logger.info(
                "order_placed",
                order_id=order.id,
                user_id=user_id,
                account_count=len(accounts),
                total=total,
            )
            return order

        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def mark_sold(self, account_id: int, order_id: int) -> Account | None:
        """Mark a single account as sold (alternative to bulk order)."""
        return await self.update(
            account_id,
            status=AccountStatus.SOLD.value,
            sold_at=datetime.now(timezone.utc),
            order_id=order_id,
        )

    async def get_stats(self) -> dict:
        """Return inventory statistics."""
        session = SessionLocal()
        try:
            # Account counts by status
            status_counts: dict[str, int] = {}
            for status in AccountStatus:
                result = await session.execute(
                    select(func.count(Account.id)).where(Account.status == status)
                )
                status_counts[status.value] = result.scalar() or 0

            total_accounts = sum(status_counts.values())

            # Order stats
            total_orders_result = await session.execute(
                select(func.count(AccountOrder.id))
            )
            total_orders = total_orders_result.scalar() or 0

            revenue_result = await session.execute(
                select(func.coalesce(func.sum(AccountOrder.total), 0.0)).where(
                    AccountOrder.status.in_([
                        OrderStatus.PAID, OrderStatus.DELIVERED
                    ])
                )
            )
            total_revenue = revenue_result.scalar() or 0.0

            # Average price of ready accounts
            avg_result = await session.execute(
                select(func.coalesce(func.avg(Account.sell_price), 0.0)).where(
                    Account.status == AccountStatus.READY
                )
            )
            avg_price = avg_result.scalar() or 0.0

            return {
                "total_accounts": total_accounts,
                "ready_for_sale": status_counts.get("ready", 0),
                "sold": status_counts.get("sold", 0),
                "banned": status_counts.get("banned", 0),
                "aging": status_counts.get("aging", 0),
                "creating": status_counts.get("creating", 0),
                "total_orders": total_orders,
                "total_revenue": round(float(total_revenue), 2),
                "avg_price": round(float(avg_price), 2),
            }
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# Singleton store instance for the router
# ---------------------------------------------------------------------------

_store = AccountStore()


# ---------------------------------------------------------------------------
# REST API endpoints
# ---------------------------------------------------------------------------

@router.post("/accounts", response_model=AccountResponse, status_code=201)
async def create_account(req: AccountCreateRequest):
    """Register a new account in inventory."""
    try:
        acct = await _store.create(
            platform=req.platform,
            username=req.username,
            password=req.password,
            email=req.email,
            phone=req.phone,
            proxy_ip=req.proxy_ip,
            device_serial=req.device_serial,
        )
        return AccountResponse(**acct.to_dict())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/accounts", response_model=list[AccountResponse])
async def list_accounts(
    platform: str | None = Query(None, description="Filter by platform"),
    status: str | None = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List accounts with optional filters."""
    accounts = await _store.list_all(
        platform=platform, status=status, limit=limit, offset=offset
    )
    return [AccountResponse(**acct.to_dict()) for acct in accounts]


@router.get("/accounts/ready", response_model=list[AccountResponse])
async def list_ready_accounts(
    platform: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List accounts ready for sale (auto-prices them)."""
    accounts = await _store.list_ready(
        platform=platform, limit=limit, offset=offset
    )
    return [AccountResponse(**acct.to_dict()) for acct in accounts]


@router.get("/accounts/{account_id}", response_model=AccountResponse)
async def get_account(account_id: int):
    """Get a single account by ID."""
    acct = await _store.get(account_id)
    if acct is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return AccountResponse(**acct.to_dict())


@router.put("/accounts/{account_id}", response_model=AccountResponse)
async def update_account(account_id: int, req: AccountUpdateRequest):
    """Update account fields."""
    updates = req.model_dump(exclude_none=True)
    acct = await _store.update(account_id, **updates)
    if acct is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return AccountResponse(**acct.to_dict())


@router.get("/stats", response_model=StatsResponse)
async def get_stats():
    """Inventory statistics."""
    return await _store.get_stats()


@router.post("/orders", response_model=OrderResponse, status_code=201)
async def create_order(req: OrderCreateRequest):
    """Place an order for accounts."""
    try:
        order = await _store.order(
            user_id=req.user_id, account_ids=req.account_ids
        )
        return OrderResponse(**order.to_dict())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/orders", response_model=list[OrderResponse])
async def list_orders(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List all orders."""
    session = SessionLocal()
    try:
        stmt = (
            select(AccountOrder)
            .order_by(AccountOrder.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await session.execute(stmt)
        orders = list(result.scalars().all())
        return [OrderResponse(**o.to_dict()) for o in orders]
    finally:
        await session.close()


@router.get("/orders/{order_id}", response_model=OrderResponse)
async def get_order(order_id: int):
    """Get a single order by ID."""
    session = SessionLocal()
    try:
        result = await session.execute(
            select(AccountOrder).where(AccountOrder.id == order_id)
        )
        order = result.scalar_one_or_none()
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        return OrderResponse(**order.to_dict())
    finally:
        await session.close()
