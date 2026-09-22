"""Add project-scoped composite deployment ownership.

Revision ID: 047
Revises: 046
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "047"
down_revision: str | None = "046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "deployment_records"
_PRIMARY_KEY = "pk_deployment_records"
_PROJECT_INDEX = "ix_deployment_records_project_id"


def upgrade() -> None:
    """Backfill legacy records and adopt the composite ownership key."""
    op.add_column(
        _TABLE,
        sa.Column(
            "project_id",
            sa.String(length=128),
            nullable=False,
            server_default="default",
        ),
    )
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_constraint(_PRIMARY_KEY, type_="primary")
        batch_op.create_primary_key(
            _PRIMARY_KEY,
            ["project_id", "provider", "instance_id"],
        )
    op.create_index(_PROJECT_INDEX, _TABLE, ["project_id"])


def downgrade() -> None:
    """Restore the global instance key only when no identity would be lost."""
    duplicate_count = op.get_bind().execute(
        sa.text(
            "SELECT COUNT(*) FROM ("
            "SELECT instance_id FROM deployment_records "
            "GROUP BY instance_id HAVING COUNT(*) > 1"
            ") AS duplicate_instance_ids"
        )
    ).scalar()
    if isinstance(duplicate_count, int) and duplicate_count > 0:
        raise RuntimeError(
            "cannot downgrade deployment ownership while duplicate instance IDs "
            "exist across projects or providers"
        )

    op.drop_index(_PROJECT_INDEX, table_name=_TABLE)
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_constraint(_PRIMARY_KEY, type_="primary")
        batch_op.create_primary_key(_PRIMARY_KEY, ["instance_id"])
    op.drop_column(_TABLE, "project_id")
