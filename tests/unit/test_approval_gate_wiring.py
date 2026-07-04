from __future__ import annotations

from fastapi import FastAPI
from starlette.testclient import TestClient

from general_ludd.approval.gate import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
    ApprovalResponse,
)
from general_ludd.routers.approval import register


def test_approval_gate_instantiation():
    gate = ApprovalGate()
    assert gate is not None


def test_approval_gate_request_approval():
    gate = ApprovalGate()
    req = ApprovalRequest(
        resource_id="test-resource",
        action="deploy",
        requester="test-agent",
        reason="testing",
    )
    resp = gate.request_approval(req)
    assert isinstance(resp, ApprovalResponse)
    assert resp.request == req
    assert resp.decision == ApprovalDecision.PENDING
    assert resp.reviewer == ""
    assert resp.comment == ""


def test_approval_gate_wired_to_app_state():
    app = FastAPI()
    gate = ApprovalGate()
    app.state._approval_gate = gate

    register(app, {})

    client = TestClient(app)
    response = client.get("/admin/approval/status")
    assert response.status_code == 200
    data = response.json()
    assert data["wired"] is True
    assert data["gate_type"] == "ApprovalGate"


def test_approval_gate_not_wired():
    app = FastAPI()
    register(app, {})

    client = TestClient(app)
    response = client.get("/admin/approval/status")
    assert response.status_code == 200
    data = response.json()
    assert data["wired"] is False
    assert data["gate_type"] == "None"


def test_approval_decision_enum_values():
    assert ApprovalDecision.APPROVED.value == "approved"
    assert ApprovalDecision.DENIED.value == "denied"
    assert ApprovalDecision.PENDING.value == "pending"
