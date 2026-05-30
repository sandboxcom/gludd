"""Initial schema - all tables.

Revision ID: 001
Revises:
Create Date: 2026-01-01 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "todos",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("todo_id", sa.String(32), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="backlog"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queue", sa.String(64), nullable=False, server_default="core"),
        sa.Column("tags", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("risk_level", sa.String(16), nullable=False, server_default="low"),
        sa.Column("work_type", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("resource_profile", sa.String(32), nullable=False, server_default="low_resource"),
        sa.Column("parent_todo_id", sa.String(32), nullable=True),
        sa.Column("child_todo_ids", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("acceptance_criteria", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("test_commands", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("molecule_scenarios", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("molecule_evidence_refs", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("coverage_requirements", sa.String(256), nullable=True),
        sa.Column("dependencies", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="agent"),
        sa.Column("assigned_agent", sa.String(128), nullable=True),
        sa.Column("model_profile", sa.String(64), nullable=True),
        sa.Column("prompt_profile", sa.String(64), nullable=True),
        sa.Column("worktree", sa.String(512), nullable=True),
        sa.Column("branch_name", sa.String(256), nullable=True),
        sa.Column("artifacts", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("evidence_refs", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("manual_hold_reason", sa.Text(), nullable=True),
        sa.Column("approval_policy", sa.String(32), nullable=False, server_default="none"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("todo_id"),
    )
    op.create_index("ix_todos_status", "todos", ["status"])
    op.create_index("ix_todos_queue", "todos", ["queue"])
    op.create_index("ix_todos_status_queue", "todos", ["status", "queue"])

    op.create_table(
        "todo_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("todo_id", sa.String(32), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("old_status", sa.String(32), nullable=True),
        sa.Column("new_status", sa.String(32), nullable=True),
        sa.Column("actor", sa.String(64), nullable=False, server_default="agent"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["todo_id"], ["todos.todo_id"]),
    )
    op.create_index("ix_todo_events_todo_id", "todo_events", ["todo_id"])

    op.create_table(
        "task_returns",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("return_id", sa.String(64), nullable=False),
        sa.Column("todo_id", sa.String(32), nullable=True),
        sa.Column("job_id", sa.String(64), nullable=False),
        sa.Column("playbook", sa.String(256), nullable=False),
        sa.Column("queue", sa.String(64), nullable=False),
        sa.Column("work_type", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("resource_profile", sa.String(32), nullable=False, server_default="low_resource"),
        sa.Column("status", sa.String(32), nullable=False, server_default="created"),
        sa.Column("exit_code", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("artifacts", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("logs_ref", sa.String(512), nullable=True),
        sa.Column("diff_ref", sa.String(512), nullable=True),
        sa.Column("test_results_ref", sa.String(512), nullable=True),
        sa.Column("molecule_results_ref", sa.String(512), nullable=True),
        sa.Column("coverage_results_ref", sa.String(512), nullable=True),
        sa.Column("model_usage_ref", sa.String(512), nullable=True),
        sa.Column("producer_worker_id", sa.String(64), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("return_id"),
    )
    op.create_index("ix_task_returns_todo_id", "task_returns", ["todo_id"])
    op.create_index("ix_task_returns_job_id", "task_returns", ["job_id"])
    op.create_index("ix_task_returns_queue", "task_returns", ["queue"])
    op.create_index("ix_task_returns_status", "task_returns", ["status"])

    op.create_table(
        "task_decisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("return_id", sa.String(64), nullable=False),
        sa.Column("matched_todo_id", sa.String(32), nullable=True),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("evidence_refs", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("todo_updates", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("child_todos", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("validation_requests", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("git_requests", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("audit_notes", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("policy_flags", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_decisions_return_id", "task_decisions", ["return_id"])

    op.create_table(
        "queues",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("queue_name", sa.String(64), nullable=False),
        sa.Column("queue_enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("priority_weight", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("resource_profile", sa.String(32), nullable=False, server_default="low_resource"),
        sa.Column("hard_cap", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("soft_cap", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("pid_group", sa.String(64), nullable=True),
        sa.Column("allowed_playbooks", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("allowed_model_profiles", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("allowed_prompt_profiles", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("required_molecule_coverage_profile", sa.String(128), nullable=True),
        sa.Column("max_error_rate", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("retry_policy", sa.Text(), nullable=False, server_default="{}"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("queue_name"),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False, server_default="agent"),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(128), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=True),
        sa.Column("details", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_entity_id", "audit_events", ["entity_id"])
    op.create_index("ix_audit_events_entity", "audit_events", ["entity_type", "entity_id"])
    op.create_index("ix_audit_events_correlation_id", "audit_events", ["correlation_id"])

    op.create_table(
        "variable_namespaces",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("namespace", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace"),
    )

    op.create_table(
        "variable_values",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(256), nullable=False),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("value_type", sa.String(32), nullable=False, server_default="string"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["namespace_id"], ["variable_namespaces.id"]),
        sa.UniqueConstraint("namespace_id", "key", name="uq_variable_namespace_key"),
    )
    op.create_index("ix_variable_values_namespace_id", "variable_values", ["namespace_id"])

    op.create_table(
        "bucket_leases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("bucket_key", sa.String(256), nullable=False),
        sa.Column("holder_id", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bucket_key", "holder_id", name="uq_bucket_lease"),
    )
    op.create_index("ix_bucket_leases_bucket_key", "bucket_leases", ["bucket_key"])


def downgrade() -> None:
    op.drop_table("bucket_leases")
    op.drop_table("variable_values")
    op.drop_table("variable_namespaces")
    op.drop_table("audit_events")
    op.drop_table("queues")
    op.drop_table("task_decisions")
    op.drop_table("task_returns")
    op.drop_table("todo_events")
    op.drop_table("todos")
