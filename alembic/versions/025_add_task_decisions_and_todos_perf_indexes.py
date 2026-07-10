"""Add performance indexes for task_decisions and todos (E11/E12).

E11 — ``task_decisions.created_at``:
EventLoop's decision-history tick (``loop.py``) runs
``ORDER BY created_at DESC LIMIT 50`` against ``task_decisions`` every tick.
The table is insert-only (rows are never updated or deleted after creation),
so without an index on ``created_at`` this is a full-table scan per tick that
only gets slower as decisions accumulate.

E12 — ``todos(status, priority, created_at)``:
``TodoRepository.claim_runnable`` (``db/repository.py``) filters
``WHERE status = 'queued'`` and orders by
``(priority DESC, created_at, id)`` on every claim. Only ``status`` was
indexed (``ix_todos_status_queue`` / the plain ``status`` index=True column),
so the priority/created_at sort had no index support once the QUEUED set grew
beyond a handful of rows. This composite index covers the WHERE clause and
the ORDER BY columns together.

The matching model-layer declarations (``TaskDecisionModel.__table_args__``
and ``TodoModel.__table_args__``) are in the same change so fresh SQLite DBs
created via ``Base.metadata.create_all`` (tests, dev) also get these indexes
without running Alembic.

Revision ID: 025
Revises: 024
"""

from collections.abc import Sequence

from alembic import op

revision: str = "025"
down_revision: str | None = "024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Index name constants — referenced in both upgrade and downgrade.
_IX_TASK_DECISIONS_CREATED_AT = "ix_task_decisions_created_at"
_IX_TODOS_STATUS_PRIORITY_CREATED_AT = "ix_todos_status_priority_created_at"


def upgrade() -> None:
    op.create_index(
        _IX_TASK_DECISIONS_CREATED_AT,
        "task_decisions",
        ["created_at"],
    )
    op.create_index(
        _IX_TODOS_STATUS_PRIORITY_CREATED_AT,
        "todos",
        ["status", "priority", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(_IX_TODOS_STATUS_PRIORITY_CREATED_AT, table_name="todos")
    op.drop_index(_IX_TASK_DECISIONS_CREATED_AT, table_name="task_decisions")
