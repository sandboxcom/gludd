"""Todo schema and state machine."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class TodoStatus(enum.StrEnum):
    BACKLOG = "backlog"
    QUEUED = "queued"
    ACTIVE = "active"
    AWAITING_RESULT = "awaiting_result"
    REVIEWING_RETURN = "reviewing_return"
    NEEDS_MORE_WORK = "needs_more_work"
    BLOCKED = "blocked"
    MANUAL_HOLD = "manual_hold"
    APPROVAL_REQUIRED = "approval_required"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkType(enum.StrEnum):
    CODE = "code"
    TEST = "test"
    REVIEW = "review"
    REFACTOR = "refactor"
    DOCS = "docs"
    INFRA = "infra"
    PROMPT = "prompt"
    ANALYSIS = "analysis"
    AUDIT = "audit"
    RELEASE = "release"
    DEPENDENCY = "dependency"
    SECURITY = "security"
    MODEL = "model"
    UNKNOWN = "unknown"


class RiskLevel(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ResourceProfile(enum.StrEnum):
    AI_HEAVY = "ai_heavy"
    LOCAL_HEAVY = "local_heavy"
    HYBRID = "hybrid"
    NETWORK_HEAVY = "network_heavy"
    LOW_RESOURCE = "low_resource"


VALID_TRANSITIONS: dict[TodoStatus, set[TodoStatus]] = {
    TodoStatus.BACKLOG: {TodoStatus.QUEUED, TodoStatus.CANCELLED},
    TodoStatus.QUEUED: {TodoStatus.ACTIVE, TodoStatus.BLOCKED, TodoStatus.CANCELLED, TodoStatus.MANUAL_HOLD},
    TodoStatus.ACTIVE: {TodoStatus.AWAITING_RESULT, TodoStatus.BLOCKED, TodoStatus.FAILED, TodoStatus.CANCELLED},
    TodoStatus.AWAITING_RESULT: {TodoStatus.REVIEWING_RETURN, TodoStatus.BLOCKED, TodoStatus.CANCELLED},
    TodoStatus.REVIEWING_RETURN: {
        TodoStatus.COMPLETE,
        TodoStatus.NEEDS_MORE_WORK,
        TodoStatus.FAILED,
        TodoStatus.BLOCKED,
        TodoStatus.MANUAL_HOLD,
    },
    TodoStatus.NEEDS_MORE_WORK: {TodoStatus.QUEUED, TodoStatus.ACTIVE, TodoStatus.CANCELLED},
    TodoStatus.BLOCKED: {TodoStatus.QUEUED, TodoStatus.CANCELLED},
    TodoStatus.MANUAL_HOLD: {TodoStatus.QUEUED, TodoStatus.CANCELLED},
    TodoStatus.APPROVAL_REQUIRED: {TodoStatus.QUEUED, TodoStatus.CANCELLED, TodoStatus.MANUAL_HOLD},
    TodoStatus.COMPLETE: set(),
    TodoStatus.FAILED: {TodoStatus.QUEUED, TodoStatus.CANCELLED},
    TodoStatus.CANCELLED: set(),
}


def validate_transition(current: TodoStatus, target: TodoStatus) -> bool:
    return target in VALID_TRANSITIONS.get(current, set())


class Todo(BaseModel):
    todo_id: str = Field(default_factory=lambda: f"TODO-{uuid4().hex[:8].upper()}")
    title: str
    description: str = ""
    status: TodoStatus = TodoStatus.BACKLOG
    priority: int = 0
    queue: str = "core"
    tags: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    work_type: WorkType = WorkType.UNKNOWN
    resource_profile: ResourceProfile = ResourceProfile.LOW_RESOURCE
    parent_todo_id: str | None = None
    child_todo_ids: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    test_commands: list[str] = Field(default_factory=list)
    molecule_scenarios: list[str] = Field(default_factory=list)
    molecule_evidence_refs: list[str] = Field(default_factory=list)
    coverage_requirements: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    created_by: str = "agent"
    assigned_agent: str | None = None
    model_profile: str | None = None
    prompt_profile: str | None = None
    worktree: str | None = None
    branch_name: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float | None = None
    manual_hold_reason: str | None = None
    approval_policy: str = "none"
    version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    def transition_to(self, target: TodoStatus) -> None:
        if not validate_transition(self.status, target):
            raise ValueError(
                f"Invalid transition from {self.status.value} to {target.value}"
            )
        self.status = target
        self.updated_at = datetime.now(UTC)
        if target == TodoStatus.COMPLETE:
            self.completed_at = datetime.now(UTC)
