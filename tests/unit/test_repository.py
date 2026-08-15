from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.db.repository import BenchmarkRepository, ModelPerformanceRepository


def test_benchmark_repository_equivalence():
    assert BenchmarkRepository is not None


@pytest.fixture
async def perf_engine():
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
async def perf_repo(perf_engine):
    factory = async_sessionmaker(perf_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield ModelPerformanceRepository(session=session), session


async def _record(
    repo: ModelPerformanceRepository,
    session: AsyncSession,
    service: str,
    model_name: str,
    task_type: str,
    success: bool,
    cost_usd: float,
    duration_ms: float,
) -> None:
    await repo.record_call(
        service=service,
        model_name=model_name,
        model_profile_id=f"{service}/{model_name}",
        task_type=task_type,
        success=success,
        cost_usd=cost_usd,
        duration_ms=duration_ms,
        session=session,
    )


class TestModelPerformanceRepositoryRanking:
    async def test_get_ranking_groups_by_model_for_task_type(self, perf_repo) -> None:
        repo, session = perf_repo
        for _ in range(4):
            await _record(repo, session, "openai", "gpt-4o", "bug_fix", True, 0.05, 200.0)
        await _record(repo, session, "openai", "gpt-4o", "bug_fix", False, 0.05, 200.0)
        for _ in range(2):
            await _record(repo, session, "anthropic", "claude", "bug_fix", False, 0.01, 100.0)
        await session.commit()

        ranking = await repo.get_ranking("bug_fix")

        assert len(ranking) == 2
        by_name = {r["model_name"]: r for r in ranking}
        gpt = by_name["gpt-4o"]
        assert gpt["sample_count"] == 5
        assert gpt["success_rate"] == pytest.approx(0.8, abs=1e-3)
        assert gpt["avg_cost_usd"] == pytest.approx(0.05, abs=1e-6)
        assert gpt["avg_latency_ms"] == pytest.approx(200.0, abs=1e-6)
        assert by_name["claude"]["success_rate"] == 0.0

    async def test_get_ranking_is_scoped_to_task_type(self, perf_repo) -> None:
        repo, session = perf_repo
        await _record(repo, session, "openai", "gpt-4o", "bug_fix", True, 0.05, 100.0)
        await _record(repo, session, "openai", "gpt-4o", "summarize", False, 0.05, 100.0)
        await session.commit()

        bug_fix = await repo.get_ranking("bug_fix")
        summarize = await repo.get_ranking("summarize")
        assert bug_fix[0]["success_rate"] == 1.0
        assert summarize[0]["success_rate"] == 0.0

    async def test_get_best_model_respects_min_calls(self, perf_repo) -> None:
        repo, session = perf_repo
        await _record(repo, session, "openai", "gpt-4o", "bug_fix", True, 0.05, 100.0)
        await session.commit()

        assert await repo.get_best_model("bug_fix", min_calls=2) is None
        best = await repo.get_best_model("bug_fix", min_calls=1)
        assert best is not None
        assert best["model_name"] == "gpt-4o"
        assert best["composite_score"] == 1.0

    async def test_get_best_model_prefer_cost_picks_cheaper(self, perf_repo) -> None:
        repo, session = perf_repo
        for _ in range(3):
            await _record(repo, session, "openai", "gpt-4o", "bug_fix", True, 0.10, 100.0)
        for _ in range(3):
            await _record(repo, session, "groq", "llama", "bug_fix", False, 0.001, 100.0)
        await session.commit()

        best = await repo.get_best_model("bug_fix", min_calls=3, prefer_cost=True)
        assert best is not None
        assert best["model_name"] == "llama", f"expected cheapest model, got {best}"

    async def test_get_summary_filters_by_service_and_task_type(self, perf_repo) -> None:
        repo, session = perf_repo
        await _record(repo, session, "openai", "gpt-4o", "bug_fix", True, 0.05, 100.0)
        await _record(repo, session, "openai", "gpt-4o", "bug_fix", False, 0.05, 100.0)
        await _record(repo, session, "anthropic", "claude", "summarize", True, 0.02, 50.0)
        await session.commit()

        all_rows = await repo.get_summary()
        assert len(all_rows) == 2

        openai_rows = await repo.get_summary(service="openai")
        assert len(openai_rows) == 1
        assert openai_rows[0]["total_calls"] == 2
        assert openai_rows[0]["successful_calls"] == 1
        assert openai_rows[0]["failed_calls"] == 1
        assert openai_rows[0]["success_rate"] == 0.5

        summarize_rows = await repo.get_summary(task_type="summarize")
        assert len(summarize_rows) == 1
        assert summarize_rows[0]["model_name"] == "claude"
