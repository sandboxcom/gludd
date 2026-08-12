"""Regression tests for the benchmark skill dimension (P3 reconciliation)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, BenchmarkResultModel
from general_ludd.db.repository import BenchmarkRepository
from general_ludd.event_loop.benchmark import record_job_benchmark


@pytest.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


def _benchmark_data(*, skill_id: str | None, task_role: str | None = None) -> dict[str, object]:
    return {
        "prompt_profile_id": None,
        "model_profile_id": "model-a",
        "task_type": "qa",
        "skill_id": skill_id,
        "task_role": task_role,
        "completion_score": 0.8,
        "code_quality_score": 0.7,
        "instruction_adherence_score": 0.9,
        "token_efficiency_score": 0.6,
        "success": True,
    }


def test_benchmark_result_model_declares_nullable_indexed_skill_dimension() -> None:
    column = BenchmarkResultModel.__table__.c.skill_id
    assert column.nullable is True
    assert column.type.length == 128
    assert {
        "ix_benchmark_results_skill_id",
        "ix_benchmark_skill_model",
    }.issubset({index.name for index in BenchmarkResultModel.__table__.indexes})


async def test_record_job_benchmark_persists_skill_id(session_factory) -> None:
    repository = BenchmarkRepository(session_factory=session_factory)
    recorder = MagicMock(_repo=repository)

    await record_job_benchmark(
        recorder=recorder,
        model_profile="model-a",
        prompt_profile=None,
        work_type="qa",
        success=True,
        skill_id="summarise",
    )

    async with session_factory() as session:
        row = (await session.execute(
            BenchmarkResultModel.__table__.select()
        )).mappings().one()
    assert row["skill_id"] == "summarise"


async def test_record_job_benchmark_remains_compatible_when_skill_is_unknown(
    session_factory,
) -> None:
    repository = BenchmarkRepository(session_factory=session_factory)
    recorder = MagicMock(_repo=repository)

    await record_job_benchmark(
        recorder=recorder,
        model_profile="legacy-model",
        prompt_profile=None,
        work_type="qa",
        success=True,
    )

    async with session_factory() as session:
        row = (await session.execute(
            BenchmarkResultModel.__table__.select()
        )).mappings().one()
    assert row["skill_id"] is None


async def test_aggregate_scores_group_and_filter_by_skill_without_losing_role_axis(
    session_factory,
) -> None:
    repository = BenchmarkRepository(session_factory=session_factory)
    await repository.record_result(_benchmark_data(skill_id="summarise", task_role="coder"))
    await repository.record_result(_benchmark_data(skill_id="summarise", task_role="coder"))
    await repository.record_result(_benchmark_data(skill_id="translate", task_role="coder"))
    await repository.record_result(_benchmark_data(skill_id="summarise", task_role="reviewer"))

    scores = await repository.get_aggregate_scores(task_type="qa", skill_id="summarise")

    by_role = {row["task_role"]: row for row in scores}
    assert set(by_role) == {"coder", "reviewer"}
    assert by_role["coder"]["skill_id"] == "summarise"
    assert by_role["coder"]["sample_count"] == 2
    assert by_role["reviewer"]["sample_count"] == 1


def test_migration_041_round_trips_skill_column_and_indexes(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "skill-dimension.db"
    config = AlembicConfig()
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    command.upgrade(config, "040")
    engine = create_engine(f"sqlite:///{db_path}")
    assert "skill_id" not in {column["name"] for column in inspect(engine).get_columns("benchmark_results")}

    command.upgrade(config, "041")
    inspector = inspect(engine)
    assert "skill_id" in {column["name"] for column in inspector.get_columns("benchmark_results")}
    assert {
        "ix_benchmark_results_skill_id",
        "ix_benchmark_skill_model",
    }.issubset({index["name"] for index in inspector.get_indexes("benchmark_results")})

    command.downgrade(config, "040")
    assert "skill_id" not in {column["name"] for column in inspect(engine).get_columns("benchmark_results")}
    engine.dispose()
