"""Add event_work_transport table for fenced cross-worker claim transport.

Revision ID: 040
Revises: 039
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "040"
down_revision: str | None = "039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "event_work_transport",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(strftime('%Y-%m-%dT%H:%M:%f', 'now'))"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "length(payload) <= 65536",
            name="ck_event_work_transport_payload_len",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_event_work_transport"),
    )
    op.create_index(
        "ix_event_work_transport_event_type",
        "event_work_transport",
        ["event_type"],
    )
    op.create_index(
        "ix_event_work_transport_status",
        "event_work_transport",
        ["status"],
    )
    op.create_index(
        "ix_event_work_transport_claimed_by",
        "event_work_transport",
        ["claimed_by"],
    )
    op.create_index(
        "ix_event_work_transport_created_at",
        "event_work_transport",
        ["created_at"],
    )
    op.create_index(
        "ix_event_work_transport_claim",
        "event_work_transport",
        ["status", "claimed_at"],
    )
    op.create_index(
        "ix_event_work_transport_type_status",
        "event_work_transport",
        ["event_type", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_event_work_transport_type_status", table_name="event_work_transport")
    op.drop_index("ix_event_work_transport_claim", table_name="event_work_transport")
    op.drop_index("ix_event_work_transport_created_at", table_name="event_work_transport")
    op.drop_index("ix_event_work_transport_claimed_by", table_name="event_work_transport")
    op.drop_index("ix_event_work_transport_status", table_name="event_work_transport")
    op.drop_index("ix_event_work_transport_event_type", table_name="event_work_transport")
    op.drop_table("event_work_transport")
