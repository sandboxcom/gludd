"""Tests for StallWatchdog stall detection in observability/timing.py."""

from __future__ import annotations

import threading
import time

from general_ludd.observability.timing import (
    DurationTracker,
    StallReport,
    StallWatchdog,
    capture_thread_stacks,
)


class TestStallWatchdogConstruction:
    def test_default_construction(self) -> None:
        wd = StallWatchdog()
        assert wd._stall_factor == 5.0
        assert wd._abs_deadline_s == 600.0
        assert wd._capture_stacks is True
        assert wd._inflight == {}
        assert wd._reported == set()

    def test_construction_with_tracker(self) -> None:
        tracker = DurationTracker()
        wd = StallWatchdog(tracker)
        assert wd._tracker is tracker

    def test_construction_custom_stall_factor(self) -> None:
        wd = StallWatchdog(stall_factor=10.0, abs_deadline_s=30.0)
        assert wd._stall_factor == 10.0
        assert wd._abs_deadline_s == 30.0

    def test_on_stall_callback_stored(self) -> None:
        calls: list[StallReport] = []
        wd = StallWatchdog(on_stall=lambda r: calls.append(r))
        assert wd._on_stall is not None
        assert calls == []


class TestStallWatchdogStartFinish:
    def test_start_registers_inflight(self) -> None:
        wd = StallWatchdog()
        wd.start("op-1", "model_call")
        assert "op-1" in wd._inflight
        key, _start, deadline = wd._inflight["op-1"]
        assert key == "model_call"
        assert deadline > 0

    def test_start_idempotent(self) -> None:
        wd = StallWatchdog()
        wd.start("op-1", "model_call")
        first_generation = wd._inflight["op-1"]

        wd.start("op-1", "replacement", deadline_s=0.0)

        assert wd._inflight["op-1"] == first_generation

    def test_duplicate_start_preserves_reported_flag(self) -> None:
        wd = StallWatchdog()
        wd.start("op-1", "model_call")
        wd._reported.add("op-1")
        wd.start("op-1", "model_call")
        assert "op-1" in wd._reported

    def test_finish_removes_inflight(self) -> None:
        wd = StallWatchdog()
        wd.start("op-1", "model_call")
        wd.finish("op-1")
        assert "op-1" not in wd._inflight

    def test_finish_unknown_is_noop(self) -> None:
        wd = StallWatchdog()
        wd.finish("nonexistent")

    def test_finish_discards_reported(self) -> None:
        wd = StallWatchdog()
        wd.start("op-1", "model_call")
        wd._reported.add("op-1")
        wd.finish("op-1")
        assert "op-1" not in wd._reported


class TestStallWatchdogPoll:
    def test_poll_empty_returns_empty(self) -> None:
        wd = StallWatchdog()
        assert wd.poll() == []

    def test_poll_reports_stalled_op(self) -> None:
        wd = StallWatchdog(abs_deadline_s=0.0)
        wd.start("op-1", "slow_op")
        time.sleep(0.01)
        reports = wd.poll()
        assert len(reports) == 1
        assert reports[0].op_id == "op-1"
        assert reports[0].key == "slow_op"

    def test_poll_only_reports_once(self) -> None:
        wd = StallWatchdog(abs_deadline_s=0.0)
        wd.start("op-1", "slow_op")
        time.sleep(0.01)
        first = wd.poll()
        assert len(first) == 1
        second = wd.poll()
        assert len(second) == 0

    def test_poll_reports_after_restart(self) -> None:
        wd = StallWatchdog(abs_deadline_s=0.0)
        wd.start("op-1", "slow_op")
        time.sleep(0.01)
        wd.poll()
        wd.finish("op-1")
        wd.start("op-1", "slow_op")
        time.sleep(0.01)
        reports = wd.poll()
        assert len(reports) == 1

    def test_poll_with_explicit_deadline(self) -> None:
        wd = StallWatchdog()
        wd.start("op-1", "slow_op", deadline_s=0.0)
        time.sleep(0.01)
        reports = wd.poll()
        assert len(reports) == 1

    def test_poll_callback_fires(self) -> None:
        calls: list[StallReport] = []
        wd = StallWatchdog(abs_deadline_s=0.0, on_stall=lambda r: calls.append(r))
        wd.start("op-1", "slow_op")
        time.sleep(0.01)
        wd.poll()
        assert len(calls) == 1
        assert calls[0].op_id == "op-1"

    def test_poll_callback_exception_does_not_abort(self) -> None:
        def bad_callback(_r: StallReport) -> None:
            raise RuntimeError("boom")
        wd = StallWatchdog(abs_deadline_s=0.0, on_stall=bad_callback)
        wd.start("op-1", "slow_op")
        wd.start("op-2", "slow_op_2")
        time.sleep(0.01)
        reports = wd.poll()
        assert len(reports) == 2

    def test_poll_with_tracker_baseline(self) -> None:
        tracker = DurationTracker()
        tracker.record("model_call", 1.0)
        for _ in range(5):
            tracker.record("model_call", 1.0)
        wd = StallWatchdog(tracker, stall_factor=0.001)
        wd.start("op-1", "model_call")
        time.sleep(0.02)
        reports = wd.poll()
        assert len(reports) == 1
        assert reports[0].key == "model_call"

    def test_poll_no_stack_capture_when_disabled(self) -> None:
        wd = StallWatchdog(abs_deadline_s=0.0, capture_stacks=False)
        wd.start("op-1", "slow_op")
        time.sleep(0.01)
        reports = wd.poll()
        assert len(reports) == 1
        assert reports[0].thread_stacks == {}


