"""Add estimated_cost_usd and actual_cost_accrued columns to todos.

Revision ID: 029
Revises: 028
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "029"
down_revision: str | None = "028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("todos", sa.Column("estimated_cost_usd", sa.Float(), nullable=True))
    op.add_column(
        "todos",
        sa.Column("actual_cost_accrued", sa.Float(), nullable=False, server_default="0.0"),
    )


def downgrade() -> None:
    op.drop_column("todos", "actual_cost_accrued")
    op.drop_column("todos", "estimated_cost_usd")
