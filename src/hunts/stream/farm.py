"""Streaming Farm — Spotify/Apple Music royalty generator.

Orchestrates a pool of phonefarm device accounts against a playlist,
playing each track for ≥31 s (royalty threshold) with random selection
and anti-detection jitter.

Anti-detection rules:
    - ≤200 streams per account per day (configurable)
    - Random track selection per device session
    - 2–8 s jitter between tracks
    - Proxy rotation per account
    - Daily counter resets at midnight UTC

Integrations:
    - 1ai-phonefarm: device execution backbone (ADB, template engine)
    - 1proxy: per-account proxy rotation
"""

from __future__ import annotations

import asyncio
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from src.core.config import get_settings, StreamConfig
from src.core.db import get_db, Database
from src.core.logger import get_logger

log = get_logger(__name__)


# ── Data Models ──────────────────────────────────────────────────

@dataclass(frozen=True)
class StreamAccount:
    """A streaming platform account bound to a device + proxy."""
    id: str
    platform: str
    username: str
    password_enc: str
    proxy_id: str | None
    device_id: str | None
    status: str
    streams_today: int
    streams_total: int
    last_stream_at: datetime | None

    @property
    def daily_quota_remaining(self) -> int:
        cfg = get_settings().stream
        return max(0, cfg.max_streams_per_day - self.streams_today)

    @property
    def is_usable(self) -> bool:
        return self.status == "active" and self.daily_quota_remaining > 0


@dataclass(frozen=True)
class Track:
    """A single track in a playlist."""
    id: str
    playlist_id: str
    title: str
    artist: str
    uri: str
    duration_ms: int
    play_order: int

    @property
    def duration_sec(self) -> float:
        return self.duration_ms / 1000.0


@dataclass
class StreamResult:
    """Outcome of a single stream attempt."""
    account_id: str
    track_id: str
    success: bool
    duration_sec: int
    error: str | None = None
    played_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Phonefarm Template ──────────────────────────────────────────

STREAM_TEMPLATE = """
# phonefarm template: spotify_stream
# Plays one track for {{ duration_sec }}s on the bound device.

- action: launch
  package: {{ app_package }}
- action: wait
  duration: 3
- action: login
  username: {{ username }}
  password: {{ password }}
  when: logged_out
- action: navigate
  uri: {{ track_uri }}
- action: wait
  duration: 2
- action: tap
  selector: play_button
- action: wait
  duration: {{ duration_sec }}
- action: tap
  selector: pause_button
"""


