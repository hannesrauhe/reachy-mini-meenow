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


def _neutral_pose():
    return create_head_pose(yaw=0.0, pitch=0.0, roll=0.0, z=0.0, degrees=True, mm=True)


def go_neutral(reachy_mini, duration: float = 0.7) -> None:
    """Smoothly return the head to the forward, level 'look at camera' pose."""
    try:
        reachy_mini.goto_target(head=_neutral_pose(), duration=duration)
    except Exception as exc:  # noqa: BLE001 - cleanup must never raise
        log.warning("go_neutral failed: %s", exc)


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


def get_ready(reachy_mini, stop_event: threading.Event, countdown_s: int = 3) -> None:
    """A brief attention wiggle, then face the camera and hold for a countdown."""
    log.info("get_ready: wiggle")
    _wiggle(reachy_mini, stop_event, duration=1.4,
            yaw_amp=18.0, pitch_amp=8.0, antenna_amp_deg=30.0,
            yaw_hz=0.7, antenna_hz=1.2)
    if stop_event.is_set():
        return
    go_neutral(reachy_mini, duration=0.5)
    log.info("get_ready: countdown %ds", countdown_s)
    for _ in range(max(0, countdown_s)):
        if stop_event.is_set():
            return
        time.sleep(1.0)


def celebrate(reachy_mini, stop_event: threading.Event) -> None:
    """A small happy wiggle after a successful post."""
    log.info("celebrate")
    _wiggle(reachy_mini, stop_event, duration=1.6,
            yaw_amp=12.0, pitch_amp=12.0, antenna_amp_deg=35.0,
            yaw_hz=1.5, antenna_hz=2.0)
    go_neutral(reachy_mini, duration=0.5)
