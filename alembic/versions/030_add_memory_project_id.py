"""Add project_id column to memory_records for cross-project isolation.

Revision ID: 030
Revises: 029
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "030"
down_revision: str | None = "029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "memory_records",
        sa.Column("project_id", sa.String(32), nullable=True),
    )
    op.create_foreign_key(
        "fk_memory_records_project_id",
        "memory_records",
        "projects",
        ["project_id"],
        ["project_id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_memory_records_project_id",
        "memory_records",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_memory_records_project_id", table_name="memory_records")
    op.drop_constraint("fk_memory_records_project_id", "memory_records", type_="foreignkey")
    op.drop_column("memory_records", "project_id")
