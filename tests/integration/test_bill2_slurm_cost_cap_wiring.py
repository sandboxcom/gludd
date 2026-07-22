"""Integration tests: bill-2 Slurm cost cap daemon wiring.

Tests SlurmJobMonitor cost cap and idle detection lifecycle through
the daemon cost endpoint, verifying proper wiring of hourly_rate,
max_cost_usd, and idle_timeout_minutes.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import general_ludd.daemon as daemon_mod
from general_ludd.daemon import create_daemon_app
from general_ludd.infra.slurm import (
    SlurmJobConfig,
    SlurmJobInfo,
    SlurmJobMonitor,
    SlurmJobState,
    _parse_elapsed,
)


@pytest.fixture(autouse=True)
def _reset_daemon_state():
    """Isolate the module-level ``_daemon_state`` shim around each test.

    ``general_ludd.daemon._daemon_state`` starts life as ``None`` — it is only a
    migration shim. ``create_daemon_app()`` allocates a FRESH per-app dict on
    ``app.state.daemon_state`` (the authoritative store) and merely rebinds the
    module global to it (daemon.py:2565-2575). Writing into the shim at
    fixture-setup time — before any app exists — raised ``TypeError: 'NoneType'
    object does not support item assignment``. Snapshot/restore is the correct
    isolation: nothing needs pre-seeding, the shim just must not leak across
    tests.
    """
    original = daemon_mod._daemon_state
    daemon_mod._daemon_state = None
    yield
    daemon_mod._daemon_state = original


def _make_db_config(tmp_path: pytest.Path) -> str:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    db_path = tmp_path / "test.db"
    (config_dir / "general-ludd.yml").write_text(
        f"database:\n  url: 'sqlite+aiosqlite:///{db_path}'\n"
    )
    return str(config_dir)


class TestSlurmCostCapWiring:
    """Integration tests for SlurmJobMonitor cost cap and idle wiring."""

    def test_cost_cap_check_exceed_threshold_cancels(self):
        """Monitor cancels job when accumulated cost exceeds max_cost_usd."""
        config = SlurmJobConfig(max_cost_usd=20.0, hourly_rate_usd=15.0)
        adapter = MagicMock()
        adapter.elapsed_seconds.side_effect = [3600.0, 7200.0]
        adapter.status.side_effect = [
            SlurmJobInfo("1001", SlurmJobState.RUNNING),
            SlurmJobInfo("1001", SlurmJobState.RUNNING),
        ]

        monitor = SlurmJobMonitor(adapter, "1001", config)

        monitor._poll()
        assert monitor.cost_incurred == 15.0
        assert not monitor.cancelled

        monitor._poll()
        assert monitor.cost_incurred == 30.0
        assert monitor.cancelled
        assert monitor.cancel_reason == SlurmJobMonitor.CANCEL_REASON_COST
        adapter.cancel.assert_called_once_with("1001")

    def test_cost_cap_not_exceeded_continues(self):
        """Monitor does not cancel when cost is under max_cost_usd."""
        config = SlurmJobConfig(max_cost_usd=999.0, hourly_rate_usd=10.0)
        adapter = MagicMock()
        adapter.elapsed_seconds.return_value = 3600.0
        adapter.status.return_value = SlurmJobInfo(
            "1002", SlurmJobState.RUNNING
        )

        monitor = SlurmJobMonitor(adapter, "1002", config)
        result = monitor._poll()
        assert result is True
        assert monitor.cost_incurred == 10.0
        assert not monitor.cancelled
        adapter.cancel.assert_not_called()

    def test_idle_detection_fires_and_cancels(self):
        """Monitor cancels when activity_checker returns False past idle_timeout."""
        config = SlurmJobConfig(idle_timeout_minutes=10.0)
        adapter = MagicMock()
        adapter.elapsed_seconds.return_value = 0.0
        adapter.status.return_value = SlurmJobInfo(
            "1003", SlurmJobState.RUNNING
        )

        activity = MagicMock(return_value=False)
        monitor = SlurmJobMonitor(
            adapter, "1003", config, activity_checker=activity
        )

        with patch("general_ludd.infra.slurm.time") as mock_time:
            mock_time.time.return_value = 100.0
            monitor._poll()
            assert not monitor.cancelled

            mock_time.time.return_value = 1000.0
            monitor._poll()
            assert monitor.cancelled
            assert monitor.cancel_reason == SlurmJobMonitor.CANCEL_REASON_IDLE
            adapter.cancel.assert_called_once_with("1003")

    def test_activity_checker_resets_idle_timer(self):
        """Active requests reset the idle timer so monitor does not cancel."""
        config = SlurmJobConfig(idle_timeout_minutes=10.0)
        adapter = MagicMock()
        adapter.elapsed_seconds.return_value = 0.0
        adapter.status.return_value = SlurmJobInfo(
            "1004", SlurmJobState.RUNNING
        )

        activity = MagicMock(return_value=True)
        monitor = SlurmJobMonitor(
            adapter, "1004", config, activity_checker=activity
        )

        with patch("general_ludd.infra.slurm.time") as mock_time:
            for t in [100.0, 400.0, 700.0, 1000.0]:
                mock_time.time.return_value = t
                monitor._poll()
                assert not monitor.cancelled

        adapter.cancel.assert_not_called()

    def test_monitor_stops_on_terminal_state(self):
        """Monitor exits polling loop when job reaches terminal state."""
        config = SlurmJobConfig(max_cost_usd=50.0, hourly_rate_usd=10.0)
        adapter = MagicMock()
        adapter.elapsed_seconds.return_value = 3600.0
        adapter.status.return_value = SlurmJobInfo(
            "1005", SlurmJobState.COMPLETED, exit_code=0
        )

        monitor = SlurmJobMonitor(adapter, "1005", config)
        result = monitor._poll()
        assert result is False
        adapter.cancel.assert_not_called()

    def test_cost_endpoint_in_daemon_returns_monitor_data(
        self, tmp_path: pytest.Path
    ):
        """GET /admin/slurm/jobs/{id}/cost returns cost data from adapter."""
        mock_adapter = MagicMock()
        mock_adapter.available.return_value = True
        mock_adapter.status.return_value = SlurmJobInfo(
            "1008",
            SlurmJobState.RUNNING,
            cost_incurred=25.50,
        )

        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ), patch(
            "general_ludd.routers.slurm.SlurmAdapter",
            return_value=mock_adapter,
        ):
            app = create_daemon_app(
                tick_interval=300.0, config_dir=_make_db_config(tmp_path)
            )
            with TestClient(app) as client:
                resp = client.get("/admin/slurm/jobs/1008/cost")
                assert resp.status_code == 200
                data = resp.json()
                assert data["cost_breakdown"]["estimated_cost_usd"] == 25.50
                assert data["cost_breakdown"]["state"] == "RUNNING"

    def test_elapsed_seconds_parse_all_formats(self):
        """_parse_elapsed handles all standard sacct time formats."""
        assert _parse_elapsed("00:00:01") == 1.0
        assert _parse_elapsed("00:01:30") == 90.0
        assert _parse_elapsed("01:00:00") == 3600.0
        assert _parse_elapsed("1-00:00:00") == 86400.0
        assert _parse_elapsed("2-06:30:00") == 196200.0
        assert _parse_elapsed("00:00:00.500") == 0.5
        assert _parse_elapsed("UNLIMITED") is None
        assert _parse_elapsed("") is None
        assert _parse_elapsed("   ") is None

    def test_monitor_start_stop_thread_lifecycle(self):
        """Monitor thread starts cleanly and stops cleanly."""
        import threading

        config = SlurmJobConfig(max_cost_usd=10.0, hourly_rate_usd=5.0)
        adapter = MagicMock()
        adapter.elapsed_seconds.return_value = 0.0
        adapter.status.return_value = SlurmJobInfo(
            "1006", SlurmJobState.COMPLETED, exit_code=0
        )

        monitor = SlurmJobMonitor(adapter, "1006", config, poll_interval=0.01)
        monitor.start()
        assert monitor._thread is not None
        assert isinstance(monitor._thread, threading.Thread)

        monitor.stop()
        monitor._thread.join(timeout=1.0)
        assert not monitor._thread.is_alive()

    def test_start_is_idempotent(self):
        """Calling start() twice does not create a second thread."""
        config = SlurmJobConfig()
        adapter = MagicMock()
        adapter.status.return_value = SlurmJobInfo(
            "1007", SlurmJobState.COMPLETED
        )
        adapter.elapsed_seconds.return_value = 0.0

        monitor = SlurmJobMonitor(adapter, "1007", config, poll_interval=0.01)
        monitor.start()
        t1 = monitor._thread
        monitor.start()
        t2 = monitor._thread
        assert t1 is t2
        monitor.stop()
