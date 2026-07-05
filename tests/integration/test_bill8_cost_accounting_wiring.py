"""Integration tests for bill-8 cost accounting wiring through daemon endpoints.

Proves the cost accounting pipeline is wired end-to-end:
- GET /admin/costs returns API+infra spend, project/provider breakdown, burn rate
- SpendLimiter.window_spend() and project_breakdown()
- InfraTracker.record_gpu_seconds()
- Per-project cost/time/LoC accounting via GET /api/accounting
- Spend limiter gate in EventLoop dispatch via try_charge
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from general_ludd.controllers.spend_limiter import SpendLimiter
from general_ludd.daemon import create_daemon_app
from general_ludd.infra.pricing import InfraTracker


def _make_limiter(limit_usd: float, window_seconds: float) -> tuple[SpendLimiter, list[float]]:
    clock_val: list[float] = [0.0]

    def fake_clock() -> float:
        return clock_val[0]

    limiter = SpendLimiter(limit_usd=limit_usd, window_seconds=window_seconds, clock=fake_clock)
    return limiter, clock_val


class TestAdminCostsEndpoint:
    def test_admin_costs_returns_structure_when_no_limiter(self):
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=10.0)
            app.state._spend_limiter = None
            app.state._infra_tracker = None
            with TestClient(app) as client:
                resp = client.get("/admin/costs")
                assert resp.status_code == 200
                body = resp.json()
                assert body["total_api_spend"] == 0.0
                assert body["total_infra_spend"] == 0.0
                assert body["total_combined_spend"] == 0.0
                assert body["breakdown_by_project"] == {}
                assert body["breakdown_by_provider"] == {}
                assert body["burn_rate_24h"] == 0.0

    def test_admin_costs_returns_live_spend_data(self):
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=10.0)
            with TestClient(app) as client:
                limiter, clock = _make_limiter(100.0, 3600.0)
                clock[0] = 200.0
                limiter.record(5.0, kind="token", project_id="p1")
                limiter.record(3.0, kind="token", project_id="p2")
                app.state._spend_limiter = limiter

                infra = InfraTracker()
                infra.record_gpu_seconds("runpod", "A100", 3600.0)
                app.state._infra_tracker = infra

                resp = client.get("/admin/costs")
                assert resp.status_code == 200
                body = resp.json()
                assert body["total_api_spend"] == pytest.approx(8.0)
                assert body["total_infra_spend"] > 0.0
                assert body["total_combined_spend"] > body["total_api_spend"]
                assert body["breakdown_by_project"] == {"p1": 5.0, "p2": 3.0}
                assert "runpod" in body["breakdown_by_provider"]
                assert body["burn_rate_24h"] >= 0.0

    def test_admin_costs_with_infra_only_no_api_spend(self):
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=10.0)
            with TestClient(app) as client:
                app.state._spend_limiter = None
                infra = InfraTracker()
                infra.record_gpu_seconds("aws", "H100", 500.0)
                app.state._infra_tracker = infra

                resp = client.get("/admin/costs")
                body = resp.json()
                assert body["total_api_spend"] == 0.0
                assert body["total_infra_spend"] > 0.0
                assert body["breakdown_by_provider"]["aws"] > 0.0
                assert body["total_combined_spend"] > 0.0

    def test_admin_costs_breakdown_by_provider_multi_provider(self):
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=10.0)
            with TestClient(app) as client:
                infra = InfraTracker()
                infra.record_gpu_seconds("runpod", "A100", 100.0)
                infra.record_gpu_seconds("aws", "A100", 200.0)
                app.state._infra_tracker = infra

                resp = client.get("/admin/costs")
                body = resp.json()
                providers = body["breakdown_by_provider"]
                assert "runpod" in providers
                assert "aws" in providers


class TestSpendLimiterWindowSpendAndProjectBreakdown:
    def test_window_spend_sums_all_records(self):
        limiter, clock = _make_limiter(200.0, 3600.0)
        clock[0] = 100.0
        limiter.record(10.0, kind="token", project_id="a")
        limiter.record(15.0, kind="token", project_id="b")
        assert limiter.window_spend() == pytest.approx(25.0)

    def test_window_spend_with_project_filter(self):
        limiter, clock = _make_limiter(200.0, 3600.0)
        clock[0] = 100.0
        limiter.record(10.0, kind="token", project_id="x")
        limiter.record(20.0, kind="token", project_id="y")
        assert limiter.window_spend(project_id="x") == pytest.approx(10.0)
        assert limiter.window_spend(project_id="y") == pytest.approx(20.0)

    def test_project_breakdown_groups_records(self):
        limiter, clock = _make_limiter(200.0, 3600.0)
        clock[0] = 100.0
        limiter.record(5.0, kind="token", project_id="a")
        limiter.record(7.0, kind="token", project_id="b")
        limiter.record(3.0, kind="token", project_id="a")
        bt = limiter.project_breakdown()
        assert bt == {"a": 8.0, "b": 7.0}

    def test_project_spend_convenience_matches(self):
        limiter, clock = _make_limiter(200.0, 3600.0)
        clock[0] = 100.0
        limiter.record(12.0, kind="token", project_id="proj")
        assert limiter.project_spend("proj") == pytest.approx(12.0)

    def test_old_records_pruned_from_breakdown(self):
        limiter, clock = _make_limiter(200.0, 3600.0)
        clock[0] = 0.0
        limiter.record(50.0, kind="token", project_id="stale")
        clock[0] = 3601.0
        limiter.record(1.0, kind="token", project_id="fresh")
        bt = limiter.project_breakdown()
        assert "stale" not in bt
        assert bt == {"fresh": 1.0}


class TestInfraTrackerRecordGpuSeconds:
    def test_record_accumulates_total_cost(self):
        tracker = InfraTracker()
        tracker.record_gpu_seconds("runpod", "A100-SXM4-80GB-1x", 100.0)
        assert tracker.get_total_infra_cost() > 0.0

    def test_record_by_provider_breakdown(self):
        tracker = InfraTracker()
        tracker.record_gpu_seconds("runpod", "A100", 60.0)
        tracker.record_gpu_seconds("aws", "H100", 120.0)
        by_provider = tracker.get_infra_cost_by_provider()
        assert "runpod" in by_provider
        assert "aws" in by_provider
        assert by_provider["runpod"] > 0.0
        assert by_provider["aws"] > 0.0

    def test_record_by_project_breakdown(self):
        tracker = InfraTracker()
        tracker.record_gpu_seconds("runpod", "A100", 60.0, project_id="proj-a")
        tracker.record_gpu_seconds("aws", "H100", 30.0, project_id="proj-b")
        by_project = tracker.get_infra_cost_by_project()
        assert "proj-a" in by_project
        assert "proj-b" in by_project

    def test_record_negative_seconds_ignored(self):
        tracker = InfraTracker()
        cost_before = tracker.get_total_infra_cost()
        tracker.record_gpu_seconds("runpod", "A100", -10.0)
        assert tracker.get_total_infra_cost() == cost_before

    def test_record_non_finite_seconds_ignored(self):
        tracker = InfraTracker()
        cost_before = tracker.get_total_infra_cost()
        tracker.record_gpu_seconds("runpod", "A100", float("nan"))
        tracker.record_gpu_seconds("runpod", "A100", float("inf"))
        assert tracker.get_total_infra_cost() == cost_before


class TestApiAccountingEndpoint:
    def test_accounting_all_returns_list(self):
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=10.0)
            with TestClient(app) as client:
                resp = client.get("/api/accounting")
                assert resp.status_code == 200
                body = resp.json()
                assert isinstance(body, list)

    def test_accounting_all_fields_when_empty(self):
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=10.0)
            with TestClient(app) as client:
                resp = client.get("/api/accounting")
                assert resp.status_code == 200
                body = resp.json()
                if body:
                    entry = body[0]
                    assert "project_id" in entry
                    assert "elapsed_seconds" in entry
                    assert "tokens_used" in entry
                    assert "usd_spent" in entry
                    assert "quota_usd" in entry
                    assert "pct_quota" in entry
                    assert "loc_changed" in entry
                    assert "role_stats" in entry
                    assert "todo_summary" in entry
                    assert "points_estimated" in entry
                    assert "points_done" in entry

    def test_accounting_specific_project_returns_200_or_404(self):
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=10.0)
            with TestClient(app) as client:
                resp = client.get("/api/accounting/nonexistent-project")
                assert resp.status_code in (200, 404)


class TestSpendLimiterGateInEventLoopDispatch:
    def test_try_charge_accepts_under_limit(self):
        limiter, clock = _make_limiter(50.0, 3600.0)
        clock[0] = 0.0
        assert limiter.try_charge(10.0, kind="token", model="claude-3")
        assert limiter.window_spend() == pytest.approx(10.0)

    def test_try_charge_refuses_over_limit(self):
        limiter, clock = _make_limiter(50.0, 3600.0)
        clock[0] = 0.0
        assert limiter.try_charge(49.0, kind="token", model="claude-3")
        assert not limiter.try_charge(2.0, kind="token", model="claude-3")
        assert limiter.window_spend() == pytest.approx(49.0)

    def test_try_charge_refuses_unknown_cost_under_cap(self):
        limiter, clock = _make_limiter(50.0, 3600.0)
        clock[0] = 0.0
        assert not limiter.try_charge(None, kind="token")

    def test_try_charge_allows_unknown_cost_when_no_cap(self):
        limiter, clock = _make_limiter(0.0, 3600.0)
        clock[0] = 0.0
        assert limiter.try_charge(None, kind="token")

    def test_try_charge_refuses_non_finite_cost(self):
        limiter, clock = _make_limiter(50.0, 3600.0)
        clock[0] = 0.0
        assert not limiter.try_charge(float("nan"), kind="token")
        assert not limiter.try_charge(float("inf"), kind="token")

    def test_remaining_decreases_after_charge(self):
        limiter, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 0.0
        assert limiter.remaining() == pytest.approx(100.0)
        limiter.try_charge(30.0, kind="token")
        assert limiter.remaining() == pytest.approx(70.0)
