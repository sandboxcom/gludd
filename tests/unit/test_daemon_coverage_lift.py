"""Tests for uncovered daemon code paths — targeting lines 97, 123-124, 128-129, 147-160,
 171, 173, 185-188, 197, 204, 211-212, 326-327, 364, 371, 440, 452, 570, 591, 609-611,
 737-743, 808-812, 822, 859-860, 870, 889, 908, 1066-1067, 1075-1076, 1319-1321, 1330-1332,
 1362-1366, 1387-1402, 1411-1413, 1433, 1460-1482, 1513, 1535-1551, 1583-1593, 1619-1626,
 1654-1655, 1702, 1730-1731, 1768-1775, 1920, 1935, 1962, 1971, 1988."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.daemon import _daemon_state, create_daemon_app


@pytest.fixture(autouse=True)
def _preserve_daemon_state():
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
            "general_ludd.secrets.manager.SecretsManager",
            side_effect=Exception("connection refused"),
        ):
            result = build_secrets_resolver(openbao_config=cfg)
            assert hasattr(result, "resolve")

    def test_build_secrets_resolver_openbao_not_reachable(self):
        from general_ludd.daemon import build_secrets_resolver
        from general_ludd.secrets.config import OpenBaoConfig

        cfg = OpenBaoConfig(mode="auto")
        with patch(
            "general_ludd.secrets.manager.SecretsManager",
            side_effect=Exception("connection refused"),
        ):
            result = build_secrets_resolver(openbao_config=cfg)
            assert hasattr(result, "resolve")

    def test_build_secrets_resolver_with_projects(self):
        from general_ludd.daemon import build_secrets_resolver

        result = build_secrets_resolver(projects_active=True)
        assert hasattr(result, "resolve")
        assert hasattr(result, "for_project")

    def test_build_secrets_resolver_projects_resolve_delegates(self):
        from general_ludd.daemon import build_secrets_resolver

        resolver = build_secrets_resolver(
            env_overrides={"TEST_KEY": "test_val"}, projects_active=True
        )
        assert resolver.resolve("TEST_KEY") is not None


class TestInitProjectWorkspaces:
    def test_init_project_workspaces_with_projects(self):
        from general_ludd.daemon import _init_project_workspaces

        mock_pm = MagicMock()
        mock_project = MagicMock()
        mock_project.project_id = "test-proj"
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
            assert len(data["config_files"]) == 2


class TestApiListTodosWithStatusFilter:
    @pytest.mark.asyncio
    async def test_api_todos_status_filter(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/todos", json={
                "title": "Done", "queue": "core", "priority": "high", "work_type": "fix",
            })
            await client.post("/api/todos", json={
                "title": "Pending", "queue": "core", "priority": "medium", "work_type": "code",
            })
        from general_ludd.daemon import _daemon_state

        _daemon_state["todos"][-2]["status"] = "completed"
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/todos", params={"status": "completed"})
            assert resp.status_code == 200
            data = resp.json()
            assert all(t["status"] == "completed" for t in data)


class TestAdminTodosWithProjectIdFilter:
    @pytest.mark.asyncio
    async def test_admin_todos_project_id_filter(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/todos", json={
                "title": "P1", "queue": "core", "priority": "high", "work_type": "fix",
                "project_id": "proj-a",
            })
            await client.post("/api/todos", json={
                "title": "P2", "queue": "core", "priority": "medium", "work_type": "code",
                "project_id": "proj-b",
            })
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
        with patch(
            "general_ludd.models.openrouter_discovery.OpenRouterScraper.fetch_models",
            new_callable=AsyncMock,
            return_value=mock_scraped,
        ), patch(
            "general_ludd.models.provider_presets.list_configured_providers",
            return_value=["openrouter"],
        ), patch(
            "general_ludd.models.provider_presets.detect_credential_alias",
            return_value="OPENROUTER_API_KEY",
        ), patch(
            "general_ludd.models.provider_presets.get_provider_preset",
            return_value={"credential_env_var": "OPENROUTER_API_KEY"},
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/admin/models/discover", params={"provider": "openrouter"}
                )
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
        mock_profile = ModelProfile(
            model_profile_id="test-1", provider="openai", model_name="gpt-4"
        )
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

        with patch(
            "general_ludd.models.quantization.HuggingFaceDetector.detect",
            new_callable=AsyncMock,
            return_value=[QuantizationInfo(precision="fp16", source="hf", confidence=0.8)],
        ), patch(
            "general_ludd.models.quantization.FireworksDetector.detect",
            new_callable=AsyncMock,
            return_value=[QuantizationInfo(precision="int8", source="fw", confidence=0.7)],
        ), patch(
            "general_ludd.models.quantization.OpenRouterEndpointDetector.detect",
            new_callable=AsyncMock,
            return_value=[QuantizationInfo(precision="fp32", source="or", confidence=0.9)],
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
        tracker.update("known-model", QuantizationInfo(
            precision=Precision.INT8.value, source="test", confidence=0.95,
            provider_name="fireworks", bits_estimate=8,
        ))
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
        with patch(
            "general_ludd.models.quantization.HuggingFaceDetector.detect",
            new_callable=AsyncMock,
            return_value=[QuantizationInfo(precision="fp16", source="hf", confidence=0.9)],
        ), patch(
            "general_ludd.models.quantization.OpenRouterEndpointDetector.detect",
            new_callable=AsyncMock,
            return_value=[],
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/admin/quantization/drift-check")
                assert resp.status_code == 200
                data = resp.json()
                assert "models_checked" in data


class TestWorktreeStatusWithMonitor:
    @pytest.mark.asyncio
    async def test_worktree_status_with_monitor(self, app, transport):
        mock_monitor = MagicMock()
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
        with patch.object(FileStore, "root_path", str(test_dir)), \
             patch.object(FileStore, "exists", return_value=True), \
             patch.object(FileStore, "is_dir", return_value=True), \
             patch.object(FileStore, "list_dir", return_value=["subfile.txt"]):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/admin/filestore/read", params={"path": "subdir"})
                assert resp.status_code == 200
                data = resp.json()
                assert data.get("is_dir") is True


class TestFilestoreReadBinary:
    @pytest.mark.asyncio
    async def test_filestore_read_binary_fallback(self, app, transport):
        with patch("general_ludd.security.sanitize.sanitize_path", return_value="binary.bin"), \
             patch("general_ludd.filestore.store.FileStore.exists", return_value=True), \
             patch("general_ludd.filestore.store.FileStore.is_dir", return_value=False), \
             patch(
                 "general_ludd.filestore.store.FileStore.read_text",
                 side_effect=UnicodeDecodeError("utf-8", b"\x00", 0, 1, "invalid"),
             ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/admin/filestore/read", params={"path": "binary.bin"}
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data.get("binary") is True


class TestFilestoreRemoveExisting:
    @pytest.mark.asyncio
    async def test_filestore_remove_existing_file(self, app, transport):
        with patch("general_ludd.security.sanitize.sanitize_path", return_value="test.txt"), \
             patch("general_ludd.filestore.store.FileStore.exists", return_value=True), \
             patch("general_ludd.filestore.store.FileStore.remove") as mock_rm:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete(
                    "/admin/filestore/remove", params={"path": "test.txt"}
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["success"] is True
                mock_rm.assert_called_once()


class TestAdminSelftest:
    @pytest.mark.asyncio
    async def test_selftest_no_molecule_dir(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/selftest")
            assert resp.status_code == 200
            data = resp.json()
            assert "scenarios_run" in data
            assert "results" in data
            assert "errors" in data


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
    @pytest.mark.asyncio
    async def test_cosign_generate_no_resolver(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/signing/cosign/generate", json={})
            assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_cosign_list_no_resolver(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/signing/cosign/list/default")
            assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_cosign_read_no_resolver(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/signing/cosign/default/test-key")
            assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_cosign_delete_no_resolver(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/admin/signing/cosign/default/test-key")
            assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_gitsign_write_no_resolver(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/signing/gitsign/config", json={})
            assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_gitsign_read_no_resolver(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/signing/gitsign/default")
            assert resp.status_code == 503
