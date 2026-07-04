"""Add ``definition_of_done`` column to ``todos``.

Revision ID: 020
Revises: 019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "020"
down_revision: str | None = "019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "todos",
        sa.Column("definition_of_done", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    with op.batch_alter_table("todos") as batch_op:
        batch_op.drop_column("definition_of_done")
