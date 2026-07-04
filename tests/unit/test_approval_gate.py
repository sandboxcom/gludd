"""G7 HITL approval gates — unit tests for ApprovalGate."""

from __future__ import annotations

from general_ludd.approval.gate import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
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
