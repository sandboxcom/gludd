"""Replace ``memory_records`` table with G1 persistent-agent-memory schema.

The original ``memory_records`` table (migration 005) carried ``scope`` /
``scope_key`` / ``kind`` / ``text`` / ``tags`` / ``source`` / ``embedding`` /
``last_used_at`` columns. G1 replaces those with an ``(agent_id, key, value,
namespace, ttl_seconds)`` shape suited for a key-value agent-memory store.

Because SQLite has no data in this unused table and no ALTER COLUMN, this
migration drops and recreates the table cleanly.

Revision ID: 022
Revises: 021
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "022"
down_revision: str | None = "021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("memory_records")
    op.create_table(
        "memory_records",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("agent_id", sa.String(128), nullable=False),
        sa.Column("key", sa.String(256), nullable=False),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("namespace", sa.String(128), nullable=False, server_default="default"),
        sa.Column("ttl_seconds", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("agent_id", "key", "namespace", name="uq_memory_agent_key_ns"),
    )
    op.create_index("ix_memory_agent_id", "memory_records", ["agent_id"])
    op.create_index("ix_memory_namespace", "memory_records", ["namespace"])


def downgrade() -> None:
    op.drop_table("memory_records")
    op.create_table(
        "memory_records",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("scope", sa.String(16), nullable=True),
        sa.Column("scope_key", sa.String(128), nullable=True),
        sa.Column("kind", sa.String(16), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column("source", sa.String(256), nullable=True),
        sa.Column("embedding", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_memory_records_scope", "memory_records", ["scope"])
    op.create_index("ix_memory_records_scope_key", "memory_records", ["scope_key"])
    op.create_index("ix_memory_records_kind", "memory_records", ["kind"])
    op.create_index("ix_memory_scope", "memory_records", ["scope", "scope_key"])
    op.create_index("ix_memory_scope_kind", "memory_records", ["scope", "scope_key", "kind"])
