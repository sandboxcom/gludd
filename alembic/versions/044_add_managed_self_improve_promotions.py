"""Add durable fenced managed self-improvement promotion receipts.

Revision ID: 044
Revises: 043
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "044"
down_revision: str | None = "043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the immutable identity, lease, fence, and receipt ledger."""
    op.create_table(
        "managed_self_improve_promotions",
        sa.Column("artifact_digest", sa.String(length=64), nullable=False),
        sa.Column("plan_identity_digest", sa.String(length=64), nullable=False),
        sa.Column("attempt_identity_digest", sa.String(length=64), nullable=False),
        sa.Column("todo_id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("return_id", sa.String(length=64), nullable=False),
        sa.Column("repo_root", sa.String(length=1024), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("worktree_branch", sa.String(length=128), nullable=True),
        sa.Column("development_commit", sa.String(length=40), nullable=True),
        sa.Column("marker", sa.String(length=256), nullable=True),
        sa.Column("last_error", sa.String(length=4096), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'promoting', 'completed')",
            name="ck_managed_self_improve_promotions_state",
        ),
        sa.CheckConstraint(
            "fencing_token >= 0",
            name="ck_managed_self_improve_promotions_fencing_token",
        ),
        sa.CheckConstraint(
            "((state = 'completed' AND development_commit IS NOT NULL "
            "AND marker IS NOT NULL AND completed_at IS NOT NULL "
            "AND lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(state <> 'completed' AND development_commit IS NULL "
            "AND marker IS NULL AND completed_at IS NULL))",
            name="ck_managed_self_improve_promotions_receipt_state",
        ),
        sa.PrimaryKeyConstraint("artifact_digest"),
        sa.UniqueConstraint("return_id"),
    )
    op.create_index(
        "ix_managed_self_improve_promotions_state",
        "managed_self_improve_promotions",
        ["state"],
    )
    op.create_index(
        "ix_managed_self_improve_promotions_todo_id",
        "managed_self_improve_promotions",
        ["todo_id"],
    )
    op.create_index(
        "ix_managed_self_improve_promotions_project_id",
        "managed_self_improve_promotions",
        ["project_id"],
    )
    op.create_index(
        "ix_managed_self_improve_promotions_lease",
        "managed_self_improve_promotions",
        ["state", "lease_expires_at"],
    )
    op.create_index(
        "ix_managed_self_improve_promotions_project_todo",
        "managed_self_improve_promotions",
        ["project_id", "todo_id"],
    )


def downgrade() -> None:
    """Drop the managed-promotion ledger and its indexes."""
    op.drop_index(
        "ix_managed_self_improve_promotions_project_todo",
        table_name="managed_self_improve_promotions",
    )
    op.drop_index(
        "ix_managed_self_improve_promotions_lease",
        table_name="managed_self_improve_promotions",
    )
    op.drop_index(
        "ix_managed_self_improve_promotions_project_id",
        table_name="managed_self_improve_promotions",
    )
    op.drop_index(
        "ix_managed_self_improve_promotions_todo_id",
        table_name="managed_self_improve_promotions",
    )
    op.drop_index(
        "ix_managed_self_improve_promotions_state",
        table_name="managed_self_improve_promotions",
    )
    op.drop_table("managed_self_improve_promotions")
