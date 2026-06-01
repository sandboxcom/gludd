"""Alembic migration for projects table and project_id columns."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("project_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("workspace_path", sa.String(512), nullable=False, server_default=""),
        sa.Column("config", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("project_id"),
    )

    op.add_column("todos", sa.Column("project_id", sa.String(32), nullable=True))
    op.create_index("ix_todos_project_id", "todos", ["project_id"])
    op.create_foreign_key("fk_todos_project_id", "todos", "projects", ["project_id"], ["project_id"])

    op.add_column("todo_events", sa.Column("project_id", sa.String(32), nullable=True))
    op.create_index("ix_todo_events_project_id", "todo_events", ["project_id"])
    op.create_foreign_key("fk_todo_events_project_id", "todo_events", "projects", ["project_id"], ["project_id"])

    op.add_column("task_returns", sa.Column("project_id", sa.String(32), nullable=True))
    op.create_index("ix_task_returns_project_id", "task_returns", ["project_id"])
    op.create_foreign_key("fk_task_returns_project_id", "task_returns", "projects", ["project_id"], ["project_id"])

    op.add_column("task_decisions", sa.Column("project_id", sa.String(32), nullable=True))
    op.create_index("ix_task_decisions_project_id", "task_decisions", ["project_id"])
    op.create_foreign_key("fk_task_decisions_project_id", "task_decisions", "projects", ["project_id"], ["project_id"])

    op.add_column("queues", sa.Column("project_id", sa.String(32), nullable=True))
    op.create_index("ix_queues_project_id", "queues", ["project_id"])
    op.create_foreign_key("fk_queues_project_id", "queues", "projects", ["project_id"], ["project_id"])

    op.add_column("audit_events", sa.Column("project_id", sa.String(32), nullable=True))
    op.create_index("ix_audit_events_project_id", "audit_events", ["project_id"])
    op.create_foreign_key("fk_audit_events_project_id", "audit_events", "projects", ["project_id"], ["project_id"])

    op.add_column("variable_namespaces", sa.Column("project_id", sa.String(32), nullable=True))
    op.create_index("ix_variable_namespaces_project_id", "variable_namespaces", ["project_id"])
    op.create_foreign_key("fk_variable_namespaces_project_id", "variable_namespaces", "projects", ["project_id"], ["project_id"])
    op.drop_constraint("uq_variable_namespace_key", "variable_namespaces", type_="unique")
    op.create_unique_constraint("uq_namespace_project", "variable_namespaces", ["namespace", "project_id"])

    op.add_column("bucket_leases", sa.Column("project_id", sa.String(32), nullable=True))
    op.create_index("ix_bucket_leases_project_id", "bucket_leases", ["project_id"])
    op.create_foreign_key("fk_bucket_leases_project_id", "bucket_leases", "projects", ["project_id"], ["project_id"])


def downgrade() -> None:
    op.drop_constraint("fk_bucket_leases_project_id", "bucket_leases", type_="foreignkey")
    op.drop_index("ix_bucket_leases_project_id", "bucket_leases")
    op.drop_column("bucket_leases", "project_id")

    op.drop_constraint("uq_namespace_project", "variable_namespaces", type_="unique")
    op.create_unique_constraint("uq_variable_namespace_key", "variable_namespaces", ["namespace"])
    op.drop_constraint("fk_variable_namespaces_project_id", "variable_namespaces", type_="foreignkey")
    op.drop_index("ix_variable_namespaces_project_id", "variable_namespaces")
    op.drop_column("variable_namespaces", "project_id")

    op.drop_constraint("fk_audit_events_project_id", "audit_events", type_="foreignkey")
    op.drop_index("ix_audit_events_project_id", "audit_events")
    op.drop_column("audit_events", "project_id")

    op.drop_constraint("fk_queues_project_id", "queues", type_="foreignkey")
    op.drop_index("ix_queues_project_id", "queues")
    op.drop_column("queues", "project_id")

    op.drop_constraint("fk_task_decisions_project_id", "task_decisions", type_="foreignkey")
    op.drop_index("ix_task_decisions_project_id", "task_decisions")
    op.drop_column("task_decisions", "project_id")

    op.drop_constraint("fk_task_returns_project_id", "task_returns", type_="foreignkey")
    op.drop_index("ix_task_returns_project_id", "task_returns")
    op.drop_column("task_returns", "project_id")

    op.drop_constraint("fk_todo_events_project_id", "todo_events", type_="foreignkey")
    op.drop_index("ix_todo_events_project_id", "todo_events")
    op.drop_column("todo_events", "project_id")

    op.drop_constraint("fk_todos_project_id", "todos", type_="foreignkey")
    op.drop_index("ix_todos_project_id", "todos")
    op.drop_column("todos", "project_id")

    op.drop_table("projects")
