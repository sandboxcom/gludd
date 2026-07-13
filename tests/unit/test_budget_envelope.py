"""Tests for budget/envelope: BudgetEnvelope, PerAgentEnvelope, PerTaskEnvelope, BudgetManager, BudgetCheckResult."""

from __future__ import annotations

import pytest

from general_ludd.budget.envelope import (
    BudgetCheckResult,
    BudgetEnvelope,
    BudgetManager,
    PerAgentEnvelope,
    PerTaskEnvelope,
    PerToolEnvelope,
)


class TestBudgetEnvelope:
    def test_default_unlimited(self):
        env = BudgetEnvelope("test")
        assert env.limit == float("inf")
        assert env.spent == 0.0
        assert env.remaining == float("inf")
        assert env.is_exhausted is False

    def test_finite_limit(self):
        env = BudgetEnvelope("test", limit=100.0)
        assert env.limit == 100.0
        assert env.remaining == 100.0

    def test_negative_limit_rejected(self):
        with pytest.raises(ValueError, match="non-negative"):
            BudgetEnvelope("test", limit=-5.0)

    def test_nan_limit_rejected(self):
        with pytest.raises(ValueError, match="finite or inf"):
            BudgetEnvelope("test", limit=float("nan"))

    def test_try_spend_allowed(self):
        env = BudgetEnvelope("test", limit=100.0)
        result = env.try_spend(50.0)
        assert result["allowed"] is True
        assert env.spent == 50.0
        assert env.remaining == 50.0

    def test_try_spend_exact_limit(self):
        env = BudgetEnvelope("test", limit=100.0)
        result = env.try_spend(100.0)
        assert result["allowed"] is True
        assert env.spent == 100.0
        assert env.is_exhausted is True

    def test_try_spend_exceeds_limit(self):
        env = BudgetEnvelope("test", limit=100.0)
        result = env.try_spend(150.0)
        assert result["allowed"] is False
        assert "budget exceeded" in result["reason"]
        assert env.spent == 0.0

    def test_try_spend_negative_denied(self):
        env = BudgetEnvelope("test", limit=100.0)
        result = env.try_spend(-10.0)
        assert result["allowed"] is False
        assert "negative" in result["reason"]

    def test_try_spend_nan_denied(self):
        env = BudgetEnvelope("test", limit=100.0)
        result = env.try_spend(float("nan"))
        assert result["allowed"] is False
        assert "non-finite" in result["reason"]

    def test_try_spend_inf_denied(self):
        env = BudgetEnvelope("test", limit=100.0)
        result = env.try_spend(float("inf"))
        assert result["allowed"] is False
        assert "non-finite" in result["reason"]

    def test_multiple_spends_exhaust(self):
        env = BudgetEnvelope("test", limit=100.0)
        assert env.try_spend(60.0)["allowed"] is True
        assert env.try_spend(30.0)["allowed"] is True
        assert env.is_exhausted is False
        assert env.try_spend(20.0)["allowed"] is False
        assert env.is_exhausted is False  # still at 90

    def test_record_spend(self):
        env = BudgetEnvelope("test", limit=100.0)
        env.record_spend(30.0)
        assert env.spent == 30.0

    def test_record_spend_negative_raises(self):
        env = BudgetEnvelope("test", limit=100.0)
        with pytest.raises(ValueError, match="non-negative"):
            env.record_spend(-1.0)

    def test_record_spend_nan_raises(self):
        env = BudgetEnvelope("test", limit=100.0)
        with pytest.raises(ValueError, match="finite"):
            env.record_spend(float("nan"))

    def test_get_status(self):
        env = BudgetEnvelope("test", limit=50.0)
        env.try_spend(20.0)
        status = env.get_status()
        assert status["name"] == "test"
        assert status["limit"] == 50.0
        assert status["spent"] == 20.0
        assert status["remaining"] == 30.0
        assert status["exhausted"] is False

    def test_reset(self):
        env = BudgetEnvelope("test", limit=100.0)
        env.try_spend(60.0)
        env.reset()
        assert env.spent == 0.0

    def test_unlimited_never_exhausted(self):
        env = BudgetEnvelope("test")
        env.record_spend(999999.0)
        assert env.is_exhausted is False

    def test_is_exhausted_at_limit(self):
        env = BudgetEnvelope("test", limit=10.0)
        env.record_spend(10.0)
        assert env.is_exhausted is True

    def test_is_exhausted_beyond_limit(self):
        env = BudgetEnvelope("test", limit=10.0)
        env.record_spend(15.0)
        assert env.is_exhausted is True

    def test_remaining_floors_at_zero(self):
        env = BudgetEnvelope("test", limit=10.0)
        env.record_spend(15.0)
        assert env.remaining == 0.0


