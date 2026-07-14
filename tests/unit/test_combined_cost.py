"""Structural tests for budget/combined_cost.py — CombinedCostTracker facade."""

from __future__ import annotations

import pytest

from general_ludd.budget.combined_cost import CombinedCostTracker


class _FakeSpendLimiter:
    def __init__(self) -> None:
        self.cap_configured = True
        self._records: list[tuple[float, str | None, str | None, str, float | None]] = []

    def record(
        self,
        cost_usd: float,
        *,
        kind: str = "token",
        at: float | None = None,
        model: str | None = None,
        project_id: str | None = None,
    ) -> None:
        if cost_usd < 0:
            raise ValueError("negative cost")
        self._records.append((cost_usd, kind, model, project_id, at))

    def window_spend(self, *, now: float | None = None) -> float:
        return 10.0

    def remaining(self, *, now: float | None = None) -> float:
        return 90.0

    def would_exceed(self, projected: float, *, now: float | None = None) -> bool:
        return projected > 90.0

    def project_breakdown(self, *, now: float | None = None) -> dict[str, float]:
        return {"proj-1": 5.0, "proj-2": 5.0}

    def snapshot(self) -> list[dict[str, object]]:
        return [{"cost": 5.0}]


class _FakeInfraTracker:
    def __init__(self) -> None:
        self._records: list[object] = []

    def record(
        self,
        provider: str,
        resource_type: str,
        resource_id: str,
        cost_usd: float,
        **kwargs: object,
    ) -> object:
        if cost_usd < 0:
            raise ValueError("negative cost")
        rec = _FakeInfraRecord(provider, resource_type, resource_id, cost_usd)
        self._records.append(rec)
        return rec

    def total_cost(self) -> float:
        return 50.0

    def cost_by_provider(self) -> dict[str, float]:
        return {"aws": 30.0, "gcp": 20.0}

    def cost_by_resource_type(self) -> dict[str, float]:
        return {"compute": 50.0}

    def cost_by_project(self) -> dict[str, float]:
        return {"proj-1": 50.0}

    def records(self) -> list[object]:
        return self._records

    def snapshot(self) -> dict[str, object]:
        return {"total": 50.0}


class _FakeInfraRecord:
    def __init__(self, provider: str, resource_type: str, resource_id: str, cost: float) -> None:
        self.provider = provider
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.cost_usd = cost


class TestCombinedCostTrackerConstruction:
    def test_both_none(self) -> None:
        tracker = CombinedCostTracker()
        assert tracker.has_model is False
        assert tracker.has_infra is False

    def test_model_only(self) -> None:
        sl = _FakeSpendLimiter()
        tracker = CombinedCostTracker(spend_limiter=sl)
        assert tracker.has_model is True
        assert tracker.has_infra is False

    def test_infra_only(self) -> None:
        it = _FakeInfraTracker()
        tracker = CombinedCostTracker(infra_tracker=it)
        assert tracker.has_model is False
        assert tracker.has_infra is True

    def test_both_wired(self) -> None:
        sl = _FakeSpendLimiter()
        it = _FakeInfraTracker()
        tracker = CombinedCostTracker(spend_limiter=sl, infra_tracker=it)
        assert tracker.has_model is True
        assert tracker.has_infra is True

    def test_exposes_underlying_trackers(self) -> None:
        sl = _FakeSpendLimiter()
        tracker = CombinedCostTracker(spend_limiter=sl)
        assert tracker.spend_limiter is sl
        assert tracker.infra_tracker is None


class TestRecording:
    def test_record_model_cost_when_none_raises(self) -> None:
        tracker = CombinedCostTracker()
        with pytest.raises(RuntimeError, match="no SpendLimiter"):
            tracker.record_model_cost(5.0)

    def test_record_model_cost_delegates(self) -> None:
        sl = _FakeSpendLimiter()
        tracker = CombinedCostTracker(spend_limiter=sl)
        tracker.record_model_cost(5.0, model="gpt-4")
        assert len(sl._records) == 1
        assert sl._records[0][0] == 5.0
        assert sl._records[0][2] == "gpt-4"

    def test_record_infra_cost_when_none_raises(self) -> None:
        tracker = CombinedCostTracker()
        with pytest.raises(RuntimeError, match="no InfraCostTracker"):
            tracker.record_infra_cost("aws", "compute", "i-123", 10.0)

    def test_record_infra_cost_delegates(self) -> None:
        it = _FakeInfraTracker()
        tracker = CombinedCostTracker(infra_tracker=it)
        rec = tracker.record_infra_cost("aws", "compute", "i-123", 10.0)
        assert rec.cost_usd == 10.0
        assert len(it._records) == 1


