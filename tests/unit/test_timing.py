"""Tests for duration/anomaly/stall detection (observability/timing.py)."""

from __future__ import annotations

import time

from general_ludd.observability.timing import (
    DurationTracker,
    StallWatchdog,
    capture_thread_stacks,
)


# --- DurationTracker --------------------------------------------------------
def test_baseline_none_until_min_samples() -> None:
    t = DurationTracker(min_samples=5)
    for _ in range(4):
        t.record("op", 1.0)
    assert t.baseline("op") is None
    t.record("op", 1.0)
    assert t.baseline("op") == 1.0


def test_is_anomalous_learning_then_flags_slow() -> None:
    t = DurationTracker(min_samples=5, slow_factor=3.0, abs_floor_s=0.05)
    v = t.is_anomalous("op", 100.0)
    assert v.anomalous is False and "learning" in v.reason
    for _ in range(5):
        t.record("op", 1.0)
    assert t.is_anomalous("op", 1.2).anomalous is False  # within baseline
    v = t.is_anomalous("op", 10.0)  # 10x baseline of 1s
    assert v.anomalous is True
    assert v.baseline == 1.0
    assert "10.0x" in v.reason or "10x" in v.reason


def test_abs_floor_suppresses_tiny_slowdowns() -> None:
    t = DurationTracker(min_samples=3, slow_factor=3.0, abs_floor_s=0.05)
    for _ in range(3):
        t.record("fast", 0.001)  # 1ms baseline
    # 5ms is 5x the baseline but only +4ms absolute (< 50ms floor) → NOT flagged.
    assert t.is_anomalous("fast", 0.005).anomalous is False


def test_zero_baseline_does_not_divide_by_zero() -> None:
    # A window of all-zero durations yields baseline 0.0; is_anomalous must not
    # raise ZeroDivisionError — it treats a non-positive baseline as not-anomalous.
    t = DurationTracker(min_samples=1, abs_floor_s=0.0)
    for _ in range(3):
        t.record("z", 0.0)
    v = t.is_anomalous("z", 5.0)
    assert v.anomalous is False
    assert v.baseline == 0.0


def test_record_ignores_non_finite_and_negative() -> None:
    t = DurationTracker(min_samples=1)
    t.record("op", -1.0)
    t.record("op", float("inf"))
    t.record("op", float("nan"))
    assert t.baseline("op") is None  # nothing valid recorded


def test_window_bounds_history() -> None:
    t = DurationTracker(window=3, min_samples=1)
    for v in (1.0, 1.0, 1.0, 9.0, 9.0, 9.0):
        t.record("op", v)
    # only the last 3 (all 9.0) survive → baseline reflects the recent window
    assert t.baseline("op") == 9.0


def test_track_context_records_duration_even_on_error() -> None:
    t = DurationTracker(min_samples=1)
    try:
        with t.track("blk"):
            raise ValueError("boom")
    except ValueError:
        pass
    assert t.baseline("blk") is not None  # duration recorded on the error path


# --- StallWatchdog ----------------------------------------------------------
def test_watchdog_flags_after_deadline_once() -> None:
    w = StallWatchdog(capture_stacks=False)
    # Widened from 0.01s: under xdist scheduling jitter a tiny deadline can
    # already have elapsed by the time the first poll() runs, flipping the
    # "not yet stalled" assertion below. 1.0s gives ample buffer while the
    # after-deadline sleep is scaled to match.
    w.start("op1", "k", deadline_s=1.0)
    assert w.poll() == []  # just started, not past deadline
    time.sleep(1.05)
    reports = w.poll()
    assert len(reports) == 1
    assert reports[0].op_id == "op1" and reports[0].key == "k"
    assert w.poll() == []  # reported once, no spam
    w.finish("op1")


def test_watchdog_on_stall_callback_fires() -> None:
    seen: list = []
    w = StallWatchdog(capture_stacks=False, on_stall=seen.append)
    w.start("op", "k", deadline_s=0.01)
    time.sleep(0.03)
    w.poll()
    assert len(seen) == 1 and seen[0].key == "k"


def test_watchdog_deadline_derived_from_tracker_baseline() -> None:
    t = DurationTracker(min_samples=1, slow_factor=3.0)
    for _ in range(3):
        t.record("k", 0.01)  # baseline ~10ms
    w = StallWatchdog(t, stall_factor=2.0, capture_stacks=False)
    w.start("op", "k")  # deadline = 0.01 * 2 = 20ms
    time.sleep(0.05)
    assert len(w.poll()) == 1


def test_watch_context_finishes_and_is_not_flagged() -> None:
    w = StallWatchdog(capture_stacks=False)
    with w.watch("op", "k", deadline_s=0.01):
        pass  # completes fast
    time.sleep(0.03)
    assert w.poll() == []  # finished on exit → not stalled


def test_finish_unknown_op_is_safe() -> None:
    w = StallWatchdog(capture_stacks=False)
    w.finish("never-started")  # must not raise


def test_capture_thread_stacks_returns_mapping() -> None:
    stacks = capture_thread_stacks()
    assert isinstance(stacks, dict) and len(stacks) >= 1
    assert all(isinstance(v, str) and v for v in stacks.values())


def test_stall_report_captures_stacks_when_enabled() -> None:
    w = StallWatchdog(capture_stacks=True)
    w.start("op", "k", deadline_s=0.01)
    time.sleep(0.03)
    reports = w.poll()
    assert len(reports) == 1
    assert reports[0].thread_stacks  # non-empty stack snapshot for investigation
