"""TDD tests for the per-project accounting ledger.

All data providers are injected as fakes — no live daemon, no DB.
Covers: zero-quota safe division, multiple roles, mixed todo states,
loc_changed aggregation, pct_quota calculation, points aggregation.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from general_ludd.accounting.ledger import Accountant, ProjectAccounting

# ---------------------------------------------------------------------------
# Fake provider helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeUsageRecord:
    project_id: str
    tokens_used: int
    usd_spent: float
    elapsed_seconds: float


@dataclass
class FakeTodo:
    project_id: str
    status: str  # "pending" | "in_progress" | "done"
    points: int = 0


@dataclass
class FakeRoleRun:
    project_id: str
    role: str


def _make_usage_provider(records: list[FakeUsageRecord]):
    """Return a callable that yields usage records for a project_id."""

    def _get(project_id: str) -> list[FakeUsageRecord]:
        return [r for r in records if r.project_id == project_id]

    return _get


def _make_todo_provider(todos: list[FakeTodo]):
    def _get(project_id: str) -> list[FakeTodo]:
        return [t for t in todos if t.project_id == project_id]

    return _get


def _make_role_provider(runs: list[FakeRoleRun]):
    def _get(project_id: str) -> list[FakeRoleRun]:
        return [r for r in runs if r.project_id == project_id]

    return _get


def _make_loc_provider(delta_map: dict[str, int]):
    """Return a callable that returns loc_changed for a project_id."""

    def _get(project_id: str) -> int:
        return delta_map.get(project_id, 0)

    return _get


def _make_project_provider(project_ids: list[str]):
    def _get() -> list[str]:
        return project_ids

    return _get


# ---------------------------------------------------------------------------
# Helper to build a default Accountant with fakes
# ---------------------------------------------------------------------------


def _build_accountant(
    usage_records: list[FakeUsageRecord] | None = None,
    todos: list[FakeTodo] | None = None,
    role_runs: list[FakeRoleRun] | None = None,
    loc_map: dict[str, int] | None = None,
    project_ids: list[str] | None = None,
    quota_usd: float = 100.0,
) -> Accountant:
    return Accountant(
        usage_provider=_make_usage_provider(usage_records or []),
        todo_provider=_make_todo_provider(todos or []),
        role_provider=_make_role_provider(role_runs or []),
        loc_provider=_make_loc_provider(loc_map or {}),
        project_provider=_make_project_provider(project_ids or []),
        quota_usd=quota_usd,
    )


# ---------------------------------------------------------------------------
# Tests: basic accounting
# ---------------------------------------------------------------------------


class TestProjectAccountingFields:
    def test_result_is_dataclass_with_expected_fields(self):
        acct = _build_accountant(project_ids=["p1"])
        result = acct.account_for("p1")
        assert isinstance(result, ProjectAccounting)
        # All required fields exist
        assert hasattr(result, "project_id")
        assert hasattr(result, "elapsed_seconds")
        assert hasattr(result, "tokens_used")
        assert hasattr(result, "usd_spent")
        assert hasattr(result, "quota_usd")
        assert hasattr(result, "pct_quota")
        assert hasattr(result, "loc_changed")
        assert hasattr(result, "role_stats")
        assert hasattr(result, "todo_summary")
        assert hasattr(result, "points_estimated")
        assert hasattr(result, "points_done")

    def test_project_id_is_preserved(self):
        acct = _build_accountant(project_ids=["proj-abc"])
        result = acct.account_for("proj-abc")
        assert result.project_id == "proj-abc"


class TestElapsedAndTokens:
    def test_elapsed_sums_across_usage_records(self):
        records = [
            FakeUsageRecord("p1", tokens_used=100, usd_spent=0.01, elapsed_seconds=30.0),
            FakeUsageRecord("p1", tokens_used=200, usd_spent=0.02, elapsed_seconds=60.0),
        ]
        acct = _build_accountant(usage_records=records, project_ids=["p1"])
        result = acct.account_for("p1")
        assert result.elapsed_seconds == pytest.approx(90.0)

    def test_tokens_sums_across_usage_records(self):
        records = [
            FakeUsageRecord("p1", tokens_used=500, usd_spent=0.05, elapsed_seconds=10.0),
            FakeUsageRecord("p1", tokens_used=300, usd_spent=0.03, elapsed_seconds=5.0),
        ]
        acct = _build_accountant(usage_records=records, project_ids=["p1"])
        result = acct.account_for("p1")
        assert result.tokens_used == 800

    def test_usd_spent_sums_across_usage_records(self):
        records = [
            FakeUsageRecord("p1", tokens_used=100, usd_spent=1.50, elapsed_seconds=10.0),
            FakeUsageRecord("p1", tokens_used=100, usd_spent=0.75, elapsed_seconds=10.0),
        ]
        acct = _build_accountant(usage_records=records, project_ids=["p1"])
        result = acct.account_for("p1")
        assert result.usd_spent == pytest.approx(2.25)

    def test_no_records_gives_zero_totals(self):
        acct = _build_accountant(project_ids=["p1"])
        result = acct.account_for("p1")
        assert result.elapsed_seconds == 0.0
        assert result.tokens_used == 0
        assert result.usd_spent == 0.0

    def test_only_matching_project_records_counted(self):
        records = [
            FakeUsageRecord("p1", tokens_used=100, usd_spent=1.0, elapsed_seconds=10.0),
            FakeUsageRecord("p2", tokens_used=999, usd_spent=9.99, elapsed_seconds=999.0),
        ]
        acct = _build_accountant(usage_records=records, project_ids=["p1", "p2"])
        result = acct.account_for("p1")
        assert result.tokens_used == 100
        assert result.usd_spent == pytest.approx(1.0)


class TestQuotaAndPctQuota:
    def test_pct_quota_correct(self):
        records = [FakeUsageRecord("p1", tokens_used=50, usd_spent=25.0, elapsed_seconds=10.0)]
        acct = _build_accountant(usage_records=records, project_ids=["p1"], quota_usd=100.0)
        result = acct.account_for("p1")
        assert result.quota_usd == pytest.approx(100.0)
        assert result.pct_quota == pytest.approx(25.0)

    def test_pct_quota_zero_when_no_spend(self):
        acct = _build_accountant(project_ids=["p1"], quota_usd=100.0)
        result = acct.account_for("p1")
        assert result.pct_quota == pytest.approx(0.0)

    def test_pct_quota_safe_when_quota_is_zero(self):
        """Division by zero must not raise; returns 0.0 when quota is zero."""
        records = [FakeUsageRecord("p1", tokens_used=10, usd_spent=5.0, elapsed_seconds=1.0)]
        acct = _build_accountant(usage_records=records, project_ids=["p1"], quota_usd=0.0)
        result = acct.account_for("p1")
        assert result.pct_quota == 0.0  # safe fallback

    def test_pct_quota_can_exceed_100(self):
        """Over-budget case: pct_quota > 100 is valid."""
        records = [FakeUsageRecord("p1", tokens_used=100, usd_spent=150.0, elapsed_seconds=10.0)]
        acct = _build_accountant(usage_records=records, project_ids=["p1"], quota_usd=100.0)
        result = acct.account_for("p1")
        assert result.pct_quota == pytest.approx(150.0)


class TestLocChanged:
    def test_loc_changed_from_provider(self):
        acct = _build_accountant(
            loc_map={"p1": 247},
            project_ids=["p1"],
        )
        result = acct.account_for("p1")
        assert result.loc_changed == 247

    def test_loc_changed_zero_when_no_entry(self):
        acct = _build_accountant(project_ids=["p1"])
        result = acct.account_for("p1")
        assert result.loc_changed == 0


class TestRoleStats:
    def test_role_stats_counts_each_role(self):
        runs = [
            FakeRoleRun("p1", "coder"),
            FakeRoleRun("p1", "reviewer"),
            FakeRoleRun("p1", "coder"),
            FakeRoleRun("p1", "tester"),
            FakeRoleRun("p1", "coder"),
        ]
        acct = _build_accountant(role_runs=runs, project_ids=["p1"])
        result = acct.account_for("p1")
        assert result.role_stats == {"coder": 3, "reviewer": 1, "tester": 1}

    def test_role_stats_empty_when_no_runs(self):
        acct = _build_accountant(project_ids=["p1"])
        result = acct.account_for("p1")
        assert result.role_stats == {}

    def test_role_stats_only_matching_project(self):
        runs = [
            FakeRoleRun("p1", "coder"),
            FakeRoleRun("p2", "reviewer"),
        ]
        acct = _build_accountant(role_runs=runs, project_ids=["p1", "p2"])
        result = acct.account_for("p1")
        assert result.role_stats == {"coder": 1}
        assert "reviewer" not in result.role_stats

    def test_multiple_roles_all_present(self):
        runs = [
            FakeRoleRun("p1", "alpha"),
            FakeRoleRun("p1", "beta"),
            FakeRoleRun("p1", "gamma"),
            FakeRoleRun("p1", "beta"),
        ]
        acct = _build_accountant(role_runs=runs, project_ids=["p1"])
        result = acct.account_for("p1")
        assert result.role_stats["alpha"] == 1
        assert result.role_stats["beta"] == 2
        assert result.role_stats["gamma"] == 1


class TestTodoSummary:
    def test_todo_summary_mixed_states(self):
        todos = [
            FakeTodo("p1", "pending", 2),
            FakeTodo("p1", "pending", 3),
            FakeTodo("p1", "in_progress", 5),
            FakeTodo("p1", "done", 1),
            FakeTodo("p1", "done", 4),
            FakeTodo("p1", "done", 6),
        ]
        acct = _build_accountant(todos=todos, project_ids=["p1"])
        result = acct.account_for("p1")
        assert result.todo_summary["pending"] == 2
        assert result.todo_summary["in_progress"] == 1
        assert result.todo_summary["done"] == 3

    def test_todo_summary_all_done(self):
        todos = [
            FakeTodo("p1", "done", 3),
            FakeTodo("p1", "done", 7),
        ]
        acct = _build_accountant(todos=todos, project_ids=["p1"])
        result = acct.account_for("p1")
        assert result.todo_summary.get("done", 0) == 2
        assert result.todo_summary.get("pending", 0) == 0

    def test_todo_summary_empty_when_no_todos(self):
        acct = _build_accountant(project_ids=["p1"])
        result = acct.account_for("p1")
        assert result.todo_summary == {}

    def test_todo_summary_only_matching_project(self):
        todos = [
            FakeTodo("p1", "pending", 1),
            FakeTodo("p2", "done", 99),
        ]
        acct = _build_accountant(todos=todos, project_ids=["p1", "p2"])
        result = acct.account_for("p1")
        assert result.todo_summary.get("pending", 0) == 1
        assert result.todo_summary.get("done", 0) == 0


class TestPointsAggregation:
    def test_points_estimated_sums_all_todos(self):
        todos = [
            FakeTodo("p1", "pending", 2),
            FakeTodo("p1", "in_progress", 5),
            FakeTodo("p1", "done", 3),
        ]
        acct = _build_accountant(todos=todos, project_ids=["p1"])
        result = acct.account_for("p1")
        assert result.points_estimated == 10

    def test_points_done_sums_only_done_todos(self):
        todos = [
            FakeTodo("p1", "pending", 2),
            FakeTodo("p1", "done", 3),
            FakeTodo("p1", "done", 7),
        ]
        acct = _build_accountant(todos=todos, project_ids=["p1"])
        result = acct.account_for("p1")
        assert result.points_done == 10

    def test_points_zero_when_no_todos(self):
        acct = _build_accountant(project_ids=["p1"])
        result = acct.account_for("p1")
        assert result.points_estimated == 0
        assert result.points_done == 0

    def test_points_estimated_includes_all_statuses(self):
        todos = [
            FakeTodo("p1", "pending", 1),
            FakeTodo("p1", "in_progress", 2),
            FakeTodo("p1", "done", 4),
        ]
        acct = _build_accountant(todos=todos, project_ids=["p1"])
        result = acct.account_for("p1")
        # All 3 todos contribute to estimated
        assert result.points_estimated == 7
        # Only done contributes to done
        assert result.points_done == 4


class TestAccountAll:
    def test_account_all_returns_list(self):
        acct = _build_accountant(project_ids=["p1", "p2"])
        results = acct.account_all()
        assert isinstance(results, list)
        assert len(results) == 2

    def test_account_all_each_project_has_correct_id(self):
        acct = _build_accountant(project_ids=["alpha", "beta", "gamma"])
        results = acct.account_all()
        ids = {r.project_id for r in results}
        assert ids == {"alpha", "beta", "gamma"}

    def test_account_all_empty_when_no_projects(self):
        acct = _build_accountant(project_ids=[])
        results = acct.account_all()
        assert results == []

    def test_account_all_per_project_isolation(self):
        records = [
            FakeUsageRecord("p1", tokens_used=100, usd_spent=10.0, elapsed_seconds=5.0),
            FakeUsageRecord("p2", tokens_used=400, usd_spent=40.0, elapsed_seconds=20.0),
        ]
        acct = _build_accountant(
            usage_records=records,
            project_ids=["p1", "p2"],
            quota_usd=100.0,
        )
        results = {r.project_id: r for r in acct.account_all()}
        assert results["p1"].tokens_used == 100
        assert results["p2"].tokens_used == 400
        assert results["p1"].pct_quota == pytest.approx(10.0)
        assert results["p2"].pct_quota == pytest.approx(40.0)
