"""Tests for Slurm daemon router endpoints."""
from __future__ import annotations

import tempfile
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from general_ludd.daemon import create_daemon_app


def _make_test_app(config_dir: str | None = None):
    tmpdir = config_dir or tempfile.mkdtemp()
    return create_daemon_app(tick_interval=0.01, config_dir=tmpdir)


class TestSlurmStatusEndpoint:
    def test_status_returns_available_false(self):
        client = TestClient(_make_test_app())
        with patch("general_ludd.infra.slurm.subprocess.run", side_effect=FileNotFoundError):
            resp = client.get("/admin/slurm/status")
        assert resp.status_code == 200
        assert resp.json()["available"] is False

    def test_status_returns_available_true(self):
        client = TestClient(_make_test_app())
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            resp = client.get("/admin/slurm/status")
        assert resp.status_code == 200
        assert resp.json()["available"] is True


class TestSlurmSubmitEndpoint:
    def test_submit_returns_job_id(self):
        client = TestClient(_make_test_app())
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Submitted batch job 42\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            resp = client.post("/admin/slurm/submit", json={"command": "echo hello"})
        assert resp.status_code == 200
        assert resp.json()["job_id"] == "42"

    def test_submit_missing_command_returns_422(self):
        client = TestClient(_make_test_app())
        resp = client.post("/admin/slurm/submit", json={})
        assert resp.status_code == 422

    def test_submit_slurm_not_installed_returns_503(self):
        client = TestClient(_make_test_app())
        with patch("general_ludd.infra.slurm.subprocess.run", side_effect=FileNotFoundError):
            resp = client.post("/admin/slurm/submit", json={"command": "echo hello"})
        assert resp.status_code == 503

    def test_submit_sbatch_failure_returns_500(self):
        client = TestClient(_make_test_app())
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "sbatch: error"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            resp = client.post("/admin/slurm/submit", json={"command": "echo hello"})
        assert resp.status_code == 500

    def test_submit_with_options(self):
        client = TestClient(_make_test_app())
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Submitted batch job 99\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            resp = client.post("/admin/slurm/submit", json={
                "command": "train.py",
                "job_name": "my-job",
                "partition": "gpu",
                "cpus_per_task": 4,
                "gpus": "1",
                "memory": "16G",
                "time_limit": "02:00:00",
            })
        assert resp.status_code == 200
        assert resp.json()["job_id"] == "99"


class TestSlurmJobStatusEndpoint:
    def test_job_status_returns_info(self):
        client = TestClient(_make_test_app())
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "12345|COMPLETED|0\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            resp = client.get("/admin/slurm/jobs/12345")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == "12345"
        assert data["state"] == "COMPLETED"
        assert data["exit_code"] == 0

    def test_job_status_running(self):
        client = TestClient(_make_test_app())
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "67890|RUNNING|\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            resp = client.get("/admin/slurm/jobs/67890")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "RUNNING"
        assert data["exit_code"] is None

    def test_job_status_not_installed_returns_503(self):
        client = TestClient(_make_test_app())
        with patch("general_ludd.infra.slurm.subprocess.run", side_effect=FileNotFoundError):
            resp = client.get("/admin/slurm/jobs/12345")
        assert resp.status_code == 503


class TestSlurmJobCancelEndpoint:
    def test_cancel_returns_cancelled(self):
        client = TestClient(_make_test_app())
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            resp = client.delete("/admin/slurm/jobs/12345")
        assert resp.status_code == 200
        assert resp.json()["cancelled"] == "12345"

    def test_cancel_not_installed_returns_503(self):
        client = TestClient(_make_test_app())
        with patch("general_ludd.infra.slurm.subprocess.run", side_effect=FileNotFoundError):
            resp = client.delete("/admin/slurm/jobs/12345")
        assert resp.status_code == 503

    def test_cancel_failure_returns_500(self):
        client = TestClient(_make_test_app())
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "scancel: error"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            resp = client.delete("/admin/slurm/jobs/99999")
        assert resp.status_code == 500


class TestSlurmJobsListEndpoint:
    def test_jobs_list_returns_empty(self):
        client = TestClient(_make_test_app())
        resp = client.get("/admin/slurm/jobs")
        assert resp.status_code == 200
        assert "jobs" in resp.json()
