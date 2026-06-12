"""Tests for uncovered daemon endpoints — models discover, code intel, local inference, worktree, benchmark."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.daemon import create_daemon_app


@pytest.fixture
def app(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    return create_daemon_app(config_dir=str(config_dir))


@pytest.fixture
def transport(app):
    return ASGITransport(app=app)


class TestModelsDiscoverEndpoint:
    @pytest.mark.asyncio
    async def test_models_discover_provider_not_configured(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/models/discover", params={"provider": "nonexistent_provider_xyz"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is False
            assert "not configured" in data["error"]

    @pytest.mark.asyncio
    async def test_models_discover_with_mocked_scraper(self, app, transport):
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
            return_value=None,
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/admin/models/discover", params={"provider": "openrouter"}
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["success"] is True
                assert data["provider"] == "openrouter"
                assert data["discovered_count"] == 1


class TestModelsDiscoveredEndpoint:
    @pytest.mark.asyncio
    async def test_models_discovered_no_profiles(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/models/discovered")
            assert resp.status_code == 200
            data = resp.json()
            assert data["profiles"] == []

    @pytest.mark.asyncio
    async def test_models_discovered_with_profiles(self, app, transport):
        app.state._discovered_profiles = [
            {
                "model_profile_id": "test-1",
                "model_name": "test-model",
                "display_name": "Test Model",
                "cost_per_input_token": 0.0,
                "cost_per_output_token": 0.0,
                "context_window": 4096,
                "is_free": True,
                "role_names": ["coder"],
                "quality_class": "good",
                "enabled": True,
            },
        ]
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/models/discovered")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["profiles"]) == 1
            assert data["profiles"][0]["model_profile_id"] == "test-1"


class TestCodeBlocksEndpoint:
    @pytest.mark.asyncio
    async def test_code_blocks_python(self, transport):
        source = "def foo():\n    pass\nclass Bar:\n    pass\n"
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/code/blocks",
                json={"source": source, "language": "python"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "blocks" in data
            assert data["count"] >= 2


class TestCodeGraphEndpoint:
    @pytest.mark.asyncio
    async def test_code_graph_python(self, transport):
        source = "def a():\n    b()\ndef b():\n    pass\n"
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/admin/code/graph",
                params={"source": source, "language": "python"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "nodes" in data or "edges" in data


class TestCodeSearchEndpoint:
    @pytest.mark.asyncio
    async def test_code_search_function(self, transport):
        source = "def find_user():\n    pass\ndef delete_user():\n    pass\n"
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/admin/code/search",
                params={"source": source, "query": "find", "language": "python"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "results" in data
            assert data["count"] >= 1


class TestLocalInferenceStart:
    @pytest.mark.asyncio
    async def test_local_inference_start(self, app, transport):
        mock_server = MagicMock()
        mock_server.server_id = "test-server"
        mock_server.endpoint_url = "http://localhost:8001/v1"
        mock_server.status = "starting"

        mock_manager = MagicMock()
        mock_manager.create_server.return_value = mock_server
        mock_manager.start_server = AsyncMock()

        with patch(
            "general_ludd.routers.models.LocalInferenceManager",
            return_value=mock_manager,
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/admin/local-inference/start",
                    json={
                        "engine": "vllm",
                        "model_path": "test-model",
                        "model_name": "test-model",
                        "host": "localhost",
                        "port": 8001,
                        "gpu_layers": -1,
                        "context_size": 4096,
                    },
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["server_id"] == "test-server"
                assert data["engine"] == "vllm"


class TestWorktreeEndpoints:
    @pytest.mark.asyncio
    async def test_worktree_scan_no_monitor(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/worktree/scan")
            assert resp.status_code == 200
            data = resp.json()
            assert "todos" in data
            assert "tracked_count" in data

    @pytest.mark.asyncio
    async def test_worktree_scan_with_path(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/worktree/scan",
                params={"watch_paths": "/tmp/test"},
            )
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_worktree_status_no_monitor(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/worktree/status")
            assert resp.status_code == 200
            data = resp.json()
            assert "tracked_worktrees" in data
            assert data["tracked_count"] == 0


class TestQuantizationDetectEndpoint:
    @pytest.mark.asyncio
    async def test_quantization_detect_no_model_id(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/quantization/detect",
                json={"model_id": ""},
            )
            assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_quantization_detect_with_model(self, transport):
        with patch(
            "general_ludd.models.quantization.HuggingFaceDetector.detect",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "general_ludd.models.quantization.FireworksDetector.detect",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "general_ludd.models.quantization.OpenRouterEndpointDetector.detect",
            new_callable=AsyncMock,
            return_value=[],
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/admin/quantization/detect",
                    json={"model_id": "test-model"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["model_id"] == "test-model"
                assert "results" in data

    @pytest.mark.asyncio
    async def test_quantization_get_model(self, app, transport):
        from general_ludd.models.quantization import Precision, QuantizationInfo, QuantizationTracker

        tracker = QuantizationTracker()
        tracker.update("test-model", QuantizationInfo(
            precision=Precision.FP16.value,
            source="test",
            confidence=0.9,
        ))
        app.state._quantization_tracker = tracker

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/quantization/test-model")
            assert resp.status_code == 200
            data = resp.json()
            assert data["model_id"] == "test-model"
            assert data["precision"] == "fp16"

    @pytest.mark.asyncio
    async def test_quantization_drift_check_no_tracker(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/quantization/drift-check")
            assert resp.status_code == 200
            data = resp.json()
            assert data["drift_detected"] is False


class TestBenchmarkEndpointsWithSession:
    @pytest.mark.asyncio
    async def test_benchmark_scores_no_session(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/benchmark/scores")
            assert resp.status_code == 200
            assert resp.json()["scores"] == []

    @pytest.mark.asyncio
    async def test_benchmark_recent_no_session(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/benchmark/recent")
            assert resp.status_code == 200
            assert resp.json()["results"] == []

    @pytest.mark.asyncio
    async def test_benchmark_leaderboard_no_session(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/benchmark/leaderboard")
            assert resp.status_code == 200
            assert resp.json()["leaderboard"] == []

    @pytest.mark.asyncio
    async def test_benchmark_record_no_session(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/benchmark/record",
                json={"model_profile_id": "test", "task_type": "feature"},
            )
            assert resp.status_code == 503


class TestPromptProfilesEndpoint:
    @pytest.mark.asyncio
    async def test_prompt_profiles_no_session(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/prompt-profiles")
            assert resp.status_code == 200
            assert resp.json()["profiles"] == []


class TestObservabilityComparison:
    @pytest.mark.asyncio
    async def test_comparison_no_session(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/observability/comparison")
            assert resp.status_code == 200
            assert resp.json()["rankings"] == []
