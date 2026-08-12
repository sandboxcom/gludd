"""Tests for Slurm daemon router endpoints."""

from __future__ import annotations

import os
import tempfile
import threading
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.daemon import create_daemon_app


def _make_test_app(config_dir: str | None = None):
    tmpdir = config_dir or tempfile.mkdtemp()
    return create_daemon_app(tick_interval=0.01, config_dir=tmpdir)


def _make_app_with_secrets(api_url: str | None = None, auth_token: str | None = None) -> FastAPI:
    app = _make_test_app()
    secrets = MagicMock()
    secrets.resolve.side_effect = lambda key: {
        "slurm_api_url": api_url,
        "slurm_auth_token": auth_token,
    }.get(key)
    app.state._secrets_resolver = secrets
    return app


# =========================================================================
# Original basic endpoint smoke tests
# =========================================================================


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
            resp = client.post(
                "/admin/slurm/submit",
                json={
                    "command": "train.py",
                    "job_name": "my-job",
                    "partition": "gpu",
                    "cpus_per_task": 4,
                    "gpus": "1",
                    "memory": "16G",
                    "time_limit": "02:00:00",
                },
            )
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
        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            resp = client.get("/admin/slurm/jobs")
        assert resp.status_code == 200
        assert "jobs" in resp.json()


class TestSlurmEventLoopOffload:
    """The async handlers must offload the blocking adapter calls to a worker
    thread (``asyncio.to_thread``) so a hung ``slurmctld`` cannot freeze the
    FastAPI event loop. We assert the synchronous ``subprocess.run`` runs on a
    thread OTHER than the event loop's main thread.
    """

    def test_status_runs_off_the_event_loop_thread(self):
        client = TestClient(_make_test_app())
        main_thread = threading.main_thread()
        seen: dict[str, object] = {}

        def _record(*args, **kwargs):
            seen["thread"] = threading.current_thread()
            result = MagicMock()
            result.returncode = 0
            return result

        with patch("general_ludd.infra.slurm.subprocess.run", side_effect=_record):
            resp = client.get("/admin/slurm/status")

        assert resp.status_code == 200
        assert seen["thread"] is not main_thread

    def test_submit_runs_off_the_event_loop_thread(self):
        client = TestClient(_make_test_app())
        main_thread = threading.main_thread()
        seen: dict[str, object] = {}

        def _record(*args, **kwargs):
            seen["thread"] = threading.current_thread()
            result = MagicMock()
            result.returncode = 0
            result.stdout = "Submitted batch job 7\n"
            return result

        with patch("general_ludd.infra.slurm.subprocess.run", side_effect=_record):
            resp = client.post("/admin/slurm/submit", json={"command": "echo hi"})

        assert resp.status_code == 200
        assert resp.json()["job_id"] == "7"
        assert seen["thread"] is not main_thread


# =========================================================================
# Deep tests — gaps not covered by the basic endpoint smoke above
# =========================================================================


class TestSlurmSecretsResolverPrecedence:
    """_resolve_slurm_creds: secrets resolver wins over env vars,
    env var fallback works when resolver is absent,
    resolver returning None falls through to env var."""

    def test_secrets_resolver_overrides_env(self):
        app = _make_app_with_secrets(
            api_url="https://slurm-via-secrets.example.com",
            auth_token="sec-token",
        )
        with patch.dict(os.environ, {"SLURM_API_URL": "https://env-url.example.com"}):
            client = TestClient(app)
            mock_result = MagicMock()
            mock_result.returncode = 0
            with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
                resp = client.get("/admin/slurm/status")
        assert resp.status_code == 200

    def test_env_var_only_when_no_resolver(self):
        app = _make_test_app()
        with patch.dict(os.environ, {"SLURM_API_URL": "https://env-only.example.com"}):
            client = TestClient(app)
            mock_result = MagicMock()
            mock_result.returncode = 0
            with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
                resp = client.get("/admin/slurm/status")
        assert resp.status_code == 200

    def test_resolver_returns_none_falls_through_to_env(self):
        app = _make_app_with_secrets(api_url=None, auth_token=None)
        with patch.dict(
            os.environ,
            {
                "SLURM_API_URL": "https://fallback.example.com",
                "SLURM_AUTH_TOKEN": "fb-token",
            },
        ):
            client = TestClient(app)
            mock_result = MagicMock()
            mock_result.returncode = 0
            with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
                resp = client.get("/admin/slurm/status")
        assert resp.status_code == 200

    def test_no_resolver_no_env_becomes_none(self):
        app = _make_test_app()
        with patch.dict(os.environ, {}, clear=True):
            client = TestClient(app)
            mock_result = MagicMock()
            mock_result.returncode = 0
            with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
                resp = client.get("/admin/slurm/status")
        assert resp.status_code == 200


