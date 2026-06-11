"""SQLAlchemy ORM models for the agentic harness."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from general_ludd.schemas.todo import TodoStatus


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _gen_todo_id() -> str:
    return f"TODO-{uuid4().hex[:8].upper()}"


class ProjectModel(Base):
    __tablename__ = "projects"

    project_id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: f"proj-{uuid4().hex[:8]}"
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    workspace_path: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    config: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class AuditEventType(enum.StrEnum):
    TODO_CREATED = "todo_created"
    TODO_STATUS_CHANGED = "todo_status_changed"
    TODO_UPDATED = "todo_updated"
    TODO_DELETED = "todo_deleted"
    TASK_RETURN_CREATED = "task_return_created"
    TASK_RETURN_CLAIMED = "task_return_claimed"
    TASK_DECISION_MADE = "task_decision_made"
    QUEUE_UPDATED = "queue_updated"
    BUCKET_LEASE_ACQUIRED = "bucket_lease_acquired"
    BUCKET_LEASE_RELEASED = "bucket_lease_released"


class TodoModel(Base):
    __tablename__ = "todos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    todo_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, default=_gen_todo_id)
    project_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("projects.project_id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=TodoStatus.BACKLOG, index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    queue: Mapped[str] = mapped_column(String(64), nullable=False, default="core", index=True)
    tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    work_type: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    resource_profile: Mapped[str] = mapped_column(String(32), nullable=False, default="low_resource")
    parent_todo_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    child_todo_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    acceptance_criteria: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    test_commands: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    molecule_scenarios: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    molecule_evidence_refs: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    coverage_requirements: Mapped[str | None] = mapped_column(String(256), nullable=True)
    dependencies: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="agent")
    assigned_agent: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_profile: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_profile: Mapped[str | None] = mapped_column(String(64), nullable=True)
    worktree: Mapped[str | None] = mapped_column(String(512), nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    artifacts: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    evidence_refs: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    plan_artifact: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    manual_hold_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_todos_status_queue", "status", "queue"),
    )

    events: Mapped[list[TodoEventModel]] = relationship(
        back_populates="todo", order_by="TodoEventModel.id"
    )


class TodoEventModel(Base):
    __tablename__ = "todo_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    todo_id: Mapped[str] = mapped_column(String(32), ForeignKey("todos.todo_id"), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("projects.project_id"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    old_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="agent")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    todo: Mapped[TodoModel] = relationship(back_populates="events")


class TaskReturnModel(Base):
    __tablename__ = "task_returns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    return_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    project_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("projects.project_id"), nullable=True, index=True
    )
    todo_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    playbook: Mapped[str] = mapped_column(String(256), nullable=False)
    queue: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    work_type: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    resource_profile: Mapped[str] = mapped_column(String(32), nullable=False, default="low_resource")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created", index=True)
    exit_code: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    artifacts: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    logs_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    diff_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    test_results_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    molecule_results_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    coverage_results_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    model_usage_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    producer_worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskDecisionModel(Base):
    __tablename__ = "task_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    return_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("projects.project_id"), nullable=True, index=True
    )
    matched_todo_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    evidence_refs: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    todo_updates: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    child_todos: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    validation_requests: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    git_requests: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    audit_notes: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    policy_flags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class QueueModel(Base):
    __tablename__ = "queues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    queue_name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    project_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("projects.project_id"), nullable=True, index=True
    )
    queue_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority_weight: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    resource_profile: Mapped[str] = mapped_column(String(32), nullable=False, default="low_resource")
    hard_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    soft_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    pid_group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    allowed_playbooks: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    allowed_model_profiles: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    allowed_prompt_profiles: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    required_molecule_coverage_profile: Mapped[str | None] = mapped_column(String(128), nullable=True)
    max_error_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    retry_policy: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class AuditEventModel(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("projects.project_id"), nullable=True, index=True
    )
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="agent")
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    details: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (
        Index("ix_audit_events_entity", "entity_type", "entity_id"),
    )


class VariableNamespaceModel(Base):
    __tablename__ = "variable_namespaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    namespace: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("projects.project_id"), nullable=True, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("namespace", "project_id", name="uq_namespace_project"),
    )

    values: Mapped[list[VariableValueModel]] = relationship(
        back_populates="namespace", cascade="all, delete-orphan"
    )


class VariableValueModel(Base):
    __tablename__ = "variable_values"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    namespace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("variable_namespaces.id"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(256), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    value_type: Mapped[str] = mapped_column(String(32), nullable=False, default="string")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    namespace: Mapped[VariableNamespaceModel] = relationship(back_populates="values")

    __table_args__ = (
        UniqueConstraint("namespace_id", "key", name="uq_variable_namespace_key"),
    )


class BucketLeaseModel(Base):
    __tablename__ = "bucket_leases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bucket_key: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("projects.project_id"), nullable=True, index=True
    )
    holder_id: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("bucket_key", "holder_id", name="uq_bucket_lease"),
    )


class PromptProfileModel(Base):
    __tablename__ = "prompt_profiles"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"pp-{uuid4().hex[:8]}"
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    task_types: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="latest")
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BenchmarkResultModel(Base):
    __tablename__ = "benchmark_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prompt_profile_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("prompt_profiles.id"), nullable=True, index=True
    )
    model_profile_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    task_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    completion_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    code_quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    instruction_adherence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    token_efficiency_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    time_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raw_output: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        Index("ix_benchmark_task_model", "task_type", "model_profile_id"),
        Index("ix_benchmark_task_prompt", "task_type", "prompt_profile_id"),
    )
