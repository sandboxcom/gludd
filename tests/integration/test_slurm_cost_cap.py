"""Integration tests: SlurmJobMonitor cost cap tracks jobs and enforces limits."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from general_ludd.infra.slurm import (
    SlurmAdapter,
    SlurmJobConfig,
    SlurmJobInfo,
    SlurmJobMonitor,
    SlurmJobState,
)


class TestSlurmCostCapIntegration:
    """Standalone integration test: SlurmJobMonitor with a mock adapter."""

    def test_monitor_starts_and_stops_cleanly(self):
        """Monitor can be started and stopped without errors."""
        from unittest.mock import patch

        adapter = SlurmAdapter()

        with patch.object(adapter, "status", return_value=SlurmJobInfo(
            job_id="1001", state=SlurmJobState.RUNNING,
        )), patch.object(adapter, "elapsed_seconds", return_value=30.0):
            # max_cost_usd is set high enough that the ~$0.017 cost sample
            # (30s @ $2/hr) never breaches the cap — this test only checks
            # clean start/stop, not cancellation, and uses a real (unmocked)
            # SlurmAdapter whose cancel() would shell out to `scancel`.
            config = SlurmJobConfig(max_cost_usd=10.0, hourly_rate_usd=2.0)
            monitor = SlurmJobMonitor(adapter=adapter, job_id="1001", config=config)
            monitor.start()
            time.sleep(0.3)  # Let the thread start and run at least one poll
            monitor.stop()
            assert monitor.cost_incurred >= 0.0

    def test_monitor_starts_tracking_job(self):
        """After submitting a job with max_cost_usd, the monitor begins tracking."""
        from unittest.mock import patch

        adapter = SlurmAdapter()

        with patch.object(adapter, "status", return_value=SlurmJobInfo(
            job_id="1002", state=SlurmJobState.RUNNING, cost_incurred=0.0
        )) as mock_status, patch.object(adapter, "elapsed_seconds", return_value=0.0):
            config = SlurmJobConfig(
                max_cost_usd=10.0,
                hourly_rate_usd=2.0,
            )
            monitor = SlurmJobMonitor(adapter=adapter, job_id="1002", config=config)
            monitor.start()
            time.sleep(0.2)
            monitor.stop()
            assert mock_status.called
            # The monitor should have polled at least once
            cost = monitor.cost_incurred
            assert cost >= 0.0

    def test_cost_cap_cancels_job(self):
        """When cost exceeds max_cost_usd, the monitor cancels the job."""
        from unittest.mock import MagicMock

        mock_adapter = MagicMock(spec=SlurmAdapter)
        mock_adapter.status.return_value = SlurmJobInfo(
            job_id="1003", state=SlurmJobState.RUNNING, cost_incurred=0.0
        )
        mock_adapter.elapsed_seconds.return_value = 3600.0  # 1 hour

        config = SlurmJobConfig(max_cost_usd=0.01, hourly_rate_usd=2.0)
        monitor = SlurmJobMonitor(
            adapter=mock_adapter,
            job_id="1003",
            config=config,
            poll_interval=0.1,
        )
        monitor.start()
        time.sleep(0.5)
        monitor.stop()

        # The monitor should have detected cost overrun and cancelled
        assert mock_adapter.cancel.called
        assert monitor.cancelled is True
        assert monitor.cancel_reason == SlurmJobMonitor.CANCEL_REASON_COST

    def test_monitor_idle_timeout(self):
        """When idle_timeout_minutes is set and no activity, job is cancelled."""
        from unittest.mock import MagicMock

        mock_adapter = MagicMock(spec=SlurmAdapter)
        mock_adapter.status.return_value = SlurmJobInfo(
            job_id="1004", state=SlurmJobState.RUNNING,
        )

        activity_checker = MagicMock(return_value=False)

        config = SlurmJobConfig(idle_timeout_minutes=0.001)
        monitor = SlurmJobMonitor(
            adapter=mock_adapter,
            job_id="1004",
            config=config,
            activity_checker=activity_checker,
            poll_interval=0.1,
        )
        monitor.start()
        time.sleep(0.8)
        monitor.stop()

        assert activity_checker.called
        assert mock_adapter.cancel.called
        assert monitor.cancel_reason == SlurmJobMonitor.CANCEL_REASON_IDLE

    def test_monitor_poll_interval_default(self):
        """Default poll_interval is 30 seconds."""
        monitor = SlurmJobMonitor(
            adapter=SlurmAdapter(),
            job_id="1005",
            config=SlurmJobConfig(),
        )
        assert monitor._poll_interval == 30.0

    def test_monitor_resolves_hourly_rate_from_config(self):
        """Monitor uses the config's hourly_rate_usd when set."""
        from unittest.mock import MagicMock

        mock_adapter = MagicMock(spec=SlurmAdapter)
        mock_adapter.status.return_value = SlurmJobInfo(
            job_id="1006", state=SlurmJobState.RUNNING,
        )
        mock_adapter.elapsed_seconds.return_value = 3600.0  # 1 hour

        config = SlurmJobConfig(max_cost_usd=100.0, hourly_rate_usd=3.75)
        monitor = SlurmJobMonitor(
            adapter=mock_adapter,
            job_id="1006",
            config=config,
            poll_interval=0.1,
        )
        monitor.start()
        time.sleep(0.3)
        monitor.stop()

        cost = monitor.cost_incurred
        # 1 hour at $3.75/hr = $3.75
        assert 3.7 <= cost <= 3.8

    def test_monitor_stops_on_terminal_state(self):
        """Monitor exits its poll loop when job reaches a terminal state."""
        mock_adapter = MagicMock(spec=SlurmAdapter)
        mock_adapter.status.return_value = SlurmJobInfo(
            job_id="1007", state=SlurmJobState.COMPLETED,
        )

        monitor = SlurmJobMonitor(
            adapter=mock_adapter,
            job_id="1007",
            config=SlurmJobConfig(),
            poll_interval=0.1,
        )
        monitor.start()
        time.sleep(0.3)
        monitor.stop()

        # Should have polled only once (terminal state returned on first poll)
        assert mock_adapter.status.call_count <= 2
