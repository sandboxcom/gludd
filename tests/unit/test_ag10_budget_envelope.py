"""AG.10 — Fine-grained budget envelope system: per-agent, per-task, per-tool.

Tests for BudgetEnvelope, PerAgentEnvelope, PerTaskEnvelope, PerToolEnvelope,
and the aggregating BudgetManager.
"""

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


class TestBudgetEnvelopeConstruction:
    def test_default_infinite_limit(self) -> None:
        e = BudgetEnvelope("test")
        assert e.limit == float("inf")
        assert e.spent == pytest.approx(0.0)
        assert e.remaining == float("inf")
        assert not e.is_exhausted

    def test_finite_limit(self) -> None:
        e = BudgetEnvelope("test", limit=100.0)
        assert e.limit == pytest.approx(100.0)
        assert e.remaining == pytest.approx(100.0)

    def test_negative_limit_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            BudgetEnvelope("test", limit=-1.0)

    def test_nan_limit_raises(self) -> None:
        with pytest.raises(ValueError, match="finite or inf"):
            BudgetEnvelope("test", limit=float("nan"))

    def test_zero_limit(self) -> None:
        e = BudgetEnvelope("test", limit=0.0)
        assert e.limit == pytest.approx(0.0)
        assert e.remaining == pytest.approx(0.0)
        assert e.is_exhausted


class TestBudgetEnvelopeSpend:
    def test_spend_within_limit(self) -> None:
        e = BudgetEnvelope("test", limit=10.0)
        result = e.try_spend(5.0)
        assert result["allowed"] is True
        assert e.spent == pytest.approx(5.0)
        assert e.remaining == pytest.approx(5.0)

    def test_spend_accumulates(self) -> None:
        e = BudgetEnvelope("test", limit=10.0)
        e.try_spend(3.0)
        e.try_spend(4.0)
        assert e.spent == pytest.approx(7.0)
        assert e.remaining == pytest.approx(3.0)

    def test_spend_exactly_limit(self) -> None:
        e = BudgetEnvelope("test", limit=10.0)
        result = e.try_spend(10.0)
        assert result["allowed"] is True
        assert e.spent == pytest.approx(10.0)
        assert e.remaining == pytest.approx(0.0)
        assert e.is_exhausted

    def test_spend_exceeds_limit(self) -> None:
        e = BudgetEnvelope("test", limit=10.0)
        e.try_spend(8.0)
        result = e.try_spend(3.0)
        assert result["allowed"] is False
        assert "budget exceeded" in str(result["reason"])
        assert "11.0000" in str(result["reason"])
        assert result["remaining"] == pytest.approx(2.0)
        assert e.spent == pytest.approx(8.0)

    def test_spend_zero(self) -> None:
        e = BudgetEnvelope("test", limit=10.0)
        result = e.try_spend(0.0)
        assert result["allowed"] is True
        assert e.spent == pytest.approx(0.0)

    def test_spend_negative_amount_fail_closed(self) -> None:
        e = BudgetEnvelope("test", limit=10.0)
        result = e.try_spend(-1.0)
        assert result["allowed"] is False
        assert "negative" in str(result["reason"])
        assert e.spent == pytest.approx(0.0)

    def test_spend_nan_fail_closed(self) -> None:
        e = BudgetEnvelope("test", limit=10.0)
        result = e.try_spend(float("nan"))
        assert result["allowed"] is False
        assert "non-finite" in str(result["reason"])

    def test_spend_inf_fail_closed(self) -> None:
        e = BudgetEnvelope("test", limit=10.0)
        result = e.try_spend(float("inf"))
        assert result["allowed"] is False
        assert "non-finite" in str(result["reason"])

    def test_spend_result_has_envelope_name(self) -> None:
        e = BudgetEnvelope("my-budget", limit=10.0)
        result = e.try_spend(1.0)
        assert result["envelope"] == "my-budget"

    def test_spend_result_has_remaining_when_denied(self) -> None:
        e = BudgetEnvelope("test", limit=10.0)
        e.try_spend(10.0)
        result = e.try_spend(0.01)
        assert result["allowed"] is False
        assert result["remaining"] == pytest.approx(0.0)

    def test_decimal_boundary_is_exact(self) -> None:
        envelope = BudgetEnvelope("decimal", limit=0.3)

        assert envelope.try_spend(0.1)["allowed"] is True
        assert envelope.try_spend(0.2)["allowed"] is True
        assert envelope.spent == 0.3
        assert envelope.remaining == 0.0
        assert envelope.is_exhausted