class TestSlurmJobCostEndpoint:
    """GET /admin/slurm/jobs/{job_id}/cost — previously untested."""

    def test_cost_returns_breakdown(self):
        client = TestClient(_make_test_app())
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "42|COMPLETED|0\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            resp = client.get("/admin/slurm/jobs/42/cost")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == "42"
        assert "cost_breakdown" in data
        assert "estimated_cost_usd" in data["cost_breakdown"]
        assert data["cost_breakdown"]["state"] == "COMPLETED"

    def test_cost_not_installed_returns_503(self):
        client = TestClient(_make_test_app())
        with patch("general_ludd.infra.slurm.subprocess.run", side_effect=FileNotFoundError):
            resp = client.get("/admin/slurm/jobs/42/cost")
        assert resp.status_code == 503

    def test_cost_offloads_to_thread(self):
        client = TestClient(_make_test_app())
        main_thread = threading.main_thread()
        seen: dict[str, object] = {}

        def _record(*args, **kwargs):
            seen["thread"] = threading.current_thread()
            result = MagicMock()
            result.returncode = 0
            result.stdout = "7|COMPLETED|0\n"
            return result

        with patch("general_ludd.infra.slurm.subprocess.run", side_effect=_record):
            resp = client.get("/admin/slurm/jobs/7/cost")

        assert resp.status_code == 200
        assert seen["thread"] is not main_thread


class TestSlurmJobsListDeep:
    """GET /admin/slurm/jobs — list with actual jobs, error paths, thread offload."""

    def test_list_returns_multiple_jobs(self):
        client = TestClient(_make_test_app())
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "1|RUNNING\n2|PENDING\n3|COMPLETED\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            resp = client.get("/admin/slurm/jobs")
        assert resp.status_code == 200
        jobs = resp.json()["jobs"]
        assert len(jobs) == 3
        assert {j["job_id"] for j in jobs} == {"1", "2", "3"}

    def test_list_not_installed_returns_503(self):
        client = TestClient(_make_test_app())
        with patch("general_ludd.infra.slurm.subprocess.run", side_effect=FileNotFoundError):
            resp = client.get("/admin/slurm/jobs")
        assert resp.status_code == 503

    def test_list_generic_exception_returns_500(self):
        client = TestClient(_make_test_app())
        with patch("general_ludd.infra.slurm.subprocess.run", side_effect=RuntimeError("sacct died")):
            resp = client.get("/admin/slurm/jobs")
        assert resp.status_code == 500
        assert resp.json()["detail"] == "Slurm jobs request failed"
        assert "sacct died" not in resp.text

    def test_list_offloads_to_thread(self):
        client = TestClient(_make_test_app())
        main_thread = threading.main_thread()
        seen: dict[str, object] = {}

        def _record(*args, **kwargs):
            seen["thread"] = threading.current_thread()
            result = MagicMock()
            result.returncode = 0
            result.stdout = "1|RUNNING\n"
            return result

        with patch("general_ludd.infra.slurm.subprocess.run", side_effect=_record):
            resp = client.get("/admin/slurm/jobs")

        assert resp.status_code == 200
        assert seen["thread"] is not main_thread


class TestSlurmCancelDeep:
    """DELETE /admin/slurm/jobs/{job_id} — thread offload, invalid id error."""

    def test_cancel_offloads_to_thread(self):
        client = TestClient(_make_test_app())
        main_thread = threading.main_thread()
        seen: dict[str, object] = {}

        def _record(*args, **kwargs):
            seen["thread"] = threading.current_thread()
            result = MagicMock()
            result.returncode = 0
            return result

        with patch("general_ludd.infra.slurm.subprocess.run", side_effect=_record):
            resp = client.delete("/admin/slurm/jobs/12345")

        assert resp.status_code == 200
        assert seen["thread"] is not main_thread

    def test_cancel_invalid_job_id_returns_422(self):
        client = TestClient(_make_test_app())
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "scancel: Invalid job id"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            resp = client.delete("/admin/slurm/jobs/bad!id")
        assert resp.status_code == 422
        assert "invalid Slurm job id" in resp.json()["detail"]


