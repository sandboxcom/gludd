"""Add the ``sts_audit`` table for persistent STS audit logging.

The in-memory :class:`~general_ludd.security.sts.StsAuditLog` is sufficient
for a single-worker daemon, but a persistent backing store is required for
post-hoc investigation of token issuance / use / expiry across restarts.
This migration creates the ``sts_audit`` table that backs
:class:`~general_ludd.db.models.StsAuditModel`.

Revision ID: 012
Revises: 011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "012"
down_revision: str | None = "011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "sts_audit"
_IX_AGENTS = "ix_sts_audit_agents"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token_id", sa.String(length=64), nullable=False),
        sa.Column("issuer_agent_id", sa.String(length=128), nullable=False),
        sa.Column("subject_agent_id", sa.String(length=128), nullable=False),
        sa.Column("spec_yaml", sa.Text(), nullable=False),
        sa.Column("issued_at", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("last_used_at", sa.Float(), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("events", sa.Text(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.UniqueConstraint("token_id", name="uq_sts_audit_token_id"),
    )
    op.create_index("ix_sts_audit_token_id", _TABLE, ["token_id"], unique=True)
    op.create_index("ix_sts_audit_issuer_agent_id", _TABLE, ["issuer_agent_id"])
    op.create_index("ix_sts_audit_subject_agent_id", _TABLE, ["subject_agent_id"])
    op.create_index("ix_sts_audit_issued_at", _TABLE, ["issued_at"])
    op.create_index("ix_sts_audit_expires_at", _TABLE, ["expires_at"])
    op.create_index(_IX_AGENTS, _TABLE, ["issuer_agent_id", "subject_agent_id"])


def downgrade() -> None:
    op.drop_index(_IX_AGENTS, table_name=_TABLE)
    op.drop_index("ix_sts_audit_expires_at", table_name=_TABLE)
    op.drop_index("ix_sts_audit_issued_at", table_name=_TABLE)
    op.drop_index("ix_sts_audit_subject_agent_id", table_name=_TABLE)
    op.drop_index("ix_sts_audit_issuer_agent_id", table_name=_TABLE)
    op.drop_index("ix_sts_audit_token_id", table_name=_TABLE)
    op.drop_table(_TABLE)
