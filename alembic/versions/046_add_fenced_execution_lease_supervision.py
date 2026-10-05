"""Add renewable, termination-fenced execution lease supervision.

Revision ID: 046
Revises: 045
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "046"
down_revision: str | None = "045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_UNIQUE = "uq_bucket_lease"
_NEW_UNIQUE = "uq_bucket_lease_bucket_key"


def upgrade() -> None:
    """Make each bucket single-owner and add recovery handshake timestamps."""
    connection = op.get_bind()
    # Legacy schema allowed one row per (bucket, holder). Those rows were only
    # ephemeral liveness hints, so retain the newest row before enforcing the
    # one-attempt-per-bucket invariant. Keeping every row would make a safe
    # uniqueness migration impossible and preserve the duplicate-owner bug.
    connection.execute(
        sa.text(
            "DELETE FROM bucket_leases WHERE id NOT IN "
            "(SELECT MAX(id) FROM bucket_leases GROUP BY bucket_key)"
        )
    )

    with op.batch_alter_table("bucket_leases") as batch_op:
        batch_op.drop_constraint(_OLD_UNIQUE, type_="unique")
        batch_op.create_unique_constraint(_NEW_UNIQUE, ["bucket_key"])
        batch_op.add_column(sa.Column("todo_version", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "termination_confirmed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True)
        )

    connection.execute(
        sa.text(
            "UPDATE bucket_leases SET heartbeat_at = created_at, "
            "updated_at = created_at"
        )
    )
    with op.batch_alter_table("bucket_leases") as batch_op:
        batch_op.alter_column(
            "heartbeat_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
        batch_op.alter_column(
            "updated_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )


def downgrade() -> None:
    """Restore the legacy multi-holder lease shape without inventing rows."""
    with op.batch_alter_table("bucket_leases") as batch_op:
        batch_op.drop_constraint(_NEW_UNIQUE, type_="unique")
        batch_op.create_unique_constraint(
            _OLD_UNIQUE,
            ["bucket_key", "holder_id"],
        )
        batch_op.drop_column("updated_at")
        batch_op.drop_column("termination_confirmed_at")
        batch_op.drop_column("cancel_requested_at")
        batch_op.drop_column("heartbeat_at")
        batch_op.drop_column("todo_version")
