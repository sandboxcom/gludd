"""Add S.13 missing FKs: todos.parent_todo_id and task_decisions.matched_todo_id.

Adds two foreign keys that were declared on the ORM models but never
emitted by any prior migration:

* ``todos.parent_todo_id`` -> ``todos.todo_id`` (``ON DELETE SET NULL``)
* ``task_decisions.matched_todo_id`` -> ``todos.todo_id`` (``ON DELETE SET NULL``)

Both columns are nullable. SET NULL is used because deleting a parent todo
should not cascade-delete child todos or decision matches; the reference
should be cleared without destroying the referencing row.

SQLite requires ``batch_alter_table`` for constraint changes; the env's
``render_as_batch=True`` handles this automatically.

Revision ID: 032
Revises: 031
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "032"
down_revision: str | None = "031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_TODOS_PARENT = "fk_todos_parent_todo_id"
FK_TASK_DECISIONS_MATCHED = "fk_task_decisions_matched_todo_id"


def upgrade() -> None:
    bind = op.get_bind()

    # --- todos.parent_todo_id -> todos.todo_id (SET NULL) -------------------
    # Precheck: NULL out orphaned parent_todo_id values that reference
    # non-existent todos. Mirror of SET NULL behaviour.
    bind.execute(
        sa.text(
            "UPDATE todos SET parent_todo_id = NULL "
            "WHERE parent_todo_id IS NOT NULL "
            "AND parent_todo_id NOT IN (SELECT todo_id FROM todos)"
        )
    )
    with op.batch_alter_table("todos", recreate="always") as batch_op:
        batch_op.create_foreign_key(
            FK_TODOS_PARENT,
            "todos",
            ["parent_todo_id"],
            ["todo_id"],
            ondelete="SET NULL",
        )

    # --- task_decisions.matched_todo_id -> todos.todo_id (SET NULL) ---------
    # Precheck: NULL out orphaned matched_todo_id values.
    bind.execute(
        sa.text(
            "UPDATE task_decisions SET matched_todo_id = NULL "
            "WHERE matched_todo_id IS NOT NULL "
            "AND matched_todo_id NOT IN (SELECT todo_id FROM todos)"
        )
    )
    with op.batch_alter_table("task_decisions", recreate="always") as batch_op:
        batch_op.create_foreign_key(
            FK_TASK_DECISIONS_MATCHED,
            "todos",
            ["matched_todo_id"],
            ["todo_id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("task_decisions", recreate="always") as batch_op:
        batch_op.drop_constraint(FK_TASK_DECISIONS_MATCHED, type_="foreignkey")

    with op.batch_alter_table("todos", recreate="always") as batch_op:
        batch_op.drop_constraint(FK_TODOS_PARENT, type_="foreignkey")
