"""Coverage tests for routers/spend.py (spend-limiter API endpoints).

CI flagged this module at 63%. The uncovered paths were the limiter-present
branch of ``GET /api/spend`` (returning live window_spend/limit/remaining) and
the entire ``POST /api/spend/configure`` handler (which constructs a fresh
SpendLimiter and stores it on app.state).

Follows the router test convention (TestClient over a bare FastAPI app with the
router registered and app.state primed), mirroring test_daemon_endpoint_coverage.py.
"""

from __future__ import annotations

import time

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
        def clock() -> float:
            return 1000.0

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
        def clock() -> float:
            return 500.0

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


class TestSpendConfigurePreservesHistory:
    """Security tests: POST /api/spend/configure must NOT reset spend history.

    D-33: replacing app.state._spend_limiter with an empty-records SpendLimiter
    lets any PSK-holder wipe the rolling-window spend mid-window, evading the cap
    by reconfiguring it.  The fix: carry old limiter records into the new limiter
    via old.snapshot() → new.restore(...) so prior spend still counts.
    """

    def test_configure_preserves_prior_spend_records(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """Spend recorded BEFORE configure must appear in the new limiter's records.

        We check _records directly (not window_spend()) to avoid any clock-domain
        mismatch between the old fake clock and the new limiter's real monotonic
        clock: if the test machine's monotonic clock is far ahead of the fake ts,
        window_spend() would prune the records even if they were carried over.
        Checking _records length asserts that the history transfer happened at all.
        """
        # Prime the limiter with spend using a real-time-anchored timestamp so
        # the record survives window_spend() pruning under the real clock.
        now_ts = time.monotonic()
        old_limiter = SpendLimiter(limit_usd=10.0, window_seconds=3600.0)
        old_limiter.record(8.0, kind="token", at=now_ts)
        app.state._spend_limiter = old_limiter

        # Reconfigure — the endpoint must carry records over.
        resp = client.post(
            "/api/spend/configure",
            json={"limit_usd": 20.0, "window_seconds": 3600.0},
        )
        assert resp.status_code == 200

        new_limiter = app.state._spend_limiter
        assert isinstance(new_limiter, SpendLimiter)
        # The raw record list must be non-empty: history was carried over.
        assert len(new_limiter._records) > 0, (
            "Spend history was wiped on reconfigure — cap-reset evasion is possible."
        )
        # And the carried-over record has cost=8.0. _records holds the internal
        # 4-tuple (seq, ts, cost_usd, project_id) — cost is element 2.
        assert any(c == pytest.approx(8.0) for _, _, c, _ in new_limiter._records)

    def test_configure_old_spend_counts_against_new_cap(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """Old spend + new lower cap must correctly trip would_exceed().

        Records are anchored to time.monotonic() so they remain within the
        new limiter's 3600-second rolling window.
        """
        now_ts = time.monotonic()
        old_limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0)
        # Record 9 USD of spend while the old cap was 100 USD.
        old_limiter.record(9.0, kind="token", at=now_ts)
        app.state._spend_limiter = old_limiter

        # Reconfigure to a 10 USD cap — the 9 USD already spent must count.
        resp = client.post(
            "/api/spend/configure",
            json={"limit_usd": 10.0, "window_seconds": 3600.0},
        )
        assert resp.status_code == 200

        new_limiter = app.state._spend_limiter
        # 2 USD projected would exceed 10 - 9 = 1 USD remaining.
        assert new_limiter.would_exceed(2.0) is True, (
            "New limiter ignores pre-configure spend — cap-reset evasion is possible."
        )

    def test_configure_without_prior_limiter_starts_empty(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """When there is no existing limiter, configure starts with zero spend."""
        # No pre-existing limiter on app.state.
        if hasattr(app.state, "_spend_limiter"):
            del app.state._spend_limiter

        resp = client.post(
            "/api/spend/configure",
            json={"limit_usd": 5.0, "window_seconds": 60.0},
        )
        assert resp.status_code == 200
        new_limiter = app.state._spend_limiter
        assert new_limiter.window_spend() == pytest.approx(0.0)
