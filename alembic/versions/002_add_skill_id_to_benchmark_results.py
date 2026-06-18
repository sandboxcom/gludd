"""Add skill_id dimension to benchmark_results for skill-vs-model usefulness tracking.

Revision ID: 002
Revises: 001
Create Date: 2026-06-18 00:00:00.000000

P3 from docs/e2e_harness/AUDIT_model_discovery_weights_cost.md:
Adds a nullable skill_id column to benchmark_results so gludd can track which
skills work better with which models. Also adds a composite index on
(skill_id, model_profile_id) to support the usefulness query in
get_aggregate_scores().

This is a safe additive migration: nullable column on existing data, no
backfill required.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("benchmark_results", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("skill_id", sa.String(length=128), nullable=True)
        )
        batch_op.create_index(
            "ix_benchmark_results_skill_id",
            ["skill_id"],
        )
        batch_op.create_index(
            "ix_benchmark_skill_model",
            ["skill_id", "model_profile_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("benchmark_results", schema=None) as batch_op:
        batch_op.drop_index("ix_benchmark_skill_model")
        batch_op.drop_index("ix_benchmark_results_skill_id")
        batch_op.drop_column("skill_id")
