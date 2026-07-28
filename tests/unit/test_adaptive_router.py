"""Unit tests for AdaptiveRouter (scoring/router.py)."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.schemas.benchmark import RoutingCandidate, TaskType
from general_ludd.scoring.router import AdaptiveRouter

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

PATCH_WEIGHTS = "general_ludd.scoring.router.weights_for"


def _make_agg(
    model_id: str = "model-a",
    prompt_id: str | None = "prompt-1",
    composite: float = 0.85,
    avg_cost: float = 0.01,
    sample_count: int = 5,
    task_type: str = "bug_fix",
) -> dict:
    return {
        "model_profile_id": model_id,
        "prompt_profile_id": prompt_id,
        "composite_score": composite,
        "avg_cost": avg_cost,
        "sample_count": sample_count,
        "task_type": task_type,
    }


@pytest.fixture(autouse=True)
def patch_weights():
    w = MagicMock()
    w.quality = 0.8
    w.cost = 0.2
    with patch(PATCH_WEIGHTS, return_value=w):
        yield


# ---------------------------------------------------------------------------
# _cache_key
# ---------------------------------------------------------------------------


def test_cache_key_formats_correctly():
    router = AdaptiveRouter()
    key = router._cache_key(TaskType.BUG_FIX, 0.05)
    assert key == "bug_fix:0.05"


def test_cache_key_with_none_cost():
    router = AdaptiveRouter()
    key = router._cache_key(TaskType.FEATURE, None)
    assert key == "feature:None"


# ---------------------------------------------------------------------------
# _cache_valid
# ---------------------------------------------------------------------------


def test_cache_valid_false_initially():
    router = AdaptiveRouter()
    assert router._cache_valid() is False


def test_cache_valid_true_within_ttl():
    router = AdaptiveRouter()
    router._cache_time = datetime.now()
    assert router._cache_valid() is True


def test_cache_valid_false_expired():
    router = AdaptiveRouter()
    router._cache_time = datetime.now() - timedelta(seconds=400)
    assert router._cache_valid() is False


# ---------------------------------------------------------------------------
# _exceeds_cap (static method)
# ---------------------------------------------------------------------------


def test_exceeds_cap_under_cap():
    assert AdaptiveRouter._exceeds_cap(0.03, 0.05) is False


def test_exceeds_cap_at_cap_boundary():
    # Exactly at cap — not exceeding
    assert AdaptiveRouter._exceeds_cap(0.05, 0.05) is False


def test_exceeds_cap_over_cap():
    assert AdaptiveRouter._exceeds_cap(0.10, 0.05) is True


def test_exceeds_cap_nan():
    import math

    assert AdaptiveRouter._exceeds_cap(math.nan, 0.05) is True


def test_exceeds_cap_inf():
    import math

    assert AdaptiveRouter._exceeds_cap(math.inf, 0.05) is True


# ---------------------------------------------------------------------------
# _apply_quantization_penalty
# ---------------------------------------------------------------------------


def test_quantization_penalty_no_entry():
    router = AdaptiveRouter(quantization_map={})
    candidate = RoutingCandidate(
        prompt_profile_id="p1",
        model_profile_id="model-x",
        composite_score=0.9,
        avg_cost_usd=0.01,
        sample_count=5,
        task_type=TaskType.BUG_FIX,
    )
    assert router._apply_quantization_penalty(candidate) == pytest.approx(0.9)


def test_quantization_penalty_low_confidence():
    # confidence < 0.5 → score * 0.6
    router = AdaptiveRouter(quantization_map={"model-q": ("int8", 0.4)})
    candidate = RoutingCandidate(
        prompt_profile_id=None,
        model_profile_id="model-q",
        composite_score=1.0,
        avg_cost_usd=0.0,
        sample_count=5,
        task_type=TaskType.FEATURE,
    )
    assert router._apply_quantization_penalty(candidate) == pytest.approx(0.6)


def test_quantization_penalty_mid_confidence():
    # 0.5 <= confidence < 0.7 → score * 0.8
    router = AdaptiveRouter(quantization_map={"model-q": ("int4", 0.6)})
    candidate = RoutingCandidate(
        prompt_profile_id=None,
        model_profile_id="model-q",
        composite_score=1.0,
        avg_cost_usd=0.0,
        sample_count=5,
        task_type=TaskType.FEATURE,
    )
    assert router._apply_quantization_penalty(candidate) == pytest.approx(0.8)


def test_quantization_penalty_high_confidence():
    # confidence >= 0.7 → no penalty
    router = AdaptiveRouter(quantization_map={"model-q": ("fp16", 0.9)})
    candidate = RoutingCandidate(
        prompt_profile_id=None,
        model_profile_id="model-q",
        composite_score=0.75,
        avg_cost_usd=0.0,
        sample_count=5,
        task_type=TaskType.FEATURE,
    )
    assert router._apply_quantization_penalty(candidate) == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# route() — fallback paths
# ---------------------------------------------------------------------------


async def test_route_no_repo():
    router = AdaptiveRouter(benchmark_repo=None)
    decision = await router.route(TaskType.BUG_FIX)
    assert decision.fallback is True
    assert decision.reason == "insufficient_historical_data"
    assert decision.sample_count == 0


async def test_route_empty_history():
    mock_repo = MagicMock()
    mock_repo.get_aggregate_scores = AsyncMock(return_value=[])
    router = AdaptiveRouter(benchmark_repo=mock_repo)
    decision = await router.route(TaskType.BUG_FIX)
    assert decision.fallback is True
    assert decision.reason == "insufficient_historical_data"


async def test_route_insufficient_samples():
    # sample_count below min_samples (default=3)
    agg = _make_agg(sample_count=2)
    mock_repo = MagicMock()
    mock_repo.get_aggregate_scores = AsyncMock(return_value=[agg])
    router = AdaptiveRouter(benchmark_repo=mock_repo)
    decision = await router.route(TaskType.BUG_FIX)
    assert decision.fallback is True
    assert decision.reason == "insufficient_historical_data"


# ---------------------------------------------------------------------------
# route() — happy path
# ---------------------------------------------------------------------------


async def test_route_happy_path():
    agg = _make_agg(model_id="model-a", composite=0.85, avg_cost=0.01)
    mock_repo = MagicMock()
    mock_repo.get_aggregate_scores = AsyncMock(return_value=[agg])
    router = AdaptiveRouter(benchmark_repo=mock_repo)
    decision = await router.route(TaskType.BUG_FIX)
    assert decision.fallback is False
    assert decision.reason == "best_historical_score"
    assert decision.selected_model_profile_id == "model-a"
    assert decision.selected_prompt_profile_id == "prompt-1"
    assert decision.sample_count == 5


# ---------------------------------------------------------------------------
# route() — cost-constrained paths
# ---------------------------------------------------------------------------


async def test_route_cost_constrained():
    # expensive: composite=1.0, avg_cost=0.50 → rank = 0.8*1.0 - 0.2*(0.50/0.50) = 0.60
    # cheap:     composite=0.1, avg_cost=0.01 → rank = 0.8*0.1 - 0.2*(0.01/0.50) = 0.076
    # expensive wins ranking, but exceeds max_cost_usd=0.10 → falls to cost_constrained path
    expensive = _make_agg(model_id="model-exp", composite=1.0, avg_cost=0.50)
    cheap = _make_agg(model_id="model-cheap", composite=0.1, avg_cost=0.01, prompt_id="p-cheap")
    mock_repo = MagicMock()
    # _get_best_from_history call, then _get_cheapest_for_task call
    mock_repo.get_aggregate_scores = AsyncMock(
        side_effect=[
            [expensive, cheap],  # first call: best from history → expensive wins on score
            [expensive, cheap],  # second call: cheapest within cap
        ]
    )
    router = AdaptiveRouter(benchmark_repo=mock_repo)
    # cap below expensive cost but above cheap cost
    decision = await router.route(TaskType.BUG_FIX, max_cost_usd=0.10)
    assert decision.fallback is False
    assert decision.reason == "cost_constrained"
    assert decision.selected_model_profile_id == "model-cheap"


async def test_route_cost_cap_no_fit():
    expensive = _make_agg(model_id="model-exp", composite=0.9, avg_cost=0.50)
    mock_repo = MagicMock()
    # Both history and cheapest return only the expensive candidate
    mock_repo.get_aggregate_scores = AsyncMock(
        side_effect=[
            [expensive],  # _get_best_from_history → expensive selected
            [expensive],  # _get_cheapest_for_task → filtered out (exceeds cap)
        ]
    )
    router = AdaptiveRouter(benchmark_repo=mock_repo)
    decision = await router.route(TaskType.BUG_FIX, max_cost_usd=0.05)
    assert decision.fallback is True
    assert decision.reason == "cost_cap_no_fit"


# ---------------------------------------------------------------------------
# route() — cache hit
# ---------------------------------------------------------------------------


async def test_route_cache_hit():
    agg = _make_agg()
    mock_repo = MagicMock()
    mock_repo.get_aggregate_scores = AsyncMock(return_value=[agg])
    router = AdaptiveRouter(benchmark_repo=mock_repo)

    decision1 = await router.route(TaskType.BUG_FIX)
    decision2 = await router.route(TaskType.BUG_FIX)

    # repo should only have been called once (second call hit cache)
    assert mock_repo.get_aggregate_scores.call_count == 1
    assert decision1 == decision2


# ---------------------------------------------------------------------------
# route() — unhealthy model skipped
# ---------------------------------------------------------------------------


async def test_route_unhealthy_model_skipped():
    agg_unhealthy = _make_agg(model_id="sick-model", composite=0.95, avg_cost=0.01)
    agg_healthy = _make_agg(model_id="ok-model", composite=0.70, avg_cost=0.01, prompt_id="p2")

    health_tracker = MagicMock()

    def is_healthy(model_id, admit_probe):
        return model_id != "sick-model"

    health_tracker.is_healthy.side_effect = is_healthy

    mock_repo = MagicMock()
    mock_repo.get_aggregate_scores = AsyncMock(return_value=[agg_unhealthy, agg_healthy])
    router = AdaptiveRouter(benchmark_repo=mock_repo, health_tracker=health_tracker)
    decision = await router.route(TaskType.BUG_FIX)
    assert decision.fallback is False
    assert decision.selected_model_profile_id == "ok-model"


# ---------------------------------------------------------------------------
# get_leaderboard
# ---------------------------------------------------------------------------


async def test_get_leaderboard_no_repo():
    router = AdaptiveRouter(benchmark_repo=None)
    result = await router.get_leaderboard()
    assert result == []


async def test_get_leaderboard_sorted_desc():
    agg_low = _make_agg(model_id="model-low", composite=0.50, avg_cost=0.01)
    agg_high = _make_agg(model_id="model-high", composite=0.90, avg_cost=0.02)
    agg_mid = _make_agg(model_id="model-mid", composite=0.70, avg_cost=0.01)

    mock_repo = MagicMock()
    mock_repo.get_aggregate_scores = AsyncMock(return_value=[agg_low, agg_high, agg_mid])
    router = AdaptiveRouter(benchmark_repo=mock_repo)
    results = await router.get_leaderboard(task_type=TaskType.BUG_FIX)
    scores = [r.composite_score for r in results]
    assert scores == sorted(scores, reverse=True)
    assert results[0].model_profile_id == "model-high"


# ---------------------------------------------------------------------------
# invalidate_cache
# ---------------------------------------------------------------------------


async def test_invalidate_cache_forces_refetch():
    agg = _make_agg()
    mock_repo = MagicMock()
    mock_repo.get_aggregate_scores = AsyncMock(side_effect=[[agg], [agg]])
    router = AdaptiveRouter(benchmark_repo=mock_repo)

    await router.route(TaskType.BUG_FIX)
    assert mock_repo.get_aggregate_scores.call_count == 1

    router.invalidate_cache()
    assert router._cache_time is None
    assert router._cache == {}

    await router.route(TaskType.BUG_FIX)
    assert mock_repo.get_aggregate_scores.call_count == 2
