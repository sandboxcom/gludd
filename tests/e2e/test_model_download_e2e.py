"""E2E: Model download, local serving, and local/cloud cost routing.

Tests the daemon endpoints for downloading models, serving them locally,
listing downloaded models, and routing between local and cloud profiles.

Network-dependent tests are marked with ``@pytest.mark.network``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from general_ludd.daemon import create_daemon_app

# ── CI-safe small model for download tests ────────────────────────────────────
_CI_SAFE_MODEL_REPO = "HuggingFaceFW/fineweb-edu-classifier"
_CI_SAFE_MODEL_REPO_GGUF = "ggml-org/models"
_CI_SAFE_MODEL_GGUF_FILE = "tinyllamas/stories15M-q4_0.gguf"


def _has_huggingface_hub() -> bool:
    try:
        import huggingface_hub  # noqa: F401

        return True
    except ImportError:
        return False


def _hf_token() -> str | None:
    return os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")


_HF_AVAILABLE = _has_huggingface_hub()
_HF_TOKEN = _hf_token()


# ── FastAPI app fixture ───────────────────────────────────────────────────────


@pytest.fixture
def app():
    return create_daemon_app(tick_interval=0.0)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ── 1. Model download via daemon endpoint ─────────────────────────────────────


@pytest.mark.network
class TestModelDownloadEndpoint:
    """Test downloading models via POST /admin/models/local/download."""

    def test_download_huggingface_ci_safe_model(self, client):
        if not _HF_AVAILABLE or not _HF_TOKEN:
            pytest.skip("HuggingFace Hub not available (no hfh or no HF_TOKEN)")

        resp = client.post(
            "/admin/models/local/download",
            json={
                "model_id": _CI_SAFE_MODEL_REPO,
                "source": "huggingface",
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["downloaded"] is True
        assert data["model_id"] == _CI_SAFE_MODEL_REPO
        assert data["source"] == "huggingface"
        assert "local_path" in data
        assert data["size_bytes"] > 0

    def test_download_with_filename_gguf(self, client):
        if not _HF_AVAILABLE or not _HF_TOKEN:
            pytest.skip("HuggingFace Hub not available (no hfh or no HF_TOKEN)")

        resp = client.post(
            "/admin/models/local/download",
            json={
                "model_id": _CI_SAFE_MODEL_REPO_GGUF,
                "source": "huggingface",
                "filename": _CI_SAFE_MODEL_GGUF_FILE,
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["downloaded"] is True
        assert data["model_id"] == _CI_SAFE_MODEL_REPO_GGUF

    def test_download_missing_model_id_returns_422(self, client):
        resp = client.post(
            "/admin/models/local/download",
            json={"source": "huggingface"},
        )
        assert resp.status_code == 422

    def test_download_invalid_source_returns_422(self, client):
        resp = client.post(
            "/admin/models/local/download",
            json={
                "model_id": "some/model",
                "source": "s3",
            },
        )
        assert resp.status_code == 422


# ── 2. Download source selection ──────────────────────────────────────────────


@pytest.mark.network
class TestDownloadSourceSelection:
    """Test source parameter selects the correct download backend."""

    def test_source_huggingface_is_default(self, client):
        if not _HF_AVAILABLE or not _HF_TOKEN:
            pytest.skip("HuggingFace Hub not available")

        resp = client.post(
            "/admin/models/local/download",
            json={
                "model_id": _CI_SAFE_MODEL_REPO,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["source"] == "huggingface"

    def test_source_explicit_huggingface_sets_correct_source(self, client):
        if not _HF_AVAILABLE or not _HF_TOKEN:
            pytest.skip("HuggingFace Hub not available")

        resp = client.post(
            "/admin/models/local/download",
            json={
                "model_id": _CI_SAFE_MODEL_REPO,
                "source": "huggingface",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["source"] == "huggingface"

    def test_source_local_returns_cache_source(self, client):
        with tempfile.TemporaryDirectory() as tmpdir:
            model_file = os.path.join(tmpdir, "fake-model.gguf")
            Path(model_file).write_bytes(b"fake gguf content\0" * 100)
            resp = client.post(
                "/admin/models/local/download",
                json={
                    "model_id": "local-test-model",
                    "source": "local",
                    "model_path": model_file,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["source"] == "local"
            assert data["local_path"] == model_file
            assert data["size_bytes"] > 0


# ── 3. Downloaded model listing ───────────────────────────────────────────────


@pytest.mark.network
class TestDownloadedModelListing:
    """Test GET /admin/models/downloaded returns downloaded models."""

    def test_list_downloaded_after_download(self, client):
        if not _HF_AVAILABLE or not _HF_TOKEN:
            pytest.skip("HuggingFace Hub not available")

        download_resp = client.post(
            "/admin/models/local/download",
            json={
                "model_id": _CI_SAFE_MODEL_REPO,
                "source": "huggingface",
            },
        )
        assert download_resp.status_code == 200

        list_resp = client.get("/admin/models/downloaded")
        assert list_resp.status_code == 200
        data = list_resp.json()
        assert "models" in data
        model_ids = [m["model_id"] for m in data["models"]]
        assert _CI_SAFE_MODEL_REPO in model_ids

    def test_list_downloaded_empty_returns_empty_list(self, client):
        list_resp = client.get("/admin/models/downloaded")
        assert list_resp.status_code == 200
        assert "models" in list_resp.json()


# ── 4. Model serving (start/stop) ─────────────────────────────────────────────


class TestModelServe:
    """Test local model serving lifecycle via daemon endpoints."""

    def test_serve_valid_request_creates_server(self, client):
        resp = client.post(
            "/admin/models/local/serve",
            json={
                "model_id": "test-serve-model",
                "model_path": "/tmp/test-serve-model.gguf",
                "engine": "llamacpp",
                "port": 18080,
                "gpu_layers": 0,
                "context_size": 2048,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "server_id" in data
        assert data["model_id"] == "test-serve-model"
        assert data["engine"] == "llamacpp"
        assert "endpoint_url" in data

    def test_serve_missing_model_id_returns_422(self, client):
        resp = client.post(
            "/admin/models/local/serve",
            json={"port": 18081},
        )
        assert resp.status_code == 422

    def test_serve_invalid_port_returns_422(self, client):
        resp = client.post(
            "/admin/models/local/serve",
            json={
                "model_id": "test-model",
                "port": 80,
            },
        )
        assert resp.status_code == 422

    def test_serve_port_too_high_returns_422(self, client):
        resp = client.post(
            "/admin/models/local/serve",
            json={
                "model_id": "test-model",
                "port": 99999,
            },
        )
        assert resp.status_code == 422

    def test_local_status_returns_server_list(self, client):
        client.post(
            "/admin/models/local/serve",
            json={
                "model_id": "status-test-model",
                "model_path": "/tmp/status-test.gguf",
                "engine": "llamacpp",
                "port": 18082,
            },
        )
        resp = client.get("/admin/models/local/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "servers" in data
        assert "total" in data
        assert data["total"] >= 1

    def test_shutdown_existing_server(self, client):
        create_resp = client.post(
            "/admin/models/local/serve",
            json={
                "model_id": "shutdown-test-model",
                "model_path": "/tmp/shutdown-test.gguf",
                "engine": "llamacpp",
                "port": 18083,
            },
        )
        assert create_resp.status_code == 200
        server_id = create_resp.json()["server_id"]

        shutdown_resp = client.post(
            "/admin/models/local/shutdown",
            json={"server_id": server_id},
        )
        assert shutdown_resp.status_code == 200
        data = shutdown_resp.json()
        assert data["shutdown"] is True
        assert data["server_id"] == server_id

    def test_shutdown_nonexistent_server_returns_404(self, client):
        # Contract aligned with test_local_serve_e2e.py: unknown/retired
        # server IDs 404 (the route checks existence before stopping).
        resp = client.post(
            "/admin/models/local/shutdown",
            json={"server_id": "nonexistent-server-id"},
        )
        assert resp.status_code == 404

    def test_shutdown_missing_server_id_returns_422(self, client):
        resp = client.post(
            "/admin/models/local/shutdown",
            json={},
        )
        assert resp.status_code == 422


# ── 5. Cost routing between local and cloud models ────────────────────────────


class TestCostRouting:
    """Test cost-aware routing with PeakPricingSchedule and budget guards."""

    def test_peak_schedule_validates_hours(self):
        from general_ludd.models.cost_router import PeakPricingSchedule

        ps = PeakPricingSchedule(
            peak_start_hour=8,
            peak_end_hour=20,
            peak_multiplier=1.5,
            off_peak_multiplier=0.7,
        )
        assert ps.peak_start_hour == 8
        assert ps.peak_end_hour == 20
        assert ps.peak_multiplier == 1.5
        assert ps.off_peak_multiplier == 0.7

    def test_peak_schedule_rejects_invalid_hours(self):
        from general_ludd.models.cost_router import PeakPricingSchedule

        with pytest.raises(ValueError):
            PeakPricingSchedule(peak_start_hour=-1, peak_end_hour=20)
        with pytest.raises(ValueError):
            PeakPricingSchedule(peak_start_hour=8, peak_end_hour=24)

    def test_peak_schedule_rejects_invalid_multipliers(self):
        from general_ludd.models.cost_router import PeakPricingSchedule

        with pytest.raises(ValueError):
            PeakPricingSchedule(peak_start_hour=8, peak_end_hour=20, peak_multiplier=0)
        with pytest.raises(ValueError):
            PeakPricingSchedule(peak_start_hour=8, peak_end_hour=20, off_peak_multiplier=-1)

    def test_peak_schedule_rejects_empty_days(self):
        from general_ludd.models.cost_router import PeakPricingSchedule

        with pytest.raises(ValueError):
            PeakPricingSchedule(peak_start_hour=8, peak_end_hour=20, peak_days=frozenset())

    def test_model_route_is_immutable(self):
        from general_ludd.models.cost_router import _PEAK, ModelRoute

        route = ModelRoute(
            model_id="test/model",
            estimated_cost=0.01,
            peak_status=_PEAK,
            hourly_rate=0.01,
        )
        assert route.model_id == "test/model"
        assert route.currency == "USD"

    def test_budget_guard_exhaustion_propagates(self):
        from general_ludd.controllers.budget import RunBudgetGuard

        guard = RunBudgetGuard(run_budget_usd=5.0)
        guard.record_spend(6.0)
        result = guard.check_run_budget()
        assert result["allowed"] is False
        assert "run budget" in str(result["reason"]).lower()

    def test_wall_clock_timeout_expired(self):
        from general_ludd.controllers.budget import RunBudgetGuard

        guard = RunBudgetGuard(run_timeout_seconds=-1.0)
        result = guard.check_wall_clock()
        assert result["allowed"] is False
        assert "timeout" in str(result["reason"]).lower()

    def test_cost_router_default_schedule_is_peak(self):
        from general_ludd.models.cost_router import (
            _DEFAULT_PEAK,
        )

        assert _DEFAULT_PEAK.peak_start_hour == 8
        assert _DEFAULT_PEAK.peak_end_hour == 20
        assert _DEFAULT_PEAK.peak_multiplier == 1.5
        assert _DEFAULT_PEAK.off_peak_multiplier == 0.7


# ── 6. Full pipeline: download → serve → call → shutdown ──────────────────────


class TestFullPipelineMock:
    """Full pipeline integration test using mocked model download/call.

    Exercises the daemon endpoints end-to-end without real model execution.
    """

    def test_pipeline_download_serve_shutdown_via_endpoints(self, client):
        with tempfile.TemporaryDirectory() as tmpdir:
            model_file = os.path.join(tmpdir, "pipeline-test.gguf")
            Path(model_file).write_bytes(b"mock gguf model data\0" * 100)

            download_resp = client.post(
                "/admin/models/local/download",
                json={
                    "model_id": "pipeline-e2e-model",
                    "source": "local",
                    "model_path": model_file,
                },
            )
            assert download_resp.status_code == 200
            assert download_resp.json()["downloaded"] is True

            serve_resp = client.post(
                "/admin/models/local/serve",
                json={
                    "model_id": "pipeline-e2e-model",
                    "model_path": model_file,
                    "engine": "llamacpp",
                    "port": 18084,
                },
            )
            assert serve_resp.status_code == 200
            server_id = serve_resp.json()["server_id"]

            status_resp = client.get("/admin/models/local/status")
            assert status_resp.status_code == 200
            server_ids = [s["server_id"] for s in status_resp.json()["servers"]]
            assert server_id in server_ids

            shutdown_resp = client.post(
                "/admin/models/local/shutdown",
                json={"server_id": server_id},
            )
            assert shutdown_resp.status_code == 200
            assert shutdown_resp.json()["shutdown"] is True

    def test_model_call_endpoint_with_profile(self, client):
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.provider_registry import ProviderRegistry

        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

        profile = ModelProfile(
            model_profile_id="e2e-call-profile",
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-3.5-turbo",
            enabled=True,
            # Zero per-token cost is only valid for non-metered profiles;
            # the validator rejects zero-cost api_metered profiles.
            api_metered=False,
            cost_per_input_token=0.0,
            cost_per_output_token=0.0,
        )

        fake_chat = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.return_value = MagicMock(
            content="mock response from e2e",
            usage_metadata={"input_tokens": 5, "output_tokens": 5},
        )
        fake_chat.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=fake_chat),
        ):
            gw = ModelGateway(
                profiles=[profile],
                provider_registry=reg,
            )

            resp = gw.call_model(
                "e2e-call-profile",
                [{"role": "user", "content": "hello"}],
            )
            assert resp.content == "mock response from e2e"

    @pytest.mark.network
    def test_model_registry_download_and_lifecycle(self):
        if not _HF_AVAILABLE or not _HF_TOKEN:
            pytest.skip("HuggingFace Hub not available (no hfh or no HF_TOKEN)")

        from general_ludd.models.model_registry import ModelRegistry

        registry = ModelRegistry()
        downloaded = registry.download(
            model_id=_CI_SAFE_MODEL_REPO,
        )
        assert downloaded is not None
        assert downloaded.model_id == _CI_SAFE_MODEL_REPO

        models = registry.list_downloaded()
        assert len(models) >= 1
        found = [m for m in models if m.model_id == _CI_SAFE_MODEL_REPO]
        assert len(found) == 1

        fetched = registry.get_downloaded(_CI_SAFE_MODEL_REPO)
        assert fetched is not None
        assert fetched.model_id == _CI_SAFE_MODEL_REPO

        registry.remove_downloaded(_CI_SAFE_MODEL_REPO)
        assert registry.get_downloaded(_CI_SAFE_MODEL_REPO) is None
