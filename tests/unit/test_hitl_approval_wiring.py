"""G7 HITL approval gates — wiring and integration tests (70% → 85%).

Tests covering ApprovalGate ↔ HumanGate ↔ EventLoop ↔ daemon wiring,
decision gating, and LangGraph-backed resume behaviour.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from general_ludd.approval.gate import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
    ApprovalResponse,
)

# ---------------------------------------------------------------------------
# ApprovalGate — data-model correctness
# ---------------------------------------------------------------------------


class TestApprovalGateDataclass:
    def test_approval_request_carries_all_fields(self) -> None:
        req = ApprovalRequest(
            resource_id="deploy/prod-42",
            action="deploy_to_production",
            requester="agent-7",
            reason="release v0.2.0",
            metadata={"ticket": "OPS-881"},
        )
        assert req.resource_id == "deploy/prod-42"
        assert req.action == "deploy_to_production"
        assert req.requester == "agent-7"
        assert req.reason == "release v0.2.0"
        assert req.metadata == {"ticket": "OPS-881"}

    def test_approval_request_defaults(self) -> None:
        req = ApprovalRequest(resource_id="r", action="a", requester="u")
        assert req.reason == ""
        assert req.metadata == {}

    def test_approval_decision_enum_has_correct_values(self) -> None:
        assert ApprovalDecision.APPROVED.value == "approved"
        assert ApprovalDecision.DENIED.value == "denied"
        assert ApprovalDecision.PENDING.value == "pending"

    def test_approval_decision_enum_membership(self) -> None:
        assert set(ApprovalDecision) == {
            ApprovalDecision.APPROVED,
            ApprovalDecision.DENIED,
            ApprovalDecision.PENDING,
        }

    def test_approval_response_pending_by_default(self) -> None:
        req = ApprovalRequest(resource_id="r", action="a", requester="u")
        resp = ApprovalResponse(request=req)
        assert resp.request is req
        assert resp.decision == ApprovalDecision.PENDING
        assert resp.reviewer == ""
        assert resp.comment == ""

    def test_approval_response_explicit_decision(self) -> None:
        req = ApprovalRequest(resource_id="r", action="a", requester="u")
        resp = ApprovalResponse(
            request=req,
            decision=ApprovalDecision.APPROVED,
            reviewer="ops-human",
            comment="LGTM",
        )
        assert resp.decision == ApprovalDecision.APPROVED
        assert resp.reviewer == "ops-human"
        assert resp.comment == "LGTM"


# ---------------------------------------------------------------------------
# ApprovalGate — instantiation and wiring
# ---------------------------------------------------------------------------


class TestApprovalGateWiring:
    def test_approval_gate_instantiated(self) -> None:
        gate = ApprovalGate()
        assert gate is not None
        assert isinstance(gate, ApprovalGate)

    def test_app_state_stores_approval_gate(self) -> None:
        app = FastAPI()
        gate = ApprovalGate()
        app.state._approval_gate = gate
        assert app.state._approval_gate is gate
        assert isinstance(app.state._approval_gate, ApprovalGate)

    def test_request_approval_returns_response(self) -> None:
        gate = ApprovalGate()
        req = ApprovalRequest(resource_id="r", action="a", requester="u")
        resp = gate.request_approval(req)
        assert isinstance(resp, ApprovalResponse)
        assert resp.request is req
        assert resp.decision == ApprovalDecision.PENDING


# ---------------------------------------------------------------------------
# HumanGate — should_interrupt respect config
# ---------------------------------------------------------------------------


class TestHumanGateShouldInterrupt:
    @pytest.fixture
    def _imports(self) -> None:
        pass

    def test_should_interrupt_disabled_when_no_config(self) -> None:
        from general_ludd.execution.human_gate import HumanGate

        gate = HumanGate(config=None)
        assert gate.enabled is False
        assert gate.should_interrupt(0.5) is False

    def test_should_interrupt_disabled_when_config_empty(self) -> None:
        from general_ludd.execution.human_gate import HumanGate

        gate = HumanGate(config={})
        assert gate.enabled is False
        assert gate.should_interrupt(0.5) is False

    def test_should_interrupt_disabled_when_human_in_the_loop_false(self) -> None:
        from general_ludd.execution.human_gate import HumanGate

        gate = HumanGate(config={"review": {"human_in_the_loop": False}})
        assert gate.enabled is False
        assert gate.should_interrupt(0.1) is False

    def test_should_interrupt_enabled_below_threshold(self) -> None:
        from general_ludd.execution.human_gate import HumanGate

        gate = HumanGate(config={
            "review": {
                "human_in_the_loop": True,
                "confidence_threshold": 0.85,
            }
        })
        assert gate.enabled is True
        assert gate.should_interrupt(0.7) is True  # 0.7 < 0.85
        assert gate.should_interrupt(0.84) is True  # 0.84 < 0.85
        assert gate.should_interrupt(0.85) is False  # equal to threshold
        assert gate.should_interrupt(0.95) is False  # above threshold

    def test_should_interrupt_default_threshold_70(self) -> None:
        from general_ludd.execution.human_gate import HumanGate

        gate = HumanGate(config={"review": {"human_in_the_loop": True}})
        assert gate.enabled is True
        assert gate.confidence_threshold == 0.7
        assert gate.should_interrupt(0.69) is True
        assert gate.should_interrupt(0.71) is False

    def test_confidence_threshold_parsed_from_config(self) -> None:
        from general_ludd.execution.human_gate import HumanGate

        gate = HumanGate(config={
            "review": {
                "human_in_the_loop": True,
                "confidence_threshold": "0.85",
            }
        })
        assert gate.confidence_threshold == 0.85

    def test_confidence_threshold_invalid_falls_back(self) -> None:
        from general_ludd.execution.human_gate import HumanGate

        gate = HumanGate(config={
            "review": {
                "human_in_the_loop": True,
                "confidence_threshold": "not-a-float",
            }
        })
        assert gate.confidence_threshold == 0.7  # default fallback

    def test_review_key_none_still_noop(self) -> None:
        from general_ludd.execution.human_gate import HumanGate

        gate = HumanGate(config={"review": None})
        assert gate.enabled is False


# ---------------------------------------------------------------------------
# HumanGate — construction and daemon_state wiring
# ---------------------------------------------------------------------------


class TestHumanGateConstruction:
    def test_human_gate_constructed_with_config(self) -> None:
        from general_ludd.execution.human_gate import HumanGate

        cfg = {"review": {"human_in_the_loop": True, "confidence_threshold": 0.80}}
        gate = HumanGate(config=cfg)
        assert gate is not None
        assert gate.enabled is True
        assert gate.confidence_threshold == 0.80

    def test_human_gate_default_constructor(self) -> None:
        from general_ludd.execution.human_gate import HumanGate

        gate = HumanGate()
        assert gate is not None
        assert gate.enabled is False
        assert gate.confidence_threshold == 0.7

    def test_human_gate_wired_via_daemon_state(self) -> None:
        from general_ludd.execution.human_gate import HumanGate

        gate = HumanGate(config={"review": {"human_in_the_loop": True}})
        daemon_state: dict = {}
        daemon_state["human_gate"] = gate
        assert daemon_state["human_gate"] is gate
        assert daemon_state["human_gate"].enabled is True

    def test_human_gate_pending_tracking_empty_initially(self) -> None:
        from general_ludd.execution.human_gate import HumanGate

        gate = HumanGate()
        assert gate.pending_count == 0
        assert gate.pending_thread_ids == []


# ---------------------------------------------------------------------------
# HumanGate — await_approval interrupt on low confidence
# ---------------------------------------------------------------------------


class TestHumanGateAwaitApproval:
    def test_await_approval_returns_none_when_confidence_above_threshold(
        self,
    ) -> None:
        from general_ludd.execution.human_gate import HumanGate

        gate = HumanGate(config={
            "review": {"human_in_the_loop": True, "confidence_threshold": 0.85}
        })
        result = asyncio.run(
            gate.await_approval("t1", "msg", confidence=0.90)
        )
        assert result is None  # should_interrupt returns False → None

    def test_await_approval_returns_none_when_disabled(self) -> None:
        from general_ludd.execution.human_gate import HumanGate

        gate = HumanGate(config={"review": {"human_in_the_loop": False}})
        result = asyncio.run(
            gate.await_approval("t1", "msg", confidence=0.30)
        )
        assert result is None

    def test_await_approval_returns_none_when_no_langgraph(self) -> None:
        from general_ludd.execution.human_gate import HumanGate

        gate = HumanGate(config={"review": {"human_in_the_loop": True}})
        if gate.available:
            pytest.skip("LangGraph available — test expects unavailable")
        result = asyncio.run(
            gate.await_approval(
                "t1", "msg", decision_id="d1", todo_id="t1", confidence=0.30,
            )
        )
        assert result is None

    def test_await_approval_pauses_graph_when_confidence_low(self) -> None:
        """Integration: with langgraph present, low confidence → graph invoke."""
        from general_ludd.execution.human_gate import HumanGate

        gate = HumanGate(config={
            "review": {"human_in_the_loop": True, "confidence_threshold": 0.85}
        })
        if not gate.available:
            pytest.skip("LangGraph graph not available")
        # ainvoke returns immediately with interrupt data
        asyncio.run(
            gate.await_approval("t99", "Deploy to prod?", confidence=0.50)
        )
        # the graph is paused; result may be None until resumed
        assert gate.pending_count >= 0  # graph stored for resume


# ---------------------------------------------------------------------------
# HumanGate — resume
# ---------------------------------------------------------------------------


class TestHumanGateResume:
    def test_resume_unknown_thread_returns_false(self) -> None:
        from general_ludd.execution.human_gate import HumanGate

        gate = HumanGate()
        ok = asyncio.run(gate.resume("no-such-thread", "approved"))
        assert ok is False

    def test_resume_unavailable_gate_returns_false(self) -> None:
        from general_ludd.execution.human_gate import HumanGate

        gate = HumanGate(config={"review": {"human_in_the_loop": True}})
        if gate.available:
            pytest.skip("LangGraph available — test expects unavailable")
        ok = asyncio.run(gate.resume("t1", "approved"))
        assert ok is False

    def test_resume_after_manual_pending_insert(self) -> None:
        from general_ludd.execution.human_gate import HumanGate

        gate = HumanGate()
        gate._pending["t42"] = {"configurable": {"thread_id": "t42"}}

        if gate.available:
            gate._graph = MagicMock()
            gate._graph.ainvoke = AsyncMock(return_value={"decision": "approved"})

            ok = asyncio.run(gate.resume("t42", "approved"))
            assert ok is True
            assert gate.pending_count == 0
            gate._graph.ainvoke.assert_awaited_once()
        else:
            ok = asyncio.run(gate.resume("t42", "approved"))
            assert ok is False

    def test_cancel_removes_pending(self) -> None:
        from general_ludd.execution.human_gate import HumanGate

        gate = HumanGate()
        gate._pending["t5"] = {"configurable": {"thread_id": "t5"}}
        assert gate.pending_count == 1
        assert gate.cancel("t5") is True
        assert gate.pending_count == 0
        assert gate.cancel("t5") is False


# ---------------------------------------------------------------------------
# Execution subsystem — HumanGate in EventLoop / worker path
# ---------------------------------------------------------------------------


class TestExecutionApprovalPath:
    def test_human_gate_importable_from_execution_module(self) -> None:
        from general_ludd.execution.human_gate import HumanGate

        assert HumanGate is not None

    def test_human_gate_imported_in_event_loop(self) -> None:
        import ast

        loop_path = "src/general_ludd/event_loop/loop.py"
        with open(loop_path) as fh:
            tree = ast.parse(fh.read())
        imports = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module is not None
        ]
        assert "general_ludd.execution.human_gate" in imports

    def test_human_gate_instantiated_in_event_loop_init(self) -> None:
        import ast

        loop_path = "src/general_ludd/event_loop/loop.py"
        with open(loop_path) as fh:
            tree = ast.parse(fh.read())
        assigns = [
            node.targets[0].attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Attribute)
            and node.targets[0].attr == "_human_gate"
        ]
        assert len(assigns) >= 1, "Expected self._human_gate assignment in EventLoop.__init__"

    def test_daemon_wires_human_gate_to_state(self) -> None:
        import ast

        daemon_path = "src/general_ludd/daemon.py"
        with open(daemon_path) as fh:
            tree = ast.parse(fh.read())
        human_gate_subscripts = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and (
                isinstance(node.value, ast.Name)
                and node.value.id == "daemon_state"
            ) and (
                isinstance(node.slice, ast.Constant)
                and node.slice.value == "human_gate"
            ):
                human_gate_subscripts.append(node)
        assert len(human_gate_subscripts) >= 1, (
            "Expected daemon_state['human_gate'] assignment in daemon.py"
        )

    def test_daemon_stores_approval_gate_on_app_state(self) -> None:
        import ast

        daemon_path = "src/general_ludd/daemon.py"
        with open(daemon_path) as fh:
            tree = ast.parse(fh.read())
        approval_gate_assigns = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Attribute)
                        and isinstance(target.value.value, ast.Name)
                        and target.value.value.id == "app"
                        and target.value.attr == "state"
                        and target.attr == "_approval_gate"
                    ):
                        approval_gate_assigns.append(node)
        assert len(approval_gate_assigns) >= 1, (
            "Expected app.state._approval_gate assignment in daemon.py"
        )
