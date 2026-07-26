"""G7 HITL approval gates — unit tests for ApprovalGate."""

from __future__ import annotations

from general_ludd.approval.gate import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
    ApprovalResult,
)


def test_request_approval_returns_pending_by_default() -> None:
    gate = ApprovalGate()
    req = ApprovalRequest(
        resource_id="deploy/123",
        action="deploy_to_production",
        requester="agent-7",
        reason="release v0.2.0",
    )
    result = gate.request_approval(req)

    assert result.request is req
    assert result.decision == ApprovalDecision.PENDING
    assert result.reviewer == ""
    assert result.comment == ""


def test_legacy_request_aliases_and_result_shape() -> None:
    req = ApprovalRequest(action="deploy", target="production", by="agent-1")
    assert req.resource_id == "production"
    assert req.requester == "agent-1"
    result = ApprovalResult(allowed=False, reason="test")
    assert result.allowed is False
    assert result.reason == "test"
