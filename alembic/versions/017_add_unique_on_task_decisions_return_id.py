"""Add unique constraint on ``task_decisions.return_id``.

A task decision should be 1:1 with a task return — two decisions for the same
return is a data-integrity violation. This migration adds the unique constraint
that the ORM declared but never had in the database schema.

Precheck: if duplicate ``return_id`` rows exist, only the row with the highest
``id`` is retained; the rest are deleted (they represent stale/duplicate
entries that should never have existed).

Revision ID: 017
Revises: 016
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "017"
down_revision: str | None = "016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNIQUE_NAME = "uq_task_decisions_return_id"


def upgrade() -> None:
    bind = op.get_bind()

    bind.execute(
        sa.text(
            "DELETE FROM task_decisions "
            "WHERE id NOT IN ("
            "  SELECT MAX(id) FROM task_decisions GROUP BY return_id"
            ")"
        )
    )

    with op.batch_alter_table("task_decisions") as batch_op:
        batch_op.create_unique_constraint(UNIQUE_NAME, ["return_id"])


def downgrade() -> None:
    with op.batch_alter_table("task_decisions") as batch_op:
        batch_op.drop_constraint(UNIQUE_NAME, type_="unique")
