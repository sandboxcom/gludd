"""Add FK: human_todos.parent_agent_todo_id -> todos.todo_id (SET NULL).

Closure for S.13 — the ORM model declares ``parent_agent_todo_id`` as a
reference to the blocked agent todo, but no prior migration created the
foreign key constraint on the database side.

SQLite requires ``batch_alter_table`` for constraint changes; the env's
``render_as_batch=True`` handles this automatically.

Revision ID: 033
Revises: 032
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "033"
down_revision: str | None = "032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_HUMAN_TODOS_PARENT = "fk_human_todos_parent_agent_todo_id"


def upgrade() -> None:
    bind = op.get_bind()

    bind.execute(
        sa.text(
            "UPDATE human_todos SET parent_agent_todo_id = NULL "
            "WHERE parent_agent_todo_id IS NOT NULL "
            "AND parent_agent_todo_id NOT IN (SELECT todo_id FROM todos)"
        )
    )

    with op.batch_alter_table("human_todos") as batch_op:
        batch_op.create_foreign_key(
            FK_HUMAN_TODOS_PARENT,
            "todos",
            ["parent_agent_todo_id"],
            ["todo_id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("human_todos") as batch_op:
        batch_op.drop_constraint(FK_HUMAN_TODOS_PARENT, type_="foreignkey")
