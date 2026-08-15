"""Model-fit loop — outcome-weighted model selection and reassessment.

Proves the loop end to end without a live server:

1. The performance router selects the higher-weighted model for a job type.
2. Recording a rejected task outcome shifts the weights and the next
   selection changes to the other model.
3. Outcome recording is per job-type: failures on one job type do not
   change selection for another.

Uses the real :class:`ModelPerformanceRepository` against an in-memory
SQLite database and the real :class:`ModelPerformanceRouter`, so the
repository glue (``get_ranking`` / ``get_best_model``) is exercised rather
than mocked.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.db.repository import ModelPerformanceRepository
from general_ludd.models.performance_router import ModelPerformanceRouter

TASK_TYPE = "bug_fix"
OTHER_TASK_TYPE = "summarize"

GOOD = ("openai", "gpt-4o", "openai/gpt-4o")
BAD = ("anthropic", "claude-haiku", "anthropic/claude-haiku")


@pytest.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def repo_and_router(engine):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        repo = ModelPerformanceRepository(session=session)
        router = ModelPerformanceRouter(perf_repo=repo, config={"min_calls": 1})
        yield repo, router, session


async def _seed(
    repo: ModelPerformanceRepository,
    session: AsyncSession,
    model: tuple[str, str, str],
    task_type: str,
    *,
    successes: int,
    failures: int,
) -> None:
    service, model_name, profile_id = model
    for ok in [True] * successes + [False] * failures:
        await repo.record_call(
            service=service,
            model_name=model_name,
            model_profile_id=profile_id,
            task_type=task_type,
            success=ok,
            duration_ms=100.0,
            cost_usd=0.01,
            session=session,
        )
    await session.commit()


class TestSelectsHigherWeightedModel:
    async def test_router_picks_model_with_better_outcomes(self, repo_and_router) -> None:
        repo, router, session = repo_and_router
        await _seed(repo, session, GOOD, TASK_TYPE, successes=10, failures=0)
        await _seed(repo, session, BAD, TASK_TYPE, successes=2, failures=8)

        choice = await router.select_model(TASK_TYPE)

        assert choice["fallback"] is False
        assert choice["model_name"] == GOOD[1], f"expected {GOOD[1]}, got {choice}"

    async def test_ranking_orders_by_recorded_weight(self, repo_and_router) -> None:
        repo, router, session = repo_and_router
        await _seed(repo, session, GOOD, TASK_TYPE, successes=9, failures=1)
        await _seed(repo, session, BAD, TASK_TYPE, successes=4, failures=6)

        ranking = await router.get_rankings(TASK_TYPE)

        assert ranking[0]["model_name"] == GOOD[1]
        assert ranking[0]["success_rate"] > ranking[1]["success_rate"]


class TestRejectedOutcomeShiftsSelection:
    async def test_rejected_outcomes_flip_next_selection(self, repo_and_router) -> None:
        repo, router, session = repo_and_router
        await _seed(repo, session, GOOD, TASK_TYPE, successes=6, failures=0)
        await _seed(repo, session, BAD, TASK_TYPE, successes=4, failures=1)

        first = await router.select_model(TASK_TYPE)
        assert first["model_name"] == GOOD[1], f"expected {GOOD[1]} first, got {first}"

        await _seed(repo, session, GOOD, TASK_TYPE, successes=0, failures=5)

        second = await router.select_model(TASK_TYPE)
        assert second["model_name"] == BAD[1], f"rejected outcomes must flip selection to {BAD[1]}, got {second}"

        ranking = await router.get_rankings(TASK_TYPE)
        assert ranking[0]["model_name"] == BAD[1]


class TestOutcomeRecordingIsPerJobType:
    async def test_failures_on_one_job_type_do_not_leak_to_another(self, repo_and_router) -> None:
        repo, router, session = repo_and_router
        await _seed(repo, session, GOOD, TASK_TYPE, successes=8, failures=0)
        await _seed(repo, session, BAD, TASK_TYPE, successes=2, failures=4)
        await _seed(repo, session, GOOD, OTHER_TASK_TYPE, successes=0, failures=10)
        await _seed(repo, session, BAD, OTHER_TASK_TYPE, successes=3, failures=0)

        choice = await router.select_model(TASK_TYPE)
        assert choice["model_name"] == GOOD[1], (
            f"{GOOD[1]} rejected on {OTHER_TASK_TYPE!r} must not affect {TASK_TYPE!r} selection; got {choice}"
        )

        choice_other = await router.select_model(OTHER_TASK_TYPE)
        assert choice_other["model_name"] == BAD[1], f"expected {BAD[1]} on other, got {choice_other}"

    async def test_rejected_model_never_selected_for_touched_job_type(self, repo_and_router) -> None:
        repo, router, session = repo_and_router
        await _seed(repo, session, GOOD, TASK_TYPE, successes=0, failures=8)
        await _seed(repo, session, BAD, TASK_TYPE, successes=1, failures=1)

        choice = await router.select_model(TASK_TYPE)
        assert choice["model_name"] == BAD[1], f"expected {BAD[1]}, got {choice}"
