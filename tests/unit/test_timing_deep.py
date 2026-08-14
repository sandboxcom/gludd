"""Deep tests for timing.py — gaps in DurationTracker, StallWatchdog, and shared singleton."""

from __future__ import annotations

import threading
import time

from general_ludd.observability.timing import (
    DurationTracker,
    DurationVerdict,
    StallReport,
    StallWatchdog,
    default_tracker,
)


class TestDurationTrackerDeep:
    def test_check_then_record_judges_before_recording(self) -> None:
        t = DurationTracker(min_samples=3, slow_factor=3.0)
        for _ in range(3):
            t.record("op", 1.0)
        v = t.check_then_record("op", 10.0)
        assert v.anomalous is True
        assert t.baseline("op") is not None

    def test_check_then_record_not_yet_learned_returns_learning(self) -> None:
        t = DurationTracker(min_samples=5)
        v = t.check_then_record("op", 1.0)
        assert v.anomalous is False
        assert "learning" in v.reason

    def test_track_with_on_anomaly_callback_fires(self) -> None:
        calls: list[DurationVerdict] = []
        t = DurationTracker(min_samples=3, slow_factor=2.0)
        for _ in range(3):
            t.record("op", 0.001)
        with t.track("op", on_anomaly=calls.append):
            time.sleep(0.05)
        assert len(calls) == 1 and calls[0].anomalous

    def test_track_on_anomaly_callback_exception_does_not_suppress(self) -> None:
        t = DurationTracker(min_samples=3, slow_factor=2.0)
        for _ in range(3):
            t.record("op", 0.001)

        def _failing_cb(_v: DurationVerdict) -> None:
            raise RuntimeError("callback exploded")

        with t.track("op", on_anomaly=_failing_cb):
            time.sleep(0.05)
        assert t.baseline("op") is not None

    def test_is_anomalous_respects_slow_factor_threshold(self) -> None:
        t = DurationTracker(min_samples=3, slow_factor=3.0, abs_floor_s=0.05)
        for _ in range(3):
            t.record("op", 1.0)
        assert t.is_anomalous("op", 2.5).anomalous is False
        assert t.is_anomalous("op", 4.0).anomalous is True

    def test_is_anomalous_abs_floor_blocks_without_baseline_gap(self) -> None:
        t = DurationTracker(min_samples=3, slow_factor=10.0, abs_floor_s=0.5)
        for _ in range(3):
            t.record("op", 0.01)
        v = t.is_anomalous("op", 0.08)
        assert v.anomalous is False

    def test_record_multiple_keys_independent_baselines(self) -> None:
        t = DurationTracker(min_samples=2)
        t.record("fast", 0.01)
        t.record("fast", 0.01)
        t.record("slow", 1.0)
        t.record("slow", 1.0)
        assert t.baseline("fast") == 0.01
        assert t.baseline("slow") == 1.0

    def test_record_window_trims_oldest(self) -> None:
        t = DurationTracker(window=2, min_samples=2)
        t.record("op", 1.0)
        t.record("op", 2.0)
        t.record("op", 3.0)
        assert t.baseline("op") == 2.5

    def test_baseline_unknown_key_returns_none(self) -> None:
        t = DurationTracker(min_samples=1)
        assert t.baseline("nonexistent") is None

    def test_constructor_raises_on_bad_params(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            DurationTracker(window=0)
        with pytest.raises(ValueError):
            DurationTracker(min_samples=0)
        with pytest.raises(ValueError):
            DurationTracker(slow_factor=1.0)

    def test_check_then_record_updates_history(self) -> None:
        t = DurationTracker(min_samples=1)
        t.check_then_record("op", 5.0)
        assert t.baseline("op") == 5.0


class TestStallWatchdogDeep:
    def test_explicit_deadline_overrides_baseline(self) -> None:
        t = DurationTracker(min_samples=1)
        for _ in range(3):
            t.record("k", 100.0)
        w = StallWatchdog(t, stall_factor=2.0, capture_stacks=False)
        w.start("op", "k", deadline_s=0.01)
        time.sleep(0.03)
        assert len(w.poll()) == 1

    def test_deadline_falls_back_to_abs_when_no_baseline(self) -> None:
        w = StallWatchdog(abs_deadline_s=0.01, capture_stacks=False)
        w.start("op", "k")
        time.sleep(0.03)
        assert len(w.poll()) == 1

    def test_deadline_falls_back_when_baseline_zero(self) -> None:
        t = DurationTracker(min_samples=1)
        for _ in range(3):
            t.record("k", 0.0)
        w = StallWatchdog(t, abs_deadline_s=0.01, capture_stacks=False)
        w.start("op", "k")
        time.sleep(0.03)
        assert len(w.poll()) == 1

    def test_on_stall_callback_exception_does_not_break_poll(self) -> None:
        def _bad(_r: StallReport) -> None:
            raise RuntimeError("callback failed")

        w = StallWatchdog(capture_stacks=False, on_stall=_bad)
        w.start("a", "k", deadline_s=0.01)
        time.sleep(0.03)
        reports = w.poll()
        assert len(reports) == 1

    def test_finish_removes_from_reported_set_so_restart_works(self) -> None:
        w = StallWatchdog(capture_stacks=False)
        w.start("op", "k", deadline_s=0.01)
        time.sleep(0.03)
        assert len(w.poll()) == 1
        w.finish("op")
        w.start("op", "k", deadline_s=0.01)
        time.sleep(0.03)
        assert len(w.poll()) == 1

    def test_start_is_idempotent(self) -> None:
        w = StallWatchdog(capture_stacks=False)
        w.start("op", "k", deadline_s=0.01)
        w.start("op", "k", deadline_s=10.0)
        time.sleep(0.03)
        assert len(w.poll()) == 1

    def test_watch_context_propagates_exception(self) -> None:
        w = StallWatchdog(capture_stacks=False)
        try:
            with w.watch("op", "k", deadline_s=60.0):
                raise ValueError("boom")
        except ValueError:
            pass
        assert w.poll() == []

    def test_stall_report_string_representation(self) -> None:
        r = StallReport(
            op_id="op1",
            key="test_key",
            elapsed_s=12.5,
            deadline_s=10.0,
            started_monotonic=100.0,
        )
        s = str(r)
        assert "STALL" in s and "test_key" in s and "op1" in s

    def test_capture_stacks_disabled_does_not_populate(self) -> None:
        w = StallWatchdog(capture_stacks=False)
        w.start("op", "k", deadline_s=0.01)
        time.sleep(0.03)
        reports = w.poll()
        assert len(reports) == 1
        assert reports[0].thread_stacks == {}

    def test_finish_during_poll_does_not_crash(self) -> None:
        w = StallWatchdog(capture_stacks=False)
        w.start("op", "k", deadline_s=0.01)
        time.sleep(0.03)
        reports = w.poll()
        assert len(reports) == 1
        w.finish("op")
        reports2 = w.poll()
        assert reports2 == []

    def test_poll_no_inflight_returns_empty(self) -> None:
        w = StallWatchdog(capture_stacks=False)
        assert w.poll() == []


class TestDefaultTracker:
    def test_singleton_returns_same_instance(self) -> None:
        a = default_tracker()
        b = default_tracker()
        assert a is b

    def test_singleton_is_usable(self) -> None:
        t = default_tracker()
        t.record("singleton_op", 0.5)
        t2 = default_tracker()
        assert t2.baseline("singleton_op") is None
        for _ in range(4):
            t.record("singleton_op", 0.5)
        assert t.baseline("singleton_op") == 0.5


class TestDurationVerdict:
    def test_fields(self) -> None:
        v = DurationVerdict(key="op", seconds=1.0, baseline=None, anomalous=False, reason="learning")
        assert v.key == "op"
        assert v.seconds == 1.0
        assert v.baseline is None
        assert v.anomalous is False
        assert v.reason == "learning"

    def test_str_representation(self) -> None:
        v = DurationVerdict(key="op", seconds=2.0, baseline=1.0, anomalous=True, reason="2.0x baseline")
        s = str(v)
        assert "SLOW" in s and "op" in s and "2.000s" in s

    def test_str_representation_normal(self) -> None:
        v = DurationVerdict(key="op", seconds=0.5, baseline=0.5, anomalous=False, reason="within baseline")
        s = str(v)
        assert "ok" in s and "op" in s

    def test_frozen_dataclass(self) -> None:
        import dataclasses

        v = DurationVerdict(key="op", seconds=1.0, baseline=None, anomalous=False)
        import pytest

        with pytest.raises(dataclasses.FrozenInstanceError):
            type(v).__setattr__(v, "anomalous", True)
        assert v.anomalous is False


class TestSweeperLifecycle:
    def test_sweeper_starts_and_runs(self) -> None:
        w = StallWatchdog(capture_stacks=False)
        w.start_sweeper(interval_s=0.05)
        assert w._sweeper is not None and w._sweeper.is_alive()
        w.start("op", "k", deadline_s=0.01)
        time.sleep(0.15)
        w.stop_sweeper()
        assert w._sweeper is None or not w._sweeper.is_alive()

    def test_start_sweeper_idempotent(self) -> None:
        w = StallWatchdog(capture_stacks=False)
        w.start_sweeper(interval_s=0.1)
        t1 = w._sweeper
        w.start_sweeper(interval_s=0.1)
        assert w._sweeper is t1
        w.stop_sweeper()

    def test_default_tracker_thread_safety(self) -> None:
        results: list[float | None] = []

        def _record() -> None:
            t = default_tracker()
            for _ in range(10):
                t.record("parallel", 1.0)
            results.append(t.baseline("parallel"))

        threads = [threading.Thread(target=_record) for _ in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert all(b == 1.0 for b in results)
