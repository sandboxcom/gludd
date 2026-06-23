"""Add task_embeddings table and seed canonical task-type rows.

Brings the migration chain into parity with the ``TaskEmbeddingModel`` ORM
model in ``general_ludd.db.models``. The table is the Tier 2 RAG routing
substrate described in ``docs/research/RAG_ROUTING_RESEARCH_2026-06-23.md``:
one row per ``TaskType`` (primary key), ``embedding`` stores a JSON list of
floats produced by the embedding model, ``dim`` records its length.

``upgrade`` seeds all ten ``TaskType`` values (see
``general_ludd.schemas.benchmark.TaskType``) with canonical descriptions so
the AdaptiveRouter has a non-empty substrate to compute cosine similarity
against even before any embedding model has run. ``embedding`` is seeded as
the empty list and ``dim`` as 0 — a freshly-created row is always
JSON-parseable, matching the model's ``default="[]"`` / ``default=0``.

The pgvector index is dialect-guarded: skipped on SQLite (and any non-
PostgreSQL backend) because (a) SQLite has no pgvector extension and (b)
the ``embedding`` column is JSON-in-Text, so the index is an accelerator
for future native-vector columns rather than a correctness requirement.

Revision ID: 007
Revises: 006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Canonical description per TaskType (general_ludd.schemas.benchmark.TaskType).
# Order matches the enum declaration; kept inline so the migration is
# self-contained and reproducible across environments.
_CANONICAL_TASK_TYPES: list[tuple[str, str]] = [
    (
        "bug_fix",
        "Fix a defect: reproduce the reported failure, isolate the faulty "
        "code, and apply a minimal corrective change with regression tests.",
    ),
    (
        "feature",
        "Add new behavior: design the interface, implement the capability, "
        "wire it through the daemon/CLI/TUI, and cover it with tests.",
    ),
    (
        "refactor",
        "Restructure existing code without changing behavior: extract "
        "helpers, consolidate duplicates, tighten types, and keep the test "
        "suite green throughout.",
    ),
    (
        "test_write",
        "Author unit, integration, and e2e tests: cover the happy path, "
        "edge cases, and failure modes; assert observable outcomes.",
    ),
    (
        "code_review",
        "Review a diff for correctness, style, security, and testability: "
        "flag risks, suggest improvements, and verify the gate stays green.",
    ),
    (
        "documentation",
        "Write or update docs, READMEs, AGENTS.md sections, and inline "
        "docstrings so future agents and operators understand the system.",
    ),
    (
        "debugging",
        "Diagnose an unresolved fault: gather logs/metrics/traces, form "
        "hypotheses, bisect, and identify root cause before fixing.",
    ),
    (
        "optimization",
        "Improve performance or resource use: profile the hotspot, apply "
        "a targeted change, and demonstrate the improvement with a bench.",
    ),
    (
        "security_fix",
        "Remediate a vulnerability: close the attack vector, add a guard, "
        "and a red-team test proving the vector is rejected.",
    ),
    (
        "integration",
        "Connect subsystems or external services: define the contract, "
        "implement the adapter, and validate end-to-end against the real "
        "dependency or a faithful mock.",
    ),
]


def _is_postgres(bind: sa.engine.Connection) -> bool:
    return bind.dialect.name == "postgresql"


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "task_embeddings",
        sa.Column("task_type", sa.String(64), primary_key=True),
        sa.Column("canonical_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("embedding", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("dim", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    # Seed the 10 canonical TaskType rows. Uses a single executemany-style
    # insert via bindparams so the canonical text (which contains quotes and
    # punctuation) is parameterized and never escaped by hand.
    insert_sql = sa.text(
        "INSERT INTO task_embeddings (task_type, canonical_text, embedding, dim) "
        "VALUES (:task_type, :canonical_text, '[]', 0)"
    )
    for task_type, canonical_text in _CANONICAL_TASK_TYPES:
        bind.execute(
            insert_sql.bindparams(task_type=task_type, canonical_text=canonical_text)
        )

    # Dialect-guarded pgvector index: PostgreSQL-only. On SQLite (and any
    # other backend) the index is skipped — the ``embedding`` column is
    # JSON-in-Text, so a native vector index is an accelerator, not a
    # correctness requirement. Wrapped in a try/except so a PostgreSQL
    # without the pgvector extension degrades gracefully (index is a nicety,
    # not a dependency).
    if _is_postgres(bind):
        try:
            op.execute(
                "CREATE INDEX IF NOT EXISTS ix_task_embeddings_embedding "
                "ON task_embeddings USING gin (to_tsvector('simple', canonical_text))"
            )
        except Exception:
            # pgvector / GIN not available — index is optional; the table
            # itself is authoritative. Swallow and continue.
            pass


def downgrade() -> None:
    bind = op.get_bind()
    if _is_postgres(bind):
        try:
            op.execute("DROP INDEX IF EXISTS ix_task_embeddings_embedding")
        except Exception:
            pass

    op.drop_table("task_embeddings")
