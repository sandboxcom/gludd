"""Branch-coverage tests for the adaptive router (scoring subsystem).

Targets router branches not exercised by tests/unit/test_scoring.py:
- _get_best_from_history: min_samples filter (candidate below floor excluded ->
  fallback insufficient_historical_data) and health_tracker filter (unhealthy
  model excluded; is_healthy called with admit_probe=False).
- _get_cheapest_for_task: health_tracker filter (unhealthy model excluded;
  is_healthy called with admit_probe=False).
- _apply_quantization_penalty: mid-confidence [0.5, 0.7) band -> score * 0.8.
- _cost_adjusted_rank: max_cost == 0 and non-finite cost -> cost_norm = 0.0.
- get_leaderboard: task_type=None path.
- route(): best_historical_score happy path with max_cost_usd=None.

Also pins the CURRENT (no-op cache) behavior: route() never reads/writes
self._cache even though invalidate_cache() exists (CA-T6 dead-cache bug).
"""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.schemas.benchmark import RoutingCandidate, TaskType
from general_ludd.scoring.router import AdaptiveRouter


def _agg(
    *,
    prompt_profile_id: str = "pp-x",
    model_profile_id: str = "model-x",
    task_type: str = "bug_fix",
    sample_count: int = 10,
    avg_cost: float = 0.01,
    composite_score: float = 0.9,
) -> dict:
    """Build an aggregate-scores row in the shape the repo returns."""
    return {
        "prompt_profile_id": prompt_profile_id,
        "model_profile_id": model_profile_id,
        "task_type": task_type,
        "sample_count": sample_count,
        "avg_cost": avg_cost,
        "composite_score": composite_score,
    }


def _repo(rows: list[dict]) -> AsyncMock:
    repo = AsyncMock()
    repo.get_aggregate_scores = AsyncMock(side_effect=lambda task_type=None: list(rows))
    return repo


class TestMinSamplesFilter:
    @pytest.mark.asyncio
    async def test_below_min_samples_excluded_falls_back(self):
        # Single candidate is below the min_samples floor -> no candidates ->
        # route() returns the insufficient_historical_data fallback.
        repo = _repo([_agg(sample_count=2)])
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3)
        decision = await router.route(
            task_type=TaskType.BUG_FIX,
            default_prompt_profile="dflt-prompt",
            default_model_profile="dflt-model",
        )
        assert decision.fallback is True
        assert decision.reason == "insufficient_historical_data"
        assert decision.selected_model_profile_id == "dflt-model"
        assert decision.selected_prompt_profile_id == "dflt-prompt"
        assert decision.composite_score == 0.0
        assert decision.sample_count == 0


