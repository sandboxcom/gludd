"""Todo schema and state machine."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

_MAX_PRIORITY: int = 1000


class TodoStatus(enum.StrEnum):
    """Lifecycle states for persisted and in-memory todos."""

    BACKLOG = "backlog"
    SCHEDULED = "scheduled"
    QUEUED = "queued"
    ACTIVE = "active"
    AWAITING_RESULT = "awaiting_result"
    REVIEWING_RETURN = "reviewing_return"
    NEEDS_MORE_WORK = "needs_more_work"
    BLOCKED = "blocked"
    BLOCKED_ON_HUMAN = "blocked_on_human"
    MANUAL_HOLD = "manual_hold"
    APPROVAL_REQUIRED = "approval_required"
    APPROVED = "approved"
    COMPLETE = "complete"
    BUDGET_EXCEEDED = "budget_exceeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkType(enum.StrEnum):
    """Supported categories of todo work."""

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
    """Risk classifications used for todo admission and review."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ResourceProfile(enum.StrEnum):
    """Resource-shape hints used when scheduling todo work."""

    AI_HEAVY = "ai_heavy"
    LOCAL_HEAVY = "local_heavy"
    HYBRID = "hybrid"
    NETWORK_HEAVY = "network_heavy"
    LOW_RESOURCE = "low_resource"


VALID_TRANSITIONS: dict[TodoStatus, set[TodoStatus]] = {
    TodoStatus.BACKLOG: {TodoStatus.QUEUED, TodoStatus.SCHEDULED, TodoStatus.CANCELLED},
    # SCHEDULED is the holding state for time-based / cron todos. A one-shot
    # scheduled todo flips to QUEUED when due; a recurring (cron) template stays
    # SCHEDULED and spawns QUEUED child clones on each fire (the scheduler does
    # not transition the template — it advances next_run_at).
    TodoStatus.SCHEDULED: {TodoStatus.QUEUED, TodoStatus.CANCELLED, TodoStatus.MANUAL_HOLD},
    TodoStatus.QUEUED: {
        TodoStatus.ACTIVE,
        TodoStatus.BLOCKED,
        TodoStatus.BLOCKED_ON_HUMAN,
        TodoStatus.CANCELLED,
        TodoStatus.MANUAL_HOLD,
    },
    TodoStatus.ACTIVE: {
        TodoStatus.AWAITING_RESULT,
        TodoStatus.BLOCKED,
        TodoStatus.BLOCKED_ON_HUMAN,
        TodoStatus.FAILED,
        TodoStatus.CANCELLED,
        TodoStatus.BUDGET_EXCEEDED,
    },
    TodoStatus.AWAITING_RESULT: {
        TodoStatus.REVIEWING_RETURN,
        TodoStatus.BLOCKED,
        TodoStatus.CANCELLED,
        TodoStatus.BUDGET_EXCEEDED,
    },
    TodoStatus.REVIEWING_RETURN: {
        TodoStatus.COMPLETE,
        TodoStatus.NEEDS_MORE_WORK,
        TodoStatus.FAILED,
        TodoStatus.BLOCKED,
        TodoStatus.MANUAL_HOLD,
        TodoStatus.BUDGET_EXCEEDED,
    },
    TodoStatus.NEEDS_MORE_WORK: {TodoStatus.QUEUED, TodoStatus.ACTIVE, TodoStatus.CANCELLED},
    TodoStatus.BLOCKED: {TodoStatus.QUEUED, TodoStatus.CANCELLED},
    TodoStatus.BLOCKED_ON_HUMAN: {TodoStatus.QUEUED, TodoStatus.CANCELLED},
    TodoStatus.MANUAL_HOLD: {TodoStatus.QUEUED, TodoStatus.CANCELLED},
    TodoStatus.APPROVAL_REQUIRED: {
        TodoStatus.APPROVED,
        TodoStatus.QUEUED,
        TodoStatus.CANCELLED,
        TodoStatus.MANUAL_HOLD,
    },
    # APPROVED is a durable non-runnable holding state. Only the explicit
    # legacy apply endpoints may consume it by claiming APPROVED -> ACTIVE.
    TodoStatus.APPROVED: {TodoStatus.ACTIVE, TodoStatus.CANCELLED},
    TodoStatus.COMPLETE: set(),
    TodoStatus.FAILED: {TodoStatus.QUEUED, TodoStatus.CANCELLED},
    TodoStatus.CANCELLED: set(),
}


