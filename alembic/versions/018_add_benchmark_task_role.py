"""Add ``task_role`` column to ``benchmark_results``.

Revision ID: 019
Revises: 018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "019"
down_revision: str | None = "018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "benchmark_results",
        sa.Column("task_role", sa.String(32), nullable=True),
    )
    op.create_index("ix_benchmark_task_role", "benchmark_results", ["task_role"])


def downgrade() -> None:
    with op.batch_alter_table("benchmark_results") as batch_op:
        batch_op.drop_index("ix_benchmark_task_role")
        batch_op.drop_column("task_role")
