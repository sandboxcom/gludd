"""Structural tests for routers/review.py — human-in-the-loop review endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.review import (
    ApproveRequest,
    PendingGate,
    PendingResponse,
    register,
)

# ---------------------------------------------------------------------------
# ApproveRequest model
# ---------------------------------------------------------------------------


class TestApproveRequest:
    def test_model_fields(self):
        req = ApproveRequest(decision="approved")
        assert req.decision == "approved"

    def test_decision_examples(self):
        for d in ("approved", "denied", "needs_more_work"):
            req = ApproveRequest(decision=d)
            assert req.decision == d


# ---------------------------------------------------------------------------
# PendingGate model
# ---------------------------------------------------------------------------


class TestPendingGate:
    def test_model_fields(self):
        gate = PendingGate(thread_id="t1")
        assert gate.thread_id == "t1"


# ---------------------------------------------------------------------------
# PendingResponse model
# ---------------------------------------------------------------------------


class TestPendingResponse:
    def test_model_fields(self):
        resp = PendingResponse(
            pending=[PendingGate(thread_id="t1")],
            count=1,
            available=True,
            enabled=True,
        )
        assert resp.count == 1
        assert resp.available is True
        assert resp.enabled is True
        assert len(resp.pending) == 1
        assert resp.pending[0].thread_id == "t1"

    def test_empty_pending(self):
        resp = PendingResponse(
            pending=[], count=0, available=False, enabled=False
        )
        assert resp.count == 0
        assert resp.pending == []


# ---------------------------------------------------------------------------
# Behavioral: register + TestClient with mocked HumanGate
# ---------------------------------------------------------------------------


def _make_app(human_gate: object | None) -> FastAPI:
    """Build a FastAPI app with an optional HumanGate in daemon_state."""
    app = FastAPI()
    daemon_state: dict[str, object] = {}
    if human_gate is not None:
        daemon_state["human_gate"] = human_gate
    register(app, daemon_state)
    return app


def test_register_is_callable():
    assert callable(register)


class TestApproveEndpoint:
    def test_returns_200_when_gate_resume_succeeds(self):
        mock_gate = MagicMock()
        mock_gate.resume = AsyncMock(return_value=True)
        app = _make_app(mock_gate)
        client = TestClient(app)
        resp = client.post(
            "/admin/review/approve/t1", json={"decision": "approved"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["thread_id"] == "t1"
        assert data["decision"] == "approved"

    def test_returns_503_when_human_gate_not_wired(self):
        app = _make_app(None)
        client = TestClient(app)
        resp = client.post(
            "/admin/review/approve/t1", json={"decision": "approved"}
        )
        assert resp.status_code == 503
        assert "not available" in resp.json()["detail"]

    def test_returns_404_when_thread_not_found(self):
        mock_gate = MagicMock()
        mock_gate.resume = AsyncMock(return_value=False)
        app = _make_app(mock_gate)
        client = TestClient(app)
        resp = client.post(
            "/admin/review/approve/t1", json={"decision": "approved"}
        )
        assert resp.status_code == 404
        assert "No pending gate" in resp.json()["detail"]

    def test_can_deny_decision(self):
        mock_gate = MagicMock()
        mock_gate.resume = AsyncMock(return_value=True)
        app = _make_app(mock_gate)
        client = TestClient(app)
        resp = client.post(
            "/admin/review/approve/t1", json={"decision": "denied"}
        )
        assert resp.status_code == 200
        assert resp.json()["decision"] == "denied"

    def test_can_needs_more_work_decision(self):
        mock_gate = MagicMock()
        mock_gate.resume = AsyncMock(return_value=True)
        app = _make_app(mock_gate)
        client = TestClient(app)
        resp = client.post(
            "/admin/review/approve/t2", json={"decision": "needs_more_work"}
        )
        assert resp.status_code == 200
        assert resp.json()["decision"] == "needs_more_work"


class TestPendingEndpoint:
    def test_returns_503_when_human_gate_not_wired(self):
        app = _make_app(None)
        client = TestClient(app)
        resp = client.get("/admin/review/pending")
        assert resp.status_code == 503

    def test_returns_pending_list_with_mocked_gate(self):
        mock_gate = MagicMock()
        mock_gate.pending_thread_ids = ["t1", "t2"]
        mock_gate.pending_count = 2
        mock_gate.available = True
        mock_gate.enabled = True
        app = _make_app(mock_gate)
        client = TestClient(app)
        resp = client.get("/admin/review/pending")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert data["available"] is True
        assert data["enabled"] is True
        assert len(data["pending"]) == 2
        assert data["pending"][0]["thread_id"] in ("t1", "t2")

    def test_empty_pending_when_gate_has_no_threads(self):
        mock_gate = MagicMock()
        mock_gate.pending_thread_ids = []
        mock_gate.pending_count = 0
        mock_gate.available = False
        mock_gate.enabled = False
        app = _make_app(mock_gate)
        client = TestClient(app)
        resp = client.get("/admin/review/pending")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["pending"] == []
        assert data["available"] is False
        assert data["enabled"] is False
