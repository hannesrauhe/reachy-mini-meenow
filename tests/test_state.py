"""Tests for the persisted state file (post idempotency + neutral pose)."""

import pytest

from reachy_mini_meenow import state


@pytest.fixture(autouse=True)
def _clear_memory():
    state._memory.clear()
    yield
    state._memory.clear()


def test_neutral_pose_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    assert state.load_neutral_pose(p) is None
    state.save_neutral_pose(p, [0.1, 0.2, 0.0, 0.0, 0.0, 0.0, -0.3])
    assert state.load_neutral_pose(p) == [0.1, 0.2, 0.0, 0.0, 0.0, 0.0, -0.3]


def test_keys_do_not_clobber_each_other(tmp_path):
    p = tmp_path / "state.json"
    state.save_posted_trigger_ms(p, 1234, post_url="http://x")
    state.save_neutral_pose(p, [1.0] * 7)
    assert state.load_posted_trigger_ms(p) == 1234
    assert state.load_neutral_pose(p) == [1.0] * 7
    # writing the trigger again must preserve the neutral pose
    state.save_posted_trigger_ms(p, 5678)
    assert state.load_neutral_pose(p) == [1.0] * 7
    assert state.load_posted_trigger_ms(p) == 5678


def test_malformed_neutral_pose_ignored(tmp_path):
    p = tmp_path / "state.json"
    p.write_text('{"neutral_pose": [1, 2, 3]}')  # wrong length
    assert state.load_neutral_pose(p) is None
