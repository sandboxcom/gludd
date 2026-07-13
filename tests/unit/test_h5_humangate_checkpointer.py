"""TDD tests for H.5 — H-HUMANGATE-NO-CHECKPOINTER.

Gate graph compiled without checkpointer breaks interrupt/resume.
Tests:
  H.5-T1: Gate graph is compiled WITH a checkpointer
  H.5-T2: Interrupt saves state (resume possible via get_state)
  H.5-T3: Compiling without checkpointer is detectable
  H.5-T4: Resume via Command restores and continues prior state
"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.execution.human_gate import HumanGate, _build_gate_graph, _GateState

_LANGGRAPH_AVAILABLE = False
try:
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import StateGraph
    from langgraph.types import Command

    _LANGGRAPH_AVAILABLE = True
except ImportError:
    pass


pytestmark = pytest.mark.skipif(not _LANGGRAPH_AVAILABLE, reason="langgraph not installed")


class TestGateGraphHasCheckpointer:
    """H.5-T1: Gate graph is compiled WITH a checkpointer."""

    def test_graph_checkpointer_is_not_none(self):
        graph = _build_gate_graph()
        assert graph.checkpointer is not None, (
            "Gate graph MUST be compiled with a checkpointer for interrupt/resume to work"
        )

    def test_graph_checkpointer_is_base_checkpoint_saver(self):
        graph = _build_gate_graph()
        assert isinstance(graph.checkpointer, BaseCheckpointSaver), (
            f"Expected BaseCheckpointSaver, got {type(graph.checkpointer)}"
        )

    def test_graph_checkpointer_is_in_memory_saver_by_default(self):
        graph = _build_gate_graph()
        assert isinstance(graph.checkpointer, InMemorySaver), (
            "Default gate graph checkpointer should be InMemorySaver for in-process state"
        )


class TestInterruptSavesState:
    """H.5-T2: Interrupt saves state — get_state returns checkpointed data."""

    @pytest.mark.asyncio
    async def test_state_accessible_after_interrupt(self):
        graph = _build_gate_graph()
        thread_config = {"configurable": {"thread_id": "test-save-state"}}

        with contextlib.suppress(Exception):
            await graph.ainvoke(
                {"message": "Test message", "decision_id": "d1", "todo_id": "t1"},
                thread_config,
            )

        state = graph.get_state(thread_config)
        assert state is not None, (
            "Interrupt MUST checkpoint state — get_state returned None (no checkpointer?)"
        )
        assert state.values is not None, "Checkpointed state should have values"

    @pytest.mark.asyncio
    async def test_state_contains_original_message(self):
        graph = _build_gate_graph()
        thread_config = {"configurable": {"thread_id": "test-msg-check"}}

        with contextlib.suppress(Exception):
            await graph.ainvoke(
                {"message": "Approve deployment?", "decision_id": "d2", "todo_id": "t2"},
                thread_config,
            )

        state = graph.get_state(thread_config)
        assert state is not None
        values = state.values or {}
        msgs = values.get("message", "")
        assert "Approve deployment" in str(msgs), (
            f"State should preserve original message, got: {msgs}"
        )


class TestNoCheckpointerDetectable:
    """H.5-T3: Compiling without checkpointer is detectable (checkpointer is None)."""

    def test_no_checkpointer_graph_has_none_checkpointer(self):
        builder = StateGraph(_GateState)

        def gate_node(state):
            return {"decision": "auto"}

        builder.add_node("gate", gate_node)
        builder.set_entry_point("gate")
        builder.set_finish_point("gate")
        graph_no_cp = builder.compile()

        assert graph_no_cp.checkpointer is None, (
            "Graph compiled without checkpointer argument MUST report checkpointer=None"
        )

    @pytest.mark.asyncio
    async def test_no_checkpointer_loses_state(self):
        builder = StateGraph(_GateState)

        def gate_node(state):
            try:
                from langgraph.types import interrupt
                interrupt("Should pause")
            except ImportError:
                pass
            return {"decision": "approved"}

        builder.add_node("gate", gate_node)
        builder.set_entry_point("gate")
        builder.set_finish_point("gate")
        graph_no_cp = builder.compile()

        thread_config = {"configurable": {"thread_id": "test-no-cp-lose"}}
        with contextlib.suppress(Exception):
            await graph_no_cp.ainvoke({"message": "msg"}, thread_config)

        with pytest.raises(ValueError, match="No checkpointer set"):
            graph_no_cp.get_state(thread_config)


class TestResumeRestoresState:
    """H.5-T4: Resume via Command(resume=...) restores and continues prior state."""

    @pytest.mark.asyncio
    async def test_resume_continues_to_completion(self):
        graph = _build_gate_graph()
        thread_config = {"configurable": {"thread_id": "test-resume-continue"}}

        with contextlib.suppress(Exception):
            await graph.ainvoke(
                {"message": "Approve?", "decision_id": "d3", "todo_id": "t3"},
                thread_config,
            )

        state = graph.get_state(thread_config)
        assert state is not None, "State must exist after interrupt"

        resumed = await graph.ainvoke(Command(resume="approved"), thread_config)
        assert isinstance(resumed, dict), f"Resume should return dict, got {type(resumed)}"
        assert resumed.get("decision") == "approved", (
            f"Resumed gate should return decision='approved', got {resumed.get('decision')}"
        )

    @pytest.mark.asyncio
    async def test_resume_with_denied_decision(self):
        graph = _build_gate_graph()
        thread_config = {"configurable": {"thread_id": "test-resume-deny"}}

        with contextlib.suppress(Exception):
            await graph.ainvoke(
                {"message": "Risky change?", "decision_id": "d4", "todo_id": "t4"},
                thread_config,
            )

        resumed = await graph.ainvoke(Command(resume="denied"), thread_config)
        assert resumed.get("decision") == "denied"

    @pytest.mark.asyncio
    async def test_get_state_returns_none_for_unknown_thread(self):
        graph = _build_gate_graph()
        thread_config = {"configurable": {"thread_id": "totally-unknown"}}
        state = graph.get_state(thread_config)
        assert state is None or state.values == {}, (
            "Unknown thread should have no saved state"
        )


class TestHumanGateIntegration:
    """Verify HumanGate._graph uses the fixed _build_gate_graph with checkpointer."""

    def test_human_gate_graph_has_checkpointer(self):
        gate = HumanGate(config={"review": {"human_in_the_loop": True}})
        if gate._graph is not None:
            assert gate._graph.checkpointer is not None, (
                "HumanGate graph must have checkpointer — gate was initialized with langgraph available"
            )

    @pytest.mark.asyncio
    async def test_await_approval_records_pending_state(self):
        gate = HumanGate(
            config={
                "review": {
                    "human_in_the_loop": True,
                    "confidence_threshold": 0.7,
                }
            }
        )
        if not gate.available:
            pytest.skip("langgraph not available — cannot test graph invoke")

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"decision": "approved"})
        mock_graph.checkpointer = MagicMock(spec=BaseCheckpointSaver)
        gate._graph = mock_graph

        mock_graph.ainvoke.reset_mock()
        result = await gate.await_approval(
            thread_id="test-hg-int",
            message="Approve?",
            decision_id="dec-int",
            todo_id="todo-int",
            confidence=0.3,
        )
        assert result == "approved"
        mock_graph.ainvoke.assert_called_once()
        assert gate.pending_count == 1
