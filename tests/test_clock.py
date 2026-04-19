"""Tests for WorldClock and action durations."""

from datetime import datetime, timedelta

from miniverse.clock import WorldClock, get_action_duration, DEFAULT_ACTION_DURATIONS


def test_clock_basic():
    clock = WorldClock(start_time=datetime(2026, 9, 17, 8, 0))
    assert clock.now == datetime(2026, 9, 17, 8, 0)
    assert clock.hour() == 8
    assert clock.is_work_hours()


def test_clock_advance():
    clock = WorldClock(start_time=datetime(2026, 9, 17, 8, 0))
    clock.advance_minutes(30)
    assert clock.now == datetime(2026, 9, 17, 8, 30)

    clock.advance(timedelta(hours=2))
    assert clock.now == datetime(2026, 9, 17, 10, 30)


def test_clock_elapsed():
    clock = WorldClock(start_time=datetime(2026, 9, 17, 8, 0))
    start = clock.now
    clock.advance_minutes(90)
    elapsed = clock.elapsed_since(start)
    assert elapsed == timedelta(minutes=90)


def test_clock_work_hours():
    clock = WorldClock(start_time=datetime(2026, 9, 17, 7, 0))
    assert not clock.is_work_hours()
    clock.advance_minutes(60)
    assert clock.is_work_hours()
    clock.advance(timedelta(hours=10))  # 6pm
    assert not clock.is_work_hours()


def test_action_duration_defaults():
    dur = get_action_duration("talk")
    assert dur == timedelta(minutes=3)

    dur = get_action_duration("work")
    assert dur == timedelta(minutes=30)

    # Unknown action gets 5 minutes default
    dur = get_action_duration("unknown_action")
    assert dur == timedelta(minutes=5)


def test_action_duration_overrides():
    dur = get_action_duration("talk", overrides={"talk": 10.0})
    assert dur == timedelta(minutes=10)

    # Override doesn't affect others
    dur = get_action_duration("work", overrides={"talk": 10.0})
    assert dur == timedelta(minutes=30)


def test_time_str():
    clock = WorldClock(start_time=datetime(2026, 9, 17, 14, 30))
    assert clock.time_str() == "2026-09-17 14:30"
