"""Bound unbounded Text-blob columns with CHECK(length(col) <= N) (D-07).

docs/audit/NEW_FINDINGS_2026-06-16.md db/models.py P2: several Text columns
store JSON-in-Text blobs with no upper bound — an unbounded write is a DoS
vector (a single giant row blows up WAL size, query memory, and
backup/restore time). ``db/models.py`` now declares
``CheckConstraint(f"length({{column}}) <= {{MAX_JSON_BLOB_LEN}}", ...)`` on:

* ``task_decisions``: ``todo_updates``, ``child_todos``,
  ``validation_requests``, ``git_requests``, ``audit_notes``,
  ``policy_flags``
* ``audit_events``: ``details``

(``TodoModel``'s 13 Text columns are a separate, not-yet-scheduled
follow-on — deliberately out of scope here.)

SQLite has no ``ALTER TABLE ... ADD CONSTRAINT``, so Alembic automatically
rebuilds each table once with all of that table's new CHECK constraints in the
same batch block. PostgreSQL retains native ALTER TABLE semantics.

Revision ID: 026
Revises: 025
"""

from collections.abc import Sequence

from alembic import op

revision: str = "026"
down_revision: str | None = "025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_JSON_BLOB_LEN = 65536

# (table, column) pairs that get a length CHECK, grouped by table so each
# table is recreated exactly once.
_TASK_DECISIONS_COLUMNS = (
    "todo_updates",
    "child_todos",
    "validation_requests",
    "git_requests",
    "audit_notes",
    "policy_flags",
)
_AUDIT_EVENTS_COLUMNS = ("details",)


def _ck_name(table: str, column: str) -> str:
    return f"ck_{table}_{column}_len"


def upgrade() -> None:
    with op.batch_alter_table("task_decisions") as batch_op:
        for column in _TASK_DECISIONS_COLUMNS:
            batch_op.create_check_constraint(
                _ck_name("task_decisions", column),
                f"length({column}) <= {MAX_JSON_BLOB_LEN}",
            )

    with op.batch_alter_table("audit_events") as batch_op:
        for column in _AUDIT_EVENTS_COLUMNS:
            batch_op.create_check_constraint(
                _ck_name("audit_events", column),
                f"length({column}) <= {MAX_JSON_BLOB_LEN}",
            )


def downgrade() -> None:
    # Reverse order of upgrade.
    with op.batch_alter_table("audit_events") as batch_op:
        for column in reversed(_AUDIT_EVENTS_COLUMNS):
            batch_op.drop_constraint(_ck_name("audit_events", column), type_="check")

    with op.batch_alter_table("task_decisions") as batch_op:
        for column in reversed(_TASK_DECISIONS_COLUMNS):
            batch_op.drop_constraint(_ck_name("task_decisions", column), type_="check")
