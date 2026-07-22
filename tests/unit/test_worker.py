"""Unit tests for worker app."""

from __future__ import annotations

import os
import tempfile
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.worker.app import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def transport(app):
    return ASGITransport(app=app)


def _make_runner_result(
    status: str = "successful",
    rc: int = 0,
    events: list[dict[str, Any]] | None = None,
) -> MagicMock:
    r = MagicMock()
    r.status = status
    r.rc = rc
    r.events = events or [{"event": "playbook_on_start"}]
    return r


def _make_adapter(
    tmp: str,
    job_id: str,
    events: list[dict[str, Any]] | None = None,
    artifacts: list[Any] | None = None,
) -> MagicMock:
    adapter = MagicMock()
    adapter.list_playbooks.return_value = ["noop.yml"]
    adapter.prepare_job_dirs.return_value = {
        "root": os.path.join(tmp, job_id),
        "env": os.path.join(tmp, job_id, "env"),
        "project": os.path.join(tmp, job_id, "project"),
        "inventory": os.path.join(tmp, job_id, "inventory"),
        "artifacts": os.path.join(tmp, job_id, "artifacts"),
    }
    adapter.write_vars.return_value = os.path.join(tmp, job_id, "env", "extravars")
    result: dict[str, Any] = {
        "status": "successful",
        "rc": 0,
        "events": events or [{"event": "playbook_on_start"}],
    }
    if artifacts is not None:
        result["artifacts"] = artifacts
    adapter.run_playbook.return_value = result
    return adapter


class TestWorkerApp:
    @pytest.mark.asyncio
    async def test_healthz(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200
            assert resp.json() == {"status": "healthy"}

    @pytest.mark.asyncio
    async def test_worker_rejects_unknown_playbook(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/jobs/execute", json={
                "job_id": "JOB-001",
                "playbook": "nonexistent.yml",
                "queue": "core",
            })
            assert resp.status_code == 400
            assert "Unknown playbook" in resp.json()["detail"]

    @pytest.mark.asyncio
    @patch("general_ludd.worker.app.get_runner")
    async def test_worker_execute_noop_playbook(self, mock_get_runner: MagicMock, app: Any) -> None:
        tmp = tempfile.mkdtemp()
        adapter = _make_adapter(tmp, "JOB-EXE")
        mock_get_runner.return_value = adapter

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/jobs/execute", json={
                "job_id": "JOB-EXE",
                "todo_id": "TODO-EXE",
                "playbook": "noop.yml",
                "queue": "core",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["exit_code"] == 0
            assert data["playbook"] == "noop.yml"
            assert data["job_id"] == "JOB-EXE"

    @pytest.mark.asyncio
    @patch("general_ludd.worker.app.get_runner")
    async def test_worker_writes_task_return_with_artifacts(self, mock_get_runner: MagicMock, app: Any) -> None:
        tmp = tempfile.mkdtemp()
        adapter = _make_adapter(
            tmp, "JOB-ART",
            events=[{"event": "runner_on_ok"}],
            artifacts=["artifact1.log"],
        )
        mock_get_runner.return_value = adapter

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/jobs/execute", json={
                "job_id": "JOB-ART",
                "todo_id": "TODO-ART",
                "playbook": "noop.yml",
                "queue": "core",
            })
            data = resp.json()
            assert data["exit_code"] == 0
            assert data["artifacts"] is not None

    @pytest.mark.asyncio
    @patch("general_ludd.worker.app.get_runner")
    async def test_worker_captures_runner_events(self, mock_get_runner: MagicMock, app: Any) -> None:
        tmp = tempfile.mkdtemp()
        events = [
            {"event": "playbook_on_start"},
            {"event": "runner_on_ok", "event_data": {"task": "debug"}},
        ]
        adapter = _make_adapter(tmp, "JOB-EVT", events=events)
        mock_get_runner.return_value = adapter

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/jobs/execute", json={
                "job_id": "JOB-EVT",
                "playbook": "noop.yml",
                "queue": "core",
            })
            data = resp.json()
            assert len(data["events"]) == 2

    @pytest.mark.asyncio
    @patch("general_ludd.worker.app.get_runner")
    async def test_worker_vars_files_created_correctly(self, mock_get_runner: MagicMock, app: Any) -> None:
        tmp = tempfile.mkdtemp()
        adapter = _make_adapter(tmp, "JOB-VAR", events=[])
        mock_get_runner.return_value = adapter

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/jobs/execute", json={
                "job_id": "JOB-VAR",
                "playbook": "noop.yml",
                "queue": "core",
                "budget_context": {"priority": "high"},
            })
            assert resp.status_code == 200
            adapter.write_vars.assert_called_once()
            call_kwargs = adapter.write_vars.call_args
            assert call_kwargs[1]["job_vars"]["job_id"] == "JOB-VAR"

    @pytest.mark.asyncio
    async def test_worker_redacts_secret_aliases_in_logs(self, transport, caplog):
        import logging
        with caplog.at_level(logging.INFO, logger="general_ludd.worker.app"):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/jobs/execute", json={
                    "job_id": "JOB-SEC",
                    "playbook": "noop.yml",
                    "queue": "core",
                    "vars_namespace_refs": ["secret/db_password"],
                })
                assert resp.status_code == 200
            for record in caplog.records:
                assert "secret/db_password" not in record.getMessage()

    @pytest.mark.asyncio
    async def test_worker_correlation_ids_in_responses(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/jobs/execute", json={
                "job_id": "JOB-CORR",
                "todo_id": "TODO-CORR",
                "playbook": "noop.yml",
                "queue": "core",
            })
            data = resp.json()
            assert data["job_id"] == "JOB-CORR"
            assert data["todo_id"] == "TODO-CORR"

    @pytest.mark.asyncio
    async def test_worker_return_review_endpoint(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/jobs/return-review", json={
                "job_id": "JOB-003",
                "playbook": "return_review.yml",
                "queue": "model",
            })
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_worker_validate_endpoint_returns_501_not_implemented(self, transport):
        # W3.8: /jobs/validate has no backing playbook — must return 501, not fake-success.
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/jobs/validate", json={
                "job_id": "JOB-004",
                "playbook": "noop.yml",
                "queue": "qa",
            })
            assert resp.status_code == 501
            data = resp.json()
            assert data["detail"]["reason"] == "not_implemented"

    @pytest.mark.asyncio
    async def test_worker_gunicorn_config_exists(self):
        import importlib
        mod = importlib.import_module("general_ludd.worker.gunicorn_conf")
        assert mod.worker_class == "uvicorn_worker.UvicornWorker"
        assert mod.workers == 2
        assert mod.timeout == 0

    def test_gunicorn_conf_max_requests(self):
        import importlib
        mod = importlib.import_module("general_ludd.worker.gunicorn_conf")
        assert mod.max_requests == 1000
        assert mod.max_requests_jitter == 50

    def test_gunicorn_on_reload(self):
        import importlib
        mod = importlib.import_module("general_ludd.worker.gunicorn_conf")
        arbiter = MagicMock()
        mod.on_reload(arbiter)

    def test_gunicorn_post_fork(self):
        import importlib
        mod = importlib.import_module("general_ludd.worker.gunicorn_conf")
        worker = MagicMock()
        worker.pid = 12345
        worker.spawned = True
        mod.post_fork(MagicMock(), worker)

    def test_gunicorn_pre_exec(self):
        import importlib
        mod = importlib.import_module("general_ludd.worker.gunicorn_conf")
        worker = MagicMock()
        worker.pid = 12345
        mod.pre_exec(worker)


