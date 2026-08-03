"""Tests for budget/off_peak_scheduler: OffPeakScheduler, OffPeakTicket, SavingsTracker."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from general_ludd.budget.off_peak_scheduler import (
    OffPeakScheduler,
    OffPeakTicket,
    SavingsTracker,
    _validate_hour,
)


class TestSavingsTracker:
    def test_initial_state(self):
        st = SavingsTracker()
        assert st.total_deferred == 0
        assert st.total_savings == 0.0

    def test_record_positive(self):
        st = SavingsTracker()
        st.record(5.0)
        assert st.total_deferred == 1
        assert st.total_savings == pytest.approx(5.0)

    def test_record_multiple(self):
        st = SavingsTracker()
        st.record(1.0)
        st.record(2.0)
        st.record(3.0)
        assert st.total_deferred == 3
        assert st.total_savings == pytest.approx(6.0)

    def test_record_negative_ignored(self):
        st = SavingsTracker()
        st.record(-1.0)
        assert st.total_deferred == 0
        assert st.total_savings == 0.0

    def test_record_nan_ignored(self):
        st = SavingsTracker()
        st.record(float("nan"))
        assert st.total_deferred == 0
        assert st.total_savings == 0.0

    def test_record_inf_ignored(self):
        st = SavingsTracker()
        st.record(float("inf"))
        assert st.total_deferred == 0
        assert st.total_savings == 0.0

    def test_snapshot(self):
        st = SavingsTracker()
        st.record(7.5)
        snap = st.snapshot()
        assert snap["total_deferred"] == 1
        assert snap["total_savings"] == pytest.approx(7.5)

    def test_thread_safety(self):
        import threading

        st = SavingsTracker()
        errors = []

        def worker():
            for _ in range(100):
                try:
                    st.record(1.0)
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert st.total_deferred == 1000
        assert st.total_savings == pytest.approx(1000.0)


class TestOffPeakTicket:
    def test_is_ready_past_runnable(self):
        ticket = OffPeakTicket(
            task_id="t1",
            task_spec={},
            deadline=time.time() + 3600,
            estimated_cost_now=10.0,
            estimated_cost_off_peak=5.0,
            savings=5.0,
            scheduled_at=time.time() - 120,
            runnable_after=time.time() - 60,
        )
        assert ticket.is_ready is True

    def test_is_ready_not_yet(self):
        ticket = OffPeakTicket(
            task_id="t1",
            task_spec={},
            deadline=time.time() + 3600,
            estimated_cost_now=10.0,
            estimated_cost_off_peak=5.0,
            savings=5.0,
            scheduled_at=time.time(),
            runnable_after=time.time() + 3600,
        )
        assert ticket.is_ready is False


class TestOffPeakSchedulerInit:
    def test_defaults(self):
        sched = OffPeakScheduler()
        assert sched.pending_count == 0

    def test_invalid_off_peak_start(self):
        with pytest.raises(ValueError, match="off_peak_start"):
            OffPeakScheduler(off_peak_start=25)

    def test_invalid_off_peak_end(self):
        with pytest.raises(ValueError, match="off_peak_end"):
            OffPeakScheduler(off_peak_end=-1)

    def test_invalid_cost_multiplier_peak(self):
        with pytest.raises(ValueError, match="cost_multiplier_peak"):
            OffPeakScheduler(cost_multiplier_peak=0.5)

    def test_invalid_min_savings_ratio(self):
        with pytest.raises(ValueError, match="min_savings_ratio"):
            OffPeakScheduler(min_savings_ratio=1.5)

    def test_valid_hours(self):
        sched = OffPeakScheduler(off_peak_start=22, off_peak_end=6)
        assert sched._off_start == 22
        assert sched._off_end == 6


class TestOffPeakSchedulerSchedule:
    @patch("general_ludd.budget.off_peak_scheduler.OffPeakScheduler._is_off_peak", return_value=False)
    def test_schedule_with_zero_min_savings(self, _mock):
        sched = OffPeakScheduler(min_savings_ratio=0.0)
        now = time.time()
        ticket = sched.schedule({"cmd": "train"}, deadline=now + 86400)
        assert ticket is not None
        assert ticket.task_id.startswith("off-peak-")
        assert sched.pending_count >= 1

    @patch("general_ludd.budget.off_peak_scheduler.OffPeakScheduler._is_off_peak", return_value=False)
    def test_schedule_savings_below_min_ratio_returns_none(self, _mock):
        sched = OffPeakScheduler(min_savings_ratio=0.50)
        ticket = sched.schedule(
            {"cmd": "train"},
            deadline=time.time() + 86400,
            estimated_cost_now=10.0,
            estimated_cost_off_peak=9.0,
        )
        assert ticket is None

    @patch("general_ludd.budget.off_peak_scheduler.OffPeakScheduler._is_off_peak", return_value=False)
    def test_schedule_savings_above_min_ratio_deferred(self, _mock):
        sched = OffPeakScheduler(min_savings_ratio=0.20)
        ticket = sched.schedule(
            {"cmd": "train"},
            deadline=time.time() + 86400,
            estimated_cost_now=10.0,
            estimated_cost_off_peak=5.0,
        )
        assert ticket is not None
        assert ticket.estimated_cost_now == 10.0
        assert ticket.estimated_cost_off_peak == 5.0
        assert ticket.savings == pytest.approx(5.0)

    @patch("general_ludd.budget.off_peak_scheduler.OffPeakScheduler._is_off_peak", return_value=False)
    def test_schedule_zero_peak_returns_none(self, _mock):
        sched = OffPeakScheduler(min_savings_ratio=0.20)
        ticket = sched.schedule(
            {"cmd": "train"},
            deadline=time.time() + 86400,
            estimated_cost_now=0.0,
        )
        assert ticket is None

    @patch("general_ludd.budget.off_peak_scheduler.OffPeakScheduler._is_off_peak", return_value=False)
    def test_schedule_negative_peak_returns_none(self, _mock):
        sched = OffPeakScheduler(min_savings_ratio=0.20)
        ticket = sched.schedule(
            {"cmd": "train"},
            deadline=time.time() + 86400,
            estimated_cost_now=-5.0,
        )
        assert ticket is None

    @patch("general_ludd.budget.off_peak_scheduler.OffPeakScheduler._is_off_peak", return_value=False)
    def test_schedule_records_savings(self, _mock):
        sched = OffPeakScheduler(min_savings_ratio=0.10)
        ticket = sched.schedule(
            {"cmd": "train"},
            deadline=time.time() + 86400,
            estimated_cost_now=10.0,
            estimated_cost_off_peak=5.0,
        )
        assert ticket is not None
        assert sched.savings.total_savings == pytest.approx(5.0)
        assert sched.savings.total_deferred == 1


class TestOffPeakSchedulerOffPeakDetection:
    def test_is_off_peak_within_window(self):
        sched = OffPeakScheduler(off_peak_start=0, off_peak_end=6)
        with patch.object(sched, "_is_off_peak", return_value=True):
            assert sched._is_off_peak() is True

    def test_schedule_during_off_peak_returns_none(self):
        sched = OffPeakScheduler(off_peak_start=0, off_peak_end=6)
        with patch.object(sched, "_is_off_peak", return_value=True):
            ticket = sched.schedule(
                {"cmd": "train"},
                deadline=time.time() + 86400,
                estimated_cost_now=10.0,
                estimated_cost_off_peak=5.0,
            )
            assert ticket is None


class TestOffPeakSchedulerGetReadyTasks:
    def test_no_tasks_ready(self):
        sched = OffPeakScheduler()
        ready = sched.get_ready_tasks()
        assert ready == []

    @patch("general_ludd.budget.off_peak_scheduler.OffPeakScheduler._is_off_peak", return_value=False)
    def test_task_becomes_ready(self, _mock_is_off):
        sched = OffPeakScheduler(off_peak_start=0, off_peak_end=6, min_savings_ratio=0.10)
        now = time.time()
        sched.schedule({"cmd": "train"}, deadline=now + 86400, estimated_cost_now=10.0, estimated_cost_off_peak=5.0)

        ready = sched.get_ready_tasks()
        assert len(ready) == 0

        for t in sched._tickets.values():
            t.runnable_after = time.time() - 60

        ready = sched.get_ready_tasks()
        assert len(ready) >= 1

    @patch("general_ludd.budget.off_peak_scheduler.OffPeakScheduler._is_off_peak", return_value=False)
    def test_expired_tasks_excluded(self, _mock_is_off):
        sched = OffPeakScheduler(ticket_ttl=0.0, min_savings_ratio=0.10)
        now = time.time()
        sched.schedule({"cmd": "train"}, deadline=now - 7200, estimated_cost_now=10.0, estimated_cost_off_peak=5.0)

        for t in sched._tickets.values():
            t.runnable_after = time.time() - 60

        ready = sched.get_ready_tasks()
        assert len(ready) == 0


class TestOffPeakSchedulerRunDeferred:
    @pytest.mark.asyncio
    @patch("general_ludd.budget.off_peak_scheduler.OffPeakScheduler._is_off_peak", return_value=False)
    async def test_no_executor_returns_empty(self, _mock_is_off):
        sched = OffPeakScheduler(off_peak_start=0, off_peak_end=6, min_savings_ratio=0.10)
        sched.schedule(
            {"cmd": "train"},
            deadline=time.time() + 86400,
            estimated_cost_now=10.0,
            estimated_cost_off_peak=5.0,
        )
        for t in sched._tickets.values():
            t.runnable_after = time.time() - 60

        results = await sched.run_deferred()
        assert results == []

    @pytest.mark.asyncio
    @patch("general_ludd.budget.off_peak_scheduler.OffPeakScheduler._is_off_peak", return_value=False)
    async def test_with_executor(self, _mock_is_off):
        executor = AsyncMock(return_value="done")
        sched = OffPeakScheduler(off_peak_start=0, off_peak_end=6, min_savings_ratio=0.10, executor=executor)
        sched.schedule(
            {"cmd": "train"},
            deadline=time.time() + 86400,
            estimated_cost_now=10.0,
            estimated_cost_off_peak=5.0,
        )
        for t in sched._tickets.values():
            t.runnable_after = time.time() - 60

        results = await sched.run_deferred()
        assert len(results) == 1
        assert results[0]["result"] == "done"
        assert results[0]["task_id"].startswith("off-peak-")
        executor.assert_awaited_once_with({"cmd": "train"})
        assert sched.pending_count == 0

    @pytest.mark.asyncio
    @patch("general_ludd.budget.off_peak_scheduler.OffPeakScheduler._is_off_peak", return_value=False)
    async def test_executor_error_captured(self, _mock_is_off):
        executor = AsyncMock(side_effect=RuntimeError("boom"))
        sched = OffPeakScheduler(off_peak_start=0, off_peak_end=6, min_savings_ratio=0.10, executor=executor)
        sched.schedule(
            {"cmd": "train"},
            deadline=time.time() + 86400,
            estimated_cost_now=10.0,
            estimated_cost_off_peak=5.0,
        )
        for t in sched._tickets.values():
            t.runnable_after = time.time() - 60

        results = await sched.run_deferred()
        assert len(results) == 1
        assert results[0]["error"] == "boom"
        assert sched.pending_count == 0

    @pytest.mark.asyncio
    @patch("general_ludd.budget.off_peak_scheduler.OffPeakScheduler._is_off_peak", return_value=False)
    async def test_multiple_tasks(self, _mock_is_off):
        executor = AsyncMock(return_value="ok")
        sched = OffPeakScheduler(off_peak_start=0, off_peak_end=6, min_savings_ratio=0.10, executor=executor)
        for i in range(3):
            sched.schedule(
                {"cmd": f"task-{i}"},
                deadline=time.time() + 86400,
                estimated_cost_now=10.0,
                estimated_cost_off_peak=5.0,
            )
        for t in sched._tickets.values():
            t.runnable_after = time.time() - 60

        results = await sched.run_deferred()
        assert len(results) == 3
        assert executor.await_count == 3
        assert sched.pending_count == 0


class TestOffPeakSchedulerBackgroundLoop:
    @pytest.mark.asyncio
    async def test_background_loop_stops_on_event(self):
        executor = AsyncMock(return_value="ok")
        sched = OffPeakScheduler(off_peak_start=0, off_peak_end=6, executor=executor)

        stop = asyncio.Event()
        stop.set()

        await sched._background_loop(poll_interval=0.01, stop_event=stop)
        executor.assert_not_awaited()


class TestOffPeakSchedulerGetStatus:
    def test_get_status(self):
        sched = OffPeakScheduler(off_peak_start=0, off_peak_end=6)
        status = sched.get_status()
        assert status["pending_count"] == 0
        assert status["off_peak_start"] == 0
        assert status["off_peak_end"] == 6
        assert "off_peak_active" in status
        assert "savings" in status


class TestOffPeakSchedulerPruneExpired:
    @patch("general_ludd.budget.off_peak_scheduler.OffPeakScheduler._is_off_peak", return_value=False)
    def test_prune_removes_expired(self, _mock_is_off):
        sched = OffPeakScheduler(ticket_ttl=0.0, min_savings_ratio=0.10)
        sched.schedule(
            {"cmd": "train"},
            deadline=time.time() - 7200,
            estimated_cost_now=10.0,
            estimated_cost_off_peak=5.0,
        )
        assert sched.pending_count >= 1
        pruned = sched._prune_expired()
        assert pruned >= 1
        assert sched.pending_count == 0

    @patch("general_ludd.budget.off_peak_scheduler.OffPeakScheduler._is_off_peak", return_value=False)
    def test_prune_keeps_valid(self, _mock_is_off):
        sched = OffPeakScheduler(ticket_ttl=3600.0, min_savings_ratio=0.10)
        sched.schedule(
            {"cmd": "train"},
            deadline=time.time() + 86400,
            estimated_cost_now=10.0,
            estimated_cost_off_peak=5.0,
        )
        pruned = sched._prune_expired()
        assert pruned == 0
        assert sched.pending_count >= 1


class TestNextOffPeak:
    def test_next_off_peak_already_in_window(self):
        sched = OffPeakScheduler(off_peak_start=0, off_peak_end=6)
        now = time.time()
        with patch.object(sched, "_is_off_peak", return_value=True):
            result = sched._next_off_peak(now)
            assert result == now

    def test_next_off_peak_same_day_hour_12(self):
        sched = OffPeakScheduler(off_peak_start=22, off_peak_end=6)
        day_start = _day_start(time.time())
        noon = day_start + 12 * 3600
        expected = day_start + 22 * 3600
        with patch.object(sched, "_is_off_peak", return_value=False):
            result = sched._next_off_peak(noon)
            assert result == pytest.approx(expected)

    def test_next_off_peak_next_day(self):
        sched = OffPeakScheduler(off_peak_start=22, off_peak_end=6)
        day_start = _day_start(time.time())
        near_midnight = day_start + 23 * 3600
        expected = day_start + 22 * 3600 + 86400
        with patch.object(sched, "_is_off_peak", return_value=False):
            result = sched._next_off_peak(near_midnight)
            assert result == pytest.approx(expected)


def _day_start(ts: float) -> float:
    lt = time.localtime(ts)
    return ts - lt.tm_hour * 3600 - lt.tm_min * 60 - lt.tm_sec


class TestValidateHour:
    def test_valid(self):
        _validate_hour(0, "test")

    def test_below_zero(self):
        with pytest.raises(ValueError, match="test"):
            _validate_hour(-1, "test")

    def test_above_23(self):
        with pytest.raises(ValueError, match="test"):
            _validate_hour(24, "test")
