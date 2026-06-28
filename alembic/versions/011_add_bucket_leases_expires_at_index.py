"""Add an index on bucket_leases.expires_at (F4).

``reclaim_expired_leases`` filters ``WHERE expires_at < now`` every event-loop
tick. Without an index on ``expires_at`` this is a full-table scan on
``bucket_leases`` per tick — fine at low volume, but as orphan leases accumulate
(e.g. across many todos / projects) the scan dominates tick latency.

This migration adds ``ix_bucket_leases_expires_at`` to make the reclaim query
indexed. The matching model-layer declaration
(``BucketLeaseModel.expires_at`` with ``index=True``) is in the same change so
fresh SQLite DBs created via ``Base.metadata.create_all`` (tests, dev) also get
the index without running Alembic.

Revision ID: 011
Revises: 010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: str | None = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Index name constant — referenced in both upgrade and downgrade.
_IX_EXPIRES_AT = "ix_bucket_leases_expires_at"


def upgrade() -> None:
    op.create_index(
        _IX_EXPIRES_AT,
        "bucket_leases",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(_IX_EXPIRES_AT, table_name="bucket_leases")
