"""TDD tests for CombinedCostTracker — unified model API + infra cost tracking.

Covers:
  - Construction with both / either / neither underlying tracker
  - record_model_cost delegates to SpendLimiter (visible to cap enforcement)
  - record_infra_cost delegates to InfraCostTracker (per-provider breakdown)
  - get_total_spend returns the combined sum
  - get_cost_breakdown returns per-category breakdown with merged project map
  - Cap-enforcement passthrough (remaining_model_budget, would_exceed_combined)
  - Snapshot shape for persistence
  - Thread safety (delegates to underlying locks)
  - Wiring into GET /api/costs
  - Error handling: recording into a missing side raises RuntimeError
  - Error handling: negative / non-finite costs raise ValueError
"""

from __future__ import annotations

import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.budget import CombinedCostTracker
from general_ludd.budget.combined_cost import CombinedCostTracker as _DirectImport
from general_ludd.controllers.spend_limiter import SpendLimiter
from general_ludd.infra.cost_tracker import (
    InfraCostTracker,
    ResourceType,
)
from general_ludd.routers.spend import register as register_spend

# ── Construction ─────────────────────────────────────────────────────────


class TestConstruction:
    def test_reexport_matches_direct_import(self) -> None:
        assert CombinedCostTracker is _DirectImport

    def test_with_both_trackers_wired(self) -> None:
        sl = SpendLimiter(limit_usd=10.0, window_seconds=3600.0)
        it = InfraCostTracker()
        cct = CombinedCostTracker(spend_limiter=sl, infra_tracker=it)
        assert cct.has_model is True
        assert cct.has_infra is True
        assert cct.spend_limiter is sl
        assert cct.infra_tracker is it

    def test_with_only_spend_limiter(self) -> None:
        sl = SpendLimiter(limit_usd=10.0, window_seconds=3600.0)
        cct = CombinedCostTracker(spend_limiter=sl)
        assert cct.has_model is True
        assert cct.has_infra is False

    def test_with_only_infra_tracker(self) -> None:
        it = InfraCostTracker()
        cct = CombinedCostTracker(infra_tracker=it)
        assert cct.has_model is False
        assert cct.has_infra is True

    def test_with_neither_defaults_to_empty(self) -> None:
        cct = CombinedCostTracker()
        assert cct.has_model is False
        assert cct.has_infra is False
        assert cct.get_total_spend() == 0.0


# ── Recording ────────────────────────────────────────────────────────────


class TestRecordModelCost:
    def test_delegates_to_spend_limiter(self) -> None:
        clock_times = [1000.0]

        def clock() -> float:
            return clock_times[0]

        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=clock)
        cct = CombinedCostTracker(spend_limiter=sl)

        cct.record_model_cost(5.0, model="claude-3-5-sonnet", project_id="p1")

        assert sl.window_spend() == pytest.approx(5.0)
        assert cct.model_spend() == pytest.approx(5.0)

    def test_visible_to_cap_enforcement(self) -> None:
        """A recorded model cost must count against the rolling cap."""
        sl = SpendLimiter(limit_usd=10.0, window_seconds=3600.0)
        cct = CombinedCostTracker(spend_limiter=sl)

        cct.record_model_cost(8.0, kind="token")
        # 8.0 spent, 2.0 headroom -> a 3.0 charge should exceed.
        assert sl.would_exceed(3.0) is True
        assert sl.would_exceed(1.5) is False

    def test_raises_on_missing_spend_limiter(self) -> None:
        cct = CombinedCostTracker()
        with pytest.raises(RuntimeError, match="no SpendLimiter"):
            cct.record_model_cost(1.0)

    def test_rejects_negative_cost(self) -> None:
        sl = SpendLimiter(limit_usd=10.0, window_seconds=3600.0)
        cct = CombinedCostTracker(spend_limiter=sl)
        with pytest.raises(ValueError):
            cct.record_model_cost(-1.0)

    def test_rejects_non_finite_cost(self) -> None:
        sl = SpendLimiter(limit_usd=10.0, window_seconds=3600.0)
        cct = CombinedCostTracker(spend_limiter=sl)
        with pytest.raises(ValueError):
            cct.record_model_cost(float("nan"))
        with pytest.raises(ValueError):
            cct.record_model_cost(float("inf"))


