"""Scope persistent-memory uniqueness to a project.

Revision ID: 037
Revises: 036
"""

from collections.abc import Sequence

from alembic import op

revision: str = "037"
down_revision: str | None = "036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("memory_records") as batch_op:
        batch_op.drop_constraint("uq_memory_agent_key_ns", type_="unique")
        batch_op.create_unique_constraint(
            "uq_memory_agent_key_ns_project",
            ["agent_id", "key", "namespace", "project_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("memory_records") as batch_op:
        batch_op.drop_constraint("uq_memory_agent_key_ns_project", type_="unique")
        batch_op.create_unique_constraint(
            "uq_memory_agent_key_ns",
            ["agent_id", "key", "namespace"],
        )
