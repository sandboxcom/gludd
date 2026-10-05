"""Deep receipt engine and cost tracking tests.

Covers the four-tracker receipt pipeline end-to-end:
  Accountant (ledger) → SpendLimiter (roll-window cap) → InfraCostTracker
  (cumulative infra) → CombinedCostTracker (unified facade)

Plus BudgetManager reservation/reconciliation, TokenCostTracker baselines,
and small-model download/quantize cost estimation.

All tests use injected fakes — no live daemon, no DB, no network.
"""

from __future__ import annotations

import math
from dataclasses import FrozenInstanceError, dataclass
from typing import cast

import pytest

from general_ludd.accounting.ledger import (
    Accountant,
    LocLedger,
    _finite_float,
    _finite_nonneg_int,
)
from general_ludd.budget.combined_cost import CombinedCostTracker
from general_ludd.controllers.budget_manager import BudgetManager, _is_uncomputable
from general_ludd.controllers.spend_limiter import SpendLimiter
from general_ludd.infra.cost_tracker import InfraCostRecord, InfraCostTracker
from general_ludd.observability.token_cost import (
    TokenCostTracker,
    TokenSample,
    TokenWeight,
)
from general_ludd.small_models import cost as small_cost

# ============================================================================
# Test helpers — fake record types for the Accountant injectable providers
# ============================================================================


@dataclass
class _FakeUsage:
    project_id: str
    tokens_used: int = 0
    usd_spent: float = 0.0
    elapsed_seconds: float = 0.0


@dataclass
class _FakeTodo:
    project_id: str
    status: str
    points: int = 0


@dataclass
class _FakeRoleRun:
    project_id: str
    role: str


def _fake_usage_provider(records: list[_FakeUsage]):
    def _get(pid: str):
        return [r for r in records if r.project_id == pid]

    return _get


def _fake_todo_provider(todos: list[_FakeTodo]):
    def _get(pid: str):
        return [t for t in todos if t.project_id == pid]

    return _get


def _fake_role_provider(runs: list[_FakeRoleRun]):
    def _get(pid: str):
        return [r for r in runs if r.project_id == pid]

    return _get


def _fake_loc_provider(loc_map: dict[str, int]):
    def _get(pid: str) -> int:
        return loc_map.get(pid, 0)

    return _get


def _fake_project_provider(pids: list[str]):
    def _get() -> list[str]:
        return pids

    return _get


# ============================================================================
# 1. Receipt Generation — Accountant produces full per-project snapshots
# ============================================================================