class TestRecordInfraCost:
    def test_delegates_to_infra_tracker(self) -> None:
        it = InfraCostTracker()
        cct = CombinedCostTracker(infra_tracker=it)

        rec = cct.record_infra_cost(
            "aws",
            ResourceType.GPU_INSTANCE,
            "i-1",
            15.0,
            sku="p4d.24xlarge",
            gpu_type="A100",
            gpu_count=8,
            region="us-east-1",
            project_id="train-42",
        )

        assert rec.cost_usd == pytest.approx(15.0)
        assert rec.provider == "aws"
        assert it.total_cost() == pytest.approx(15.0)
        assert it.cost_by_provider()["aws"] == pytest.approx(15.0)

    def test_visible_in_provider_breakdown(self) -> None:
        it = InfraCostTracker()
        cct = CombinedCostTracker(infra_tracker=it)

        cct.record_infra_cost("aws", ResourceType.GPU_INSTANCE, "i-1", 10.0)
        cct.record_infra_cost("gcp", ResourceType.CPU_INSTANCE, "ci-1", 5.0)
        cct.record_infra_cost("runpod", ResourceType.GPU_INSTANCE, "r-1", 3.0)

        bd = cct.get_cost_breakdown()
        assert bd["breakdown_by_provider"]["aws"] == pytest.approx(10.0)
        assert bd["breakdown_by_provider"]["gcp"] == pytest.approx(5.0)
        assert bd["breakdown_by_provider"]["runpod"] == pytest.approx(3.0)
        assert bd["record_count"] == 3

    def test_raises_on_missing_infra_tracker(self) -> None:
        cct = CombinedCostTracker()
        with pytest.raises(RuntimeError, match="no InfraCostTracker"):
            cct.record_infra_cost("aws", ResourceType.GPU_INSTANCE, "i-1", 1.0)

    def test_rejects_negative_cost(self) -> None:
        it = InfraCostTracker()
        cct = CombinedCostTracker(infra_tracker=it)
        with pytest.raises(ValueError):
            cct.record_infra_cost("aws", ResourceType.GPU_INSTANCE, "i-1", -1.0)


# ── Combined totals & breakdown ─────────────────────────────────────────


class TestGetTotalSpend:
    def test_sums_model_plus_infra(self) -> None:
        def clock() -> float:
            return 1000.0

        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=clock)
        it = InfraCostTracker()
        cct = CombinedCostTracker(spend_limiter=sl, infra_tracker=it)

        cct.record_model_cost(20.0, at=1000.0)
        cct.record_infra_cost("aws", ResourceType.GPU_INSTANCE, "i-1", 30.0)

        assert cct.model_spend() == pytest.approx(20.0)
        assert cct.infra_spend() == pytest.approx(30.0)
        assert cct.get_total_spend() == pytest.approx(50.0)

    def test_zero_when_nothing_recorded(self) -> None:
        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600.0)
        it = InfraCostTracker()
        cct = CombinedCostTracker(spend_limiter=sl, infra_tracker=it)
        assert cct.get_total_spend() == 0.0

    def test_model_only_side(self) -> None:
        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600.0)
        cct = CombinedCostTracker(spend_limiter=sl)
        cct.record_model_cost(7.0)
        assert cct.get_total_spend() == pytest.approx(7.0)
        assert cct.infra_spend() == 0.0

    def test_infra_only_side(self) -> None:
        it = InfraCostTracker()
        cct = CombinedCostTracker(infra_tracker=it)
        cct.record_infra_cost("runpod", ResourceType.GPU_INSTANCE, "r-1", 9.0)
        assert cct.get_total_spend() == pytest.approx(9.0)
        assert cct.model_spend() == 0.0


