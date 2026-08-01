"""Reconcile migration drift with ORM models (WP-D3).

Brings ``alembic upgrade head`` into structural parity with
``Base.metadata.create_all`` by fixing two categories of drift surfaced by
``tests/unit/test_alembic_create_all_parity.py``:

INDEX drift (5 tables):

* ``benchmark_results`` — 4 single-column indexes declared by ORM column
  ``index=True`` but absent from the migration chain.
* ``memory_records`` — name-prefix mismatch (migration 022 used
  ``ix_memory_*``; ORM uses ``ix_memory_records_*``) plus a missing
  ``ix_memory_records_namespace`` index.
* ``model_performance`` — uniqueness mismatch: ORM column ``unique=True``
  makes the index UNIQUE; migration 015 created it non-unique.
* ``ornith_training_pairs`` — name-prefix mismatch (migration 014a used
  ``ix_ornith_pairs_*``; ORM uses ``ix_ornith_training_pairs_*``).
* ``task_decisions`` — migration 001 created a plain index on ``return_id``
  that duplicates the UNIQUE constraint added by migration 017; the ORM
  does not declare a named index here.

FK ondelete drift (10 tables):

* ``audit_events``, ``benchmark_results``, ``bucket_leases``, ``queues``,
  ``task_decisions``, ``task_returns``, ``todos``, ``variable_namespaces``
  — project_id FKs declared ``ondelete="SET NULL"`` in the ORM but created
  WITHOUT the clause by migrations 002/004, leaving SQLite at ``NO ACTION``.
* ``todo_events`` — project_id SET NULL + todo_id CASCADE, both at
  ``NO ACTION`` in the migrations.
* ``variable_values`` — namespace_id CASCADE at ``NO ACTION``.

SQLite cannot ``ALTER TABLE ... DROP CONSTRAINT``; every FK change uses
``batch_alter_table``. A ``naming_convention`` is supplied so that the
previously-unnamed FKs (migrations 001/004) acquire deterministic names
during reflection and can be dropped by name.

Revision ID: 024
Revises: 023
"""

from collections.abc import Sequence

from alembic import op

revision: str = "024"
down_revision: str | None = "023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}

# project_id → projects: named FKs from migration 002 that need ondelete=SET NULL.
_PROJECT_ID_TABLES = (
    "audit_events",
    "bucket_leases",
    "queues",
    "task_decisions",
    "task_returns",
    "todos",
    "variable_namespaces",
)


def _legacy_fk_name(batch_name: str, postgresql_name: str) -> str:
    """Select the name assigned by the original migration on this dialect."""
    if op.get_bind().dialect.name == "postgresql":
        return postgresql_name
    return batch_name


def upgrade() -> None:
    _upgrade_indexes()
    _upgrade_foreign_keys()


def downgrade() -> None:
    _downgrade_foreign_keys()
    _downgrade_indexes()


# ── indexes ───────────────────────────────────────────────────────────


def _upgrade_indexes() -> None:
    # benchmark_results: 4 single-column indexes from ORM index=True.
    for col in ("model_profile_id", "prompt_profile_id", "task_role", "task_type"):
        op.create_index(
            f"ix_benchmark_results_{col}", "benchmark_results", [col]
        )

    # memory_records: rename ix_memory_agent_id → ix_memory_records_agent_id;
    # add missing ix_memory_records_namespace.
    op.drop_index("ix_memory_agent_id", table_name="memory_records")
    op.create_index(
        "ix_memory_records_agent_id", "memory_records", ["agent_id"]
    )
    op.create_index(
        "ix_memory_records_namespace", "memory_records", ["namespace"]
    )

    # model_performance: unique=True on ORM column makes the index UNIQUE.
    op.drop_index(
        "ix_model_performance_model_profile_id", table_name="model_performance"
    )
    op.create_index(
        "ix_model_performance_model_profile_id",
        "model_performance",
        ["model_profile_id"],
        unique=True,
    )

    # ornith_training_pairs: rename ix_ornith_pairs_* → ix_ornith_training_pairs_*.
    for col in ("scaffold_hash", "outcome_status", "invoked_at", "project_id"):
        op.drop_index(
            f"ix_ornith_pairs_{col}", table_name="ornith_training_pairs"
        )
        op.create_index(
            f"ix_ornith_training_pairs_{col}",
            "ornith_training_pairs",
            [col],
        )

    # task_decisions: plain index duplicates the UNIQUE constraint (017).
    op.drop_index("ix_task_decisions_return_id", table_name="task_decisions")


def _downgrade_indexes() -> None:
    op.create_index(
        "ix_task_decisions_return_id", "task_decisions", ["return_id"]
    )

    for col in ("project_id", "invoked_at", "outcome_status", "scaffold_hash"):
        op.drop_index(
            f"ix_ornith_training_pairs_{col}",
            table_name="ornith_training_pairs",
        )
        op.create_index(
            f"ix_ornith_pairs_{col}", "ornith_training_pairs", [col]
        )

    op.drop_index(
        "ix_model_performance_model_profile_id", table_name="model_performance"
    )
    op.create_index(
        "ix_model_performance_model_profile_id",
        "model_performance",
        ["model_profile_id"],
    )

    op.drop_index(
        "ix_memory_records_namespace", table_name="memory_records"
    )
    op.drop_index(
        "ix_memory_records_agent_id", table_name="memory_records"
    )
    op.create_index("ix_memory_agent_id", "memory_records", ["agent_id"])

    for col in ("task_type", "task_role", "prompt_profile_id", "model_profile_id"):
        op.drop_index(
            f"ix_benchmark_results_{col}", table_name="benchmark_results"
        )


