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
    resource_id: str
    action: str
    requester: str
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApprovalResponse:
    request: ApprovalRequest
    decision: ApprovalDecision = ApprovalDecision.PENDING
    reviewer: str = ""
    comment: str = ""


class ApprovalGate:
    def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(request=request)
