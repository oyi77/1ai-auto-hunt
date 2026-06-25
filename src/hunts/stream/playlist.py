"""PlaylistManager — CRUD and scheduling for streaming playlists.

Manages track collections and generates daily schedules that distribute
tracks across accounts with anti-detection randomization.

All playlists live in the stream_playlists / stream_tracks tables.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.core.config import get_settings
from src.core.db import get_db, Database
from src.core.logger import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class Playlist:
    """A named collection of tracks."""
    id: str
    name: str
    track_count: int
    created_at: datetime


@dataclass(frozen=True)
class TrackEntry:
    """A track within a playlist."""
    id: str
    playlist_id: str
    title: str
    artist: str
    uri: str
    duration_ms: int
    play_order: int


@dataclass(frozen=True)
class ScheduleSlot:
    """One slot in a daily schedule for an account."""
    account_id: str
    track: TrackEntry
    slot_index: int
    estimated_start: datetime
    estimated_end: datetime


@dataclass(frozen=True)
class DailySchedule:
    """Full daily schedule for a single account."""
    account_id: str
    date: str  # YYYY-MM-DD
    slots: list[ScheduleSlot]
    total_streams: int
    estimated_duration_min: float


class PlaylistManager:
    """CRUD operations and schedule generation for streaming playlists.

    Args:
        db: Database instance (defaults to singleton).

    Usage:
        pm = PlaylistManager()
        pid = pm.create("Summer Hits 2026")
        pm.add_track(pid, title="Blinding Lights", artist="The Weeknd",
                      uri="spotify:track:0VjIjW4GlUZAMYd2vXMi3b",
                      duration_ms=200040)
        schedule = pm.get_daily_schedule(account_id="abc123")
    """

    def __init__(self, db: Database | None = None) -> None:
        self._db = db or get_db()

    # ── Playlist CRUD ───────────────────────────────────────────

    def create(self, name: str, tracks: list[dict[str, Any]] | None = None) -> str:
        """Create a new playlist, optionally with initial tracks.

        Args:
            name: Human-readable playlist name.
            tracks: Optional list of dicts with keys: title, artist, uri, duration_ms.

        Returns:
            The new playlist ID.
        """
        playlist_id = str(uuid.uuid4())
        self._db.execute(
            "INSERT INTO stream_playlists (id, name, track_count) VALUES (?, ?, ?)",
            (playlist_id, name, len(tracks) if tracks else 0),
        )

        if tracks:
            for order, track in enumerate(tracks):
                self._insert_track(playlist_id, track, order)
            log.info("playlist.created_with_tracks", name=name, count=len(tracks))
        else:
            log.info("playlist.created_empty", name=name)

        return playlist_id

    def add_track(
        self,
        playlist_id: str,
        title: str,
        artist: str,
        uri: str,
        duration_ms: int,
    ) -> str:
        """Add a single track to an existing playlist.

        Args:
            playlist_id: Target playlist.
            title: Track title.
            artist: Artist name.
            uri: Platform URI (e.g. spotify:track:...).
            duration_ms: Track duration in milliseconds.

        Returns:
            New track ID.
        """
        # Get next play_order
        row = self._db.fetchone(
            "SELECT COALESCE(MAX(play_order), -1) + 1 AS next_order FROM stream_tracks WHERE playlist_id = ?",
            (playlist_id,),
        )
        next_order = row["next_order"] if row else 0

        track_id = self._insert_track(
            playlist_id,
            {"title": title, "artist": artist, "uri": uri, "duration_ms": duration_ms},
            next_order,
        )

        # Update track count
        self._db.execute(
            """
            UPDATE stream_playlists
            SET track_count = (SELECT COUNT(*) FROM stream_tracks WHERE playlist_id = ?)
            WHERE id = ?
            """,
            (playlist_id, playlist_id),
        )

        log.info("playlist.track_added", playlist_id=playlist_id, title=title, order=next_order)
        return track_id

    def remove_track(self, playlist_id: str, track_id: str) -> bool:
        """Remove a track from a playlist.

        Returns:
            True if the track was found and removed.
        """
        cursor = self._db.execute(
            "DELETE FROM stream_tracks WHERE id = ? AND playlist_id = ?",
            (track_id, playlist_id),
        )
        removed = cursor.rowcount > 0

        if removed:
            self._db.execute(
                """
                UPDATE stream_playlists
                SET track_count = (SELECT COUNT(*) FROM stream_tracks WHERE playlist_id = ?)
                WHERE id = ?
                """,
                (playlist_id, playlist_id),
            )
            log.info("playlist.track_removed", playlist_id=playlist_id, track_id=track_id)

        return removed

    def get_playlist(self, playlist_id: str) -> Playlist | None:
        """Fetch a playlist by ID."""
        row = self._db.fetchone(
            "SELECT * FROM stream_playlists WHERE id = ?",
            (playlist_id,),
        )
        if not row:
            return None
        return Playlist(
            id=row["id"],
            name=row["name"],
            track_count=row["track_count"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list_playlists(self) -> list[Playlist]:
        """Return all playlists."""
        rows = self._db.fetchall("SELECT * FROM stream_playlists ORDER BY created_at DESC")
        return [
            Playlist(
                id=r["id"],
                name=r["name"],
                track_count=r["track_count"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    def get_tracks(self, playlist_id: str) -> list[TrackEntry]:
        """Return all tracks in a playlist, ordered by play_order."""
        rows = self._db.fetchall(
            "SELECT * FROM stream_tracks WHERE playlist_id = ? ORDER BY play_order",
            (playlist_id,),
        )
        return [self._row_to_track(r) for r in rows]

    def import_tracks_from_uri(self, playlist_id: str, uris: list[dict[str, Any]]) -> int:
        """Bulk import tracks from a list of track metadata dicts.

        Each dict must have: title, artist, uri, duration_ms.

        Returns:
            Number of tracks imported.
        """
        row = self._db.fetchone(
            "SELECT COALESCE(MAX(play_order), -1) + 1 AS next_order FROM stream_tracks WHERE playlist_id = ?",
            (playlist_id,),
        )
        next_order = row["next_order"] if row else 0

        for i, track in enumerate(uris):
            self._insert_track(playlist_id, track, next_order + i)

        # Update count
        self._db.execute(
            """
            UPDATE stream_playlists
            SET track_count = (SELECT COUNT(*) FROM stream_tracks WHERE playlist_id = ?)
            WHERE id = ?
            """,
            (playlist_id, playlist_id),
        )

        log.info("playlist.bulk_import", playlist_id=playlist_id, count=len(uris))
        return len(uris)

    # ── Daily Schedule ──────────────────────────────────────────

    def get_daily_schedule(
        self,
        account_id: str,
        playlist_id: str | None = None,
        date: str | None = None,
    ) -> DailySchedule:
        """Generate a randomized daily stream schedule for an account.

        The schedule respects:
            - max_streams_per_day from config
            - Random track selection (no repeated pattern day-to-day)
            - ≥31 s minimum per track
            - Jitter between slots

        Args:
            account_id: Account to schedule for.
            playlist_id: Specific playlist, or None to pick the most recent.
            date: YYYY-MM-DD, defaults to today UTC.

        Returns:
            A DailySchedule with ordered slots.
        """
        cfg = get_settings().stream
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Resolve playlist
        if playlist_id is None:
            row = self._db.fetchone(
                "SELECT id FROM stream_playlists ORDER BY created_at DESC LIMIT 1"
            )
            if not row:
                log.warning("schedule.no_playlists", account_id=account_id)
                return DailySchedule(
                    account_id=account_id, date=date, slots=[],
                    total_streams=0, estimated_duration_min=0.0,
                )
            playlist_id = row["id"]

        # Load tracks and check current streams
        tracks = self.get_tracks(playlist_id)
        if not tracks:
            log.warning("schedule.empty_playlist", playlist_id=playlist_id)
            return DailySchedule(
                account_id=account_id, date=date, slots=[],
                total_streams=0, estimated_duration_min=0.0,
            )

        # Check how many streams this account already has today
        acct_row = self._db.fetchone(
            "SELECT streams_today FROM stream_accounts WHERE id = ?",
            (account_id,),
        )
        already_played = acct_row["streams_today"] if acct_row else 0
        remaining = max(0, cfg.max_streams_per_day - already_played)

        if remaining == 0:
            log.info("schedule.quota_full", account_id=account_id)
            return DailySchedule(
                account_id=account_id, date=date, slots=[],
                total_streams=0, estimated_duration_min=0.0,
            )

        # Randomly select tracks for today
        num_streams = min(remaining, len(tracks))
        # Use account_id + date as seed for deterministic but varied daily selection
        seed = hash(f"{account_id}:{date}") % (2**32)
        rng = random.Random(seed)
        selected = rng.sample(tracks, num_streams)

        # Build time-slotted schedule
        slots: list[ScheduleSlot] = []
        current_time = datetime.fromisoformat(f"{date}T00:00:00+00:00")

        for i, track in enumerate(selected):
            play_sec = max(cfg.min_track_seconds, int(track.duration_ms / 1000))
            play_sec = min(play_sec, cfg.min_track_seconds + rng.randint(0, 30))

            slot = ScheduleSlot(
                account_id=account_id,
                track=track,
                slot_index=i,
                estimated_start=current_time,
                estimated_end=current_time.replace(second=current_time.second + play_sec),
            )
            slots.append(slot)

            # Advance clock: play duration + jitter
            jitter = rng.uniform(*cfg.cooldown_jitter_seconds)
            from datetime import timedelta as td
            current_time += td(seconds=play_sec + jitter)

        total_duration = sum(
            (s.estimated_end - s.estimated_start).total_seconds() for s in slots
        ) / 60.0

        schedule = DailySchedule(
            account_id=account_id,
            date=date,
            slots=slots,
            total_streams=len(slots),
            estimated_duration_min=round(total_duration, 1),
        )

        log.info(
            "schedule.generated",
            account_id=account_id,
            date=date,
            streams=schedule.total_streams,
            duration_min=schedule.estimated_duration_min,
        )
        return schedule

    # ── Internals ───────────────────────────────────────────────

    def _insert_track(
        self, playlist_id: str, track: dict[str, Any], order: int
    ) -> str:
        """Insert a track row and return its ID."""
        track_id = str(uuid.uuid4())
        self._db.execute(
            """
            INSERT INTO stream_tracks (id, playlist_id, title, artist, uri, duration_ms, play_order)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                track_id,
                playlist_id,
                track["title"],
                track["artist"],
                track["uri"],
                track["duration_ms"],
                order,
            ),
        )
        return track_id

    @staticmethod
    def _row_to_track(row: dict[str, Any]) -> TrackEntry:
        return TrackEntry(
            id=row["id"],
            playlist_id=row["playlist_id"],
            title=row["title"],
            artist=row["artist"],
            uri=row["uri"],
            duration_ms=row["duration_ms"],
            play_order=row["play_order"],
        )
