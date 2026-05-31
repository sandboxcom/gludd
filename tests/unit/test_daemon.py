"""Unit tests for unified daemon app."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import agentic_harness.daemon as daemon_mod
from agentic_harness.daemon import create_daemon_app


@pytest.fixture(autouse=True)
def _reset_daemon_state():
    daemon_mod._daemon_state["todos"] = []
    daemon_mod._daemon_state["tick_metrics"] = {}


@pytest.fixture
def app():
    return create_daemon_app(tick_interval=0.01)


@pytest.fixture
def transport(app):
    return ASGITransport(app=app)


class TestDaemonApp:
    def test_create_daemon_app_returns_fastapi(self):
        from fastapi import FastAPI
        app = create_daemon_app()
        assert isinstance(app, FastAPI)

    @pytest.mark.asyncio
    async def test_healthz_endpoint(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200
            assert resp.json() == {"status": "healthy"}

    @pytest.mark.asyncio
    async def test_log_level_endpoint_changes_level(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/log-level", json={"level": "debug"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["level"] == "debug"
            root = logging.getLogger()
            assert root.level == logging.DEBUG
            logging.getLogger().setLevel(logging.WARNING)

    @pytest.mark.asyncio
    async def test_log_level_rejects_invalid_level(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/log-level", json={"level": "verbose"})
            assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_add_todo_endpoint(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/todos", json={
                "title": "Fix the login bug",
                "queue": "core",
                "priority": "high",
                "work_type": "code",
            })
            assert resp.status_code == 201
            data = resp.json()
            assert data["todo_id"].startswith("TODO-")
            assert data["title"] == "Fix the login bug"
            assert data["status"] == "queued"
            assert data["queue"] == "core"

    @pytest.mark.asyncio
    async def test_list_todos_endpoint(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/todos", json={"title": "Task A", "queue": "core"})
            await client.post("/api/todos", json={"title": "Task B", "queue": "infra"})
            resp = await client.get("/api/todos", params={"queue": "core"})
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["title"] == "Task A"

    @pytest.mark.asyncio
    async def test_get_todo_endpoint(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            create_resp = await client.post("/api/todos", json={"title": "Find me"})
            todo_id = create_resp.json()["todo_id"]
            resp = await client.get(f"/api/todos/{todo_id}")
            assert resp.status_code == 200
            assert resp.json()["title"] == "Find me"

    @pytest.mark.asyncio
    async def test_get_todo_not_found(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/todos/DOESNOTEXIST")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_status_endpoint(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/status")
            assert resp.status_code == 200
            data = resp.json()
            assert "queue_depths" in data
            assert "tick_metrics" in data

    @pytest.mark.asyncio
    async def test_deployments_endpoint(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/deployments")
            assert resp.status_code == 200
            assert isinstance(resp.json(), list)


class TestDaemonLifespan:
    @pytest.mark.asyncio
    async def test_lifespan_creates_event_loop_and_task(self):
        mock_loop = MagicMock()
        mock_loop.run_forever = AsyncMock()
        with patch("agentic_harness.event_loop.loop.EventLoop", return_value=mock_loop):
            from fastapi import FastAPI

            from agentic_harness.daemon import _lifespan
            app = FastAPI()
            app.state.tick_interval = 0.01
            app.state.event_loop = None
            async with _lifespan(app):
                assert app.state.event_loop is mock_loop
            mock_loop.stop.assert_called()

    @pytest.mark.asyncio
    async def test_lifespan_stops_event_loop_on_shutdown(self):
        mock_loop = MagicMock()
        mock_loop.run_forever = AsyncMock()
        with patch("agentic_harness.event_loop.loop.EventLoop", return_value=mock_loop):
            from fastapi import FastAPI

            from agentic_harness.daemon import _lifespan
            app = FastAPI()
            app.state.tick_interval = 0.01
            app.state.event_loop = None
            async with _lifespan(app):
                pass
            mock_loop.stop.assert_called()

    @pytest.mark.asyncio
    async def test_lifespan_handles_event_loop_failure(self):
        with patch("agentic_harness.event_loop.loop.EventLoop", side_effect=RuntimeError("boom")):
            from fastapi import FastAPI

            from agentic_harness.daemon import _lifespan
            app = FastAPI()
            app.state.tick_interval = 0.01
            app.state.event_loop = None
            async with _lifespan(app):
                assert app.state.event_loop is None


class TestDirectDispatch:
    @pytest.mark.asyncio
    async def test_event_loop_with_runner_dispatches_directly(self):
        from agentic_harness.event_loop.loop import EventLoop
        mock_runner = MagicMock()
        mock_runner.prepare_job_dirs.return_value = {
            "root": "/tmp/test",
            "env": "/tmp/test/env",
            "project": "/tmp/test/project",
            "inventory": "/tmp/test/inventory",
            "artifacts": "/tmp/test/artifacts",
        }
        mock_runner.write_vars.return_value = "/tmp/test/env/extravars"
        mock_runner.run_playbook.return_value = {"status": "successful", "rc": 0, "events": []}
        loop = EventLoop(runner=mock_runner)
        todo = MagicMock()
        todo.todo_id = "TODO-001"
        todo.queue = "core"
        todo.work_type = "code"
        todo.resource_profile = "low_resource"
        todo.plan_artifact = None
        await loop._dispatch_execute_job(todo)
        mock_runner.run_playbook.assert_called_once()

    @pytest.mark.asyncio
    async def test_event_loop_with_runner_dispatches_review_directly(self):
        from agentic_harness.event_loop.loop import EventLoop
        mock_runner = MagicMock()
        mock_runner.prepare_job_dirs.return_value = {
            "root": "/tmp/test",
            "env": "/tmp/test/env",
            "project": "/tmp/test/project",
            "inventory": "/tmp/test/inventory",
            "artifacts": "/tmp/test/artifacts",
        }
        mock_runner.write_vars.return_value = "/tmp/test/env/extravars"
        mock_runner.run_playbook.return_value = {"status": "successful", "rc": 0, "events": []}
        loop = EventLoop(runner=mock_runner)
        tr = MagicMock()
        tr.return_id = "RET-001"
        tr.todo_id = "TODO-001"
        tr.queue = "model"
        tr.plan_artifact = None
        await loop._dispatch_review_job(tr)
        mock_runner.run_playbook.assert_called_once()

    @pytest.mark.asyncio
    async def test_event_loop_without_runner_falls_back_to_http(self):
        from agentic_harness.event_loop.loop import EventLoop
        http_client = AsyncMock()
        http_client.post.return_value = MagicMock(status_code=202)
        loop = EventLoop(worker_base_url="http://worker:8000", http_client=http_client)
        todo = MagicMock()
        todo.todo_id = "TODO-001"
        todo.queue = "core"
        todo.work_type = "code"
        todo.resource_profile = "low_resource"
        todo.plan_artifact = None
        await loop._dispatch_execute_job(todo)
        http_client.post.assert_called_once()
        assert "execute" in http_client.post.call_args[0][0]

    @pytest.mark.asyncio
    async def test_event_loop_without_runner_review_falls_back_to_http(self):
        from agentic_harness.event_loop.loop import EventLoop
        http_client = AsyncMock()
        http_client.post.return_value = MagicMock(status_code=202)
        loop = EventLoop(worker_base_url="http://worker:8000", http_client=http_client)
        tr = MagicMock()
        tr.return_id = "RET-001"
        tr.todo_id = "TODO-001"
        tr.queue = "model"
        tr.plan_artifact = None
        await loop._dispatch_review_job(tr)
        http_client.post.assert_called_once()
        assert "return-review" in http_client.post.call_args[0][0]
