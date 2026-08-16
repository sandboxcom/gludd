"""Live E2E: cross-task weight-DB reuse — recorded performance drives model
selection for a never-seen task type.

Offline-safe (real SQLite model-performance DB + real router; no model
download or server). Proves the loop the user asked for: the weight DB is
updated by task performance, and that knowledge is used for OTHER tasks —
not only the task that produced it.

Runtime bounded to < 2 minutes by the pytest-timeout marker.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.db.repository import ModelPerformanceRepository
from general_ludd.models.performance_router import ModelPerformanceRouter

_GOOD_MODEL_PROFILE = "local/qwen2.5-0.5b"
_BAD_MODEL_PROFILE = "local/qwen2.5-0.5b-bad"


@pytest_asyncio.fixture
async def repo_session() -> AsyncSession:
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


async def _record(
    repo: ModelPerformanceRepository,
    model_profile_id: str,
    *,
    task_type: str,
    success: bool,
    cost_usd: float,
) -> None:
    service, _, model_name = model_profile_id.partition("/")
    await repo.record_call(
        service=service,
        model_name=model_name,
        model_profile_id=model_profile_id,
        task_type=task_type,
        success=success,
        duration_ms=100.0,
        cost_usd=cost_usd,
    )


@pytest.mark.timeout(120)
async def test_cross_task_weight_db_reuse(repo_session: AsyncSession) -> None:
    """Performance recorded for one task type must be reused to pick the
    model for a DIFFERENT, never-seen task type (no hardcoded fallback)."""
    repo = ModelPerformanceRepository(session=repo_session)
    router = ModelPerformanceRouter(perf_repo=repo, config={"min_calls": 1})

    await _record(repo, _GOOD_MODEL_PROFILE, task_type="local_factoid", success=True, cost_usd=0.05)
    await _record(repo, _BAD_MODEL_PROFILE, task_type="local_factoid", success=False, cost_usd=0.10)

    picked = await router.select_model("brand_new_task_type")
    assert picked["fallback"] is False, f"cross-task reuse must not fall back, got {picked}"
    assert picked["reason"] == "cross_task_reuse", f"unexpected selection reason: {picked}"
    assert picked["model_name"] == "qwen2.5-0.5b", (
        f"the weight DB must pick the model that performed better on other tasks, got {picked}"
    )

    global_ranking = await router.get_global_rankings(strategy="quality")
    assert global_ranking, "global rankings must be non-empty"
    assert global_ranking[0]["model_name"] == "qwen2.5-0.5b"
    assert global_ranking[0]["sample_count"] == 1


@pytest.mark.timeout(120)
async def test_cross_task_reuse_ranks_good_model_above_bad(repo_session: AsyncSession) -> None:
    """The global ranking must order models by their cross-task record."""
    repo = ModelPerformanceRepository(session=repo_session)
    router = ModelPerformanceRouter(perf_repo=repo, config={"min_calls": 1})

    await _record(repo, _GOOD_MODEL_PROFILE, task_type="game_gen", success=True, cost_usd=0.05)
    await _record(repo, _GOOD_MODEL_PROFILE, task_type="summary", success=True, cost_usd=0.05)
    await _record(repo, _BAD_MODEL_PROFILE, task_type="game_gen", success=False, cost_usd=0.10)

    ranking = await router.get_global_rankings(strategy="quality")
    assert len(ranking) == 2, f"expected 2 models in the global ranking, got {ranking}"
    assert ranking[0]["model_name"] == "qwen2.5-0.5b"
    assert ranking[0]["sample_count"] == 2
    assert ranking[1]["model_name"] == "qwen2.5-0.5b-bad"
    assert ranking[1]["success_rate"] == 0.0
    assert ranking[0]["score"] > ranking[1]["score"]