class TestReceiptGeneration:
    """Accountant.account_for() produces a complete "receipt" (ProjectAccounting)
    aggregating usage, todos, roles, and LOC for a single project."""

    def test_receipt_aggregates_usage_correctly(self):
        usage = [
            _FakeUsage("proj-x", tokens_used=1_200, usd_spent=0.84, elapsed_seconds=42.0),
            _FakeUsage("proj-x", tokens_used=800, usd_spent=0.56, elapsed_seconds=18.0),
        ]
        acct = Accountant(
            usage_provider=_fake_usage_provider(usage),
            todo_provider=_fake_todo_provider([]),
            role_provider=_fake_role_provider([]),
            loc_provider=_fake_loc_provider({}),
            project_provider=_fake_project_provider(["proj-x"]),
            quota_usd=200.0,
        )
        receipt = acct.account_for("proj-x")
        assert receipt.tokens_used == 2_000
        assert receipt.usd_spent == pytest.approx(1.40)
        assert receipt.elapsed_seconds == pytest.approx(60.0)
        assert receipt.pct_quota == pytest.approx(0.70)

    def test_receipt_includes_todo_line_items(self):
        todos = [
            _FakeTodo("p1", "done", 5),
            _FakeTodo("p1", "done", 3),
            _FakeTodo("p1", "in_progress", 2),
            _FakeTodo("p1", "pending", 1),
        ]
        acct = Accountant(
            usage_provider=_fake_usage_provider([]),
            todo_provider=_fake_todo_provider(todos),
            role_provider=_fake_role_provider([]),
            loc_provider=_fake_loc_provider({}),
            project_provider=_fake_project_provider(["p1"]),
        )
        receipt = acct.account_for("p1")
        assert receipt.todo_summary == {"done": 2, "in_progress": 1, "pending": 1}
        assert receipt.points_estimated == 11
        assert receipt.points_done == 8

    def test_receipt_includes_role_line_items(self):
        runs = [
            _FakeRoleRun("p1", "coder"),
            _FakeRoleRun("p1", "coder"),
            _FakeRoleRun("p1", "auditor"),
        ]
        acct = Accountant(
            usage_provider=_fake_usage_provider([]),
            todo_provider=_fake_todo_provider([]),
            role_provider=_fake_role_provider(runs),
            loc_provider=_fake_loc_provider({}),
            project_provider=_fake_project_provider(["p1"]),
        )
        receipt = acct.account_for("p1")
        assert receipt.role_stats == {"coder": 2, "auditor": 1}

    def test_receipt_includes_loc_changed(self):
        acct = Accountant(
            usage_provider=_fake_usage_provider([]),
            todo_provider=_fake_todo_provider([]),
            role_provider=_fake_role_provider([]),
            loc_provider=_fake_loc_provider({"p1": 347}),
            project_provider=_fake_project_provider(["p1"]),
        )
        receipt = acct.account_for("p1")
        assert receipt.loc_changed == 347

    def test_receipt_zero_quota_safe_pct(self):
        usage = [_FakeUsage("p1", usd_spent=50.0)]
        acct = Accountant(
            usage_provider=_fake_usage_provider(usage),
            todo_provider=_fake_todo_provider([]),
            role_provider=_fake_role_provider([]),
            loc_provider=_fake_loc_provider({}),
            project_provider=_fake_project_provider(["p1"]),
            quota_usd=0.0,
        )
        receipt = acct.account_for("p1")
        assert receipt.pct_quota == 0.0

    def test_receipt_over_budget_pct_exceeds_100(self):
        usage = [_FakeUsage("p1", usd_spent=250.0)]
        acct = Accountant(
            usage_provider=_fake_usage_provider(usage),
            todo_provider=_fake_todo_provider([]),
            role_provider=_fake_role_provider([]),
            loc_provider=_fake_loc_provider({}),
            project_provider=_fake_project_provider(["p1"]),
            quota_usd=100.0,
        )
        receipt = acct.account_for("p1")
        assert receipt.pct_quota == pytest.approx(250.0)

    def test_account_all_batches_receipts(self):
        usage = [
            _FakeUsage("a", tokens_used=10, usd_spent=1.0),
            _FakeUsage("b", tokens_used=20, usd_spent=2.0),
            _FakeUsage("c", tokens_used=30, usd_spent=3.0),
        ]
        acct = Accountant(
            usage_provider=_fake_usage_provider(usage),
            todo_provider=_fake_todo_provider([]),
            role_provider=_fake_role_provider([]),
            loc_provider=_fake_loc_provider({}),
            project_provider=_fake_project_provider(["a", "b", "c"]),
        )
        receipts = acct.account_all()
        assert len(receipts) == 3
        by_id = {r.project_id: r for r in receipts}
        assert by_id["a"].tokens_used == 10
        assert by_id["c"].tokens_used == 30


# ============================================================================
# 2. Cost Aggregation — CombinedCostTracker merges model + infra spend
# ============================================================================


class _FakeSpendLimiter:
    """Minimal fake capturing only the record() -> window_spend() loop."""

    def __init__(self, limit: float = 500.0, clock: float = 0.0) -> None:
        self.cap_configured: bool = limit > 0
        self._records: list[tuple[float, float, str | None]] = []
        self._now = clock

    def record(
        self,
        cost_usd: float,
        *,
        kind: str = "token",
        at: float | None = None,
        model: str | None = None,
        project_id: str | None = None,
    ) -> None:
        if cost_usd < 0 or not math.isfinite(cost_usd):
            raise ValueError("bad cost")
        self._records.append((at or self._now, cost_usd, project_id))

    def window_spend(self, *, now: float | None = None) -> float:
        return sum(c for _t, c, _p in self._records)

    def remaining(self, *, now: float | None = None) -> float:
        return max(0.0, 500.0 - self.window_spend(now=now))

    def would_exceed(self, projected: float, *, now: float | None = None) -> bool:
        return self.window_spend(now=now) + projected > 500.0

    def project_breakdown(self, *, now: float | None = None) -> dict[str, float]:
        bd: dict[str, float] = {}
        for _t, c, pid in self._records:
            k = pid or ""
            bd[k] = bd.get(k, 0.0) + c
        return bd

    def snapshot(self) -> list[dict[str, object]]:
        return [{"t": t, "c": c} for t, c, _p in self._records]


