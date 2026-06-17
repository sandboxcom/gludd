"""Alembic migration for runtime tables: features, agent_messages, spend_records,
role_runs, memory_records; and prompt_profiles.updated_at.

Brings the migration chain (head 004) into parity with Base.metadata so that
``alembic upgrade head`` reproduces the ORM models exactly.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "features",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(32),
            sa.ForeignKey("projects.project_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(256), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("category", sa.String(64), nullable=False, server_default="general"),
        sa.Column("status", sa.String(32), nullable=False, server_default="requested"),
        sa.Column("acceptance_criteria", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("evidence", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("verifier_kind", sa.String(64), nullable=False, server_default="evidence"),
        sa.Column("requested_by", sa.String(128), nullable=False, server_default="agent"),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verify_detail", sa.Text(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_features_project_id", "features", ["project_id"])
    op.create_index("ix_features_status", "features", ["status"])

    op.create_table(
        "agent_messages",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(32),
            sa.ForeignKey("projects.project_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sender", sa.String(128), nullable=False),
        sa.Column("recipient", sa.String(128), nullable=False),
        sa.Column("topic", sa.String(256), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("priority", sa.String(16), nullable=False, server_default="normal"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ttl_seconds", sa.Integer(), nullable=True),
    )
    op.create_index("ix_agent_messages_project_id", "agent_messages", ["project_id"])
    op.create_index("ix_agent_messages_recipient", "agent_messages", ["recipient"])
    op.create_index("ix_agent_messages_created_at", "agent_messages", ["created_at"])
    op.create_index(
        "ix_agent_messages_recipient_read", "agent_messages", ["recipient", "read_at"]
    )

    op.create_table(
        "spend_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.String(32),
            sa.ForeignKey("projects.project_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("ts", sa.Float(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("model", sa.String(256), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_spend_records_project_id", "spend_records", ["project_id"])
    op.create_index("ix_spend_records_ts", "spend_records", ["ts"])
    op.create_index("ix_spend_records_ts_kind", "spend_records", ["ts", "kind"])

    op.create_table(
        "role_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.String(32),
            sa.ForeignKey("projects.project_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("role", sa.String(128), nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_role_runs_project_id", "role_runs", ["project_id"])
    op.create_index("ix_role_runs_role", "role_runs", ["role"])
    op.create_index("ix_role_runs_project_role", "role_runs", ["project_id", "role"])

    op.create_table(
        "memory_records",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("scope", sa.String(16), nullable=False, server_default="global"),
        sa.Column("scope_key", sa.String(128), nullable=False, server_default=""),
        sa.Column("kind", sa.String(16), nullable=False, server_default="fact"),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("tags", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("source", sa.String(256), nullable=False, server_default=""),
        sa.Column("embedding", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_memory_records_scope", "memory_records", ["scope"])
    op.create_index("ix_memory_records_scope_key", "memory_records", ["scope_key"])
    op.create_index("ix_memory_records_kind", "memory_records", ["kind"])
    op.create_index("ix_memory_scope", "memory_records", ["scope", "scope_key"])
    op.create_index("ix_memory_scope_kind", "memory_records", ["scope", "scope_key", "kind"])

    with op.batch_alter_table("prompt_profiles") as batch_op:
        batch_op.add_column(
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("prompt_profiles") as batch_op:
        batch_op.drop_column("updated_at")

    op.drop_index("ix_memory_scope_kind", table_name="memory_records")
    op.drop_index("ix_memory_scope", table_name="memory_records")
    op.drop_index("ix_memory_records_kind", table_name="memory_records")
    op.drop_index("ix_memory_records_scope_key", table_name="memory_records")
    op.drop_index("ix_memory_records_scope", table_name="memory_records")
    op.drop_table("memory_records")

    op.drop_index("ix_role_runs_project_role", table_name="role_runs")
    op.drop_index("ix_role_runs_role", table_name="role_runs")
    op.drop_index("ix_role_runs_project_id", table_name="role_runs")
    op.drop_table("role_runs")

    op.drop_index("ix_spend_records_ts_kind", table_name="spend_records")
    op.drop_index("ix_spend_records_ts", table_name="spend_records")
    op.drop_index("ix_spend_records_project_id", table_name="spend_records")
    op.drop_table("spend_records")

    op.drop_index("ix_agent_messages_recipient_read", table_name="agent_messages")
    op.drop_index("ix_agent_messages_created_at", table_name="agent_messages")
    op.drop_index("ix_agent_messages_recipient", table_name="agent_messages")
    op.drop_index("ix_agent_messages_project_id", table_name="agent_messages")
    op.drop_table("agent_messages")

    op.drop_index("ix_features_status", table_name="features")
    op.drop_index("ix_features_project_id", table_name="features")
    op.drop_table("features")
