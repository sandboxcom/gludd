"""Integration tests: bill-1 Slurm billing daemon wiring.

Tests the /admin/slurm/* endpoints through TestClient with mocked SlurmAdapter,
verifying billing fields (account, qos, time_limit) propagate correctly.
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
    SlurmJobState,
)


@pytest.fixture(autouse=True)
def _reset_daemon_state():
    daemon_mod._daemon_state["todos"] = []
    daemon_mod._daemon_state["tick_metrics"] = {}
    yield
    daemon_mod._daemon_state["todos"] = []
    daemon_mod._daemon_state["tick_metrics"] = {}


def _make_db_config(tmp_path: pytest.Path) -> str:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    db_path = tmp_path / "test.db"
    (config_dir / "general-ludd.yml").write_text(
        f"database:\n  url: 'sqlite+aiosqlite:///{db_path}'\n"
    )
    return str(config_dir)


class TestSlurmBillingWiring:
    """Daemon endpoint tests for Slurm billing fields propagation."""

    def test_slurm_billing_config_roundtrip(self):
        """SlurmJobConfig billing fields survive create/read roundtrip."""
        cfg = SlurmJobConfig(
            account="billing-acct",
            qos="high-priority",
            time_limit_str="02:30:00",
            max_cost_usd=50.0,
            hourly_rate_usd=25.0,
            idle_timeout_minutes=30.0,
        )
        assert cfg.account == "billing-acct"
        assert cfg.qos == "high-priority"
        assert cfg.time_limit_str == "02:30:00"
        assert cfg.max_cost_usd == 50.0
        assert cfg.hourly_rate_usd == 25.0
        assert cfg.idle_timeout_minutes == 30.0

    def test_submit_endpoint_passes_account_and_qos(self, tmp_path: pytest.Path):
        """POST /admin/slurm/submit propagates account + qos to adapter."""
        mock_adapter = MagicMock()
        mock_adapter.submit.return_value = "job-account-001"
        mock_adapter.available.return_value = True
        mock_adapter.status.return_value = SlurmJobInfo(
            "job-account-001", SlurmJobState.RUNNING
        )
        mock_adapter.list_jobs.return_value = []

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
                resp = client.post("/admin/slurm/submit", json={
                    "command": "python train.py --epochs 100",
                    "account": "billing-dept-42",
                    "qos": "high-priority",
                    "time_limit": "01:30:00",
                    "job_name": "train-job",
                    "gpus": 4,
                })
                assert resp.status_code == 200
                data = resp.json()
                assert data["job_id"] == "job-account-001"

                call_kwargs = mock_adapter.submit.call_args.kwargs
                assert call_kwargs["account"] == "billing-dept-42"
                assert call_kwargs["qos"] == "high-priority"
                assert call_kwargs["time_limit"] == "01:30:00"
                assert call_kwargs["gpus"] == 4

    def test_status_endpoint_returns_billing_fields(self, tmp_path: pytest.Path):
        """GET /admin/slurm/jobs/{job_id} returns state and exit_code."""
        mock_adapter = MagicMock()
        mock_adapter.available.return_value = True
        mock_adapter.status.return_value = SlurmJobInfo(
            "job-status-001",
            SlurmJobState.COMPLETED,
            exit_code=0,
            cost_incurred=12.75,
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
                resp = client.get("/admin/slurm/jobs/job-status-001")
                assert resp.status_code == 200
                data = resp.json()
                assert data["job_id"] == "job-status-001"
                assert data["state"] == "COMPLETED"
                assert data["exit_code"] == 0

    def test_cost_endpoint_returns_breakdown(self, tmp_path: pytest.Path):
        """GET /admin/slurm/jobs/{job_id}/cost returns cost_breakdown."""
        mock_adapter = MagicMock()
        mock_adapter.available.return_value = True
        mock_adapter.status.return_value = SlurmJobInfo(
            "job-cost-001",
            SlurmJobState.RUNNING,
            cost_incurred=7.50,
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
                resp = client.get("/admin/slurm/jobs/job-cost-001/cost")
                assert resp.status_code == 200
                data = resp.json()
                assert data["job_id"] == "job-cost-001"
                assert "cost_breakdown" in data
                assert data["cost_breakdown"]["estimated_cost_usd"] == 7.50
                assert data["cost_breakdown"]["state"] == "RUNNING"

    def test_submit_without_command_returns_422(self, tmp_path: pytest.Path):
        """POST /admin/slurm/submit without command returns 422."""
        mock_adapter = MagicMock()
        mock_adapter.available.return_value = True

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
                resp = client.post("/admin/slurm/submit", json={})
                assert resp.status_code == 422

    def test_jobs_list_endpoint_returns_all(self, tmp_path: pytest.Path):
        """GET /admin/slurm/jobs returns list of all jobs."""
        mock_adapter = MagicMock()
        mock_adapter.available.return_value = True
        mock_adapter.list_jobs.return_value = [
            SlurmJobInfo("job-a", SlurmJobState.RUNNING),
            SlurmJobInfo("job-b", SlurmJobState.PENDING),
            SlurmJobInfo("job-c", SlurmJobState.COMPLETED),
        ]

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
                resp = client.get("/admin/slurm/jobs")
                assert resp.status_code == 200
                data = resp.json()
                assert "jobs" in data
                assert len(data["jobs"]) == 3
                states = {j["state"] for j in data["jobs"]}
                assert states == {"RUNNING", "PENDING", "COMPLETED"}

    def test_cancel_endpoint_returns_cancelled_id(self, tmp_path: pytest.Path):
        """DELETE /admin/slurm/jobs/{job_id} cancels and returns id."""
        mock_adapter = MagicMock()
        mock_adapter.available.return_value = True
        mock_adapter.cancel.return_value = None

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
                resp = client.delete("/admin/slurm/jobs/job-to-cancel")
                assert resp.status_code == 200
                data = resp.json()
                assert data["cancelled"] == "job-to-cancel"
                mock_adapter.cancel.assert_called_once_with("job-to-cancel")

    def test_status_endpoint_available(self, tmp_path: pytest.Path):
        """GET /admin/slurm/status returns availability."""
        mock_adapter = MagicMock()
        mock_adapter.available.return_value = True

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
                resp = client.get("/admin/slurm/status")
                assert resp.status_code == 200
                data = resp.json()
                assert data["available"] is True

    def test_slurm_not_installed_returns_503(self, tmp_path: pytest.Path):
        """All endpoints return 503 when SlurmNotInstalledError is raised."""
        from general_ludd.infra.slurm import SlurmNotInstalledError

        mock_adapter = MagicMock()
        mock_adapter.available.side_effect = SlurmNotInstalledError()

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
                resp = client.get("/admin/slurm/status")
                assert resp.status_code == 503

                mock_adapter.available.side_effect = None
                mock_adapter.submit.side_effect = SlurmNotInstalledError()
                resp = client.post("/admin/slurm/submit", json={
                    "command": "echo hi"
                })
                assert resp.status_code == 503
