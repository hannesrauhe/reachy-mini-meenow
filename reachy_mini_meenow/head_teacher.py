"""Antenna-gated head teaching: hold a neutral pose, or go soft and re-pose it.

The head normally sits at a *neutral* pose, held by the position controllers.
Push **both** antennas down and the head enters *teach* mode — gravity
compensation, so it is soft to move and floats where you leave it. Then either:

- raise the antennas back up: the pose you left the head in becomes the new
  neutral (persisted), and the head locks there; or
- move nothing for ``timeout_s``: the head drifts slowly back to the *old*
  neutral and locks (the pose you were shaping is discarded).

The neutral pose is what the robot returns to after a selfie, so teaching it
once re-aims every future post.

Gravity compensation is the whole point of the soft window, and it needs the
daemon on the Placo kinematics engine; if it is unavailable the teacher logs
once and stays locked rather than pretending. It is deliberately a plain state
machine driven by ``update()`` from the app's poll loop — no background thread —
so it can never race with a scripted capture, and it is unit-testable.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Callable

from . import gestures
from .antenna_gestures import BOTH, UP

log = logging.getLogger(__name__)

LOCKED = "locked"
TEACH = "teach"

# A head move smaller than this (per joint) is not "movement" for the timeout.
_HEAD_MOVE_DEG = 2.0


class HeadTeacher:
    """LOCKED/TEACH state machine (see module docstring)."""

    def __init__(
        self,
        reachy_mini,
        *,
        neutral_pose: list[float] | None = None,
        timeout_s: float = 10.0,
        head_move_deg: float = _HEAD_MOVE_DEG,
        return_s: float = 2.5,
        read=None,
        lock=None,
        goto=None,
        enable_gc=None,
        disable_gc=None,
        soft_antennas=None,
        rearm=None,
        on_store: Callable[[list[float]], None] | None = None,
        now: Callable[[], float] = time.monotonic,
    ):
        self._robot = reachy_mini
        self._neutral = list(neutral_pose) if neutral_pose else None
        self._timeout_s = timeout_s
        self._head_move_rad = math.radians(head_move_deg)
        self._return_s = return_s
        self._now = now
        self._read = read or (lambda: list(reachy_mini.get_current_joint_positions()[0]))
        # Joint-space lock/return: the only way to command the head without the
        # daemon's Cartesian IK dragging the body around.
        self._lock = lock or (
            lambda j: reachy_mini._set_joint_positions(head_joint_positions=list(j))  # noqa: SLF001
        )
        self._goto = goto or (
            lambda j, d: reachy_mini._goto_joint_positions(head_joint_positions=list(j), duration=d)  # noqa: SLF001
        )
        self._enable_gc = enable_gc or reachy_mini.enable_gravity_compensation
        self._disable_gc = disable_gc or reachy_mini.disable_gravity_compensation
        # Enabling gravity comp flips the *antennas* to a stiff mode too (the
        # daemon's mode switch touches all motors), so re-release them for the
        # soft window; on exit, re-perk + re-baseline the gesture reader so the
        # antennas go soft again and a held-down pair can't instantly re-trigger.
        self._soft = soft_antennas or gestures.release_antennas
        self._rearm = rearm or (lambda: None)
        self._on_store = on_store

        self.state = LOCKED
        self._last_head: list[float] | None = None
        self._last_move = 0.0
        self._gc_warned = False

    @property
    def neutral_pose(self) -> list[float] | None:
        return list(self._neutral) if self._neutral else None

    def update(self, gesture: str) -> str | None:
        """Advance the machine one tick; return an event for logging or None."""
        if self.state == LOCKED:
            if gesture == BOTH:
                return self._enter_teach()
            return None

        # TEACH: watch for head movement to keep the timeout honest.
        head = self._read()
        moved = self._last_head is not None and any(
            abs(c - p) > self._head_move_rad for c, p in zip(head[1:], self._last_head[1:])
        )
        self._last_head = head
        if moved or gesture != BOTH:
            self._last_move = self._now()
        if gesture == UP:
            return self._store_and_lock(head)
        if self._now() - self._last_move >= self._timeout_s:
            return self._return_to_neutral()
        return None

    def _enter_teach(self) -> str | None:
        try:
            self._enable_gc()
        except Exception as exc:  # noqa: BLE001 - needs Placo engine; degrade once
            if not self._gc_warned:
                log.warning("Teach mode unavailable (gravity comp): %s", exc)
                self._gc_warned = True
            return None
        self.state = TEACH
        self._soft(self._robot)  # GC mode stiffened the antennas — free them again
        self._last_head = self._read()
        self._last_move = self._now()
        log.info("Teach mode: head is soft — move it, then raise the antennas.")
        return "teach"

    def _store_and_lock(self, head: list[float]) -> str:
        """Antennas raised: the current pose becomes the new neutral."""
        self._disable_gc()
        self._neutral = list(head)
        self._lock(self._neutral)
        self.state = LOCKED
        if self._on_store is not None:
            try:
                self._on_store(self._neutral)
            except Exception as exc:  # noqa: BLE001 - persistence must not break teach
                log.warning("Could not persist neutral pose: %s", exc)
        self._rearm()
        log.info("New neutral pose stored and locked.")
        return "stored"

    def _return_to_neutral(self) -> str:
        """Timeout: drift back to the old neutral and lock (pose discarded)."""
        self._disable_gc()
        if self._neutral:
            self._goto(self._neutral, self._return_s)
        else:
            self._lock(self._read())
        self.state = LOCKED
        # Perk the antennas back up ourselves: the user may still be holding them
        # down, and a lingering ``both`` would otherwise re-enter teach at once.
        self._rearm()
        log.info("Teach timed out — returning to neutral and locking.")
        return "timeout"

    def suspend_for_capture(self) -> None:
        """Force out of teach before scripted motion (which needs position control).

        The pose being shaped is discarded; the app returns to neutral after the
        capture. Re-arm teach by pushing both antennas down again.
        """
        if self.state == TEACH:
            self._disable_gc()
            self.state = LOCKED
            log.info("Teach suspended for capture.")

    def go_to_neutral(self, duration: float | None = None) -> None:
        """Move the head back to the taught neutral pose (used after a selfie).

        Falls back to the world-neutral (0) pose when none has been taught.
        """
        if self._neutral:
            self._goto(self._neutral, duration or self._return_s)
        else:
            from . import gestures

            gestures.go_neutral(self._robot, duration=duration or self._return_s)