class TestBudgetEnvelopeRecordSpend:
    def test_record_spend_no_gating(self) -> None:
        e = BudgetEnvelope("test", limit=5.0)
        e.record_spend(20.0)
        assert e.spent == pytest.approx(20.0)

    def test_record_spend_negative_raises(self) -> None:
        e = BudgetEnvelope("test", limit=10.0)
        with pytest.raises(ValueError, match="non-negative"):
            e.record_spend(-5.0)

    def test_record_spend_uses_decimal_arithmetic(self) -> None:
        envelope = BudgetEnvelope("decimal", limit=1.0)
        envelope.record_spend(0.1)
        envelope.record_spend(0.2)

        assert envelope.spent == 0.3
        assert envelope.remaining == 0.7


class TestBudgetEnvelopeStatus:
    def test_get_status_initial(self) -> None:
        e = BudgetEnvelope("test", limit=50.0)
        status = e.get_status()
        assert status["name"] == "test"
        assert status["limit"] == pytest.approx(50.0)
        assert status["spent"] == pytest.approx(0.0)
        assert status["remaining"] == pytest.approx(50.0)
        assert status["exhausted"] is False

    def test_get_status_after_spend(self) -> None:
        e = BudgetEnvelope("test", limit=50.0)
        e.try_spend(30.0)
        status = e.get_status()
        assert status["spent"] == pytest.approx(30.0)
        assert status["remaining"] == pytest.approx(20.0)
        assert status["exhausted"] is False

    def test_get_status_exhausted(self) -> None:
        e = BudgetEnvelope("test", limit=50.0)
        e.try_spend(50.0)
        status = e.get_status()
        assert status["exhausted"] is True
        assert status["remaining"] == pytest.approx(0.0)

    def test_status_preserves_public_float_shape(self) -> None:
        envelope = BudgetEnvelope("decimal", limit=1.0)
        envelope.try_spend(0.1)

        status = envelope.get_status()
        assert isinstance(status["limit"], float)
        assert isinstance(status["spent"], float)
        assert isinstance(status["remaining"], float)

    def test_updated_limit_reuses_fail_closed_validation(self) -> None:
        envelope = BudgetEnvelope("decimal", limit=1.0)

        with pytest.raises(ValueError, match="non-negative"):
            envelope.limit = float("-inf")


class TestBudgetEnvelopeReset:
    def test_reset_zeroes_spent(self) -> None:
        e = BudgetEnvelope("test", limit=100.0)
        e.try_spend(80.0)
        e.reset()
        assert e.spent == pytest.approx(0.0)
        assert e.remaining == pytest.approx(100.0)
        assert not e.is_exhausted


class TestPerAgentEnvelope:
    def test_no_limit_allows_all(self) -> None:
        pe = PerAgentEnvelope()
        result = pe.try_spend("sonnet", 1000.0)
        assert result["allowed"] is True

    def test_set_limit_then_spend(self) -> None:
        pe = PerAgentEnvelope()
        pe.set_limit("sonnet", 10.0)
        result = pe.try_spend("sonnet", 5.0)
        assert result["allowed"] is True
        assert pe.total_spent() == pytest.approx(5.0)

    def test_set_limit_then_exceed(self) -> None:
        pe = PerAgentEnvelope()
        pe.set_limit("sonnet", 10.0)
        pe.try_spend("sonnet", 8.0)
        result = pe.try_spend("sonnet", 3.0)
        assert result["allowed"] is False

    def test_different_agents_independent(self) -> None:
        pe = PerAgentEnvelope()
        pe.set_limit("sonnet", 10.0)
        pe.set_limit("opus", 20.0)
        pe.try_spend("sonnet", 10.0)
        result = pe.try_spend("opus", 5.0)
        assert result["allowed"] is True

    def test_update_existing_limit(self) -> None:
        pe = PerAgentEnvelope()
        pe.set_limit("sonnet", 10.0)
        pe.set_limit("sonnet", 5.0)
        result = pe.try_spend("sonnet", 6.0)
        assert result["allowed"] is False

    def test_total_spent_across_agents(self) -> None:
        pe = PerAgentEnvelope()
        pe.set_limit("a", 100.0)
        pe.set_limit("b", 200.0)
        pe.try_spend("a", 30.0)
        pe.try_spend("b", 70.0)
        assert pe.total_spent() == pytest.approx(100.0)

    def test_get_status(self) -> None:
        pe = PerAgentEnvelope()
        pe.set_limit("sonnet", 10.0)
        pe.try_spend("sonnet", 2.0)
        status = pe.get_status()
        assert "sonnet" in status
        assert status["sonnet"]["spent"] == pytest.approx(2.0)

    def test_reset_all(self) -> None:
        pe = PerAgentEnvelope()
        pe.set_limit("sonnet", 10.0)
        pe.set_limit("opus", 20.0)
        pe.try_spend("sonnet", 5.0)
        pe.try_spend("opus", 10.0)
        pe.reset_all()
        assert pe.total_spent() == pytest.approx(0.0)


