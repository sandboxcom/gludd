"""Add idempotency_key column and dedup index to remediation_actions (C25).

Revision ID: 027
Revises: 026

Adds:
- ``idempotency_key`` (nullable, unique, indexed)
- Composite index on ``(blocked_todo_id, action_kind, created_at)``

Uses ``batch_alter_table(recreate="always")`` so the migration works on
SQLite (which can't ALTER constraints in-place).
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "027"
down_revision: str | None = "026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("remediation_actions", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column("idempotency_key", sa.String(length=256), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_remediation_actions_idempotency_key",
            ["idempotency_key"],
        )
        batch_op.create_index(
            "ix_remediation_actions_idempotency_key",
            ["idempotency_key"],
            unique=True,
        )
        batch_op.create_index(
            "ix_remediation_actions_dedup",
            ["blocked_todo_id", "action_kind", "created_at"],
        )


def downgrade() -> None:
    with op.batch_alter_table("remediation_actions", recreate="always") as batch_op:
        batch_op.drop_index("ix_remediation_actions_dedup")
        batch_op.drop_index("ix_remediation_actions_idempotency_key")
        batch_op.drop_constraint(
            "uq_remediation_actions_idempotency_key",
            type_="unique",
        )
        batch_op.drop_column("idempotency_key")