class _FakeInfraTracker:
    def __init__(self) -> None:
        self._total = 0.0
        self._by_provider: dict[str, float] = {}
        self._by_rt: dict[str, float] = {}
        self._by_proj: dict[str, float] = {}
        self._records: list[object] = []

    def record(self, provider: str, resource_type: str, resource_id: str, cost_usd: float, **kw: object) -> object:
        if cost_usd < 0 or not math.isfinite(cost_usd):
            raise ValueError("bad cost")
        self._total += cost_usd
        self._by_provider[provider] = self._by_provider.get(provider, 0.0) + cost_usd
        self._by_rt[resource_type] = self._by_rt.get(resource_type, 0.0) + cost_usd
        pid = kw.get("project_id")
        if isinstance(pid, str):
            self._by_proj[pid] = self._by_proj.get(pid, 0.0) + cost_usd
        rec = object()
        self._records.append(rec)
        return rec

    def total_cost(self) -> float:
        return self._total

    def cost_by_provider(self) -> dict[str, float]:
        return dict(self._by_provider)

    def cost_by_resource_type(self) -> dict[str, float]:
        return dict(self._by_rt)

    def cost_by_project(self) -> dict[str, float]:
        return dict(self._by_proj)

    def records(self) -> list[object]:
        return list(self._records)

    def snapshot(self) -> dict[str, object]:
        return {"total": self._total}


class TestCostAggregation:
    """CombinedCostTracker merges SpendLimiter (rolling model API) and
    InfraCostTracker (cumulative infra) into a single receipt surface."""

    def test_combined_total_sums_model_and_infra(self):
        model = _FakeSpendLimiter()
        model._records.append((0.0, 120.0, None))
        infra = _FakeInfraTracker()
        infra.record("aws", "gpu_instance", "i-1", 300.0)
        combined = CombinedCostTracker(
            spend_limiter=cast(SpendLimiter, model),
            infra_tracker=cast(InfraCostTracker, infra),
        )
        assert combined.get_total_spend() == pytest.approx(420.0)

    def test_breakdown_by_provider(self):
        infra = _FakeInfraTracker()
        infra.record("aws", "gpu_instance", "i-1", 100.0)
        infra.record("gcp", "gpu_instance", "i-2", 200.0)
        combined = CombinedCostTracker(
            infra_tracker=cast(InfraCostTracker, infra),
        )
        bd = combined.get_cost_breakdown()
        assert bd["breakdown_by_provider"]["aws"] == pytest.approx(100.0)
        assert bd["breakdown_by_provider"]["gcp"] == pytest.approx(200.0)

    def test_breakdown_by_resource_type(self):
        infra = _FakeInfraTracker()
        infra.record("aws", "gpu_instance", "i-1", 50.0)
        infra.record("aws", "storage", "vol-1", 10.0)
        combined = CombinedCostTracker(
            infra_tracker=cast(InfraCostTracker, infra),
        )
        bd = combined.get_cost_breakdown()
        assert bd["breakdown_by_resource_type"]["gpu_instance"] == pytest.approx(50.0)
        assert bd["breakdown_by_resource_type"]["storage"] == pytest.approx(10.0)

    def test_breakdown_by_project_merges_both_sides(self):
        model = _FakeSpendLimiter()
        model._records.append((0.0, 30.0, "proj-a"))
        infra = _FakeInfraTracker()
        infra.record("aws", "gpu_instance", "i-1", 70.0, project_id="proj-a")
        infra.record("gcp", "storage", "vol-1", 20.0, project_id="proj-b")
        combined = CombinedCostTracker(
            spend_limiter=cast(SpendLimiter, model),
            infra_tracker=cast(InfraCostTracker, infra),
        )
        bd = combined.get_cost_breakdown()
        assert bd["breakdown_by_project"]["proj-a"] == pytest.approx(100.0)
        assert bd["breakdown_by_project"]["proj-b"] == pytest.approx(20.0)

    def test_no_spend_limiter_reports_zero_model(self):
        infra = _FakeInfraTracker()
        infra.record("aws", "gpu_instance", "i-1", 42.0)
        combined = CombinedCostTracker(
            infra_tracker=cast(InfraCostTracker, infra),
        )
        assert combined.model_spend() == 0.0
        assert combined.get_total_spend() == pytest.approx(42.0)

    def test_no_infra_tracker_reports_zero_infra(self):
        model = _FakeSpendLimiter()
        model._records.append((0.0, 88.0, None))
        combined = CombinedCostTracker(spend_limiter=cast(SpendLimiter, model))
        assert combined.infra_spend() == 0.0
        assert combined.get_total_spend() == pytest.approx(88.0)

    def test_record_model_cost_raises_without_limiter(self):
        combined = CombinedCostTracker()
        with pytest.raises(RuntimeError, match="no SpendLimiter"):
            combined.record_model_cost(1.0)

    def test_record_infra_cost_raises_without_tracker(self):
        combined = CombinedCostTracker()
        with pytest.raises(RuntimeError, match="no InfraCostTracker"):
            combined.record_infra_cost("aws", "gpu_instance", "i-1", 1.0)

    def test_snapshot_includes_both_sides(self):
        model = _FakeSpendLimiter()
        infra = _FakeInfraTracker()
        infra.record("aws", "gpu_instance", "i-1", 55.0)
        combined = CombinedCostTracker(
            spend_limiter=cast(SpendLimiter, model),
            infra_tracker=cast(InfraCostTracker, infra),
        )
        snap = combined.snapshot()
        assert "model_records" in snap
        assert "infra" in snap
        assert snap["infra"]["total"] == pytest.approx(55.0)

    def test_remaining_model_budget(self):
        model = _FakeSpendLimiter(limit=500.0)
        model._records.append((0.0, 300.0, None))
        combined = CombinedCostTracker(spend_limiter=cast(SpendLimiter, model))
        assert combined.remaining_model_budget() == pytest.approx(200.0)

    def test_would_exceed_combined_delegates(self):
        model = _FakeSpendLimiter(limit=500.0)
        model._records.append((0.0, 480.0, None))
        combined = CombinedCostTracker(spend_limiter=cast(SpendLimiter, model))
        assert combined.would_exceed_combined(30.0) is True  # 480+30=510 >500
        assert combined.would_exceed_combined(20.0) is False  # 480+20=500 ==500


