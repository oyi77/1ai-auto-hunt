"""Stream hunt router — streaming farm endpoints.

Automates Spotify / Apple Music play generation across a pool of
accounts, tracks revenue, and manages playlists.
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

    logger = get_logger("1ai-auto-hunt.hunts.stream")
except ImportError:
    logger = logging.getLogger("1ai-auto-hunt.hunts.stream")  # type: ignore[assignment]

router = APIRouter()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Platform(str, Enum):
    SPOTIFY = "spotify"
    APPLE_MUSIC = "apple_music"
    YOUTUBE_MUSIC = "youtube_music"


class FarmStatus(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class FarmCreate(BaseModel):
    """Start a streaming farm session."""

    platform: Platform
    account_count: int = Field(..., ge=1, le=1000, description="Number of accounts to use")
    playlist_id: str | None = Field(None, description="Playlist ID to stream")
    track_ids: list[str] = Field(default=[], description="Specific track IDs to stream")
    plays_per_account: int = Field(10, ge=1, le=100, description="Plays per account per session")
    duration_hours: float = Field(8.0, ge=0.5, le=24.0, description="Session duration in hours")
    geo_target: str | None = Field(None, description="Country code for geo-targeted plays (e.g. US, ID)")
    proxy_pool: str | None = Field(None, description="Named proxy pool to use")


class FarmResponse(BaseModel):
    id: str
    platform: Platform
    status: FarmStatus
    account_count: int
    playlist_id: str | None
    plays_per_account: int
    duration_hours: float
    total_plays: int = 0
    completed_plays: int = 0
    failed_plays: int = 0
    estimated_revenue: float = 0.0
    started_at: str | None = None
    created_at: str


class FarmList(BaseModel):
    items: list[FarmResponse]
    total: int


class PlaylistCreate(BaseModel):
    """Register a playlist for streaming."""

    name: str = Field(..., min_length=1, max_length=200)
    platform: Platform
    playlist_url: HttpUrl | None = None
    track_ids: list[str] = Field(default=[], description="Track IDs in play order")
    loop: bool = Field(True, description="Loop playlist when finished")
    shuffle: bool = Field(False, description="Shuffle track order")


class PlaylistResponse(BaseModel):
    id: str
    name: str
    platform: Platform
    track_count: int
    loop: bool
    shuffle: bool
    total_plays: int = 0
    created_at: str


class PlaylistList(BaseModel):
    items: list[PlaylistResponse]
    total: int


class RevenueReport(BaseModel):
    """Revenue report for a given period."""

    month: str  # YYYY-MM
    platform: Platform | None = None
    total_plays: int = 0
    total_hours: float = 0.0
    estimated_revenue_usd: float = 0.0
    cost_proxy_usd: float = 0.0
    cost_accounts_usd: float = 0.0
    net_profit_usd: float = 0.0
    top_tracks: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/farm",
    response_model=FarmResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a streaming farm session",
)
async def start_farm(
    body: FarmCreate,
    user: dict = Depends(get_current_user),
):
    """Start a new streaming farm session.

    Distributes plays across the specified number of accounts using
    rotated proxies to simulate organic listening patterns.
    """
    import uuid

    farm_id = f"FARM-{uuid.uuid4().hex[:8].upper()}"
    now = datetime.now(timezone.utc).isoformat()
    return FarmResponse(
        id=farm_id,
        platform=body.platform,
        status=FarmStatus.STARTING,
        account_count=body.account_count,
        playlist_id=body.playlist_id,
        plays_per_account=body.plays_per_account,
        duration_hours=body.duration_hours,
        created_at=now,
    )


@router.get(
    "/farms",
    response_model=FarmList,
    summary="List farm sessions",
)
async def list_farms(
    platform: Platform | None = None,
    status_filter: FarmStatus | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    """List all streaming farm sessions."""
    return FarmList(items=[], total=0)


@router.get(
    "/farm/{farm_id}",
    response_model=FarmResponse,
    summary="Get farm status",
)
async def get_farm(
    farm_id: str,
    user: dict = Depends(get_current_user),
):
    """Get the current status and metrics of a streaming farm session."""
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Farm {farm_id} not found",
    )


@router.post(
    "/farm/{farm_id}/stop",
    response_model=FarmResponse,
    summary="Stop a farm session",
)
async def stop_farm(
    farm_id: str,
    user: dict = Depends(get_current_user),
):
    """Gracefully stop a running farm session."""
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Farm {farm_id} not found",
    )


@router.post(
    "/playlist",
    response_model=PlaylistResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a playlist",
)
async def create_playlist(
    body: PlaylistCreate,
    user: dict = Depends(get_current_user),
):
    """Register a playlist for use in farm sessions."""
    import uuid

    playlist_id = f"PL-{uuid.uuid4().hex[:8].upper()}"
    now = datetime.now(timezone.utc).isoformat()
    return PlaylistResponse(
        id=playlist_id,
        name=body.name,
        platform=body.platform,
        track_count=len(body.track_ids),
        loop=body.loop,
        shuffle=body.shuffle,
        created_at=now,
    )


@router.get(
    "/playlists",
    response_model=PlaylistList,
    summary="List playlists",
)
async def list_playlists(
    platform: Platform | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    """List all registered playlists."""
    return PlaylistList(items=[], total=0)


@router.get(
    "/revenue",
    response_model=RevenueReport,
    summary="Get revenue report",
)
async def get_revenue(
    month: str = Query(..., description="Month in YYYY-MM format"),
    platform: Platform | None = None,
    user: dict = Depends(get_current_user),
):
    """Get revenue report for a specific month."""
    return RevenueReport(month=month, platform=platform)
