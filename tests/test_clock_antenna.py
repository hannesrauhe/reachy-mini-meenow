"""Tests for the right-antenna clock (wind down, tick up to 12, capture)."""

import math

from reachy_mini_meenow.clock_antenna import (
    IDLE,
    TICKING,
    ClockAntenna,
    angle_to_hour,
    hour_to_angle,
)

D = math.radians
LEFT_UP = 0.1745  # SDK up pose for the left antenna (index 1)


def test_hour_angle_roundtrip():
    up, dph, sign = 0.0, D(30), 1.0
    for hour in range(7):
        assert angle_to_hour(hour_to_angle(hour, up, dph, sign), up, dph, sign) == hour


def test_angle_to_hour_clamps():
    up, dph, sign = 0.0, D(30), 1.0
    assert angle_to_hour(-D(10), up, dph, sign) == 0   # above 12 -> 12
    assert angle_to_hour(D(200), up, dph, sign) == 6   # past 6 -> 6


class Fake:
    """Drives the clock's seams: a mutable antenna position and a fake clock."""

    def __init__(self, right=0.0, left=LEFT_UP):
        self.pos = [right, left]
        self.t = 0.0
        self.moves = []
        self.torque = []

    def read(self, _robot=None):
        return list(self.pos)

    def set_right(self, angle, left):
        self.moves.append(float(angle))
        self.pos[0] = float(angle)  # the hand arrives where it is commanded

    def enable(self):
        self.torque.append("on")

    def release(self):
        self.torque.append("off")

    def clock(self, *, up_rad=0.0, deg_per_hour=30.0, sign=1.0, tick_s=1.0,
              ticks_per_hour=5, wind_hold_s=1.0, tick_move_s=0.4):
        return ClockAntenna(
            object(), up_rad=up_rad, deg_per_hour=deg_per_hour, sign=sign,
            tick_s=tick_s, ticks_per_hour=ticks_per_hour, wind_hold_s=wind_hold_s,
            tick_move_s=tick_move_s,
            read=self.read, set_right=self.set_right, enable=self.enable,
            release=self.release, now=lambda: self.t,
        )


def test_winding_down_and_holding_starts_the_countdown():
    f = Fake(right=D(120))  # 4 hours down from 12
    c = f.clock()
    assert c.update() is None          # t=0: hour 4 noted, hold timer starts
    f.t = 1.0
    assert c.update() == "wind"        # held 1s -> committed
    assert c.state == TICKING
    assert f.torque == ["on"]          # torque on to drive the hand
    assert c._remaining == 20          # 4 hours x 5 ticks


def test_ticks_once_per_second_and_strikes():
    f = Fake(right=D(60))  # 2 hours down -> 10 ticks
    c = f.clock(tick_s=1.0)
    c.update()
    f.t = 1.0
    assert c.update() == "wind"        # wound to 2, next tick at t=2
    for t in range(2, 11):             # the poll loop calls update every second
        f.t = float(t)
        assert c.update() is None
    assert c._remaining == 1           # one tick still to go
    f.t = 11.0
    assert c.update() == "strike"      # tick 10 -> 12, photo time
    assert c.state == IDLE
    assert f.torque == ["on", "off"]   # released for the capture path


def test_winding_to_eight_counts_twenty_ticks_in_twenty_seconds():
    f = Fake(right=D(120))  # 8 o'clock = 4 hours counter-clockwise from 12
    c = f.clock(tick_s=1.0)
    c.update()
    f.t = 1.0
    assert c.update() == "wind"
    strikes = []
    for step in range(1, 25):          # sweep well past the expected strike
        f.t = 1.0 + step
        if c.update() == "strike":
            strikes.append(f.t)
            break
    assert strikes == [21.0]           # 1s hold + 20 ticks x 1s
    assert c._remaining == 0


def test_pushing_the_hand_back_down_cancels():
    f = Fake(right=D(120))
    c = f.clock(tick_s=1.0, tick_move_s=0.4)
    c.update()
    f.t = 1.0
    assert c.update() == "wind"        # pos_hour 4, settled at t=1.4
    f.t = 2.0                          # past settle
    f.pos[0] = D(150)                  # hand pushed further down (hour 5)
    assert c.update() is None
    assert c.state == IDLE
    assert f.torque == ["on", "off"]   # antenna let go again


def test_tick_in_flight_is_not_a_false_cancel():
    f = Fake(right=D(120))
    c = f.clock(tick_s=1.0, tick_move_s=0.4)
    c.update()
    f.t = 1.0
    c.update()                         # wind; hand still travelling
    f.pos[0] = D(120)                  # present lags at the old hour
    f.t = 1.1                          # before the settle deadline
    assert c.update() is None
    assert c.state == TICKING


def test_left_antenna_down_never_winds_the_clock():
    # Both antennas down is the head-teach gesture, not a clock wind.
    f = Fake(right=D(120), left=1.2)
    c = f.clock()
    assert c.update() is None
    f.t = 5.0
    assert c.update() is None
    assert c.state == IDLE
    assert f.torque == []


def test_at_rest_it_stays_idle():
    f = Fake(right=0.0, left=LEFT_UP)
    c = f.clock()
    for t in range(10):
        f.t = float(t)
        assert c.update() is None
    assert c.state == IDLE


def test_no_readback_keeps_it_idle():
    f = Fake(right=D(120))
    c = ClockAntenna(
        object(), up_rad=0.0, read=lambda _r: None,
        set_right=f.set_right, enable=f.enable, release=f.release,
        now=lambda: f.t,
    )
    assert c.update() is None
    assert c.state == IDLE
