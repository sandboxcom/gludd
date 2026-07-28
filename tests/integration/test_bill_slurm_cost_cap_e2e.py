"""End-to-end tests: SlurmJobMonitor cost caps + idle detection lifecycle."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from general_ludd.infra.slurm import (
    SlurmAdapter,
    SlurmJobConfig,
    SlurmJobInfo,
    SlurmJobMonitor,
    SlurmJobState,
    _parse_elapsed,
)


class TestSlurmCostCapE2E:
    def test_monitor_full_lifecycle_under_cap_completes(self):
        config = SlurmJobConfig(max_cost_usd=50.0, hourly_rate_usd=10.0)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.elapsed_seconds.return_value = 3600.0
        adapter.status.return_value = SlurmJobInfo("1001", SlurmJobState.COMPLETED, exit_code=0)

        monitor = SlurmJobMonitor(adapter, "1001", config)
        result = monitor._poll()
        assert result is False
        assert monitor.cost_incurred == 10.0
        assert not monitor.cancelled
        adapter.cancel.assert_not_called()

    def test_monitor_full_lifecycle_exceeds_cap_cancels(self):
        config = SlurmJobConfig(max_cost_usd=20.0, hourly_rate_usd=15.0)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.elapsed_seconds.side_effect = [3600.0, 7200.0]
        adapter.status.side_effect = [
            SlurmJobInfo("1002", SlurmJobState.RUNNING),
            SlurmJobInfo("1002", SlurmJobState.RUNNING),
        ]

        monitor = SlurmJobMonitor(adapter, "1002", config)
        monitor._poll()
        assert monitor.cost_incurred == 15.0
        assert not monitor.cancelled

        monitor._poll()
        assert monitor.cost_incurred == 30.0
        assert monitor.cancelled
        assert monitor.cancel_reason == SlurmJobMonitor.CANCEL_REASON_COST
        adapter.cancel.assert_called_once_with("1002")

    def test_monitor_idle_detection_fires_and_cancels(self):
        config = SlurmJobConfig(idle_timeout_minutes=10.0)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.elapsed_seconds.return_value = 0.0
        adapter.status.return_value = SlurmJobInfo("1003", SlurmJobState.RUNNING)

        activity = MagicMock(return_value=False)
        monitor = SlurmJobMonitor(adapter, "1003", config, activity_checker=activity)

        with patch("general_ludd.infra.slurm.time") as mock_time:
            mock_time.time.return_value = 100.0
            monitor._poll()
            assert not monitor.cancelled

            mock_time.time.return_value = 1000.0
            monitor._poll()
            assert monitor.cancelled
            assert monitor.cancel_reason == SlurmJobMonitor.CANCEL_REASON_IDLE
            adapter.cancel.assert_called_once_with("1003")

    def test_thread_lifecycle_start_poll_stop(self):
        config = SlurmJobConfig(max_cost_usd=10.0, hourly_rate_usd=5.0)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.elapsed_seconds.return_value = 0.0
        adapter.status.return_value = SlurmJobInfo("1004", SlurmJobState.RUNNING)

        monitor = SlurmJobMonitor(adapter, "1004", config, poll_interval=0.01)
        monitor.start()
        assert monitor._thread is not None
        assert isinstance(monitor._thread, threading.Thread)
        assert monitor._thread.is_alive()

        monitor.stop()
        monitor._thread.join(timeout=1.0)
        assert not monitor._thread.is_alive()

    def test_parse_elapsed_all_slurm_formats(self):
        assert _parse_elapsed("00:00:01") == 1.0
        assert _parse_elapsed("00:01:30") == 90.0
        assert _parse_elapsed("01:00:00") == 3600.0
        assert _parse_elapsed("1-00:00:00") == 86400.0
        assert _parse_elapsed("2-06:30:00") == 196200.0
        assert _parse_elapsed("00:00:00.500") == 0.5
        assert _parse_elapsed("UNLIMITED") is None
        assert _parse_elapsed("") is None
        assert _parse_elapsed("   ") is None

    def test_no_crash_when_elapsed_is_none(self):
        config = SlurmJobConfig(max_cost_usd=50.0, hourly_rate_usd=10.0)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.elapsed_seconds.return_value = None
        adapter.status.return_value = SlurmJobInfo("1005", SlurmJobState.RUNNING)

        monitor = SlurmJobMonitor(adapter, "1005", config)
        result = monitor._poll()
        assert result is True
        assert monitor.cost_incurred == 0.0
        adapter.cancel.assert_not_called()

    def test_stop_marks_thread_complete(self):
        config = SlurmJobConfig()
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.status.return_value = SlurmJobInfo("1006", SlurmJobState.RUNNING)
        adapter.elapsed_seconds.return_value = 0.0

        monitor = SlurmJobMonitor(adapter, "1006", config, poll_interval=0.05)
        monitor.start()
        assert monitor._thread is not None
        monitor.stop()
        monitor._thread.join(timeout=2.0)
        assert not monitor._thread.is_alive()

    def test_start_is_idempotent(self):
        config = SlurmJobConfig()
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.status.return_value = SlurmJobInfo("1007", SlurmJobState.COMPLETED)
        adapter.elapsed_seconds.return_value = 0.0

        monitor = SlurmJobMonitor(adapter, "1007", config, poll_interval=0.01)
        monitor.start()
        t1 = monitor._thread
        monitor.start()
        t2 = monitor._thread
        assert t1 is t2
        monitor.stop()
