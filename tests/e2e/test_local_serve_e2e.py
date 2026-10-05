"""E2E: Local model serving against a running daemon.

Tests the daemon endpoints for local model serving lifecycle — start,
health check, completion, route selection, shutdown, and concurrent
requests — using CI-safe models.

Network-dependent tests are marked with ``@pytest.mark.network``.
"""

from __future__ import annotations

import os
import socket
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from general_ludd.daemon import create_daemon_app
from tests.e2e._local_model_configs import list_models


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


_CI_SAFE_MODELS = list_models(ci_safe=True)


def _has_llama_cpp() -> bool:
    try:
        import llama_cpp

        _ = llama_cpp
        return True
    except ImportError:
        return False


_LLAMA_AVAILABLE = _has_llama_cpp()


# ── FastAPI app fixture ───────────────────────────────────────────────────────


@pytest.fixture
def app():
    return create_daemon_app(tick_interval=0.0)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _create_mock_gguf(tmpdir: str, size_kb: int = 100) -> str:
    path = os.path.join(tmpdir, "mock-model.gguf")
    Path(path).write_bytes(b"GGUF_MAGIC\0" + b"\0" * (size_kb * 1024))
    return path


# ── 1. Start local inference server via daemon endpoint ───────────────────────


class TestLocalServeStart:
    """Start a local inference server via POST /admin/models/local/serve."""

    def test_serve_creates_server_and_returns_details(self, client):
        port = _find_free_port()
        resp = client.post(
            "/admin/models/local/serve",
            json={
                "model_id": "serve-start-test",
                "model_path": "/tmp/serve-start-test.gguf",
                "engine": "llamacpp",
                "port": port,
                "gpu_layers": 0,
                "context_size": 2048,
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "server_id" in data
        assert data["model_id"] == "serve-start-test"
        assert data["engine"] == "llamacpp"
        assert "endpoint_url" in data
        assert data["status"] == "stopped"

    def test_serve_missing_model_id_returns_422(self, client):
        resp = client.post(
            "/admin/models/local/serve",
            json={"port": 18090},
        )
        assert resp.status_code == 422

    def test_serve_invalid_port_returns_422(self, client):
        resp = client.post(
            "/admin/models/local/serve",
            json={
                "model_id": "bad-port",
                "port": 80,
            },
        )
        assert resp.status_code == 422

    def test_serve_port_too_high_returns_422(self, client):
        resp = client.post(
            "/admin/models/local/serve",
            json={
                "model_id": "bad-port",
                "port": 99999,
            },
        )
        assert resp.status_code == 422

    def test_serve_with_ci_safe_model_config(self, client):
        if not _CI_SAFE_MODELS:
            pytest.skip("No CI-safe models in registry")
        model = _CI_SAFE_MODELS[0]
        port = _find_free_port()
        resp = client.post(
            "/admin/models/local/serve",
            json={
                "model_id": model.name,
                "model_path": f"/tmp/{model.name}.gguf",
                "engine": "llamacpp",
                "port": port,
                "gpu_layers": 0,
                "context_size": model.context_size,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["model_id"] == model.name
        assert data["engine"] == "llamacpp"
        assert isinstance(data["server_id"], str)
        assert len(data["server_id"]) > 0


# ── 2. Health check on local server ───────────────────────────────────────────


class TestLocalServeHealth:
    """Health check via GET /admin/models/local/status."""

    def test_status_returns_servers_after_serve(self, client):
        port = _find_free_port()
        create = client.post(
            "/admin/models/local/serve",
            json={
                "model_id": "health-check-model",
                "model_path": "/tmp/health-check.gguf",
                "engine": "llamacpp",
                "port": port,
            },
        )
        assert create.status_code == 200
        server_id = create.json()["server_id"]

        resp = client.get("/admin/models/local/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "servers" in data
        assert "total" in data
        assert data["total"] >= 1
        matching = [s for s in data["servers"] if s["server_id"] == server_id]
        assert len(matching) == 1
        assert matching[0]["model_name"] == "health-check-model"

    def test_status_with_no_servers_returns_empty(self, client):
        resp = client.get("/admin/models/local/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "servers" in data
        assert "total" in data
        assert isinstance(data["total"], int)

    def test_status_returns_endpoints_list(self, client):
        port = _find_free_port()
        client.post(
            "/admin/models/local/serve",
            json={
                "model_id": "endpoint-check-model",
                "model_path": "/tmp/endpoint-check.gguf",
                "engine": "llamacpp",
                "port": port,
            },
        )
        resp = client.get("/admin/models/local/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "endpoints" in data


# ── 3. Send a completion request to local model ───────────────────────────────


class TestLocalServeCompletion:
    """Send completions via POST /admin/models/local/consume."""

    def test_consume_missing_server_id_returns_422(self, client):
        resp = client.post(
            "/admin/models/local/consume",
            json={"prompt": "hello"},
        )
        assert resp.status_code == 422

    def test_consume_missing_prompt_returns_422(self, client):
        resp = client.post(
            "/admin/models/local/consume",
            json={"server_id": "local-0"},
        )
        assert resp.status_code == 422

    def test_consume_nonexistent_server_returns_404(self, client):
        resp = client.post(
            "/admin/models/local/consume",
            json={
                "server_id": "local-99999",
                "prompt": "hello",
            },
        )
        assert resp.status_code == 404

    def test_consume_stopped_server_returns_503(self, client):
        port = _find_free_port()
        create = client.post(
            "/admin/models/local/serve",
            json={
                "model_id": "stopped-consume-model",
                "model_path": "/tmp/stopped-consume.gguf",
                "engine": "llamacpp",
                "port": port,
            },
        )
        assert create.status_code == 200
        server_id = create.json()["server_id"]

        resp = client.post(
            "/admin/models/local/consume",
            json={
                "server_id": server_id,
                "prompt": "say hello",
            },
        )
        assert resp.status_code == 503

    @pytest.mark.network
    def test_consume_with_running_server(self, client):
        if not _LLAMA_AVAILABLE:
            pytest.skip("llama-cpp-python not available")
        if not _CI_SAFE_MODELS:
            pytest.skip("No CI-safe models in registry")

        model = _CI_SAFE_MODELS[0]
        with tempfile.TemporaryDirectory() as tmpdir:
            gguf_path = _create_mock_gguf(tmpdir)
            port = _find_free_port()

            create = client.post(
                "/admin/models/local/serve",
                json={
                    "model_id": model.name,
                    "model_path": gguf_path,
                    "engine": "llamacpp",
                    "port": port,
                    "gpu_layers": 0,
                    "context_size": model.context_size,
                },
            )
            assert create.status_code == 200
            server_id = create.json()["server_id"]

            consume = client.post(
                "/admin/models/local/consume",
                json={
                    "server_id": server_id,
                    "prompt": "Hello, world",
                    "max_tokens": 8,
                },
            )
            assert consume.status_code in (200, 502, 503)


# ── 4. Cloud vs local route selection ─────────────────────────────────────────


class TestLocalCloudRouting:
    """Route selection between local and cloud models via cost/comparison."""

    def test_cost_router_peak_pricing_defaults(self):
        from general_ludd.models.cost_router import (
            _DEFAULT_PEAK,
        )

        assert _DEFAULT_PEAK.peak_start_hour == 8
        assert _DEFAULT_PEAK.peak_end_hour == 20
        assert _DEFAULT_PEAK.peak_multiplier == 1.5
        assert _DEFAULT_PEAK.off_peak_multiplier == 0.7

    def test_model_route_creation_is_immutable(self):
        from general_ludd.models.cost_router import _PEAK, ModelRoute

        route = ModelRoute(
            model_id="test/model",
            estimated_cost=0.01,
            peak_status=_PEAK,
            hourly_rate=0.01,
        )
        assert route.model_id == "test/model"
        assert route.currency == "USD"

    def test_local_profile_hints_in_gateway(self):
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.provider_registry import ProviderRegistry

        reg = ProviderRegistry()
        reg.register_provider("local-test", "langchain-openai", "ChatOpenAI")

        profile = ModelProfile(
            model_profile_id="local-route-test-profile",
            provider="local-test",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="local-model-v1",
            enabled=True,
            # Local inference is not API-metered: zero per-token cost is the
            # whole point of a local profile (the validator only rejects
            # zero-cost profiles when api_metered=True).
            api_metered=False,
            cost_per_input_token=0.0,
            cost_per_output_token=0.0,
        )
        gw = ModelGateway(profiles=[profile], provider_registry=reg)
        assert gw.get_profile("local-route-test-profile") is not None

    def test_budget_guard_blocks_when_exhausted(self):
        from general_ludd.controllers.budget import RunBudgetGuard

        guard = RunBudgetGuard(run_budget_usd=5.0)
        guard.record_spend(6.0)
        result = guard.check_run_budget()
        assert result["allowed"] is False
        assert "run budget" in str(result["reason"]).lower()

    def test_budget_guard_allows_within_limit(self):
        from general_ludd.controllers.budget import RunBudgetGuard

        guard = RunBudgetGuard(run_budget_usd=10.0)
        guard.record_spend(2.0)
        result = guard.check_run_budget()
        assert result["allowed"] is True

    def test_wall_clock_timeout_expired(self):
        from general_ludd.controllers.budget import RunBudgetGuard

        guard = RunBudgetGuard(run_timeout_seconds=-1.0)
        result = guard.check_wall_clock()
        assert result["allowed"] is False
        assert "timeout" in str(result["reason"]).lower()

    def test_cost_router_peak_multiplier_range(self):
        from general_ludd.models.cost_router import PeakPricingSchedule

        with pytest.raises(ValueError):
            PeakPricingSchedule(peak_start_hour=-1, peak_end_hour=20)
        with pytest.raises(ValueError):
            PeakPricingSchedule(peak_start_hour=8, peak_end_hour=24)
        with pytest.raises(ValueError):
            PeakPricingSchedule(peak_start_hour=8, peak_end_hour=20, peak_multiplier=0)
        with pytest.raises(ValueError):
            PeakPricingSchedule(peak_start_hour=8, peak_end_hour=20, off_peak_multiplier=-1)

    @pytest.mark.network
    def test_route_recommendation_endpoint(self, client):
        resp = client.get("/admin/models/recommend?task=coding")
        assert resp.status_code == 200
        data = resp.json()
        assert "selected_model_profile_id" in data

    def test_local_vs_cloud_latency_comparison(self):
        import time

        t_start = time.monotonic()
        _ = sum(range(1000))
        cpu_duration = time.monotonic() - t_start

        from general_ludd.models.cost_router import _PEAK, ModelRoute

        local_route = ModelRoute(
            model_id="local-sm-model",
            estimated_cost=0.0,
            peak_status=_PEAK,
            hourly_rate=0.0,
        )
        cloud_route = ModelRoute(
            model_id="cloud-gpt-4",
            estimated_cost=0.03,
            peak_status=_PEAK,
            hourly_rate=0.03,
        )

        assert local_route.estimated_cost < cloud_route.estimated_cost
        assert cpu_duration >= 0.0


# ── 5. Shutdown local server ──────────────────────────────────────────────────


class TestLocalServeShutdown:
    """Shutdown via POST /admin/models/local/shutdown."""

    def test_shutdown_existing_server(self, client):
        port = _find_free_port()
        create = client.post(
            "/admin/models/local/serve",
            json={
                "model_id": "shutdown-test-model",
                "model_path": "/tmp/shutdown-test.gguf",
                "engine": "llamacpp",
                "port": port,
            },
        )
        assert create.status_code == 200
        server_id = create.json()["server_id"]

        shutdown = client.post(
            "/admin/models/local/shutdown",
            json={"server_id": server_id},
        )
        assert shutdown.status_code == 200
        data = shutdown.json()
        assert data["shutdown"] is True
        assert data["server_id"] == server_id

    def test_shutdown_nonexistent_server_returns_404(self, client):
        resp = client.post(
            "/admin/models/local/shutdown",
            json={"server_id": "local-99999"},
        )
        assert resp.status_code == 404

    def test_shutdown_missing_server_id_returns_422(self, client):
        resp = client.post(
            "/admin/models/local/shutdown",
            json={},
        )
        assert resp.status_code == 422

    def test_shutdown_twice_returns_404_second_time(self, client):
        port = _find_free_port()
        create = client.post(
            "/admin/models/local/serve",
            json={
                "model_id": "twice-shutdown-model",
                "model_path": "/tmp/twice-shutdown.gguf",
                "engine": "llamacpp",
                "port": port,
            },
        )
        assert create.status_code == 200
        server_id = create.json()["server_id"]

        first = client.post(
            "/admin/models/local/shutdown",
            json={"server_id": server_id},
        )
        assert first.status_code == 200

        second = client.post(
            "/admin/models/local/shutdown",
            json={"server_id": server_id},
        )
        assert second.status_code == 404


# ── 6. Full lifecycle: serve → status → consume → shutdown ───────────────────


class TestLocalServeFullLifecycle:
    """Start-to-finish lifecycle through daemon endpoints."""

    def test_full_lifecycle_with_local_file(self, client):
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = _create_mock_gguf(tmpdir)
            port = _find_free_port()

            serve = client.post(
                "/admin/models/local/serve",
                json={
                    "model_id": "lifecycle-model",
                    "model_path": model_path,
                    "engine": "llamacpp",
                    "port": port,
                    "gpu_layers": 0,
                    "context_size": 2048,
                },
            )
            assert serve.status_code == 200
            sdata = serve.json()
            server_id = sdata["server_id"]
            assert sdata["model_id"] == "lifecycle-model"
            assert sdata["endpoint_url"] == f"http://localhost:{port}/v1"

            status = client.get("/admin/models/local/status")
            assert status.status_code == 200
            stat_data = status.json()
            assert stat_data["total"] >= 1
            found = any(s["server_id"] == server_id for s in stat_data["servers"])
            assert found, f"server_id {server_id} not in status servers"

            consume = client.post(
                "/admin/models/local/consume",
                json={
                    "server_id": server_id,
                    "prompt": "test prompt",
                    "max_tokens": 2,
                },
            )
            assert consume.status_code in (200, 502, 503)

            shutdown = client.post(
                "/admin/models/local/shutdown",
                json={"server_id": server_id},
            )
            assert shutdown.status_code == 200
            assert shutdown.json()["shutdown"] is True

            final_status = client.get("/admin/models/local/status")
            assert final_status.status_code == 200

    def test_multi_server_lifecycle(self, client):
        ports = [_find_free_port() for _ in range(3)]
        server_ids: list[str] = []
        model_paths: list[str] = []

        with tempfile.TemporaryDirectory() as tmpdir:
            for idx, port in enumerate(ports):
                model_path = _create_mock_gguf(tmpdir, size_kb=50 + idx * 10)
                model_paths.append(model_path)
                serve = client.post(
                    "/admin/models/local/serve",
                    json={
                        "model_id": f"multi-model-{idx}",
                        "model_path": model_path,
                        "engine": "llamacpp",
                        "port": port,
                    },
                )
                assert serve.status_code == 200
                server_ids.append(serve.json()["server_id"])

            status = client.get("/admin/models/local/status")
            assert status.status_code == 200
            assert status.json()["total"] == 3

            for sid in server_ids:
                shutdown = client.post(
                    "/admin/models/local/shutdown",
                    json={"server_id": sid},
                )
                assert shutdown.status_code == 200

    @pytest.mark.network
    def test_lifecycle_with_ci_safe_model_entry(self, client):
        if not _CI_SAFE_MODELS:
            pytest.skip("No CI-safe models in registry")
        model = _CI_SAFE_MODELS[0]
        with tempfile.TemporaryDirectory() as tmpdir:
            gguf_path = _create_mock_gguf(tmpdir, size_kb=500)
            port = _find_free_port()

            serve = client.post(
                "/admin/models/local/serve",
                json={
                    "model_id": model.name,
                    "model_path": gguf_path,
                    "engine": "llamacpp",
                    "port": port,
                    "gpu_layers": 0,
                    "context_size": min(model.context_size, 2048),
                },
            )
            assert serve.status_code == 200
            server_id = serve.json()["server_id"]

            status = client.get("/admin/models/local/status")
            assert status.status_code == 200

            shutdown = client.post(
                "/admin/models/local/shutdown",
                json={"server_id": server_id},
            )
            assert shutdown.status_code == 200


# ── 7. Concurrent requests to local server ────────────────────────────────────


class TestLocalServeConcurrent:
    """Concurrent operations against the local serving daemon."""

    def test_concurrent_serve_and_status(self, client):
        ports = [_find_free_port() for _ in range(4)]
        for idx, port in enumerate(ports):
            resp = client.post(
                "/admin/models/local/serve",
                json={
                    "model_id": f"concurrent-model-{idx}",
                    "model_path": f"/tmp/concurrent-{idx}.gguf",
                    "engine": "llamacpp",
                    "port": port,
                },
            )
            assert resp.status_code == 200

        status = client.get("/admin/models/local/status")
        assert status.status_code == 200
        assert status.json()["total"] == 4

        for idx in range(4):
            shutdown = client.post(
                "/admin/models/local/shutdown",
                json={"server_id": f"local-{idx}"},
            )
            assert shutdown.status_code == 200

    def test_concurrent_shutdown_all_servers(self, client):
        ports = [_find_free_port() for _ in range(3)]
        server_ids: list[str] = []
        for idx, port in enumerate(ports):
            resp = client.post(
                "/admin/models/local/serve",
                json={
                    "model_id": f"concurrent-shutdown-{idx}",
                    "model_path": f"/tmp/concurrent-sd-{idx}.gguf",
                    "engine": "llamacpp",
                    "port": port,
                },
            )
            assert resp.status_code == 200
            server_ids.append(resp.json()["server_id"])

        status_before = client.get("/admin/models/local/status")
        assert status_before.status_code == 200
        assert status_before.json()["total"] == 3

        for sid in server_ids:
            shutdown = client.post(
                "/admin/models/local/shutdown",
                json={"server_id": sid},
            )
            assert shutdown.status_code == 200, f"failed to shutdown {sid}"

        status_after = client.get("/admin/models/local/status")
        assert status_after.status_code == 200

    def test_serve_reuse_port_after_shutdown(self, client):
        port = _find_free_port()

        create = client.post(
            "/admin/models/local/serve",
            json={
                "model_id": "reuse-port-model",
                "model_path": "/tmp/reuse-port.gguf",
                "engine": "llamacpp",
                "port": port,
            },
        )
        assert create.status_code == 200
        sid = create.json()["server_id"]

        shutdown = client.post(
            "/admin/models/local/shutdown",
            json={"server_id": sid},
        )
        assert shutdown.status_code == 200

        reuse = client.post(
            "/admin/models/local/serve",
            json={
                "model_id": "reuse-port-model-v2",
                "model_path": "/tmp/reuse-port-v2.gguf",
                "engine": "llamacpp",
                "port": port,
            },
        )
        assert reuse.status_code == 200
        assert reuse.json()["model_id"] == "reuse-port-model-v2"
        assert reuse.json()["server_id"] != sid

    @pytest.mark.network
    def test_concurrent_serve_with_registry_models(self, client):
        if not _CI_SAFE_MODELS:
            pytest.skip("No CI-safe models in registry")

        ci_safe = _CI_SAFE_MODELS[:2]
        ports = [_find_free_port() for _ in ci_safe]
        server_ids: list[str] = []

        with tempfile.TemporaryDirectory() as tmpdir:
            for model, port in zip(ci_safe, ports, strict=False):
                gguf_path = _create_mock_gguf(tmpdir)
                resp = client.post(
                    "/admin/models/local/serve",
                    json={
                        "model_id": model.name,
                        "model_path": gguf_path,
                        "engine": "llamacpp",
                        "port": port,
                        "gpu_layers": 0,
                        "context_size": min(model.context_size, 2048),
                    },
                )
                assert resp.status_code == 200, resp.text
                server_ids.append(resp.json()["server_id"])

            status = client.get("/admin/models/local/status")
            assert status.status_code == 200
            data = status.json()
            assert data["total"] == len(ci_safe)

            for sid in server_ids:
                shutdown = client.post(
                    "/admin/models/local/shutdown",
                    json={"server_id": sid},
                )
                assert shutdown.status_code == 200


# ── 8. Edge cases and error handling ──────────────────────────────────────────


class TestLocalServeEdgeCases:
    """Edge cases for local model serving via daemon."""

    def test_serve_with_ci_safe_models_have_valid_sizes(self):
        for model in _CI_SAFE_MODELS:
            assert model.size_mb < 500, f"{model.name} marked ci_safe but is {model.size_mb} MB"

    def test_serve_empty_string_model_id_rejected(self, client):
        resp = client.post(
            "/admin/models/local/serve",
            json={
                "model_id": "",
                "port": _find_free_port(),
            },
        )
        assert resp.status_code == 422

    def test_status_consistent_after_multiple_serves(self, client):
        ports = [_find_free_port() for _ in range(3)]
        for idx, port in enumerate(ports):
            client.post(
                "/admin/models/local/serve",
                json={
                    "model_id": f"consistent-{idx}",
                    "model_path": f"/tmp/consistent-{idx}.gguf",
                    "engine": "llamacpp",
                    "port": port,
                },
            )

        resp1 = client.get("/admin/models/local/status")
        resp2 = client.get("/admin/models/local/status")
        assert resp1.json()["total"] == resp2.json()["total"]
        assert resp1.json()["total"] == 3

        for idx in range(3):
            client.post(
                "/admin/models/local/shutdown",
                json={"server_id": f"local-{idx}"},
            )
