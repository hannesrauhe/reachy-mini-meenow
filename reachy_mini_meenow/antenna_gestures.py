"""Antenna gestures: read the (back-drivable) antennas as a two-way switch.

The antennas are torque-released while the app idles, so they feel loose and can
be pushed by hand. Their present positions are compared to a known "up" baseline
(the perk-up pose recorded on arming) and classified into three gestures:

- ``up``   — both antennas near the baseline (the resting pose)
- ``one``  — exactly one antenna pushed away from the baseline
- ``both`` — both antennas pushed away from the baseline

``one`` is the manual-capture gesture; ``both`` is the head-teach gesture (hold
them down to go soft, raise them to lock). A single reader owns both so the two
gestures can never be confused — the old trigger fired on *any* deflection and
would have mistaken the teach gesture for a capture.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Callable

from . import gestures

log = logging.getLogger(__name__)

_SETTLE_S = 0.5  # let released antennas drop to rest before baselining
_DEBOUNCE = 2    # consecutive equal raw readings before a gesture is reported

UP = "up"
ONE = "one"
BOTH = "both"


def classify(pos: list[float], baseline: list[float], threshold_rad: float) -> str:
    """Classify one antenna reading against the up baseline."""
    deflected = [abs(p - b) > threshold_rad for p, b in zip(pos, baseline)]
    if all(deflected):
        return BOTH
    if any(deflected):
        return ONE
    return UP


class AntennaGesture:
    """Debounce the raw antenna classification into a stable gesture.

    Any SDK failure leaves it disarmed (``poll()`` then always returns ``up``),
    so the app degrades to schedule-only rather than firing phantom gestures.
    """

    def __init__(
        self,
        reachy_mini,
        threshold_deg: float = 20.0,
        *,
        cooldown_s: float = 5.0,
        perk: Callable | None = None,
        release: Callable | None = None,
        hold: Callable | None = None,
        read: Callable | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ):
        self._robot = reachy_mini
        self._threshold_rad = math.radians(threshold_deg)
        self._cooldown_s = cooldown_s
        self._perk = perk or gestures.perk_antennas
        self._release = release or gestures.release_antennas
        self._hold = hold or gestures.hold_antennas
        self._read = read or gestures.read_antennas
        self._sleep = sleep
        self._now = now
        self._baseline: list[float] | None = None
        self._ready_at = 0.0
        self._stable = UP
        self._pending = UP
        self._pending_count = 0

    @property
    def armed(self) -> bool:
        return self._baseline is not None

    def arm(self) -> bool:
        """Raise, then release antenna torque, and record the up baseline."""
        self._baseline = None
        self._stable = UP
        self._pending = UP
        self._pending_count = 0
        self._perk(self._robot)
        if not self._release(self._robot):
            return False
        self._sleep(_SETTLE_S)
        pos = self._read(self._robot)
        if pos is None:
            log.warning("Antenna gestures unavailable (no position readback).")
            return False
        if len(pos) == len(gestures.UP_ANTENNAS_RAD) and any(
            abs(p - u) > 2 * self._threshold_rad
            for p, u in zip(pos, gestures.UP_ANTENNAS_RAD)
        ):
            # Something is still holding the antennas (e.g. a hand after a teach
            # timeout). Baselining there would make the release look like a
            # gesture — fall back to the known perk-up pose instead.
            log.info("Antennas not at rest — using the up pose as baseline.")
            pos = list(gestures.UP_ANTENNAS_RAD)
        self._baseline = pos
        self._ready_at = self._now() + self._cooldown_s
        log.info("Antenna gestures armed (baseline %s).", self._baseline)
        return True

    def disarm(self) -> None:
        """Re-enable antenna torque; ``poll()`` is inert (returns ``up``)."""
        if self._baseline is not None:
            self._baseline = None
            self._hold(self._robot)

    def poll(self) -> str:
        """Return the current debounced gesture: ``up`` / ``one`` / ``both``."""
        if self._baseline is None or self._now() < self._ready_at:
            return UP
        pos = self._read(self._robot)
        if pos is None or len(pos) != len(self._baseline):
            return self._stable
        raw = classify(pos, self._baseline, self._threshold_rad)
        if raw == self._pending:
            self._pending_count += 1
        else:
            self._pending = raw
            self._pending_count = 1
        if self._pending_count >= _DEBOUNCE and self._pending != self._stable:
            self._stable = self._pending
        return self._stable
