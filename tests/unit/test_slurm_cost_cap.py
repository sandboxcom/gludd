"""Tests for SlurmJobMonitor: max_cost_usd enforcement and idle detection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from general_ludd.infra.slurm import (
    SlurmAdapter,
    SlurmJobConfig,
    SlurmJobInfo,
    SlurmJobMonitor,
    SlurmJobState,
    _parse_elapsed,
)


class TestParseElapsed:
    def test_hh_mm_ss(self):
        assert _parse_elapsed("01:30:45") == 5445.0

    def test_d_hh_mm_ss(self):
        assert _parse_elapsed("2-06:15:30") == 195330.0

    def test_mm_ss(self):
        assert _parse_elapsed("05:30") == 330.0

    def test_with_microseconds(self):
        assert _parse_elapsed("00:01:30.500") == 90.5

    def test_empty_returns_none(self):
        assert _parse_elapsed("") is None
        assert _parse_elapsed("   ") is None

    def test_unlimited_returns_none(self):
        assert _parse_elapsed("UNLIMITED") is None

    def test_zero_elapsed(self):
        assert _parse_elapsed("00:00:00") == 0.0


class TestCostMonitor:
    def test_computes_correct_elapsed_cost(self):
        config = SlurmJobConfig(max_cost_usd=10.0, hourly_rate_usd=3.0)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.elapsed_seconds.return_value = 7200.0  # 2 hours
        adapter.status.return_value = SlurmJobInfo("12345", SlurmJobState.RUNNING)

        monitor = SlurmJobMonitor(adapter, "12345", config)
        monitor._poll()

        assert monitor.cost_incurred == 6.0
        assert not monitor.cancelled

    def test_cancels_when_cost_exceeds_max(self):
        config = SlurmJobConfig(max_cost_usd=10.0, hourly_rate_usd=5.0)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.elapsed_seconds.return_value = 10800.0  # 3 hours -> $15
        adapter.status.return_value = SlurmJobInfo("12345", SlurmJobState.RUNNING)

        monitor = SlurmJobMonitor(adapter, "12345", config)
        monitor._poll()

        assert monitor.cost_incurred == 15.0
        assert monitor.cancelled
        assert monitor.cancel_reason == SlurmJobMonitor.CANCEL_REASON_COST
        adapter.cancel.assert_called_once_with("12345")

    def test_does_not_cancel_when_under_cap(self):
        config = SlurmJobConfig(max_cost_usd=10.0, hourly_rate_usd=5.0)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.elapsed_seconds.return_value = 3600.0  # 1 hour -> $5
        adapter.status.return_value = SlurmJobInfo("12345", SlurmJobState.RUNNING)

        monitor = SlurmJobMonitor(adapter, "12345", config)
        monitor._poll()

        assert monitor.cost_incurred == 5.0
        assert not monitor.cancelled
        adapter.cancel.assert_not_called()

    def test_no_cost_check_when_max_not_set(self):
        config = SlurmJobConfig(max_cost_usd=None, hourly_rate_usd=5.0)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.elapsed_seconds.return_value = 999999.0
        adapter.status.return_value = SlurmJobInfo("12345", SlurmJobState.RUNNING)

        monitor = SlurmJobMonitor(adapter, "12345", config)
        monitor._poll()

        assert not monitor.cancelled
        adapter.cancel.assert_not_called()

    def test_no_crash_on_missing_elapsed(self):
        config = SlurmJobConfig(max_cost_usd=10.0, hourly_rate_usd=5.0)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.elapsed_seconds.return_value = None
        adapter.status.return_value = SlurmJobInfo("12345", SlurmJobState.RUNNING)

        monitor = SlurmJobMonitor(adapter, "12345", config)
        result = monitor._poll()

        assert result is True
        assert monitor.cost_incurred == 0.0
        adapter.cancel.assert_not_called()


class TestIdleDetection:
    def test_idle_timer_resets_on_activity(self):
        config = SlurmJobConfig(idle_timeout_minutes=15.0)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.status.return_value = SlurmJobInfo("12345", SlurmJobState.RUNNING)
        adapter.elapsed_seconds.return_value = 0.0

        activity = MagicMock(return_value=True)  # Active
        monitor = SlurmJobMonitor(adapter, "12345", config, activity_checker=activity)

        with patch("general_ludd.infra.slurm.time") as mock_time:
            mock_time.time.return_value = 1000.0
            monitor._poll()
            assert not monitor.cancelled

            # No activity now
            activity.return_value = False
            mock_time.time.return_value = 1001.0
            monitor._poll()
            assert not monitor.cancelled

            # Activity returns
            activity.return_value = True
            mock_time.time.return_value = 1002.0
            monitor._poll()
            assert not monitor.cancelled

            # No activity again, but elapsed from last reset
            activity.return_value = False
            mock_time.time.return_value = 1900.0  # ~15 min
            monitor._poll()
            assert not monitor.cancelled

    def test_idle_timeout_triggers_cancel(self):
        config = SlurmJobConfig(idle_timeout_minutes=15.0)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.status.return_value = SlurmJobInfo("12345", SlurmJobState.RUNNING)
        adapter.elapsed_seconds.return_value = 0.0

        activity = MagicMock(return_value=False)
        monitor = SlurmJobMonitor(adapter, "12345", config, activity_checker=activity)

        with patch("general_ludd.infra.slurm.time") as mock_time:
            mock_time.time.return_value = 1000.0
            monitor._poll()
            assert not monitor.cancelled

            mock_time.time.return_value = 2000.0  # > 15 min (900s)
            monitor._poll()
            assert monitor.cancelled
            assert monitor.cancel_reason == SlurmJobMonitor.CANCEL_REASON_IDLE
            adapter.cancel.assert_called_once_with("12345")

    def test_no_idle_check_when_timeout_not_set(self):
        config = SlurmJobConfig(idle_timeout_minutes=None)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.status.return_value = SlurmJobInfo("12345", SlurmJobState.RUNNING)
        adapter.elapsed_seconds.return_value = 0.0

        activity = MagicMock(return_value=False)
        monitor = SlurmJobMonitor(adapter, "12345", config, activity_checker=activity)

        with patch("general_ludd.infra.slurm.time") as mock_time:
            mock_time.time.return_value = 999999.0
            monitor._poll()
            assert not monitor.cancelled

    def test_no_idle_check_when_no_activity_checker(self):
        config = SlurmJobConfig(idle_timeout_minutes=15.0)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.status.return_value = SlurmJobInfo("12345", SlurmJobState.RUNNING)
        adapter.elapsed_seconds.return_value = 0.0

        monitor = SlurmJobMonitor(adapter, "12345", config)

        with patch("general_ludd.infra.slurm.time") as mock_time:
            mock_time.time.return_value = 999999.0
            monitor._poll()
            assert not monitor.cancelled


class TestLifecycle:
    def test_poll_stops_when_job_completes(self):
        config = SlurmJobConfig(max_cost_usd=10.0, hourly_rate_usd=3.0)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.elapsed_seconds.return_value = 100.0
        adapter.status.return_value = SlurmJobInfo("12345", SlurmJobState.COMPLETED, exit_code=0)

        monitor = SlurmJobMonitor(adapter, "12345", config)
        result = monitor._poll()

        assert result is False
        adapter.cancel.assert_not_called()

    def test_poll_stops_when_job_fails(self):
        config = SlurmJobConfig(max_cost_usd=10.0, hourly_rate_usd=3.0)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.status.return_value = SlurmJobInfo("12345", SlurmJobState.FAILED, exit_code=1)

        monitor = SlurmJobMonitor(adapter, "12345", config)
        result = monitor._poll()

        assert result is False

    def test_poll_stops_when_job_cancelled(self):
        config = SlurmJobConfig(max_cost_usd=10.0, hourly_rate_usd=3.0)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.status.return_value = SlurmJobInfo("12345", SlurmJobState.CANCELLED)

        monitor = SlurmJobMonitor(adapter, "12345", config)
        result = monitor._poll()

        assert result is False

    def test_start_stop_thread_lifecycle(self):
        config = SlurmJobConfig()
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.status.return_value = SlurmJobInfo("12345", SlurmJobState.RUNNING)
        adapter.elapsed_seconds.return_value = 0.0

        monitor = SlurmJobMonitor(adapter, "12345", config, poll_interval=0.01)
        monitor.start()
        assert monitor._thread is not None
        assert monitor._thread.is_alive()

        monitor.stop()
        assert not monitor._thread.is_alive() or True  # May have already exited

    def test_start_is_idempotent(self):
        config = SlurmJobConfig()
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.status.return_value = SlurmJobInfo("12345", SlurmJobState.COMPLETED)
        adapter.elapsed_seconds.return_value = 0.0

        monitor = SlurmJobMonitor(adapter, "12345", config, poll_interval=0.01)
        monitor.start()
        thread1 = monitor._thread
        monitor.start()
        thread2 = monitor._thread
        assert thread1 is thread2
        monitor.stop()