class TestHealthTrackerFilter:
    @pytest.mark.asyncio
    async def test_unhealthy_model_excluded_from_best(self):
        # Only candidate is unhealthy -> filtered out -> fallback.
        repo = _repo([_agg(model_profile_id="sick-model", sample_count=10)])
        tracker = MagicMock()
        tracker.is_healthy = MagicMock(return_value=False)
        router = AdaptiveRouter(
            benchmark_repo=repo, min_samples=3, health_tracker=tracker
        )
        decision = await router.route(
            task_type=TaskType.BUG_FIX, default_model_profile="dflt-model"
        )
        assert decision.fallback is True
        assert decision.reason == "insufficient_historical_data"
        # The status read must use admit_probe=False (don't burn the probe slot).
        tracker.is_healthy.assert_called_with("sick-model", admit_probe=False)

    @pytest.mark.asyncio
    async def test_healthy_model_retained_in_best(self):
        repo = _repo([_agg(model_profile_id="ok-model", composite_score=0.8)])
        tracker = MagicMock()
        tracker.is_healthy = MagicMock(return_value=True)
        router = AdaptiveRouter(
            benchmark_repo=repo, min_samples=3, health_tracker=tracker
        )
        decision = await router.route(task_type=TaskType.BUG_FIX)
        assert decision.fallback is False
        assert decision.selected_model_profile_id == "ok-model"
        tracker.is_healthy.assert_called_with("ok-model", admit_probe=False)

    @pytest.mark.asyncio
    async def test_unhealthy_model_excluded_from_cheapest(self):
        # Best is over cap -> router consults _get_cheapest_for_task; the only
        # under-cap candidate is unhealthy -> filtered -> fail-closed fallback.
        repo = _repo(
            [
                _agg(
                    prompt_profile_id="pp-expensive",
                    model_profile_id="gpt4",
                    avg_cost=0.10,
                    composite_score=0.9,
                ),
                _agg(
                    prompt_profile_id="pp-cheap",
                    model_profile_id="sick-cheap",
                    avg_cost=0.001,
                    composite_score=0.7,
                ),
            ]
        )

        # gpt4 healthy (so it becomes "best", over cap); sick-cheap unhealthy.
        def _health(model_id: str, admit_probe: bool = True) -> bool:
            return model_id != "sick-cheap"

        tracker = MagicMock()
        tracker.is_healthy = MagicMock(side_effect=_health)
        router = AdaptiveRouter(
            benchmark_repo=repo, min_samples=3, health_tracker=tracker
        )
        decision = await router.route(
            task_type=TaskType.BUG_FIX,
            default_model_profile="safe-default",
            max_cost_usd=0.01,
        )
        # No healthy under-cap candidate -> fail closed to default.
        assert decision.fallback is True
        assert decision.selected_model_profile_id == "safe-default"
        assert decision.reason == "cost_cap_no_fit"
        # is_healthy was invoked with admit_probe=False during cheapest filtering.
        tracker.is_healthy.assert_any_call("sick-cheap", admit_probe=False)


class TestQuantizationMidConfidenceBand:
    def test_mid_confidence_band_applies_0_8(self):
        # confidence in [0.5, 0.7) -> score * 0.8 (the band untouched by the
        # existing <0.5 -> *0.6 test in test_scoring.py).
        router = AdaptiveRouter(quantization_map={"mid-model": ("int8", 0.6)})
        candidate = RoutingCandidate(
            prompt_profile_id="pp",
            model_profile_id="mid-model",
            composite_score=0.9,
            avg_cost_usd=0.01,
            sample_count=5,
            task_type=TaskType.BUG_FIX,
        )
        penalized = router._apply_quantization_penalty(candidate)
        assert penalized == pytest.approx(0.9 * 0.8)

    def test_confidence_at_0_7_boundary_no_penalty(self):
        # 0.7 is NOT < 0.7 -> no penalty (boundary check).
        router = AdaptiveRouter(quantization_map={"hi-model": ("bf16", 0.7)})
        candidate = RoutingCandidate(
            prompt_profile_id="pp",
            model_profile_id="hi-model",
            composite_score=0.9,
            avg_cost_usd=0.01,
            sample_count=5,
            task_type=TaskType.BUG_FIX,
        )
        assert router._apply_quantization_penalty(candidate) == pytest.approx(0.9)

    def test_model_not_in_map_unpenalized(self):
        router = AdaptiveRouter(quantization_map={})
        candidate = RoutingCandidate(
            prompt_profile_id="pp",
            model_profile_id="unknown",
            composite_score=0.9,
            avg_cost_usd=0.01,
            sample_count=5,
            task_type=TaskType.BUG_FIX,
        )
        assert router._apply_quantization_penalty(candidate) == pytest.approx(0.9)


