"""E2E proof for G7 Human-in-the-loop approval flow.

Full flow: dispatch → review → human approval → resume → complete.

Tests:
  - Full HITL flow: low-confidence decision paused, human approves, resumes
  - Low-confidence decisions land in pending review
  - POST /admin/review/approve/{thread_id} resumes the graph
  - Denied approvals re-queue work (needs_more_work)
  - High-confidence decisions skip the human gate
  - GET /admin/review/pending lists paused gates

Uses HumanGate with mock graph, plus minimal FastAPI app for router endpoints.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.execution.human_gate import HumanGate

HITL_CONFIG = {
    "review": {
        "human_in_the_loop": True,
        "confidence_threshold": 0.7,
    }
}


def _make_mock_graph(*return_values):
    """Build a mock graph whose ainvoke returns values in sequence."""
    mock = AsyncMock()
    mock.ainvoke = AsyncMock()
    if len(return_values) == 1:
        mock.ainvoke.return_value = return_values[0]
    else:
        mock.ainvoke.side_effect = list(return_values)
    return mock


def _paused_gate(thread_id: str, decision_id: str, todo_id: str):
    """Create a HumanGate with a pre-populated pending entry."""
    gate = HumanGate(config=HITL_CONFIG)
    gate._graph = _make_mock_graph({"decision": "approved"})
    gate._pending[thread_id] = {"configurable": {"thread_id": thread_id}}
    gate._decision_id = decision_id
    gate._todo_id = todo_id
    return gate


def _make_hitl_app(gate: HumanGate) -> FastAPI:
    """Minimal FastAPI app with review router wired to a provided HumanGate."""
    from general_ludd.routers.review import register

    app = FastAPI()
    daemon_state: dict = {"human_gate": gate}
    register(app, daemon_state)
    return app


# ---------------------------------------------------------------------------
# E2E full flow with mock LangGraph
# ---------------------------------------------------------------------------


class TestHitlFullFlow:
    """Full dispatch → review → human approval → resume → complete."""

    @pytest.mark.asyncio
    async def test_full_flow_approve_resumes_and_completes(self):
        """A low-confidence decision is paused, human approves, and the
        graph resumes with the decision 'approved'."""
        gate = HumanGate(config=HITL_CONFIG)

        mock_graph = _make_mock_graph(
            {"decision": "approved"},
        )
        gate._graph = mock_graph

        decision = await gate.await_approval(
            thread_id="thread-full-1",
            message="Approve deployment to production?",
            decision_id="DEC-001",
            todo_id="TODO-001",
            confidence=0.3,
        )

        assert decision == "approved"
        assert gate.pending_count == 1
        assert "thread-full-1" in gate._pending

        resume_ok = await gate.resume("thread-full-1", "approved")
        assert resume_ok is True
        assert gate.pending_count == 0
        assert "thread-full-1" not in gate._pending
        assert mock_graph.ainvoke.call_count >= 2

    @pytest.mark.asyncio
    async def test_full_flow_denied_clears_pending(self):
        """A denied decision clears the pending gate."""
        gate = HumanGate(config=HITL_CONFIG)

        mock_graph = _make_mock_graph({"decision": "denied"})
        gate._graph = mock_graph

        decision = await gate.await_approval(
            thread_id="thread-full-2",
            message="Should we proceed?",
            decision_id="DEC-002",
            todo_id="TODO-002",
            confidence=0.3,
        )

        assert decision == "denied"

        resume_ok = await gate.resume("thread-full-2", "denied")
        assert resume_ok is True
        assert gate.pending_count == 0

    @pytest.mark.asyncio
    async def test_full_flow_needs_more_work_requeues(self):
        """needs_more_work decision clears the gate so work can be re-queued."""
        gate = HumanGate(config=HITL_CONFIG)

        mock_graph = _make_mock_graph({"decision": "needs_more_work"})
        gate._graph = mock_graph

        decision = await gate.await_approval(
            thread_id="thread-full-3",
            message="Re-queue this for more work?",
            decision_id="DEC-003",
            todo_id="TODO-003",
            confidence=0.5,
        )

        assert decision == "needs_more_work"

        resume_ok = await gate.resume("thread-full-3", "needs_more_work")
        assert resume_ok is True
        assert gate.pending_count == 0


# ---------------------------------------------------------------------------
# Low-confidence → pending review
# ---------------------------------------------------------------------------


class TestHitlLowConfidencePending:
    """Low-confidence decisions go to pending review."""

    def test_should_interrupt_below_threshold(self):
        gate = HumanGate(config=HITL_CONFIG)
        assert gate.should_interrupt(0.55) is True
        assert gate.should_interrupt(0.1) is True
        assert gate.should_interrupt(0.0) is True

    @pytest.mark.asyncio
    async def test_low_confidence_pauses_graph(self):
        """Confidence 0.55 (below 0.7 threshold) triggers gate pause."""
        gate = HumanGate(config=HITL_CONFIG)

        mock_graph = _make_mock_graph({"decision": "approved"})
        gate._graph = mock_graph

        result = await gate.await_approval(
            thread_id="thread-low-1",
            message="Review this low-confidence completion",
            decision_id="DEC-LOW",
            todo_id="TODO-LOW",
            confidence=0.55,
        )

        assert result == "approved"
        assert gate.pending_count == 1

    def test_confidence_at_threshold_no_interrupt(self):
        gate = HumanGate(config=HITL_CONFIG)
        assert gate.should_interrupt(0.7) is False

    @pytest.mark.asyncio
    async def test_multiple_low_confidence_tracked_independently(self):
        gate = HumanGate(config=HITL_CONFIG)

        mock_graph = _make_mock_graph(
            {"decision": "approved"},
            {"decision": "denied"},
            {"decision": "needs_more_work"},
        )
        gate._graph = mock_graph

        d1 = await gate.await_approval(
            thread_id="thread-multi-A",
            message="Decision A",
            decision_id="D-A",
            todo_id="T-A",
            confidence=0.3,
        )
        d2 = await gate.await_approval(
            thread_id="thread-multi-B",
            message="Decision B",
            decision_id="D-B",
            todo_id="T-B",
            confidence=0.4,
        )
        d3 = await gate.await_approval(
            thread_id="thread-multi-C",
            message="Decision C",
            decision_id="D-C",
            todo_id="T-C",
            confidence=0.5,
        )

        assert d1 == "approved"
        assert d2 == "denied"
        assert d3 == "needs_more_work"
        assert gate.pending_count == 3
        assert set(gate.pending_thread_ids) == {
            "thread-multi-A", "thread-multi-B", "thread-multi-C",
        }

        await gate.resume("thread-multi-B", "denied")
        assert gate.pending_count == 2
        assert "thread-multi-B" not in gate.pending_thread_ids


# ---------------------------------------------------------------------------
# POST /admin/review/approve/{thread_id} resumes
# ---------------------------------------------------------------------------


class TestHitlApproveResumes:
    """POST /admin/review/approve/{thread_id} resumes the paused graph."""

    def test_approve_endpoint_resumes_gate(self):
        gate = HumanGate(config=HITL_CONFIG)
        gate._graph = _make_mock_graph(
            {"decision": "approved"},
            {"decision": "approved"},
        )
        gate._pending["thread-http-1"] = {
            "configurable": {"thread_id": "thread-http-1"},
        }

        app = _make_hitl_app(gate)
        client = TestClient(app)

        resp = client.post(
            "/admin/review/approve/thread-http-1",
            json={"decision": "approved"},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["thread_id"] == "thread-http-1"
        assert body["decision"] == "approved"
        assert gate.pending_count == 0

    def test_approve_nonexistent_thread_404(self):
        gate = HumanGate(config=HITL_CONFIG)
        gate._graph = _make_mock_graph({"decision": "approved"})

        app = _make_hitl_app(gate)
        client = TestClient(app)

        resp = client.post(
            "/admin/review/approve/nonexistent-thread",
            json={"decision": "approved"},
        )

        assert resp.status_code == 404, resp.text
        assert "nonexistent-thread" in resp.text

    def test_approve_no_gate_503(self):
        app = FastAPI()
        from general_ludd.routers.review import register

        daemon_state: dict = {"human_gate": None}
        register(app, daemon_state)
        client = TestClient(app)

        resp = client.post(
            "/admin/review/approve/any-thread",
            json={"decision": "approved"},
        )

        assert resp.status_code == 503, resp.text

    def test_approve_denied_decision(self):
        gate = HumanGate(config=HITL_CONFIG)
        gate._graph = _make_mock_graph(
            {"decision": "denied"},
            {"decision": "denied"},
        )
        gate._pending["thread-http-2"] = {
            "configurable": {"thread_id": "thread-http-2"},
        }

        app = _make_hitl_app(gate)
        client = TestClient(app)

        resp = client.post(
            "/admin/review/approve/thread-http-2",
            json={"decision": "denied"},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["decision"] == "denied"
        assert gate.pending_count == 0

    def test_approve_needs_more_work_decision(self):
        gate = HumanGate(config=HITL_CONFIG)
        gate._graph = _make_mock_graph(
            {"decision": "needs_more_work"},
            {"decision": "needs_more_work"},
        )
        gate._pending["thread-http-3"] = {
            "configurable": {"thread_id": "thread-http-3"},
        }

        app = _make_hitl_app(gate)
        client = TestClient(app)

        resp = client.post(
            "/admin/review/approve/thread-http-3",
            json={"decision": "needs_more_work"},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["decision"] == "needs_more_work"
        assert gate.pending_count == 0


# ---------------------------------------------------------------------------
# GET /admin/review/pending lists paused gates
# ---------------------------------------------------------------------------


class TestHitlPendingList:
    """GET /admin/review/pending lists currently paused review gates."""

    def test_pending_empty(self):
        gate = HumanGate(config=HITL_CONFIG)
        gate._graph = MagicMock()

        app = _make_hitl_app(gate)
        client = TestClient(app)

        resp = client.get("/admin/review/pending")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["pending"] == []
        assert body["count"] == 0
        assert body["available"] is True
        assert body["enabled"] is True

    def test_pending_with_gates(self):
        gate = HumanGate(config=HITL_CONFIG)
        gate._graph = MagicMock()
        gate._pending["t1"] = {"configurable": {"thread_id": "t1"}}
        gate._pending["t2"] = {"configurable": {"thread_id": "t2"}}

        app = _make_hitl_app(gate)
        client = TestClient(app)

        resp = client.get("/admin/review/pending")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["pending"]) == 2
        assert body["count"] == 2
        tids = {p["thread_id"] for p in body["pending"]}
        assert tids == {"t1", "t2"}

    def test_pending_reflects_approved_removal(self):
        gate = HumanGate(config=HITL_CONFIG)
        gate._graph = _make_mock_graph({"decision": "approved"})
        gate._pending["t-rem"] = {"configurable": {"thread_id": "t-rem"}}

        app = _make_hitl_app(gate)
        client = TestClient(app)

        resp_pre = client.get("/admin/review/pending")
        assert resp_pre.json()["count"] == 1

        client.post(
            "/admin/review/approve/t-rem",
            json={"decision": "approved"},
        )

        resp_post = client.get("/admin/review/pending")
        assert resp_post.json()["count"] == 0

    def test_pending_no_gate_503(self):
        app = FastAPI()
        from general_ludd.routers.review import register

        daemon_state: dict = {"human_gate": None}
        register(app, daemon_state)
        client = TestClient(app)

        resp = client.get("/admin/review/pending")
        assert resp.status_code == 503, resp.text


# ---------------------------------------------------------------------------
# Denied approvals re-queue work
# ---------------------------------------------------------------------------


class TestHitlDenyRequeues:
    """Denied approvals re-queue work (via needs_more_work)."""

    @pytest.mark.asyncio
    async def test_await_approval_returns_denied(self):
        """When graph returns 'denied', the caller gets 'denied' back."""
        gate = HumanGate(config=HITL_CONFIG)

        mock_graph = _make_mock_graph({"decision": "denied"})
        gate._graph = mock_graph

        decision = await gate.await_approval(
            thread_id="thread-deny-1",
            message="This should be denied",
            decision_id="DEC-DENY",
            todo_id="TODO-DENY",
            confidence=0.2,
        )

        assert decision == "denied"

    @pytest.mark.asyncio
    async def test_needs_more_work_returns_for_requeue(self):
        """Returns 'needs_more_work' so the event loop can re-queue."""
        gate = HumanGate(config=HITL_CONFIG)

        mock_graph = _make_mock_graph({"decision": "needs_more_work"})
        gate._graph = mock_graph

        decision = await gate.await_approval(
            thread_id="thread-requeue-1",
            message="Insufficient — needs rework",
            decision_id="DEC-RQ",
            todo_id="TODO-RQ",
            confidence=0.25,
        )

        assert decision == "needs_more_work"

    @pytest.mark.asyncio
    async def test_cancel_clears_pending_without_resume(self):
        """Cancelling a gate removes it without triggering a resume."""
        gate = HumanGate(config=HITL_CONFIG)
        gate._pending["thread-cancel-1"] = {
            "configurable": {"thread_id": "thread-cancel-1"},
        }
        assert gate.pending_count == 1

        ok = gate.cancel("thread-cancel-1")
        assert ok is True
        assert gate.pending_count == 0
        assert "thread-cancel-1" not in gate._pending


# ---------------------------------------------------------------------------
# High-confidence decisions skip gate
# ---------------------------------------------------------------------------


class TestHitlHighConfidenceSkips:
    """High-confidence decisions skip the human gate entirely."""

    def test_high_confidence_above_threshold_no_interrupt(self):
        gate = HumanGate(config=HITL_CONFIG)
        assert gate.should_interrupt(0.85) is False
        assert gate.should_interrupt(0.95) is False
        assert gate.should_interrupt(1.0) is False

    @pytest.mark.asyncio
    async def test_high_confidence_returns_none_no_pause(self):
        """Confidence 0.95 (above threshold) returns None — no gate created."""
        gate = HumanGate(config=HITL_CONFIG)

        mock_graph = _make_mock_graph({"decision": "approved"})
        gate._graph = mock_graph

        result = await gate.await_approval(
            thread_id="thread-high-1",
            message="This should skip the gate",
            decision_id="DEC-HIGH",
            todo_id="TODO-HIGH",
            confidence=0.95,
        )

        assert result is None
        assert gate.pending_count == 0
        mock_graph.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_config_disabled_skips_even_low_confidence(self):
        """When HITL is disabled, even very low confidence skips."""
        gate = HumanGate(
            config={
                "review": {
                    "human_in_the_loop": False,
                    "confidence_threshold": 0.7,
                }
            }
        )

        mock_graph = _make_mock_graph({"decision": "approved"})
        gate._graph = mock_graph

        result = await gate.await_approval(
            thread_id="thread-disabled-1",
            message="This should be skipped",
            decision_id="DEC-DISABLED",
            todo_id="TODO-DISABLED",
            confidence=0.01,
        )

        assert result is None
        assert gate.pending_count == 0
        mock_graph.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_custom_confidence_threshold_respected(self):
        """Custom threshold of 0.55 gates confidence 0.5 but not 0.6."""
        gate = HumanGate(
            config={
                "review": {
                    "human_in_the_loop": True,
                    "confidence_threshold": 0.55,
                }
            }
        )

        mock_graph = _make_mock_graph({"decision": "approved"})
        gate._graph = mock_graph

        r1 = await gate.await_approval(
            thread_id="t1", message="m", confidence=0.5,
        )
        r2 = await gate.await_approval(
            thread_id="t2", message="m", confidence=0.6,
        )

        assert r1 == "approved"
        assert r2 is None
        assert gate.pending_count == 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestHitlEdgeCases:
    def test_graph_not_available_returns_none(self):
        gate = HumanGate(config=HITL_CONFIG)
        gate._graph = None
        assert gate.available is False

    @pytest.mark.asyncio
    async def test_graph_not_available_await_approval_returns_none(self):
        gate = HumanGate(config=HITL_CONFIG)
        gate._graph = None

        result = await gate.await_approval(
            thread_id="t", message="m", confidence=0.2,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_graph_error_returns_false_on_resume(self):
        gate = HumanGate(config=HITL_CONFIG)
        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(side_effect=RuntimeError("graph died"))
        gate._graph = mock_graph
        gate._pending["t"] = {"configurable": {"thread_id": "t"}}

        result = await gate.resume("t", "approved")
        assert result is False
