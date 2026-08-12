"""SQLAlchemy ORM models for the agentic harness."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from general_ludd.schemas.todo import TodoStatus


class Base(DeclarativeBase):
    pass


# D-07 (docs/audit/NEW_FINDINGS_2026-06-16.md db/models.py P2): several Text
# columns store JSON-in-Text blobs (task-decision review payloads, audit-event
# details, ...) with no upper bound. An unbounded write is a DoS vector — a
# single giant row blows up WAL size, query memory, and backup/restore time.
# 64 KiB is generous headroom for the JSON payloads these columns actually
# carry (small lists/dicts of ids and short strings) while still capping the
# worst case.
MAX_JSON_BLOB_LEN = 65536


def _len_check(column: str, table: str, max_len: int = MAX_JSON_BLOB_LEN) -> CheckConstraint:
    """Return a ``CHECK(length(col) <= max_len)`` constraint bounding a Text blob.

    ``length()`` is SQLite's byte-length builtin, evaluated per-row on every
    INSERT/UPDATE — this is enforced by the database itself, not just at the
    ORM layer, so it also protects direct SQL writes.
    """
    return CheckConstraint(f"length({column}) <= {max_len}", name=f"ck_{table}_{column}_len")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Persist UTC timestamps and restore timezone awareness on every backend.

    SQLite stores ``DateTime(timezone=True)`` values without an offset.  Returning
    those naive values from the ORM makes normal UTC comparisons fail at runtime,
    while PostgreSQL returns aware values.  This decorator keeps the model API
    consistent across both backends and normalizes caller-provided timestamps to
    UTC before persistence.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def _gen_todo_id() -> str:
    return f"TODO-{uuid4().hex[:8].upper()}"


class ProjectModel(Base):
    __tablename__ = "projects"

    project_id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: f"proj-{uuid4().hex[:8]}")
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    workspace_path: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    config: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class RelationType(enum.StrEnum):
    PARENT = "parent"  # the environment THIS project runs inside
    CHILD = "child"  # a project that runs inside THIS one
    SIBLING = "sibling"  # peer under a shared parent (gludd may control it)
    EXTERNAL = "external"  # a neighbor gludd does NOT control


class LocationKind(enum.StrEnum):
    GLUDD_PROJECT_NAME = "gludd_project_name"  # resolves to a ProjectModel.name
    DIRECTORY = "directory"  # absolute/relative path on disk
    URL = "url"  # git/https/service URL


def _gen_rel_id() -> str:
    return f"rel-{uuid4().hex[:12]}"


class ProjectRelationshipModel(Base):
    """A declared edge from one project to a neighbor (parent/child/sibling/external).

    Edges are USER-DECLARED (config or API), never inferred, so the AI never
    guesses topology. The neighbor is identified by (location_kind, location_value):
    a gludd project NAME, a DIRECTORY path, or a URL. When the neighbor is itself a
    gludd project, ``related_project_id`` is resolved and FK-linked; for external
    neighbors it stays NULL and only the location fields identify it.

    Two FKs to ``projects`` with different ``ondelete``:
      - ``project_id`` (the owning "from" side) is CASCADE: an edge has no meaning
        without its owning project.
      - ``related_project_id`` (a resolved neighbor) is SET NULL: losing the
        neighbor must not delete the edge — the operator's declared intent survives
        so it can re-resolve later. Mirrors the repo-wide SET NULL convention.

    One-parent cardinality (at most one ``relation_type='parent'`` per project) is
    not portably expressible as a SQL UNIQUE, so it is enforced by the repository
    guard (``ProjectRelationshipRepository.set_parent``); PostgreSQL also gets a
    partial unique index in the migration.
    """

    __tablename__ = "project_relationships"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_gen_rel_id)
    project_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("projects.project_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relation_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    location_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # The identifier of the neighbor under location_kind (a name, a path, a URL).
    location_value: Mapped[str] = mapped_column(String(1024), nullable=False)
    # Resolved gludd project id of the neighbor, when it IS a gludd project and we
    # could resolve its name/dir/url to a ProjectModel. NULL for external/unresolved.
    related_project_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("projects.project_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    controlled_by_gludd: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Optional free-form hint describing the interface this edge implies
    # (e.g. "GET /health", "publishes kafka topic orders").
    interface_hint: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Optional structured interface contract (JSON-in-Text). Empty by default.
    interface_contract: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        # A project declares a given neighbor under a given relation at most once.
        UniqueConstraint(
            "project_id",
            "relation_type",
            "location_kind",
            "location_value",
            name="uq_project_relationship_edge",
        ),
        Index("ix_project_rel_from_type", "project_id", "relation_type"),
        Index("ix_project_rel_related", "related_project_id"),
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
        String(32),
        ForeignKey("projects.project_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=TodoStatus.BACKLOG, index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    queue: Mapped[str] = mapped_column(String(64), nullable=False, default="core", index=True)
    tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    work_type: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown", index=True)
    resource_profile: Mapped[str] = mapped_column(String(32), nullable=False, default="low_resource")
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_cost_accrued: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    parent_todo_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("todos.todo_id", ondelete="SET NULL"),
        nullable=True,
    )
    child_todo_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    acceptance_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    definition_of_done: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_utcnow, onupdate=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    # ── Scheduling / cron-style recurrence ──────────────────────────────────
    # scheduled_at: one-shot fire time. The scheduler promotes the todo
    # SCHEDULED→QUEUED when now >= scheduled_at (cron must be None).
    scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # cron: 5-field cron expression (croniter grammar). When set, this row
    # is a TEMPLATE that stays SCHEDULED; the scheduler spawns a QUEUED
    # child clone on each fire and advances next_run_at.
    cron: Mapped[str | None] = mapped_column(String(256), nullable=True)
    schedule_timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schedule_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_todos_status_queue", "status", "queue"),
        # Supports TodoRepository.list_due_scheduled (scheduler due-todo query):
        # the (status, schedule_paused) prefix narrows to pending schedules, then
        # the datetime columns filter on COALESCE(next_run_at, scheduled_at).
        # Declared here so the ORM matches migration 010 (alembic-check parity).
        Index(
            "ix_todos_scheduled_lookup",
            "status",
            "schedule_paused",
            "next_run_at",
            "scheduled_at",
        ),
        # E12: TodoRepository.claim_runnable filters QUEUED todos and orders by
        # (priority DESC, created_at, id) every claim. Only `status` was
        # indexed, so priority/created_at were sorted without index support on
        # a table that grows with every todo ever created. This composite
        # index covers the WHERE + ORDER BY together.
        Index("ix_todos_status_priority_created_at", "status", "priority", "created_at"),
        # E12: composite index for scheduler and queue-filtered claim queries.
        # Queries that filter by status+queue with a time-range on scheduled_at
        # (e.g. list_due_scheduled, claim variants filtering on queue) benefit
        # from this covering index that matches WHERE + time sort.
        Index("ix_todos_status_queue_scheduled", "status", "queue", "scheduled_at"),
        # E12: _reap_stuck_todos queries WHERE status='active' AND updated_at < cutoff.
        # status alone is indexed; this composite makes the reaper query index-only.
        Index("ix_todos_status_updated_at", "status", "updated_at"),
        # H.14: priority bounded at DB level so direct SQL writes can't bypass
        # the application-layer clamping in TodoRepository._validate_create_data.
        CheckConstraint("priority >= 0 AND priority <= 1000", name="ck_todos_priority_range"),
    )

    __mapper_args__: ClassVar[dict[str, Any]] = {"version_id_col": version}

    events: Mapped[list[TodoEventModel]] = relationship(
        back_populates="todo",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class TodoEventModel(Base):
    __tablename__ = "todo_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    todo_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("todos.todo_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("projects.project_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    old_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="agent")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_utcnow)

    todo: Mapped[TodoModel] = relationship(back_populates="events")


class TaskReturnModel(Base):
    __tablename__ = "task_returns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    return_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    project_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("projects.project_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    todo_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("todos.todo_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
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

    __table_args__ = (
        Index("ix_task_returns_status_created", "status", "created_at"),
        # E12: claim_unreviewed queries WHERE status='created' AND project_id=?
        # ORDER BY created_at ASC. The 2-column index on (status,created_at)
        # doesn't cover the project_id filter; this 3-column composite does.
        Index("ix_task_returns_status_project_created", "status", "project_id", "created_at"),
    )


class TaskDecisionModel(Base):
    __tablename__ = "task_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    return_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("task_returns.return_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    project_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("projects.project_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    matched_todo_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("todos.todo_id", ondelete="SET NULL"),
        nullable=True,
    )
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

    __table_args__ = (
        # E11: EventLoop's decision-history tick (loop.py) runs
        # `ORDER BY created_at DESC LIMIT 50` against this insert-only table on
        # every tick. Without an index that is a full-table scan per tick that
        # only grows as decisions accumulate.
        Index("ix_task_decisions_created_at", "created_at"),
        # D-07: bound unbounded JSON-in-Text blob columns (DoS via giant rows).
        _len_check("todo_updates", "task_decisions"),
        _len_check("child_todos", "task_decisions"),
        _len_check("validation_requests", "task_decisions"),
        _len_check("git_requests", "task_decisions"),
        _len_check("audit_notes", "task_decisions"),
        _len_check("policy_flags", "task_decisions"),
    )


class QueueModel(Base):
    __tablename__ = "queues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    queue_name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    project_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("projects.project_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
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
        String(32),
        ForeignKey("projects.project_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="agent")
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    details: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    # Ornith scaffold provenance (migration 014_add_ornith_audit_fields). All
    # nullable so pre-existing rows remain valid.
    scaffold_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_audit_events_entity", "entity_type", "entity_id"),
        # D-07: bound unbounded JSON-in-Text blob column (DoS via giant rows).
        _len_check("details", "audit_events"),
    )


class DeploymentRecordModel(Base):
    """Durable cross-worker source of truth for chargeable deployments."""

    __tablename__ = "deployment_records"

    instance_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    working_dir: Mapped[str] = mapped_column(String(1024), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    model_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="running", index=True)
    ip_address: Mapped[str | None] = mapped_column(String(256), nullable=True)
    endpoint_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    destroy_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_deployment_records_revision_positive"),
        CheckConstraint(
            "((state = 'destroying' AND destroy_owner IS NOT NULL) OR "
            "(state <> 'destroying' AND destroy_owner IS NULL))",
            name="ck_deployment_records_destroy_owner_state",
        ),
    )


class VariableNamespaceModel(Base):
    __tablename__ = "variable_namespaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    namespace: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("projects.project_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (UniqueConstraint("namespace", "project_id", name="uq_namespace_project"),)

    values: Mapped[list[VariableValueModel]] = relationship(
        back_populates="namespace", cascade="all, delete-orphan", passive_deletes=True
    )


class VariableValueModel(Base):
    __tablename__ = "variable_values"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    namespace_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("variable_namespaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key: Mapped[str] = mapped_column(String(256), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    value_type: Mapped[str] = mapped_column(String(32), nullable=False, default="string")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    namespace: Mapped[VariableNamespaceModel] = relationship(back_populates="values")

    __table_args__ = (UniqueConstraint("namespace_id", "key", name="uq_variable_namespace_key"),)


class BucketLeaseModel(Base):
    __tablename__ = "bucket_leases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bucket_key: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("projects.project_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    holder_id: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("bucket_key", "holder_id", name="uq_bucket_lease"),
        Index("ix_bucket_leases_key_expires", "bucket_key", "expires_at"),
    )


class AzureCostPredictionModel(Base):
    """Immutable Azure prediction envelope plus mutable reconciliation cursor."""

    __tablename__ = "azure_cost_predictions"

    prediction_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    prediction_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    todo_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    identity_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    identity_payload: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    state_rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    not_before: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, index=True)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state_changed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_utcnow)
    finalized_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_utcnow, onupdate=_utcnow)

    @property
    def id(self) -> tuple[str, int]:
        """Stable composite identity convenient for repository callers."""
        return (self.prediction_id, self.prediction_version)

    __table_args__ = (
        CheckConstraint(
            "prediction_version > 0",
            name="ck_azure_cost_predictions_version_positive",
        ),
        CheckConstraint(
            "fencing_token >= 0",
            name="ck_azure_cost_predictions_fencing_nonnegative",
        ),
        CheckConstraint(
            "state_rank >= 0 AND state_rank <= 7",
            name="ck_azure_cost_predictions_state_rank",
        ),
        CheckConstraint(
            "((lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL))",
            name="ck_azure_cost_predictions_lease_pair",
        ),
        _len_check("identity_payload", "azure_cost_predictions"),
        Index(
            "ix_azure_cost_predictions_due_claim",
            "state_rank",
            "not_before",
            "lease_expires_at",
        ),
    )


class AzureCostObservationModel(Base):
    """Append-only billed-cost row retained at its source snapshot identity."""

    __tablename__ = "azure_cost_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prediction_id: Mapped[str] = mapped_column(String(64), nullable=False)
    prediction_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(256), nullable=False)
    row_identity: Mapped[str] = mapped_column(String(256), nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_utcnow)

    __table_args__ = (
        ForeignKeyConstraint(
            ["prediction_id", "prediction_version"],
            [
                "azure_cost_predictions.prediction_id",
                "azure_cost_predictions.prediction_version",
            ],
            ondelete="CASCADE",
            name="fk_azure_cost_observations_prediction",
        ),
        UniqueConstraint(
            "prediction_id",
            "prediction_version",
            "source",
            "snapshot_id",
            "row_identity",
            name="uq_azure_cost_observation_identity",
        ),
        CheckConstraint(
            "fencing_token > 0",
            name="ck_azure_cost_observations_fencing_positive",
        ),
        _len_check("payload", "azure_cost_observations"),
        Index(
            "ix_azure_cost_observations_prediction",
            "prediction_id",
            "prediction_version",
        ),
    )


class AzureCostOutboxEventModel(Base):
    """Transactional state event awaiting publication by an outbox relay."""

    __tablename__ = "azure_cost_outbox_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    prediction_id: Mapped[str] = mapped_column(String(64), nullable=False)
    prediction_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    deduplication_key: Mapped[str] = mapped_column(String(256), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_utcnow)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["prediction_id", "prediction_version"],
            [
                "azure_cost_predictions.prediction_id",
                "azure_cost_predictions.prediction_version",
            ],
            ondelete="CASCADE",
            name="fk_azure_cost_outbox_prediction",
        ),
        UniqueConstraint(
            "deduplication_key",
            name="uq_azure_cost_outbox_deduplication_key",
        ),
        _len_check("payload", "azure_cost_outbox_events"),
        Index("ix_azure_cost_outbox_pending", "published_at", "created_at"),
        Index(
            "ix_azure_cost_outbox_prediction",
            "prediction_id",
            "prediction_version",
        ),
    )


class FeatureStatus(enum.StrEnum):
    REQUESTED = "requested"
    # Compatibility alias: old clients called the initial requested state
    # ``planned``. Persist the canonical value for mixed-version workers.
    PLANNED = "requested"
    IN_PROGRESS = "in_progress"
    IMPLEMENTED = "implemented"
    VERIFIED = "verified"
    REGRESSED = "regressed"


def _gen_feature_id() -> str:
    return f"FEAT-{uuid4().hex[:8].upper()}"


class FeatureModel(Base):
    """Feature-database row: tracks a requested product feature through verification.

    Evidence references (evidence column, JSON list) follow the same grammar as
    make audit-evidence:
      test:<pytest-node-id>   role:<name>   module:<name>
      molecule:<scenario>     file:<path>::<symbol>

    JSON lists (acceptance_criteria, evidence, last_verify_detail) are stored as
    Text following the PromptProfileModel / TodoModel JSON-in-Text convention.
    """

    __tablename__ = "features"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_gen_feature_id)
    project_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("projects.project_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="general")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=FeatureStatus.REQUESTED, index=True)
    # JSON list of acceptance-criterion strings
    acceptance_criteria: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # JSON list of evidence-reference strings (grammar: test: role: module: molecule: file:)
    evidence: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    verifier_kind: Mapped[str] = mapped_column(String(64), nullable=False, default="evidence")
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False, default="agent")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # JSON object: per-ref pass/fail detail from last verify run
    last_verify_detail: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class PromptProfileModel(Base):
    __tablename__ = "prompt_profiles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"pp-{uuid4().hex[:8]}")
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    task_types: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="latest")
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentMessageModel(Base):
    """Message-queue row for inter-agent / inter-role coordination.

    A message is addressed to a single ``recipient`` (a role/agent name) or to
    the literal string ``"broadcast"`` to reach every recipient. ``read_at`` is
    NULL until acked; ``ttl_seconds`` (when set) makes the row eligible for
    purge once ``created_at + ttl_seconds`` is in the past.
    """

    __tablename__ = "agent_messages"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: f"MSG-{uuid4().hex[:12].upper()}")
    project_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("projects.project_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sender: Mapped[str] = mapped_column(String(128), nullable=False)
    recipient: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    topic: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ttl_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (Index("ix_agent_messages_recipient_read", "recipient", "read_at"),)


class SpendRecordModel(Base):
    """Persisted spend event for the rolling-window spend limiter.

    ``ts`` is a Unix epoch float so arithmetic stays in pure Python / SQL
    without timezone machinery.  ``project_id`` and ``model`` are optional
    metadata and are NOT used in the rolling-window math — only ``ts`` and
    ``cost_usd`` matter for aggregation.
    """

    __tablename__ = "spend_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("projects.project_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    ts: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (Index("ix_spend_records_ts_kind", "ts", "kind"),)


class RoleRunModel(Base):
    """Records each time a role runs for a project.  Used by the accounting ledger."""

    __tablename__ = "role_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("projects.project_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (Index("ix_role_runs_project_role", "project_id", "role"),)


class BenchmarkResultModel(Base):
    __tablename__ = "benchmark_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prompt_profile_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("prompt_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Project-hierarchy phase 3: benchmark history becomes project-aware so the
    # AdaptiveRouter can borrow proven picks ACROSS declared project edges with
    # edge-distance decay. NULL = global/legacy history (today's behaviour). The
    # SET NULL FK matches the repo-wide project_id convention (a deleted project
    # degrades its history to global rather than destroying the benchmark rows).
    project_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("projects.project_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
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
    task_role: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (
        Index("ix_benchmark_task_model", "task_type", "model_profile_id"),
        Index("ix_benchmark_task_prompt", "task_type", "prompt_profile_id"),
        # Project-aware aggregation key: get_aggregate_scores(project_id=...)
        # filters and groups on (project_id, task_type) when borrowing is on.
        Index("ix_benchmark_project_task", "project_id", "task_type"),
        Index("ix_benchmark_task_role", "task_role"),
    )


def _gen_memory_id() -> str:
    return f"mem-{uuid4().hex[:12]}"


class MemoryRecordModel(Base):
    """Persistent agent-memory record (G1).

    A key-value store scoped by (agent_id, namespace) so each agent has its
    own namespace-isolated working memory that survives restarts. TTL support
    allows automatic expiry of transient entries.
    """

    __tablename__ = "memory_records"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_gen_memory_id)
    project_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("projects.project_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(256), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    namespace: Mapped[str] = mapped_column(String(128), nullable=False, default="default", index=True)
    ttl_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "agent_id",
            "key",
            "namespace",
            "project_id",
            name="uq_memory_agent_key_ns_project",
        ),
        Index("ix_memory_namespace", "namespace"),
    )


class TaskEmbeddingModel(Base):
    """Canonical embedding vector per task type (Tier 2 RAG routing substrate).

    One row per ``task_type`` (the primary key). ``embedding`` stores a JSON
    list of floats produced by the embedding model; ``dim`` records its
    length so consumers can sanity-check shape without re-parsing. Used by
    the AdaptiveRouter to weight historical aggregates by cosine similarity
    to the current task, turning the flat 10-arm bandit into a soft cluster.
    """

    __tablename__ = "task_embeddings"

    task_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # JSON array of floats (Text column — JSON-in-Text convention used
    # elsewhere in this file, e.g. tags/acceptance_criteria). Empty list by
    # default so a freshly-created row is always parseable.
    embedding: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    dim: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class StsAuditModel(Base):
    __tablename__ = "sts_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    issuer_agent_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    subject_agent_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    spec_yaml: Mapped[str] = mapped_column(Text, nullable=False)
    issued_at: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    last_used_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    events: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (Index("ix_sts_audit_agents", "issuer_agent_id", "subject_agent_id"),)


class AgentTokenModel(Base):
    """Per-subagent OpenBao AppRole token record.

    Never stores secret_id — that lives only in OpenBao. Fields per
    docs/specs/FEATURE_STS_TOKENS.md §6.
    """

    __tablename__ = "agent_tokens"

    token_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    parent_agent_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    role_name: Mapped[str] = mapped_column(String(256), nullable=False)
    role_id: Mapped[str] = mapped_column(String(128), nullable=False)
    scope_hash: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    scope_actions: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    hydration_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # NOTE: ``index=True`` on ``agent_id`` and ``parent_agent_id`` above already
    # creates the ``ix_agent_tokens_*`` indexes. We do NOT duplicate them in a
    # ``__table_args__`` tuple — doing so emits two ``CREATE INDEX`` statements
    # with the same name, which fails on SQLite/PostgreSQL at create_all time.


class PermissionEscalationRequestModel(Base):
    """Persistent record of a permission-escalation request.

    Populated by POST /admin/perm/escalation-request. Each row records the
    agent's current spec (yaml), the additional capabilities it requested
    (yaml), the alternatives it documented, and the human/automated
    decision.
    """

    __tablename__ = "permission_escalation_request"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    current_spec_yaml: Mapped[str] = mapped_column(Text, nullable=False)
    requested_capabilities_yaml: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    alternatives_tried_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    human_reviewer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)


def _gen_human_todo_id() -> str:
    return f"HTODO-{uuid4().hex[:10].upper()}"


class HumanTodoModel(Base):
    """A request from an agent to a human.

    Distinct from :class:`TodoModel` (which represents work a user assigns to
    an agent). A ``HumanTodoModel`` is filed by an agent when it cannot
    complete its work without a human action — a permission escalation, an
    external action, a decision, missing input, or an unblocker. The human
    resolves it (done / dismissed / superseded); the resolution is fed back to
    the agent via ``human_resolution`` and, when ``parent_agent_todo_id`` is
    set, the parent agent todo is unblocked.

    Lifecycle: ``open`` → ``in_progress`` → {``done``, ``dismissed``,
    ``superseded``}. Terminal states: ``done``, ``dismissed``, ``superseded``.
    """

    __tablename__ = "human_todos"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_gen_human_todo_id)
    # The agent todo whose progress is blocked on this human-todo. NULL when
    # the agent is merely logging a need (no parent todo to block).
    parent_agent_todo_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("todos.todo_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="medium", index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", index=True)
    human_resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    human_resolver: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # JSON array of tag strings, following the JSON-in-Text convention.
    tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    __table_args__ = (
        Index("ix_human_todos_status_category", "status", "category"),
        Index("ix_human_todos_status_priority", "status", "priority"),
    )


def _gen_remediation_id() -> str:
    return f"REM-{uuid4().hex[:12].upper()}"


class RemediationActionModel(Base):
    """Audit-trail row for every remediation action the dispatcher took.

    One row per (blocked task, action kind). The dispatcher writes a row
    whether the action succeeded, failed, or was a no-op so the operator
    can query the full history via ``GET /admin/remediation/history`` and
    the ``gludd remediation history`` CLI.

    Additive table: participates in ``Base.metadata`` so ``create_all``
    picks it up without a heavy migration, mirroring the
    :class:`HumanTodoModel` and :class:`AgentMessageModel` precedent.
    """

    __tablename__ = "remediation_actions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_gen_remediation_id)
    # The blocked task that triggered this action. Synthetic id
    # (``HTODO:<id>``) when the finding came from a stale human-todo with
    # no parent agent todo.
    blocked_todo_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("projects.project_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    blocker_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    action_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Short human-readable summary of what was done.
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Free-form detail: the new todo id, the scheduled cron entry, the
    # filed human-todo id, etc.
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # ``ok`` is False when the dispatcher tried and the action raised
    # (logged for visibility, not retried inline). no_action rows are
    # always ok=True with a reason.
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(256), nullable=True, unique=True, index=True)

    __table_args__ = (
        Index("ix_remediation_actions_project_created", "project_id", "created_at"),
        Index("ix_remediation_actions_blocked_kind", "blocked_todo_id", "action_kind"),
        Index(
            "ix_remediation_actions_dedup",
            "blocked_todo_id",
            "action_kind",
            "created_at",
        ),
    )


def _gen_model_call_id() -> str:
    return f"MC-{uuid4().hex[:12].upper()}"


class ModelCallLogModel(Base):
    """Immutable log of every model call made through the gateway.

    Each row records one invocation: service, model name, token counts,
    cost, duration, success/failure, and optional error details. Populated
    by the worker HTTP path and the EventLoop in-process runner path so
    that all model calls are centrally observable.
    """

    __tablename__ = "model_call_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_model_call_id)
    todo_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Provider name (e.g. ``"openai"``, ``"anthropic"``).
    service: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_profile_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False, default="generation")
    work_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)

    __table_args__ = (
        Index("ix_model_call_logs_created", "created_at"),
        Index("ix_model_call_logs_profile_created", "model_profile_id", "created_at"),
    )


class ModelPerformanceModel(Base):
    """Pre-aggregated rolling performance stats per model profile.

    Updated by ``ModelPerformanceRepository.refresh_recent_stats()``.  The
    log table keeps raw per-call data; this table keeps the derived summary
    so dashboards and the adaptive router can read performance without
    scanning the entire log table.
    """

    __tablename__ = "model_performance"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_model_call_id)
    model_profile_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    service: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    total_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    successful_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    last_call_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_call_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


def _gen_ornith_pair_id() -> str:
    return f"ORN-{uuid4().hex}"


class OrnithTrainingPairModel(Base):
    """A ``(scaffold, outcome)`` pair captured from an Ornith invocation.

    Feeds the offline RL trainer per the symbiotic design
    (``docs/design/SYMBIOTIC_AGENT_INTEGRATION.md`` §5.7). The scaffold half
    is recorded at ``solve()`` time; the outcome half is set later by the
    :class:`~general_ludd.ornith.outcome_observer.OutcomeObserver` when the
    gate / review / git-history decides what actually happened to the
    scaffold Ornith produced.
    """

    __tablename__ = "ornith_training_pairs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_ornith_pair_id)
    invoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    task_description: Mapped[str] = mapped_column(Text, nullable=False)
    # JSON array of target file paths.
    target_files: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    scaffold_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scaffold_content: Mapped[str] = mapped_column(Text, nullable=False)
    scaffold_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    iterations_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_consumed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_sha: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    outcome_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    # JSON dict: gate output, review notes, reverted-because, etc.
    outcome_details: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    outcome_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)

    __table_args__ = (Index("ix_ornith_pairs_status_invoked", "outcome_status", "invoked_at"),)


class SlurmJobModel(Base):
    """Persistent record of a Slurm job lifecycle.

    Written when a job is submitted; updated on completion/failure/cancellation.
    The ``daemon_pid`` field ties the row to the daemon process that submitted it,
    enabling orphan detection (startup) and shutdown scancel (shutdown hook).
    """

    __tablename__ = "slurm_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    deployment_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    account: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    qos: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    partition: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    gpu_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gpu_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    max_hours: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    hourly_rate_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cost_incurred: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="submitted", index=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    daemon_pid: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_slurm_jobs_status_daemon", "status", "daemon_pid"),
        Index("ix_slurm_jobs_deployment", "deployment_id"),
    )


class EventWorkTransportModel(Base):
    """Durable event/work transport with fenced cross-worker claims.

    Workers claim rows via ``SELECT ... FOR UPDATE SKIP LOCKED`` and progress
    them through the lifecycle: pending → claimed → processing → completed/failed.
    Stale claims (``claimed_at`` older than a configurable TTL) are reaped and
    re-queued by a background reaper, giving at-least-once delivery semantics.

    Immutable event identity lives in ``event_type`` + ``payload``; mutable
    claim/lifecycle state is tracked via ``status``, ``claimed_by``,
    ``claimed_at``, ``attempts``, and ``completed_at``.
    """

    __tablename__ = "event_work_transport"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_utcnow, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("length(payload) <= 65536", name="ck_event_work_transport_payload_len"),
        Index("ix_event_work_transport_claim", "status", "claimed_at"),
        Index("ix_event_work_transport_type_status", "event_type", "status", "created_at"),
    )