class TestSlurmSubmitEdgeCases:
    """Additional submit edge cases beyond the basic smoke tests."""

    def test_submit_with_account_and_qos(self):
        client = TestClient(_make_test_app())
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Submitted batch job 77\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result) as mock_run:
            resp = client.post(
                "/admin/slurm/submit",
                json={
                    "command": "train.py",
                    "account": "cs-research",
                    "qos": "high",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["job_id"] == "77"
        assert mock_run.called

    def test_submit_with_extra_args(self):
        client = TestClient(_make_test_app())
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Submitted batch job 88\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result) as mock_run:
            resp = client.post(
                "/admin/slurm/submit",
                json={
                    "command": "script.sh",
                    "extra_args": ["--constraint=gpu", "--gres=gpu:2"],
                },
            )
        assert resp.status_code == 200
        assert resp.json()["job_id"] == "88"
        assert mock_run.called

    def test_submit_with_output_path(self):
        client = TestClient(_make_test_app())
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Submitted batch job 99\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result) as mock_run:
            resp = client.post(
                "/admin/slurm/submit",
                json={
                    "command": "echo hi",
                    "output": "/scratch/job-%j.out",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["job_id"] == "99"
        assert mock_run.called

    def test_submit_empty_command_returns_422(self):
        client = TestClient(_make_test_app())
        resp = client.post("/admin/slurm/submit", json={"command": ""})
        assert resp.status_code == 422

    def test_submit_runtime_error_returns_500(self):
        client = TestClient(_make_test_app())
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "sbatch: error: Invalid account"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            resp = client.post("/admin/slurm/submit", json={"command": "echo hi"})
        assert resp.status_code == 500

    def test_submit_invalid_option_returns_422(self):
        client = TestClient(_make_test_app())

        resp = client.post(
            "/admin/slurm/submit",
            json={"command": "true", "job_name": "bad\n#SBATCH --uid=root"},
        )

        assert resp.status_code == 422
        assert "invalid Slurm job_name" in resp.json()["detail"]


class TestSlurmRemoteAdapterRouting:
    """When SLURM_API_URL is set via secrets resolver, the adapter is
    constructed with api_url and routes to the REST path. The router
    should pass credentials through correctly."""

    def test_status_uses_rest_when_remote(self):
        app = _make_app_with_secrets(api_url="https://slurm.example.com")
        with patch("general_ludd.routers.slurm.SlurmAdapter") as MockAdapter:
            mock_instance = MockAdapter.return_value
            mock_instance.available.return_value = True
            client = TestClient(app)
            resp = client.get("/admin/slurm/status")
        assert resp.status_code == 200
        assert resp.json()["available"] is True
        MockAdapter.assert_called_once_with(
            api_url="https://slurm.example.com",
            auth_token=None,
        )

    def test_submit_uses_rest_when_remote(self):
        app = _make_app_with_secrets(api_url="https://slurm.example.com")
        with patch("general_ludd.routers.slurm.SlurmAdapter") as MockAdapter:
            mock_instance = MockAdapter.return_value
            mock_instance.submit.return_value = "200"
            client = TestClient(app)
            resp = client.post("/admin/slurm/submit", json={"command": "echo hi"})
        assert resp.status_code == 200
        assert resp.json()["job_id"] == "200"

    def test_job_cost_uses_adapter_status_with_cost(self):
        from general_ludd.infra.slurm import SlurmJobInfo, SlurmJobState

        app = _make_test_app()
        with patch("general_ludd.routers.slurm.SlurmAdapter") as MockAdapter:
            mock_instance = MockAdapter.return_value
            mock_instance.status.return_value = SlurmJobInfo(
                job_id="42",
                state=SlurmJobState.RUNNING,
                cost_incurred=1.23,
            )
            client = TestClient(app)
            resp = client.get("/admin/slurm/jobs/42/cost")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cost_breakdown"]["estimated_cost_usd"] == 1.23
        assert data["cost_breakdown"]["state"] == "RUNNING"


class TestSlurmConcurrentAccessSafety:
    """The adapter is constructed per-request; two concurrent requests should
    get independent adapter instances and not share mutable state."""

    def test_concurrent_status_requests(self):
        client = TestClient(_make_test_app())
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            r1 = client.get("/admin/slurm/status")
            r2 = client.get("/admin/slurm/status")
        assert r1.status_code == 200
        assert r2.status_code == 200

    def test_concurrent_status_and_submit(self):
        client = TestClient(_make_test_app())
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Submitted batch job 42\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            r1 = client.get("/admin/slurm/status")
            r2 = client.post("/admin/slurm/submit", json={"command": "echo hi"})
        assert r1.status_code == 200
        assert r2.status_code == 200


class TestSlurmStatusAdapterFailure:
    """Status endpoint — adapter-level errors that are NOT SlurmNotInstalledError."""

    def test_status_runtime_error_from_adapter(self):
        from general_ludd.infra.slurm import SlurmAdapter

        app = _make_test_app()
        with patch.object(SlurmAdapter, "available", side_effect=RuntimeError("slurmctld unreachable")):
            client = TestClient(app)
            resp = client.get("/admin/slurm/status")
        assert resp.status_code == 500
        assert resp.json()["detail"] == "Slurm status request failed"
        assert "slurmctld unreachable" not in resp.text

    def test_status_slurm_connection_error_from_adapter(self):
        from general_ludd.infra.slurm import SlurmAdapter, SlurmConnectionError

        app = _make_test_app()
        with patch.object(SlurmAdapter, "available", side_effect=SlurmConnectionError("unreachable")):
            client = TestClient(app)
            resp = client.get("/admin/slurm/status")
        assert resp.status_code == 503
        assert resp.json()["detail"] == "Slurm controller is unavailable"


class TestSlurmJobStatusDeep:
    """Status endpoint — adapter-level errors and UNKNOWN job."""

    def test_status_runtime_error_from_adapter(self):
        from general_ludd.infra.slurm import SlurmAdapter

        app = _make_test_app()
        with patch.object(SlurmAdapter, "status", side_effect=RuntimeError("sacct failed")):
            client = TestClient(app)
            resp = client.get("/admin/slurm/jobs/12345")
        assert resp.status_code == 500
        assert resp.json()["detail"] == "Slurm job status request failed"
        assert "sacct failed" not in resp.text

    def test_status_slurm_connection_error_from_adapter(self):
        from general_ludd.infra.slurm import SlurmAdapter, SlurmConnectionError

        app = _make_test_app()
        with patch.object(SlurmAdapter, "status", side_effect=SlurmConnectionError("unreachable")):
            client = TestClient(app)
            resp = client.get("/admin/slurm/jobs/12345")
        assert resp.status_code == 503
        assert resp.json()["detail"] == "Slurm controller is unavailable"

    def test_status_unknown_job_returns_200(self):
        """sacct returns empty → UNKNOWN state, endpoint returns 200 (not 404)."""
        client = TestClient(_make_test_app())
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            resp = client.get("/admin/slurm/jobs/999999")
        assert resp.status_code == 200

    def test_status_invalid_job_id_returns_422(self):
        client = TestClient(_make_test_app())

        resp = client.get("/admin/slurm/jobs/not-a-job")

        assert resp.status_code == 422
        assert "invalid Slurm job id" in resp.json()["detail"]


class TestSlurmAdapterPerRequestIsolation:
    """Each request creates a fresh adapter (no shared state via self._api_url)."""

    def test_status_and_submit_get_different_adapters(self):
        from general_ludd.routers.slurm import SlurmAdapter

        app = _make_test_app()
        with patch("general_ludd.routers.slurm.SlurmAdapter", wraps=SlurmAdapter) as MockAdapter:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "Submitted batch job 1\n"
            with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
                client = TestClient(app)
                client.get("/admin/slurm/status")
                client.post("/admin/slurm/submit", json={"command": "echo hi"})
            assert MockAdapter.call_count == 2


class TestSlurmSubmitParamValidationBypass:
    """Router casts req.get values; verify that None values pass through correctly
    without triggering the adapter's _validate_submit_params prematurely."""

    def test_submit_omits_optional_params_correctly(self):
        client = TestClient(_make_test_app())
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Submitted batch job 55\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            resp = client.post(
                "/admin/slurm/submit",
                json={
                    "command": "true",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["job_id"] == "55"

    def test_submit_with_all_top_level_params(self):
        client = TestClient(_make_test_app())
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Submitted batch job 999\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            resp = client.post(
                "/admin/slurm/submit",
                json={
                    "command": "all-the-things",
                    "job_name": "big-job",
                    "partition": "gpu",
                    "cpus_per_task": 8,
                    "gpus": "4",
                    "memory": "64G",
                    "time_limit": "12:00:00",
                    "account": "acct",
                    "qos": "premium",
                    "output": "/scratch/%j.out",
                    "extra_args": ["--nice=100"],
                },
            )
        assert resp.status_code == 200
        assert resp.json()["job_id"] == "999"
