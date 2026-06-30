"""Add the ``ornith_training_pairs`` table.

Captures ``(scaffold, outcome)`` pairs from Ornith invocations to feed
the offline RL trainer per the symbiotic design (see
``docs/design/SYMBIOTIC_AGENT_INTEGRATION.md`` §5.7). Each row records
the scaffold Ornith produced (playbook/module/plugin/rego/patch) plus
the eventual outcome of applying it (gate green/red, review rejection,
revert). Outcome is set later by the ``OutcomeObserver``.

Revision ID: 014
Revises: 013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "014"
down_revision: str | None = "013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "ornith_training_pairs"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("invoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("task_description", sa.Text(), nullable=False),
        sa.Column("target_files", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("scaffold_kind", sa.String(length=32), nullable=False),
        sa.Column("scaffold_content", sa.Text(), nullable=False),
        sa.Column("scaffold_hash", sa.String(length=64), nullable=False),
        sa.Column("iterations_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_consumed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model_sha", sa.String(length=128), nullable=False, server_default=""),
        sa.Column(
            "outcome_status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("outcome_details", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("outcome_set_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("project_id", sa.String(length=32), nullable=True),
        sa.Column("agent_id", sa.String(length=128), nullable=False),
    )
    op.create_index("ix_ornith_pairs_scaffold_hash", _TABLE, ["scaffold_hash"])
    op.create_index("ix_ornith_pairs_outcome_status", _TABLE, ["outcome_status"])
    op.create_index("ix_ornith_pairs_invoked_at", _TABLE, ["invoked_at"])
    op.create_index("ix_ornith_pairs_project_id", _TABLE, ["project_id"])
    op.create_index("ix_ornith_pairs_status_invoked", _TABLE, ["outcome_status", "invoked_at"])


def downgrade() -> None:
    op.drop_index("ix_ornith_pairs_status_invoked", table_name=_TABLE)
    op.drop_index("ix_ornith_pairs_project_id", table_name=_TABLE)
    op.drop_index("ix_ornith_pairs_invoked_at", table_name=_TABLE)
    op.drop_index("ix_ornith_pairs_outcome_status", table_name=_TABLE)
    op.drop_index("ix_ornith_pairs_scaffold_hash", table_name=_TABLE)
    op.drop_table(_TABLE)
