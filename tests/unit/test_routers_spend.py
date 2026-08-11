"""Deep behavioral tests for routers/spend.py — SpendLimiter, cost, credits, and budget endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class TestSpendRegister:
    def test_register_is_callable(self) -> None:
        from general_ludd.routers.spend import register

        assert callable(register)

    def test_register_adds_all_expected_paths(self) -> None:
        from general_ludd.routers.spend import register

        expected = {
            "/api/spend",
            "/api/spend/configure",
            "/api/costs",
            "/api/credits",
            "/admin/costs",
            "/admin/budget/rates",
            "/admin/budget/savings",
        }
        app = FastAPI()
        register(app, {})
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        for ep in expected:
            assert ep in paths, f"Missing path: {ep}"

    def test_register_returns_none(self) -> None:
        from general_ludd.routers.spend import register

        result = register(FastAPI(), {})
        assert result is None


class TestSpendStatus:
    def test_no_limiter_returns_inactive_defaults(self) -> None:
        from general_ludd.routers.spend import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/spend")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limiter_active"] is False
        assert data["window_spend_usd"] == 0.0
        assert data["limit_usd"] == 20.0
        assert data["remaining_usd"] == 20.0
        assert data["window_seconds"] == 3600.0

    def test_with_limiter_returns_active_and_values(self) -> None:
        from general_ludd.controllers.spend_limiter import SpendLimiter
        from general_ludd.routers.spend import register

        app = FastAPI()
        app.state._spend_limiter = SpendLimiter(limit_usd=50.0, window_seconds=7200.0)
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/spend")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limiter_active"] is True
        assert data["limit_usd"] == 50.0
        assert data["window_seconds"] == 7200.0

    def test_unknown_object_as_limiter_falls_back_to_inactive(self) -> None:
        from general_ludd.routers.spend import register

        app = FastAPI()
        app.state._spend_limiter = object()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/spend")
        assert resp.json()["limiter_active"] is False


class TestSpendConfigure:
    def test_no_limiter_creates_new(self) -> None:
        from general_ludd.routers.spend import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/spend/configure", json={"limit_usd": 30.0, "window_seconds": 1800.0})
        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is True
        assert data["limit_usd"] == 30.0
        assert data["window_seconds"] == 1800.0
        assert app.state._spend_limiter is not None

    def test_reconfigure_preserves_history(self) -> None:
        from general_ludd.controllers.spend_limiter import SpendLimiter
        from general_ludd.routers.spend import register

        app = FastAPI()
        old = SpendLimiter(limit_usd=100.0, window_seconds=3600.0)
        old.record(10.0, kind="token")
        app.state._spend_limiter = old
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/spend/configure", json={"limit_usd": 200.0, "window_seconds": 3600.0})
        assert resp.status_code == 200
        new = app.state._spend_limiter
        assert new is not old
        assert new._limit_usd == 200.0
        assert new.window_spend() == pytest.approx(10.0)

    def test_reconfigure_rejects_zero_limit(self) -> None:
        from general_ludd.routers.spend import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/spend/configure", json={"limit_usd": 0.0, "window_seconds": 3600.0})
        assert resp.status_code == 422

    def test_reconfigure_rejects_zero_window(self) -> None:
        from general_ludd.routers.spend import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/spend/configure", json={"limit_usd": 10.0, "window_seconds": 0.0})
        assert resp.status_code == 422


class TestAdminCosts:
    def test_no_limiter_returns_zero(self) -> None:
        from general_ludd.routers.spend import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/costs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_api_spend"] == 0.0
        assert data["total_infra_spend"] == 0.0
        assert data["burn_rate_24h"] == 0.0

    def test_with_limiter_and_infra_tracker(self) -> None:
        from general_ludd.controllers.spend_limiter import SpendLimiter
        from general_ludd.routers.spend import register

        app = FastAPI()
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0)
        limiter.record(15.0, kind="token")
        app.state._spend_limiter = limiter

        infra_tracker = MagicMock()
        infra_tracker.get_total_infra_cost.return_value = 5.0
        infra_tracker.get_infra_cost_by_provider.return_value = {"aws": 5.0}
        app.state._infra_tracker = infra_tracker

        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/costs")
        data = resp.json()
        assert data["total_api_spend"] == 15.0
        assert data["total_infra_spend"] == 5.0
        assert data["total_combined_spend"] == 20.0


class TestApiCosts:
    def test_no_combined_no_limiter_returns_zeros(self) -> None:
        from general_ludd.routers.spend import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/costs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["model_api"] == 0.0
        assert data["infrastructure"] == 0.0
        assert data["total"] == 0.0

    def test_combined_cost_tracker_takes_priority(self) -> None:
        from general_ludd.routers.spend import register

        combined = MagicMock()
        combined.get_cost_breakdown.return_value = {
            "model_api": 42.0,
            "infrastructure": 8.0,
            "total": 50.0,
            "breakdown_by_provider": {},
            "breakdown_by_resource_type": {},
            "breakdown_by_project": {},
            "record_count": 0,
        }
        app = FastAPI()
        app.state._combined_cost_tracker = combined
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/costs")
        data = resp.json()
        assert data["total"] == 50.0

    def test_cost_tracker_v2_fallback(self) -> None:
        from general_ludd.routers.spend import register

        v2 = MagicMock()
        v2.total_cost.return_value = 7.0
        v2.cost_by_provider.return_value = {"gcp": 7.0}
        v2.cost_by_resource_type.return_value = {}
        v2.cost_by_project.return_value = {}
        v2.records.return_value = [1, 2]

        app = FastAPI()
        app.state._infra_cost_tracker = v2
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/costs")
        data = resp.json()
        assert data["infrastructure"] == 7.0
        assert data["record_count"] == 2

    def test_infra_tracker_v1_fallback(self) -> None:
        from general_ludd.routers.spend import register

        v1 = MagicMock()
        v1.get_total_infra_cost.return_value = 3.0
        v1.get_infra_cost_by_provider.return_value = {"aws": 3.0}
        v1.get_infra_cost_by_project.return_value = {}

        app = FastAPI()
        app.state._infra_tracker = v1
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/costs")
        data = resp.json()
        assert data["infrastructure"] == 3.0


class TestApiCredits:
    def test_no_tracker_returns_empty_dict(self) -> None:
        from general_ludd.routers.spend import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/credits")
        assert resp.json() == {}

    def test_tracker_cached_returns_balances(self) -> None:
        from general_ludd.routers.spend import register

        tracker = MagicMock()
        tracker._last_balance = {"openai": {"balance_usd": 10.0}}
        tracker.last_balance.return_value = {"balance_usd": 10.0}
        app = FastAPI()
        app.state._credit_tracker = tracker
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/credits")
        data = resp.json()
        assert "openai" in data

    def test_tracker_empty_cache_probes(self) -> None:
        from general_ludd.routers.spend import register

        tracker = MagicMock()
        tracker._last_balance = None
        tracker.check_all_balances.return_value = {"openai": {"balance_usd": 5.0}}
        app = FastAPI()
        app.state._credit_tracker = tracker
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/credits")
        data = resp.json()
        assert data["openai"]["balance_usd"] == 5.0

    def test_refresh_param_forces_probe(self) -> None:
        from general_ludd.routers.spend import register

        tracker = MagicMock()
        tracker._last_balance = {"openai": {"balance_usd": 10.0}}
        tracker.check_all_balances.return_value = {"openai": {"balance_usd": 99.0}}
        app = FastAPI()
        app.state._credit_tracker = tracker
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/credits?refresh=true")
        data = resp.json()
        assert data["openai"]["balance_usd"] == 99.0

    def test_probe_exception_returns_empty(self) -> None:
        from general_ludd.routers.spend import register

        tracker = MagicMock()
        tracker._last_balance = None
        tracker.check_all_balances.side_effect = RuntimeError("network gone")
        app = FastAPI()
        app.state._credit_tracker = tracker
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/credits")
        assert resp.json() == {}


class TestBudgetRates:
    def test_budget_rates_returns_structure(self) -> None:
        from general_ludd.routers.spend import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/budget/rates")
        assert resp.status_code == 200
        data = resp.json()
        assert "current_period" in data
        assert data["current_period"] in ("peak", "off-peak")
        assert "rate_multiplier" in data
        assert "models" in data

    def test_budget_rates_with_gateway(self) -> None:
        from general_ludd.routers.spend import register

        profile = MagicMock()
        profile.enabled = True
        profile.model_profile_id = "gpt4"
        profile.model_name = "gpt-4"
        profile.cost_per_input_token = 0.03
        profile.cost_per_output_token = 0.06

        gateway = MagicMock()
        gateway.list_profiles.return_value = [profile]
        app = FastAPI()
        app.state._model_gateway = gateway
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/budget/rates")
        data = resp.json()
        assert "gpt4" in data["models"]


class TestBudgetSavings:
    def test_budget_savings_returns_structure(self) -> None:
        from general_ludd.routers.spend import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/budget/savings")
        assert resp.status_code == 200
        data = resp.json()
        assert "cumulative_full_cost" in data
        assert "cumulative_discounted_cost" in data
        assert "cumulative_savings" in data
        assert "savings_percentage" in data
