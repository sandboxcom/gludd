"""Unit tests for unified daemon app."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import general_ludd.daemon as daemon_mod
from general_ludd.daemon import create_daemon_app


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
            # A-3 (redteam): /healthz now also advertises the auth security
            # posture (no_auth / auth_degraded). The liveness `status` field keeps
            # its original "healthy" value for back-compat.
            body = resp.json()
            assert body["status"] == "healthy"
            assert "no_auth" in body
            assert "auth_degraded" in body

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
            assert "version" in data
            assert isinstance(data["version"], str)
            assert "uptime_ticks" in data
            assert isinstance(data["uptime_ticks"], int)
            assert "todos_total" in data
            assert "queue_depths" in data
            assert "tick_metrics" in data
            assert "config_dir" in data
            assert "config_files" in data
            assert "filestore_root" in data
            assert "filestore_binaries" in data
            assert "db_engine" in data
            assert "db_url" in data

    @pytest.mark.asyncio
    async def test_deployments_endpoint(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/deployments")
            assert resp.status_code == 200
            assert isinstance(resp.json(), list)


class TestDaemonStartupConfig:
    def test_create_app_with_config_dir_loads_config(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "model_routing.yml").write_text(
            "default_profile: test_prof\nrole_routing:\n  coder: test_prof\n"
        )
        app = create_daemon_app(config_dir=str(config_dir))
        assert app.state._config_dir == str(config_dir)

    def test_create_app_without_config_dir_still_works(self):
        app = create_daemon_app()
        assert app.state._config_dir is None

    def test_load_startup_config_loads_model_routing(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "model_routing.yml").write_text(
            "default_profile: my_profile\n"
        )
        cfg = load_startup_config(config_dir=str(config_dir))
        assert cfg["model_routing"].default_profile == "my_profile"

    def test_load_startup_config_loads_user_config(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "general-ludd.yml").write_text(
            "database:\n  url: postgresql://localhost/gludd\n"
        )
        cfg = load_startup_config(config_dir=str(config_dir))
        assert cfg["user_config"].database["url"] == "postgresql://localhost/gludd"

    def test_load_startup_config_handles_missing_dir(self):
        from general_ludd.daemon import load_startup_config

        cfg = load_startup_config(config_dir="/nonexistent")
        assert cfg["model_routing"].default_profile is None
        assert cfg["user_config"] is not None

    def test_load_startup_config_loads_binary_paths(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "binary_paths.yml").write_text(
            "binary_paths:\n  terraform: /usr/local/bin/terraform\n"
        )
        cfg = load_startup_config(config_dir=str(config_dir))
        assert cfg["binary_paths"] is not None

    def test_load_startup_config_loads_openbao(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        openbao_dir = config_dir / "openbao"
        openbao_dir.mkdir()
        (openbao_dir / "default.yml").write_text("mode: external\nexternal_url: http://bao:8200\n")
        cfg = load_startup_config(config_dir=str(config_dir))
        assert cfg["openbao_config"] is not None

    def test_load_startup_config_loads_process_isolation(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        ansible_dir = config_dir / "ansible"
        ansible_dir.mkdir()
        (ansible_dir / "isolation.yml").write_text(
            "process_isolation:\n  enabled: true\n  executable: docker\n"
        )
        cfg = load_startup_config(config_dir=str(config_dir))
        assert cfg["process_isolation"] is not None


class TestDaemonLifespan:
    @pytest.mark.asyncio
    async def test_lifespan_creates_event_loop_and_task(self):
        mock_loop = MagicMock()
        mock_loop.run_forever = AsyncMock()
        with patch("general_ludd.daemon.EventLoop", return_value=mock_loop):
            from fastapi import FastAPI

            from general_ludd.daemon import _lifespan
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
        with patch("general_ludd.daemon.EventLoop", return_value=mock_loop):
            from fastapi import FastAPI

            from general_ludd.daemon import _lifespan
            app = FastAPI()
            app.state.tick_interval = 0.01
            app.state.event_loop = None
            async with _lifespan(app):
                pass
            mock_loop.stop.assert_called()

    @pytest.mark.asyncio
    async def test_lifespan_handles_event_loop_failure(self):
        with patch("general_ludd.daemon.EventLoop", side_effect=RuntimeError("boom")):
            from fastapi import FastAPI

            from general_ludd.daemon import _lifespan
            app = FastAPI()
            app.state.tick_interval = 0.01
            app.state.event_loop = None
            async with _lifespan(app):
                assert app.state.event_loop is None


class TestExtendedSubsystemsWiring:
    def test_extended_subsystems_includes_skill_registry(self):
        from fastapi import FastAPI

        from general_ludd.daemon import _get_or_create_extended_subsystems
        app = FastAPI()
        ext = _get_or_create_extended_subsystems(app)
        assert "skill_registry" in ext
        assert ext["skill_registry"] is not None

    def test_skill_registry_is_reused_on_second_call(self):
        from fastapi import FastAPI

        from general_ludd.daemon import _get_or_create_extended_subsystems
        app = FastAPI()
        ext1 = _get_or_create_extended_subsystems(app)
        ext2 = _get_or_create_extended_subsystems(app)
        assert ext1["skill_registry"] is ext2["skill_registry"]


class TestDirectDispatch:
    @pytest.mark.asyncio
    async def test_event_loop_with_runner_dispatches_directly(self):
        from general_ludd.event_loop.loop import EventLoop
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
        from general_ludd.event_loop.loop import EventLoop
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
        from general_ludd.event_loop.loop import EventLoop
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
        from general_ludd.event_loop.loop import EventLoop
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


class TestBuildExecutionEngine:
    """Tests for _build_execution_engine: P4-activation wiring.

    The helper must ALWAYS succeed (no-op when P4 params are absent) and
    MUST pass spend_limiter + session_factory when the ExecutionEngine
    signature accepts them.

    Merge-ordering note: the xfail test below proves that once the P4 branch
    (feature/p4-spend-limiter-record-enforce) lands and ExecutionEngine gains
    ``spend_limiter`` + ``session_factory`` params, those are wired through
    automatically — no further daemon change required.  KEEP the xfail; flip
    it to a regular pass after confirming P4 is merged and the params exist.
    """

    def test_build_execution_engine_returns_engine_instance(self):
        """_build_execution_engine always returns an ExecutionEngine (no-op guard)."""
        from fastapi import FastAPI

        from general_ludd.daemon import _build_execution_engine
        from general_ludd.execution.engine import ExecutionEngine

        app = FastAPI()
        # Pre-set state as the lifespan does.
        app.state._spend_limiter = None
        app.state._session_factory = None

        engine = _build_execution_engine(app)
        assert isinstance(engine, ExecutionEngine)

    def test_build_execution_engine_with_spend_limiter_on_state(self):
        """spend_limiter on app.state is forwarded when the param exists."""
        import inspect

        from fastapi import FastAPI

        from general_ludd.daemon import _build_execution_engine
        from general_ludd.execution.engine import ExecutionEngine

        # If the current ExecutionEngine does NOT accept spend_limiter, this
        # test is vacuously correct — the helper just omits it, so the engine
        # constructs fine.  The important case is post-P4 (see xfail below).
        app = FastAPI()
        mock_limiter = MagicMock()
        app.state._spend_limiter = mock_limiter
        app.state._session_factory = None

        engine = _build_execution_engine(app)
        assert isinstance(engine, ExecutionEngine)

        sig = inspect.signature(ExecutionEngine.__init__)
        if "spend_limiter" in sig.parameters:
            # Post-P4: the engine must have received our limiter.
            assert engine._spend_limiter is mock_limiter  # type: ignore[attr-defined]

    @pytest.mark.xfail(
        reason=(
            "ExecutionEngine on this branch does NOT yet have spend_limiter / "
            "session_factory params — they live on feature/p4-spend-limiter-record-enforce. "
            "Merge that branch first, then this xfail becomes a pass."
        ),
        strict=True,
    )
    def test_build_execution_engine_wires_spend_limiter_and_session_factory(self):
        """Post-P4: engine is constructed WITH spend_limiter + session_factory.

        This test is the binding contract: once P4 merges and ExecutionEngine
        grows those params, the daemon helper must pass them — cost
        recording/enforcement becomes live without any further daemon change.

        xfail(strict=True): expected to FAIL on the pre-P4 branch; will XPASS
        (and turn green) after P4 is merged.  Do NOT remove the xfail
        decorator until you have confirmed both params are in the signature.
        """
        import inspect

        from fastapi import FastAPI

        from general_ludd.daemon import _build_execution_engine
        from general_ludd.execution.engine import ExecutionEngine

        app = FastAPI()
        mock_limiter = MagicMock()
        mock_factory = MagicMock()
        app.state._spend_limiter = mock_limiter
        app.state._session_factory = mock_factory

        engine = _build_execution_engine(app)

        sig = inspect.signature(ExecutionEngine.__init__)
        # This assertion drives the xfail: it fails when the params are absent.
        assert "spend_limiter" in sig.parameters, (
            "ExecutionEngine.__init__ does not have 'spend_limiter' — "
            "merge feature/p4-spend-limiter-record-enforce first"
        )
        assert "session_factory" in sig.parameters, (
            "ExecutionEngine.__init__ does not have 'session_factory' — "
            "merge feature/p4-spend-limiter-record-enforce first"
        )
        # Confirm the values were actually forwarded.
        assert engine._spend_limiter is mock_limiter  # type: ignore[attr-defined]
        assert engine._session_factory is mock_factory  # type: ignore[attr-defined]
