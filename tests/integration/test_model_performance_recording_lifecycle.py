"""Lifecycle acceptance for factory-owned model-performance telemetry writes."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from general_ludd.db.models import Base, ModelCallLogModel
from general_ludd.db.repository import ModelPerformanceRepository


@pytest.mark.asyncio
async def test_factory_owned_concurrent_records_commit_and_release_sessions(
    tmp_path: Path,
) -> None:
    """Each concurrent write must own, commit, and release one async session."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'telemetry.db'}")
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    repository = ModelPerformanceRepository(session_factory=sessions)
    try:
        outcomes = await asyncio.gather(
            *(
                repository.record_call(
                    service="test-provider",
                    model_name=f"model-{index}",
                    model_profile_id=f"test/model-{index}",
                    todo_id=f"todo-{index}",
                    job_id=f"job-{index}",
                )
                for index in range(4)
            ),
            return_exceptions=True,
        )
        failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
        assert failures == []

        async with sessions() as verification_session:
            persisted = await verification_session.scalar(
                select(func.count()).select_from(ModelCallLogModel)
            )
        assert persisted == 4
        assert repository._session is None
    finally:
        cached_session = repository._session
        if cached_session is not None:
            await cached_session.close()
        await engine.dispose()
