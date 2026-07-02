"""Add ``model_call_logs`` and ``model_performance`` tables.

Tracks per-call model usage (``model_call_logs``) and pre-aggregated
rolling performance stats per model profile (``model_performance``).

Revision ID: 015
Revises: 014a
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "015"
down_revision: str | None = "014a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CALL_LOGS = "model_call_logs"
_PERFORMANCE = "model_performance"


def upgrade() -> None:
    # ── model_call_logs ──────────────────────────────────────────────────
    op.create_table(
        _CALL_LOGS,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("todo_id", sa.String(length=64), nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("service", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("model_profile_id", sa.String(length=64), nullable=False),
        sa.Column(
            "task_type",
            sa.String(length=32),
            nullable=False,
            server_default="generation",
        ),
        sa.Column("work_type", sa.String(length=32), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("duration_ms", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("error_code", sa.String(length=32), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
    )
    op.create_index("ix_model_call_logs_todo_id", _CALL_LOGS, ["todo_id"])
    op.create_index(
        "ix_model_call_logs_model_profile_id", _CALL_LOGS, ["model_profile_id"]
    )
    op.create_index("ix_model_call_logs_created_at", _CALL_LOGS, ["created_at"])
    op.create_index("ix_model_call_logs_created", _CALL_LOGS, ["created_at"])
    op.create_index(
        "ix_model_call_logs_profile_created",
        _CALL_LOGS,
        ["model_profile_id", "created_at"],
    )

    # ── model_performance ────────────────────────────────────────────────
    op.create_table(
        _PERFORMANCE,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "model_profile_id",
            sa.String(length=64),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "model_name", sa.String(length=128), nullable=False, server_default=""
        ),
        sa.Column(
            "service", sa.String(length=64), nullable=False, server_default=""
        ),
        sa.Column("total_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "successful_calls", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("failed_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "total_input_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "total_output_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "total_cost_usd", sa.Float(), nullable=False, server_default="0.0"
        ),
        sa.Column(
            "avg_duration_ms", sa.Float(), nullable=False, server_default="0.0"
        ),
        sa.Column("last_call_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_call_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False
        ),
    )
    op.create_index(
        "ix_model_performance_model_profile_id",
        _PERFORMANCE,
        ["model_profile_id"],
    )


def downgrade() -> None:
    # model_performance
    op.drop_index(
        "ix_model_performance_model_profile_id", table_name=_PERFORMANCE
    )
    op.drop_table(_PERFORMANCE)

    # model_call_logs
    op.drop_index(
        "ix_model_call_logs_profile_created", table_name=_CALL_LOGS
    )
    op.drop_index("ix_model_call_logs_created", table_name=_CALL_LOGS)
    op.drop_index("ix_model_call_logs_created_at", table_name=_CALL_LOGS)
    op.drop_index(
        "ix_model_call_logs_model_profile_id", table_name=_CALL_LOGS
    )
    op.drop_index("ix_model_call_logs_todo_id", table_name=_CALL_LOGS)
    op.drop_table(_CALL_LOGS)