class TestPerAgentEnvelope:
    def test_no_limit_by_default(self):
        pa = PerAgentEnvelope()
        result = pa.try_spend("sonnet", 10.0)
        assert result["allowed"] is True
        assert "no limit configured" in result["reason"]

    def test_set_and_spend(self):
        pa = PerAgentEnvelope()
        pa.set_limit("sonnet", 100.0)
        result = pa.try_spend("sonnet", 50.0)
        assert result["allowed"] is True
        assert result["remaining"] == 50.0

    def test_exceed_limit(self):
        pa = PerAgentEnvelope()
        pa.set_limit("sonnet", 100.0)
        result = pa.try_spend("sonnet", 150.0)
        assert result["allowed"] is False

    def test_update_existing_limit(self):
        pa = PerAgentEnvelope()
        pa.set_limit("sonnet", 100.0)
        pa.try_spend("sonnet", 80.0)
        pa.set_limit("sonnet", 200.0)
        assert pa.try_spend("sonnet", 100.0)["allowed"] is True

    def test_get_status(self):
        pa = PerAgentEnvelope()
        pa.set_limit("sonnet", 50.0)
        status = pa.get_status()
        assert "sonnet" in status

    def test_total_spent(self):
        pa = PerAgentEnvelope()
        pa.set_limit("a", 100.0)
        pa.set_limit("b", 100.0)
        pa.try_spend("a", 30.0)
        pa.try_spend("b", 20.0)
        assert pa.total_spent() == 50.0

    def test_reset_all(self):
        pa = PerAgentEnvelope()
        pa.set_limit("a", 100.0)
        pa.try_spend("a", 50.0)
        pa.reset_all()
        assert pa.total_spent() == 0.0


class TestPerTaskEnvelope:
    def test_default_unlimited(self):
        pt = PerTaskEnvelope()
        result = pt.try_spend("task-1", 1000.0)
        assert result["allowed"] is True
        assert "no limit configured" in result["reason"]

    def test_with_default_limit(self):
        pt = PerTaskEnvelope(default_limit=200.0)
        result = pt.try_spend("task-1", 50.0)
        assert result["allowed"] is True
        assert result["remaining"] == 150.0

    def test_exceed_default_limit(self):
        pt = PerTaskEnvelope(default_limit=200.0)
        result = pt.try_spend("task-1", 250.0)
        assert result["allowed"] is False

    def test_set_specific_limit(self):
        pt = PerTaskEnvelope()
        pt.set_limit("task-1", 500.0)
        result = pt.try_spend("task-1", 400.0)
        assert result["allowed"] is True

    def test_get_status(self):
        pt = PerTaskEnvelope(default_limit=100.0)
        pt.try_spend("t1", 30.0)
        status = pt.get_status()
        assert "t1" in status

    def test_total_spent(self):
        pt = PerTaskEnvelope(default_limit=100.0)
        pt.try_spend("t1", 20.0)
        pt.try_spend("t2", 30.0)
        assert pt.total_spent() == 50.0

    def test_reset_all_clears(self):
        pt = PerTaskEnvelope(default_limit=100.0)
        pt.try_spend("t1", 50.0)
        pt.reset_all()
        assert pt.total_spent() == 0.0


