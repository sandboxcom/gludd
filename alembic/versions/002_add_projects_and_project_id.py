"""Alembic migration for projects table and project_id columns."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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

    # SQLite cannot ``ALTER TABLE ... ADD CONSTRAINT``; every constraint change
    # (foreign keys, the unique-constraint swap) is therefore done through
    # ``batch_alter_table`` (copy-and-move table rebuild), mirroring migrations
    # 006 and 009. The column add and the index live OUTSIDE the batch block
    # because ``ADD COLUMN`` / ``CREATE INDEX`` are natively supported on SQLite
    # (and adding them inside a ``recreate="always"`` block would otherwise be
    # rebuilt twice). ``recreate="always"`` is set so the FK lands on every
    # backend regardless of dialect autodetection.
    op.add_column("todos", sa.Column("project_id", sa.String(32), nullable=True))
    op.create_index("ix_todos_project_id", "todos", ["project_id"])
    with op.batch_alter_table("todos", recreate="always") as batch_op:
        batch_op.create_foreign_key(
            "fk_todos_project_id", "projects", ["project_id"], ["project_id"]
        )

    op.add_column("todo_events", sa.Column("project_id", sa.String(32), nullable=True))
    op.create_index("ix_todo_events_project_id", "todo_events", ["project_id"])
    with op.batch_alter_table("todo_events", recreate="always") as batch_op:
        batch_op.create_foreign_key(
            "fk_todo_events_project_id", "projects", ["project_id"], ["project_id"]
        )

    op.add_column("task_returns", sa.Column("project_id", sa.String(32), nullable=True))
    op.create_index("ix_task_returns_project_id", "task_returns", ["project_id"])
    with op.batch_alter_table("task_returns", recreate="always") as batch_op:
        batch_op.create_foreign_key(
            "fk_task_returns_project_id", "projects", ["project_id"], ["project_id"]
        )

    op.add_column("task_decisions", sa.Column("project_id", sa.String(32), nullable=True))
    op.create_index("ix_task_decisions_project_id", "task_decisions", ["project_id"])
    with op.batch_alter_table("task_decisions", recreate="always") as batch_op:
        batch_op.create_foreign_key(
            "fk_task_decisions_project_id", "projects", ["project_id"], ["project_id"]
        )

    op.add_column("queues", sa.Column("project_id", sa.String(32), nullable=True))
    op.create_index("ix_queues_project_id", "queues", ["project_id"])
    with op.batch_alter_table("queues", recreate="always") as batch_op:
        batch_op.create_foreign_key(
            "fk_queues_project_id", "projects", ["project_id"], ["project_id"]
        )

    op.add_column("audit_events", sa.Column("project_id", sa.String(32), nullable=True))
    op.create_index("ix_audit_events_project_id", "audit_events", ["project_id"])
    with op.batch_alter_table("audit_events", recreate="always") as batch_op:
        batch_op.create_foreign_key(
            "fk_audit_events_project_id", "projects", ["project_id"], ["project_id"]
        )

    op.add_column("variable_namespaces", sa.Column("project_id", sa.String(32), nullable=True))
    op.create_index("ix_variable_namespaces_project_id", "variable_namespaces", ["project_id"])
    # FK + unique-constraint swap in ONE batch rebuild: drop the old single-column
    # uniqueness on (namespace) — created UNNAMED in migration 001
    # (``sa.UniqueConstraint("namespace")``) — and add the composite
    # (namespace, project_id). Because the old constraint has no explicit name,
    # a ``naming_convention`` is supplied so batch reflection assigns it the
    # deterministic SQLAlchemy default name ``uq_variable_namespaces_namespace``,
    # which is then droppable by name. (The original migration tried to drop
    # ``uq_variable_namespace_key`` here, but that name belongs to the
    # variable_VALUES table — it never existed on variable_namespaces.)
    naming_convention = {"uq": "uq_%(table_name)s_%(column_0_name)s"}
    with op.batch_alter_table(
        "variable_namespaces",
        recreate="always",
        naming_convention=naming_convention,
    ) as batch_op:
        batch_op.create_foreign_key(
            "fk_variable_namespaces_project_id",
            "projects",
            ["project_id"],
            ["project_id"],
        )
        batch_op.drop_constraint("uq_variable_namespaces_namespace", type_="unique")
        batch_op.create_unique_constraint(
            "uq_namespace_project", ["namespace", "project_id"]
        )

    op.add_column("bucket_leases", sa.Column("project_id", sa.String(32), nullable=True))
    op.create_index("ix_bucket_leases_project_id", "bucket_leases", ["project_id"])
    with op.batch_alter_table("bucket_leases", recreate="always") as batch_op:
        batch_op.create_foreign_key(
            "fk_bucket_leases_project_id", "projects", ["project_id"], ["project_id"]
        )


def downgrade() -> None:
    # Mirror upgrade: constraint drops go through batch_alter_table (SQLite cannot
    # ALTER ... DROP CONSTRAINT); index/column drops are native.
    with op.batch_alter_table("bucket_leases", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_bucket_leases_project_id", type_="foreignkey")
    op.drop_index("ix_bucket_leases_project_id", "bucket_leases")
    op.drop_column("bucket_leases", "project_id")

    with op.batch_alter_table("variable_namespaces", recreate="always") as batch_op:
        batch_op.drop_constraint("uq_namespace_project", type_="unique")
        # Restore the original single-column uniqueness on (namespace); reuse the
        # deterministic conventional name so a subsequent re-upgrade can drop it.
        batch_op.create_unique_constraint(
            "uq_variable_namespaces_namespace", ["namespace"]
        )
        batch_op.drop_constraint("fk_variable_namespaces_project_id", type_="foreignkey")
    op.drop_index("ix_variable_namespaces_project_id", "variable_namespaces")
    op.drop_column("variable_namespaces", "project_id")

    with op.batch_alter_table("audit_events", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_audit_events_project_id", type_="foreignkey")
    op.drop_index("ix_audit_events_project_id", "audit_events")
    op.drop_column("audit_events", "project_id")

    with op.batch_alter_table("queues", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_queues_project_id", type_="foreignkey")
    op.drop_index("ix_queues_project_id", "queues")
    op.drop_column("queues", "project_id")

    with op.batch_alter_table("task_decisions", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_task_decisions_project_id", type_="foreignkey")
    op.drop_index("ix_task_decisions_project_id", "task_decisions")
    op.drop_column("task_decisions", "project_id")

    with op.batch_alter_table("task_returns", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_task_returns_project_id", type_="foreignkey")
    op.drop_index("ix_task_returns_project_id", "task_returns")
    op.drop_column("task_returns", "project_id")

    with op.batch_alter_table("todo_events", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_todo_events_project_id", type_="foreignkey")
    op.drop_index("ix_todo_events_project_id", "todo_events")
    op.drop_column("todo_events", "project_id")

    with op.batch_alter_table("todos", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_todos_project_id", type_="foreignkey")
    op.drop_index("ix_todos_project_id", "todos")
    op.drop_column("todos", "project_id")

    op.drop_table("projects")
