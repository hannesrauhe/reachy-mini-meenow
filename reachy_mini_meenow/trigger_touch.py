"""Manual capture trigger: wiggle an antenna by hand.

While the app idles, antenna torque is off (back-drivable) and their present
positions are polled; a sustained deviation from the armed baseline fires a
capture. There is no touch sensor or motor-load readback on the Reachy Mini, so
position deviation of the compliant antennas is the interaction signal.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Callable

from . import gestures

log = logging.getLogger(__name__)

_SETTLE_S = 0.5  # let released antennas drop to rest before baselining


class AntennaTrigger:
    """Fires when either antenna deviates from its rest baseline.

    Debounced: the deviation must exceed the threshold on two consecutive
    ``check()`` calls. Any SDK failure leaves the trigger disarmed (``check()``
    then always returns False), so the app degrades to schedule-only.
    """

    def __init__(
        self,
        reachy_mini,
        threshold_deg: float = 20.0,
        *,
        cooldown_s: float = 5.0,
        release: Callable | None = None,
        hold: Callable | None = None,
        read: Callable | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ):
        self._robot = reachy_mini
        self._threshold_rad = math.radians(threshold_deg)
        self._cooldown_s = cooldown_s
        self._release = release or gestures.release_antennas
        self._hold = hold or gestures.hold_antennas
        self._read = read or gestures.read_antennas
        self._sleep = sleep
        self._now = now
        self._baseline: list[float] | None = None
        self._over_count = 0
        self._ready_at = 0.0

    @property
    def armed(self) -> bool:
        return self._baseline is not None

    def arm(self) -> bool:
        """Release antenna torque and record the rest baseline."""
        self._baseline = None
        self._over_count = 0
        if not self._release(self._robot):
            return False
        self._sleep(_SETTLE_S)
        self._baseline = self._read(self._robot)
        if self._baseline is None:
            log.warning("Antenna trigger unavailable (no position readback).")
            return False
        # Cooldown: a hand still on the antenna at arm time skews the baseline;
        # ignore checks until it has plausibly let go.
        self._ready_at = self._now() + self._cooldown_s
        log.info("Antenna trigger armed (baseline %s).", self._baseline)
        return True

    def disarm(self) -> None:
        """Re-enable antenna torque; ``check()`` is inert until re-armed."""
        if self._baseline is not None:
            self._baseline = None
            self._over_count = 0
        self._hold(self._robot)

    def check(self) -> bool:
        if self._baseline is None:
            return False
        if self._now() < self._ready_at:
            self._over_count = 0
            return False
        pos = self._read(self._robot)
        if pos is None or len(pos) != len(self._baseline):
            return False
        over = any(
            abs(p - b) > self._threshold_rad for p, b in zip(pos, self._baseline)
        )
        self._over_count = self._over_count + 1 if over else 0
        if self._over_count >= 2:
            log.info("Antenna trigger fired (positions %s).", pos)
            self._over_count = 0
            return True
        return False
