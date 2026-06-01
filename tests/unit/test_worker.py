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
        adapter = MagicMock()
        adapter.prepare_job_dirs.return_value = {
            "root": os.path.join(tmp, "JOB-EXE"),
            "env": os.path.join(tmp, "JOB-EXE", "env"),
            "project": os.path.join(tmp, "JOB-EXE", "project"),
            "inventory": os.path.join(tmp, "JOB-EXE", "inventory"),
            "artifacts": os.path.join(tmp, "JOB-EXE", "artifacts"),
        }
        adapter.write_vars.return_value = os.path.join(tmp, "JOB-EXE", "env", "extravars")
        adapter.run_playbook.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [{"event": "playbook_on_start"}],
        }
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
        adapter = MagicMock()
        adapter.prepare_job_dirs.return_value = {
            "root": os.path.join(tmp, "JOB-ART"),
            "env": os.path.join(tmp, "JOB-ART", "env"),
            "project": os.path.join(tmp, "JOB-ART", "project"),
            "inventory": os.path.join(tmp, "JOB-ART", "inventory"),
            "artifacts": os.path.join(tmp, "JOB-ART", "artifacts"),
        }
        adapter.write_vars.return_value = os.path.join(tmp, "JOB-ART", "env", "extravars")
        adapter.run_playbook.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [{"event": "runner_on_ok"}],
        }
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
        adapter = MagicMock()
        adapter.prepare_job_dirs.return_value = {
            "root": os.path.join(tmp, "JOB-EVT"),
            "env": os.path.join(tmp, "JOB-EVT", "env"),
            "project": os.path.join(tmp, "JOB-EVT", "project"),
            "inventory": os.path.join(tmp, "JOB-EVT", "inventory"),
            "artifacts": os.path.join(tmp, "JOB-EVT", "artifacts"),
        }
        adapter.write_vars.return_value = os.path.join(tmp, "JOB-EVT", "env", "extravars")
        events = [
            {"event": "playbook_on_start"},
            {"event": "runner_on_ok", "event_data": {"task": "debug"}},
        ]
        adapter.run_playbook.return_value = {
            "status": "successful",
            "rc": 0,
            "events": events,
        }
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
        adapter = MagicMock()
        adapter.prepare_job_dirs.return_value = {
            "root": os.path.join(tmp, "JOB-VAR"),
            "env": os.path.join(tmp, "JOB-VAR", "env"),
            "project": os.path.join(tmp, "JOB-VAR", "project"),
            "inventory": os.path.join(tmp, "JOB-VAR", "inventory"),
            "artifacts": os.path.join(tmp, "JOB-VAR", "artifacts"),
        }
        adapter.write_vars.return_value = os.path.join(tmp, "JOB-VAR", "env", "extravars")
        adapter.run_playbook.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
        }
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
                assert "secret/db_password" not in record.message

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
    async def test_worker_validate_endpoint(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/jobs/validate", json={
                "job_id": "JOB-004",
                "playbook": "noop.yml",
                "queue": "qa",
            })
            assert resp.status_code == 200

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
