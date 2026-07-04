"""Unit tests for HumanGate — LangGraph interrupt() human-in-the-loop.

Tests:
  - interrupt() called when condition met (low confidence + config enabled)
  - Command(resume=...) resumes the paused graph
  - High-confidence decisions skip interrupt (no gate)
  - Config disabled → no interrupt (no gate)
  - Thread ID tracking for resume
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.execution.human_gate import HumanGate


class TestHumanGateConfig:
    def test_default_disabled(self):
        gate = HumanGate(config={})
        assert gate.enabled is False

    def test_explicitly_enabled(self):
        gate = HumanGate(config={"review": {"human_in_the_loop": True}})
        assert gate.enabled is True

    def test_explicitly_disabled(self):
        gate = HumanGate(config={"review": {"human_in_the_loop": False}})
        assert gate.enabled is False

    def test_missing_review_key(self):
        gate = HumanGate(config={"other": True})
        assert gate.enabled is False

    def test_review_not_a_dict(self):
        gate = HumanGate(config={"review": "string"})
        assert gate.enabled is False

    def test_confidence_threshold_default(self):
        gate = HumanGate(config={})
        assert gate.confidence_threshold > 0.0

    def test_confidence_threshold_custom(self):
        gate = HumanGate(config={"review": {"confidence_threshold": 0.85}})
        assert gate.confidence_threshold == 0.85

    def test_confidence_threshold_non_numeric_fallback(self):
        gate = HumanGate(config={"review": {"confidence_threshold": "high"}})
        assert gate.confidence_threshold > 0.0

    def test_none_config_disabled(self):
        gate = HumanGate(config=None)
        assert gate.enabled is False


class TestShouldInterrupt:
    def test_disabled_never_interrupts(self):
        gate = HumanGate(config={"review": {"human_in_the_loop": False}})
        assert gate.should_interrupt(0.1) is False

    def test_enabled_low_confidence_interrupts(self):
        gate = HumanGate(
            config={
                "review": {
                    "human_in_the_loop": True,
                    "confidence_threshold": 0.7,
                }
            }
        )
        assert gate.should_interrupt(0.5) is True

    def test_enabled_high_confidence_skips(self):
        gate = HumanGate(
            config={
                "review": {
                    "human_in_the_loop": True,
                    "confidence_threshold": 0.7,
                }
            }
        )
        assert gate.should_interrupt(0.9) is False

    def test_enabled_at_threshold(self):
        gate = HumanGate(
            config={
                "review": {
                    "human_in_the_loop": True,
                    "confidence_threshold": 0.7,
                }
            }
        )
        assert gate.should_interrupt(0.7) is False


class TestPendingTracking:
    def test_no_pending_initially(self):
        gate = HumanGate()
        assert gate.pending_count == 0
        assert gate.pending_thread_ids == []

    def test_cancel_removes_pending(self):
        gate = HumanGate()
        gate._pending["thread-1"] = {"configurable": {"thread_id": "thread-1"}}
        assert gate.pending_count == 1
        gate.cancel("thread-1")
        assert gate.pending_count == 0

    def test_cancel_nonexistent_no_error(self):
        gate = HumanGate()
        assert gate.cancel("nonexistent") is False

    def test_available_without_langgraph(self):
        gate = HumanGate()
        assert isinstance(gate.available, bool)


class TestAwaitApprovalConfigDisabled:
    @pytest.mark.asyncio
    async def test_disabled_returns_none(self):
        gate = HumanGate(config={"review": {"human_in_the_loop": False}})
        result = await gate.await_approval(
            thread_id="t1",
            message="msg",
            confidence=0.3,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_config_returns_none(self):
        gate = HumanGate(config={})
        result = await gate.await_approval(
            thread_id="t1",
            message="msg",
            confidence=0.3,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_high_confidence_returns_none(self):
        gate = HumanGate(
            config={
                "review": {
                    "human_in_the_loop": True,
                    "confidence_threshold": 0.7,
                }
            }
        )
        result = await gate.await_approval(
            thread_id="t1",
            message="msg",
            confidence=0.95,
        )
        assert result is None


class TestAwaitApprovalWithMockedLangGraph:
    @pytest.mark.asyncio
    async def test_interrupt_called_when_condition_met(self):
        """Verify interrupt() is invoked via the graph when conditions are met."""
        gate = HumanGate(
            config={
                "review": {
                    "human_in_the_loop": True,
                    "confidence_threshold": 0.7,
                }
            }
        )

        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(
            return_value={"decision": "approved"}
        )

        gate._graph = mock_graph

        _discard = await gate.await_approval(
            thread_id="test-thread",
            message="Approve this?",
            decision_id="dec-1",
            todo_id="todo-1",
            confidence=0.3,
        )

        assert gate.pending_count == 1
        assert "test-thread" in gate._pending

    @pytest.mark.asyncio
    async def test_high_confidence_skips_interrupt(self):
        """High confidence decisions never trigger the gate."""
        gate = HumanGate(
            config={
                "review": {
                    "human_in_the_loop": True,
                    "confidence_threshold": 0.7,
                }
            }
        )

        mock_graph = AsyncMock()
        gate._graph = mock_graph

        result = await gate.await_approval(
            thread_id="test-thread",
            message="msg",
            confidence=0.95,
        )

        assert result is None
        mock_graph.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_config_disabled_no_graph_invoke(self):
        """When config is disabled, the graph is never invoked."""
        gate = HumanGate(config={"review": {"human_in_the_loop": False}})

        mock_graph = AsyncMock()
        gate._graph = mock_graph

        result = await gate.await_approval(
            thread_id="test-thread",
            message="msg",
            confidence=0.1,
        )

        assert result is None
        mock_graph.ainvoke.assert_not_called()


class TestResumeWithMockedLangGraph:
    @pytest.mark.asyncio
    async def test_resume_nonexistent_thread(self):
        gate = HumanGate()
        gate._graph = MagicMock()

        result = await gate.resume("nonexistent", "approved")
        assert result is False

    @pytest.mark.asyncio
    async def test_resume_sends_command_resume(self):
        """Verify resume() calls graph.ainvoke with Command(resume=...)."""
        gate = HumanGate(
            config={
                "review": {
                    "human_in_the_loop": True,
                    "confidence_threshold": 0.7,
                }
            }
        )

        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(return_value={"decision": "approved"})
        gate._graph = mock_graph

        gate._pending["thread-1"] = {"configurable": {"thread_id": "thread-1"}}

        result = await gate.resume("thread-1", "approved")

        assert result is True
        assert gate.pending_count == 0
        mock_graph.ainvoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_resume_clears_pending(self):
        """After resume, the thread_id is removed from pending."""
        gate = HumanGate(
            config={
                "review": {
                    "human_in_the_loop": True,
                    "confidence_threshold": 0.7,
                }
            }
        )

        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(return_value={"decision": "approved"})
        gate._graph = mock_graph

        gate._pending["thread-1"] = {"configurable": {"thread_id": "thread-1"}}

        assert gate.pending_count == 1
        await gate.resume("thread-1", "approved")
        assert gate.pending_count == 0
        assert "thread-1" not in gate._pending


class TestThreadIdTracking:
    def test_multiple_threads_tracked(self):
        gate = HumanGate(
            config={
                "review": {
                    "human_in_the_loop": True,
                    "confidence_threshold": 0.7,
                }
            }
        )

        gate._pending["thread-A"] = {"configurable": {"thread_id": "thread-A"}}
        gate._pending["thread-B"] = {"configurable": {"thread_id": "thread-B"}}

        assert gate.pending_count == 2
        assert set(gate.pending_thread_ids) == {"thread-A", "thread-B"}

    def test_thread_ids_are_isolated(self):
        gate = HumanGate()
        gate._pending["thread-A"] = {"configurable": {"thread_id": "thread-A"}}
        gate._pending["thread-B"] = {"configurable": {"thread_id": "thread-B"}}

        gate.cancel("thread-A")

        assert gate.pending_count == 1
        assert "thread-A" not in gate._pending
        assert "thread-B" in gate._pending

    def test_cancel_returns_true_for_existing(self):
        gate = HumanGate()
        gate._pending["thread-1"] = {}
        assert gate.cancel("thread-1") is True

    def test_cancel_returns_false_for_missing(self):
        gate = HumanGate()
        assert gate.cancel("missing") is False


class TestAwaitApprovalReturnsPendingState:
    @pytest.mark.asyncio
    async def test_returns_decision_from_graph(self):
        """When graph completes, await_approval returns the decision."""
        gate = HumanGate(
            config={
                "review": {
                    "human_in_the_loop": True,
                    "confidence_threshold": 0.7,
                }
            }
        )

        mock_graph = AsyncMock()
        mock_graph.ainvoke.side_effect = [
            {"decision": "approved"},
        ]
        gate._graph = mock_graph

        result = await gate.await_approval(
            thread_id="thread-complete",
            message="msg",
            decision_id="dec-1",
            todo_id="todo-1",
            confidence=0.3,
        )

        assert result == "approved"


class TestGraphNotAvailable:
    @pytest.mark.asyncio
    async def test_available_false_returns_none(self):
        """When langgraph is not available, returns None (fallback to HumanTodo)."""
        gate = HumanGate(
            config={
                "review": {
                    "human_in_the_loop": True,
                    "confidence_threshold": 0.3,
                }
            }
        )
        gate._graph = None

        result = await gate.await_approval(
            thread_id="t1",
            message="msg",
            confidence=0.1,
        )
        assert result is None

    def test_available_when_graph_exists(self):
        gate = HumanGate()
        mock_graph = MagicMock()
        gate._graph = mock_graph

        assert gate.available is True

    def test_not_available_when_graph_none(self):
        gate = HumanGate()
        gate._graph = None

        assert gate.available is False
