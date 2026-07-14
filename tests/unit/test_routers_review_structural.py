"""Structural tests for routers/review.py — human-in-the-loop review endpoints."""

from __future__ import annotations

from general_ludd.routers.review import (
    ApproveRequest,
    PendingGate,
    PendingResponse,
    register,
)


class TestApproveRequest:
    def test_model_fields(self):
        req = ApproveRequest(decision="approved")
        assert req.decision == "approved"

    def test_decision_examples(self):
        for d in ("approved", "denied", "needs_more_work"):
            req = ApproveRequest(decision=d)
            assert req.decision == d


class TestPendingGate:
    def test_model_fields(self):
        gate = PendingGate(thread_id="t1")
        assert gate.thread_id == "t1"


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


class TestRegister:
    def test_register_is_callable(self):
        assert callable(register)

    def test_registers_review_routes(self):
        from fastapi import FastAPI
        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        routes = {r.path for r in app.routes}
        assert "/admin/review/approve/{thread_id}" in routes
        assert "/admin/review/pending" in routes
