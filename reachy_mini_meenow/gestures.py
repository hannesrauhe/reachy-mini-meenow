"""Expressive robot motions around a capture.

Amplitudes stay well inside the SDK safety limits (head pitch/roll ±40°, head yaw
±180°). Every loop polls ``stop_event`` so a shutdown mid-gesture is honored within
one control step; callers always finish by returning to a neutral pose.
"""

from __future__ import annotations

import logging
import threading
import time

import numpy as np

from reachy_mini.utils import create_head_pose

log = logging.getLogger(__name__)

_CTRL_HZ = 50.0
_CTRL_DT = 1.0 / _CTRL_HZ
_ANTENNA_IDS = ["left_antenna", "right_antenna"]


def release_antennas(reachy_mini) -> bool:
    """Torque off the antennas so they are back-drivable; False if unsupported."""
    try:
        reachy_mini.disable_motors(ids=list(_ANTENNA_IDS))
        return True
    except Exception as exc:  # noqa: BLE001 - optional feature, degrade silently
        log.warning("release_antennas failed: %s", exc)
        return False


def hold_antennas(reachy_mini) -> None:
    """Re-enable antenna torque (pins targets to the present pose first)."""
    try:
        reachy_mini.enable_motors(ids=list(_ANTENNA_IDS))
    except Exception as exc:  # noqa: BLE001
        log.warning("hold_antennas failed: %s", exc)


def read_antennas(reachy_mini) -> list[float] | None:
    """Present antenna joint positions in radians, or ``None`` if unavailable."""
    try:
        pos = reachy_mini.get_present_antenna_joint_positions()
        return None if pos is None else [float(p) for p in pos]
    except Exception as exc:  # noqa: BLE001
        log.warning("read_antennas failed: %s", exc)
        return None


# The SDK's near-vertical antenna pose (10° off exactly-vertical, which is an
# unstable equilibrium that shakes). Used as the deterministic trigger baseline.
UP_ANTENNAS_RAD = [-0.1745, 0.1745]


def perk_antennas(reachy_mini, duration: float = 0.8) -> None:
    """Raise the antennas to the near-vertical pose while torque is still on.

    Head and body are explicitly held (``head=None``, ``body_yaw=None``) so the
    perk-up never disturbs a hand-aimed head pose.
    """
    log.info("perk_antennas: raising")
    try:
        reachy_mini.goto_target(
            antennas=list(UP_ANTENNAS_RAD), body_yaw=None, duration=duration
        )
    except Exception as exc:  # noqa: BLE001 - cosmetic, degrade to drooping
        log.warning("perk_antennas failed: %s", exc)


def _neutral_pose():
    return create_head_pose(yaw=0.0, pitch=0.0, roll=0.0, z=0.0, degrees=True, mm=True)


def _goto(reachy_mini, head, body_yaw_deg: float | None, duration: float) -> None:
    """goto_target with body rotation; falls back to head-only on SDKs without body_yaw."""
    if body_yaw_deg is None:
        reachy_mini.goto_target(head=head, duration=duration)
        return
    try:
        reachy_mini.goto_target(
            head=head, body_yaw=float(np.deg2rad(body_yaw_deg)), duration=duration
        )
    except TypeError:
        reachy_mini.goto_target(head=head, duration=duration)


def go_neutral(reachy_mini, duration: float = 0.7) -> None:
    """Smoothly return head and body to the forward, level 'look at camera' pose."""
    try:
        _goto(reachy_mini, _neutral_pose(), body_yaw_deg=0.0, duration=duration)
    except Exception as exc:  # noqa: BLE001 - cleanup must never raise
        log.warning("go_neutral failed: %s", exc)


def look_at_mirror(reachy_mini, stop_event: threading.Event,
                   yaw_deg: float = -90.0, pitch_deg: float = 10.0,
                   duration: float = 1.5, settle_s: float = 1.0) -> None:
    """Turn body+head towards the side mirror and tilt slightly down for the selfie.

    The head pose is world-framed, so the head gets the full yaw itself; the body
    follows with the same ``body_yaw`` where the SDK supports it (head-only turn
    otherwise). ``settle_s`` lets the motion damp out before the capture.
    """
    log.info("look_at_mirror: yaw=%.0f° pitch=%.0f°", yaw_deg, pitch_deg)
    pose = create_head_pose(yaw=yaw_deg, pitch=pitch_deg, roll=0.0, degrees=True, mm=True)
    _goto(reachy_mini, pose, body_yaw_deg=yaw_deg, duration=duration)
    end = time.time() + max(0.0, settle_s)
    while time.time() < end and not stop_event.is_set():
        time.sleep(0.05)


def _wiggle(reachy_mini, stop_event: threading.Event, duration: float,
            yaw_amp: float, pitch_amp: float, antenna_amp_deg: float,
            yaw_hz: float, antenna_hz: float) -> None:
    t0 = time.time()
    while not stop_event.is_set():
        t = time.time() - t0
        if t >= duration:
            break
        yaw = yaw_amp * np.sin(2.0 * np.pi * yaw_hz * t)
        pitch = pitch_amp * np.sin(2.0 * np.pi * yaw_hz * 0.5 * t)
        head = create_head_pose(yaw=yaw, pitch=pitch, degrees=True, mm=True)
        a = np.deg2rad(antenna_amp_deg * np.sin(2.0 * np.pi * antenna_hz * t))
        reachy_mini.set_target(head=head, antennas=np.array([a, -a]))
        time.sleep(_CTRL_DT)


def hello(reachy_mini, stop_event: threading.Event) -> None:
    """A slow greeting wave on startup so it's visible the app is running."""
    log.info("hello")
    _wiggle(reachy_mini, stop_event, duration=2.2,
            yaw_amp=25.0, pitch_amp=6.0, antenna_amp_deg=45.0,
            yaw_hz=0.5, antenna_hz=0.8)
    go_neutral(reachy_mini, duration=0.6)


def get_ready(reachy_mini, stop_event: threading.Event, countdown_s: int = 3,
              hold_pose=None) -> None:
    """A brief attention wiggle, then face the camera and hold for a countdown.

    ``hold_pose`` defaults to :func:`go_neutral`; pass a callable to hold a
    taught neutral pose instead of the absolute forward-facing one.
    """
    log.info("get_ready: wiggle")
    _wiggle(reachy_mini, stop_event, duration=1.4,
            yaw_amp=18.0, pitch_amp=8.0, antenna_amp_deg=30.0,
            yaw_hz=0.7, antenna_hz=1.2)
    if stop_event.is_set():
        return
    (hold_pose or go_neutral)(reachy_mini, duration=0.5)
    log.info("get_ready: countdown %ds", countdown_s)
    for _ in range(max(0, countdown_s)):
        if stop_event.is_set():
            return
        time.sleep(1.0)
