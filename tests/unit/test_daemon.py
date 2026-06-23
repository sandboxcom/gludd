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
            assert "config_file_count" in data
            assert isinstance(data["config_file_count"], int)
            assert "filestore_available" in data
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


def _lifespan_patches(mock_loop):
    """Return a contextlib.ExitStack with all patches needed for _lifespan tests."""
    import contextlib as _contextlib
    from unittest.mock import AsyncMock as _AsyncMock
    from unittest.mock import MagicMock as _MagicMock
    from unittest.mock import patch as _patch
    stack = _contextlib.ExitStack()
    cm = _MagicMock()
    _mock_session = _MagicMock()
    _mock_session.commit = _AsyncMock()
    cm.__aenter__ = _AsyncMock(return_value=_mock_session)
    cm.__aexit__ = _AsyncMock(return_value=False)
    session_factory = _MagicMock(return_value=cm)
    stack.enter_context(_patch("general_ludd.daemon.EventLoop", return_value=mock_loop))
    _mock_engine = _MagicMock()
    _mock_engine.dispose = _AsyncMock()
    stack.enter_context(_patch("general_ludd.daemon.init_engine_from_config", return_value=_mock_engine))
    stack.enter_context(_patch("general_ludd.daemon.ensure_tables", new_callable=_AsyncMock))
    stack.enter_context(_patch("general_ludd.daemon.create_async_session_factory", return_value=session_factory))
    stack.enter_context(_patch("general_ludd.daemon.seed_initial_queues", new_callable=_AsyncMock))
    stack.enter_context(_patch("general_ludd.daemon.is_sqlite_url", return_value=False))
    stack.enter_context(_patch("general_ludd.daemon.AnsibleRunnerAdapter", return_value=_MagicMock()))
    stack.enter_context(_patch("general_ludd.daemon._get_or_create_subsystems", return_value={"bus": _MagicMock()}))
    stack.enter_context(_patch(
        "general_ludd.daemon._get_or_create_extended_subsystems",
        return_value={
            "projects": _MagicMock(),
            "skill_registry": _MagicMock(),
            "adaptive_router": _MagicMock(),
            "metrics_collector": _MagicMock(),
        },
    ))
    stack.enter_context(_patch("general_ludd.daemon._restore_persisted_projects", new_callable=_AsyncMock))
    stack.enter_context(_patch("general_ludd.daemon.build_secrets_resolver", return_value=_MagicMock()))
    stack.enter_context(_patch("general_ludd.daemon.PromptRegistry", return_value=_MagicMock()))
    stack.enter_context(_patch("general_ludd.daemon.build_event_loop_mcp_dispatcher", return_value=None))
    stack.enter_context(_patch("general_ludd.daemon._init_project_workspaces", return_value={}))
    stack.enter_context(_patch("general_ludd.models.timeout_detector.ModelHealthTracker", return_value=_MagicMock()))
    stack.enter_context(_patch("general_ludd.daemon.ModelHealthTracker", return_value=_MagicMock()))
    # The lifespan shutdown path does `await task`, plus task.cancel() and
    # task.add_done_callback(). A completed asyncio.Future supports all three
    # and is directly awaitable; a bare AsyncMock instance is NOT awaitable
    # (only calling it returns a coroutine), so use a resolved Future here.
    import asyncio as _asyncio
    _done_task = _asyncio.get_running_loop().create_future()
    _done_task.set_result(None)
    stack.enter_context(_patch("asyncio.create_task", return_value=_done_task))
    return stack


