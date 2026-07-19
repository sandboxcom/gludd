"""Add ck_todos_priority_range CHECK constraint on todos.priority (H.14).

Prevents DB-bypass writes with priority outside [0, 1000] — mirrors the
application-layer clamping in TodoRepository._validate_create_data.

Revision ID: 036
Revises: 035
"""

from collections.abc import Sequence

from alembic import op

revision: str = "036"
down_revision: str | None = "035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CK_NAME = "ck_todos_priority_range"


def upgrade() -> None:
    with op.batch_alter_table("todos", recreate="always") as batch_op:
        batch_op.create_check_constraint(
            _CK_NAME,
            "priority >= 0 AND priority <= 1000",
        )


def downgrade() -> None:
    with op.batch_alter_table("todos", recreate="always") as batch_op:
        batch_op.drop_constraint(_CK_NAME, type_="check")
