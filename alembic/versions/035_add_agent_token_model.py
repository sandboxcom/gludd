"""Add the ``agent_tokens`` table for per-subagent STS token tracking.

Backs :class:`~general_ludd.db.models.AgentTokenModel`. Never stores
secret_id — that lives only in OpenBao. Fields per
docs/specs/FEATURE_STS_TOKENS.md §6.

Revision ID: 035
Revises: 034
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "035"
down_revision: str | None = "034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "agent_tokens"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("token_id", sa.String(length=64), primary_key=True),
        sa.Column("agent_id", sa.String(length=128), nullable=False),
        sa.Column("parent_agent_id", sa.String(length=128), nullable=False),
        sa.Column("role_name", sa.String(length=256), nullable=False),
        sa.Column("role_id", sa.String(length=128), nullable=False),
        sa.Column("scope_hash", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("scope_actions", sa.Text(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "hydration_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_index("ix_agent_tokens_agent_id", _TABLE, ["agent_id"])
    op.create_index("ix_agent_tokens_parent_agent_id", _TABLE, ["parent_agent_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_tokens_parent_agent_id", table_name=_TABLE)
    op.drop_index("ix_agent_tokens_agent_id", table_name=_TABLE)
    op.drop_table(_TABLE)