class TestDaemonLifespan:
    @pytest.mark.asyncio
    async def test_lifespan_creates_event_loop_and_task(self):
        mock_loop = MagicMock()
        mock_loop.run_forever = AsyncMock()
        with _lifespan_patches(mock_loop):
            from fastapi import FastAPI

            from general_ludd.daemon import _lifespan
            app = FastAPI()
            app.state.tick_interval = 0.01
            app.state.event_loop = None
            app.state._receiver_buffer = MagicMock()
            async with _lifespan(app):
                assert app.state.event_loop is mock_loop
            mock_loop.stop.assert_called()

    @pytest.mark.asyncio
    async def test_lifespan_stops_event_loop_on_shutdown(self):
        mock_loop = MagicMock()
        mock_loop.run_forever = AsyncMock()
        with _lifespan_patches(mock_loop):
            from fastapi import FastAPI

            from general_ludd.daemon import _lifespan
            app = FastAPI()
            app.state.tick_interval = 0.01
            app.state.event_loop = None
            app.state._receiver_buffer = MagicMock()
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

    def test_adaptive_router_receives_live_health_tracker_ca_t7(self):
        """CA-T7/CA-T8: adaptive_router must receive a non-None health_tracker.

        Previously _get_or_create_extended_subsystems was called BEFORE
        app.state._health_tracker was set, so the router got None and the
        health-filtering + quantization-penalty logic was permanently inert.
        The fix pre-assigns the tracker to app.state before the call.
        """
        from unittest.mock import MagicMock

        from fastapi import FastAPI

        from general_ludd.daemon import _get_or_create_extended_subsystems
        from general_ludd.models.timeout_detector import ModelHealthTracker

        app = FastAPI()
        # Simulate the fixed startup order: assign health_tracker BEFORE the call
        health_tracker = ModelHealthTracker()
        app.state._health_tracker = health_tracker

        # Provide a minimal session_factory so the router branch is entered
        mock_sf = MagicMock()
        ext = _get_or_create_extended_subsystems(app, session_factory=mock_sf)

        router = ext.get("adaptive_router")
        assert router is not None, "AdaptiveRouter should be built when session_factory provided"
        assert router._health_tracker is not None, (
            "CA-T7: router._health_tracker must be non-None after startup wiring "
            "(was None before the fix because tracker was assigned after router was built)"
        )
        assert router._health_tracker is health_tracker, (
            "CA-T7: router must hold the SAME ModelHealthTracker instance set on app.state"
        )

    def test_adaptive_router_health_tracker_is_none_without_pre_assignment(self):
        """Regression guard: without the fix (no pre-assignment), health_tracker is None.

        This test documents the old broken behaviour to prove the fix is necessary.
        """
        from unittest.mock import MagicMock

        from fastapi import FastAPI

        from general_ludd.daemon import _get_or_create_extended_subsystems

        app = FastAPI()
        # Do NOT set app.state._health_tracker — simulates old broken order
        mock_sf = MagicMock()
        ext = _get_or_create_extended_subsystems(app, session_factory=mock_sf)

        router = ext.get("adaptive_router")
        assert router is not None
        # Without pre-assignment the router gets None — this is the pre-fix bug
        assert router._health_tracker is None, (
            "Without pre-assignment of _health_tracker, router gets None (old bug documented)"
        )

    def test_adaptive_router_receives_live_quantization_tracker_ca_t9(self):
        """CA-T9: adaptive_router must receive a non-empty quantization_map source.

        Previously _get_or_create_extended_subsystems checked
        getattr(app.state, "_quantization_tracker", None) but nothing ever
        assigned app.state._quantization_tracker before the router was built, so
        quantization_map stayed {} and _apply_quantization_penalty was permanently
        inert in the running daemon.  The fix pre-assigns a QuantizationTracker
        to app.state BEFORE _get_or_create_extended_subsystems is called.
        """
        from unittest.mock import MagicMock

        from fastapi import FastAPI

        from general_ludd.daemon import _get_or_create_extended_subsystems
        from general_ludd.models.quantization import QuantizationInfo, QuantizationTracker

        app = FastAPI()
        # Simulate the fixed startup order: assign _quantization_tracker BEFORE the call
        tracker = QuantizationTracker()
        # Pre-populate with one entry so quantization_map is non-empty in the router
        tracker.update("test-model", QuantizationInfo(precision="int4", source="test", confidence=0.9))
        app.state._quantization_tracker = tracker

        mock_sf = MagicMock()
        ext = _get_or_create_extended_subsystems(app, session_factory=mock_sf)

        router = ext.get("adaptive_router")
        assert router is not None, "AdaptiveRouter should be built when session_factory provided"
        assert router._quantization_map is not None, (
            "CA-T9: router._quantization_map must not be None"
        )
        assert "test-model" in router._quantization_map, (
            "CA-T9: router._quantization_map must reflect the pre-assigned tracker's data"
        )
        prec, conf = router._quantization_map["test-model"]
        assert prec == "int4"
        assert conf == pytest.approx(0.9)

    def test_adaptive_router_quantization_map_empty_without_pre_assignment(self):
        """Regression guard for CA-T9: without pre-assignment, quantization_map stays {}.

        This documents the old broken behaviour: nothing ever created
        app.state._quantization_tracker before the router was built, so
        quantization_map was always {} and penalty logic never fired.
        """
        from unittest.mock import MagicMock

        from fastapi import FastAPI

        from general_ludd.daemon import _get_or_create_extended_subsystems

        app = FastAPI()
        # Do NOT set app.state._quantization_tracker — simulates old broken order
        mock_sf = MagicMock()
        ext = _get_or_create_extended_subsystems(app, session_factory=mock_sf)

        router = ext.get("adaptive_router")
        assert router is not None
        # Without pre-assignment quantization_map is {} — old bug documented
        assert router._quantization_map == {}, (
            "Without pre-assignment of _quantization_tracker, quantization_map is {} (old bug documented)"
        )

    @pytest.mark.asyncio
    async def test_lifespan_wires_budget_guard_into_model_gateway(self):
        """budget_guard must be passed to ModelGateway so spend ceilings are enforced.

        Commit 6c019e0 fixed a silent regression where budget_guard was built from
        the operator's UserConfig but never forwarded to the ModelGateway constructor
        (daemon.py ~750: ``budget_guard=budget_guard``).  Without that kwarg the
        gateway's ``_budget_guard`` is always None and configured spend limits are
        completely inert in the running daemon.

        This test drives _lifespan with a minimal startup_config that has:
          - a UserConfig-like object with a non-zero budget so budget_guard is built
          - one ModelProfile so the ModelGateway branch is entered
        and asserts that the resulting app.state._model_gateway._budget_guard is
        the live RunBudgetGuard instance (not None).
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from fastapi import FastAPI

        from general_ludd.controllers.budget import RunBudgetGuard
        from general_ludd.daemon import _lifespan
        from general_ludd.models.gateway import ModelProfile

        # Build a minimal UserConfig-like object with a non-zero daily_limit so
        # _parse_budget_config / the budget_guard construction branch fires.
        mock_uc = MagicMock()
        mock_uc.budget = {"daily_limit": 10.0, "per_task_limit": 1.0, "timeout_seconds": 3600.0}
        mock_uc.database = {}
        mock_uc.self_improve = {}

        profile = ModelProfile(
            model_profile_id="test-prof",
            provider="openai",
            model_name="gpt-4o-mini",
            api_key_env="OPENAI_API_KEY",
        )

        app = FastAPI()
        app.state.tick_interval = 0.01
        app.state.event_loop = None
        app.state._startup_config = {
            "user_config": mock_uc,
            "model_routing": MagicMock(default_profile=None),
            "model_profiles": [profile],
            "binary_paths": None,
            "openbao_config": None,
            "process_isolation": None,
            "mcp_servers": {},
        }

        mock_event_loop = MagicMock()
        mock_event_loop.run_forever = AsyncMock()

        # Stub out the async DB machinery so we don't need a real DB engine.
        # engine.dispose() is awaited during lifespan teardown → AsyncMock.
        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_factory = MagicMock(return_value=mock_session)

        with (
            patch("general_ludd.daemon.EventLoop", return_value=mock_event_loop),
            patch("general_ludd.daemon.init_engine_from_config", return_value=mock_engine),
            patch("general_ludd.daemon.ensure_tables", new_callable=AsyncMock),
            patch("general_ludd.daemon.create_async_session_factory", return_value=mock_session_factory),
            patch("general_ludd.daemon.seed_initial_queues", new_callable=AsyncMock),
            patch("general_ludd.daemon.is_sqlite_url", return_value=False),
            patch("general_ludd.daemon._restore_persisted_projects", new_callable=AsyncMock),
        ):
            async with _lifespan(app):
                gateway = getattr(app.state, "_model_gateway", None)
                assert gateway is not None, (
                    "ModelGateway must be built during lifespan when model_profiles are configured"
                )
                assert gateway._budget_guard is not None, (
                    "budget_guard must be wired into ModelGateway._budget_guard "
                    "(was silently None before commit 6c019e0 — configured spend ceilings were inert)"
                )
                assert isinstance(gateway._budget_guard, RunBudgetGuard), (
                    "ModelGateway._budget_guard must be a live RunBudgetGuard instance"
                )


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
