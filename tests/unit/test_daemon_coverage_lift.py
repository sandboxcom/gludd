"""Tests for uncovered daemon code paths — targeting lines 97, 123-124, 128-129, 147-160,
171, 173, 185-188, 197, 204, 211-212, 326-327, 364, 371, 440, 452, 570, 591, 609-611,
737-743, 808-812, 822, 859-860, 870, 889, 908, 1066-1067, 1075-1076, 1319-1321, 1330-1332,
1362-1366, 1387-1402, 1411-1413, 1433, 1460-1482, 1513, 1535-1551, 1583-1593, 1619-1626,
1654-1655, 1702, 1730-1731, 1768-1775, 1920, 1935, 1962, 1971, 1988."""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.daemon import _daemon_state, create_daemon_app
from general_ludd.secrets.env import EnvSecretsManager


@pytest.fixture(autouse=True)
def _preserve_daemon_state():
    if _daemon_state is None or "todos" not in _daemon_state:
        yield
    else:
        snapshot = list(_daemon_state["todos"])
        yield
        _daemon_state["todos"] = snapshot


@pytest.fixture
def app(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    return create_daemon_app(config_dir=str(config_dir))


@pytest.fixture
def transport(app):
    return ASGITransport(app=app)


class TestLoadStartupConfigMcpAndTasks:
    @pytest.mark.asyncio
    async def test_load_startup_config_with_mcp_file(self, tmp_path):
        import yaml

        from general_ludd.daemon import load_startup_config

        config_dir = tmp_path / "cfg_mcp"
        config_dir.mkdir()
        mcp_dir = config_dir / "mcp_servers"
        mcp_dir.mkdir()
        mcp_file = mcp_dir / "example.yml"
        mcp_file.write_text(yaml.dump({"servers": {"test": {"command": ["echo", "hello"]}}}))
        cfg = load_startup_config(str(config_dir))
        assert "mcp_servers" in cfg

    @pytest.mark.asyncio
    async def test_load_startup_config_with_tasks_dir(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        config_dir = tmp_path / "cfg_tasks"
        config_dir.mkdir()
        tasks_dir = config_dir / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "sample.yml").write_text("name: test_task\nsteps: []\n")
        cfg = load_startup_config(str(config_dir))
        assert "task_definitions" in cfg

    @pytest.mark.asyncio
    async def test_load_startup_config_with_model_routing_in_general_ludd(self, tmp_path):
        import yaml

        from general_ludd.daemon import load_startup_config

        config_dir = tmp_path / "cfg_mr"
        config_dir.mkdir()
        gl_path = config_dir / "general-ludd.yml"
        gl_path.write_text(yaml.dump({"model_routing": {"default_profile": "fast"}}))
        cfg = load_startup_config(str(config_dir))
        assert cfg["user_config"].model_routing is not None
        assert cfg["user_config"].model_routing.default_profile == "fast"

    def test_embedded_model_routing_falls_back_when_user_model_omits_it(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        (tmp_path / "general-ludd.yml").write_text(
            "model_routing:\n  default_profile: fast\n"
        )
        user_config = SimpleNamespace(model_routing=None, rules=[], connectors=[])
        with patch("general_ludd.daemon.UserConfig", return_value=user_config):
            cfg = load_startup_config(str(tmp_path))

        assert cfg["model_routing"].default_profile == "fast"

    def test_mcp_list_inventory_accepts_only_named_mappings(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        mcp_dir = tmp_path / "mcp_servers"
        mcp_dir.mkdir()
        (mcp_dir / "servers.yml").write_text("servers: []\n")
        inventory = [
            {"name": "trusted", "command": ["safe"]},
            {"command": ["unnamed"]},
            "not-a-mapping",
        ]
        with patch("general_ludd.daemon.load_mcp_config", return_value=inventory):
            cfg = load_startup_config(str(tmp_path))

        assert cfg["mcp_servers"] == {"trusted": inventory[0]}

    def test_connector_inventory_ignores_non_list_payload(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        (tmp_path / "connectors.yml").write_text("connectors: invalid\n")
        cfg = load_startup_config(str(tmp_path))
        assert cfg["connectors"] == []

    def test_invalid_project_overlay_preserves_last_valid_user_config(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        overlay = tmp_path / "project.yml"
        overlay.write_text("rules:\n  - name: project-rule\n")
        base = SimpleNamespace(
            model_dump=lambda: {},
            rules=[],
            connectors=[],
        )
        missing_config_dir = tmp_path / "missing-config"
        with (
            patch("general_ludd.daemon.UserConfig", side_effect=[base, ValueError("invalid overlay")]),
            patch("general_ludd.daemon.find_project_gludd_dir", return_value=tmp_path),
            patch("general_ludd.daemon.project_config_path", return_value=overlay),
        ):
            cfg = load_startup_config(str(missing_config_dir))

        assert cfg["user_config"] is base


class TestBuildSecretsResolver:
    def test_build_secrets_resolver_openbao_external(self):
        from general_ludd.daemon import build_secrets_resolver
        from general_ludd.secrets.config import OpenBaoConfig

        cfg = OpenBaoConfig(mode="external", external_url="http://localhost:8200")
        with patch("general_ludd.daemon.SecretsManager") as MockMgr:
            instance = MockMgr.return_value
            instance.connect = MagicMock()
            build_secrets_resolver(openbao_config=cfg)
            assert instance.connect.called

    def test_build_secrets_resolver_openbao_failure(self):
        from general_ludd.daemon import build_secrets_resolver
        from general_ludd.secrets.config import OpenBaoConfig

        cfg = OpenBaoConfig(mode="external", external_url="http://localhost:8200")
        with patch(
            "general_ludd.daemon.SecretsManager",
            side_effect=Exception("connection refused"),
        ) as MockMgr:
            result = build_secrets_resolver(openbao_config=cfg)
            assert MockMgr.called, "SecretsManager constructor must be attempted before fallback"
            assert isinstance(result, EnvSecretsManager), (
                f"failure path must fall back to EnvSecretsManager, got {type(result)!r}"
            )

    def test_build_secrets_resolver_openbao_not_reachable(self):
        from general_ludd.daemon import build_secrets_resolver
        from general_ludd.secrets.config import OpenBaoConfig

        # Must supply external_url so that the auto+url branch (which calls
        # SecretsManager) is exercised rather than the trivial "no url → env" path.
        cfg = OpenBaoConfig(mode="auto", external_url="https://bao.example.internal:8200")
        with patch(
            "general_ludd.daemon.SecretsManager",
            side_effect=Exception("connection refused"),
        ) as MockMgr:
            result = build_secrets_resolver(openbao_config=cfg)
            assert MockMgr.called, "SecretsManager constructor must be attempted before fallback"
            assert isinstance(result, EnvSecretsManager), (
                f"auto-mode unreachable path must fall back to EnvSecretsManager, got {type(result)!r}"
            )

    def test_build_secrets_resolver_with_projects(self):
        from general_ludd.daemon import build_secrets_resolver

        result = build_secrets_resolver(projects_active=True)
        assert hasattr(result, "resolve")
        assert hasattr(result, "for_project")

    def test_build_secrets_resolver_projects_resolve_delegates(self):
        from general_ludd.daemon import build_secrets_resolver

        resolver = build_secrets_resolver(env_overrides={"TEST_KEY": "test_val"}, projects_active=True)
        assert resolver.resolve("TEST_KEY") is not None

    def test_unknown_openbao_mode_fails_soft_to_environment(self):
        from general_ludd.daemon import build_secrets_resolver

        cfg = SimpleNamespace(mode="future-mode", external_url=None)
        result = build_secrets_resolver(openbao_config=cfg)
        assert isinstance(result, EnvSecretsManager)

    def test_project_wrapper_scopes_resolves_and_closes(self):
        from general_ludd.daemon import build_secrets_resolver

        base = MagicMock()
        scoped = MagicMock()
        scoped.resolve.return_value = "project-secret"
        with (
            patch("general_ludd.daemon.EnvSecretsManager", return_value=base),
            patch("general_ludd.daemon.ProjectSecretsManager", return_value=scoped),
        ):
            resolver = build_secrets_resolver(projects_active=True)
            assert resolver.resolve("TOKEN", project_id="proj-a") == "project-secret"
            base.resolve.return_value = "global-secret"
            assert resolver.resolve("TOKEN") == "global-secret"
            base.resolve.return_value = object()
            assert resolver.resolve("TOKEN") is None
            resolver.close()

        base.close.assert_called_once_with()

    def test_sts_claim_builds_narrow_scoped_resolver(self):
        from general_ludd.daemon import resolve_secret_manager_for_call

        resolver = SimpleNamespace(_client=object(), _config=object())
        claim = SimpleNamespace(spec=object())
        registry = SimpleNamespace(resolve=lambda _token: claim)
        app = SimpleNamespace(
            state=SimpleNamespace(
                _secrets_resolver=resolver,
                _sts_registry=registry,
            )
        )
        narrowed = object()
        with patch("general_ludd.secrets.manager.SecretsManager", return_value=narrowed) as cls:
            assert resolve_secret_manager_for_call(app, "Bearer scoped-token") is narrowed
        cls.assert_called_once_with(
            client=resolver._client,
            config=resolver._config,
            permission_spec=claim.spec,
        )

    def test_project_wrapper_close_tolerates_base_without_close(self):
        from general_ludd.daemon import build_secrets_resolver

        base = SimpleNamespace(resolve=lambda _alias: None)
        with patch("general_ludd.daemon.EnvSecretsManager", return_value=base):
            resolver = build_secrets_resolver(projects_active=True)
            resolver.close()


class TestInitProjectWorkspaces:
    def test_init_project_workspaces_with_projects(self):
        from general_ludd.daemon import _init_project_workspaces

        mock_pm = MagicMock()
        mock_project = MagicMock()
        mock_project.project_id = "test-proj"
        mock_project.workspace_path = "test-proj"
        mock_project.repo_url = "https://example.com/test-proj.git"
        mock_pm.list_active.return_value = [mock_project]
        with patch("general_ludd.projects.workspace.ProjectWorkspace") as MockWS:
            ws = MagicMock()
            MockWS.return_value = ws
            result = _init_project_workspaces(mock_pm)
            assert "test-proj" in result

    def test_init_project_workspaces_exception(self):
        from general_ludd.daemon import _init_project_workspaces

        mock_pm = MagicMock()
        mock_pm.list_active.side_effect = RuntimeError("db error")
        result = _init_project_workspaces(mock_pm)
        assert result == {}

    def test_init_project_workspaces_none(self):
        from general_ludd.daemon import _init_project_workspaces

        result = _init_project_workspaces(None)
        assert result == {}


class TestPersistedStartupRestoration:
    @pytest.mark.asyncio
    async def test_project_restore_noops_without_both_dependencies(self):
        from general_ludd.daemon import _restore_persisted_projects

        await _restore_persisted_projects(None, object())
        await _restore_persisted_projects(object(), None)

    @pytest.mark.asyncio
    async def test_project_restore_merges_new_binding_and_materializes_repo(self):
        from general_ludd.daemon import _restore_persisted_projects

        session = MagicMock()
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        configured = SimpleNamespace(project_id="proj-configured", repo_url="")
        restored = SimpleNamespace(
            project_id="proj-restored",
            repo_url="https://example.com/org/restored.git",
            workspace_path="team/restored",
        )
        db_manager = SimpleNamespace(list_active=lambda: [configured, restored])
        manager = SimpleNamespace(
            _projects={configured.project_id: configured},
            list_projects=lambda *, active_only: [configured],
        )
        rebuild = AsyncMock(return_value=db_manager)
        with (
            patch("general_ludd.projects.manager.rebuild_manager_from_db", rebuild),
            patch("general_ludd.projects.manager.materialize_project_workspace") as materialize,
        ):
            await _restore_persisted_projects(manager, factory)

        assert manager._projects[restored.project_id] is restored
        materialize.assert_called_once_with(
            repo_url=restored.repo_url,
            workspace_path=restored.workspace_path,
        )

    @pytest.mark.asyncio
    async def test_spend_restore_noops_without_both_dependencies(self):
        from general_ludd.daemon import _restore_persisted_spend

        await _restore_persisted_spend(None, object(), window_seconds=60.0)
        await _restore_persisted_spend(object(), None, window_seconds=60.0)


class TestLoadModelProfiles:
    def test_load_model_profiles_none(self):
        from general_ludd.daemon import load_model_profiles

        assert load_model_profiles(None) == []

    def test_load_model_profiles_with_underscore_skip(self, tmp_path):
        from general_ludd.daemon import load_model_profiles

        (tmp_path / "_skip.yml").write_text("model_id: skip\nprovider: test\n")
        result = load_model_profiles(str(tmp_path))
        assert result == []

    def test_load_model_profiles_with_bad_yaml(self, tmp_path):
        from general_ludd.daemon import load_model_profiles

        bad = tmp_path / "bad.yml"
        bad.write_text("{{invalid yaml::")
        result = load_model_profiles(str(tmp_path))
        assert result == []

    def test_load_model_profiles_skips_explicitly_disabled_profile(self, tmp_path):
        from general_ludd.daemon import load_model_profiles

        (tmp_path / "disabled.yml").write_text(
            "model_profile_id: disabled\nprovider: openai\nmodel_name: ignored\nenabled: false\n"
        )
        assert load_model_profiles(str(tmp_path)) == []


class TestDaemonHelperBranches:
    @pytest.mark.asyncio
    async def test_factory_lazy_dispatch_handlers_rebind_or_fail_closed(self, tmp_path):
        app = create_daemon_app(config_dir=str(tmp_path))
        route = next(
            candidate
            for candidate in app.routes
            if getattr(candidate, "path", None) == "/api/dispatch"
        )
        dispatcher = inspect.getclosurevars(route.endpoint).nonlocals["dispatcher"]
        handlers = dispatcher._handlers

        with pytest.raises(RuntimeError, match="MCP client"):
            await handlers["mcp"]("server/tool", {})
        with pytest.raises(RuntimeError, match="SkillRegistry"):
            handlers["skill"]("skill", {})
        with pytest.raises(RuntimeError, match="AgentDispatcher"):
            await handlers["role"]("coder", {})
        with pytest.raises(RuntimeError, match="AnsibleRunnerAdapter"):
            await handlers["collection"]("general_ludd.agent.local_model_stop", {})

        mcp_client = SimpleNamespace(call_tool=AsyncMock(return_value={"ok": True}))
        skill_registry = SimpleNamespace(
            get=MagicMock(return_value=SimpleNamespace(body="trusted skill"))
        )
        agent_dispatcher = SimpleNamespace(
            dispatch_one=AsyncMock(return_value=SimpleNamespace(output="role output"))
        )
        runner = SimpleNamespace(
            private_data_dir=str(tmp_path),
            register_playbook=MagicMock(),
            unregister_playbook=MagicMock(),
            run_playbook=MagicMock(return_value={"rc": 0}),
        )
        app.state._mcp_client = mcp_client
        app.state._skill_registry = skill_registry
        app.state._agent_dispatcher = agent_dispatcher
        app.state._runner = runner

        assert await handlers["mcp"]("server/tool", {"value": 1}) == {"ok": True}
        assert handlers["skill"]("skill", {}) == "trusted skill"
        assert await handlers["role"]("coder", {"prompt": "bounded"}) == "role output"
        assert await handlers["collection"](
            "general_ludd.agent.local_model_stop",
            {"model": "small"},
        ) == {"rc": 0}
        mcp_client.call_tool.assert_awaited_once_with("server", "tool", {"value": 1})
        skill_registry.get.assert_called_once_with("skill")
        agent_dispatcher.dispatch_one.assert_awaited_once()
        dispatched_task = agent_dispatcher.dispatch_one.await_args.args[0]
        assert dispatched_task.agent_name == "coder"
        assert dispatched_task.prompt == "bounded"
        runner.run_playbook.assert_called_once_with(
            "local_model_stop.yml",
            extravars={"model": "small"},
            timeout=None,
        )

    def test_factory_preserves_explicit_runtime_paths_and_repairs_state_proxy(
        self,
        tmp_path,
        monkeypatch,
    ):
        import general_ludd.daemon as daemon_module

        monkeypatch.setattr(daemon_module, "_daemon_state", {})
        templates = tmp_path / "templates"
        playbooks = tmp_path / "playbooks"
        app = daemon_module.create_daemon_app(
            tick_interval=2.0,
            log_level="warning",
            config_dir=str(tmp_path),
            templates_dir=str(templates),
            playbooks_dir=str(playbooks),
        )
        assert app.state.tick_interval == 2.0
        assert app.state.log_level == "warning"
        assert app.state._config_dir == str(tmp_path)
        assert app.state._templates_dir == str(templates)
        assert app.state._playbooks_dir == str(playbooks)
        assert isinstance(daemon_module._daemon_state, daemon_module._DaemonStateProxy)

    def test_factory_passes_dedicated_connector_inventory_to_observability(
        self,
        monkeypatch,
    ):
        connector = {"type": "bounded-test"}
        startup = {
            "connectors": [connector],
            "project_gludd_dir": None,
            "user_config": SimpleNamespace(connectors=[{"type": "embedded"}]),
        }
        wired = MagicMock()
        monkeypatch.setattr(
            "general_ludd.daemon.load_startup_config",
            lambda _config_dir: startup,
        )
        monkeypatch.setattr(
            "general_ludd.routers.observe.wire_observability",
            wired,
        )
        app = create_daemon_app()
        wired.assert_called_once_with(app, app.state.daemon_state, [connector])

    def test_startup_config_surfaces_empty_lists_when_loader_returns_none(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        with patch("general_ludd.daemon.load_user_config", return_value=None):
            config = load_startup_config(str(tmp_path))
        assert config["user_config"] is None
        assert config["rules"] == []
        assert config["connectors"] == []

    def test_dynamic_dispatcher_is_absent_without_handlers(self):
        from general_ludd.daemon import build_event_loop_mcp_dispatcher

        assert (
            build_event_loop_mcp_dispatcher(
                mcp_client=None,
                mcp_tool_registry=None,
                skill_registry=None,
                agent_dispatcher=None,
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_warm_start_noops_when_profiles_are_remote(self):
        from general_ludd.daemon import _warm_start_local_models

        gateway = SimpleNamespace(
            _profiles={
                "remote": SimpleNamespace(resource_profile="medium", provider="openai")
            }
        )
        await _warm_start_local_models(gateway)

    @pytest.mark.asyncio
    async def test_lifespan_shutdown_closes_all_optional_owned_resources(self):
        from fastapi import FastAPI

        from general_ludd.daemon import _lifespan
        from tests.unit.test_daemon import _lifespan_patches

        event_loop = MagicMock()
        event_loop.run_forever = AsyncMock()
        event_loop.shutdown = AsyncMock()
        app = FastAPI()
        app.state.tick_interval = 0.01
        app.state.event_loop = None
        app.state._receiver_buffer = MagicMock()

        session = MagicMock()
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=session)
        session_cm.__aexit__ = AsyncMock(return_value=False)
        session_factory = MagicMock(return_value=session_cm)
        slurm_repo = MagicMock()
        slurm_repo.list_orphans = AsyncMock(return_value=[])
        job = SimpleNamespace(job_id="job-owned")
        slurm_repo.list_active = AsyncMock(return_value=[job])
        slurm_repo.update_status = AsyncMock()
        slurm_adapter = MagicMock()

        off_peak_task = asyncio.get_running_loop().create_future()
        off_peak_stop = MagicMock()
        off_peak_scheduler = SimpleNamespace(
            savings=SimpleNamespace(total_deferred=2, total_savings=1.25)
        )
        monitor = MagicMock()
        pipeline = SimpleNamespace(stop=AsyncMock())
        mcp_client = SimpleNamespace(stop_all=AsyncMock())
        terraform_bridge = SimpleNamespace(aclose=AsyncMock())
        event_bus = SimpleNamespace(drain=AsyncMock())
        deployment_router = SimpleNamespace(aclose=AsyncMock())
        credit_tracker = SimpleNamespace(close=MagicMock())
        secrets_resolver = SimpleNamespace(close=MagicMock())
        searx_client = SimpleNamespace(close=AsyncMock())
        model_gateway = SimpleNamespace(close=MagicMock())
        cache_owners = [SimpleNamespace(close=MagicMock()) for _ in range(4)]
        web_cache = SimpleNamespace(close=MagicMock())
        stall_watchdog = SimpleNamespace(stop_sweeper=MagicMock())
        write_queue = SimpleNamespace(clear=MagicMock())
        writer_process = SimpleNamespace(stop=MagicMock())
        embedding_session = SimpleNamespace(close=AsyncMock())
        otel_bridge = SimpleNamespace(shutdown=MagicMock())
        ornith_process = SimpleNamespace(
            terminate=MagicMock(),
            wait=AsyncMock(return_value=0),
            kill=MagicMock(),
        )
        searx_server = SimpleNamespace(stop=MagicMock())
        quantization_monitor = SimpleNamespace(stop=AsyncMock())

        with (
            _lifespan_patches(event_loop),
            patch("general_ludd.daemon.SlurmJobRepository", return_value=slurm_repo),
            patch("general_ludd.infra.slurm.SlurmAdapter", return_value=slurm_adapter),
        ):
            async with _lifespan(app):
                app.state._off_peak_stop = off_peak_stop
                app.state._off_peak_task = off_peak_task
                app.state._off_peak_scheduler = off_peak_scheduler
                app.state._session_factory = session_factory
                app.state._slurm_monitors = {job.job_id: monitor}
                app.state._pipeline_controller = pipeline
                app.state._mcp_client = mcp_client
                app.state._terraform_event_bridge = terraform_bridge
                app.state._event_bus = event_bus
                app.state._deployment_health_router = deployment_router
                app.state._credit_tracker = credit_tracker
                app.state._secrets_resolver = secrets_resolver
                app.state._searx_client = searx_client
                app.state._model_gateway = model_gateway
                (
                    app.state._codebase_indexer,
                    app.state._research_index,
                    app.state._local_memory,
                    app.state._semantic_searcher,
                ) = cache_owners
                app.state._web_retriever = SimpleNamespace(_cache=web_cache)
                app.state._stall_watchdog = stall_watchdog
                app.state._write_queue = write_queue
                app.state._writer_process = writer_process
                app.state._embedding_session = embedding_session
                app.state._otel_bridge = otel_bridge
                app.state._ornith_mcp_proc = ornith_process
                app.state._searx_server = searx_server
                app.state._quantization_monitor = quantization_monitor

        off_peak_stop.set.assert_called_once_with()
        assert off_peak_task.cancelled()
        slurm_adapter.cancel.assert_called_once_with(job.job_id)
        slurm_repo.update_status.assert_awaited_once_with(job.job_id, "cancelled")
        monitor.stop.assert_called_once_with()
        pipeline.stop.assert_awaited_once_with()
        mcp_client.stop_all.assert_awaited_once_with()
        terraform_bridge.aclose.assert_awaited_once_with()
        event_bus.drain.assert_awaited_once_with()
        deployment_router.aclose.assert_awaited_once_with()
        credit_tracker.close.assert_called_once_with()
        secrets_resolver.close.assert_called_once_with()
        searx_client.close.assert_awaited_once_with()
        model_gateway.close.assert_called_once_with()
        for owner in cache_owners:
            owner.close.assert_called_once_with()
        web_cache.close.assert_called_once_with()
        stall_watchdog.stop_sweeper.assert_called_once_with()
        write_queue.clear.assert_called_once_with()
        writer_process.stop.assert_called_once_with()
        embedding_session.close.assert_awaited_once_with()
        otel_bridge.shutdown.assert_called_once_with()
        ornith_process.terminate.assert_called_once_with()
        ornith_process.kill.assert_not_called()
        searx_server.stop.assert_called_once_with()
        quantization_monitor.stop.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_lifespan_shutdown_isolates_failures_and_reports_group(self):
        from fastapi import FastAPI

        from general_ludd.daemon import _lifespan
        from tests.unit.test_daemon import _lifespan_patches

        event_loop = MagicMock()
        event_loop.run_forever = AsyncMock()
        event_loop.shutdown = AsyncMock(side_effect=RuntimeError("loop shutdown"))
        app = FastAPI()
        app.state.tick_interval = 0.01
        app.state.event_loop = None
        app.state._receiver_buffer = MagicMock()
        bad_engine = MagicMock()
        bad_engine.url = "sqlite+aiosqlite:///test.db"
        bad_engine.dispose = AsyncMock(side_effect=RuntimeError("engine dispose"))
        ornith_process = SimpleNamespace(
            terminate=MagicMock(),
            wait=AsyncMock(side_effect=[TimeoutError(), 0]),
            kill=MagicMock(),
        )

        with (
            _lifespan_patches(event_loop),
            patch("general_ludd.daemon.init_engine_from_config", return_value=bad_engine),
            pytest.raises(ExceptionGroup) as exc_info,
        ):
            async with _lifespan(app):
                app.state._pipeline_controller = SimpleNamespace(
                    stop=AsyncMock(side_effect=RuntimeError("pipeline"))
                )
                app.state._mcp_client = SimpleNamespace(
                    stop_all=AsyncMock(side_effect=RuntimeError("mcp"))
                )
                app.state._terraform_event_bridge = SimpleNamespace(
                    aclose=AsyncMock(side_effect=RuntimeError("terraform"))
                )
                app.state._event_bus = SimpleNamespace(
                    drain=AsyncMock(side_effect=RuntimeError("event bus"))
                )
                app.state._deployment_health_router = SimpleNamespace(
                    aclose=AsyncMock(side_effect=RuntimeError("deployment"))
                )
                app.state._credit_tracker = SimpleNamespace(
                    close=MagicMock(side_effect=RuntimeError("credit"))
                )
                app.state._secrets_resolver = SimpleNamespace(
                    close=MagicMock(side_effect=RuntimeError("secrets"))
                )
                app.state._searx_client = SimpleNamespace(
                    close=AsyncMock(side_effect=RuntimeError("searx client"))
                )
                app.state._model_gateway = SimpleNamespace(
                    close=MagicMock(side_effect=RuntimeError("gateway"))
                )
                app.state._codebase_indexer = SimpleNamespace(
                    close=MagicMock(side_effect=RuntimeError("cache"))
                )
                app.state._web_retriever = SimpleNamespace(
                    _cache=SimpleNamespace(
                        close=MagicMock(side_effect=RuntimeError("web cache"))
                    )
                )
                app.state._write_queue = SimpleNamespace(
                    clear=MagicMock(side_effect=RuntimeError("queue"))
                )
                app.state._writer_process = SimpleNamespace(
                    stop=MagicMock(side_effect=RuntimeError("writer"))
                )
                app.state._embedding_session = SimpleNamespace(
                    close=AsyncMock(side_effect=RuntimeError("embedding"))
                )
                app.state._otel_bridge = SimpleNamespace(
                    shutdown=MagicMock(side_effect=RuntimeError("otel"))
                )
                app.state._ornith_mcp_proc = ornith_process
                app.state._searx_server = SimpleNamespace(
                    stop=MagicMock(side_effect=RuntimeError("searx server"))
                )
                app.state._quantization_monitor = SimpleNamespace(
                    stop=AsyncMock(side_effect=RuntimeError("quantization"))
                )

        assert len(exc_info.value.exceptions) == 5
        ornith_process.kill.assert_called_once_with()
        assert ornith_process.wait.await_count == 2

    @pytest.mark.asyncio
    async def test_lifespan_preserves_body_failure_with_and_without_cleanup_failure(self):
        from fastapi import FastAPI

        from general_ludd.daemon import _lifespan
        from tests.unit.test_daemon import _lifespan_patches

        def app_and_loop() -> tuple[FastAPI, MagicMock]:
            app = FastAPI()
            app.state.tick_interval = 0.01
            app.state.event_loop = None
            app.state._receiver_buffer = MagicMock()
            loop = MagicMock()
            loop.run_forever = AsyncMock()
            loop.shutdown = AsyncMock()
            return app, loop

        app, event_loop = app_and_loop()
        with _lifespan_patches(event_loop), pytest.raises(LookupError, match="body"):
            async with _lifespan(app):
                raise LookupError("body")

        app, event_loop = app_and_loop()
        with _lifespan_patches(event_loop), pytest.raises(BaseExceptionGroup) as exc_info:
            async with _lifespan(app):
                app.state._pipeline_controller = SimpleNamespace(
                    stop=AsyncMock(side_effect=RuntimeError("cleanup"))
                )
                raise LookupError("body")
        assert len(exc_info.value.exceptions) == 2

    @pytest.mark.asyncio
    async def test_warm_start_skips_missing_url_and_bounds_success_and_failure(
        self,
        monkeypatch,
    ):
        from general_ludd.daemon import _warm_start_local_models

        monkeypatch.setenv("GLUDD_LOCAL_ONE", "http://127.0.0.1:8101/")
        monkeypatch.setenv("GLUDD_LOCAL_TWO", "http://127.0.0.1:8102")
        profiles = {
            "missing": SimpleNamespace(
                model_profile_id="missing",
                resource_profile="local_heavy",
                provider="openai",
                api_base_alias=None,
            ),
            "success": SimpleNamespace(
                model_profile_id="success",
                resource_profile="medium",
                provider="llamacpp",
                api_base_alias="GLUDD_LOCAL_ONE",
            ),
            "failure": SimpleNamespace(
                model_profile_id="failure",
                resource_profile="medium",
                provider="vllm",
                api_base_alias="GLUDD_LOCAL_TWO",
            ),
        }
        client = MagicMock()
        client.get = AsyncMock(
            side_effect=[SimpleNamespace(status_code=204), RuntimeError("offline")]
        )
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=context):
            await _warm_start_local_models(SimpleNamespace(_profiles=profiles))

        assert client.get.await_args_list[0].args == ("http://127.0.0.1:8101/health",)
        assert client.get.await_args_list[1].args == ("http://127.0.0.1:8102/health",)

    @pytest.mark.asyncio
    async def test_self_update_audit_sink_persists_and_contains_failure(self):
        from general_ludd.daemon import _build_self_update_audit_sink

        session = MagicMock()
        session.commit = AsyncMock()
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        record = SimpleNamespace(
            outcome="applied",
            requested_by="operator",
            as_dict=lambda: {"outcome": "applied"},
        )
        tasks: set[asyncio.Task[Any]] = set()
        repo = MagicMock()
        repo.create = AsyncMock()

        with patch("general_ludd.daemon.AuditEventRepository", return_value=repo):
            sink = _build_self_update_audit_sink(factory, task_registry=tasks)
            sink(record)
            pending = tuple(tasks)
            assert pending
            await asyncio.gather(*pending)
        repo.create.assert_awaited_once()
        session.commit.assert_awaited_once()

        failing_repo = MagicMock()
        failing_repo.create = AsyncMock(side_effect=RuntimeError("db unavailable"))
        with patch("general_ludd.daemon.AuditEventRepository", return_value=failing_repo):
            sink = _build_self_update_audit_sink(factory, task_registry=tasks)
            sink(record)
            pending = tuple(tasks)
            assert pending
            await asyncio.gather(*pending)

    def test_self_update_audit_sink_skips_without_running_loop(self):
        from general_ludd.daemon import _build_self_update_audit_sink

        factory = MagicMock()
        sink = _build_self_update_audit_sink(factory)
        sink(SimpleNamespace(outcome="applied"))
        factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_sts_audit_logger_handles_missing_row_and_corrupt_history(self):
        from general_ludd.daemon import _build_sts_audit_logger

        session = MagicMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        query_result = SimpleNamespace(scalar_one_or_none=MagicMock(return_value=None))
        session.execute.return_value = query_result
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        audit = _build_sts_audit_logger(factory)

        await audit("missing", "use", "agent")
        session.commit.assert_not_awaited()

        row = SimpleNamespace(use_count=None, events="not-json", last_used_at=None)
        query_result.scalar_one_or_none.return_value = row
        await audit("present", "use", "agent")
        assert row.use_count == 1
        assert row.events == '["use"]'
        assert row.last_used_at is not None
        session.commit.assert_awaited_once_with()

        row.events = '["use"]'
        await audit("present", "refresh", "agent")
        assert row.use_count == 2
        assert row.events == '["use", "refresh"]'

    def test_extended_subsystems_register_global_and_project_skills(self, tmp_path):
        from general_ludd.daemon import _get_or_create_extended_subsystems

        project_gludd = tmp_path / ".gludd"
        (project_gludd / "skills").mkdir(parents=True)
        state = SimpleNamespace(
            _metrics_collector=object(),
            _recent_traces=object(),
            _receiver_buffer=object(),
            _project_manager=object(),
            _utilization_tracker=object(),
            _model_registry=object(),
            _skill_registry=None,
            _config_dir=str(tmp_path),
            _project_gludd_dir=str(project_gludd),
        )
        app = SimpleNamespace(state=state)
        registry = MagicMock()
        skills = [object(), object()]
        with (
            patch("general_ludd.daemon.SkillRegistry", return_value=registry),
            patch("general_ludd.daemon.discover_skills", return_value=skills),
        ):
            result = _get_or_create_extended_subsystems(app)

        assert result["skill_registry"] is registry
        assert [call.args[0] for call in registry.register.call_args_list] == skills
        registry.refresh.assert_called_once_with(
            search_paths=[str(project_gludd / "skills")]
        )

    def test_extended_subsystems_builds_router_from_relationship_policy(self):
        import general_ludd.daemon as daemon_module

        rr = SimpleNamespace(
            enable_cross_project_borrowing=True,
            edge_decay=0.7,
            external_penalty=0.4,
            min_borrow_weight=0.2,
        )
        tracker = SimpleNamespace(
            _data={"model": SimpleNamespace(precision="int8", confidence=0.9)}
        )
        state = SimpleNamespace(
            _metrics_collector=object(),
            _recent_traces=object(),
            _receiver_buffer=object(),
            _project_manager=object(),
            _utilization_tracker=object(),
            _model_registry=object(),
            _skill_registry=object(),
            _adaptive_router=daemon_module._STARTUP_UNSET,
            _startup_config={
                "user_config": SimpleNamespace(relationship_routing=rr)
            },
            _quantization_tracker=tracker,
            _health_tracker=object(),
            _embedding_store=object(),
        )
        app = SimpleNamespace(state=state)
        built_router = object()
        with (
            patch("general_ludd.daemon.BenchmarkRepository"),
            patch("general_ludd.daemon.ParetoRouter"),
            patch("general_ludd.daemon.AdaptiveRouter", return_value=built_router) as cls,
        ):
            result = daemon_module._get_or_create_extended_subsystems(
                app,
                session_factory=object(),
            )

        assert result["adaptive_router"] is built_router
        assert state._adaptive_router is built_router
        assert cls.call_args.kwargs["quantization_map"] == {"model": ("int8", 0.9)}
        assert cls.call_args.kwargs["enable_cross_project_borrowing"] is True
        assert cls.call_args.kwargs["edge_decay"] == 0.7
        assert cls.call_args.kwargs["external_penalty"] == 0.4
        assert cls.call_args.kwargs["min_borrow_weight"] == 0.2

    def test_extended_subsystems_reuses_existing_router(self):
        import general_ludd.daemon as daemon_module

        existing = object()
        state = SimpleNamespace(
            _metrics_collector=object(),
            _recent_traces=object(),
            _receiver_buffer=object(),
            _project_manager=object(),
            _utilization_tracker=object(),
            _model_registry=object(),
            _skill_registry=object(),
            _adaptive_router=existing,
        )
        result = daemon_module._get_or_create_extended_subsystems(
            SimpleNamespace(state=state),
            session_factory=object(),
        )
        assert result["adaptive_router"] is existing


class TestApiStatusWithConfigDir:
    @pytest.mark.asyncio
    async def test_api_status_lists_config_files(self, tmp_path):
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        (config_dir / "general-ludd.yml").write_text("key: val\n")
        (config_dir / "other.yaml").write_text("key2: val2\n")
        app = create_daemon_app(config_dir=str(config_dir))
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["config_file_count"] == 2


class TestDaemonStatusEndpointBranches:
    @pytest.mark.asyncio
    async def test_dashboard_provider_absent_and_available(self, app, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            missing = await client.get("/admin/dashboard/overview")
            assert missing.json() == {
                "error": "Dashboard data provider not initialized"
            }

            provider = SimpleNamespace(
                get_overview=AsyncMock(return_value={"status": "ready"})
            )
            app.state._dashboard_data = provider
            ready = await client.get("/admin/dashboard/overview")
            assert ready.json() == {"status": "ready"}
            provider.get_overview.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_eval_and_execution_status_absent_and_available(self, app, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            missing_eval = await client.get("/admin/eval/status")
            assert missing_eval.json() == {"status": "not_configured", "ready": False}
            app.state.eval_harness = SimpleNamespace(ready=True, model="judge")
            configured_eval = await client.get("/admin/eval/status")
            assert configured_eval.json() == {
                "status": "configured",
                "ready": True,
                "model": "judge",
            }

            missing_engine = await client.get("/admin/execution/engine-status")
            assert missing_engine.json()["status"] == "not_configured"
            app.state._execution_engine = SimpleNamespace(
                workspace_path="trusted/workspace",
                _model_gateway=object(),
                _budget_guard=None,
                _metrics_collector=object(),
            )
            configured_engine = await client.get("/admin/execution/engine-status")
            assert configured_engine.json() == {
                "status": "configured",
                "workspace_path": "trusted/workspace",
                "has_model_gateway": True,
                "has_budget_guard": False,
                "has_metrics_collector": True,
            }

    @pytest.mark.asyncio
    async def test_plan_critique_status_and_execution_branches(self, app, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            app.state.plan_critique = None
            missing_status = await client.get("/admin/plan/critique-status")
            assert missing_status.json() == {"wired": False, "class": None}
            missing_run = await client.post("/admin/plan/critique", json={})
            assert missing_run.json() == {
                "status": "not_configured",
                "findings": [],
            }

            critique = SimpleNamespace(
                critique_plan=MagicMock(return_value=[{"severity": "info"}])
            )
            app.state.plan_critique = critique
            ready_status = await client.get("/admin/plan/critique-status")
            assert ready_status.json() == {
                "wired": True,
                "class": "SimpleNamespace",
            }
            ready_run = await client.post(
                "/admin/plan/critique",
                json={"title": "Bound plan"},
            )
            assert ready_run.json()["finding_count"] == 1
            critique.critique_plan.assert_called_once_with({"title": "Bound plan"})

    @pytest.mark.asyncio
    async def test_compaction_and_connector_status_branches(self, app, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            empty_compaction = await client.get("/admin/compaction/eval-status")
            assert empty_compaction.json() == {
                "wired": False,
                "champion": None,
                "metrics": None,
            }
            app.state._compaction_compactor = SimpleNamespace(
                champion=SimpleNamespace(name="bounded")
            )
            app.state._compaction_metrics = SimpleNamespace(
                model_dump=lambda: {"score": 0.9}
            )
            ready_compaction = await client.get("/admin/compaction/eval-status")
            assert ready_compaction.json() == {
                "wired": True,
                "champion": "bounded",
                "metrics": {"score": 0.9},
            }

            app.state._connector_registry = None
            empty_connectors = await client.get("/admin/connectors/health")
            assert empty_connectors.json() == {
                "health": {},
                "count": 0,
                "errors": [],
            }
            registry = SimpleNamespace(
                health_all=MagicMock(return_value={"source": {"ok": True}}),
                errors=MagicMock(return_value=[]),
            )
            app.state._connector_registry = registry
            ready_connectors = await client.get("/admin/connectors/health")
            assert ready_connectors.json() == {
                "health": {"source": {"ok": True}},
                "count": 1,
                "errors": [],
            }
            registry.health_all.assert_called_once_with()
            registry.errors.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_healthz_contains_local_probe_failure_and_dead_loop(self, app, transport):
        class DeadTask:
            def __init__(self, *, cancelled: bool) -> None:
                self._cancelled = cancelled

            def done(self) -> bool:
                return True

            def cancelled(self) -> bool:
                return self._cancelled

        with patch(
            "general_ludd.daemon.local_model_health_check",
            new=AsyncMock(side_effect=RuntimeError("probe failed")),
        ):
            app.state._event_loop_task = DeadTask(cancelled=True)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                cancelled = await client.get("/healthz")
                app.state._event_loop_task = DeadTask(cancelled=False)
                finished = await client.get("/healthz")

        assert cancelled.status_code == 200
        assert cancelled.json()["reason"] == "event_loop_cancelled"
        assert cancelled.json()["local_model"] == {
            "model_exists": False,
            "llama_cpp_available": False,
        }
        assert finished.status_code == 200
        assert finished.json()["reason"] == "event_loop_done"


class TestDaemonAuthenticationAndCidrBranches:
    @staticmethod
    def _authenticated_app(monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "test-secret")
        monkeypatch.delenv("GLUDD_PSK", raising=False)
        monkeypatch.delenv("GLUDD_PSK_DISABLE", raising=False)
        monkeypatch.delenv("GLUDD_ALLOW_NO_AUTH", raising=False)
        monkeypatch.delenv("GLUDD_REQUIRE_AUTH", raising=False)
        return create_daemon_app()

    @pytest.mark.asyncio
    async def test_method_path_and_project_claim_auth_matrix(self, monkeypatch):
        app = self._authenticated_app(monkeypatch)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/healthz")).status_code == 200
            assert (await client.get("/v1/not-a-route")).status_code != 401
            assert (await client.get("/render/not-found")).status_code != 401
            assert (await client.post("/healthz")).status_code == 401
            assert (await client.get("/docs_evil")).status_code == 401

            plain = await client.get(
                "/admin/plan/critique-status",
                headers={"Authorization": "Bearer test-secret"},
            )
            assert plain.status_code == 200
            claimed = await client.get(
                "/admin/plan/critique-status",
                headers={"Authorization": "Bearer project-a:test-secret"},
            )
            assert claimed.status_code == 200

    @pytest.mark.asyncio
    async def test_cidr_denies_outside_and_accepts_inside_network(self, monkeypatch):
        app = self._authenticated_app(monkeypatch)
        headers = {"Authorization": "Bearer test-secret"}
        app.state._allowed_cidr = ["10.0.0.0/8"]
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            denied = await client.get("/admin/plan/critique-status", headers=headers)
            assert denied.status_code == 403

        app.state._allowed_cidr = ["127.0.0.0/8"]
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            accepted = await client.get("/admin/plan/critique-status", headers=headers)
            assert accepted.status_code == 200

    @pytest.mark.asyncio
    async def test_cidr_rejects_unparseable_client_address(self, monkeypatch):
        app = self._authenticated_app(monkeypatch)
        app.state._allowed_cidr = ["127.0.0.0/8"]
        transport = ASGITransport(app=app, client=("not-an-ip", 1234))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/admin/plan/critique-status",
                headers={"Authorization": "Bearer test-secret"},
            )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_cidr_normalizes_testclient_to_loopback(self, monkeypatch):
        app = self._authenticated_app(monkeypatch)
        app.state._allowed_cidr = ["127.0.0.0/8"]
        transport = ASGITransport(app=app, client=("testclient", 1234))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/admin/plan/critique-status",
                headers={"Authorization": "Bearer test-secret"},
            )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_required_auth_without_psk_fails_closed(self, monkeypatch):
        monkeypatch.delenv("GLUDD_AUTH_PSK", raising=False)
        monkeypatch.delenv("GLUDD_PSK", raising=False)
        monkeypatch.delenv("GLUDD_PSK_DISABLE", raising=False)
        monkeypatch.delenv("GLUDD_ALLOW_NO_AUTH", raising=False)
        monkeypatch.setenv("GLUDD_REQUIRE_AUTH", "1")
        app = create_daemon_app()
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            blocked = await client.get("/admin/plan/critique-status")
            public = await client.get("/healthz")
        assert blocked.status_code == 503
        assert blocked.json()["error"] == "auth_required"
        assert public.status_code == 200

    @pytest.mark.asyncio
    async def test_degraded_state_guards_mutating_dispatch_surface(self, monkeypatch):
        app = self._authenticated_app(monkeypatch)
        app.state._degraded = "startup failed"
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/dispatch",
                json={},
                headers={"Authorization": "Bearer test-secret"},
            )
        assert response.status_code == 503
        assert response.json() == {
            "error": "degraded",
            "reason": "startup failed",
        }


class TestApiListTodosWithStatusFilter:
    @pytest.mark.asyncio
    async def test_api_todos_status_filter(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp1 = await client.post(
                "/api/todos",
                json={
                    "title": "Done",
                    "queue": "core",
                    "priority": "high",
                    "work_type": "fix",
                },
            )
            assert resp1.status_code == 201
            resp2 = await client.post(
                "/api/todos",
                json={
                    "title": "Pending",
                    "queue": "core",
                    "priority": "medium",
                    "work_type": "code",
                },
            )
            assert resp2.status_code == 201
        todos = app.state.daemon_state["todos"]
        assert len(todos) >= 2, f"expected >=2 todos, got {len(todos)}"
        todos[-2]["status"] = "completed"
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/todos", params={"status": "completed"})
            assert resp.status_code == 200
            data = resp.json()
            assert all(t["status"] == "completed" for t in data)


class TestAdminTodosWithProjectIdFilter:
    @pytest.mark.asyncio
    async def test_admin_todos_project_id_filter(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/api/todos",
                json={
                    "title": "P1",
                    "queue": "core",
                    "priority": "high",
                    "work_type": "fix",
                    "project_id": "proj-a",
                },
            )
            await client.post(
                "/api/todos",
                json={
                    "title": "P2",
                    "queue": "core",
                    "priority": "medium",
                    "work_type": "code",
                    "project_id": "proj-b",
                },
            )
            resp = await client.get("/admin/todos", params={"project_id": "proj-a"})
            assert resp.status_code == 200
            data = resp.json()
            assert all(t.get("project_id") == "proj-a" for t in data["todos"])


class TestAdminModelsDiscoverWithCredentials:
    @pytest.mark.asyncio
    async def test_models_discover_with_credential_alias(self, app, transport):
        mock_scraped = [
            {
                "model_name": "test-model",
                "cost_per_input_token": 0.0,
                "cost_per_output_token": 0.0,
                "context_window": 4096,
                "is_free": True,
                "role_names": ["coder"],
                "quality_class": "good",
            },
        ]
        with (
            patch(
                "general_ludd.models.openrouter_discovery.OpenRouterScraper.fetch_models",
                new_callable=AsyncMock,
                return_value=mock_scraped,
            ),
            patch(
                "general_ludd.models.provider_presets.list_configured_providers",
                return_value=["openrouter"],
            ),
            patch(
                "general_ludd.models.provider_presets.detect_credential_alias",
                return_value="OPENROUTER_API_KEY",
            ),
            patch(
                "general_ludd.models.provider_presets.get_provider_preset",
                return_value={"credential_env_var": "OPENROUTER_API_KEY"},
            ),
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/admin/models/discover", params={"provider": "openrouter"})
                assert resp.status_code == 200
                data = resp.json()
                assert data["success"] is True


class TestAdminObservabilityWithSession:
    @pytest.mark.asyncio
    async def test_observability_comparison_with_session(self, app, transport):
        mock_session = MagicMock()
        app.state._session = mock_session
        with patch(
            "general_ludd.observability.comparison.ModelComparison.compare_models",
            new_callable=AsyncMock,
            return_value={"rankings": [{"model": "test"}], "summary": "ok"},
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/admin/observability/comparison")
                assert resp.status_code == 200
                data = resp.json()
                assert "rankings" in data


class TestAdminCodeBlocksStringBody:
    @pytest.mark.asyncio
    async def test_code_blocks_string_body(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/code/blocks",
                content=b'{"source": "def foo(): pass", "language": "python"}',
                headers={"content-type": "application/json"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] >= 1


class TestAdminModelsListWithGateway:
    @pytest.mark.asyncio
    async def test_models_list_with_gateway(self, app, transport):
        from general_ludd.models.gateway import ModelGateway, ModelProfile

        mock_gw = MagicMock(spec=ModelGateway)
        mock_profile = ModelProfile(model_profile_id="test-1", provider="openai", model_name="gpt-4")
        mock_gw.list_profiles.return_value = [mock_profile]
        app.state._model_gateway = mock_gw
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/models")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["profiles"]) == 1
            assert data["profiles"][0]["model_profile_id"] == "test-1"


class TestAdminModelsHealthWithGateway:
    @pytest.mark.asyncio
    async def test_models_health_with_gateway_and_tracker(self, app, transport):
        from general_ludd.models.gateway import ModelGateway
        from general_ludd.models.timeout_detector import ModelHealthTracker

        mock_gw = MagicMock(spec=ModelGateway)
        mock_profile = MagicMock()
        mock_profile.model_profile_id = "test-1"
        mock_gw.list_profiles.return_value = [mock_profile]
        app.state._model_gateway = mock_gw
        app.state._health_tracker = ModelHealthTracker()
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/models/health")
            assert resp.status_code == 200
            data = resp.json()
            assert "health" in data

    @pytest.mark.asyncio
    async def test_models_health_with_tracker_no_gateway(self, app, transport):
        from general_ludd.models.timeout_detector import ModelHealthTracker

        app.state._health_tracker = ModelHealthTracker()
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/models/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["health"] == []


class TestAdminTemplatesListWithRegistry:
    @pytest.mark.asyncio
    async def test_templates_list_with_registry(self, app, transport):
        mock_reg = MagicMock()
        mock_reg.list_templates.return_value = [{"name": "test.tpl"}]
        app.state._prompt_registry = mock_reg
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/templates")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["templates"]) == 1


class TestAdminPlaybooksListWithRunner:
    @pytest.mark.asyncio
    async def test_playbooks_list_with_runner(self, app, transport):
        mock_runner = MagicMock()
        mock_runner.list_playbooks.return_value = [{"name": "noop.yml"}]
        app.state._runner = mock_runner
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/playbooks")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["playbooks"]) == 1


class TestAdminSetProjectWeightError:
    @pytest.mark.asyncio
    async def test_set_project_weight_not_found_raises_422(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/admin/projects/nonexistent/weight",
                json={"weight": 0.5},
            )
            assert resp.status_code == 422


class TestAdminRebalanceProjectsError:
    @pytest.mark.asyncio
    async def test_rebalance_projects_invalid_raises_422(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/projects/rebalance",
                json={"weights": {"nonexistent": 99.0}},
            )
            assert resp.status_code == 422


class TestBenchmarkScoresWithSession:
    @pytest.mark.asyncio
    async def test_benchmark_scores_with_session(self, app, transport):
        mock_session = MagicMock()
        app.state._session = mock_session
        with patch(
            "general_ludd.db.repository.BenchmarkRepository.get_aggregate_scores",
            new_callable=AsyncMock,
            return_value=[],
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/admin/benchmark/scores")
                assert resp.status_code == 200
                data = resp.json()
                assert data["scores"] == []


class TestBenchmarkRecentWithSession:
    @pytest.mark.asyncio
    async def test_benchmark_recent_with_session(self, app, transport):
        mock_session = MagicMock()
        app.state._session = mock_session
        with patch(
            "general_ludd.db.repository.BenchmarkRepository.list_recent",
            new_callable=AsyncMock,
            return_value=[],
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/admin/benchmark/recent")
                assert resp.status_code == 200
                data = resp.json()
                assert data["results"] == []


class TestBenchmarkLeaderboardWithSession:
    @pytest.mark.asyncio
    async def test_benchmark_leaderboard_with_session(self, app, transport):
        mock_session = MagicMock()
        app.state._session = mock_session
        with patch(
            "general_ludd.scoring.router.AdaptiveRouter.get_leaderboard",
            new_callable=AsyncMock,
            return_value=[],
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/admin/benchmark/leaderboard")
                assert resp.status_code == 200
                data = resp.json()
                assert data["leaderboard"] == []


class TestBenchmarkRecordWithSession:
    @pytest.mark.asyncio
    async def test_benchmark_record_with_session(self, app, transport):
        mock_session = MagicMock()
        mock_session.commit = AsyncMock()
        mock_sf = MagicMock()
        mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
        app.state._session_factory = mock_sf
        mock_row = MagicMock()
        mock_row.id = "rec-1"
        mock_row.success = True
        with patch(
            "general_ludd.db.repository.BenchmarkRepository.record_result",
            new_callable=AsyncMock,
            return_value=mock_row,
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/admin/benchmark/record",
                    json={"model_profile_id": "test", "task_type": "feature"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["id"] == "rec-1"


class TestPromptProfilesWithSession:
    @pytest.mark.asyncio
    async def test_prompt_profiles_with_session(self, app, transport):
        mock_session = MagicMock()
        app.state._session = mock_session
        with patch(
            "general_ludd.db.repository.PromptProfileRepository.list_all",
            new_callable=AsyncMock,
            return_value=[],
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/admin/prompt-profiles")
                assert resp.status_code == 200
                data = resp.json()
                assert data["profiles"] == []


class TestQuantizationListWithTracker:
    @pytest.mark.asyncio
    async def test_quantization_list_with_tracker(self, app, transport):
        from general_ludd.models.quantization import Precision, QuantizationInfo, QuantizationTracker

        tracker = QuantizationTracker()
        tracker.update("m1", QuantizationInfo(precision=Precision.FP16.value, source="test", confidence=0.9))
        app.state._quantization_tracker = tracker
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/quantization")
            assert resp.status_code == 200
            data = resp.json()
            assert "models" in data
            assert len(data["models"]) > 0


class TestQuantizationDetectWithResults:
    @pytest.mark.asyncio
    async def test_quantization_detect_returns_results(self, app, transport):
        from general_ludd.models.quantization import QuantizationInfo

        with (
            patch(
                "general_ludd.models.quantization.HuggingFaceDetector.detect",
                new_callable=AsyncMock,
                return_value=[QuantizationInfo(precision="fp16", source="hf", confidence=0.8)],
            ),
            patch(
                "general_ludd.models.quantization.FireworksDetector.detect",
                new_callable=AsyncMock,
                return_value=[QuantizationInfo(precision="int8", source="fw", confidence=0.7)],
            ),
            patch(
                "general_ludd.models.quantization.OpenRouterEndpointDetector.detect",
                new_callable=AsyncMock,
                return_value=[QuantizationInfo(precision="fp32", source="or", confidence=0.9)],
            ),
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/admin/quantization/detect",
                    json={"model_id": "test-model"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["model_id"] == "test-model"
                assert data["sources_checked"] == 3
                assert data["best"] is not None


class TestQuantizationGetWithKnownModel:
    @pytest.mark.asyncio
    async def test_quantization_get_known_model(self, app, transport):
        from general_ludd.models.quantization import Precision, QuantizationInfo, QuantizationTracker

        tracker = QuantizationTracker()
        tracker.update(
            "known-model",
            QuantizationInfo(
                precision=Precision.INT8.value,
                source="test",
                confidence=0.95,
                provider_name="fireworks",
                bits_estimate=8,
            ),
        )
        app.state._quantization_tracker = tracker
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/quantization/known-model")
            assert resp.status_code == 200
            data = resp.json()
            assert data["precision"] == "int8"
            assert data["provider_name"] == "fireworks"


class TestQuantizationDriftCheckWithTracker:
    @pytest.mark.asyncio
    async def test_quantization_drift_check_with_data(self, app, transport):
        from general_ludd.models.quantization import Precision, QuantizationInfo, QuantizationTracker

        tracker = QuantizationTracker()
        tracker.update("m1", QuantizationInfo(precision=Precision.FP16.value, source="test", confidence=0.9))
        app.state._quantization_tracker = tracker
        with (
            patch(
                "general_ludd.models.quantization.HuggingFaceDetector.detect",
                new_callable=AsyncMock,
                return_value=[QuantizationInfo(precision="fp16", source="hf", confidence=0.9)],
            ),
            patch(
                "general_ludd.models.quantization.OpenRouterEndpointDetector.detect",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/admin/quantization/drift-check")
                assert resp.status_code == 200
                data = resp.json()
                assert "models_checked" in data


class TestWorktreeStatusWithMonitor:
    @pytest.mark.asyncio
    async def test_worktree_status_with_monitor(self, app, transport):
        from general_ludd.worktree import WorktreeMonitor

        mock_monitor = MagicMock()
        mock_monitor.__class__ = WorktreeMonitor
        mock_wt = MagicMock()
        mock_wt.path = "/tmp/test-wt"
        mock_wt.todo_id = "todo-1"
        mock_wt.agents_md = None
        mock_wt.last_scanned = None
        mock_wt.last_activity = None
        mock_monitor.tracked_worktrees = {"wt-1": mock_wt}
        from general_ludd.daemon import _get_or_create_extended_subsystems

        with patch(
            "general_ludd.routers.worktree._get_or_create_extended_subsystems",
            return_value={**_get_or_create_extended_subsystems(app), "worktree_monitor": mock_monitor},
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/admin/worktree/status")
                assert resp.status_code == 200
                data = resp.json()
                assert data["tracked_count"] == 1
                assert data["tracked_worktrees"][0]["path"] == "/tmp/test-wt"


class TestFilestoreReadDir:
    @pytest.mark.asyncio
    async def test_filestore_read_directory(self, app, transport, tmp_path):
        from general_ludd.filestore.store import FileStore

        test_dir = tmp_path / "fs_test_read"
        test_dir.mkdir()
        (test_dir / "subfile.txt").write_text("hello")
        with (
            patch.object(FileStore, "root_path", str(test_dir)),
            patch.object(FileStore, "exists", return_value=True),
            patch.object(FileStore, "is_dir", return_value=True),
            patch.object(FileStore, "list_dir", return_value=["subfile.txt"]),
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/admin/filestore/read", params={"path": "subdir"})
                assert resp.status_code == 200
                data = resp.json()
                assert data.get("is_dir") is True


class TestFilestoreReadBinary:
    @pytest.mark.asyncio
    async def test_filestore_read_binary_fallback(self, app, transport):
        with (
            patch("general_ludd.security.sanitize.sanitize_path", return_value="binary.bin"),
            patch("general_ludd.filestore.store.FileStore.exists", return_value=True),
            patch("general_ludd.filestore.store.FileStore.is_dir", return_value=False),
            patch(
                "general_ludd.filestore.store.FileStore.read_text",
                side_effect=UnicodeDecodeError("utf-8", b"\x00", 0, 1, "invalid"),
            ),
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/admin/filestore/read", params={"path": "binary.bin"})
                assert resp.status_code == 200
                data = resp.json()
                assert data.get("binary") is True


class TestFilestoreRemoveExisting:
    @pytest.mark.asyncio
    async def test_filestore_remove_existing_file(self, app, transport):
        with (
            patch("general_ludd.security.sanitize.sanitize_path", return_value="test.txt"),
            patch("general_ludd.filestore.store.FileStore.exists", return_value=True),
            patch("general_ludd.filestore.store.FileStore.remove") as mock_rm,
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete("/admin/filestore/remove", params={"path": "test.txt"})
                assert resp.status_code == 200
                data = resp.json()
                assert data["success"] is True
                mock_rm.assert_called_once()


class TestAdminSelftest:
    @pytest.mark.asyncio
    async def test_selftest_no_molecule_dir(self, transport):
        # Hermetic: this test asserts the "no molecule dir" fast path, so the
        # molecule-dir probe MUST be forced False. Without this the endpoint
        # finds the repo's real molecule/playbooks and shells out to a real
        # `uv run molecule test` per scenario (each timeout=300) — a non-hermetic
        # fan-out that blows the 180s per-test timeout under load. Forcing the
        # probe False exercises the empty path with zero subprocesses.
        import os.path as _ospath

        real_isdir = _ospath.isdir

        def _no_molecule(path):
            return False if "molecule" in str(path) else real_isdir(path)

        with patch("os.path.isdir", side_effect=_no_molecule):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/admin/selftest")
                assert resp.status_code == 200
                data = resp.json()
                assert "scenarios_run" in data
                assert data["scenarios_run"] == 0
                assert data["results"] == []
                assert data["errors"] == []


class TestDispatchModeEndpoint:
    @pytest.mark.asyncio
    async def test_dispatch_mode_invalid(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/admin/dispatch/mode",
                json={"mode": "invalid_mode"},
            )
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_dispatch_mode_valid_sets_config(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/admin/dispatch/mode",
                json={"mode": "passive_external"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["dispatch_mode"] == "passive_external"


class TestSigningEndpointsNoResolver:
    ADMIN_HEADERS: ClassVar[dict[str, str]] = {"X-Admin-Token": "test-admin-token"}

    @pytest.fixture(autouse=True)
    def _configure_admin_token(self, monkeypatch):
        monkeypatch.setenv("GLUDD_ADMIN_TOKEN", "test-admin-token")

    @pytest.mark.asyncio
    async def test_cosign_generate_no_resolver(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/signing/cosign/generate",
                json={},
                headers=self.ADMIN_HEADERS,
            )
            assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_cosign_list_no_resolver(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/admin/signing/cosign/list/default",
                headers=self.ADMIN_HEADERS,
            )
            assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_cosign_read_no_resolver(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/admin/signing/cosign/default/test-key",
                headers=self.ADMIN_HEADERS,
            )
            assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_cosign_delete_no_resolver(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                "/admin/signing/cosign/default/test-key",
                headers=self.ADMIN_HEADERS,
            )
            assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_gitsign_write_no_resolver(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/signing/gitsign/config",
                json={},
                headers=self.ADMIN_HEADERS,
            )
            assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_gitsign_read_no_resolver(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/admin/signing/gitsign/default",
                headers=self.ADMIN_HEADERS,
            )
            assert resp.status_code == 503
