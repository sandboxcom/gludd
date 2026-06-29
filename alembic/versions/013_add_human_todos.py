"""Add the ``human_todos`` table.

A separate table for bot→human requests (distinct from the agent ``todos``
table). An agent files a HumanTodo when it cannot complete its task without a
human action; the human resolves it and the parent agent todo is unblocked.

Revision ID: 013
Revises: 012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "013"
down_revision: str | None = "012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "human_todos"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("parent_agent_todo_id", sa.String(length=32), nullable=True),
        sa.Column("agent_id", sa.String(length=128), nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("human_resolution", sa.Text(), nullable=True),
        sa.Column("human_resolver", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tags", sa.Text(), nullable=False, server_default="[]"),
    )
    op.create_index("ix_human_todos_parent_agent_todo_id", _TABLE, ["parent_agent_todo_id"])
    op.create_index("ix_human_todos_agent_id", _TABLE, ["agent_id"])
    op.create_index("ix_human_todos_session_id", _TABLE, ["session_id"])
    op.create_index("ix_human_todos_category", _TABLE, ["category"])
    op.create_index("ix_human_todos_priority", _TABLE, ["priority"])
    op.create_index("ix_human_todos_status", _TABLE, ["status"])
    op.create_index("ix_human_todos_created_at", _TABLE, ["created_at"])
    op.create_index("ix_human_todos_status_category", _TABLE, ["status", "category"])
    op.create_index("ix_human_todos_status_priority", _TABLE, ["status", "priority"])


def downgrade() -> None:
    op.drop_index("ix_human_todos_status_priority", table_name=_TABLE)
    op.drop_index("ix_human_todos_status_category", table_name=_TABLE)
    op.drop_index("ix_human_todos_created_at", table_name=_TABLE)
    op.drop_index("ix_human_todos_status", table_name=_TABLE)
    op.drop_index("ix_human_todos_priority", table_name=_TABLE)
    op.drop_index("ix_human_todos_category", table_name=_TABLE)
    op.drop_index("ix_human_todos_session_id", table_name=_TABLE)
    op.drop_index("ix_human_todos_agent_id", table_name=_TABLE)
    op.drop_index("ix_human_todos_parent_agent_todo_id", table_name=_TABLE)
    op.drop_table(_TABLE)