# ============================================================================
# 3. Billing Period — SpendLimiter rolling-window expiry + snapshot/restore
# ============================================================================


class TestBillingPeriod:
    """SpendLimiter enforces a rolling-window billing period — old records
    expire, new records push the cap, and snapshot/restore survives restarts."""

    def test_window_prunes_expired_records(self):
        clock = [200.0]
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=60.0, clock=lambda: clock[0])
        limiter.record(10.0, kind="token", at=100.0)
        limiter.record(20.0, kind="token", at=130.0)
        limiter.record(30.0, kind="token", at=150.0)
        # At now=200, cutoff=140: only the 150 record survives
        assert limiter.window_spend(now=200.0) == pytest.approx(30.0)

    def test_snapshot_restore_roundtrip(self):
        WINDOW = 3600.0
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=WINDOW, clock=lambda: 6000.0)
        limiter.record(10.0, kind="token", at=3000.0)
        limiter.record(30.0, kind="token", at=4000.0, project_id="p-x")
        snap = limiter.snapshot()
        assert len(snap) == 2

        restored = SpendLimiter(limit_usd=100.0, window_seconds=WINDOW, clock=lambda: 6000.0)
        restored.restore(snap)
        assert restored.window_spend(now=6000.0) == pytest.approx(40.0)
        assert restored.project_spend("p-x", now=6000.0) == pytest.approx(30.0)

    def test_restore_drops_negative_costs(self):
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=lambda: 5000.0)
        limiter.restore([(1000.0, -50.0), (2000.0, 30.0)])
        assert limiter.window_spend(now=5000.0) == pytest.approx(30.0)

    def test_restore_clamps_future_timestamps(self):
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=lambda: 5000.0)
        limiter.restore([(99999.0, 40.0)])
        assert limiter.window_spend(now=5000.0) == pytest.approx(40.0)

    def test_unflushed_records_and_flush_watermark(self):
        clock = [1000.0]
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=lambda: clock[0])
        limiter.record(5.0, kind="token", at=1000.0)
        limiter.record(15.0, kind="token", at=1000.0)
        unflushed = limiter.unflushed_records()
        assert len(unflushed) == 2
        limiter.mark_flushed(unflushed[-1][0])
        assert len(limiter.unflushed_records()) == 0

    def test_spend_in_last_seconds_subwindow(self):
        clock = [1000.0]
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=lambda: clock[0])
        limiter.record(10.0, kind="token", at=100.0)
        limiter.record(20.0, kind="token", at=900.0)
        limiter.record(30.0, kind="token", at=960.0)
        # Last 50s from 1000: cutoff=950, only the 960 record survives
        assert limiter.spend_in_last_seconds(50.0, now=1000.0) == pytest.approx(30.0)

    def test_project_breakdown_aggregates_correctly(self):
        clock = [1000.0]
        limiter = SpendLimiter(limit_usd=1000.0, window_seconds=3600.0, clock=lambda: clock[0])
        limiter.record(10.0, kind="token", at=500.0, project_id="a")
        limiter.record(20.0, kind="token", at=600.0, project_id="a")
        limiter.record(5.0, kind="token", at=700.0, project_id="b")
        bd = limiter.project_breakdown(now=1000.0)
        assert bd["a"] == pytest.approx(30.0)
        assert bd["b"] == pytest.approx(5.0)


