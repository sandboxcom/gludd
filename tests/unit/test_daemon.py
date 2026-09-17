"""Unit tests for unified daemon app."""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import general_ludd.daemon as daemon_mod
from general_ludd.daemon import create_daemon_app


@pytest.fixture(autouse=True)
def _reset_daemon_state():
    if daemon_mod._daemon_state is None:
        daemon_mod._daemon_state = {}
    daemon_mod._daemon_state["todos"] = []
    daemon_mod._daemon_state["tick_metrics"] = {}
    daemon_mod._daemon_state["quality_gate"] = {}
    daemon_mod._daemon_state.pop("receiver_buffer", None)
    daemon_mod._daemon_state.pop("human_gate", None)
    daemon_mod._daemon_state.pop("remediation_config", None)
    daemon_mod._daemon_state.pop("_degraded", None)
    daemon_mod._daemon_state.pop("credits", None)
    daemon_mod._daemon_state.pop("self_improve_last_analysis", None)
    daemon_mod._daemon_state.pop("self_improve_error_patterns", None)
    daemon_mod._daemon_state.pop("_last_gpu_metrics", None)
    daemon_mod._daemon_state.pop("_last_gpu_metrics_at", None)


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
        root = logging.getLogger()
        original_level = root.level
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/admin/log-level", json={"level": "debug"})
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "ok"
                assert data["level"] == "debug"
                assert root.level == logging.DEBUG
        finally:
            # Restore the PRE-TEST root level.  /admin/log-level mutates the
            # process-global root logger; the old hardcoded setLevel(WARNING)
            # left root at WARNING, suppressing INFO/DEBUG records for later
            # caplog tests on the same xdist worker (systemic caplog-empty
            # pollution — the prime polluter behind the flaky caplog failures).
            root.setLevel(original_level)

    @pytest.mark.asyncio
    async def test_log_level_rejects_invalid_level(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/log-level", json={"level": "verbose"})
            assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_add_todo_endpoint(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/todos",
                json={
                    "title": "Fix the login bug",
                    "queue": "core",
                    "priority": "high",
                    "work_type": "code",
                },
            )
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
            assert "db_engine" not in data
            assert "db_url" not in data

    @pytest.mark.asyncio
    async def test_deployments_endpoint(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/deployments")
            assert resp.status_code == 200
            body = resp.json()
            assert isinstance(body, dict)
            assert "deployments" in body
            assert isinstance(body["deployments"], list)


class TestDaemonStartupConfig:
    def test_create_app_with_config_dir_loads_config(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "model_routing.yml").write_text("default_profile: test_prof\nrole_routing:\n  coder: test_prof\n")
        app = create_daemon_app(config_dir=str(config_dir))
        assert app.state._config_dir == str(config_dir)

    def test_create_app_without_config_dir_still_works(self):
        app = create_daemon_app()
        assert app.state._config_dir is None

    def test_load_startup_config_loads_model_routing(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "model_routing.yml").write_text("default_profile: my_profile\n")
        cfg = load_startup_config(config_dir=str(config_dir))
        assert cfg["model_routing"].default_profile == "my_profile"

    def test_load_startup_config_loads_user_config(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "general-ludd.yml").write_text("database:\n  url: postgresql://localhost/gludd\n")
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
        (config_dir / "binary_paths.yml").write_text("binary_paths:\n  terraform: /usr/local/bin/terraform\n")
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
        digest = "a" * 64
        (ansible_dir / "isolation.yml").write_text(
            "process_isolation:\n"
            "  enabled: true\n"
            "  executable: docker\n"
            f"  container_image: registry.example/gludd-ee:beta4@sha256:{digest}\n"
        )
        cfg = load_startup_config(config_dir=str(config_dir))
        isolation = cfg["process_isolation"]
        assert isolation is not None
        assert isolation.enabled is True
        assert isolation.container_image.endswith(digest)


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
    stack.enter_context(
        _patch(
            "general_ludd.daemon._get_or_create_extended_subsystems",
            return_value={
                "projects": _MagicMock(),
                "skill_registry": _MagicMock(),
                "adaptive_router": _MagicMock(),
                "metrics_collector": _MagicMock(),
            },
        )
    )
    stack.enter_context(_patch("general_ludd.daemon._restore_persisted_projects", new_callable=_AsyncMock))
    stack.enter_context(_patch("general_ludd.daemon.build_secrets_resolver", return_value=_MagicMock()))
    stack.enter_context(_patch("general_ludd.daemon.PromptRegistry", return_value=_MagicMock()))
    stack.enter_context(_patch("general_ludd.daemon.build_event_loop_mcp_dispatcher", return_value=None))
    stack.enter_context(_patch("general_ludd.daemon._init_project_workspaces", return_value={}))
    stack.enter_context(_patch("general_ludd.models.timeout_detector.ModelHealthTracker", return_value=_MagicMock()))
    stack.enter_context(_patch("general_ludd.daemon.ModelHealthTracker", return_value=_MagicMock()))
    # These lifecycle unit tests deliberately replace app.state owners while
    # the lifespan is running.  Keep their setup hermetic: constructing the
    # real diskcache-backed owners here would open SQLite handles that become
    # unreachable when a test installs its shutdown doubles.
    for cache_factory in (
        "general_ludd.retrieval.indexer.open_safe_diskcache",
        "general_ludd.retrieval.research_index.open_safe_diskcache",
        "general_ludd.retrieval.searx_client.open_safe_diskcache",
        "general_ludd.retrieval.searcher.open_safe_diskcache",
        "general_ludd.memory.local.open_safe_diskcache",
    ):
        stack.enter_context(_patch(cache_factory, return_value=_MagicMock()))
    # The lifespan shutdown path does `await task`, plus task.cancel() and
    # task.add_done_callback(). A completed asyncio.Future supports all three
    # and is directly awaitable; a bare AsyncMock instance is NOT awaitable
    # (only calling it returns a coroutine), so use a resolved Future here.
    import asyncio as _asyncio

    def _completed_task(coro):
        coro.close()
        done_task = _asyncio.get_running_loop().create_future()
        done_task.set_result(None)
        return done_task

    stack.enter_context(_patch("asyncio.create_task", side_effect=_completed_task))
    return stack


class TestDaemonLifespan:
    @pytest.mark.asyncio
    async def test_lifespan_does_not_autostart_searx_by_default(self):
        from general_ludd.config.user_config import UserConfig
        from general_ludd.daemon import _lifespan

        mock_loop = MagicMock()
        mock_loop.run_forever = AsyncMock()
        app = create_daemon_app(tick_interval=0.01)
        app.state._startup_config["user_config"] = UserConfig()
        with (
            _lifespan_patches(mock_loop),
            patch("general_ludd.searx.install.ensure_searx_installed") as install,
            patch("general_ludd.searx.install.ensure_searx_initialized") as initialize,
            patch("general_ludd.searx.server.SearXServer") as server,
        ):
            async with _lifespan(app):
                pass

        install.assert_not_called()
        initialize.assert_not_called()
        server.assert_not_called()

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

    def test_adaptive_router_built_when_preset_to_none_on_app_state(self):
        """H-ADAPTIVE-ROUTER-NULL: create_daemon_app() pre-seeds
        app.state._adaptive_router = None BEFORE the lifespan runs. The guard
        used to be `not hasattr(app.state, "_adaptive_router")`, which is False
        once the attribute exists as None — so the real router was never built
        and ext["adaptive_router"] stayed None for the process life (flowing into
        EventLoop(adaptive_router=None) + ReturnReviewer(router=None), silently
        disabling adaptive model/prompt routing). The guard now also fires when
        the pre-seeded value is None.
        """
        from unittest.mock import MagicMock

        from fastapi import FastAPI

        from general_ludd.daemon import _get_or_create_extended_subsystems

        app = FastAPI()
        app.state._adaptive_router = None  # mirrors create_daemon_app()'s pre-seed
        mock_sf = MagicMock()
        ext = _get_or_create_extended_subsystems(app, session_factory=mock_sf)

        router = ext.get("adaptive_router")
        assert router is not None, (
            "adaptive_router must be built even when app.state._adaptive_router "
            "was pre-seeded to None (the real daemon-boot precondition) — the "
            "hasattr-only guard left it permanently None"
        )
        assert app.state._adaptive_router is router

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
        assert router._quantization_map is not None, "CA-T9: router._quantization_map must not be None"
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

        from general_ludd.config.user_config import NetworkConfig
        from general_ludd.controllers.budget import RunBudgetGuard
        from general_ludd.daemon import _lifespan
        from general_ludd.models.gateway import ModelProfile

        # Build a minimal UserConfig-like object with a non-zero daily_limit so
        # _parse_budget_config / the budget_guard construction branch fires.
        mock_uc = MagicMock()
        mock_uc.budget = {
            "daily_limit": 10.0,
            "per_task_limit": 1.0,
            "timeout_seconds": 3600.0,
            "spend_window_usd": 2.0,
            "spend_window_seconds": 3_600.0,
        }
        mock_uc.database = {}
        mock_uc.self_improve = {}
        # A real loopback network block prevents ambient settings or MagicMock's
        # truthiness from turning this isolated unit app into an external bind.
        mock_uc.network = NetworkConfig(host="127.0.0.1")

        profile = ModelProfile(
            model_profile_id="test-prof",
            provider="openai",
            model_name="gpt-4o-mini",
            credential_alias="OPENAI_API_KEY",
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

        def _completed_task(coro):
            coro.close()
            done_task = asyncio.get_running_loop().create_future()
            done_task.set_result(None)
            return done_task

        # Stub out the async DB machinery so we don't need a real DB engine.
        # engine.dispose() is awaited during lifespan teardown → AsyncMock.
        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()
        mock_session = MagicMock()
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.close = AsyncMock()
        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)
        mock_session_factory = MagicMock(return_value=mock_session_cm)

        with (
            patch("general_ludd.daemon.EventLoop", return_value=mock_event_loop),
            patch("general_ludd.daemon.init_engine_from_config", return_value=mock_engine),
            patch("general_ludd.daemon.ensure_tables", new_callable=AsyncMock),
            patch("general_ludd.daemon.create_async_session_factory", return_value=mock_session_factory),
            patch("general_ludd.daemon.seed_initial_queues", new_callable=AsyncMock),
            patch("general_ludd.daemon.is_sqlite_url", return_value=False),
            patch("general_ludd.daemon._restore_persisted_projects", new_callable=AsyncMock),
            patch("asyncio.create_task", side_effect=_completed_task),
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
                assert gateway._health_tracker is app.state._health_tracker, (
                    "ModelGateway must share the daemon's pre-created health tracker"
                )
                spend_limiter = app.state._spend_limiter
                execution_engine = app.state._execution_engine
                assert spend_limiter is not None
                assert execution_engine._spend_limiter is spend_limiter, (
                    "ExecutionEngine must share the daemon's rehydrated SpendLimiter; "
                    "a separate or absent limiter bypasses the live rolling cap"
                )

    @pytest.mark.asyncio
    async def test_daemon_shutdown_drains_execution_engine_background_tasks(self):
        """Lifespan teardown must call execution_engine.shutdown() to drain background tasks."""
        from unittest.mock import AsyncMock

        from general_ludd.models.gateway import ModelProfile

        mock_loop = MagicMock()
        mock_loop.run_forever = AsyncMock()

        mock_engine = MagicMock()
        mock_engine.shutdown = AsyncMock()
        mock_engine._verify_sandbox = MagicMock(return_value=None)
        mock_engine._projected_cost = MagicMock(return_value=0.0)
        mock_engine._budget_pre_check = MagicMock(return_value=None)
        mock_engine._record_metrics = MagicMock()
        mock_engine.defer_commit = MagicMock()
        mock_engine.execute_async = AsyncMock()

        mock_gateway = MagicMock()
        mock_gateway._profiles = MagicMock()
        mock_gateway._profiles.values.return_value = []

        with (
            _lifespan_patches(mock_loop),
            patch("general_ludd.daemon.ExecutionEngine", return_value=mock_engine),
            patch("general_ludd.daemon.ModelGateway", return_value=mock_gateway),
            patch("general_ludd.daemon.SemanticSearcher", return_value=MagicMock()),
            patch("general_ludd.daemon.ModelEvaluator", return_value=MagicMock()),
            patch("general_ludd.daemon.EvalHarness", return_value=MagicMock()),
            patch("general_ludd.daemon.DeploymentHealthChecker", return_value=MagicMock()),
            patch("general_ludd.daemon.SelfHealingRouter", return_value=MagicMock()),
            patch("general_ludd.daemon.ProviderRegistry"),
            patch("general_ludd.daemon.migrate_profile_secrets", return_value={"migrated": 0, "skipped": []}),
        ):
            from fastapi import FastAPI

            from general_ludd.daemon import _lifespan

            app = FastAPI()
            app.state.tick_interval = 0.01
            app.state.event_loop = None
            app.state._receiver_buffer = MagicMock()
            app.state._startup_config = {
                "model_profiles": [
                    ModelProfile(
                        model_profile_id="test-prof",
                        provider="openai",
                        model_name="gpt-4o-mini",
                        credential_alias="OPENAI_API_KEY",
                    )
                ],
            }
            app.state._health_tracker = MagicMock()

            async with _lifespan(app):
                pass

            assert mock_engine.shutdown.await_count == 1, (
                "execution_engine.shutdown() must be called exactly once during teardown"
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


class TestDaemonStateIsolation:
    """Regression: the module-level ``_daemon_state`` dict was shared across every
    FastAPI instance, so state (todos, tick_metrics, quality_gate) bled between
    tests. Each ``create_daemon_app()`` must own a fresh per-app dict.
    """

    def test_each_app_gets_its_own_daemon_state_dict(self):
        app_a = create_daemon_app(tick_interval=999.0)
        app_b = create_daemon_app(tick_interval=999.0)
        state_a = app_a.state.daemon_state
        state_b = app_b.state.daemon_state
        assert state_a is not state_b
        # Mutating one app's state must not appear in the other's.
        state_a["todos"].append({"todo_id": "ONLY-A"})
        assert state_b["todos"] == []

    def test_new_app_daemon_state_starts_empty(self):
        # A previously created app must not contaminate a freshly built one.
        first = create_daemon_app(tick_interval=999.0)
        first.state.daemon_state["todos"].append({"todo_id": "LEFTOVER"})
        first.state.daemon_state["tick_metrics"]["leaked"] = True

        second = create_daemon_app(tick_interval=999.0)
        assert second.state.daemon_state["todos"] == []
        assert second.state.daemon_state["tick_metrics"] == {}
        assert second.state.daemon_state["quality_gate"] == {}


class TestApiStatusNoDbLeak:
    """SEC-8 regression: /api/status must not expose db_url or db_engine to
    unauthenticated callers.  The endpoint is public and formerly disclosed the
    DB host/port/dialect in plaintext."""

    @pytest.fixture
    def transport(self):
        app = create_daemon_app(tick_interval=999.0)
        return ASGITransport(app=app)

    @pytest.mark.asyncio
    async def test_api_status_does_not_leak_db_url(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "db_url" not in data, "SEC-8: db_url must not be exposed in /api/status response"
        assert "db_engine" not in data, "SEC-8: db_engine must not be exposed in /api/status response"

    @pytest.mark.asyncio
    async def test_api_status_retains_required_fields(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data, "/api/status must still return version"
        assert "todos_total" in data, "/api/status must still return todos_total"


class TestDegradedGuard:
    """Bug #1: mutating handlers must return 503 when app.state._degraded is set.

    When the daemon lifespan fails the process keeps serving but enforcement
    infrastructure (dispatcher, self-update ladder, spend limiter) is silently
    inert.  Calling _check_degraded() at the top of each mutating handler
    prevents silent pass-through of requests that can have no real effect.
    """

    def test_check_degraded_returns_none_when_healthy(self):
        from fastapi import FastAPI

        from general_ludd.daemon import _check_degraded

        app = FastAPI()
        # No _degraded set — healthy case
        assert _check_degraded(app) is None

    def test_check_degraded_returns_json_response_when_set(self):
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse

        from general_ludd.daemon import _check_degraded

        app = FastAPI()
        app.state._degraded = "startup failed: DB unreachable"
        resp = _check_degraded(app)
        assert resp is not None
        assert isinstance(resp, JSONResponse)
        assert resp.status_code == 503

    def test_check_degraded_truncates_long_reason(self):
        from fastapi import FastAPI

        from general_ludd.daemon import _check_degraded

        app = FastAPI()
        app.state._degraded = "x" * 500
        resp = _check_degraded(app)
        import json

        body = json.loads(resp.body)
        assert len(body["reason"]) <= 200

    @pytest.mark.asyncio
    async def test_dispatch_returns_503_when_degraded(self):
        """POST /api/dispatch must return 503 when _degraded is set."""
        import os

        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        app.state._degraded = "lifespan boom"
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/dispatch",
                json={"kind": "role", "name": "planner", "args": {}},
            )
        assert resp.status_code == 503
        body = resp.json()
        assert body.get("error") == "degraded"

    @pytest.mark.asyncio
    async def test_self_update_plan_returns_503_when_degraded(self):
        """POST /admin/self-update/plan must return 503 when _degraded is set."""
        import os

        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        app.state._degraded = "lifespan boom"
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/self-update/plan",
                json={"raw_text": "add logging", "requested_by": "test"},
            )
        assert resp.status_code == 503
        body = resp.json()
        assert body.get("error") == "degraded"

    @pytest.mark.asyncio
    async def test_self_update_enqueue_returns_503_when_degraded(self):
        """POST /admin/self-update/enqueue must return 503 when _degraded is set."""
        import os

        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        app.state._degraded = "lifespan boom"
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/self-update/enqueue",
                json={"raw_text": "add logging", "requested_by": "test"},
            )
        assert resp.status_code == 503
        body = resp.json()
        assert body.get("error") == "degraded"

    @pytest.mark.asyncio
    async def test_spend_configure_returns_503_when_degraded(self):
        """POST /api/spend/configure must return 503 when _degraded is set."""
        import os

        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        app.state._degraded = "lifespan boom"
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/spend/configure",
                json={"limit_usd": 5.0, "window_seconds": 3600.0},
            )
        assert resp.status_code == 503
        body = resp.json()
        assert body.get("error") == "degraded"

    @pytest.mark.asyncio
    async def test_healthy_dispatch_not_blocked(self):
        """Sanity: when _degraded is absent, dispatch proceeds to real handling."""
        import os

        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        # No _degraded — the dispatcher should handle the request (likely error,
        # but NOT a 503 degraded response).
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/dispatch",
                json={"kind": "role", "name": "planner", "args": {}},
            )
        # Should not be blocked as degraded; any non-503-degraded response is fine.
        body = resp.json()
        assert body.get("error") != "degraded", "Healthy daemon must not return degraded error from /api/dispatch"


class TestIngestAuthGuard:
    """Bug #2: structural guard — every /v1/* and /ingest/* route must enforce
    ingest-token auth and must NOT serve unauthenticated requests.

    The PSK middleware explicitly skips these prefixes (_RECEIVER_PREFIXES) so
    the receiver router's own auth runs instead.  This test locks down that
    invariant: if a future route is added under /v1/ or /ingest/ and forgets its
    own auth, the test catches it before it ships as a wide-open relay.

    Covered routes (POST):
      /v1/logs, /v1/metrics, /v1/traces
      /ingest/webhook, /ingest/gelf, /ingest/fluent, /ingest/beats
    """

    _INGEST_ROUTES: ClassVar[list[str]] = [
        "/v1/logs",
        "/v1/metrics",
        "/v1/traces",
        "/ingest/webhook",
        "/ingest/gelf",
        "/ingest/fluent",
        "/ingest/beats",
    ]

    @pytest.fixture
    def _app_no_ingest_token(self, monkeypatch):
        """Daemon app with no GLUDD_INGEST_TOKEN configured (fail-closed mode)."""
        monkeypatch.delenv("GLUDD_INGEST_TOKEN", raising=False)
        monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")
        return create_daemon_app(tick_interval=999.0)

    @pytest.fixture
    def _app_with_ingest_token(self, monkeypatch):
        """Daemon app with a known GLUDD_INGEST_TOKEN configured."""
        monkeypatch.setenv("GLUDD_INGEST_TOKEN", "test-ingest-secret")
        monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")
        return create_daemon_app(tick_interval=999.0)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", _INGEST_ROUTES)
    async def test_ingest_route_fail_closed_without_token(self, path, _app_no_ingest_token, monkeypatch):
        """Without GLUDD_INGEST_TOKEN the receiver must fail-closed (503), never
        expose an open relay.  A 200/404/422 here would mean auth was skipped."""
        # Ensure the env var is absent for the request evaluation too
        monkeypatch.delenv("GLUDD_INGEST_TOKEN", raising=False)
        transport = ASGITransport(app=_app_no_ingest_token)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                path,
                content=b'{"msg": "hello"}',
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 503, (
            f"GUARD: {path} must return 503 (ingest_disabled) when "
            f"GLUDD_INGEST_TOKEN is not configured — got {resp.status_code}. "
            "A new route under /v1/ or /ingest/ that skips ingest auth will "
            "show up here as a non-503 response."
        )
        body = resp.json()
        assert body.get("error") == "ingest_disabled", (
            f"GUARD: {path} fail-closed response must carry error=ingest_disabled, got: {body}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", _INGEST_ROUTES)
    async def test_ingest_route_rejects_bad_token(self, path, _app_with_ingest_token, monkeypatch):
        """A wrong ingest token must return 401 Unauthorized, never 200/404."""
        monkeypatch.setenv("GLUDD_INGEST_TOKEN", "test-ingest-secret")
        transport = ASGITransport(app=_app_with_ingest_token)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                path,
                content=b'{"msg": "hello"}',
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer wrong-token",
                },
            )
        assert resp.status_code == 401, (
            f"GUARD: {path} must return 401 on a bad ingest token — "
            f"got {resp.status_code}. A route that skips auth will return "
            "something other than 401 here."
        )
