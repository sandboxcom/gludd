"""Add E12 composite indexes: task_returns(status,project_id,created_at) and todos(status,updated_at).

E12 — task_returns(status, project_id, created_at):
claim_unreviewed queries WHERE status='created' AND project_id=?
ORDER BY created_at ASC. The existing ix_task_returns_status_created
covers (status, created_at) but project_id filtering happens after
an index scan. This 3-column composite covers the full WHERE + ORDER BY.

E12 — todos(status, updated_at):
_reap_stuck_todos scans WHERE status='active' AND updated_at < cutoff.
status alone is indexed; the updated_at filter is applied after scanning
all ACTIVE rows. A composite makes the reaper query index-only.

The matching model-layer declarations are in models.py so fresh SQLite
DBs get these indexes without running Alembic.

Revision ID: 032
Revises: 031
"""

from collections.abc import Sequence

from alembic import op

revision: str = "032"
down_revision: str | None = "031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_task_returns_status_project_created",
        "task_returns",
        ["status", "project_id", "created_at"],
    )
    op.create_index(
        "ix_todos_status_updated_at",
        "todos",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_todos_status_updated_at", table_name="todos")
    op.drop_index("ix_task_returns_status_project_created", table_name="task_returns")
