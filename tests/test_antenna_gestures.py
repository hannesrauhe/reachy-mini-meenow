"""Tests for the antenna gesture reader (up / one / both)."""

import math

import pytest

from reachy_mini_meenow.antenna_gestures import BOTH, ONE, UP, AntennaGesture, classify

D = math.radians


def test_classify_up_one_both():
    base = [0.0, 0.0]
    t = D(20)
    assert classify([0.0, 0.0], base, t) == UP
    assert classify([D(30), 0.0], base, t) == ONE
    assert classify([0.0, -D(30)], base, t) == ONE
    assert classify([D(30), -D(30)], base, t) == BOTH
    # sign of deflection does not matter, only magnitude
    assert classify([-D(30), D(30)], base, t) == BOTH


def test_classify_threshold_is_per_antenna():
    base = [0.0, 0.0]
    t = D(20)
    # each just under threshold -> up, not both
    assert classify([D(19), D(19)], base, t) == UP


def _gesture(reads, *, threshold_deg=20.0, cooldown_s=0.0, now=None, events=None):
    """reads[0] is consumed by arm() as the baseline; the rest drive poll()."""
    events = events if events is not None else []
    queue = list(reads)

    def read(_robot=None):
        return queue.pop(0) if queue else [0.0, 0.0]

    g = AntennaGesture(
        object(), threshold_deg, cooldown_s=cooldown_s,
        perk=lambda _r: events.append("perk"),
        release=lambda _r: (events.append("release"), True)[1],
        hold=lambda _r: events.append("hold"),
        read=read, sleep=lambda _s: None, now=now or (lambda: 0.0),
    )
    return g, events


def test_arm_raises_then_releases():
    g, events = _gesture([[0.0, 0.0]])
    assert g.arm()
    assert events == ["perk", "release"]


def test_disarm_holds_torque():
    g, events = _gesture([[0.0, 0.0]])
    g.arm()
    g.disarm()
    assert events == ["perk", "release", "hold"]
    assert not g.armed


def test_arm_falls_back_to_up_pose_when_antennas_held_down():
    # After a teach timeout the re-perk can fight a hand still holding both
    # antennas down; baselining there would make the release look like a
    # gesture. The known up pose must be used instead.
    held = [D(60), D(-60)]  # far from UP_ANTENNAS_RAD (±10°)
    g, _ = _gesture([held])
    assert g.arm()
    assert g._baseline == pytest.approx([-0.1745, 0.1745])


def test_poll_debounces_to_both():
    d = D(30)
    g, _ = _gesture([[0.0, 0.0], [d, -d], [d, -d]])
    g.arm()
    assert g.poll() == UP    # first BOTH read only pending (count 1 < 2)
    assert g.poll() == BOTH  # second confirms it


def test_poll_needs_two_reads_before_changing():
    d = D(30)
    g, _ = _gesture([[0.0, 0.0], [d, -d], [0.0, 0.0]])
    g.arm()
    assert g.poll() == UP  # first BOTH read pending, not confirmed
    assert g.poll() == UP  # back to up before confirmation: never fires BOTH


def test_poll_one_antenna():
    d = D(30)
    g, _ = _gesture([[0.0, 0.0], [d, 0.0], [d, 0.0]])
    g.arm()
    assert g.poll() == UP
    assert g.poll() == ONE


def test_cooldown_suppresses_until_ready():
    d = D(30)
    clock = {"t": 0.0}
    g, _ = _gesture([[0.0, 0.0], [d, -d], [d, -d]], cooldown_s=5.0,
                    now=lambda: clock["t"])
    g.arm()
    assert g.poll() == UP  # within cooldown (no read consumed)
    clock["t"] = 6.0
    assert g.poll() == UP    # first real read pending
    assert g.poll() == BOTH  # confirmed after cooldown
