"""Add the skill dimension to benchmark results.

Revision ID: 041
Revises: 040

The nullable column is an additive, zero-downtime change: existing benchmark
history remains valid as global-to-skill data and requires no backfill.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "041"
down_revision: str | None = "040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("benchmark_results", schema=None) as batch_op:
        batch_op.add_column(sa.Column("skill_id", sa.String(length=128), nullable=True))
        batch_op.create_index("ix_benchmark_results_skill_id", ["skill_id"])
        batch_op.create_index(
            "ix_benchmark_skill_model",
            ["skill_id", "model_profile_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("benchmark_results", schema=None) as batch_op:
        batch_op.drop_index("ix_benchmark_skill_model")
        batch_op.drop_index("ix_benchmark_results_skill_id")
        batch_op.drop_column("skill_id")
