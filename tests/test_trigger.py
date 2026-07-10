"""Parity tests for the trigger-time math ported from meenow's trigger-core.mjs."""

from datetime import datetime, timezone

from reachy_mini_meenow.trigger import (
    WINDOW_MINUTES,
    TriggerClock,
    djb2,
    trigger_offset_minutes,
    xorshift32,
)

_MASK = 0xFFFFFFFF


def _single_round_offset(date_str: str) -> int:
    """One xorshift round — the buggy variant the source warns clusters."""
    x = xorshift32(djb2(date_str))
    return int((x / 0x100000000) * WINDOW_MINUTES)


def _june_2026_dates():
    return [f"2026-06-{d:02d}" for d in range(1, 31)]


def test_djb2_is_uint32():
    for s in _june_2026_dates() + ["", "hello", "2026-01-01"]:
        h = djb2(s)
        assert 0 <= h <= _MASK


def test_xorshift_fixed_point_guard():
    # 0 is the algorithm's fixed point; it must be replaced, not returned as 0.
    assert xorshift32(0) != 0


def test_offset_within_window():
    for s in _june_2026_dates():
        off = trigger_offset_minutes(s)
        assert 0 <= off < WINDOW_MINUTES


def test_integer_and_float_forms_agree():
    # floor(x/2**32 * 720) == (x*720) >> 32 for every produced x.
    for s in _june_2026_dates():
        x = xorshift32(xorshift32(xorshift32(djb2(s))))
        assert int((x / 0x100000000) * WINDOW_MINUTES) == (x * WINDOW_MINUTES) >> 32


def test_single_round_clusters_but_three_rounds_spread():
    """The core masking regression check (trigger-core.mjs:34-40 rationale).

    A single xorshift round leaves consecutive June-2026 dates clustered near the
    same minute; the shipped three-round variant must spread them across the window.
    """
    single = [_single_round_offset(s) for s in _june_2026_dates()]
    three = [trigger_offset_minutes(s) for s in _june_2026_dates()]
    assert max(single) - min(single) < 30, single  # clustered (~16:50)
    assert max(three) - min(three) > 400, three  # well spread


def test_last_before_next_and_period_is_a_day():
    clock = TriggerClock(timezone.utc)
    now_ms = int(datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    last = clock.last_trigger_ms(now_ms)
    nxt = clock.next_trigger_ms(now_ms)
    assert last < nxt
    # Consecutive-day triggers each land anywhere in a 12h window, so a period
    # spans 24h +/- up to ~12h.
    period_hours = (nxt - last) / 3_600_000
    assert 12 <= period_hours <= 36


def test_branch_selection_matches_now_relative_to_today():
    clock = TriggerClock(timezone.utc)
    day = datetime(2026, 6, 15, tzinfo=timezone.utc)
    today_trigger = clock.today_trigger_ms(
        int(day.replace(hour=12).timestamp() * 1000)
    )
    # Just after today's trigger: last == today's trigger, next is tomorrow's.
    after = today_trigger + 60_000
    assert clock.last_trigger_ms(after) == today_trigger
    assert clock.next_trigger_ms(after) > today_trigger
    # Just before today's trigger: next == today's trigger.
    before = today_trigger - 60_000
    assert clock.next_trigger_ms(before) == today_trigger
    assert clock.last_trigger_ms(before) < today_trigger
