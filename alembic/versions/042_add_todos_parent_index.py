"""Index todos.parent_todo_id for parent-tree traversal and FK maintenance.

Revision ID: 042
Revises: 041
"""

from collections.abc import Sequence

from alembic import op

revision: str = "042"
down_revision: str | None = "041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_todos_parent_todo_id"


def upgrade() -> None:
    op.create_index(INDEX_NAME, "todos", ["parent_todo_id"], unique=False)


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="todos")
