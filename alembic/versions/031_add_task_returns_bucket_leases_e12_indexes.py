"""Add E12 composite indexes on task_returns(status, created_at) and bucket_leases(bucket_key, expires_at).

E12 — task_returns(status, created_at):
claim_unreviewed queries WHERE status='created' ORDER BY created_at ASC.
The single-column status index can't cover the sort; this composite
index makes the claim_unreviewed scan an index-only traversal.

E12 — bucket_leases(bucket_key, expires_at):
reclaim_expired_leases checks per-bucket live-lease existence with
WHERE bucket_key=X AND expires_at>=now. A composite index covering both
columns avoids a two-index merge on every expired lease.

Revision ID: 031
Revises: 030
"""

from collections.abc import Sequence

from alembic import op

revision: str = "031"
down_revision: str | None = "030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_task_returns_status_created",
        "task_returns",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_bucket_leases_key_expires",
        "bucket_leases",
        ["bucket_key", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_bucket_leases_key_expires", table_name="bucket_leases")
    op.drop_index("ix_task_returns_status_created", table_name="task_returns")
