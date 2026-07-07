"""Add the ``slurm_jobs`` table.

Persistent record of a Slurm job lifecycle — written when a job is submitted,
updated on completion/failure/cancellation.  The ``daemon_pid`` column ties
the row to the daemon process that submitted it, enabling orphan detection
(startup) and shutdown scancel (shutdown hook).

Revision ID: 023
Revises: 022
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "023"
down_revision: str | None = "022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "slurm_jobs"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(64), nullable=False),
        sa.Column("deployment_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("account", sa.String(64), nullable=False, server_default=""),
        sa.Column("qos", sa.String(64), nullable=False, server_default=""),
        sa.Column("partition", sa.String(64), nullable=False, server_default=""),
        sa.Column("gpu_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gpu_type", sa.String(64), nullable=False, server_default=""),
        sa.Column("max_hours", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("max_cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("hourly_rate_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("cost_incurred", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="submitted"),
        sa.Column(
            "submitted_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("daemon_pid", sa.Integer(), nullable=False),
    )
    op.create_index("ix_slurm_jobs_job_id", _TABLE, ["job_id"])
    op.create_index("ix_slurm_jobs_status", _TABLE, ["status"])
    op.create_index("ix_slurm_jobs_status_daemon", _TABLE, ["status", "daemon_pid"])
    op.create_index("ix_slurm_jobs_deployment", _TABLE, ["deployment_id"])


def downgrade() -> None:
    op.drop_index("ix_slurm_jobs_deployment", table_name=_TABLE)
    op.drop_index("ix_slurm_jobs_status_daemon", table_name=_TABLE)
    op.drop_index("ix_slurm_jobs_status", table_name=_TABLE)
    op.drop_index("ix_slurm_jobs_job_id", table_name=_TABLE)
    op.drop_table(_TABLE)
