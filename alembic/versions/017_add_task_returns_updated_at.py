"""Add ``updated_at`` column to ``task_returns``.

The ORM model (``TaskReturnModel``) declares ``updated_at`` as a nullable
``DateTime(timezone=True)``, but migration 001_initial_schema never created
the column.  This migration brings the migrated schema into parity with the
ORM so ``test_alembic_orm_parity.py`` passes the column-level check.

Revision ID: 018
Revises: 017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "018"
down_revision: str | None = "017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "task_returns",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("task_returns") as batch_op:
        batch_op.drop_column("updated_at")