# ============================================================================
# 4. Line Item Tracking — InfraCostTracker per-record accumulation
# ============================================================================


class TestLineItemTracking:
    """InfraCostTracker accumulates per-provider, per-resource-type, and
    per-project line items with full record history."""

    def test_record_returns_infra_cost_record(self):
        tracker = InfraCostTracker()
        rec = tracker.record("aws", "gpu_instance", "i-abc", 12.50)
        assert isinstance(rec, InfraCostRecord)
        assert rec.provider == "aws"
        assert rec.resource_type == "gpu_instance"
        assert rec.resource_id == "i-abc"
        assert rec.cost_usd == pytest.approx(12.50)

    def test_tracks_per_provider_costs(self):
        tracker = InfraCostTracker()
        tracker.record("aws", "gpu_instance", "i-1", 100.0)
        tracker.record("gcp", "gpu_instance", "i-2", 50.0)
        tracker.record("aws", "storage", "vol-1", 25.0)
        by_prov = tracker.cost_by_provider()
        assert by_prov["aws"] == pytest.approx(125.0)
        assert by_prov["gcp"] == pytest.approx(50.0)

    def test_tracks_per_resource_type_costs(self):
        tracker = InfraCostTracker()
        tracker.record("aws", "gpu_instance", "i-1", 100.0)
        tracker.record("gcp", "storage", "vol-1", 20.0)
        tracker.record("azure", "network", "vnet-1", 5.0)
        by_rt = tracker.cost_by_resource_type()
        assert by_rt["gpu_instance"] == pytest.approx(100.0)
        assert by_rt["storage"] == pytest.approx(20.0)
        assert by_rt["network"] == pytest.approx(5.0)

    def test_tracks_per_project_costs(self):
        tracker = InfraCostTracker()
        tracker.record("aws", "gpu_instance", "i-1", 70.0, project_id="alpha")
        tracker.record("gcp", "gpu_instance", "i-2", 30.0, project_id="beta")
        tracker.record("aws", "storage", "vol-1", 15.0, project_id="alpha")
        by_proj = tracker.cost_by_project()
        assert by_proj["alpha"] == pytest.approx(85.0)
        assert by_proj["beta"] == pytest.approx(30.0)

    def test_records_list_preserves_all_entries(self):
        tracker = InfraCostTracker()
        tracker.record("aws", "gpu_instance", "i-1", 10.0)
        tracker.record("gcp", "cpu_instance", "i-2", 5.0)
        assert len(tracker.records()) == 2

    def test_provider_breakdown_by_resource_type(self):
        tracker = InfraCostTracker()
        tracker.record("aws", "gpu_instance", "i-1", 100.0)
        tracker.record("aws", "storage", "vol-1", 20.0)
        tracker.record("gcp", "gpu_instance", "i-2", 40.0)
        aws_bd = tracker.provider_breakdown("aws")
        assert aws_bd["gpu_instance"] == pytest.approx(100.0)
        assert aws_bd["storage"] == pytest.approx(20.0)

    def test_record_negative_cost_raises(self):
        tracker = InfraCostTracker()
        with pytest.raises(ValueError):
            tracker.record("aws", "gpu_instance", "i-1", -1.0)

    def test_record_by_duration_computes_cost(self):
        tracker = InfraCostTracker()
        tracker.hourly_rate_usd = lambda provider, sku, spot=False: 2.50  # type: ignore[method-assign]
        rec = tracker.record_by_duration("aws", "p4d.24xlarge", 4.0)
        assert rec.cost_usd == pytest.approx(10.00)

    def test_snapshot_is_serializable(self):
        tracker = InfraCostTracker()
        tracker.record("aws", "gpu_instance", "i-1", 42.0)
        snap = tracker.snapshot()
        assert snap["total_cost"] == pytest.approx(42.0)
        assert snap["record_count"] == 1
        assert "by_provider" in snap
        assert "by_resource_type" in snap
        assert "by_project" in snap