# ── foreign keys ──────────────────────────────────────────────────────


def _upgrade_foreign_keys() -> None:
    # project_id → projects (SET NULL) — named FKs from migration 002.
    for table in _PROJECT_ID_TABLES:
        fk_name = f"fk_{table}_project_id"
        with op.batch_alter_table(
            table, naming_convention=NAMING_CONVENTION
        ) as batch_op:
            batch_op.drop_constraint(fk_name, type_="foreignkey")
            batch_op.create_foreign_key(
                fk_name,
                "projects",
                ["project_id"],
                ["project_id"],
                ondelete="SET NULL",
            )

    # todo_events: project_id (SET NULL) + todo_id (CASCADE).
    with op.batch_alter_table(
        "todo_events", naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_constraint(
            "fk_todo_events_project_id", type_="foreignkey"
        )
        batch_op.create_foreign_key(
            "fk_todo_events_project_id",
            "projects",
            ["project_id"],
            ["project_id"],
            ondelete="SET NULL",
        )
        batch_op.drop_constraint(
            _legacy_fk_name(
                "fk_todo_events_todo_id_todos", "todo_events_todo_id_fkey"
            ),
            type_="foreignkey",
        )
        batch_op.create_foreign_key(
            "fk_todo_events_todo_id_todos",
            "todos",
            ["todo_id"],
            ["todo_id"],
            ondelete="CASCADE",
        )

    # variable_values: namespace_id (CASCADE) — unnamed FK from migration 001.
    with op.batch_alter_table(
        "variable_values", naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_constraint(
            _legacy_fk_name(
                "fk_variable_values_namespace_id_variable_namespaces",
                "variable_values_namespace_id_fkey",
            ),
            type_="foreignkey",
        )
        batch_op.create_foreign_key(
            "fk_variable_values_namespace_id_variable_namespaces",
            "variable_namespaces",
            ["namespace_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # benchmark_results: prompt_profile_id (SET NULL) — unnamed FK from
    # migration 004. project_id FK (migration 009) already has SET NULL.
    with op.batch_alter_table(
        "benchmark_results",
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        batch_op.drop_constraint(
            _legacy_fk_name(
                "fk_benchmark_results_prompt_profile_id_prompt_profiles",
                "benchmark_results_prompt_profile_id_fkey",
            ),
            type_="foreignkey",
        )
        batch_op.create_foreign_key(
            "fk_benchmark_results_prompt_profile_id_prompt_profiles",
            "prompt_profiles",
            ["prompt_profile_id"],
            ["id"],
            ondelete="SET NULL",
        )


def _downgrade_foreign_keys() -> None:
    # Reverse order of upgrade.

    with op.batch_alter_table(
        "benchmark_results",
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        batch_op.drop_constraint(
            "fk_benchmark_results_prompt_profile_id_prompt_profiles",
            type_="foreignkey",
        )
        batch_op.create_foreign_key(
            _legacy_fk_name(
                "fk_benchmark_results_prompt_profile_id_prompt_profiles",
                "benchmark_results_prompt_profile_id_fkey",
            ),
            "prompt_profiles",
            ["prompt_profile_id"],
            ["id"],
        )

    with op.batch_alter_table(
        "variable_values", naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_constraint(
            "fk_variable_values_namespace_id_variable_namespaces",
            type_="foreignkey",
        )
        batch_op.create_foreign_key(
            _legacy_fk_name(
                "fk_variable_values_namespace_id_variable_namespaces",
                "variable_values_namespace_id_fkey",
            ),
            "variable_namespaces",
            ["namespace_id"],
            ["id"],
        )

    with op.batch_alter_table(
        "todo_events", naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_constraint(
            "fk_todo_events_todo_id_todos", type_="foreignkey"
        )
        batch_op.create_foreign_key(
            _legacy_fk_name(
                "fk_todo_events_todo_id_todos", "todo_events_todo_id_fkey"
            ),
            "todos",
            ["todo_id"],
            ["todo_id"],
        )
        batch_op.drop_constraint(
            "fk_todo_events_project_id", type_="foreignkey"
        )
        batch_op.create_foreign_key(
            "fk_todo_events_project_id",
            "projects",
            ["project_id"],
            ["project_id"],
        )

    for table in reversed(_PROJECT_ID_TABLES):
        fk_name = f"fk_{table}_project_id"
        with op.batch_alter_table(
            table, naming_convention=NAMING_CONVENTION
        ) as batch_op:
            batch_op.drop_constraint(fk_name, type_="foreignkey")
            batch_op.create_foreign_key(
                fk_name, "projects", ["project_id"], ["project_id"]
            )