class TestPerTaskEnvelope:
    def test_no_limit_allows_all(self) -> None:
        pt = PerTaskEnvelope()
        result = pt.try_spend("task-1", 1000.0)
        assert result["allowed"] is True

    def test_set_limit_then_spend(self) -> None:
        pt = PerTaskEnvelope()
        pt.set_limit("task-1", 10.0)
        result = pt.try_spend("task-1", 5.0)
        assert result["allowed"] is True
        assert pt.total_spent() == pytest.approx(5.0)

    def test_set_limit_then_exceed(self) -> None:
        pt = PerTaskEnvelope()
        pt.set_limit("task-1", 10.0)
        pt.try_spend("task-1", 8.0)
        result = pt.try_spend("task-1", 3.0)
        assert result["allowed"] is False

    def test_default_limit_applies_to_new_tasks(self) -> None:
        pt = PerTaskEnvelope(default_limit=5.0)
        result = pt.try_spend("task-new", 4.0)
        assert result["allowed"] is True
        result = pt.try_spend("task-new", 2.0)
        assert result["allowed"] is False

    def test_explicit_limit_overrides_default(self) -> None:
        pt = PerTaskEnvelope(default_limit=5.0)
        pt.set_limit("task-special", 50.0)
        pt.try_spend("task-special", 6.0)
        assert pt.total_spent() == pytest.approx(6.0)

    def test_different_tasks_independent(self) -> None:
        pt = PerTaskEnvelope()
        pt.set_limit("task-a", 10.0)
        pt.set_limit("task-b", 20.0)
        pt.try_spend("task-a", 10.0)
        result = pt.try_spend("task-b", 5.0)
        assert result["allowed"] is True

    def test_total_spent(self) -> None:
        pt = PerTaskEnvelope()
        pt.set_limit("t1", 100.0)
        pt.set_limit("t2", 200.0)
        pt.try_spend("t1", 30.0)
        pt.try_spend("t2", 70.0)
        assert pt.total_spent() == pytest.approx(100.0)

    def test_reset_all_clears_envelopes(self) -> None:
        pt = PerTaskEnvelope()
        pt.set_limit("task-1", 10.0)
        pt.try_spend("task-1", 5.0)
        pt.reset_all()
        assert pt.total_spent() == pytest.approx(0.0)
        result = pt.try_spend("task-1", 1000.0)
        assert result["allowed"] is True


class TestPerToolEnvelope:
    def test_no_limit_allows_all(self) -> None:
        pt = PerToolEnvelope()
        result = pt.try_spend("bash", 1000.0)
        assert result["allowed"] is True

    def test_set_limit_then_spend(self) -> None:
        pt = PerToolEnvelope()
        pt.set_limit("bash", 10.0)
        result = pt.try_spend("bash", 5.0)
        assert result["allowed"] is True

    def test_tool_exceeded_blocks_specific_tool(self) -> None:
        pt = PerToolEnvelope()
        pt.set_limit("bash", 10.0)
        pt.set_limit("write", 100.0)
        pt.try_spend("bash", 10.0)
        result_bash = pt.try_spend("bash", 0.01)
        assert result_bash["allowed"] is False
        result_write = pt.try_spend("write", 50.0)
        assert result_write["allowed"] is True

    def test_reset_all(self) -> None:
        pt = PerToolEnvelope()
        pt.set_limit("bash", 10.0)
        pt.try_spend("bash", 10.0)
        pt.reset_all()
        result = pt.try_spend("bash", 5.0)
        assert result["allowed"] is True


