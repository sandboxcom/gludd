"""Unit tests for RunBudgetGuard — run-level caps and time tracking."""

from __future__ import annotations

import pytest

from agentic_harness.controllers.budget import RunBudgetGuard


class TestCheckRunBudget:
    def test_check_run_budget_within_cap(self):
        guard = RunBudgetGuard(run_budget_usd=10.0)
        guard.record_spend(5.0)
        result = guard.check_run_budget()
        assert result["allowed"] is True

    def test_check_run_budget_exceeds_cap(self):
        guard = RunBudgetGuard(run_budget_usd=10.0)
        guard.record_spend(11.0)
        result = guard.check_run_budget()
        assert result["allowed"] is False
        assert "reason" in result
        assert "run budget" in result["reason"].lower()


class TestCheckWallClock:
    def test_check_wall_clock_within_timeout(self):
        guard = RunBudgetGuard(run_timeout_seconds=3600.0)
        result = guard.check_wall_clock()
        assert result["allowed"] is True

    def test_check_wall_clock_exceeds_timeout(self):
        guard = RunBudgetGuard(run_timeout_seconds=-1.0)
        result = guard.check_wall_clock()
        assert result["allowed"] is False
        assert "reason" in result
        assert "timeout" in result["reason"].lower()


class TestCheckPerCallBudget:
    def test_check_per_call_budget(self):
        guard = RunBudgetGuard(per_call_budget_usd=5.0)
        result = guard.check_per_call(3.0)
        assert result["allowed"] is True

    def test_check_per_call_budget_exceeded(self):
        guard = RunBudgetGuard(per_call_budget_usd=5.0)
        result = guard.check_per_call(6.0)
        assert result["allowed"] is False
        assert "reason" in result
        assert "per-call" in result["reason"].lower()


class TestRecordSpend:
    def test_record_spend_accumulates(self):
        guard = RunBudgetGuard()
        guard.record_spend(1.5)
        guard.record_spend(2.5)
        assert guard.get_total_spend() == pytest.approx(4.0)


class TestCheckAllLimits:
    def test_check_all_limits_pass(self):
        guard = RunBudgetGuard(
            run_budget_usd=100.0,
            run_timeout_seconds=3600.0,
            per_call_budget_usd=10.0,
        )
        guard.record_spend(5.0)
        result = guard.check_all_limits(estimated_cost=3.0)
        assert result["allowed"] is True

    def test_check_all_limits_timeout_exceeded(self):
        guard = RunBudgetGuard(
            run_budget_usd=100.0,
            run_timeout_seconds=-1.0,
            per_call_budget_usd=10.0,
        )
        result = guard.check_all_limits(estimated_cost=3.0)
        assert result["allowed"] is False
        assert "timeout" in result["reason"].lower()


class TestDefaultCaps:
    def test_default_caps_are_unlimited(self):
        guard = RunBudgetGuard()
        guard.record_spend(999_999.0)
        budget_result = guard.check_run_budget()
        assert budget_result["allowed"] is True

        wall_result = guard.check_wall_clock()
        assert wall_result["allowed"] is True

        per_call_result = guard.check_per_call(999_999.0)
        assert per_call_result["allowed"] is True

        all_result = guard.check_all_limits(estimated_cost=999_999.0)
        assert all_result["allowed"] is True
