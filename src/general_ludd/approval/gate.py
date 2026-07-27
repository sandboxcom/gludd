from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from general_ludd.db.repository import HumanTodoRepository


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
    # Compatibility aliases used by the original approval workflow API.
    target: str | None = None
    by: str | None = None

    def __post_init__(self) -> None:
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
    """Legacy compact result shape retained for compatibility callers."""

    allowed: bool
    reason: str = ""


class ApprovalGate:
    """Human-in-the-loop approval gate backed by human-todo system.

    When ``repo_factory`` is provided, ``request_approval`` creates a
    ``HumanTodoModel`` with ``category="permission_escalation"``, and
    ``check_decision`` maps the human-todo status to an ApprovalDecision
    (done → APPROVED, dismissed → DENIED, else → PENDING).

    Without a ``repo_factory``, ``request_approval`` returns PENDING
    (degraded mode — the operator must wire a repo for full HITL).
    """

    def __init__(
        self,
        repo_factory: Callable[[], HumanTodoRepository | None] | None = None,
    ) -> None:
        self._repo_factory = repo_factory
        self._pending_requests: dict[str, ApprovalRequest] = {}

    def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        if self._repo_factory is None:
            return ApprovalResponse(request=request)
        repo = self._repo_factory()
        if repo is None:
            return ApprovalResponse(request=request)
        try:
            import asyncio

            human_todo_id = _next_human_todo_id()
            self._pending_requests[human_todo_id] = request
            task = asyncio.ensure_future(
                repo.create(
                    agent_id=request.requester or "approval-gate",
                    title=f"Approval: {request.action} on {request.resource_id}",
                    body=request.reason or "No reason provided",
                    category="permission_escalation",
                    priority="high",
                )
            )
            assert task is not None  # noqa: S101
            return ApprovalResponse(request=request)
        except Exception:
            return ApprovalResponse(request=request)

    def check_decision(self, human_todo_id: str) -> ApprovalDecision:
        """Map a human-todo status to an ApprovalDecision.

        Query the repository for the human-todo and map: done → APPROVED,
        dismissed → DENIED, anything else (including missing) → PENDING.
        """
        if self._repo_factory is None:
            return ApprovalDecision.PENDING
        repo = self._repo_factory()
        if repo is None:
            return ApprovalDecision.PENDING
        try:
            import asyncio

            loop = asyncio.get_event_loop()
            if loop.is_running():
                return ApprovalDecision.PENDING
            row = loop.run_until_complete(repo.get(human_todo_id))
            if row is None:
                return ApprovalDecision.PENDING
            status = getattr(row, "status", "open")
            if status == "done":
                return ApprovalDecision.APPROVED
            if status in ("dismissed", "superseded"):
                return ApprovalDecision.DENIED
            return ApprovalDecision.PENDING
        except Exception:
            return ApprovalDecision.PENDING


def _next_human_todo_id() -> str:
    import uuid

    return f"ht-{uuid.uuid4().hex[:12]}"