class TestBudgetManager:
    def test_check_all_passes_when_no_limits(self) -> None:
        bm = BudgetManager()
        result = bm.check_all(
            agent_type="sonnet", task_id="t1", tool_type="bash", amount=100.0
        )
        assert result.allowed is True

    def test_check_all_tool_block_first(self) -> None:
        bm = BudgetManager()
        bm.per_tool.set_limit("bash", 5.0)
        bm.try_spend = bm.per_tool.try_spend  # shortcut ok, no direct access needed
        bm.per_tool.try_spend("bash", 5.0)
        result = bm.check_all(
            agent_type="sonnet", task_id="t1", tool_type="bash", amount=0.01
        )
        assert result.allowed is False
        assert result.details["layer"] == "tool"

    def test_check_all_task_block_second(self) -> None:
        bm = BudgetManager()
        bm.per_task.set_limit("t1", 3.0)
        bm.per_task.try_spend("t1", 3.0)
        result = bm.check_all(
            agent_type="sonnet", task_id="t1", tool_type="bash", amount=1.0
        )
        assert result.allowed is False
        assert result.details["layer"] == "task"

    def test_check_all_agent_block_third(self) -> None:
        bm = BudgetManager()
        bm.per_agent.set_limit("sonnet", 2.0)
        bm.per_agent.try_spend("sonnet", 2.0)
        result = bm.check_all(
            agent_type="sonnet", task_id="t1", tool_type="bash", amount=0.01
        )
        assert result.allowed is False
        assert result.details["layer"] == "agent"

    def test_check_all_first_block_short_circuits(self) -> None:
        bm = BudgetManager()
        bm.per_tool.set_limit("bash", 1.0)
        bm.per_tool.try_spend("bash", 1.0)
        bm.per_task.set_limit("t1", 1.0)
        bm.per_task.try_spend("t1", 1.0)
        result = bm.check_all(
            agent_type="sonnet", task_id="t1", tool_type="bash", amount=0.01
        )
        assert result.allowed is False
        assert result.details["layer"] == "tool"

    def test_check_all_none_params_skipped(self) -> None:
        bm = BudgetManager()
        result = bm.check_all(agent_type=None, task_id=None, tool_type=None, amount=10.0)
        assert result.allowed is True

    def test_total_spent_aggregates_all_layers(self) -> None:
        bm = BudgetManager()
        bm.per_agent.set_limit("sonnet", 100.0)
        bm.per_task.set_limit("t1", 100.0)
        bm.per_tool.set_limit("bash", 100.0)
        bm.per_agent.try_spend("sonnet", 10.0)
        bm.per_task.try_spend("t1", 20.0)
        bm.per_tool.try_spend("bash", 30.0)
        assert bm.total_spent() == pytest.approx(60.0)

    def test_total_spent_aggregates_decimal_values_exactly(self) -> None:
        manager = BudgetManager()
        manager.per_agent.set_limit("agent", 1.0)
        manager.per_task.set_limit("task", 1.0)
        manager.per_tool.set_limit("tool", 1.0)
        manager.per_agent.try_spend("agent", 0.1)
        manager.per_task.try_spend("task", 0.2)
        manager.per_tool.try_spend("tool", 0.3)

        assert manager.total_spent() == 0.6

    def test_get_status_all_layers(self) -> None:
        bm = BudgetManager()
        bm.per_agent.set_limit("sonnet", 50.0)
        bm.per_tool.set_limit("bash", 30.0)
        status = bm.get_status()
        assert "agents" in status
        assert "tasks" in status
        assert "tools" in status
        assert "sonnet" in status["agents"]

    def test_reset_all_clears_everything(self) -> None:
        bm = BudgetManager()
        bm.per_agent.set_limit("sonnet", 100.0)
        bm.per_task.set_limit("t1", 100.0)
        bm.per_tool.set_limit("bash", 100.0)
        bm.per_agent.try_spend("sonnet", 50.0)
        bm.per_task.try_spend("t1", 50.0)
        bm.per_tool.try_spend("bash", 50.0)
        bm.reset_all()
        assert bm.total_spent() == pytest.approx(0.0)


