"""Add durable legacy self-improvement approval artifact bindings.

Revision ID: 045
Revises: 044
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "045"
down_revision: str | None = "044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MANAGED_POLICY = "managed_self_improve_plan"
_CONFIG_FIELDS = frozenset(
    {"capability_required", "change_content", "kind", "reason", "target_paths"}
)
_NON_CONFIG_FIELDS = frozenset(
    {
        "description",
        "kind",
        "project_id",
        "schema_version",
        "title",
        "worktree_path",
    }
)
_QUARANTINE_REASON = "Migration 045 quarantined an invalid queued legacy self-improve artifact"
_MAX_ARTIFACT_BYTES = 1_048_576
_MIGRATION_BATCH_SIZE = 500


def _legacy_artifact_is_exact(raw: object) -> bool:
    """Recognize only the two legacy artifacts eligible for manual apply."""
    if not isinstance(raw, str) or not raw or len(raw.encode("utf-8")) > _MAX_ARTIFACT_BYTES:
        return False
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return False
    if not isinstance(value, dict):
        return False
    fields = frozenset(value)
    if fields == _CONFIG_FIELDS:
        return (
            value["kind"] in {"config", "yaml"}
            and all(
                isinstance(value[field], str)
                for field in ("capability_required", "change_content", "kind", "reason")
            )
            and isinstance(value["target_paths"], list)
            and all(isinstance(path, str) for path in value["target_paths"])
        )
    if fields == _NON_CONFIG_FIELDS:
        return (
            type(value["schema_version"]) is int
            and value["schema_version"] == 1
            and all(
                isinstance(value[field], str)
                for field in _NON_CONFIG_FIELDS - {"schema_version"}
            )
            and bool(value["project_id"])
            and bool(value["title"])
            and value["kind"] not in {"config", "yaml"}
        )
    return False


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def upgrade() -> None:
    """Add the artifact binding and recover/quarantine pre-status queue rows."""
    with op.batch_alter_table("todos") as batch_op:
        batch_op.add_column(
            sa.Column("approved_artifact_digest", sa.String(length=64), nullable=True)
        )

    connection = op.get_bind()
    last_id = 0
    while True:
        rows = list(
            connection.execute(
                sa.text(
                    "SELECT id, approval_policy, plan_artifact FROM todos "
                    "WHERE status = 'queued' AND work_type = 'self_improve' "
                    "AND id > :last_id ORDER BY id LIMIT :batch_size"
                ),
                {"last_id": last_id, "batch_size": _MIGRATION_BATCH_SIZE},
            ).mappings()
        )
        if not rows:
            break
        for row in rows:
            raw = row["plan_artifact"]
            if row["approval_policy"] == _MANAGED_POLICY:
                if (
                    isinstance(raw, str)
                    and raw
                    and len(raw.encode("utf-8")) <= _MAX_ARTIFACT_BYTES
                ):
                    connection.execute(
                        sa.text(
                            "UPDATE todos SET approved_artifact_digest = :digest "
                            "WHERE id = :id AND status = 'queued'"
                        ),
                        {"digest": _digest(raw), "id": row["id"]},
                    )
                continue
            if _legacy_artifact_is_exact(raw):
                connection.execute(
                    sa.text(
                        "UPDATE todos SET status = 'approved', "
                        "approved_artifact_digest = :digest, version = version + 1 "
                        "WHERE id = :id AND status = 'queued'"
                    ),
                    {"digest": _digest(raw), "id": row["id"]},
                )
            else:
                connection.execute(
                    sa.text(
                        "UPDATE todos SET status = 'manual_hold', "
                        "manual_hold_reason = :reason, version = version + 1 "
                        "WHERE id = :id AND status = 'queued'"
                    ),
                    {"id": row["id"], "reason": _QUARANTINE_REASON},
                )
        last_id = int(rows[-1]["id"])


def downgrade() -> None:
    """Return recovered rows to a safe human gate and remove the binding."""
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE todos SET status = 'approval_required', version = version + 1 "
            "WHERE status = 'approved' AND work_type = 'self_improve'"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE todos SET status = 'approval_required', manual_hold_reason = NULL, "
            "version = version + 1 WHERE status = 'manual_hold' "
            "AND work_type = 'self_improve' AND manual_hold_reason = :reason"
        ),
        {"reason": _QUARANTINE_REASON},
    )
    with op.batch_alter_table("todos") as batch_op:
        batch_op.drop_column("approved_artifact_digest")