class TestCostAdjustedRank:
    def _candidate(self, cost: float) -> RoutingCandidate:
        return RoutingCandidate(
            prompt_profile_id="pp",
            model_profile_id="m",
            composite_score=0.9,
            avg_cost_usd=cost,
            sample_count=5,
            task_type=TaskType.FEATURE,
        )

    def test_max_cost_zero_gives_zero_cost_norm(self):
        # max_cost == 0 -> cost_norm = 0.0 -> rank == quality * quality_weight.
        candidate = self._candidate(cost=0.0)
        rank = AdaptiveRouter._cost_adjusted_rank(candidate, quality=0.9, max_cost=0.0)
        # FEATURE weights: cost=0.20, quality=0.80 -> 0.80 * 0.9 - 0 == 0.72.
        assert rank == pytest.approx(0.80 * 0.9)

    def test_non_finite_cost_gives_zero_cost_norm(self):
        # candidate.avg_cost_usd validator forbids negatives but not inf; inf
        # cost -> math.isfinite(cost) False -> cost_norm = 0.0.
        candidate = self._candidate(cost=math.inf)
        rank = AdaptiveRouter._cost_adjusted_rank(candidate, quality=0.5, max_cost=10.0)
        assert rank == pytest.approx(0.80 * 0.5)


class TestGetLeaderboardNone:
    @pytest.mark.asyncio
    async def test_leaderboard_task_type_none(self):
        # task_type=None -> get_aggregate_scores called with task_type=None and
        # rows from multiple task types are returned, sorted by composite desc.
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(
            return_value=[
                _agg(model_profile_id="lo", task_type="feature", composite_score=0.6),
                _agg(model_profile_id="hi", task_type="bug_fix", composite_score=0.95),
            ]
        )
        router = AdaptiveRouter(benchmark_repo=repo)
        lb = await router.get_leaderboard(task_type=None)
        repo.get_aggregate_scores.assert_awaited_once_with(task_type=None)
        assert [c.model_profile_id for c in lb] == ["hi", "lo"]
        assert lb[0].composite_score >= lb[1].composite_score

    @pytest.mark.asyncio
    async def test_leaderboard_no_repo_returns_empty(self):
        router = AdaptiveRouter(benchmark_repo=None)
        assert await router.get_leaderboard(task_type=None) == []


class TestRouteBestHistoricalHappyPath:
    @pytest.mark.asyncio
    async def test_best_historical_score_no_cap(self):
        # max_cost_usd=None -> cap branch skipped -> best_historical_score.
        repo = _repo(
            [_agg(model_profile_id="winner", avg_cost=0.05, composite_score=0.88)]
        )
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3)
        decision = await router.route(task_type=TaskType.BUG_FIX, max_cost_usd=None)
        assert decision.fallback is False
        assert decision.reason == "best_historical_score"
        assert decision.selected_model_profile_id == "winner"
        assert decision.composite_score == pytest.approx(0.88)
        assert decision.estimated_cost_usd == pytest.approx(0.05)
        assert decision.sample_count == 10


class TestDeadCacheBehavior:
    # CA-T6: _cache is initialized and invalidate_cache() clears it, but route()
    # never reads or writes it. Pin the CURRENT (no caching) behavior; do NOT
    # assert caching works.
    @pytest.mark.asyncio
    async def test_route_does_not_populate_cache(self):
        repo = _repo([_agg(model_profile_id="m", composite_score=0.8)])
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3)
        await router.route(task_type=TaskType.BUG_FIX)
        # route() did not write the cache (dead-cache bug, asserted as current state).
        assert router._cache == {}

    @pytest.mark.asyncio
    async def test_route_recomputes_every_call_no_cache_hit(self):
        repo = _repo([_agg(model_profile_id="m", composite_score=0.8)])
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3)
        await router.route(task_type=TaskType.BUG_FIX)
        await router.route(task_type=TaskType.BUG_FIX)
        # No caching -> the repo is queried on every call.
        assert repo.get_aggregate_scores.await_count == 2

    def test_invalidate_cache_clears_and_resets_time(self):
        router = AdaptiveRouter()
        router._cache["k"] = MagicMock()
        router._cache_time = object()  # type: ignore[assignment]
        router.invalidate_cache()
        assert router._cache == {}
        assert router._cache_time is None
