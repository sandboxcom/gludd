"""Add shared deployment registry with fenced destroy ownership.

Revision ID: 039
Revises: 038
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "039"
down_revision: str | None = "038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deployment_records",
        sa.Column("instance_id", sa.String(length=128), nullable=False),
        sa.Column("working_dir", sa.String(length=1024), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model_name", sa.String(length=256), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("ip_address", sa.String(length=256), nullable=True),
        sa.Column("endpoint_url", sa.String(length=2048), nullable=True),
        sa.Column("destroy_owner", sa.String(length=128), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "revision > 0", name="ck_deployment_records_revision_positive"
        ),
        sa.CheckConstraint(
            "((state = 'destroying' AND destroy_owner IS NOT NULL) OR "
            "(state <> 'destroying' AND destroy_owner IS NULL))",
            name="ck_deployment_records_destroy_owner_state",
        ),
        sa.PrimaryKeyConstraint("instance_id", name="pk_deployment_records"),
    )
    op.create_index(
        "ix_deployment_records_state", "deployment_records", ["state"]
    )


def downgrade() -> None:
    op.drop_index("ix_deployment_records_state", table_name="deployment_records")
    op.drop_table("deployment_records")