class TestPerToolEnvelope:
    def test_no_limit_by_default(self):
        pt = PerToolEnvelope()
        result = pt.try_spend("bash", 1.0)
        assert result["allowed"] is True
        assert "no limit configured" in result["reason"]

    def test_set_and_spend(self):
        pt = PerToolEnvelope()
        pt.set_limit("bash", 50.0)
        result = pt.try_spend("bash", 20.0)
        assert result["allowed"] is True

    def test_exceed_limit(self):
        pt = PerToolEnvelope()
        pt.set_limit("write", 10.0)
        result = pt.try_spend("write", 20.0)
        assert result["allowed"] is False

    def test_total_spent(self):
        pt = PerToolEnvelope()
        pt.set_limit("bash", 100.0)
        pt.set_limit("write", 100.0)
        pt.try_spend("bash", 10.0)
        pt.try_spend("write", 20.0)
        assert pt.total_spent() == 30.0

    def test_reset_all(self):
        pt = PerToolEnvelope()
        pt.set_limit("bash", 100.0)
        pt.try_spend("bash", 10.0)
        pt.reset_all()
        assert pt.total_spent() == 0.0


class TestBudgetCheckResult:
    def test_allowed_true(self):
        bcr = BudgetCheckResult(allowed=True, reason="ok")
        assert bcr.allowed is True
        assert bcr.reason == "ok"
        assert bcr.details == {}

    def test_allowed_false_with_details(self):
        bcr = BudgetCheckResult(
            allowed=False,
            reason="budget exceeded",
            details={"layer": "task", "remaining": 0.0},
        )
        assert bcr.allowed is False
        assert bcr.details["layer"] == "task"


class TestBudgetManager:
    def test_all_allowed_when_no_limits(self):
        mgr = BudgetManager()
        result = mgr.check_all(agent_type="sonnet", task_id="t1", tool_type="bash", amount=10.0)
        assert result.allowed is True

    def test_tool_envelope_blocks_first(self):
        per_tool = PerToolEnvelope()
        per_tool.set_limit("bash", 5.0)
        mgr = BudgetManager(per_tool=per_tool)
        result = mgr.check_all(tool_type="bash", amount=10.0)
        assert result.allowed is False
        assert result.details.get("layer") == "tool"

    def test_task_envelope_blocks(self):
        per_task = PerTaskEnvelope(default_limit=50.0)
        mgr = BudgetManager(per_task=per_task)
        result = mgr.check_all(task_id="t1", amount=100.0)
        assert result.allowed is False
        assert result.details.get("layer") == "task"

    def test_agent_envelope_blocks(self):
        per_agent = PerAgentEnvelope()
        per_agent.set_limit("opus", 10.0)
        mgr = BudgetManager(per_agent=per_agent)
        result = mgr.check_all(agent_type="opus", amount=50.0)
        assert result.allowed is False
        assert result.details.get("layer") == "agent"

    def test_short_circuit_order(self):
        per_tool = PerToolEnvelope()
        per_tool.set_limit("bash", 5.0)
        per_agent = PerAgentEnvelope()
        per_agent.set_limit("sonnet", 5.0)
        mgr = BudgetManager(per_tool=per_tool, per_agent=per_agent)
        result = mgr.check_all(tool_type="bash", agent_type="sonnet", amount=10.0)
        assert result.allowed is False
        assert result.details.get("layer") == "tool"

    def test_none_values_skipped(self):
        mgr = BudgetManager()
        result = mgr.check_all(agent_type=None, task_id=None, tool_type=None, amount=10.0)
        assert result.allowed is True

    def test_get_status(self):
        per_agent = PerAgentEnvelope()
        per_agent.set_limit("sonnet", 100.0)
        mgr = BudgetManager(per_agent=per_agent)
        status = mgr.get_status()
        assert "agents" in status
        assert "tasks" in status
        assert "tools" in status

    def test_total_spent(self):
        per_task = PerTaskEnvelope(default_limit=100.0)
        per_task.try_spend("t1", 30.0)
        mgr = BudgetManager(per_task=per_task)
        assert mgr.total_spent() == 30.0

    def test_reset_all(self):
        per_agent = PerAgentEnvelope()
        per_agent.set_limit("sonnet", 100.0)
        per_agent.try_spend("sonnet", 50.0)
        mgr = BudgetManager(per_agent=per_agent)
        mgr.reset_all()
        assert mgr.total_spent() == 0.0

    def test_custom_envelopes_accepted(self):
        pa = PerAgentEnvelope()
        pt = PerTaskEnvelope()
        pto = PerToolEnvelope()
        mgr = BudgetManager(per_agent=pa, per_task=pt, per_tool=pto)
        assert mgr.per_agent is pa
        assert mgr.per_task is pt
        assert mgr.per_tool is pto