class TestStallWatchdogWatchContextManager:
    def test_watch_auto_finish(self) -> None:
        wd = StallWatchdog()
        with wd.watch("ctx-1", "test_op"):
            assert "ctx-1" in wd._inflight
        assert "ctx-1" not in wd._inflight

    def test_watch_auto_finish_on_exception(self) -> None:
        wd = StallWatchdog()
        try:
            with wd.watch("ctx-2", "test_op"):
                raise ValueError("boom")
        except ValueError:
            pass
        assert "ctx-2" not in wd._inflight

    def test_watch_with_deadline(self) -> None:
        wd = StallWatchdog()
        with wd.watch("ctx-3", "test_op", deadline_s=999.0):
            _key, _start, deadline = wd._inflight["ctx-3"]
            assert deadline == 999.0


class TestStallWatchdogSweeper:
    def test_start_sweeper_idempotent(self) -> None:
        wd = StallWatchdog()
        wd.start_sweeper(interval_s=0.1)
        thread1 = wd._sweeper
        wd.start_sweeper(interval_s=0.1)
        assert wd._sweeper is thread1
        wd.stop_sweeper()

    def test_stop_sweeper_cleans_up(self) -> None:
        wd = StallWatchdog()
        wd.start_sweeper(interval_s=0.1)
        wd.stop_sweeper()
        assert wd._sweeper is None
        assert wd._stop.is_set()

    def test_sweeper_polls(self) -> None:
        calls: list[StallReport] = []
        wd = StallWatchdog(abs_deadline_s=0.0, on_stall=lambda r: calls.append(r))
        wd.start("sweep-op", "slow_op")
        wd.start_sweeper(interval_s=0.05)
        time.sleep(0.2)
        wd.stop_sweeper()
        assert len(calls) >= 1


class TestStallReport:
    def test_report_fields(self) -> None:
        sr = StallReport(
            op_id="op-1",
            key="model_call",
            elapsed_s=30.5,
            deadline_s=5.0,
            started_monotonic=100.0,
        )
        assert sr.op_id == "op-1"
        assert sr.key == "model_call"
        assert sr.elapsed_s == 30.5
        assert sr.deadline_s == 5.0

    def test_report_str(self) -> None:
        sr = StallReport(
            op_id="op-1",
            key="model_call",
            elapsed_s=30.5,
            deadline_s=5.0,
            started_monotonic=100.0,
        )
        s = str(sr)
        assert "STALL" in s
        assert "model_call" in s
        assert "30.5" in s


class TestCaptureThreadStacks:
    def test_capture_returns_dict(self) -> None:
        stacks = capture_thread_stacks()
        assert isinstance(stacks, dict)

    def test_capture_includes_current_thread(self) -> None:
        stacks = capture_thread_stacks()
        current_name = threading.current_thread().name
        found = any(current_name in key for key in stacks)
        assert found
