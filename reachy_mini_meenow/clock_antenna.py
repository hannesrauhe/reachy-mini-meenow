"""Right-antenna clock: wind it down, it ticks back up to 12 and takes the photo.

The bot's **right** antenna is a clock hand. 12 o'clock is the resting (up) pose;
it can be wound down towards 6 (counter-clockwise: 11, 10, 9, 8...). Push it to
an hour and hold it still for a moment, and the antenna then ticks back up to
12 **once per second**, five ticks per clock hour — winding to 8 is a 20-second
countdown. Pushing it back down mid-countdown cancels.

The left antenna is left alone (it stays up), so the separate *both antennas
down* head-teach gesture is unaffected. Like the head teacher this is a plain
state machine driven by ``update()`` from the app's poll loop — no thread, so it
can never race a scripted capture — and every robot touch is an injectable seam,
so the whole thing is unit-testable without hardware.

The joint-angle <-> hour mapping (where 12 sits, degrees per hour, and which way
"down" sweeps) is configurable, because the exact resting angle and sweep
direction are calibrated on the real robot.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Callable

from . import gestures

log = logging.getLogger(__name__)

IDLE = "idle"
TICKING = "ticking"

UP_HOUR = 0   # 12 o'clock == the resting (up) pose
MAX_HOUR = 6  # 6 o'clock is the lowest the hand can be wound


def _label(hour: int, clockwise: bool = False) -> str:
    """Clock-face position for an hour offset from 12.

    Winding counter-clockwise (12 -> 11 -> 10 -> 9 -> 8) puts an offset of 4 at
    the 8 o'clock position, which is how the hand is actually wound; a clockwise
    wind reads 12 -> 1 -> 2 -> 3 instead.
    """
    pos = hour % 12 if clockwise else (12 - hour) % 12
    return str(pos or 12)


def hour_to_angle(hour: float, up_rad: float, deg_per_hour_rad: float, sign: float) -> float:
    """Right-antenna joint angle for a clock hour (0 == 12 o'clock).

    ``hour`` may be fractional — the countdown steps in fractions of an hour so
    the ticks land evenly between the hour marks.
    """
    return up_rad + sign * hour * deg_per_hour_rad


def angle_to_hour(pos_right: float, up_rad: float, deg_per_hour_rad: float, sign: float) -> int:
    """Nearest clock hour for a right-antenna reading, clamped to 0..6."""
    offset = (pos_right - up_rad) * sign / deg_per_hour_rad
    return max(UP_HOUR, min(MAX_HOUR, int(round(offset))))


class ClockAntenna:
    """Wind-down / tick-up capture gesture on the right antenna (see module doc).

    ``update()`` is called every poll tick; it reads the antennas itself and
    returns an event for the caller to log/act on: ``"wind"`` when a countdown
    commits, ``"strike"`` when the hand reaches 12 (the caller captures then), or
    ``None``. Any SDK failure just leaves it idle, so the app degrades to
    schedule-only rather than firing phantom captures.
    """

    def __init__(
        self,
        reachy_mini,
        *,
        up_rad: float | None = None,
        deg_per_hour: float = 30.0,
        sign: float = -1.0,
        tick_s: float = 1.0,
        ticks_per_hour: int = 5,
        wind_hold_s: float = 1.0,
        left_up_tol_deg: float = 25.0,
        tick_move_s: float = 0.4,
        clockwise: bool = False,
        read: Callable | None = None,
        set_right: Callable[[float, float], None] | None = None,
        enable: Callable[[], None] | None = None,
        release: Callable[[], None] | None = None,
        now: Callable[[], float] = time.monotonic,
    ):
        self._robot = reachy_mini
        self._up_rad = gestures.UP_ANTENNAS_RAD[0] if up_rad is None else up_rad
        self._left_up_rad = gestures.UP_ANTENNAS_RAD[1]
        self._dph = math.radians(deg_per_hour)
        self._sign = sign
        self._tick_s = tick_s
        self._tph = max(1, int(ticks_per_hour))
        self._wind_hold_s = wind_hold_s
        self._left_tol = math.radians(left_up_tol_deg)
        self._tick_move_s = tick_move_s
        self._clockwise = clockwise
        self._read = read or gestures.read_antennas
        self._now = now
        # Torque on the right antenna only, so the left stays soft for teach.
        self._enable = enable or (
            lambda: reachy_mini.enable_motors(ids=["right_antenna"])
        )
        # Back to soft if a countdown is abandoned mid-tick (a strike instead
        # hands torque over to the capture path, which re-enables both antennas).
        self._release = release or (
            lambda: reachy_mini.disable_motors(ids=["right_antenna"])
        )
        # Antenna-only joint-space move: _goto_joint_positions would also echo
        # the *head* joints at their present position for the whole move (the
        # old sag bug), so interpolate here and send SetAntennasCmd only.
        self._set_right = set_right or self._move_right

        self.state = IDLE
        self._wound_hour = 0
        self._since_move = 0.0
        self._remaining = 0
        self._pos_hour = 0.0
        self._next_tick = 0.0
        self._settled_at = 0.0
        self._cmd_right: float | None = None
        self._left_hold = self._left_up_rad

    def _move_right(self, angle: float, left: float) -> None:
        """Drive the right antenna to ``angle`` over ``tick_move_s`` seconds.

        Ramps from the last **commanded** angle, not the present read: the
        motor always trails its target a little, and starting each ramp from
        that lagging position made consecutive steps visibly uneven (tiny
        step, then a double one). From command to command the steps are
        exactly uniform.

        Antenna-only on purpose: the SDK's joint-space goto would also re-assert
        the head joints at their present position for the whole move (the old
        sag bug), so this interpolates and sends ``SetAntennasCmd`` only.
        """
        start = self._cmd_right
        if start is None:
            present = self._read(self._robot)
            if present is None or len(present) != 2:
                return
            start = float(present[0])
        steps = max(1, int(self._tick_move_s / 0.02))
        for i in range(1, steps + 1):
            frac = i / steps
            self._robot._set_joint_positions(  # noqa: SLF001
                antennas_joint_positions=[
                    start + (angle - start) * frac,
                    left,
                ]
            )
            time.sleep(self._tick_move_s / steps)
        self._cmd_right = angle

    def update(self) -> str | None:
        """Advance the clock one tick; return ``wind``/``strike``/None."""
        pos = self._read(self._robot)
        if pos is None or len(pos) != 2:
            return None
        right, left = float(pos[0]), float(pos[1])
        if self.state == TICKING:
            return self._tick(right, left)
        return self._watch(right, left)

    def _watch(self, right: float, left: float) -> str | None:
        """Idle: look for the right hand wound down (left up) and held still."""
        left_up = abs(left - self._left_up_rad) <= self._left_tol
        hour = angle_to_hour(right, self._up_rad, self._dph, self._sign)
        if not left_up or hour < 1:
            self._wound_hour = 0
            self._since_move = self._now()
            return None
        if hour != self._wound_hour:  # still being moved — restart the hold timer
            self._wound_hour = hour
            self._since_move = self._now()
            return None
        if self._now() - self._since_move >= self._wind_hold_s:
            return self._start(right, left)
        return None

    def _start(self, right: float, left: float) -> str:
        """Commit the wind: sync the target to the present pose, then torque on.

        The target is set *before* enabling torque so the motor has no stale
        (up) target to snap to — that is what would make the hand jump.
        """
        self._cmd_right = right  # sync the ramp origin before the first tick
        self._set_right(right, left)
        self._enable()
        self.state = TICKING
        self._remaining = self._wound_hour * self._tph
        self._pos_hour = float(self._wound_hour)
        self._left_hold = left
        self._next_tick = self._now() + self._tick_s
        self._settled_at = self._now() + self._tick_move_s
        log.info(
            "Clock wound to %s o'clock — %ds to twelve.",
            _label(self._wound_hour, self._clockwise),
            int(self._remaining * self._tick_s),
        )
        return "wind"

    def _tick(self, right: float, left: float) -> str | None:
        """Ticking: one tick per second (five per hour) until the hand strikes 12."""
        # Only judge "pushed back down" once the hand has settled at its
        # commanded position — while a tick is still in flight the present
        # position lags behind it and would read as a false push-back.
        if self._now() >= self._settled_at:
            hour = angle_to_hour(right, self._up_rad, self._dph, self._sign)
            if hour >= self._pos_hour + 1:  # a deliberate push back down
                self.state = IDLE
                self._wound_hour = 0
                self._cmd_right = None
                self._release()
                log.info("Clock cancelled (hand pushed back down).")
                return None
        if self._now() < self._next_tick:
            return None
        self._remaining -= 1
        self._next_tick += self._tick_s
        self._pos_hour = self._remaining / self._tph
        self._set_right(
            hour_to_angle(self._pos_hour, self._up_rad, self._dph, self._sign),
            self._left_hold,
        )
        self._settled_at = self._now() + self._tick_move_s
        if self._remaining <= 0:
            self.state = IDLE
            self._wound_hour = 0
            self._cmd_right = None
            self._release()  # soft again; the capture path perks both antennas up
            log.info("Clock struck twelve — capturing.")
            return "strike"
        log.info("Tick — %ds to twelve.", int(self._remaining * self._tick_s))
        return None
