"""Tests for routers.review: Pydantic models and register function."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI

from general_ludd.routers.review import (
    ApproveRequest,
    PendingGate,
    PendingResponse,
    register,
)


class TestApproveRequest:
    def test_default_construction(self):
        req = ApproveRequest(decision="approved")
        assert req.decision == "approved"

    def test_denied_decision(self):
        req = ApproveRequest(decision="denied")
        assert req.decision == "denied"

    def test_needs_more_work(self):
        req = ApproveRequest(decision="needs_more_work")
        assert req.decision == "needs_more_work"

    def test_empty_decision_allowed_no_validator(self):
        req = ApproveRequest(decision="")
        assert req.decision == ""


class TestPendingGate:
    def test_construction(self):
        gate = PendingGate(thread_id="thread-123")
        assert gate.thread_id == "thread-123"


class TestPendingResponse:
    def test_empty_pending(self):
        resp = PendingResponse(pending=[], count=0, available=False, enabled=False)
        assert resp.pending == []
        assert resp.count == 0
        assert not resp.available
        assert not resp.enabled

    def test_with_gates(self):
        gates = [PendingGate(thread_id="t1"), PendingGate(thread_id="t2")]
        resp = PendingResponse(pending=gates, count=2, available=True, enabled=True)
        assert len(resp.pending) == 2
        assert resp.count == 2
        assert resp.available
        assert resp.enabled


class TestRegister:
    def test_register_adds_routes(self):
        app = FastAPI()
        mock_gate = MagicMock()
        mock_gate.pending_count = 0
        mock_gate.available = True
        mock_gate.enabled = True
        mock_gate.pending_thread_ids = []
        mock_gate.resume = AsyncMock(return_value=True)
        daemon_state: dict[str, object] = {"human_gate": mock_gate}

        register(app, daemon_state)

        route_paths = [r.path for r in app.routes]
        assert "/admin/review/approve/{thread_id}" in route_paths
        assert "/admin/review/pending" in route_paths

    def test_register_without_human_gate(self):
        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)

        route_paths = [r.path for r in app.routes]
        assert "/admin/review/approve/{thread_id}" in route_paths
        assert "/admin/review/pending" in route_paths
