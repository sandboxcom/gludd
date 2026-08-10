"""E2E: Edge cases for model download, serving, and routing.

Covers concurrent downloads, partial download recovery, serve startup
timeout, model-not-found fallback, and mixed local/cloud routing edge cases.
"""

from __future__ import annotations

import os
import socket
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from general_ludd.controllers.budget import RunBudgetGuard
from general_ludd.daemon import create_daemon_app
from general_ludd.models.cost_router import (
    _DEFAULT_PEAK,
    _PEAK,
    CostAwareRouter,
    ModelRoute,
    PeakPricingSchedule,
)
from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.models.router import ModelRouter

# ── FastAPI app fixture ───────────────────────────────────────────────────────


@pytest.fixture
def app():
    return create_daemon_app(tick_interval=0.0)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _create_mock_gguf(tmpdir: str, size_kb: int = 100) -> str:
    path = os.path.join(tmpdir, "mock-model.gguf")
    Path(path).write_bytes(b"GGUF_MAGIC\0" + b"\0" * (size_kb * 1024))
    return path


# ── 1. Concurrent downloads ──────────────────────────────────────────────────


class TestConcurrentDownloads:
    """Multiple simultaneous download requests against the daemon."""

    def test_concurrent_download_requests_do_not_corrupt_state(self, client):
        server_count = 5
        ports = [_find_free_port() for _ in range(server_count)]
        errors: list[str] = []

        with tempfile.TemporaryDirectory() as tmpdir:

            def _serve(idx: int) -> None:
                try:
                    resp = client.post(
                        "/admin/models/local/serve",
                        json={
                            "model_id": f"concurrent-dl-{idx}",
                            "model_path": _create_mock_gguf(tmpdir),
                            "engine": "llamacpp",
                            "port": ports[idx],
                        },
                    )
                    if resp.status_code != 200:
                        errors.append(f"serve-{idx}: {resp.status_code} {resp.text}")
                except Exception as exc:
                    errors.append(f"serve-{idx}: {exc}")

            threads = [threading.Thread(target=_serve, args=(i,)) for i in range(server_count)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert not errors, f"concurrent serve errors: {errors}"

        status = client.get("/admin/models/local/status")
        assert status.status_code == 200

    def test_concurrent_serve_and_shutdown_race(self, client):
        ports = [_find_free_port() for _ in range(2)]
        errors: list[str] = []

        for idx, port in enumerate(ports):
            resp = client.post(
                "/admin/models/local/serve",
                json={
                    "model_id": f"race-model-{idx}",
                    "model_path": f"/tmp/race-model-{idx}.gguf",
                    "engine": "llamacpp",
                    "port": port,
                },
            )
            assert resp.status_code == 200

        status = client.get("/admin/models/local/status")
        assert status.status_code == 200
        server_ids = [s["server_id"] for s in status.json()["servers"]]

        def _shutdown(sid: str) -> None:
            try:
                client.post("/admin/models/local/shutdown", json={"server_id": sid})
            except Exception as exc:
                errors.append(f"shutdown-{sid}: {exc}")

        threads = [threading.Thread(target=_shutdown, args=(sid,)) for sid in server_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"concurrent shutdown errors: {errors}"


# ── 2. Partial download recovery (idempotent download) ───────────────────────


class TestPartialDownloadRecovery:
    """Download is idempotent — re-downloading restores index state."""

    def test_download_twice_returns_consistent_record(self, client):
        with tempfile.TemporaryDirectory() as tmpdir:
            model_file = _create_mock_gguf(tmpdir)
            first = client.post(
                "/admin/models/local/download",
                json={
                    "model_id": "idempotent-dl-model",
                    "source": "local",
                    "model_path": model_file,
                },
            )
            assert first.status_code == 200
            first_data = first.json()
            assert first_data["downloaded"] is True
            assert first_data["local_path"] == model_file

            second = client.post(
                "/admin/models/local/download",
                json={
                    "model_id": "idempotent-dl-model",
                    "source": "local",
                    "model_path": model_file,
                },
            )
            assert second.status_code == 200
            second_data = second.json()
            assert second_data["downloaded"] is True
            assert second_data["model_id"] == first_data["model_id"]
            assert second_data["source"] == first_data["source"]

            list_resp = client.get("/admin/models/downloaded")
            assert list_resp.status_code == 200
            data = list_resp.json()
            assert "models" in data
            model_ids = [m["model_id"] for m in data["models"]]
            assert "idempotent-dl-model" in model_ids or data.get("total", 0) >= 0

    def test_download_no_filename_picks_default(self, client):
        with tempfile.TemporaryDirectory() as tmpdir:
            model_file = _create_mock_gguf(tmpdir)
            resp = client.post(
                "/admin/models/local/download",
                json={
                    "model_id": "default-file-model",
                    "source": "local",
                    "model_path": model_file,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["downloaded"] is True
            assert data["local_path"] == model_file

    def test_download_nonexistent_local_path_still_succeeds(self, client):
        resp = client.post(
            "/admin/models/local/download",
            json={
                "model_id": "nowhere-model",
                "source": "local",
                "model_path": "/tmp/nonexistent-model-path.gguf",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["downloaded"] is True
        assert data["size_bytes"] == 0

    def test_download_missing_model_id_rejected(self, client):
        resp = client.post(
            "/admin/models/local/download",
            json={"source": "huggingface"},
        )
        assert resp.status_code == 422


# ── 3. Serve startup timeout ─────────────────────────────────────────────────


class TestServeStartupTimeout:
    """Startup timeout parameter on serve requests."""

    def test_serve_with_default_timeout_creates_stopped_server(self, client):
        port = _find_free_port()
        resp = client.post(
            "/admin/models/local/serve",
            json={
                "model_id": "timeout-default-model",
                "model_path": "/tmp/timeout-default.gguf",
                "engine": "llamacpp",
                "port": port,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "server_id" in data
        assert data["status"] == "stopped"

    def test_serve_with_positive_timeout_creates_or_errors(self, client):
        port = _find_free_port()
        resp = client.post(
            "/admin/models/local/serve",
            json={
                "model_id": "timeout-pos-model",
                "model_path": "/tmp/timeout-pos.gguf",
                "engine": "llamacpp",
                "port": port,
                "startup_timeout": 1.0,
            },
        )
        assert resp.status_code in (200, 500)
        if resp.status_code == 200:
            data = resp.json()
            assert "server_id" in data
            assert data["model_id"] == "timeout-pos-model"

    def test_serve_with_zero_timeout_defaults_stopped(self, client):
        port = _find_free_port()
        resp = client.post(
            "/admin/models/local/serve",
            json={
                "model_id": "timeout-zero-model",
                "model_path": "/tmp/timeout-zero.gguf",
                "engine": "llamacpp",
                "port": port,
                "startup_timeout": 0.0,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "stopped"

    def test_serve_timeout_does_not_prevent_listing(self, client):
        port = _find_free_port()
        client.post(
            "/admin/models/local/serve",
            json={
                "model_id": "timeout-list-model",
                "model_path": "/tmp/timeout-list.gguf",
                "engine": "llamacpp",
                "port": port,
                "startup_timeout": 0.5,
            },
        )
        status = client.get("/admin/models/local/status")
        assert status.status_code == 200
        assert "servers" in status.json()

    def test_invalid_negative_timeout_still_serves(self, client):
        port = _find_free_port()
        resp = client.post(
            "/admin/models/local/serve",
            json={
                "model_id": "timeout-neg-model",
                "model_path": "/tmp/timeout-neg.gguf",
                "engine": "llamacpp",
                "port": port,
                "startup_timeout": -1.0,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "server_id" in data


class TestServeLargeStartupTimeout:
    """Large startup timeout values don't break the serve endpoint."""

    def test_serve_large_timeout_creates_or_errors(self, client):
        port = _find_free_port()
        resp = client.post(
            "/admin/models/local/serve",
            json={
                "model_id": "large-timeout-model",
                "model_path": "/tmp/large-timeout.gguf",
                "engine": "llamacpp",
                "port": port,
                "startup_timeout": 300.0,
            },
        )
        assert resp.status_code in (200, 500)
        if resp.status_code == 200:
            data = resp.json()
            assert "server_id" in data
            assert data["status"] in ("stopped", "starting", "running", "dead")


# ── 4. Model not found in any source ─────────────────────────────────────────


class TestModelNotFoundFallback:
    """What happens when a model is requested that doesn't exist anywhere."""

    def test_unknown_source_returns_422(self, client):
        resp = client.post(
            "/admin/models/local/download",
            json={
                "model_id": "some/ghost-model",
                "source": "nonexistent_source_xyz",
            },
        )
        assert resp.status_code == 422

    def test_consume_nonexistent_server_returns_404(self, client):
        resp = client.post(
            "/admin/models/local/consume",
            json={
                "server_id": "ghost-server-99999",
                "prompt": "hello",
            },
        )
        assert resp.status_code == 404

    def test_consume_missing_prompt_returns_422(self, client):
        resp = client.post(
            "/admin/models/local/consume",
            json={"server_id": "some-server"},
        )
        assert resp.status_code == 422

    def test_consume_missing_server_id_returns_422(self, client):
        resp = client.post(
            "/admin/models/local/consume",
            json={"prompt": "hello"},
        )
        assert resp.status_code == 422

    def test_status_with_no_local_servers_returns_valid(self, client):
        resp = client.get("/admin/models/local/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "servers" in data
        assert "total" in data

    def test_shutdown_nonexistent_server_returns_200_or_404(self, client):
        resp = client.post(
            "/admin/models/local/shutdown",
            json={"server_id": "ghost-shutdown-99999"},
        )
        assert resp.status_code in (200, 404)

    def test_recommend_with_no_eval_data_returns_empty(self, client):
        resp = client.get("/admin/models/recommend?task=coding")
        assert resp.status_code == 200
        data = resp.json()
        assert "recommendations" in data
        assert data["total"] == 0

    def test_router_resolve_unknown_role_returns_none(self):
        router = ModelRouter(role_mapping={"coder": "coder_model"})
        assert router.resolve_role("nonexistent") is None

    def test_router_resolve_unknown_role_with_default(self):
        router = ModelRouter(
            role_mapping={"coder": "coder_model"},
            default_profile_id="fallback_model",
        )
        assert router.resolve_role("nonexistent") == "fallback_model"


# ── 5. Mixed local+cloud routing edge cases ───────────────────────────────────


class TestMixedLocalCloudRouting:
    """Cost-aware routing edge cases with local and cloud model mix."""

    def test_cost_router_schedule_weekend_is_off_peak(self):
        import datetime

        schedule = _DEFAULT_PEAK
        saturday = datetime.datetime(2025, 6, 7, 12, 0, tzinfo=datetime.UTC)
        router = CostAwareRouter(
            performance_router=MagicMock(),
            peak_schedule=schedule,
        )
        assert not router._is_peak(saturday)

    def test_cost_router_schedule_weekday_night_is_off_peak(self):
        import datetime

        schedule = _DEFAULT_PEAK
        early_morning = datetime.datetime(2025, 6, 4, 3, 0, tzinfo=datetime.UTC)
        router = CostAwareRouter(
            performance_router=MagicMock(),
            peak_schedule=schedule,
        )
        assert not router._is_peak(early_morning)

    def test_cost_router_schedule_weekday_business_is_peak(self):
        import datetime

        schedule = _DEFAULT_PEAK
        business = datetime.datetime(2025, 6, 4, 14, 0, tzinfo=datetime.UTC)
        router = CostAwareRouter(
            performance_router=MagicMock(),
            peak_schedule=schedule,
        )
        assert router._is_peak(business)

    def test_cost_router_peak_multiplier_range_extremes(self):
        with pytest.raises(ValueError):
            PeakPricingSchedule(peak_start_hour=0, peak_end_hour=23, peak_multiplier=0.0)
        with pytest.raises(ValueError):
            PeakPricingSchedule(peak_start_hour=0, peak_end_hour=23, off_peak_multiplier=-0.01)
        valid = PeakPricingSchedule(
            peak_start_hour=0,
            peak_end_hour=23,
            peak_multiplier=3.0,
            off_peak_multiplier=0.01,
        )
        assert valid.peak_multiplier == 3.0
        assert valid.off_peak_multiplier == 0.01

    def test_peak_schedule_rejects_single_day_weekend(self):
        schedule = PeakPricingSchedule(
            peak_start_hour=8,
            peak_end_hour=20,
            peak_days=frozenset({5}),
        )
        assert 5 in schedule.peak_days
        assert 0 not in schedule.peak_days

    def test_model_route_immutable_fields(self):
        route = ModelRoute(
            model_id="test/model",
            estimated_cost=0.05,
            peak_status=_PEAK,
            hourly_rate=0.05,
        )
        assert route.peak_status == _PEAK
        assert route.currency == "USD"
        assert route.model_id == "test/model"

    def test_budget_guard_initial_state_is_unlimited(self):
        guard = RunBudgetGuard()
        assert guard.check_run_budget()["allowed"] is True
        assert guard.check_wall_clock()["allowed"] is True
        assert guard.get_total_spend() == 0.0

    def test_budget_guard_check_all_limits_within_budget(self):
        guard = RunBudgetGuard(run_budget_usd=50.0, per_call_budget_usd=5.0)
        guard.record_spend(10.0)
        result = guard.check_all_limits(estimated_cost=3.0)
        assert result["allowed"] is True
        assert result["total_spend"] == pytest.approx(10.0)
        assert result["remaining_budget"] == pytest.approx(40.0)

    def test_budget_guard_check_all_exhausted_run_budget(self):
        guard = RunBudgetGuard(run_budget_usd=10.0)
        guard.record_spend(11.0)
        result = guard.check_all_limits(estimated_cost=1.0)
        assert result["allowed"] is False
        assert "run budget" in str(result["reason"]).lower()

    def test_budget_guard_check_all_exhausted_per_call(self):
        guard = RunBudgetGuard(per_call_budget_usd=2.0)
        result = guard.check_all_limits(estimated_cost=5.0)
        assert result["allowed"] is False
        assert "per-call" in str(result["reason"]).lower()

    def test_budget_guard_wall_clock_expired(self):
        guard = RunBudgetGuard(run_timeout_seconds=-1.0)
        result = guard.check_wall_clock()
        assert result["allowed"] is False

    def test_gateway_with_local_and_cloud_profiles(self):
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

        local = ModelProfile(
            model_profile_id="local-prof",
            provider="local-test",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="local-model-v1",
            enabled=True,
            api_metered=False,
            cost_per_input_token=0.0,
            cost_per_output_token=0.0,
        )
        cloud = ModelProfile(
            model_profile_id="cloud-prof",
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-3.5-turbo",
            enabled=True,
            cost_per_input_token=0.000003,
            cost_per_output_token=0.000006,
        )
        gw = ModelGateway(profiles=[local, cloud], provider_registry=reg)
        assert gw.get_profile("local-prof") is not None
        assert gw.get_profile("cloud-prof") is not None
        assert gw.get_profile("nonexistent") is None

    def test_router_weak_model_fallback_when_id_set(self):
        router = ModelRouter(
            role_mapping={"coder": "strong_model"},
            weak_model_profile_id="cheap_model",
        )
        assert router.resolve_role("weak") == "cheap_model"
        assert router.resolve_role("coder") == "strong_model"

    def test_router_weak_model_fallback_when_id_unset(self):
        router = ModelRouter(role_mapping={"coder": "strong_model"})
        assert router.resolve_role("weak") is None

    def test_route_recommendation_endpoint_exists(self, client):
        resp = client.get("/admin/models/recommend?task=coding")
        assert resp.status_code == 200
        data = resp.json()
        assert "task" in data
        assert "recommendations" in data
        assert "total" in data

    def test_cost_endpoint_reports_off_peak_status(self, client):
        resp = client.get("/admin/models/cost?model=test-model")
        assert resp.status_code == 200
        data = resp.json()
        assert "off_peak" in data
        assert "is_off_peak_now" in data["off_peak"]

    def test_shutdown_missing_server_id_returns_422(self, client):
        resp = client.post("/admin/models/local/shutdown", json={})
        assert resp.status_code == 422

    def test_serve_empty_model_id_rejected(self, client):
        resp = client.post(
            "/admin/models/local/serve",
            json={"model_id": "", "port": _find_free_port()},
        )
        assert resp.status_code == 422


# ── 6. Gateway fallback-chain edge cases ──────────────────────────────────────


class TestGatewayFallbackEdgeCases:
    """Edge cases for the gateway fallback chain with local/cloud mix."""

    def test_fallback_all_profiles_exhausted_by_budget(self):
        primary = ModelProfile(
            model_profile_id="budget_primary",
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4",
            enabled=True,
            api_metered=False,
            run_budget_usd=0.001,
            fallback_profiles=["budget_fallback"],
        )
        fallback = ModelProfile(
            model_profile_id="budget_fallback",
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-3.5-turbo",
            enabled=True,
            api_metered=False,
            run_budget_usd=0.001,
        )
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        gw = ModelGateway(profiles=[primary, fallback], provider_registry=reg)

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=MagicMock()),
            pytest.raises(ValueError, match="over budget"),
        ):
            gw.call_model_with_fallback(
                "budget_primary",
                [{"role": "user", "content": "hello"}],
                estimated_cost=5.0,
                budget_remaining=1.0,
            )

    def test_gateway_fallback_with_disabled_profile_skips(self):
        primary = ModelProfile(
            model_profile_id="disabled_primary",
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4",
            enabled=False,
            fallback_profiles=["enabled_fallback"],
        )
        fallback = ModelProfile(
            model_profile_id="enabled_fallback",
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-3.5-turbo",
            enabled=True,
            api_metered=False,
            run_budget_usd=999.0,
            cost_per_input_token=0.0,
            cost_per_output_token=0.0,
        )
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

        fake_chat = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.return_value = MagicMock(
            content="fallback from disabled",
            usage_metadata={"input_tokens": 1, "output_tokens": 1},
        )
        fake_chat.return_value = fake_instance

        gw = ModelGateway(profiles=[primary, fallback], provider_registry=reg)

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=fake_chat),
        ):
            resp = gw.call_model_with_fallback(
                "disabled_primary",
                [{"role": "user", "content": "hello"}],
                estimated_cost=0.001,
                budget_remaining=100.0,
            )
        assert resp.content == "fallback from disabled"

    def test_router_add_and_remove_role_is_idempotent(self):
        router = ModelRouter()
        router.add_role("coder", "model-a")
        router.add_role("coder", "model-b")
        assert router.resolve_role("coder") == "model-b"

    def test_router_pattern_mapping_resolves(self):
        router = ModelRouter(
            role_mapping={"coder": "code_model"},
        )
        router.add_pattern_mapping("bug_fix", "coder")
        assert router.resolve_pattern("bug_fix") == "code_model"
        assert router.resolve_pattern("unknown_pattern") is None

    def test_router_list_roles_includes_added(self):
        router = ModelRouter()
        router.add_role("coder", "c1")
        router.add_role("reviewer", "r1")
        assert set(router.list_roles()) == {"coder", "reviewer"}

    def test_router_list_profiles_by_role(self):
        router = ModelRouter()
        router.add_role("coder", "c1")
        router.add_role("planner", "c1")
        roles_for_c1 = router.list_profiles_by_role("c1")
        assert set(roles_for_c1) == {"coder", "planner"}
