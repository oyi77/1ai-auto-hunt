"""Flash sale countdown timer with NTP synchronization.

Provides sub-second precision countdown to a target time, supporting
configurable pre-trigger offsets (e.g. fire checkout 200ms before the
sale officially starts).
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# NTP helpers
# ---------------------------------------------------------------------------

_NTP_EPOCH_OFFSET: float = 2208988800.0  # seconds between 1900-01-01 and 1970-01-01
_NTP_DEFAULT_SERVER: str = "pool.ntp.org"
_NTP_PORT: int = 123
_NTP_PACKET_SIZE: int = 48


def _ntp_request(server: str = _NTP_DEFAULT_SERVER, timeout: float = 3.0) -> float:
    """Send a single NTP request and return the server timestamp (unix epoch).

    Returns
    -------
    float
        NTP server time as a unix timestamp.

    Raises
    ------
    OSError
        If the UDP request fails or times out.
    """
    # Build a minimal NTP v3 request
    packet = bytearray(_NTP_PACKET_SIZE)
    packet[0] = 0x1B  # LI=0, VN=3, Mode=3 (client)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(bytes(packet), (server, _NTP_PORT))
        data, _ = sock.recvfrom(_NTP_PACKET_SIZE)

    # Extract the transmit timestamp (bytes 40–47)
    tx_sec, tx_frac = struct.unpack("!II", data[40:48])
    tx_time = (tx_sec - _NTP_EPOCH_OFFSET) + (tx_frac / (2**32))
    return tx_time


def get_ntp_offset(
    server: str = _NTP_DEFAULT_SERVER,
    attempts: int = 3,
    timeout: float = 3.0,
) -> float:
    """Calculate the offset between local clock and NTP server.

    Parameters
    ----------
    server : str
        NTP server hostname.
    attempts : int
        Number of requests to average.
    timeout : float
        Socket timeout per request.

    Returns
    -------
    float
        Offset in seconds (server_time − local_time).
        Positive means local clock is behind.
    """
    offsets: list[float] = []
    for _ in range(attempts):
        try:
            t_before = time.time()
            ntp_time = _ntp_request(server, timeout)
            t_after = time.time()
            round_trip = t_after - t_before
            # Estimate one-way latency
            offset = ntp_time - (t_before + round_trip / 2.0)
            offsets.append(offset)
        except OSError:
            logger.warning("NTP request to %s failed, skipping attempt", server)

    if not offsets:
        logger.warning("All NTP attempts failed; using offset=0")
        return 0.0

    # Use median to discard outliers
    offsets.sort()
    return offsets[len(offsets) // 2]


# ---------------------------------------------------------------------------
# Time formatting
# ---------------------------------------------------------------------------

def format_hms(seconds: float) -> str:
    """Format a duration in seconds as ``HH:MM:SS``.

    Negative values are shown as ``-HH:MM:SS``.
    """
    sign = "-" if seconds < 0 else ""
    total = abs(int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{sign}{h:02d}:{m:02d}:{s:02d}"


def format_hms_ms(seconds: float) -> str:
    """Format a duration as ``HH:MM:SS.mmm`` (millisecond precision)."""
    sign = "-" if seconds < 0 else ""
    total_ms = round(abs(seconds) * 1000)
    h, rem_ms = divmod(total_ms, 3_600_000)
    m, rem_ms = divmod(rem_ms, 60_000)
    s, ms = divmod(rem_ms, 1000)
    return f"{sign}{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


# ---------------------------------------------------------------------------
# FlashSaleTimer
# ---------------------------------------------------------------------------

@dataclass
class TimerSnapshot:
    """A point-in-time snapshot of the timer state."""
    target_utc: datetime
    now_utc: datetime
    remaining_sec: float
    formatted: str
    ntp_offset: float
    triggered: bool


class FlashSaleTimer:
    """Countdown timer that triggers at a target UTC time.

    Supports:
    - ``sub_ms`` offset: fire *before* the target (e.g. 200ms early).
    - NTP clock sync to compensate for local drift.
    - Async and sync countdown loops with configurable tick interval.

    Parameters
    ----------
    target_time : datetime
        The UTC time the flash sale starts. If timezone-naive, assumed UTC.
    sub_ms : int
        Milliseconds to subtract from target (trigger early). Default 0.
    ntp_server : str
        NTP server hostname. Set to ``""`` to disable NTP sync.
    ntp_attempts : int
        Number of NTP samples to average per sync call.
    tick_interval : float
        Seconds between countdown prints / callback invocations.

    Usage::

        target = datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc)
        timer = FlashSaleTimer(target, sub_ms=200)
        await timer.countdown(callback=my_checkout_fn)
    """

    def __init__(
        self,
        target_time: datetime,
        sub_ms: int = 0,
        ntp_server: str = _NTP_DEFAULT_SERVER,
        ntp_attempts: int = 3,
        tick_interval: float = 1.0,
    ) -> None:
        if target_time.tzinfo is None:
            target_time = target_time.replace(tzinfo=timezone.utc)
        self._target_utc = target_time
        self._sub_offset = timedelta(milliseconds=sub_ms)
        self._effective_target = target_time - self._sub_offset
        self._ntp_server = ntp_server
        self._ntp_attempts = ntp_attempts
        self._tick_interval = tick_interval
        self._ntp_offset: float = 0.0

    # -- properties ---------------------------------------------------------

    @property
    def target_utc(self) -> datetime:
        """The original target time."""
        return self._target_utc

    @property
    def effective_target(self) -> datetime:
        """Target minus the sub-ms offset (when we actually fire)."""
        return self._effective_target

    @property
    def ntp_offset(self) -> float:
        """Last computed NTP offset in seconds."""
        return self._ntp_offset

    # -- sync ---------------------------------------------------------------

    def sync_ntp(self) -> float:
        """Perform an NTP sync and store the offset.

        Returns
        -------
        float
            The computed offset in seconds.
        """
        if not self._ntp_server:
            return 0.0
        self._ntp_offset = get_ntp_offset(
            server=self._ntp_server,
            attempts=self._ntp_attempts,
        )
        logger.info(
            "NTP sync complete: offset=%.3fms (server=%s)",
            self._ntp_offset * 1000,
            self._ntp_server,
        )
        return self._ntp_offset

    def now_utc(self) -> datetime:
        """Current UTC time adjusted by the NTP offset."""
        return datetime.now(timezone.utc) + timedelta(seconds=self._ntp_offset)

    def remaining(self) -> float:
        """Seconds until effective target (negative if past)."""
        delta = self._effective_target - self.now_utc()
        return delta.total_seconds()

    def snapshot(self) -> TimerSnapshot:
        """Capture the current timer state."""
        now = self.now_utc()
        rem = (self._effective_target - now).total_seconds()
        return TimerSnapshot(
            target_utc=self._target_utc,
            now_utc=now,
            remaining_sec=rem,
            formatted=format_hms(rem),
            ntp_offset=self._ntp_offset,
            triggered=rem <= 0,
        )

    # -- async countdown ----------------------------------------------------

    async def countdown(
        self,
        callback: Callable[[], object] | None = None,
        on_tick: Callable[[TimerSnapshot], None] | None = None,
    ) -> TimerSnapshot:
        """Block until the effective target, then invoke *callback*.

        Parameters
        ----------
        callback : callable, optional
            Invoked (once) when the effective target is reached.
        on_tick : callable, optional
            Called every ``tick_interval`` seconds with a :class:`TimerSnapshot`.

        Returns
        -------
        TimerSnapshot
            The snapshot at trigger time.
        """
        # Pre-sync NTP
        self.sync_ntp()

        logger.info(
            "Timer target=%s effective=%s sub_ms=%d",
            self._target_utc.isoformat(),
            self._effective_target.isoformat(),
            int(self._sub_offset.total_seconds() * 1000),
        )

        while True:
            snap = self.snapshot()
            if on_tick:
                on_tick(snap)

            if snap.triggered:
                break

            # Adaptive sleep: fine-grained near the target
            rem = snap.remaining_sec
            if rem > 60:
                sleep_time = min(self._tick_interval, 1.0)
            elif rem > 5:
                sleep_time = min(self._tick_interval, 0.5)
            elif rem > 1:
                sleep_time = 0.1
            else:
                sleep_time = 0.01  # 10ms granularity in the final second

            await asyncio.sleep(sleep_time)

        # Re-sync right before firing for maximum accuracy
        self.sync_ntp()
        final = self.snapshot()
        logger.info("FLASH SALE TRIGGERED at %s", final.now_utc.isoformat())

        if callback:
            result = callback()
            if asyncio.iscoroutine(result):
                await result

        return final

    # -- sync countdown -----------------------------------------------------

    def countdown_sync(
        self,
        callback: Callable[[], object] | None = None,
        on_tick: Callable[[TimerSnapshot], None] | None = None,
    ) -> TimerSnapshot:
        """Blocking (non-async) version of :meth:`countdown`."""
        self.sync_ntp()
        logger.info(
            "Timer (sync) target=%s effective=%s",
            self._target_utc.isoformat(),
            self._effective_target.isoformat(),
        )

        while True:
            snap = self.snapshot()
            if on_tick:
                on_tick(snap)

            if snap.triggered:
                break

            rem = snap.remaining_sec
            if rem > 60:
                time.sleep(min(self._tick_interval, 1.0))
            elif rem > 5:
                time.sleep(min(self._tick_interval, 0.5))
            elif rem > 1:
                time.sleep(0.1)
            else:
                time.sleep(0.01)

        self.sync_ntp()
        final = self.snapshot()
        logger.info("FLASH SALE TRIGGERED (sync) at %s", final.now_utc.isoformat())

        if callback:
            callback()

        return final
