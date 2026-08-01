"""Make ``acceptance_criteria`` and ``definition_of_done`` nullable on ``todos``.

Revision ID: 021
Revises: 020
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "021"
down_revision: str | None = "020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("todos") as batch_op:
        batch_op.alter_column("acceptance_criteria", nullable=True, existing_type=sa.Text())
        batch_op.alter_column("definition_of_done", nullable=True, existing_type=sa.Text())


def downgrade() -> None:
    with op.batch_alter_table("todos") as batch_op:
        batch_op.alter_column(
            "acceptance_criteria", nullable=False, existing_type=sa.Text(),
            server_default="[]",
        )
        batch_op.alter_column(
            "definition_of_done", nullable=False, existing_type=sa.Text(),
            server_default="",
        )
