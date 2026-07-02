"""Add ``permission_escalation_request`` and ``remediation_actions`` tables.

Both tables are declared in ``general_ludd.db.models`` but had no migration,
so ``alembic upgrade head`` produced a schema missing them (they only
appeared via ``Base.metadata.create_all``). This migration brings the
migrated schema into line with the ORM.

``permission_escalation_request`` records agent permission-escalation
requests (``PermissionEscalationRequestModel``). ``remediation_actions`` is
the dispatcher's remediation audit trail (``RemediationActionModel``), with a
nullable ``project_id`` FK to ``projects`` (ON DELETE SET NULL).

Revision ID: 016
Revises: 015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "016"
down_revision: str | None = "015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ESC = "permission_escalation_request"
_REM = "remediation_actions"


def upgrade() -> None:
    op.create_table(
        _ESC,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("agent_id", sa.String(length=128), nullable=False),
        sa.Column("current_spec_yaml", sa.Text(), nullable=False),
        sa.Column("requested_capabilities_yaml", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "alternatives_tried_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("human_reviewer", sa.String(length=128), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_permission_escalation_request_agent_id", _ESC, ["agent_id"])
    op.create_index("ix_permission_escalation_request_status", _ESC, ["status"])
    op.create_index(
        "ix_permission_escalation_request_created_at", _ESC, ["created_at"]
    )

    op.create_table(
        _REM,
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("blocked_todo_id", sa.String(length=64), nullable=False),
        sa.Column(
            "project_id",
            sa.String(length=32),
            sa.ForeignKey("projects.project_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("blocker_kind", sa.String(length=32), nullable=False),
        sa.Column("action_kind", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("detail", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_remediation_actions_blocked_todo_id", _REM, ["blocked_todo_id"])
    op.create_index("ix_remediation_actions_project_id", _REM, ["project_id"])
    op.create_index("ix_remediation_actions_blocker_kind", _REM, ["blocker_kind"])
    op.create_index("ix_remediation_actions_action_kind", _REM, ["action_kind"])
    op.create_index("ix_remediation_actions_created_at", _REM, ["created_at"])
    op.create_index(
        "ix_remediation_actions_project_created", _REM, ["project_id", "created_at"]
    )
    op.create_index(
        "ix_remediation_actions_blocked_kind", _REM, ["blocked_todo_id", "action_kind"]
    )


def downgrade() -> None:
    op.drop_index("ix_remediation_actions_blocked_kind", table_name=_REM)
    op.drop_index("ix_remediation_actions_project_created", table_name=_REM)
    op.drop_index("ix_remediation_actions_created_at", table_name=_REM)
    op.drop_index("ix_remediation_actions_action_kind", table_name=_REM)
    op.drop_index("ix_remediation_actions_blocker_kind", table_name=_REM)
    op.drop_index("ix_remediation_actions_project_id", table_name=_REM)
    op.drop_index("ix_remediation_actions_blocked_todo_id", table_name=_REM)
    op.drop_table(_REM)

    op.drop_index("ix_permission_escalation_request_created_at", table_name=_ESC)
    op.drop_index("ix_permission_escalation_request_status", table_name=_ESC)
    op.drop_index("ix_permission_escalation_request_agent_id", table_name=_ESC)
    op.drop_table(_ESC)
