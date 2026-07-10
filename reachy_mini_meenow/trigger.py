"""Daily trigger-time math, ported bit-for-bit from meenow's ``src/trigger-core.mjs``.

The pseudo-random daily "trigger time" is derived from the calendar date so that
this app fires at the exact same moment the meenow PWA prompts its users. The
JavaScript original relies on 32-bit unsigned integer semantics (``Math.imul`` and
``>>> 0``); every operation here is masked to 32 bits to reproduce it identically.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

WINDOW_START_HOUR = 9  # 9:00 AM local
WINDOW_MINUTES = 12 * 60  # 9:00 AM – 9:00 PM = 720 min
_MASK = 0xFFFFFFFF


def djb2(s: str) -> int:
    """djb2 hash with 32-bit unsigned wraparound (``Math.imul(h, 33) ^ c``)."""
    h = 5381
    for ch in s:
        h = (((h * 33) & _MASK) ^ ord(ch)) & _MASK
    return h


def xorshift32(seed: int) -> int:
    """One xorshift32 round. 0 is the algorithm's fixed point, so it is guarded."""
    x = seed if seed != 0 else 2463534242
    x = (x ^ ((x << 13) & _MASK)) & _MASK
    x = (x ^ (x >> 17)) & _MASK
    x = (x ^ ((x << 5) & _MASK)) & _MASK
    return x & _MASK


def trigger_offset_minutes(date_str: str) -> int:
    """Minutes past ``WINDOW_START_HOUR`` of the trigger for calendar day ``date_str``.

    Three xorshift rounds are load-bearing: consecutive days' date strings differ
    only in the last character, and a single round leaves their djb2 hashes too
    close to spread across the 720-minute window (every day in June 2026 lands at
    ~16:50 with one round).
    """
    x = djb2(date_str)
    x = xorshift32(x)
    x = xorshift32(x)
    x = xorshift32(x)
    rand = x / 0x100000000  # uniform [0, 1); denominator is a power of two -> exact
    return math.floor(rand * WINDOW_MINUTES)


def local_date_string(dt: datetime) -> str:
    """Zero-padded ``YYYY-MM-DD`` from the wall-clock parts of ``dt``.

    Mirrors meenow ``timer.ts`` ``localDateString`` — the padding must match, since
    djb2 is seeded by this exact string.
    """
    return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}"


def _epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


class TriggerClock:
    """Computes trigger instants in a given timezone.

    ``tz`` may be a ``tzinfo`` (aware mode, mirrors trigger-core's IANA helpers) or
    ``None`` (naive local mode, mirrors ``timer.ts`` which uses the browser's local
    zone). Since this app runs co-located with the Reachy Mini Lite host, naive
    local matches the user's device exactly.
    """

    def __init__(self, tz=None):
        self._tz = tz

    def _now(self) -> datetime:
        return datetime.now(self._tz)

    def _trigger_for_date(self, dt: datetime) -> datetime:
        """The trigger instant for the calendar day of ``dt``."""
        offset = trigger_offset_minutes(local_date_string(dt))
        base = dt.replace(
            hour=WINDOW_START_HOUR, minute=0, second=0, microsecond=0
        )
        # Minute overflow rolls into hours automatically (offset 719 -> 20:59),
        # reproducing new Date(y, mo, d, 9, offsetMinutes) in timer.ts.
        return base + timedelta(minutes=offset)

    def today_trigger_ms(self, now_ms: int | None = None) -> int:
        now = self._at(now_ms)
        return _epoch_ms(self._trigger_for_date(now))

    def last_trigger_ms(self, now_ms: int | None = None) -> int:
        now = self._at(now_ms)
        today = self._trigger_for_date(now)
        if now >= today:
            return _epoch_ms(today)
        return _epoch_ms(self._trigger_for_date(now - timedelta(days=1)))

    def next_trigger_ms(self, now_ms: int | None = None) -> int:
        now = self._at(now_ms)
        today = self._trigger_for_date(now)
        if now < today:
            return _epoch_ms(today)
        return _epoch_ms(self._trigger_for_date(now + timedelta(days=1)))

    def _at(self, now_ms: int | None) -> datetime:
        if now_ms is None:
            return self._now()
        return datetime.fromtimestamp(now_ms / 1000, self._tz)
