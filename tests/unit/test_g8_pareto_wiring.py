"""Unit tests for G8: ParetoRouter wiring into AdaptiveRouter and daemon."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.schemas.benchmark import TaskType
from general_ludd.scoring.pareto import ParetoRouter
from general_ludd.scoring.router import AdaptiveRouter

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

PATCH_WEIGHTS = "general_ludd.scoring.router.weights_for"


def _make_agg(
    model_id: str = "model-a",
    composite: float = 0.85,
    avg_cost: float = 0.01,
    sample_count: int = 5,
    task_type: str = "bug_fix",
) -> dict[str, Any]:
    return {
        "model_profile_id": model_id,
        "prompt_profile_id": "default",
        "composite_score": composite,
        "avg_cost": avg_cost,
        "sample_count": sample_count,
        "task_type": task_type,
    }


@pytest.fixture(autouse=True)
def _patch_weights() -> Any:
    w = MagicMock()
    w.quality = 0.8
    w.cost = 0.2
    with patch(PATCH_WEIGHTS, return_value=w):
        yield


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestParetoRouterConstruction:
    def test_default_weights_from_g8(self) -> None:
        router = ParetoRouter(cost_weight=0.15, quality_weight=0.85)
        assert router._cost_weight == 0.15
        assert router._quality_weight == 0.85

    def test_pareto_router_default_weights(self) -> None:
        router = ParetoRouter()
        assert router._cost_weight == 0.5
        assert router._quality_weight == 0.5


# ---------------------------------------------------------------------------
# Pareto filter — AdaptiveRouter integration
# ---------------------------------------------------------------------------


class TestParetoFilterInAdaptiveRouter:
    @pytest.mark.asyncio
    async def test_dominated_candidate_excluded_by_pareto(self) -> None:
        repo = AsyncMock()
        repo.get_aggregate_scores.return_value = [
            _make_agg("cheap_best", composite=0.95, avg_cost=0.01),
            _make_agg("expensive_bad", composite=0.60, avg_cost=0.10),
        ]
        pareto = ParetoRouter()
        router = AdaptiveRouter(
            benchmark_repo=repo,
            min_samples=1,
            pareto_router=pareto,
        )
        result = await router.route(TaskType("bug_fix"))
        assert not result.fallback
        assert result.selected_model_profile_id == "cheap_best"

    @pytest.mark.asyncio
    async def test_no_pareto_router_passes_all_candidates(self) -> None:
        repo = AsyncMock()
        repo.get_aggregate_scores.return_value = [
            _make_agg("cheap_best", composite=0.95, avg_cost=0.01),
            _make_agg("expensive_bad", composite=0.60, avg_cost=0.10),
        ]
        router = AdaptiveRouter(
            benchmark_repo=repo,
            min_samples=1,
            pareto_router=None,
        )
        result = await router.route(TaskType("bug_fix"))
        assert not result.fallback
        assert result.selected_model_profile_id == "cheap_best"

    @pytest.mark.asyncio
    async def test_cheaper_equivalent_after_pareto_filter(self) -> None:
        repo = AsyncMock()
        repo.get_aggregate_scores.return_value = [
            _make_agg("cheap_highq", composite=0.90, avg_cost=0.01),
            _make_agg("mid", composite=0.85, avg_cost=0.02),
            _make_agg("expensive_lowq", composite=0.50, avg_cost=0.10),
        ]
        pareto = ParetoRouter()
        router = AdaptiveRouter(
            benchmark_repo=repo,
            min_samples=1,
            pareto_router=pareto,
            adequacy_margin=0.05,
        )
        result = await router.route(TaskType("bug_fix"))
        assert not result.fallback
        assert result.selected_model_profile_id == "cheap_highq"

    @pytest.mark.asyncio
    async def test_all_frontier_candidates_no_domination(self) -> None:
        repo = AsyncMock()
        repo.get_aggregate_scores.return_value = [
            _make_agg("cheap_lowq", composite=0.70, avg_cost=0.01),
            _make_agg("expensive_highq", composite=0.99, avg_cost=0.10),
        ]
        pareto = ParetoRouter()
        router = AdaptiveRouter(
            benchmark_repo=repo,
            min_samples=1,
            pareto_router=pareto,
        )
        result = await router.route(TaskType("bug_fix"))
        assert not result.fallback
        assert result.selected_model_profile_id == "expensive_highq"

    @pytest.mark.asyncio
    async def test_embeddings_path_still_uses_pareto_filter(self) -> None:
        repo = AsyncMock()
        repo.get_aggregate_scores.return_value = [
            _make_agg("best", composite=0.90, avg_cost=0.01, task_type="bug_fix"),
            _make_agg("dominated", composite=0.50, avg_cost=0.10, task_type="bug_fix"),
        ]
        emb_store = MagicMock()
        emb_store.similarity_to = AsyncMock(
            return_value={"code_generation": 0.8}
        )
        pareto = ParetoRouter()
        router = AdaptiveRouter(
            benchmark_repo=repo,
            min_samples=1,
            pareto_router=pareto,
            embedding_store=emb_store,
        )
        result = await router.route(TaskType("bug_fix"))
        assert not result.fallback
        assert result.selected_model_profile_id == "best"


# ---------------------------------------------------------------------------
# Daemon wiring
# ---------------------------------------------------------------------------


class TestDaemonParetoWiring:
    def test_adaptive_router_constructed_with_pareto_router(self) -> None:
        pareto = ParetoRouter(cost_weight=0.15, quality_weight=0.85)

        repo = AsyncMock()
        repo.get_aggregate_scores.return_value = [
            _make_agg("model_a", avg_cost=0.01, composite=0.95),
            _make_agg("model_b", avg_cost=0.05, composite=0.70),
        ]

        router = AdaptiveRouter(
            benchmark_repo=repo,
            min_samples=1,
            pareto_router=pareto,
        )

        assert router._pareto_router is not None
        assert isinstance(router._pareto_router, ParetoRouter)
        assert router._pareto_router._cost_weight == 0.15
        assert router._pareto_router._quality_weight == 0.85

    @pytest.mark.asyncio
    async def test_daemon_construction_uses_pareto_in_route(self) -> None:
        pareto = ParetoRouter(cost_weight=0.15, quality_weight=0.85)
        repo = AsyncMock()
        repo.get_aggregate_scores.return_value = [
            _make_agg("cheap", avg_cost=0.01, composite=0.95),
            _make_agg("expensive", avg_cost=0.10, composite=0.50),
        ]

        router = AdaptiveRouter(
            benchmark_repo=repo,
            min_samples=1,
            pareto_router=pareto,
        )

        result = await router.route(TaskType("bug_fix"))
        assert not result.fallback
        assert result.selected_model_profile_id == "cheap"
