"""Tests for the head-teacher LOCKED/TEACH state machine."""

import math

from reachy_mini_meenow.antenna_gestures import BOTH, ONE, UP
from reachy_mini_meenow.head_teacher import LOCKED, TEACH, HeadTeacher

D = math.radians


class Fake:
    """Records calls and drives the teacher's injected seams."""

    def __init__(self, head=None, gc_ok=True):
        self.head = head if head is not None else [0.0] * 7
        self.gc_ok = gc_ok
        self.gc = 0
        self.locked = []
        self.gotos = []
        self.stored = []
        self.soft = []
        self.rearmed = []

    def release_soft(self, _robot):
        self.soft.append("soft")

    def rearm(self):
        self.rearmed.append("rearm")

    def read(self):
        return list(self.head)

    def lock(self, j):
        self.locked.append(list(j))

    def goto(self, j, d):
        self.gotos.append((list(j), d))

    def enable_gc(self):
        if not self.gc_ok:
            raise RuntimeError("no Placo engine")
        self.gc += 1

    def disable_gc(self):
        self.gc -= 1

    def on_store(self, pose):
        self.stored.append(list(pose))

    def teacher(self, *, neutral=None, timeout_s=10.0, now=None):
        return HeadTeacher(
            object(), neutral_pose=neutral, timeout_s=timeout_s,
            read=self.read, lock=self.lock, goto=self.goto,
            enable_gc=self.enable_gc, disable_gc=self.disable_gc,
            soft_antennas=self.release_soft, rearm=self.rearm,
            on_store=self.on_store, now=now or (lambda: 0.0),
        )


def test_locked_ignores_up_and_one():
    f = Fake()
    t = f.teacher()
    assert t.update(UP) is None
    assert t.update(ONE) is None
    assert t.state == LOCKED
    assert f.gc == 0


def test_both_enters_teach_and_enables_gravity_comp():
    f = Fake()
    t = f.teacher()
    assert t.update(BOTH) == "teach"
    assert t.state == TEACH
    assert f.gc == 1
    # gravity comp stiffens the antennas as a side effect — they must be
    # released again so the user can raise them without force
    assert f.soft == ["soft"]


def test_gravity_comp_unavailable_stays_locked():
    f = Fake(gc_ok=False)
    t = f.teacher()
    assert t.update(BOTH) is None
    assert t.state == LOCKED
    assert f.gc == 0


def test_raise_antennas_stores_new_neutral_and_locks():
    taught = [0.1, 0.2, 0.0, 0.0, 0.0, 0.0, -0.3]
    f = Fake(head=taught)
    t = f.teacher()
    t.update(BOTH)          # enter teach
    assert t.update(UP) == "stored"
    assert t.state == LOCKED
    assert f.gc == 0
    assert f.locked == [taught]
    assert f.stored == [taught]
    assert t.neutral_pose == taught
    assert f.rearmed == ["rearm"]  # antennas perk up + go soft again


def test_timeout_returns_to_old_neutral_and_locks():
    old = [0.0] * 7
    moved = [0.0, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0]  # hand moved a Stewart joint
    clock = {"t": 0.0}
    f = Fake(head=moved)
    t = f.teacher(neutral=old, timeout_s=10.0, now=lambda: clock["t"])
    assert t.update(BOTH) == "teach"   # t=0, last_move=0
    clock["t"] = 11.0
    # still holding BOTH but no head movement since entering teach
    assert t.update(BOTH) == "timeout"
    assert t.state == LOCKED
    assert f.gc == 0
    assert f.gotos and f.gotos[0][0] == old  # drifted back to the OLD neutral
    assert f.stored == []                     # nothing stored on timeout
    # re-perk so a still-held antenna can't instantly re-trigger teach
    assert f.rearmed == ["rearm"]


def test_head_movement_resets_the_timeout():
    old = [0.0] * 7
    clock = {"t": 0.0}
    f = Fake(head=old)
    t = f.teacher(neutral=old, timeout_s=10.0, now=lambda: clock["t"])
    t.update(BOTH)  # enter teach, last_head = old
    # at t=6, move the head: last_move refreshes, no timeout yet
    clock["t"] = 6.0
    f.head = [0.0, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0]
    assert t.update(BOTH) is None
    # at t=12 (< 6 + 10 since the move), still teaching
    clock["t"] = 12.0
    assert t.update(BOTH) is None
    # at t=17 (> 6 + 10), timeout
    clock["t"] = 17.0
    assert t.update(BOTH) == "timeout"


def test_suspend_for_capture_disables_gc():
    f = Fake()
    t = f.teacher()
    t.update(BOTH)
    t.suspend_for_capture()
    assert t.state == LOCKED
    assert f.gc == 0


def test_suspend_when_locked_is_noop():
    f = Fake()
    t = f.teacher()
    t.suspend_for_capture()
    assert t.state == LOCKED
    assert f.gc == 0


def test_go_to_neutral_uses_taught_pose():
    taught = [0.1] * 7
    f = Fake()
    t = f.teacher(neutral=taught)
    t.go_to_neutral(duration=2.0)
    assert f.gotos == [(taught, 2.0)]
