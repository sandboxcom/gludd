"""Lifecycle acceptance for factory-owned model-performance reads and refresh."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from general_ludd.db.models import Base, ModelPerformanceModel
from general_ludd.db.repository import ModelPerformanceRepository


@pytest.mark.asyncio
async def test_factory_owned_refresh_and_concurrent_queries_commit_and_release_sessions(
    tmp_path: Path,
) -> None:
    """Refresh commits and each concurrent query releases its factory session."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'performance.db'}")
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    repository = ModelPerformanceRepository(session_factory=sessions)
    try:
        for index in range(4):
            await repository.record_call(
                service="test-provider",
                model_name=f"model-{index}",
                model_profile_id=f"test/model-{index}",
                task_type="generation",
                success=index != 3,
                cost_usd=0.01 * (index + 1),
                duration_ms=10.0 * (index + 1),
            )

        assert await repository.refresh_recent_stats() == 4

        outcomes = await asyncio.gather(
            repository.get_summary(),
            repository.get_ranking("generation"),
            repository.get_recent_calls(),
            repository.get_stats_by_model(),
            repository.get_stats_by_service(),
            repository.get_daily_stats(),
            repository.get_best_model("generation", min_calls=1),
            return_exceptions=True,
        )
        failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
        assert failures == []
        assert all(outcome for outcome in outcomes)

        async with sessions() as verification_session:
            persisted_profiles = await verification_session.scalar(
                select(func.count()).select_from(ModelPerformanceModel)
            )
        assert persisted_profiles == 4
        assert repository._session is None

        assert isinstance(engine.sync_engine.pool, AsyncAdaptedQueuePool)
        assert engine.sync_engine.pool.checkedout() == 0
    finally:
        cached_session = repository._session
        if cached_session is not None:
            await cached_session.close()
        await engine.dispose()
