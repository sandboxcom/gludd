"""Alembic migration for prompt_profiles and benchmark_results tables."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prompt_profiles",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_url", sa.String(512), nullable=False, server_default=""),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("task_types", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("tags", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("version", sa.String(32), nullable=False, server_default="latest"),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    op.create_table(
        "benchmark_results",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("prompt_profile_id", sa.String(64), sa.ForeignKey("prompt_profiles.id"), nullable=True),
        sa.Column("model_profile_id", sa.String(64), nullable=False),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("task_description", sa.Text(), nullable=False, server_default=""),
        sa.Column("completion_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("code_quality_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("instruction_adherence_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("token_efficiency_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("time_seconds", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("success", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw_output", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    op.create_index("ix_benchmark_task_model", "benchmark_results", ["task_type", "model_profile_id"])
    op.create_index("ix_benchmark_task_prompt", "benchmark_results", ["task_type", "prompt_profile_id"])


def downgrade() -> None:
    op.drop_index("ix_benchmark_task_prompt", table_name="benchmark_results")
    op.drop_index("ix_benchmark_task_model", table_name="benchmark_results")
    op.drop_table("benchmark_results")
    op.drop_table("prompt_profiles")
