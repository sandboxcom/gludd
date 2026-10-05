"""Unit tests pinning the model fit loop.

The loop: outcomes are recorded per (job type, model) into the
``model_call_logs`` table, ``ModelPerformanceRouter`` selects the best model
for a job type from that data, and a rejected outcome for the current pick
changes the next selection.  These tests use the real
``ModelPerformanceRepository`` on an in-memory SQLite DB — no mocks.
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.db.repository import ModelPerformanceRepository
from general_ludd.models.performance_router import ModelPerformanceRouter

_TASK = "bug_fix"
_GOOD = "openai/gpt-4o"
_REJECTED = "anthropic/claude-haiku"


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


def _router(repo: ModelPerformanceRepository) -> ModelPerformanceRouter:
    return ModelPerformanceRouter(perf_repo=repo, config={"min_calls": 1})


async def _record(
    repo: ModelPerformanceRepository,
    model_profile_id: str,
    *,
    task_type: str = _TASK,
    success: bool,
    count: int = 1,
    cost_usd: float = 0.01,
) -> None:
    service, _, model_name = model_profile_id.partition("/")
    for _ in range(count):
        await repo.record_call(
            service=service,
            model_name=model_name,
            model_profile_id=model_profile_id,
            task_type=task_type,
            success=success,
            duration_ms=100.0,
            cost_usd=cost_usd,
        )


class TestOutcomeWeightedSelection:
    async def test_good_outcome_beats_rejected_outcome(self, session: AsyncSession) -> None:
        repo = ModelPerformanceRepository(session=session)
        await _record(repo, _GOOD, success=True)
        await _record(repo, _REJECTED, success=False)

        picked = await _router(repo).select_model(_TASK)

        assert picked["model_name"] == "gpt-4o"
        assert picked["service"] == "openai"
        assert picked["fallback"] is False
        assert picked["reason"] == "historical_best"

    async def test_rejected_outcome_flips_the_pick(self, session: AsyncSession) -> None:
        repo = ModelPerformanceRepository(session=session)
        await _record(repo, _GOOD, success=True, count=3)
        await _record(repo, _REJECTED, success=True, count=2)
        await _record(repo, _REJECTED, success=False)

        first = await _router(repo).select_model(_TASK)
        assert first["model_name"] == "gpt-4o"

        await _record(repo, _GOOD, success=False, count=3)

        second = await _router(repo).select_model(_TASK)
        assert second["model_name"] == "claude-haiku"
        assert second["service"] == "anthropic"

    async def test_outcomes_are_keyed_per_job_type(self, session: AsyncSession) -> None:
        repo = ModelPerformanceRepository(session=session)
        await _record(repo, _GOOD, task_type="bug_fix", success=True, count=3)
        await _record(repo, _REJECTED, task_type="bug_fix", success=False, count=3)
        await _record(repo, _GOOD, task_type="feature", success=False, count=3)
        await _record(repo, _REJECTED, task_type="feature", success=True, count=3)

        bug_fix = await _router(repo).select_model("bug_fix")
        feature = await _router(repo).select_model("feature")

        assert bug_fix["model_name"] == "gpt-4o"
        assert feature["model_name"] == "claude-haiku"

    async def test_reassessment_reflects_latest_outcomes(self, session: AsyncSession) -> None:
        repo = ModelPerformanceRepository(session=session)
        await _record(repo, _GOOD, success=True, count=3, cost_usd=0.001)
        await _record(repo, _REJECTED, success=True, count=3, cost_usd=0.01)

        picked = await _router(repo).select_model(_TASK)
        assert picked["model_name"] == "gpt-4o"

        await _record(repo, _GOOD, success=False, count=2)

        ranking = await _router(repo).get_rankings(_TASK, strategy="quality")
        assert ranking[0]["model_name"] == "claude-haiku"
        assert ranking[1]["model_name"] == "gpt-4o"