class StreamingFarm:
    """Orchestrates the streaming farm across accounts and playlists.

    Args:
        db: Database instance (defaults to singleton).
        cfg: Stream configuration (defaults to settings).

    Usage:
        farm = StreamingFarm()
        farm.load_accounts("accounts.csv")
        results = await farm.start(playlist_id="my_playlist", cycles=5)
    """

    def __init__(self, db: Database | None = None, cfg: StreamConfig | None = None) -> None:
        self._db = db or get_db()
        self._cfg = cfg or get_settings().stream
        self._running = False

    # ── Account Management ──────────────────────────────────────

    def load_accounts(self, source: str) -> int:
        """Load accounts from CSV or JSON file.

        Each row/entry must have: platform, username, password, proxy_id (optional).

        Returns:
            Number of accounts loaded.
        """
        from pathlib import Path

        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"Account source not found: {source}")

        loaded = 0
        if path.suffix == ".csv":
            import csv
            with open(path, newline="") as f:
                for row in csv.DictReader(f):
                    self._insert_account(row)
                    loaded += 1
        elif path.suffix == ".json":
            import json
            data = json.loads(path.read_text())
            for entry in data:
                self._insert_account(entry)
                loaded += 1
        else:
            raise ValueError(f"Unsupported format: {path.suffix} (use .csv or .json)")

        log.info("farm.accounts_loaded", count=loaded, source=source)
        return loaded

    def _insert_account(self, row: dict[str, Any]) -> str:
        """Insert a single account into the database."""
        account_id = str(uuid.uuid4())
        self._db.execute(
            """
            INSERT INTO stream_accounts (id, platform, username, password_enc, proxy_id, device_id, status)
            VALUES (?, ?, ?, ?, ?, ?, 'active')
            """,
            (
                account_id,
                row.get("platform", "spotify"),
                row["username"],
                row["password"],
                row.get("proxy_id"),
                row.get("device_id"),
            ),
        )
        return account_id

    def get_accounts(self, status: str = "active") -> list[StreamAccount]:
        """Return accounts matching status."""
        rows = self._db.fetchall(
            "SELECT * FROM stream_accounts WHERE status = ? ORDER BY streams_today ASC",
            (status,),
        )
        return [self._row_to_account(r) for r in rows]

    @staticmethod
    def _row_to_account(row: dict[str, Any]) -> StreamAccount:
        return StreamAccount(
            id=row["id"],
            platform=row["platform"],
            username=row["username"],
            password_enc=row["password_enc"],
            proxy_id=row.get("proxy_id"),
            device_id=row.get("device_id"),
            status=row["status"],
            streams_today=row["streams_today"],
            streams_total=row["streams_total"],
            last_stream_at=(
                datetime.fromisoformat(row["last_stream_at"]) if row.get("last_stream_at") else None
            ),
        )

    # ── Core Execution ──────────────────────────────────────────

    async def start(self, playlist_id: str, cycles: int = 1) -> list[StreamResult]:
        """Run the streaming farm for the given playlist.

        Each cycle assigns every usable account a batch of tracks.
        Tracks are randomly selected from the playlist to avoid pattern
        detection.

        Args:
            playlist_id: ID of the playlist to play.
            cycles: Number of full account rotation cycles.

        Returns:
            List of StreamResult for every play attempt.
        """
        self._running = True
        all_results: list[StreamResult] = []

        for cycle in range(cycles):
            if not self._running:
                log.info("farm.stopped", cycle=cycle)
                break

            accounts = [a for a in self.get_accounts() if a.is_usable]
            if not accounts:
                log.warning("farm.no_available_accounts", cycle=cycle)
                break

            log.info("farm.cycle_start", cycle=cycle, accounts=len(accounts))
            batch_results = await self._run_cycle(accounts, playlist_id)
            all_results.extend(batch_results)

            # Reset daily counters at midnight UTC
            self._maybe_reset_daily_counters()

        self._running = False
        log.info(
            "farm.complete",
            total_streams=len(all_results),
            successful=sum(1 for r in all_results if r.success),
            failed=sum(1 for r in all_results if not r.success),
        )
        return all_results

    def stop(self) -> None:
        """Gracefully stop the farm after the current cycle."""
        self._running = False
        log.info("farm.stop_requested")

    async def _run_cycle(
        self, accounts: list[StreamAccount], playlist_id: str
    ) -> list[StreamResult]:
        """Execute one cycle: each account streams a random batch of tracks."""
        tracks = self._load_tracks(playlist_id)
        if not tracks:
            log.error("farm.empty_playlist", playlist_id=playlist_id)
            return []

        results: list[StreamResult] = []
        # Process accounts in batches to limit concurrency
        batch_size = self._cfg.devices_per_batch

        for batch_start in range(0, len(accounts), batch_size):
            batch = accounts[batch_start:batch_start + batch_size]
            tasks = [
                self._stream_account(account, tracks)
                for account in batch
            ]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for account, outcome in zip(batch, batch_results):
                if isinstance(outcome, Exception):
                    log.error("farm.account_error", account_id=account.id, error=str(outcome))
                    results.append(StreamResult(
                        account_id=account.id,
                        track_id="",
                        success=False,
                        duration_sec=0,
                        error=str(outcome),
                    ))
                else:
                    results.extend(outcome)

        return results

    async def _stream_account(
        self, account: StreamAccount, tracks: list[Track]
    ) -> list[StreamResult]:
        """Stream a random selection of tracks on one account.

        Selects min(remaining_quota, len(tracks)) tracks randomly.
        Each play lasts ≥ min_track_seconds for royalty qualification.
        """
        results: list[StreamResult] = []
        quota = account.daily_quota_remaining
        if quota <= 0:
            return results

        # Random selection — don't play the same order every session
        selected = random.sample(tracks, min(quota, len(tracks)))

        for track in selected:
            if not self._running:
                break

            play_duration = self._calculate_play_duration(track)
            result = await self._play_track(account, track, play_duration)
            results.append(result)

            if result.success:
                self._record_stream(account, track, play_duration)

            # Anti-detection jitter between tracks
            jitter = random.uniform(*self._cfg.cooldown_jitter_seconds)
            await asyncio.sleep(jitter)

        return results

    async def _play_track(
        self, account: StreamAccount, track: Track, duration_sec: int
    ) -> StreamResult:
        """Execute a single track play via phonefarm template.

        In production this sends the template to 1ai-phonefarm's
        execution engine. Here we render the template and simulate
        the async execution.
        """
        rendered = self._render_template(account, track, duration_sec)

        log.info(
            "farm.play_track",
            account_id=account.id,
            track=f"{track.artist} — {track.title}",
            duration=duration_sec,
        )

        try:
            # ── Production: send to phonefarm executor ──
            # await self._phonefarm.execute(
            #     device_id=account.device_id,
            #     template=rendered,
            #     proxy=account.proxy_id,
            #     timeout=duration_sec + 30,
            # )

            # Simulate streaming duration
            await asyncio.sleep(min(duration_sec, 0.1))

            return StreamResult(
                account_id=account.id,
                track_id=track.id,
                success=True,
                duration_sec=duration_sec,
            )
        except Exception as exc:
            log.error(
                "farm.play_failed",
                account_id=account.id,
                track_id=track.id,
                error=str(exc),
            )
            return StreamResult(
                account_id=account.id,
                track_id=track.id,
                success=False,
                duration_sec=0,
                error=str(exc),
            )

    def _calculate_play_duration(self, track: Track) -> int:
        """Return play duration in seconds, respecting minimum threshold.

        The 31-second minimum ensures the stream counts as a royalty-
        qualifying play on Spotify. We add a small random buffer above
        the minimum to vary timing patterns.
        """
        min_sec = self._cfg.min_track_seconds
        actual_sec = int(track.duration_sec)

        if actual_sec <= min_sec:
            return actual_sec  # Short track — play in full

        # Play between min_track_seconds and min(actual, min+30)
        # with some randomness to look organic
        upper = min(actual_sec, min_sec + 30)
        return random.randint(min_sec, upper)

    def _render_template(
        self, account: StreamAccount, track: Track, duration_sec: int
    ) -> str:
        """Render the phonefarm stream template with account/track data."""
        app_package = {
            "spotify": "com.spotify.music",
            "apple_music": "com.apple.android.music",
            "youtube_music": "com.google.android.apps.youtube.music",
        }.get(account.platform, "com.spotify.music")

        return STREAM_TEMPLATE.replace("{{ app_package }}", app_package) \
            .replace("{{ username }}", account.username) \
            .replace("{{ password }}", account.password_enc) \
            .replace("{{ track_uri }}", track.uri) \
            .replace("{{ duration_sec }}", str(duration_sec))

    # ── Database Helpers ────────────────────────────────────────

    def _load_tracks(self, playlist_id: str) -> list[Track]:
        """Load tracks for a playlist from the database."""
        rows = self._db.fetchall(
            "SELECT * FROM stream_tracks WHERE playlist_id = ? ORDER BY play_order",
            (playlist_id,),
        )
        return [
            Track(
                id=r["id"],
                playlist_id=r["playlist_id"],
                title=r["title"],
                artist=r["artist"],
                uri=r["uri"],
                duration_ms=r["duration_ms"],
                play_order=r["play_order"],
            )
            for r in rows
        ]

    def _record_stream(self, account: StreamAccount, track: Track, duration_sec: int) -> None:
        """Log a successful stream and update account counters."""
        self._db.execute(
            """
            INSERT INTO stream_logs (account_id, track_id, duration_sec, success)
            VALUES (?, ?, ?, 1)
            """,
            (account.id, track.id, duration_sec),
        )
        self._db.execute(
            """
            UPDATE stream_accounts
            SET streams_today = streams_today + 1,
                streams_total = streams_total + 1,
                last_stream_at = datetime('now'),
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (account.id,),
        )

    def _maybe_reset_daily_counters(self) -> None:
        """Reset streams_today for all accounts at UTC midnight.

        Checks if any account has streams_today > 0 and last_stream_at
        is from a previous UTC date.
        """
        today = datetime.now(timezone.utc).date().isoformat()
        self._db.execute(
            """
            UPDATE stream_accounts
            SET streams_today = 0, updated_at = datetime('now')
            WHERE streams_today > 0
              AND date(last_stream_at) < ?
            """,
            (today,),
        )

    # ── Stats ───────────────────────────────────────────────────

    def revenue_report(self, month: str | None = None) -> dict[str, Any]:
        """Generate a revenue report for a given month.

        Args:
            month: YYYY-MM format. Defaults to current month.

        Returns:
            Dict with total_streams, unique_accounts, avg_streams_per_account,
            estimated_revenue_usd.
        """
        if month is None:
            month = datetime.now(timezone.utc).strftime("%Y-%m")

        stats = self._db.fetchone(
            """
            SELECT
                COUNT(*) as total_streams,
                COUNT(DISTINCT account_id) as unique_accounts,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful
            FROM stream_logs
            WHERE strftime('%%Y-%%m', played_at) = ?
            """,
            (month,),
        ) or {"total_streams": 0, "unique_accounts": 0, "successful": 0}

        # Spotify pays ~$0.003–0.005 per stream
        estimated_revenue = stats["successful"] * 0.004
        avg_per_account = (
            stats["successful"] / stats["unique_accounts"]
            if stats["unique_accounts"] > 0
            else 0
        )

        return {
            "month": month,
            "total_streams": stats["total_streams"],
            "successful_streams": stats["successful"],
            "unique_accounts": stats["unique_accounts"],
            "avg_streams_per_account": round(avg_per_account, 1),
            "estimated_revenue_usd": round(estimated_revenue, 2),
        }
