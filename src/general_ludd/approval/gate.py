from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ApprovalDecision(Enum):
    APPROVED = "approved"
    DENIED = "denied"
    PENDING = "pending"


@dataclass
class ApprovalRequest:
    resource_id: str = ""
    action: str = ""
    requester: str = ""
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    target: str | None = None
    by: str | None = None

    def __post_init__(self) -> None:
        """Normalize names used by the original compact approval API."""
        if not self.resource_id and self.target:
            self.resource_id = self.target
        if not self.requester and self.by:
            self.requester = self.by


@dataclass
class ApprovalResponse:
    request: ApprovalRequest
    decision: ApprovalDecision = ApprovalDecision.PENDING
    reviewer: str = ""
    comment: str = ""


@dataclass
class ApprovalResult:
    """Compact compatibility result for synchronous approval checks."""

    allowed: bool
    reason: str = ""


class ApprovalGate:
    def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(request=request)

    def check(self, request: ApprovalRequest) -> ApprovalResult:
        """Adapt the response model to the original compact check contract."""
        response = self.request_approval(request)
        return ApprovalResult(
            allowed=response.decision is ApprovalDecision.APPROVED,
            reason=response.comment or response.decision.value,
        )