class TestThreadSafety:
    def test_envelope_try_spend_under_contention(self) -> None:
        import threading

        e = BudgetEnvelope("concurrent", limit=100.0)
        n_threads = 16
        per_thread = 100
        amount = 0.05

        def _hammer() -> None:
            for _ in range(per_thread):
                e.try_spend(amount)

        threads = [threading.Thread(target=_hammer) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        expected = n_threads * per_thread * amount
        assert e.spent == pytest.approx(expected)

    def test_per_agent_try_spend_under_contention(self) -> None:
        import threading

        pe = PerAgentEnvelope()
        pe.set_limit("sonnet", 1000.0)
        n_threads = 8
        per_thread = 200
        amount = 0.25

        def _hammer() -> None:
            for _ in range(per_thread):
                pe.try_spend("sonnet", amount)

        threads = [threading.Thread(target=_hammer) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        expected = n_threads * per_thread * amount
        assert pe.total_spent() == pytest.approx(expected)


class TestBudgetEdgeCases:
    def test_spend_zero_on_exhausted_envelope(self) -> None:
        e = BudgetEnvelope("test", limit=10.0)
        e.try_spend(10.0)
        result = e.try_spend(0.0)
        assert result["allowed"] is True
        assert e.spent == pytest.approx(10.0)

    def test_spend_epsilon_near_limit(self) -> None:
        e = BudgetEnvelope("test", limit=10.0)
        e.try_spend(9.999999999)
        result = e.try_spend(0.000000001)
        assert result["allowed"] is True
        assert e.spent == pytest.approx(10.0)

    def test_spend_epsilon_exceeds_limit(self) -> None:
        e = BudgetEnvelope("test", limit=10.0)
        e.try_spend(10.0)
        result = e.try_spend(1e-12)
        assert result["allowed"] is False


class TestBudgetManagerPartialParams:
    def test_check_all_only_tool_type_blocked(self) -> None:
        bm = BudgetManager()
        bm.per_tool.set_limit("bash", 1.0)
        bm.per_tool.try_spend("bash", 1.0)
        result = bm.check_all(
            agent_type=None, task_id=None, tool_type="bash", amount=0.01
        )
        assert result.allowed is False
        assert result.details["layer"] == "tool"

    def test_check_all_all_layers_exhausted_short_circuits(self) -> None:
        bm = BudgetManager()
        bm.per_tool.set_limit("bash", 1.0)
        bm.per_tool.try_spend("bash", 1.0)
        bm.per_task.set_limit("t1", 1.0)
        bm.per_task.try_spend("t1", 1.0)
        bm.per_agent.set_limit("sonnet", 1.0)
        bm.per_agent.try_spend("sonnet", 1.0)
        result = bm.check_all(
            agent_type="sonnet", task_id="t1", tool_type="bash", amount=0.01
        )
        assert result.allowed is False
        assert result.details["layer"] == "tool"


class TestBudgetManagerEmptyStatus:
    def test_get_status_empty_layers(self) -> None:
        bm = BudgetManager()
        status = bm.get_status()
        assert status["agents"] == {}
        assert status["tasks"] == {}
        assert status["tools"] == {}


class TestPerTaskEnvelopeResetDefault:
    def test_reset_all_clears_default_created_envelopes(self) -> None:
        pt = PerTaskEnvelope(default_limit=10.0)
        pt.try_spend("task-1", 5.0)
        assert pt.total_spent() == pytest.approx(5.0)
        pt.reset_all()
        assert pt.total_spent() == pytest.approx(0.0)
        result = pt.try_spend("task-1", 6.0)
        assert result["allowed"] is True
        result = pt.try_spend("task-1", 5.0)
        assert result["allowed"] is False


class TestBudgetCheckResult:
    def test_dataclass_defaults(self) -> None:
        r = BudgetCheckResult(allowed=True, reason="ok")
        assert r.allowed is True
        assert r.reason == "ok"
        assert r.details == {}

    def test_dataclass_with_details(self) -> None:
        r = BudgetCheckResult(
            allowed=False, reason="exceeded", details={"layer": "tool"}
        )
        assert r.allowed is False
        assert r.details["layer"] == "tool"