class TestModelPerformanceRecording:
    @pytest.mark.asyncio
    @patch("general_ludd.worker.app._invoke_gateway_for_job")
    async def test_records_successful_model_call(
        self, mock_invoke: MagicMock, app: Any,
    ) -> None:
        mock_profile = MagicMock()
        mock_profile.provider = "test"
        mock_profile.model_name = "test-model"
        mock_profile.cost_per_input_token = 0.0
        mock_profile.cost_per_output_token = 0.0

        mock_gateway = MagicMock()
        mock_gateway.get_profile.return_value = mock_profile

        app.state.gateway = mock_gateway
        app.state.model_perf_repo = MagicMock()

        mock_invoke.return_value = ("response text", None)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/jobs/execute", json={
                "job_id": "JOB-PERF",
                "todo_id": "TODO-PERF",
                "playbook": "noop.yml",
                "queue": "model",
                "work_type": "code",
                "model_profile": "test-model",
                "prompt_text": "write a function",
            })
            assert resp.status_code == 200
            app.state.model_perf_repo.record_call_sync.assert_called_once_with(
                service="test",
                model_name="test-model",
                model_profile_id="test-model",
                task_type="generation",
                work_type="code",
                success=True,
                input_tokens=4,
                output_tokens=3,
                cost_usd=0.0,
                duration_ms=pytest.approx(0, abs=100),
                todo_id="TODO-PERF",
                job_id="JOB-PERF",
                error_message=None,
            )

    @pytest.mark.asyncio
    @patch("general_ludd.worker.app._invoke_gateway_for_job")
    async def test_records_failed_model_call(
        self, mock_invoke: MagicMock, app: Any,
    ) -> None:
        mock_profile = MagicMock()
        mock_profile.provider = "test"
        mock_profile.model_name = "test-model"
        mock_profile.cost_per_input_token = 0.0
        mock_profile.cost_per_output_token = 0.0

        mock_gateway = MagicMock()
        mock_gateway.get_profile.return_value = mock_profile

        app.state.gateway = mock_gateway
        app.state.model_perf_repo = MagicMock()

        mock_invoke.side_effect = ValueError("API error")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/jobs/execute", json={
                "job_id": "JOB-PERF-FAIL",
                "todo_id": "TODO-PERF-FAIL",
                "playbook": "noop.yml",
                "queue": "model",
                "work_type": "code",
                "model_profile": "test-model",
                "prompt_text": "write a function",
            })
            assert resp.status_code == 200
            app.state.model_perf_repo.record_call_sync.assert_called_once()
            _args, kwargs = app.state.model_perf_repo.record_call_sync.call_args
            assert kwargs["success"] is False
            assert kwargs["error_message"] == "API error"
            assert kwargs["service"] == "test"
            assert kwargs["model_name"] == "test-model"
            assert kwargs["model_profile_id"] == "test-model"
            assert kwargs["task_type"] == "generation"
            assert kwargs["work_type"] == "code"
            assert kwargs["input_tokens"] == 4
            assert kwargs["output_tokens"] == 0
            assert kwargs["cost_usd"] == 0.0
            assert kwargs["duration_ms"] > 0
            assert kwargs["todo_id"] == "TODO-PERF-FAIL"
            assert kwargs["job_id"] == "JOB-PERF-FAIL"
