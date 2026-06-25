"""
Boost Service — Anti-detection engine.

Provides timing randomization, account warming schedules, and behavioral
diversity patterns so boost traffic blends with organic activity.
"""

from __future__ import annotations

import random
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Timing configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TimingProfile:
    """Timing parameters for a delivery batch."""

    min_delay_s: float = 5.0      # minimum seconds between actions
    max_delay_s: float = 60.0     # maximum seconds between actions
    burst_size: int = 10          # actions per burst before pause
    burst_pause_min_s: float = 30.0
    burst_pause_max_s: float = 180.0
    jitter_pct: float = 0.20      # ±20% random jitter on all timings


SPEED_TIMING_PROFILES: dict[str, TimingProfile] = {
    "slow": TimingProfile(
        min_delay_s=20.0,
        max_delay_s=120.0,
        burst_size=3,
        burst_pause_min_s=120.0,
        burst_pause_max_s=600.0,
        jitter_pct=0.30,
    ),
    "normal": TimingProfile(
        min_delay_s=10.0,
        max_delay_s=60.0,
        burst_size=10,
        burst_pause_min_s=30.0,
        burst_pause_max_s=180.0,
        jitter_pct=0.20,
    ),
    "fast": TimingProfile(
        min_delay_s=5.0,
        max_delay_s=30.0,
        burst_size=20,
        burst_pause_min_s=10.0,
        burst_pause_max_s=60.0,
        jitter_pct=0.15,
    ),
}


# ---------------------------------------------------------------------------
# Behavioral diversity ratios
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ActionMix:
    """Weighted ratio of action types for behavioral diversity.

    Prevents monotonic "all likes" patterns — mixes in organic-looking
    secondary actions alongside the primary boost action.
    """

    primary_weight: float = 0.70      # the ordered action (e.g. likes)
    browse_weight: float = 0.15       # passive browsing / profile visits
    engage_weight: float = 0.10       # secondary engagement (comment, share)
    skip_weight: float = 0.05         # accounts that "see but don't act"


DEFAULT_ACTION_MIXES: dict[str, ActionMix] = {
    "followers":  ActionMix(primary_weight=0.60, browse_weight=0.25, engage_weight=0.10, skip_weight=0.05),
    "likes":      ActionMix(primary_weight=0.70, browse_weight=0.15, engage_weight=0.10, skip_weight=0.05),
    "views":      ActionMix(primary_weight=0.75, browse_weight=0.15, engage_weight=0.05, skip_weight=0.05),
    "comments":   ActionMix(primary_weight=0.60, browse_weight=0.10, engage_weight=0.25, skip_weight=0.05),
    "shares":     ActionMix(primary_weight=0.65, browse_weight=0.15, engage_weight=0.15, skip_weight=0.05),
    "_default":   ActionMix(primary_weight=0.70, browse_weight=0.15, engage_weight=0.10, skip_weight=0.05),
}


# ---------------------------------------------------------------------------
# Account warming schedule
# ---------------------------------------------------------------------------

@dataclass
class WarmingSchedule:
    """Gradual ramp-up to avoid sudden spikes that trigger detection.

    Day 0 = 10% of total, Day 1 = 20%, Day 2 = 30%, Day 3+ = remaining.
    This mimics organic growth patterns.
    """

    ramp_days: int = 3
    ramp_pcts: list[float] = field(default_factory=lambda: [0.10, 0.20, 0.30])

    def daily_quantities(self, total: int, days: int) -> list[int]:
        """Split *total* across *days* with warming ramp.

        Returns a list of per-day quantities. If *days* <= ramp period,
        the ramp is compressed. Leftover from rounding goes to the last day.
        """
        if days <= 0:
            return [total]

        plan: list[int] = []
        remaining = total
        for d in range(min(days, self.ramp_days)):
            pct = self.ramp_pcts[d] if d < len(self.ramp_pcts) else self.ramp_pcts[-1]
            qty = int(total * pct)
            plan.append(qty)
            remaining -= qty

        # Spread remainder over remaining days
        leftover_days = days - len(plan)
        if leftover_days > 0 and remaining > 0:
            per_day = remaining // leftover_days
            for _ in range(leftover_days - 1):
                plan.append(per_day)
                remaining -= per_day
            plan.append(remaining)   # last day gets rounding remainder
        elif leftover_days > 0:
            plan.extend([0] * leftover_days)
        else:
            # All days consumed by ramp, dump remainder into last day
            if plan:
                plan[-1] += remaining
            else:
                plan.append(remaining)

        return plan


# ---------------------------------------------------------------------------
# Core anti-detect engine
# ---------------------------------------------------------------------------