# ============================================================================
# 5. Currency Handling — BudgetManager reservation/reconciliation
# ============================================================================


class TestCurrencyHandling:
    """BudgetManager uses reservation/reconciliation to avoid double-counting
    projected-vs-actual costs. Tests the atomic check-and-hold pattern."""

    def test_record_spend_reconciles_todo_reservation(self):
        mgr = BudgetManager(per_todo_limit_usd=10.0)
        mgr.check_todo_budget("t1", 7.0)
        mgr.record_spend("t1", 9.0)
        status = mgr.get_status()
        assert status["paused"] is False

    def test_record_spend_without_reservation_adds_plain(self):
        mgr = BudgetManager(per_todo_limit_usd=10.0)
        mgr.record_spend("t2", 3.0)
        mgr.record_spend("t2", 2.0)
        assert mgr._todo_spend["t2"] == pytest.approx(5.0)

    def test_release_reservation_frees_budget(self):
        mgr = BudgetManager(per_todo_limit_usd=10.0)
        mgr.check_todo_budget("t3", 6.0)
        mgr.release_reservation("t3")
        assert mgr._todo_spend.get("t3", 0.0) == pytest.approx(0.0)

    def test_daily_budget_kill_switch(self):
        mgr = BudgetManager(daily_limit_usd=10.0)
        result = mgr.check_daily_budget(9.0)
        assert result["allowed"] is True
        result2 = mgr.check_daily_budget(2.0)
        assert result2["allowed"] is False
        assert "budget exceeded" in str(result2["reason"])
        result3 = mgr.check_daily_budget(0.01)
        assert result3["allowed"] is False
        assert result3["reason"] == "budget_exhausted"

    def test_daily_budget_reservation_with_reconciliation(self):
        mgr = BudgetManager(daily_limit_usd=50.0)
        result = mgr.check_daily_budget_reserved("t4", 30.0)
        assert result["allowed"] is True
        # Re-check same todo with lower cost replaces, not stacks
        result2 = mgr.check_daily_budget_reserved("t4", 20.0)
        assert result2["allowed"] is True
        # Record actual vs reserved
        mgr.record_spend("t4", 22.0)
        assert mgr._daily_spend == pytest.approx(22.0)

    def test_uncomputable_cost_fails_closed(self):
        assert _is_uncomputable(float("nan")) is True
        assert _is_uncomputable(50.0) is False
        assert _is_uncomputable(float("inf")) is False

    def test_estimate_call_cost_delegates(self):
        mgr = BudgetManager()
        cost = mgr.estimate_call_cost(5000, 0.002)
        assert cost == pytest.approx(0.01)

    def test_get_status_defaults(self):
        mgr = BudgetManager()
        status = mgr.get_status()
        assert status["paused"] is False
        assert status["daily_limit"] == float("inf")


# ============================================================================
# 6. TokenCostTracker — per-task-kind token baselines
# ============================================================================