class TestGetCostBreakdown:
    def test_full_breakdown_shape(self) -> None:
        def clock() -> float:
            return 1000.0

        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=clock)
        it = InfraCostTracker()
        cct = CombinedCostTracker(spend_limiter=sl, infra_tracker=it)

        cct.record_model_cost(12.0, project_id="proj-a", at=1000.0)
        cct.record_infra_cost(
            "aws", ResourceType.GPU_INSTANCE, "i-1", 8.0, project_id="proj-a"
        )

        bd = cct.get_cost_breakdown()

        # Required keys present (matches GET /api/costs shape).
        for key in (
            "model_api",
            "infrastructure",
            "total",
            "breakdown_by_provider",
            "breakdown_by_resource_type",
            "breakdown_by_project",
            "record_count",
        ):
            assert key in bd, f"missing key {key!r}"

        assert bd["model_api"] == pytest.approx(12.0)
        assert bd["infrastructure"] == pytest.approx(8.0)
        assert bd["total"] == pytest.approx(20.0)
        assert bd["breakdown_by_provider"]["aws"] == pytest.approx(8.0)
        assert bd["breakdown_by_resource_type"]["gpu_instance"] == pytest.approx(8.0)
        assert bd["record_count"] == 1

    def test_project_breakdown_merges_model_and_infra(self) -> None:
        """Same project_id on both sides must sum, not overwrite."""
        def clock() -> float:
            return 1000.0

        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=clock)
        it = InfraCostTracker()
        cct = CombinedCostTracker(spend_limiter=sl, infra_tracker=it)

        cct.record_model_cost(10.0, project_id="p1", at=1000.0)
        cct.record_infra_cost("aws", ResourceType.GPU_INSTANCE, "i-1", 25.0, project_id="p1")

        bd = cct.get_cost_breakdown()
        assert bd["breakdown_by_project"]["p1"] == pytest.approx(35.0)

    def test_project_breakdown_no_project_grouped_under_empty(self) -> None:
        def clock() -> float:
            return 1000.0

        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=clock)
        cct = CombinedCostTracker(spend_limiter=sl)
        cct.record_model_cost(5.0, at=1000.0)  # no project_id
        bd = cct.get_cost_breakdown()
        assert bd["breakdown_by_project"].get("", 0.0) == pytest.approx(5.0)

    def test_empty_breakdown_when_neither_configured(self) -> None:
        cct = CombinedCostTracker()
        bd = cct.get_cost_breakdown()
        assert bd == {
            "model_api": 0.0,
            "infrastructure": 0.0,
            "total": 0.0,
            "breakdown_by_provider": {},
            "breakdown_by_resource_type": {},
            "breakdown_by_project": {},
            "record_count": 0,
        }


# ── Cap enforcement passthrough ─────────────────────────────────────────


class TestCapEnforcementPassthrough:
    def test_remaining_model_budget_with_cap(self) -> None:
        def clock() -> float:
            return 1000.0

        sl = SpendLimiter(limit_usd=50.0, window_seconds=3600.0, clock=clock)
        cct = CombinedCostTracker(spend_limiter=sl)
        cct.record_model_cost(20.0, at=1000.0)
        assert cct.remaining_model_budget() == pytest.approx(30.0)

    def test_remaining_model_budget_inf_when_no_limiter(self) -> None:
        cct = CombinedCostTracker()
        assert cct.remaining_model_budget() == float("inf")

    def test_remaining_model_budget_inf_when_no_cap(self) -> None:
        sl = SpendLimiter(limit_usd=0.0, window_seconds=3600.0)  # no cap
        cct = CombinedCostTracker(spend_limiter=sl)
        assert cct.remaining_model_budget() == float("inf")

    def test_would_exceed_combined_uses_model_cap(self) -> None:
        def clock() -> float:
            return 1000.0

        sl = SpendLimiter(limit_usd=10.0, window_seconds=3600.0, clock=clock)
        cct = CombinedCostTracker(spend_limiter=sl)
        cct.record_model_cost(8.0, at=1000.0)
        assert cct.would_exceed_combined(3.0) is True
        assert cct.would_exceed_combined(1.0) is False

    def test_would_exceed_combined_false_when_no_limiter(self) -> None:
        cct = CombinedCostTracker()
        assert cct.would_exceed_combined(1e9) is False