def validate_transition(current: TodoStatus, target: TodoStatus) -> bool:
    """Return whether the lifecycle permits ``current`` to become ``target``."""
    return target in VALID_TRANSITIONS.get(current, set())


_TODO_MAX_PRIORITY: int = 1000


class Todo(BaseModel):
    """Validated application representation of a unit of work."""

    todo_id: str = Field(default_factory=lambda: f"TODO-{uuid4().hex[:8].upper()}")
    title: str
    description: str = ""
    project_id: str | None = None
    status: TodoStatus = TodoStatus.BACKLOG
    priority: int = 0
    queue: str = "core"
    tags: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    work_type: WorkType = WorkType.UNKNOWN
    resource_profile: ResourceProfile = ResourceProfile.LOW_RESOURCE
    estimated_cost_usd: float | None = None
    actual_cost_accrued: float = 0.0
    parent_todo_id: str | None = None
    child_todo_ids: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=20)
    definition_of_done: str = Field(default="", max_length=4096)
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
    plan_artifact: str | None = None
    approved_artifact_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    confidence: float | None = None
    manual_hold_reason: str | None = None
    approval_policy: str = "none"
    version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    # ── Scheduling / cron-style recurrence (integrated into the todo) ──
    # scheduled_at: one-shot fire time — when now >= scheduled_at the scheduler
    #   transitions the todo SCHEDULED -> QUEUED (runs once).
    # cron: a 5-field cron expression for recurrence — the todo stays SCHEDULED
    #   as a template and a QUEUED child clone is spawned on each fire.
    scheduled_at: datetime | None = None
    cron: str | None = None
    schedule_timezone: str = "UTC"
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    run_count: int = 0
    max_runs: int | None = None
    schedule_paused: bool = False

    @field_validator("title", "queue", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("field must not be empty")
        return v

    @field_validator("confidence")
    @classmethod
    def _confidence_range(cls, v: float | None) -> float | None:
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError("confidence must be between 0.0 and 1.0")
        return v

    @field_validator("priority")
    @classmethod
    def _priority_range(cls, v: int) -> int:
        if v < 0:
            raise ValueError("priority must be non-negative")
        if v > _TODO_MAX_PRIORITY:
            raise ValueError(f"priority must not exceed {_TODO_MAX_PRIORITY}")
        return v

    @field_validator("version")
    @classmethod
    def _version_minimum(cls, v: int) -> int:
        if v < 1:
            raise ValueError("version must be at least 1")
        return v

    @field_validator("run_count")
    @classmethod
    def _run_count_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("run_count must be non-negative")
        return v

    @field_validator("max_runs")
    @classmethod
    def _max_runs_positive(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("max_runs must be at least 1 when set")
        return v

    @field_validator("cron")
    @classmethod
    def _cron_well_formed(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        # A standard cron expression has 5 whitespace-separated fields. Full
        # semantic validation (and next-run computation) is done by the
        # scheduler via croniter; here we reject obviously-malformed input
        # without importing croniter at the schema layer.
        if len(v.split()) != 5:
            raise ValueError("cron must be a 5-field expression (min hour dom month dow)")
        return v

    @model_validator(mode="after")
    def _check_completed_at(self) -> Todo:
        if self.completed_at is not None and self.status != TodoStatus.COMPLETE:
            raise ValueError("completed_at can only be set when status is COMPLETE")
        return self

    def transition_to(self, target: TodoStatus) -> None:
        """Apply one valid lifecycle transition and update its timestamps."""
        if not validate_transition(self.status, target):
            raise ValueError(
                f"Invalid transition from {self.status.value} to {target.value}"
            )
        self.status = target
        self.updated_at = datetime.now(UTC)
        if target == TodoStatus.COMPLETE:
            self.completed_at = datetime.now(UTC)
