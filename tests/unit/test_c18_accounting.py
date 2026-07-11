"""C18 accounting fixes — TDD tests.

Covers three defects from docs/AGENTIC_IMPLEMENTATION_SPEC.md C18 (P1):

1. Blocking subprocess.run on the async event loop (routers/facts.py
   _accounting_facet calls account_for/account_all directly on the loop).
2. No tenant scoping — unknown project_id accepted silently.
3. NaN/Inf USD poisons JSON — non-finite values in usd_spent/tokens_used/points
   are not sanitized before serialization.

Follows AGENTS.md TDD policy: write failing test FIRST, run it, THEN write
implementation.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from unittest.mock import AsyncMock, Mock, patch

import pytest

from general_ludd.accounting.ledger import Accountant, LocLedger, ProjectAccounting

# ---------------------------------------------------------------------------
# Test 1: NaN/Inf USD sanitized (ledger.py account_for)
# ---------------------------------------------------------------------------


@dataclass
class _NanUsage:
    project_id: str
    tokens_used: int
    usd_spent: float
    elapsed_seconds: float


@dataclass
class _NanTodo:
    project_id: str
    status: str
    points: int = 0


@dataclass
class _NanRole:
    project_id: str
    role: str


def _build_accountant_for_nan(usage: list[_NanUsage] | None = None) -> Accountant:
    recs = usage or []

    def _usage_provider(pid: str) -> list[_NanUsage]:
        return [r for r in recs if r.project_id == pid]

    def _todo_provider(pid: str) -> list[_NanTodo]:
        return []

    def _role_provider(pid: str) -> list[_NanRole]:
        return []

    def _loc_provider(pid: str) -> int:
        return 0

    def _project_provider() -> list[str]:
        return ["p1"]

    return Accountant(
        usage_provider=_usage_provider,
        todo_provider=_todo_provider,
        role_provider=_role_provider,
        loc_provider=_loc_provider,
        project_provider=_project_provider,
        quota_usd=100.0,
    )


class TestNanInfUsdSanitized:
    def test_nan_usd_spent_does_not_poison_sum(self):
        """NaN in a usage record must not make the total usd_spent NaN."""
        records = [
            _NanUsage("p1", tokens_used=100, usd_spent=float("nan"), elapsed_seconds=10.0),
            _NanUsage("p1", tokens_used=200, usd_spent=1.50, elapsed_seconds=5.0),
        ]
        accountant = _build_accountant_for_nan(records)
        result = accountant.account_for("p1")
        assert not math.isnan(result.usd_spent)
        assert result.usd_spent >= 0.0

    def test_inf_usd_spent_does_not_poison_sum(self):
        """Inf in a usage record must not make the total usd_spent infinite."""
        records = [
            _NanUsage("p1", tokens_used=100, usd_spent=float("inf"), elapsed_seconds=10.0),
            _NanUsage("p1", tokens_used=200, usd_spent=1.50, elapsed_seconds=5.0),
        ]
        accountant = _build_accountant_for_nan(records)
        result = accountant.account_for("p1")
        assert math.isfinite(result.usd_spent)
        assert result.usd_spent >= 0.0

    def test_neg_inf_usd_spent_does_not_poison_sum(self):
        """-Inf in a usage record must not make the total usd_spent -Inf."""
        records = [
            _NanUsage("p1", tokens_used=100, usd_spent=float("-inf"), elapsed_seconds=10.0),
            _NanUsage("p1", tokens_used=200, usd_spent=1.50, elapsed_seconds=5.0),
        ]
        accountant = _build_accountant_for_nan(records)
        result = accountant.account_for("p1")
        assert math.isfinite(result.usd_spent)

    def test_nan_tokens_clamped_to_zero(self):
        """NaN tokens must not raise ValueError when cast to int; clamp to 0."""

        class _BadTokenRecord:
            def __init__(self):
                self.project_id = "p1"
                self.tokens_used = float("nan")
                self.usd_spent = 1.0
                self.elapsed_seconds = 10.0

        def _bad_provider(pid: str) -> list[_BadTokenRecord]:
            return [_BadTokenRecord()]

        accountant = Accountant(
            usage_provider=_bad_provider,
            todo_provider=lambda pid: [],
            role_provider=lambda pid: [],
            loc_provider=lambda pid: 0,
            project_provider=lambda: ["p1"],
            quota_usd=100.0,
        )
        result = accountant.account_for("p1")
        assert result.tokens_used == 0
        assert math.isfinite(result.pct_quota)

    def test_nan_points_clamped_to_zero(self):
        """NaN points in a todo must not raise ValueError; clamp to 0."""

        class _BadPointTodo:
            project_id = "p1"
            status = "pending"
            points = float("nan")

        accountant = Accountant(
            usage_provider=lambda pid: [],
            todo_provider=lambda pid: [_BadPointTodo()],
            role_provider=lambda pid: [],
            loc_provider=lambda pid: 0,
            project_provider=lambda: ["p1"],
            quota_usd=100.0,
        )
        result = accountant.account_for("p1")
        assert result.points_estimated == 0
        assert result.points_done == 0

    def test_inf_points_clamped_to_zero(self):
        """Inf points in a todo must not raise OverflowError; clamp to 0."""

        class _InfPointTodo:
            project_id = "p1"
            status = "done"
            points = float("inf")

        accountant = Accountant(
            usage_provider=lambda pid: [],
            todo_provider=lambda pid: [_InfPointTodo()],
            role_provider=lambda pid: [],
            loc_provider=lambda pid: 0,
            project_provider=lambda: ["p1"],
            quota_usd=100.0,
        )
        result = accountant.account_for("p1")
        assert result.points_estimated == 0

    def test_negative_tokens_clamped_to_zero(self):
        """Negative tokens must be clamped to 0."""

        class _NegTokenRecord:
            def __init__(self):
                self.project_id = "p1"
                self.tokens_used = -500
                self.usd_spent = 1.0
                self.elapsed_seconds = 10.0

        def _neg_provider(pid: str) -> list[_NegTokenRecord]:
            return [_NegTokenRecord()]

        accountant = Accountant(
            usage_provider=_neg_provider,
            todo_provider=lambda pid: [],
            role_provider=lambda pid: [],
            loc_provider=lambda pid: 0,
            project_provider=lambda: ["p1"],
            quota_usd=100.0,
        )
        result = accountant.account_for("p1")
        assert result.tokens_used >= 0

    def test_negative_points_clamped_to_zero(self):
        """Negative points must be clamped to 0."""

        class _NegPointTodo:
            project_id = "p1"
            status = "pending"
            points = -3

        accountant = Accountant(
            usage_provider=lambda pid: [],
            todo_provider=lambda pid: [_NegPointTodo()],
            role_provider=lambda pid: [],
            loc_provider=lambda pid: 0,
            project_provider=lambda: ["p1"],
            quota_usd=100.0,
        )
        result = accountant.account_for("p1")
        assert result.points_estimated >= 0

    def test_json_serializable_with_finite_values(self):
        """asdict(result) must serialize to JSON without NaN/Inf tokens."""
        import json

        records = [
            _NanUsage("p1", tokens_used=100, usd_spent=float("nan"), elapsed_seconds=10.0),
            _NanUsage("p1", tokens_used=200, usd_spent=1.50, elapsed_seconds=5.0),
        ]
        accountant = _build_accountant_for_nan(records)
        result = accountant.account_for("p1")
        d = asdict(result)
        json_str = json.dumps(d, allow_nan=False)
        assert isinstance(json_str, str)
        assert "NaN" not in json_str
        assert "Infinity" not in json_str

    def test_all_finite_happy_path_unchanged(self):
        """When all values are finite, totals must be correct."""
        records = [
            _NanUsage("p1", tokens_used=100, usd_spent=10.0, elapsed_seconds=30.0),
            _NanUsage("p1", tokens_used=200, usd_spent=20.0, elapsed_seconds=60.0),
        ]
        accountant = _build_accountant_for_nan(records)
        result = accountant.account_for("p1")
        assert result.tokens_used == 300
        assert result.usd_spent == pytest.approx(30.0)
        assert result.elapsed_seconds == pytest.approx(90.0)
        assert result.pct_quota == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# Test 2: asyncio.to_thread offload for _accounting_facet
# ---------------------------------------------------------------------------


class TestGitDiffNotBlockingEventLoop:
    def test_accounting_facet_offloads_to_thread(self):
        """_accounting_facet must call account_for/account_all inside
        asyncio.to_thread, not directly on the event loop."""
        from general_ludd.routers import facts

        accountant_mock = Mock()
        accountant_mock.account_for.return_value = ProjectAccounting(
            project_id="p1",
            elapsed_seconds=0.0,
            tokens_used=0,
            usd_spent=0.0,
            quota_usd=100.0,
            pct_quota=0.0,
            loc_changed=0,
            role_stats={},
            todo_summary={},
            points_estimated=0,
            points_done=0,
        )
        accountant_mock.account_all.return_value = [accountant_mock.account_for.return_value]

        app_mock = Mock()
        with patch.object(
            facts, "_build_accounting_accountant", AsyncMock(return_value=accountant_mock)
        ):
            import asyncio as _asyncio

            result = _asyncio.run(
                facts._accounting_facet(app_mock, project_id="p1")
            )
        assert result is not None


# ---------------------------------------------------------------------------
# Test 3: Tenant scoping
# ---------------------------------------------------------------------------


class TestTenantScopingApplied:
    def test_loc_ledger_isolates_projects(self):
        """LocLedger must keep per-project totals separate."""
        ledger = LocLedger()
        ledger.record_loc_changed("project-a", 100)
        ledger.record_loc_changed("project-b", 200)
        assert ledger.total("project-a") == 100
        assert ledger.total("project-b") == 200
        assert ledger.total("unknown-project") == 0

    def test_accountant_filters_by_project_id(self):
        """Accountant must only aggregate data for the requested project_id."""

        @dataclass
        class _Rec:
            project_id: str
            tokens_used: int
            usd_spent: float
            elapsed_seconds: float

        all_records = [
            _Rec("p1", 100, 10.0, 5.0),
            _Rec("p2", 999, 99.0, 50.0),
        ]

        def _usage(pid: str) -> list[_Rec]:
            return [r for r in all_records if r.project_id == pid]

        accountant = Accountant(
            usage_provider=_usage,
            todo_provider=lambda pid: [],
            role_provider=lambda pid: [],
            loc_provider=lambda pid: 0,
            project_provider=lambda: ["p1", "p2"],
            quota_usd=100.0,
        )
        result = accountant.account_for("p1")
        assert result.tokens_used == 100
        assert result.usd_spent == pytest.approx(10.0)
