"""TDD tests for skill_id dimension on benchmark_results (P3).

Tests:
1. BenchmarkResultModel has a skill_id column (model-level check).
2. record_job_benchmark writes skill_id into the DB row.
3. Written row can be read back with correct skill_id.
4. record_job_benchmark backward-compat: skill_id defaults to None.
5. get_aggregate_scores groups correctly when two rows share (model_id, skill_id).
6. get_aggregate_scores skill_id filter works.
7. Alembic migration 002 upgrades and downgrades cleanly on a real SQLite DB.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from general_ludd.db.models import Base, BenchmarkResultModel
from general_ludd.db.repository import BenchmarkRepository
from general_ludd.event_loop.benchmark import record_job_benchmark

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_async_session_factory(engine: Any) -> Any:
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _setup_db() -> tuple[Any, Any]:
    """Return (engine, session_factory) for an in-memory SQLite DB."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = _make_async_session_factory(engine)
    return engine, factory


# ---------------------------------------------------------------------------
# 1. Model-level: column exists on BenchmarkResultModel
# ---------------------------------------------------------------------------

def test_benchmark_result_model_has_skill_id_column() -> None:
    cols = {c.name for c in BenchmarkResultModel.__table__.columns}
    assert "skill_id" in cols, "BenchmarkResultModel must have a skill_id column"


def test_benchmark_skill_id_column_is_nullable() -> None:
    col = BenchmarkResultModel.__table__.columns["skill_id"]
    assert col.nullable, "skill_id column must be nullable"


def test_benchmark_skill_model_index_exists() -> None:
    index_names = {idx.name for idx in BenchmarkResultModel.__table__.indexes}
    assert "ix_benchmark_skill_model" in index_names, (
        "Composite index ix_benchmark_skill_model(skill_id, model_profile_id) must exist"
    )


# ---------------------------------------------------------------------------
# 2-4. record_job_benchmark write path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_job_benchmark_writes_skill_id() -> None:
    engine, factory = await _setup_db()
    repo = BenchmarkRepository(session_factory=factory)

    recorder = MagicMock()
    recorder._repo = repo

    await record_job_benchmark(
        recorder=recorder,
        model_profile="test-model",
        prompt_profile=None,
        work_type="unit-test",
        success=True,
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.01,
        skill_id="code-gen-skill",
    )

    # Read it back
    async with factory() as session:
        result = await session.execute(
            sa.select(BenchmarkResultModel).where(
                BenchmarkResultModel.skill_id == "code-gen-skill"
            )
        )
        row = result.scalars().first()

    assert row is not None, "A benchmark row with skill_id='code-gen-skill' must be written"
    assert row.skill_id == "code-gen-skill"
    assert row.model_profile_id == "test-model"
    assert row.success is True

    await engine.dispose()


@pytest.mark.asyncio
async def test_record_job_benchmark_skill_id_defaults_none() -> None:
    """Backward-compat: omitting skill_id must produce NULL in the row."""
    engine, factory = await _setup_db()
    repo = BenchmarkRepository(session_factory=factory)

    recorder = MagicMock()
    recorder._repo = repo

    await record_job_benchmark(
        recorder=recorder,
        model_profile="model-x",
        prompt_profile=None,
        work_type="unit-test",
        success=True,
    )

    async with factory() as session:
        result = await session.execute(
            sa.select(BenchmarkResultModel).where(
                BenchmarkResultModel.model_profile_id == "model-x"
            )
        )
        row = result.scalars().first()

    assert row is not None
    assert row.skill_id is None, "skill_id must default to None when not supplied"

    await engine.dispose()


# ---------------------------------------------------------------------------
# 5-6. get_aggregate_scores groups by skill_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_aggregate_scores_groups_by_skill_id() -> None:
    """Two rows with the same (model_id, skill_id) must appear as ONE aggregate row."""
    engine, factory = await _setup_db()
    repo = BenchmarkRepository(session_factory=factory)

    async with factory() as session:
        session.add_all([
            BenchmarkResultModel(
                model_profile_id="model-a",
                task_type="qa",
                skill_id="summarise",
                success=True,
                completion_score=0.8,
                code_quality_score=0.6,
                instruction_adherence_score=0.9,
                token_efficiency_score=0.7,
            ),
            BenchmarkResultModel(
                model_profile_id="model-a",
                task_type="qa",
                skill_id="summarise",
                success=True,
                completion_score=1.0,
                code_quality_score=0.8,
                instruction_adherence_score=1.0,
                token_efficiency_score=0.9,
            ),
            BenchmarkResultModel(
                model_profile_id="model-a",
                task_type="qa",
                skill_id="translate",
                success=True,
                completion_score=0.5,
                code_quality_score=0.5,
                instruction_adherence_score=0.5,
                token_efficiency_score=0.5,
            ),
        ])
        await session.commit()

    scores = await repo.get_aggregate_scores()

    # We should have TWO aggregate rows: one for "summarise", one for "translate"
    skill_ids_seen = {r["skill_id"] for r in scores}
    assert "summarise" in skill_ids_seen
    assert "translate" in skill_ids_seen

    summarise_rows = [r for r in scores if r["skill_id"] == "summarise"]
    assert len(summarise_rows) == 1, "Two rows with same skill_id must collapse into one aggregate"
    assert summarise_rows[0]["sample_count"] == 2

    await engine.dispose()