class TestQueries:
    def test_model_spend_returns_zero_when_none(self) -> None:
        tracker = CombinedCostTracker()
        assert tracker.model_spend() == 0.0

    def test_model_spend_delegates(self) -> None:
        tracker = CombinedCostTracker(spend_limiter=_FakeSpendLimiter())
        assert tracker.model_spend() == 10.0

    def test_infra_spend_returns_zero_when_none(self) -> None:
        tracker = CombinedCostTracker()
        assert tracker.infra_spend() == 0.0

    def test_infra_spend_delegates(self) -> None:
        tracker = CombinedCostTracker(infra_tracker=_FakeInfraTracker())
        assert tracker.infra_spend() == 50.0

    def test_get_total_spend_combines(self) -> None:
        tracker = CombinedCostTracker(
            spend_limiter=_FakeSpendLimiter(),
            infra_tracker=_FakeInfraTracker(),
        )
        assert tracker.get_total_spend() == 60.0

    def test_get_total_spend_model_only(self) -> None:
        tracker = CombinedCostTracker(spend_limiter=_FakeSpendLimiter())
        assert tracker.get_total_spend() == 10.0


class TestCostBreakdown:
    def test_breakdown_keys(self) -> None:
        tracker = CombinedCostTracker(
            spend_limiter=_FakeSpendLimiter(),
            infra_tracker=_FakeInfraTracker(),
        )
        bd = tracker.get_cost_breakdown()
        assert bd.keys() == {
            "model_api", "infrastructure", "total",
            "breakdown_by_provider", "breakdown_by_resource_type",
            "breakdown_by_project", "record_count",
        }

    def test_breakdown_values(self) -> None:
        tracker = CombinedCostTracker(
            spend_limiter=_FakeSpendLimiter(),
            infra_tracker=_FakeInfraTracker(),
        )
        bd = tracker.get_cost_breakdown()
        assert bd["model_api"] == 10.0
        assert bd["infrastructure"] == 50.0
        assert bd["total"] == 60.0

    def test_breakdown_none_trackers(self) -> None:
        tracker = CombinedCostTracker()
        bd = tracker.get_cost_breakdown()
        assert bd["model_api"] == 0.0
        assert bd["infrastructure"] == 0.0
        assert bd["total"] == 0.0
        assert bd["record_count"] == 0


class TestCapEnforcement:
    def test_remaining_budget_inf_when_no_limiter(self) -> None:
        tracker = CombinedCostTracker()
        assert tracker.remaining_model_budget() == float("inf")

    def test_remaining_budget_delegates(self) -> None:
        tracker = CombinedCostTracker(spend_limiter=_FakeSpendLimiter())
        assert tracker.remaining_model_budget() == 90.0

    def test_would_exceed_false_when_no_limiter(self) -> None:
        tracker = CombinedCostTracker()
        assert tracker.would_exceed_combined(999.0) is False

    def test_would_exceed_delegates(self) -> None:
        tracker = CombinedCostTracker(spend_limiter=_FakeSpendLimiter())
        assert tracker.would_exceed_combined(100.0) is True
        assert tracker.would_exceed_combined(50.0) is False


class TestSnapshot:
    def test_snapshot_structure(self) -> None:
        tracker = CombinedCostTracker(
            spend_limiter=_FakeSpendLimiter(),
            infra_tracker=_FakeInfraTracker(),
        )
        snap = tracker.snapshot()
        assert "model_records" in snap
        assert "infra" in snap

    def test_snapshot_none_trackers(self) -> None:
        tracker = CombinedCostTracker()
        snap = tracker.snapshot()
        assert snap["model_records"] == []
        assert snap["infra"] == {}


class TestRepr:
    def test_repr_includes_state(self) -> None:
        tracker = CombinedCostTracker(
            spend_limiter=_FakeSpendLimiter(),
            infra_tracker=_FakeInfraTracker(),
        )
        r = repr(tracker)
        assert "CombinedCostTracker" in r
        assert "has_model=True" in r
        assert "has_infra=True" in r