# ── Snapshot ─────────────────────────────────────────────────────────────


class TestSnapshot:
    def test_snapshot_shape_with_both_sides(self) -> None:
        def clock() -> float:
            return 1000.0

        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=clock)
        it = InfraCostTracker()
        cct = CombinedCostTracker(spend_limiter=sl, infra_tracker=it)

        cct.record_model_cost(5.0, at=1000.0)
        cct.record_infra_cost("aws", ResourceType.GPU_INSTANCE, "i-1", 7.0)

        snap = cct.snapshot()
        assert "model_records" in snap
        assert "infra" in snap
        assert len(snap["model_records"]) == 1
        assert snap["model_records"][0][1] == pytest.approx(5.0)
        assert snap["infra"]["total_cost"] == pytest.approx(7.0)

    def test_snapshot_empty_when_neither_configured(self) -> None:
        cct = CombinedCostTracker()
        snap = cct.snapshot()
        assert snap == {"model_records": [], "infra": {}}


# ── Thread safety ────────────────────────────────────────────────────────


class TestThreadSafety:
    def test_concurrent_model_and_infra_records(self) -> None:
        """Delegates to underlying locks; no lost updates under contention."""
        def clock() -> float:
            return 1000.0

        sl = SpendLimiter(limit_usd=1e9, window_seconds=3600.0, clock=clock)
        it = InfraCostTracker()
        cct = CombinedCostTracker(spend_limiter=sl, infra_tracker=it)

        n_threads = 8
        per_thread = 100

        def worker() -> None:
            for _ in range(per_thread):
                cct.record_model_cost(0.01, at=1000.0)
                cct.record_infra_cost(
                    "aws", ResourceType.GPU_INSTANCE, "i", 0.01
                )

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        expected = n_threads * per_thread * 0.01
        assert cct.model_spend() == pytest.approx(expected)
        assert cct.infra_spend() == pytest.approx(expected)
        assert cct.get_total_spend() == pytest.approx(expected * 2)


# ── GET /api/costs wiring ────────────────────────────────────────────────


def _make_app() -> tuple[FastAPI, TestClient]:
    app = FastAPI()
    register_spend(app, {})
    return app, TestClient(app)


class TestApiCostsWiring:
    def test_endpoint_uses_combined_tracker_when_wired(self) -> None:
        """When app.state._combined_cost_tracker is set, /api/costs delegates to it."""
        app, client = _make_app()

        def clock() -> float:
            return 1000.0

        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=clock)
        it = InfraCostTracker()
        cct = CombinedCostTracker(spend_limiter=sl, infra_tracker=it)
        cct.record_model_cost(15.0, project_id="p1", at=1000.0)
        cct.record_infra_cost("aws", ResourceType.GPU_INSTANCE, "i-1", 25.0, project_id="p1")
        app.state._combined_cost_tracker = cct

        resp = client.get("/api/costs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["model_api"] == pytest.approx(15.0)
        assert data["infrastructure"] == pytest.approx(25.0)
        assert data["total"] == pytest.approx(40.0)
        assert data["breakdown_by_project"]["p1"] == pytest.approx(40.0)
        assert data["record_count"] == 1

    def test_endpoint_falls_back_when_no_combined_tracker(self) -> None:
        """Existing inline logic still works when _combined_cost_tracker is absent."""
        _app, client = _make_app()
        resp = client.get("/api/costs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == pytest.approx(0.0)
