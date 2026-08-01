"""Add scheduling columns to todos table.

Adds the 8 columns that implement the integrated cron / one-shot scheduling
feature: scheduled_at, cron, schedule_timezone, next_run_at, last_run_at,
run_count, max_runs, schedule_paused. All columns are additive (nullable or
have a server default) so a plain ``op.add_column`` works on SQLite and
PostgreSQL without batch-mode table copy.

A composite index (status, schedule_paused, next_run_at, scheduled_at) is added
to support the scheduler's ``list_due_scheduled`` query efficiently.

Revision ID: 010
Revises: 009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: str | None = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Index name constant — referenced in both upgrade and downgrade.
_IX_SCHED = "ix_todos_scheduled_lookup"


def upgrade() -> None:
    # All new columns are nullable or carry a server_default so SQLite can
    # add them without a table-copy (op.add_column is natively supported for
    # nullable / defaulted columns on SQLite >= 3.37.0).
    op.add_column(
        "todos",
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "todos",
        sa.Column("cron", sa.String(256), nullable=True),
    )
    op.add_column(
        "todos",
        sa.Column(
            "schedule_timezone",
            sa.String(64),
            nullable=False,
            server_default="UTC",
        ),
    )
    op.add_column(
        "todos",
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "todos",
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "todos",
        sa.Column(
            "run_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "todos",
        sa.Column("max_runs", sa.Integer(), nullable=True),
    )
    op.add_column(
        "todos",
        sa.Column(
            "schedule_paused",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Composite index supporting the scheduler's due-todo query:
    # WHERE status='scheduled' AND schedule_paused=0
    #   AND COALESCE(next_run_at, scheduled_at) <= :now
    # SQLite cannot index a COALESCE expression, so we index the four
    # individual columns; the planner will use the status+schedule_paused
    # prefix to narrow the scan, then filter on the datetime columns.
    op.create_index(
        _IX_SCHED,
        "todos",
        ["status", "schedule_paused", "next_run_at", "scheduled_at"],
    )


def downgrade() -> None:
    op.drop_index(_IX_SCHED, table_name="todos")
    op.drop_column("todos", "schedule_paused")
    op.drop_column("todos", "max_runs")
    op.drop_column("todos", "run_count")
    op.drop_column("todos", "last_run_at")
    op.drop_column("todos", "next_run_at")
    op.drop_column("todos", "schedule_timezone")
    op.drop_column("todos", "cron")
    op.drop_column("todos", "scheduled_at")
