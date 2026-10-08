"""Lifecycle acceptance for factory-owned benchmark writes and queries."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from general_ludd.db.models import Base, BenchmarkResultModel
from general_ludd.db.repository import BenchmarkRepository


def _benchmark_data(index: int) -> dict[str, object]:
    """Return one complete benchmark row for lifecycle acceptance."""
    return {
        "prompt_profile_id": None,
        "model_profile_id": "model-a",
        "task_type": "generation",
        "task_description": f"benchmark-{index}",
        "completion_score": 0.8,
        "code_quality_score": 0.7,
        "instruction_adherence_score": 0.9,
        "token_efficiency_score": 0.6,
        "cost_usd": 0.01 * (index + 1),
        "success": True,
    }


@pytest.mark.asyncio
async def test_default_expiration_concurrent_queries_release_factory_sessions(
    tmp_path: Path,
) -> None:
    """Factory reads return usable rows without sharing or retaining sessions."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'benchmarks.db'}")
    sessions = async_sessionmaker(engine, class_=AsyncSession)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    repository = BenchmarkRepository(session_factory=sessions)
    try:
        for index in range(4):
            await repository.record_result(_benchmark_data(index))

        model_rows, recent_rows, aggregate_rows, best_rows = await asyncio.gather(
            repository.get_model_scores("model-a"),
            repository.list_recent(limit=2),
            repository.get_aggregate_scores(task_type="generation"),
            repository.get_best_for_task("generation", min_samples=1),
        )

        # Access attributes only after every factory-owned query context exited.
        # With default expire_on_commit=True, committing a read transaction here
        # would expire the rows before the session closes and make this access
        # raise DetachedInstanceError.
        assert [row.task_description for row in model_rows] == [
            "benchmark-3",
            "benchmark-2",
            "benchmark-1",
            "benchmark-0",
        ]
        assert [row.task_description for row in recent_rows] == [
            "benchmark-3",
            "benchmark-2",
        ]
        assert aggregate_rows[0]["sample_count"] == 4
        assert best_rows[0]["model_profile_id"] == "model-a"

        async with sessions() as verification_session:
            persisted = await verification_session.scalar(
                select(func.count()).select_from(BenchmarkResultModel)
            )
        assert persisted == 4
        assert repository._session is None
        assert isinstance(engine.sync_engine.pool, AsyncAdaptedQueuePool)
        assert engine.sync_engine.pool.checkedout() == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_factory_write_rollback_does_not_poison_the_next_operation(
    tmp_path: Path,
) -> None:
    """A failed factory transaction rolls back, closes, and permits recovery."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rollback.db'}")
    sessions = async_sessionmaker(engine, class_=AsyncSession)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    repository = BenchmarkRepository(session_factory=sessions)
    try:
        await repository.record_result({**_benchmark_data(0), "id": 41})
        with pytest.raises(IntegrityError):
            await repository.record_result({**_benchmark_data(1), "id": 41})
        recovered = await repository.record_result({**_benchmark_data(2), "id": 42})

        assert recovered.id == 42
        async with sessions() as verification_session:
            persisted = await verification_session.scalar(
                select(func.count()).select_from(BenchmarkResultModel)
            )
        assert persisted == 2
        assert isinstance(engine.sync_engine.pool, AsyncAdaptedQueuePool)
        assert engine.sync_engine.pool.checkedout() == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_caller_owned_session_retains_transaction_control(tmp_path: Path) -> None:
    """Repository calls neither commit nor close an explicitly supplied session."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'caller-owned.db'}")
    sessions = async_sessionmaker(engine, class_=AsyncSession)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        async with sessions() as caller_session:
            repository = BenchmarkRepository(session=caller_session)
            created = await repository.record_result(_benchmark_data(0))
            listed = await repository.list_recent(limit=1)

            assert listed == [created]
            assert caller_session.in_transaction()
            await caller_session.rollback()
            assert caller_session.is_active

        async with sessions() as verification_session:
            persisted = await verification_session.scalar(
                select(func.count()).select_from(BenchmarkResultModel)
            )
        assert persisted == 0
    finally:
        await engine.dispose()
