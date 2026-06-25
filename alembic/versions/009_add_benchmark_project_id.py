"""Add project_id to benchmark_results (project-aware benchmark history).

Brings the migration chain into parity with the ``BenchmarkResultModel`` ORM
model in ``general_ludd.db.models`` after project-hierarchy phase 3 made
benchmark history project-aware. The new ``project_id`` column lets the
``AdaptiveRouter`` key aggregate scores per project so it can borrow proven
prompt+model picks ACROSS declared project edges (parent/child/sibling/external)
with edge-distance decay. NULL = global/legacy history (today's behaviour).

The FK to ``projects.project_id`` uses ``ON DELETE SET NULL`` (the repo-wide
project_id convention): deleting a project degrades its benchmark history to
global rather than destroying the rows.

SQLite cannot ``ALTER TABLE ... ADD CONSTRAINT``; a bare ``op.create_foreign_key``
fails on SQLite outside batch mode (this is the pre-existing condition that breaks
migration 002 under ``make alembic-check``). So the FK here is added via
``batch_alter_table`` (table copy + rebuild), mirroring migration 006's pattern.
The column add and the index are plain ``op.add_column`` / ``op.create_index``
(both supported natively on SQLite).

Revision ID: 009
Revises: 008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: str | None = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FK_BENCHMARK_PROJECT = "fk_benchmark_results_project_id"
IX_BENCHMARK_PROJECT = "ix_benchmark_results_project_id"
IX_BENCHMARK_PROJECT_TASK = "ix_benchmark_project_task"


def upgrade() -> None:
    # 1. Additive column — natively supported on SQLite (no constraint).
    op.add_column(
        "benchmark_results",
        sa.Column("project_id", sa.String(32), nullable=True),
    )
    # 2a. Single-column index — matches the ORM column-level index=True so the
    #     migration/ORM parity gate (alembic-check) sees the implied index.
    op.create_index(
        IX_BENCHMARK_PROJECT,
        "benchmark_results",
        ["project_id"],
    )
    # 2b. Composite (project_id, task_type) index — the project-aware
    #     aggregation key used by get_aggregate_scores(project_id=...).
    op.create_index(
        IX_BENCHMARK_PROJECT_TASK,
        "benchmark_results",
        ["project_id", "task_type"],
    )
    # 3. FK to projects.project_id (SET NULL). Added through batch_alter_table so
    #    SQLite (which cannot ALTER ... ADD CONSTRAINT) rebuilds the table; mirrors
    #    migration 006. The alembic env enables render_as_batch globally, but we
    #    use an explicit batch block here so the FK lands on every backend.
    with op.batch_alter_table("benchmark_results", recreate="always") as batch_op:
        batch_op.create_foreign_key(
            FK_BENCHMARK_PROJECT,
            "projects",
            ["project_id"],
            ["project_id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("benchmark_results", recreate="always") as batch_op:
        batch_op.drop_constraint(FK_BENCHMARK_PROJECT, type_="foreignkey")
    op.drop_index(IX_BENCHMARK_PROJECT_TASK, "benchmark_results")
    op.drop_index(IX_BENCHMARK_PROJECT, "benchmark_results")
    op.drop_column("benchmark_results", "project_id")
