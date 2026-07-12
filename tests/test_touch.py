"""Tests for the antenna-wiggle manual trigger."""

import math

from reachy_mini_meenow.trigger_touch import AntennaTrigger


def _make(reads, *, release_ok=True, threshold_deg=20.0, cooldown_s=0.0,
          now=None, events=None):
    events = events if events is not None else []
    queue = list(reads)

    def read(_robot):
        return queue.pop(0) if queue else None

    return AntennaTrigger(
        object(), threshold_deg,
        cooldown_s=cooldown_s,
        release=lambda _r: (events.append("release"), release_ok)[1],
        hold=lambda _r: events.append("hold"),
        read=read,
        sleep=lambda _s: None,
        now=now or (lambda: 0.0),
    ), events


def test_fires_after_two_consecutive_deflections():
    d = math.radians(30)
    trig, _ = _make([[0.0, 0.0], [d, 0.0], [d, 0.0]])
    assert trig.arm()
    assert not trig.check()  # first over-threshold read: debounce
    assert trig.check()


def test_ignores_sub_threshold_and_resets_debounce():
    d = math.radians(30)
    small = math.radians(5)
    trig, _ = _make([[0.0, 0.0], [small, small], [d, 0.0], [small, 0.0], [d, 0.0]])
    assert trig.arm()
    assert not trig.check()  # small deviation
    assert not trig.check()  # first big
    assert not trig.check()  # back small: debounce counter resets
    assert not trig.check()  # first big again


def test_cooldown_suppresses_checks_after_arm():
    d = math.radians(30)
    clock = {"t": 0.0}
    trig, _ = _make([[0.0, 0.0], [d, 0.0], [d, 0.0], [d, 0.0], [d, 0.0]],
                    cooldown_s=5.0, now=lambda: clock["t"])
    assert trig.arm()
    assert not trig.check()  # within cooldown
    assert not trig.check()
    clock["t"] = 6.0
    assert not trig.check()  # debounce restarts after cooldown
    assert trig.check()


def test_disarmed_when_release_fails():
    trig, _ = _make([[0.0, 0.0]], release_ok=False)
    assert not trig.arm()
    assert not trig.armed
    assert not trig.check()


def test_disarmed_when_read_unavailable():
    trig, _ = _make([None])
    assert not trig.arm()
    assert not trig.check()


def test_disarm_holds_torque_and_rearm_resets_baseline():
    d = math.radians(30)
    trig, events = _make([[0.0, 0.0], [d, 0.0], [d, 0.0], [d, 0.0], [d + 0.01, 0.0]])
    assert trig.arm()
    trig.disarm()
    assert events == ["release", "hold"]
    assert not trig.check()  # disarmed: inert
    # re-arm consumes the next read as the new baseline (the deflected pose)
    assert trig.arm()
    assert not trig.check()
    assert not trig.check()  # near new baseline: never fires
