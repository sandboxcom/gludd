"""Drop the projects FK from memory_records.project_id and widen it to 256.

Revision ID: 043
Revises: 042
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "043"
down_revision: str | None = "042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("memory_records") as batch_op:
        batch_op.drop_constraint("fk_memory_records_project_id", type_="foreignkey")
        batch_op.alter_column(
            "project_id",
            existing_type=sa.String(32),
            type_=sa.String(256),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("memory_records") as batch_op:
        batch_op.alter_column(
            "project_id",
            existing_type=sa.String(256),
            type_=sa.String(32),
            existing_nullable=True,
        )
        batch_op.create_foreign_key(
            "fk_memory_records_project_id",
            "projects",
            ["project_id"],
            ["project_id"],
            ondelete="SET NULL",
        )
