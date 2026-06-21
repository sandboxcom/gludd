"""Add D9 referential-integrity foreign keys.

Brings the migration chain into parity with the ORM models in
``general_ludd.db.models`` for two foreign keys that were declared on the
models but never emitted by migration ``001_initial_schema`` (the columns and
their indexes exist, but no ``FOREIGN KEY`` constraint was created):

* ``task_returns.todo_id`` -> ``todos.todo_id`` (``ON DELETE SET NULL``)
* ``task_decisions.return_id`` -> ``task_returns.return_id`` (``ON DELETE CASCADE``)

SQLite cannot ``ALTER TABLE ... ADD CONSTRAINT``; every constraint change is
done through ``batch_alter_table`` (table copy + rebuild), which the alembic
env already enables globally via ``render_as_batch=True``.

Because adding a FK to a populated table fails (or silently corrupts integrity)
when orphan rows exist, each FK is preceded by an orphan-row precheck:

* ``task_returns.todo_id`` uses ``SET NULL`` semantics, so orphaned values
  (a ``todo_id`` with no matching ``todos`` row) are NULLed out — the same
  effect the FK would have on a later delete.
* ``task_decisions.return_id`` is ``NOT NULL`` with ``CASCADE`` delete, so it
  cannot be NULLed; orphaned decision rows (a ``return_id`` with no matching
  ``task_returns`` row) are deleted — the same effect the CASCADE would have.

Revision ID: 006
Revises: 005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FK_TASK_RETURNS_TODO = "fk_task_returns_todo_id"
FK_TASK_DECISIONS_RETURN = "fk_task_decisions_return_id"


def upgrade() -> None:
    bind = op.get_bind()

    # --- task_returns.todo_id -> todos.todo_id (SET NULL) -----------------
    # Precheck: NULL out orphaned todo_id values (no matching todos row).
    # Mirrors the eventual SET NULL behaviour, and lets the FK be added
    # without violating integrity against existing data.
    bind.execute(
        sa.text(
            "UPDATE task_returns SET todo_id = NULL "
            "WHERE todo_id IS NOT NULL "
            "AND todo_id NOT IN (SELECT todo_id FROM todos)"
        )
    )
    with op.batch_alter_table("task_returns", recreate="always") as batch_op:
        batch_op.create_foreign_key(
            FK_TASK_RETURNS_TODO,
            "todos",
            ["todo_id"],
            ["todo_id"],
            ondelete="SET NULL",
        )

    # --- task_decisions.return_id -> task_returns.return_id (CASCADE) -----
    # Precheck: return_id is NOT NULL so orphans cannot be NULLed; delete
    # orphaned decision rows (no matching task_returns row). Mirrors the
    # eventual CASCADE behaviour.
    bind.execute(
        sa.text(
            "DELETE FROM task_decisions "
            "WHERE return_id NOT IN (SELECT return_id FROM task_returns)"
        )
    )
    with op.batch_alter_table("task_decisions", recreate="always") as batch_op:
        batch_op.create_foreign_key(
            FK_TASK_DECISIONS_RETURN,
            "task_returns",
            ["return_id"],
            ["return_id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("task_decisions", recreate="always") as batch_op:
        batch_op.drop_constraint(FK_TASK_DECISIONS_RETURN, type_="foreignkey")

    with op.batch_alter_table("task_returns", recreate="always") as batch_op:
        batch_op.drop_constraint(FK_TASK_RETURNS_TODO, type_="foreignkey")
