"""Add E12 performance indexes on todos (status,queue,scheduled_at) and work_type.

E12 — ``todos(status, queue, scheduled_at)``:
Queries that filter TODOs by status and queue with a time-range on
``scheduled_at`` (e.g. ``list_due_scheduled`` variants, queue-filtered
claim queries) scan on ``status`` + ``queue`` and then sort on
``scheduled_at``.  This composite index covers WHERE + time sort.

E12 — ``todos(work_type)``:
``TodoRepository.status_summary`` groups by ``work_type`` every tick to
populate ``/api/facts``.  The ``work_type`` column had no index, so this
was a full-table scan regardless of row count.  Adding a plain index
makes the GROUP BY an index-only scan.

The matching model-layer declarations are in the same change so fresh
SQLite DBs created via ``Base.metadata.create_all`` also get these
indexes without running Alembic.

Revision ID: 027
Revises: 026
"""

from collections.abc import Sequence

from alembic import op

revision: str = "027"
down_revision: str | None = "026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IX_TODOS_STATUS_QUEUE_SCHEDULED = "ix_todos_status_queue_scheduled"
_IX_TODOS_WORK_TYPE = "ix_todos_work_type"


def upgrade() -> None:
    op.create_index(
        _IX_TODOS_STATUS_QUEUE_SCHEDULED,
        "todos",
        ["status", "queue", "scheduled_at"],
    )
    op.create_index(
        _IX_TODOS_WORK_TYPE,
        "todos",
        ["work_type"],
    )


def downgrade() -> None:
    op.drop_index(_IX_TODOS_WORK_TYPE, table_name="todos")
    op.drop_index(_IX_TODOS_STATUS_QUEUE_SCHEDULED, table_name="todos")
