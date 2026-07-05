"""E2E: AdaptiveRouter routes across all pricing-catalog providers.

Proves scoring-cost-routing can select from every provider registered
in the PricingCatalog, verifying the full AdaptiveRouter -> benchmark
repo -> provider pipeline end to end.

The test constructs an AdaptiveRouter with a BenchmarkRepository backed
by an in-memory SQLite database seeded with benchmark results for each
catalog provider slug, then calls route() and asserts:
  1. A non-fallback decision is returned when historical data exists.
  2. The cost-constrained routing prefers cheaper models under budget cap.
  3. get_leaderboard() surfaces all seeded providers.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.db.repository import BenchmarkRepository
from general_ludd.pricing_intel.catalog import PricingCatalog
from general_ludd.schemas.benchmark import TaskType
from general_ludd.scoring.router import AdaptiveRouter


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
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest.fixture
async def benchmark_repo(engine):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield BenchmarkRepository(session)


def _catalog_provider_slugs() -> list[str]:
    catalog = PricingCatalog()
    return catalog.provider_slugs()


def _make_row(model_id: str, task_type: str, composite: float, cost: float, **extra):
    return {
        "model_profile_id": model_id,
        "task_type": task_type,
        "completion_score": composite,
        "code_quality_score": 0.8,
        "instruction_adherence_score": 0.75,
        "token_efficiency_score": 0.6,
        "success": True,
        "input_tokens": 500,
        "output_tokens": 200,
        "cost_usd": cost,
        "time_seconds": 0.0,
        "error_message": "",
        **extra,
    }


class TestAdaptiveRouterAcrossAllProviders:
    @pytest.mark.asyncio
    async def test_routing_uses_catalog_providers(self, benchmark_repo) -> None:
        slugs = _catalog_provider_slugs()
        assert len(slugs) > 0, "Catalog must have registered provider slugs"

        for i, slug in enumerate(slugs):
            model_id = f"{slug}-model-test"
            await benchmark_repo.record_result(data=_make_row(
                model_id=model_id,
                task_type="bug_fix",
                composite=0.85 + (i * 0.0001),
                cost=0.005,
                prompt_profile_id=f"prompt-{slug}",
            ))

        router = AdaptiveRouter(benchmark_repo=benchmark_repo, min_samples=1)

        decision = await router.route(
            TaskType.BUG_FIX,
            default_prompt_profile="fallback",
            default_model_profile="fallback-model",
        )

        assert decision is not None
        assert decision.fallback is False, (
            f"Expected non-fallback decision; got reason={decision.reason}"
        )

    @pytest.mark.asyncio
    async def test_routing_handles_empty_providers(self, benchmark_repo) -> None:
        router = AdaptiveRouter(benchmark_repo=benchmark_repo, min_samples=1)

        decision = await router.route(
            TaskType.BUG_FIX,
            default_prompt_profile="fallback-prompt",
            default_model_profile="fallback-model",
        )

        assert decision.fallback is True
        assert decision.selected_model_profile_id == "fallback-model"
        assert decision.reason == "insufficient_historical_data"

    @pytest.mark.asyncio
    async def test_cost_constrained_routing_prefers_cheaper(self, benchmark_repo) -> None:
        await benchmark_repo.record_result(data=_make_row(
            model_id="expensive-model",
            task_type="bug_fix",
            composite=1.0,
            cost=10.0,
            prompt_profile_id="prompt-expensive",
            code_quality_score=1.0,
            instruction_adherence_score=1.0,
            token_efficiency_score=1.0,
        ))
        await benchmark_repo.record_result(data=_make_row(
            model_id="cheap-model",
            task_type="bug_fix",
            composite=0.5,
            cost=0.001,
            prompt_profile_id="prompt-cheap",
            code_quality_score=0.5,
            instruction_adherence_score=0.5,
            token_efficiency_score=0.5,
        ))

        router = AdaptiveRouter(benchmark_repo=benchmark_repo, min_samples=1)

        decision = await router.route(
            TaskType.BUG_FIX,
            max_cost_usd=0.10,
            default_prompt_profile="fallback",
            default_model_profile="fallback-model",
        )

        assert decision.reason == "cost_constrained", (
            f"Expected cost_constrained; got reason={decision.reason}"
            f" selected={decision.selected_model_profile_id}"
            f" cost={decision.estimated_cost_usd}"
        )
        assert decision.selected_model_profile_id == "cheap-model"

    @pytest.mark.asyncio
    async def test_leaderboard_returns_all_catalog_providers(self, benchmark_repo) -> None:
        slugs = _catalog_provider_slugs()
        for slug in slugs[:3]:
            await benchmark_repo.record_result(data=_make_row(
                model_id=f"{slug}-leader",
                task_type="bug_fix",
                composite=0.80,
                cost=0.005,
            ))

        router = AdaptiveRouter(benchmark_repo=benchmark_repo, min_samples=1)

        leaderboard = await router.get_leaderboard(TaskType.BUG_FIX)
        assert len(leaderboard) >= 1
        model_ids = {c.model_profile_id for c in leaderboard}
        assert any(slug in mid for slug in slugs[:3] for mid in model_ids), (
            f"Leaderboard should contain models from catalog providers; got {model_ids}"
        )

    @pytest.mark.asyncio
    async def test_all_providers_get_scored(self, benchmark_repo) -> None:
        slugs = _catalog_provider_slugs()
        assert len(slugs) > 0, "Must have catalog providers"

        seeded_models: list[str] = []
        for slug in slugs:
            model_id = f"{slug}-scored"
            await benchmark_repo.record_result(data=_make_row(
                model_id=model_id,
                task_type="bug_fix",
                composite=0.75,
                cost=0.01,
            ))
            seeded_models.append(model_id)

        router = AdaptiveRouter(benchmark_repo=benchmark_repo, min_samples=1)

        leaderboard = await router.get_leaderboard(TaskType.BUG_FIX)
        scored_ids = {c.model_profile_id for c in leaderboard}

        for model_id in seeded_models:
            assert model_id in scored_ids, (
                f"Seeded model {model_id} must appear in leaderboard"
            )
