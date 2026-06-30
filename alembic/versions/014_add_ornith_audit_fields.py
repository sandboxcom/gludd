"""Add ornith audit fields to ``audit_events``.

Adds the ``scaffold_sha256``, ``model``, ``tokens_in`` and ``tokens_out``
columns to the existing ``audit_events`` table. All four are nullable so
existing rows remain valid.

Revision ID: 014
Revises: 013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "014"
down_revision: str | None = "013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "audit_events"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("scaffold_sha256", sa.String(), nullable=True))
    op.add_column(_TABLE, sa.Column("model", sa.String(), nullable=True))
    op.add_column(_TABLE, sa.Column("tokens_in", sa.Integer(), nullable=True))
    op.add_column(_TABLE, sa.Column("tokens_out", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, "tokens_out")
    op.drop_column(_TABLE, "tokens_in")
    op.drop_column(_TABLE, "model")
    op.drop_column(_TABLE, "scaffold_sha256")