class AntiDetectEngine:
    """Produces timing delays, action-mix decisions, and warming schedules
    so boost fulfillment traffic looks organic to platform detectors.

    Usage::

        engine = AntiDetectEngine()
        delay = engine.next_delay("fast")
        action = engine.pick_action("likes")
        schedule = engine.warming_plan(total=10_000, days=7)
    """

    def __init__(
        self,
        timing_profiles: dict[str, TimingProfile] | None = None,
        action_mixes: dict[str, ActionMix] | None = None,
        warming: WarmingSchedule | None = None,
    ) -> None:
        self._profiles = timing_profiles or SPEED_TIMING_PROFILES
        self._mixes = action_mixes or DEFAULT_ACTION_MIXES
        self._warming = warming or WarmingSchedule()
        self._burst_counter = 0

    # ------------------------------------------------------------------
    # Timing
    # ------------------------------------------------------------------

    def _jitter(self, value: float, pct: float) -> float:
        """Apply ±pct random jitter to *value*."""
        delta = value * pct
        return value + random.uniform(-delta, delta)

    def next_delay(self, speed: str = "normal") -> float:
        """Return next delay in seconds, respecting burst patterns.

        Every ``burst_size`` calls returns a longer burst-pause instead
        of the normal inter-action delay.
        """
        profile = self._profiles.get(speed, self._profiles["normal"])
        self._burst_counter += 1

        if self._burst_counter >= profile.burst_size:
            # Burst pause
            self._burst_counter = 0
            pause = random.uniform(
                profile.burst_pause_min_s, profile.burst_pause_max_s
            )
            delay = self._jitter(pause, profile.jitter_pct)
            logger.debug("Burst pause: %.1fs", delay)
        else:
            # Normal inter-action delay
            delay = random.uniform(profile.min_delay_s, profile.max_delay_s)
            delay = self._jitter(delay, profile.jitter_pct)
            logger.debug("Action delay: %.1fs", delay)

        return max(0.1, delay)  # floor at 100ms

    def delays_sequence(
        self, count: int, speed: str = "normal"
    ) -> list[float]:
        """Pre-compute *count* delay values for batch scheduling."""
        return [self.next_delay(speed) for _ in range(count)]

    # ------------------------------------------------------------------
    # Behavioral diversity
    # ------------------------------------------------------------------

    def pick_action(self, primary_action: str) -> str:
        """Randomly select an action type using weighted diversity.

        Returns one of: ``"primary"``, ``"browse"``, ``"engage"``, ``"skip"``.
        The caller maps these to concrete platform actions.
        """
        mix = self._mixes.get(primary_action, self._mixes["_default"])
        roll = random.random()

        cumulative = 0.0
        for label, weight in [
            ("primary", mix.primary_weight),
            ("browse", mix.browse_weight),
            ("engage", mix.engage_weight),
            ("skip", mix.skip_weight),
        ]:
            cumulative += weight
            if roll < cumulative:
                return label

        return "primary"  # fallback

    def action_sequence(
        self, primary_action: str, count: int
    ) -> list[str]:
        """Generate *count* action decisions for batch planning."""
        return [self.pick_action(primary_action) for _ in range(count)]

    def compute_action_distribution(
        self, primary_action: str, count: int
    ) -> dict[str, int]:
        """Deterministic distribution (for planning, not per-event)."""
        mix = self._mixes.get(primary_action, self._mixes["_default"])
        return {
            "primary": int(count * mix.primary_weight),
            "browse":  int(count * mix.browse_weight),
            "engage":  int(count * mix.engage_weight),
            "skip":    int(count * mix.skip_weight),
        }

    # ------------------------------------------------------------------
    # Warming schedule
    # ------------------------------------------------------------------

    def warming_plan(self, total: int, days: int) -> list[int]:
        """Return per-day quantity plan with gradual ramp-up.

        Args:
            total: total units to deliver.
            days:  delivery window in days.

        Returns:
            List of daily quantities (length == days).
        """
        return self._warming.daily_quantities(total, days)

    # ------------------------------------------------------------------
    # Account rotation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def rotate_accounts(
        account_ids: list[str],
        actions_per_account: int = 50,
    ) -> list[str]:
        """Shuffle and cycle account IDs for rotation.

        Returns an infinite-cycle iterator materialized as a shuffled
        round-robin sequence. Callers should slice to needed length.
        """
        if not account_ids:
            return []
        shuffled = list(account_ids)
        random.shuffle(shuffled)
        return shuffled

    @staticmethod
    def build_session_schedule(
        total_actions: int,
        start_time: datetime | None = None,
        delays: list[float] | None = None,
    ) -> list[datetime]:
        """Build an absolute-time schedule for a batch of actions.

        Useful for pre-computing a drip-feed timeline and handing it
        to a scheduler/executor.
        """
        if start_time is None:
            start_time = datetime.now(timezone.utc)
        if delays is None:
            delays = [random.uniform(5.0, 60.0) for _ in range(total_actions)]

        schedule: list[datetime] = []
        current = start_time
        for i in range(total_actions):
            schedule.append(current)
            if i < len(delays):
                current += timedelta(seconds=delays[i])
            else:
                current += timedelta(seconds=random.uniform(10.0, 45.0))

        return schedule
