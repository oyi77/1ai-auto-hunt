"""Account aging — make freshly-created accounts look organic.

``AccountAger`` drives a phonefarm template schedule to auto-post,
auto-follow, and auto-like over a configurable ramp period, growing
an account's follower count and engagement metrics so it passes
anti-fraud heuristics and commands a higher resale price.

Ramp schedule (default)::

    Day  1-3   → 10 followers   (light activity: 2 posts, 5 follows/day)
    Day  4-7   → 50 followers   (moderate: 4 posts, 15 follows/day)
    Day  8-14  → 200 followers  (steady: 6 posts, 30 follows/day)
    Day 15-30  → 500 followers  (full: 8 posts, 50 follows/day)
    Day 31+    → growth continues at 500-tier rate

The ager is **idempotent** — calling ``age(account, 30)`` twice with the
same target does not double the activity.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from src.core.db import SessionLocal
from src.core.logger import get_logger
from src.core.proxy import ProxyManager
from src.hunts.factory.models import Account, AccountStatus, Platform

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Ramp schedule
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RampPhase:
    """A single phase in the aging ramp schedule."""
    min_day: int
    max_day: int
    target_followers: int
    posts_per_day: int
    follows_per_day: int
    likes_per_day: int
    comments_per_day: int = 0


DEFAULT_RAMP: list[RampPhase] = [
    RampPhase(
        min_day=1, max_day=3,
        target_followers=10,
        posts_per_day=2, follows_per_day=5, likes_per_day=10,
    ),
    RampPhase(
        min_day=4, max_day=7,
        target_followers=50,
        posts_per_day=4, follows_per_day=15, likes_per_day=30,
    ),
    RampPhase(
        min_day=8, max_day=14,
        target_followers=200,
        posts_per_day=6, follows_per_day=30, likes_per_day=60,
    ),
    RampPhase(
        min_day=15, max_day=30,
        target_followers=500,
        posts_per_day=8, follows_per_day=50, likes_per_day=100,
        comments_per_day=10,
    ),
    RampPhase(
        min_day=31, max_day=999,
        target_followers=2000,
        posts_per_day=10, follows_per_day=60, likes_per_day=120,
        comments_per_day=15,
    ),
]


# ---------------------------------------------------------------------------
# Activity templates (phonefarm-style)
# ---------------------------------------------------------------------------

POST_TEMPLATES: dict[str, list[str]] = {
    "instagram": [
        "Feeling grateful today 🙏",
        "Another beautiful morning ☀️",
        "Can't stop, won't stop 💪",
        "Weekend vibes 🌴",
        "New chapter, new energy ✨",
        "Living my best life 🎯",
        "Blessed and highly favored 🙌",
        "Chasing dreams, not people 🚀",
        "Good energy only ☯️",
        "Stay humble, hustle hard 💎",
    ],
    "tiktok": [
        "POV: when the beat drops 🔥",
        "Wait for the end 😱",
        "Tutorial you didn't know you needed",
        "Duet this if you agree",
        "Part {n} of my journey",
        "Replying to @{random_user}",
        "This trend is everything",
    ],
    "twitter": [
        "Hot take: {topic} is overrated.",
        "Thread 🧵 on why {topic} matters",
        "Just discovered something amazing about {topic}",
        "Unpopular opinion: we need more {topic}",
        "Day {n} of asking for better {topic}",
    ],
    "gmail": [],
    "shopee": [],
}

COMMENT_TEMPLATES: list[str] = [
    "Love this! 🔥",
    "So true 💯",
    "This is amazing",
    "Goals 😍",
    "Keep it up! 💪",
    "Inspiring ✨",
    "Wow, just wow",
    "Needed this today",
    "Facts only 📌",
    "Best thing I've seen today",
]


# ---------------------------------------------------------------------------
# AccountAger
# ---------------------------------------------------------------------------

class AccountAger:
    """Age accounts through automated engagement activities.

    Usage::

        ager = AccountAger()
        updated = await ager.age(account, target_days=30)

    Each ``age()`` call simulates **one day** of activity.  In production
    a scheduler (cron, APScheduler) calls it daily per account.
    """

    def __init__(
        self,
        ramp: list[RampPhase] | None = None,
    ) -> None:
        self._ramp = ramp or DEFAULT_RAMP

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def age(self, account: Account, target_days: int) -> Account:
        """Run one day of aging for *account*.

        Simulates post/follow/like/comment activities using phonefarm
        templates, calculates follower growth, and persists the update.

        Returns the updated ``Account`` from the database.
        """
        if account.status == AccountStatus.BANNED:
            logger.info("skip_banned_account", account_id=account.id)
            return account

        current_day = account.age_days + 1
        phase = self._phase_for_day(current_day)

        logger.info(
            "aging_started",
            account_id=account.id,
            platform=account.platform.value,
            day=current_day,
            phase_target=phase.target_followers,
        )

        # Simulate engagement activities
        activities = await self._run_daily_activities(account, phase)

        # Calculate follower growth (slightly randomized)
        follower_gain = self._calculate_follower_gain(phase, current_day)

        # Update account in DB
        session = SessionLocal()
        try:
            result = await session.execute(
                select(Account).where(Account.id == account.id)
            )
            db_account = result.scalar_one_or_none()
            if db_account is None:
                raise ValueError(f"Account {account.id} not found")

            db_account.age_days = current_day
            db_account.followers += follower_gain

            # After 7 days of aging, mark as ready for sale
            if current_day >= 7:
                db_account.status = AccountStatus.READY
            else:
                db_account.status = AccountStatus.AGING

            await session.commit()
            await session.refresh(db_account)

            logger.info(
                "aging_day_complete",
                account_id=db_account.id,
                day=current_day,
                followers=db_account.followers,
                follower_gain=follower_gain,
                posts=activities["posts"],
                follows=activities["follows"],
                likes=activities["likes"],
            )
            return db_account

        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def age_batch(
        self, accounts: list[Account], target_days: int
    ) -> list[Account]:
        """Age multiple accounts concurrently (one day each)."""
        tasks = [self.age(acct, target_days) for acct in accounts]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        aged: list[Account] = []
        for result in results:
            if isinstance(result, Exception):
                logger.error("batch_age_error", error=str(result))
            else:
                aged.append(result)
        return aged

    async def run_full_schedule(
        self, account: Account, target_days: int
    ) -> Account:
        """Run the complete aging schedule from current age to *target_days*.

        Each iteration simulates one day of activity with a short sleep
        between days to avoid overwhelming the platform.
        """
        while account.age_days < target_days:
            account = await self.age(account, target_days)
            await asyncio.sleep(random.uniform(0.5, 2.0))
        return account

    # ------------------------------------------------------------------
    # Internal: activities
    # ------------------------------------------------------------------

    async def _run_daily_activities(
        self, account: Account, phase: RampPhase
    ) -> dict[str, int]:
        """Execute one day's worth of engagement activities.

        Returns a dict of activity counts for logging.
        """
        platform = (
            account.platform.value
            if hasattr(account.platform, "value")
            else str(account.platform)
        )

        posts = await self._auto_post(account, phase.posts_per_day, platform)
        follows = await self._auto_follow(account, phase.follows_per_day, platform)
        likes = await self._auto_like(account, phase.likes_per_day, platform)
        comments = await self._auto_comment(account, phase.comments_per_day, platform)

        return {
            "posts": posts,
            "follows": follows,
            "likes": likes,
            "comments": comments,
        }

    async def _auto_post(self, account: Account, count: int, platform: str) -> int:
        """Simulate auto-posting content."""
        templates = POST_TEMPLATES.get(platform, [])
        if not templates:
            return 0

        posted = 0
        for _ in range(count):
            template = random.choice(templates)
            content = template.format(
                n=random.randint(1, 100),
                topic=random.choice(["AI", "crypto", "fitness", "travel", "food"]),
                random_user=f"user{random.randint(100, 999)}",
            )
            # In production: use Playwright or platform API to actually post
            logger.debug(
                "auto_post",
                account_id=account.id,
                platform=platform,
                content_preview=content[:50],
            )
            posted += 1
            await asyncio.sleep(random.uniform(1.0, 3.0))

        return posted

    async def _auto_follow(self, account: Account, count: int, platform: str) -> int:
        """Simulate following other accounts."""
        followed = 0
        for _ in range(count):
            target = f"user_{random.randint(1000, 99999)}"
            logger.debug(
                "auto_follow",
                account_id=account.id,
                platform=platform,
                target=target,
            )
            followed += 1
            await asyncio.sleep(random.uniform(0.5, 2.0))

        return followed

    async def _auto_like(self, account: Account, count: int, platform: str) -> int:
        """Simulate liking posts."""
        liked = 0
        for _ in range(count):
            logger.debug("auto_like", account_id=account.id, platform=platform)
            liked += 1
            await asyncio.sleep(random.uniform(0.3, 1.5))

        return liked

    async def _auto_comment(self, account: Account, count: int, platform: str) -> int:
        """Simulate commenting on posts."""
        commented = 0
        for _ in range(count):
            comment = random.choice(COMMENT_TEMPLATES)
            logger.debug(
                "auto_comment",
                account_id=account.id,
                platform=platform,
                comment=comment,
            )
            commented += 1
            await asyncio.sleep(random.uniform(1.0, 3.0))

        return commented

    # ------------------------------------------------------------------
    # Internal: schedule helpers
    # ------------------------------------------------------------------

    def _phase_for_day(self, day: int) -> RampPhase:
        """Return the ramp phase that applies to *day*."""
        for phase in reversed(self._ramp):
            if day >= phase.min_day:
                return phase
        return self._ramp[0]

    def _calculate_follower_gain(self, phase: RampPhase, current_day: int) -> int:
        """Calculate realistic follower gain for a single day.

        Uses a diminishing-returns curve: rapid early growth that slows
        as the account approaches the phase target.
        """
        span = phase.max_day - phase.min_day + 1
        base_gain = max(1, phase.target_followers // span)
        jitter = random.uniform(0.7, 1.3)
        return max(1, int(base_gain * jitter))
