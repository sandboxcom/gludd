"""Coverage tests for routers/spend.py (spend-limiter API endpoints).

CI flagged this module at 63%. The uncovered paths were the limiter-present
branch of ``GET /api/spend`` (returning live window_spend/limit/remaining) and
the entire ``POST /api/spend/configure`` handler (which constructs a fresh
SpendLimiter and stores it on app.state).

Follows the router test convention (TestClient over a bare FastAPI app with the
router registered and app.state primed), mirroring test_daemon_endpoint_coverage.py.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.controllers.spend_limiter import SpendLimiter
from general_ludd.routers.spend import (
    _DEFAULT_LIMIT_USD,
    _DEFAULT_WINDOW_SECONDS,
    register,
)


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    register(app, {})
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


class TestSpendStatusNoLimiter:
    def test_status_returns_safe_defaults_when_no_limiter(self, client: TestClient) -> None:
        resp = client.get("/api/spend")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limiter_active"] is False
        assert data["window_spend_usd"] == pytest.approx(0.0)
        assert data["limit_usd"] == pytest.approx(_DEFAULT_LIMIT_USD)
        assert data["remaining_usd"] == pytest.approx(_DEFAULT_LIMIT_USD)
        assert data["window_seconds"] == pytest.approx(_DEFAULT_WINDOW_SECONDS)


class TestSpendStatusWithLimiter:
    def test_status_reports_live_limiter_values(self, app: FastAPI, client: TestClient) -> None:
        # Fixed fake clock so window math is deterministic.
        clock = lambda: 1000.0  # noqa: E731
        limiter = SpendLimiter(limit_usd=10.0, window_seconds=3600.0, clock=clock)
        limiter.record(2.5, kind="token", at=1000.0)
        app.state._spend_limiter = limiter

        resp = client.get("/api/spend")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limiter_active"] is True
        assert data["window_spend_usd"] == pytest.approx(2.5)
        assert data["limit_usd"] == pytest.approx(10.0)
        assert data["remaining_usd"] == pytest.approx(7.5)
        assert data["window_seconds"] == pytest.approx(3600.0)

    def test_status_remaining_floors_at_zero_when_over_limit(
        self, app: FastAPI, client: TestClient
    ) -> None:
        clock = lambda: 500.0  # noqa: E731
        limiter = SpendLimiter(limit_usd=1.0, window_seconds=3600.0, clock=clock)
        limiter.record(5.0, kind="token", at=500.0)
        app.state._spend_limiter = limiter

        resp = client.get("/api/spend")
        data = resp.json()
        assert data["window_spend_usd"] == pytest.approx(5.0)
        assert data["remaining_usd"] == pytest.approx(0.0)


class TestSpendConfigure:
    def test_configure_installs_new_limiter(self, app: FastAPI, client: TestClient) -> None:
        resp = client.post(
            "/api/spend/configure",
            json={"limit_usd": 42.0, "window_seconds": 120.0},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"configured": True, "limit_usd": 42.0, "window_seconds": 120.0}

        # The new limiter must be stored on app.state with the requested config.
        limiter = app.state._spend_limiter
        assert isinstance(limiter, SpendLimiter)
        assert limiter._limit_usd == pytest.approx(42.0)
        assert limiter._window_seconds == pytest.approx(120.0)

    def test_configure_then_status_reflects_new_limits(self, client: TestClient) -> None:
        client.post(
            "/api/spend/configure",
            json={"limit_usd": 8.0, "window_seconds": 60.0},
        )
        resp = client.get("/api/spend")
        data = resp.json()
        assert data["limiter_active"] is True
        assert data["limit_usd"] == pytest.approx(8.0)
        # fresh limiter starts with an empty window
        assert data["window_spend_usd"] == pytest.approx(0.0)
        assert data["remaining_usd"] == pytest.approx(8.0)

    def test_configure_rejects_non_positive_limit(self, client: TestClient) -> None:
        # Field(gt=0.0) -> 422 validation error
        resp = client.post(
            "/api/spend/configure",
            json={"limit_usd": 0.0, "window_seconds": 60.0},
        )
        assert resp.status_code == 422

    def test_configure_rejects_non_positive_window(self, client: TestClient) -> None:
        resp = client.post(
            "/api/spend/configure",
            json={"limit_usd": 5.0, "window_seconds": -1.0},
        )
        assert resp.status_code == 422