@pytest.mark.asyncio
async def test_get_aggregate_scores_skill_id_filter() -> None:
    """Passing skill_id= to get_aggregate_scores must return only matching rows."""
    engine, factory = await _setup_db()
    repo = BenchmarkRepository(session_factory=factory)

    async with factory() as session:
        session.add_all([
            BenchmarkResultModel(
                model_profile_id="model-b",
                task_type="qa",
                skill_id="code-gen",
                success=True,
                completion_score=1.0,
                code_quality_score=1.0,
                instruction_adherence_score=1.0,
                token_efficiency_score=1.0,
            ),
            BenchmarkResultModel(
                model_profile_id="model-b",
                task_type="qa",
                skill_id="review",
                success=True,
                completion_score=0.5,
                code_quality_score=0.5,
                instruction_adherence_score=0.5,
                token_efficiency_score=0.5,
            ),
        ])
        await session.commit()

    scores = await repo.get_aggregate_scores(skill_id="code-gen")
    assert all(r["skill_id"] == "code-gen" for r in scores), (
        "skill_id filter must exclude rows with a different skill_id"
    )
    assert len(scores) >= 1

    await engine.dispose()


# ---------------------------------------------------------------------------
# 7. Alembic migration 002 upgrade + downgrade
# ---------------------------------------------------------------------------

def test_alembic_migration_002_upgrade_downgrade() -> None:
    """Migration 002 must apply (upgrade) and revert (downgrade) cleanly.

    Strategy: since migration 001 only creates the original schema tables (todos,
    queues, etc.) and benchmark_results was added outside the migration chain
    (D-11 migration drift), we bootstrap the test DB with create_all() to produce
    all current tables, then stamp it at revision "001" to simulate a production DB
    that has the tables but pre-dates migration 002. We then verify upgrade/downgrade
    of the skill_id column works correctly.

    Uses get_alembic_config() to avoid triggering fileConfig() on the minimal
    alembic.ini (which lacks [formatters]/[handlers] sections). This matches the
    pattern used elsewhere in the test suite (test_db_migrations.py).
    """
    import os
    import tempfile

    from alembic import command

    from general_ludd.db.migrations import get_alembic_config

    # Use a fresh temp SQLite file so migration runs on a blank DB
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        alembic_cfg = get_alembic_config(f"sqlite:///{db_path}")

        # Bootstrap: create all tables (including benchmark_results which is not
        # in migration 001 due to D-11 migration-drift), then stamp at "001" so
        # alembic treats this as a pre-002 DB.
        engine = sa.create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(engine)
        engine.dispose()
        command.stamp(alembic_cfg, "001")

        # Confirm skill_id is absent before upgrade (create_all uses current model
        # which has skill_id — so we remove it to simulate the pre-002 state)
        engine = sa.create_engine(f"sqlite:///{db_path}")
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE TABLE benchmark_results_old AS SELECT * FROM benchmark_results"))
            conn.execute(sa.text("DROP TABLE benchmark_results"))
            # Recreate without skill_id
            conn.execute(sa.text("""
                CREATE TABLE benchmark_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prompt_profile_id VARCHAR(64),
                    model_profile_id VARCHAR(64) NOT NULL,
                    task_type VARCHAR(64) NOT NULL,
                    task_description TEXT NOT NULL DEFAULT '',
                    completion_score FLOAT NOT NULL DEFAULT 0.0,
                    code_quality_score FLOAT NOT NULL DEFAULT 0.0,
                    instruction_adherence_score FLOAT NOT NULL DEFAULT 0.0,
                    token_efficiency_score FLOAT NOT NULL DEFAULT 0.0,
                    time_seconds FLOAT NOT NULL DEFAULT 0.0,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    cost_usd FLOAT NOT NULL DEFAULT 0.0,
                    success BOOLEAN NOT NULL DEFAULT 0,
                    error_message TEXT NOT NULL DEFAULT '',
                    raw_output TEXT NOT NULL DEFAULT '',
                    created_at DATETIME NOT NULL
                )
            """))
        engine.dispose()

        # Verify skill_id is NOT present before upgrade
        engine = sa.create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            insp = sa.inspect(conn)
            col_names_before = [c["name"] for c in insp.get_columns("benchmark_results")]
        assert "skill_id" not in col_names_before, "skill_id must not exist before upgrade to 002"
        engine.dispose()

        # Run migration 002 upgrade
        command.upgrade(alembic_cfg, "002")

        # Verify the column exists after upgrade
        engine = sa.create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            insp = sa.inspect(conn)
            col_names = [c["name"] for c in insp.get_columns("benchmark_results")]
        assert "skill_id" in col_names, "skill_id column must exist after upgrade to 002"
        engine.dispose()

        # Downgrade back to 001 — must not error
        command.downgrade(alembic_cfg, "001")

        # Verify the column is gone after downgrade
        engine = sa.create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            insp = sa.inspect(conn)
            col_names = [c["name"] for c in insp.get_columns("benchmark_results")]
        assert "skill_id" not in col_names, "skill_id column must be removed after downgrade to 001"
        engine.dispose()

    finally:
        os.unlink(db_path)