class TestTokenCostBaselines:
    """TokenCostTracker learns per-key token profiles from billed calls."""

    def test_records_and_computes_weight(self):
        tracker = TokenCostTracker(min_samples=2)
        tracker.record("audit", 100, 200)
        tracker.record("audit", 120, 250)
        w = tracker.weight("audit")
        assert w is not None
        assert w.samples == 2
        assert w.median_input == pytest.approx(110.0)
        assert w.median_output == pytest.approx(225.0)

    def test_weight_none_before_min_samples(self):
        tracker = TokenCostTracker(min_samples=5)
        tracker.record("audit", 100, 200)
        assert tracker.weight("audit") is None

    def test_heaviest_ranks_by_median_total(self):
        tracker = TokenCostTracker(min_samples=2)
        tracker.record("light", 10, 20)
        tracker.record("light", 12, 18)
        tracker.record("heavy", 500, 500)
        tracker.record("heavy", 600, 400)
        tracker.record("medium", 100, 100)
        tracker.record("medium", 120, 80)
        top = tracker.heaviest()
        assert len(top) == 3
        assert top[0].key == "heavy"
        assert top[-1].key == "light"

    def test_classify_adapts_to_relative_scale(self):
        tracker = TokenCostTracker(min_samples=2, heavy_factor=2.0)
        tracker.record("light", 10, 10)
        tracker.record("light", 20, 10)
        tracker.record("medium", 200, 200)
        tracker.record("medium", 180, 220)
        tracker.record("heavy", 800, 800)
        tracker.record("heavy", 1000, 600)
        assert tracker.classify("heavy") == "heavy"
        assert tracker.classify("medium") == "moderate"
        assert tracker.classify("light") == "light"

    def test_classify_unknown_for_untrusted_key(self):
        tracker = TokenCostTracker(min_samples=3)
        tracker.record("x", 10, 20)
        assert tracker.classify("x") == "unknown"

    def test_token_sample_total(self):
        s = TokenSample(100, 200)
        assert s.total == 300

    def test_token_weight_immutable(self):
        w = TokenWeight(key="k", samples=10, median_input=5.0, median_output=7.0, median_total=12.0)
        with pytest.raises(FrozenInstanceError):
            w.key = "other"  # type: ignore[misc]


# ============================================================================
# 7. Sanitization helpers
# ============================================================================


class TestSanitization:
    def test_finite_float_clamps_nan(self):
        assert _finite_float(float("nan")) == 0.0

    def test_finite_float_clamps_negative(self):
        assert _finite_float(-5.0) == 0.0

    def test_finite_float_passes_finite_positive(self):
        assert _finite_float(3.14) == pytest.approx(3.14)

    def test_finite_nonneg_int_clamps_string(self):
        assert _finite_nonneg_int("garbage") == 0

    def test_finite_nonneg_int_passes_int(self):
        assert _finite_nonneg_int(42) == 42


# ============================================================================
# 8. LocLedger — cumulative per-project lines-of-code
# ============================================================================


class TestLocLedgerDeep:
    def test_record_accumulates(self):
        ll = LocLedger()
        assert ll.record_loc_changed("p", 10) == 10
        assert ll.record_loc_changed("p", 5) == 15

    def test_negative_delta_clamped(self):
        ll = LocLedger()
        assert ll.record_loc_changed("p", -3) == 0

    def test_as_provider_binds_to_total(self):
        ll = LocLedger()
        ll.record_loc_changed("p", 7)
        fn = ll.as_provider()
        assert fn("p") == 7
        ll.record_loc_changed("p", 3)
        assert fn("p") == 10

    def test_snapshot_is_independent_copy(self):
        ll = LocLedger()
        ll.record_loc_changed("p", 20)
        snap = ll.snapshot()
        snap["p"] = 999
        assert ll.total("p") == 20


# ============================================================================
# 9. Small-model cost estimation — download / quantize / inference
# ============================================================================


class TestSmallModelCosts:
    def test_estimate_inference_cost_returns_keys(self):
        info = small_cost.estimate_inference_cost("phi-2")
        for k in ("model_id", "tier", "input_usd_per_1m_tokens", "output_usd_per_1m_tokens", "estimated_usd_per_hour"):
            assert k in info

    def test_estimate_download_cost_computes_transfer(self):
        info = small_cost.estimate_download_cost("phi-2")
        assert info["data_transfer_usd"] == pytest.approx(2.7 * 0.09)

    def test_estimate_quantize_cost_uses_method_rate(self):
        info = small_cost.estimate_quantize_cost("phi-2", 4.0, "q4_k_m")
        assert info["estimated_gpu_hours"] == pytest.approx(4.0 * 0.2)

    def test_is_off_peak_detects_weekend(self):
        from datetime import UTC, datetime

        saturday = datetime(2026, 8, 1, 14, 0, 0, tzinfo=UTC)
        assert small_cost.is_off_peak(saturday) is True

    def test_should_defer_large_download_during_peak(self):
        from datetime import UTC, datetime

        tuesday_noon = datetime(2026, 8, 4, 12, 0, 0, tzinfo=UTC)
        calls = 0

        def clock():
            nonlocal calls
            calls += 1
            return tuesday_noon

        result = small_cost.should_defer_download(
            2.5,
            threshold_gb=2.0,
            clock=clock,
        )
        assert result["defer"] is True
        assert calls == 1

    def test_compute_cost_score_bounded_0_to_1(self):
        score = small_cost.compute_cost_score("phi-2")
        assert 0.0 <= score <= 1.0
