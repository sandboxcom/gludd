"""Regression: AdaptiveRouter cache must re-validate health on hit.

Bug: lines 66-69 of scoring/router.py returned a cached RoutingDecision without
re-checking is_healthy(), so a model whose circuit-breaker opened mid-window
continued to receive traffic for up to 5 minutes.

Fix: on a cache hit, if a health_tracker is wired, verify the cached model is
still healthy; if not, drop the entry and fall through to recompute.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from general_ludd.schemas.benchmark import RoutingDecision, TaskType
from general_ludd.scoring.router import AdaptiveRouter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_decision(model_id: str = "model-a") -> RoutingDecision:
    return RoutingDecision(
        selected_prompt_profile_id=None,
        selected_model_profile_id=model_id,
        composite_score=0.9,
        estimated_cost_usd=0.01,
        sample_count=5,
        fallback=False,
        reason="best_historical_score",
    )


def _healthy_tracker(model_id: str, healthy: bool) -> MagicMock:
    tracker = MagicMock()
    tracker.is_healthy.side_effect = (
        lambda mid, admit_probe=True: healthy if mid == model_id else True
    )
    return tracker


def _mock_repo_for(model_id: str = "model-b") -> MagicMock:
    """Return a repo whose get_aggregate_scores yields a single healthy fallback."""
    repo = MagicMock()

    async def _agg(*args, **kwargs):
        return [
            {
                "model_profile_id": model_id,
                "prompt_profile_id": None,
                "composite_score": 0.7,
                "avg_cost": 0.005,
                "sample_count": 5,
                "task_type": TaskType.BUG_FIX.value,
            }
        ]

    # AsyncMock so .call_count is tracked while awaiting like a coroutine.
    repo.get_aggregate_scores = AsyncMock(side_effect=_agg)
    return repo


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCacheHealthRevalidation:
    """Cache hit must re-check health; stale unhealthy entry must be evicted."""

    async def test_cache_hit_healthy_model_returns_cached(self):
        """If the cached model is still healthy, return from cache (no repo call)."""
        tracker = _healthy_tracker("model-a", healthy=True)
        router = AdaptiveRouter(health_tracker=tracker)
        key = router._cache_key(TaskType.BUG_FIX, None)
        decision = _make_decision("model-a")
        # Seed the cache directly.
        from datetime import datetime

        router._cache[key] = decision
        router._cache_time = datetime.now()

        result = await router.route(TaskType.BUG_FIX)

        assert result is decision
        tracker.is_healthy.assert_called_once_with("model-a", admit_probe=False)

    async def test_cache_hit_unhealthy_model_evicts_and_recomputes(self):
        """Core regression: if cached model's breaker is open, drop entry + recompute."""
        tracker = _healthy_tracker("model-a", healthy=False)
        repo = _mock_repo_for("model-b")
        router = AdaptiveRouter(benchmark_repo=repo, health_tracker=tracker)
        key = router._cache_key(TaskType.BUG_FIX, None)
        decision = _make_decision("model-a")

        from datetime import datetime

        router._cache[key] = decision
        router._cache_time = datetime.now()

        # model-b is healthy; model-a's tracker returns False.
        # The tracker must also pass health check for model-b during recompute.
        tracker.is_healthy.side_effect = (
            lambda mid, admit_probe=True: mid != "model-a"
        )

        result = await router.route(TaskType.BUG_FIX)

        # Must NOT return the stale cached decision for model-a.
        assert result.selected_model_profile_id != "model-a", (
            "Router returned stale cached model-a after its breaker opened"
        )
        # Cache entry for model-a must have been evicted.
        assert key not in router._cache or router._cache[key].selected_model_profile_id != "model-a"

    async def test_cache_hit_no_tracker_returns_cached_without_check(self):
        """Without a health_tracker, cache hit returns immediately (existing behaviour)."""
        router = AdaptiveRouter(health_tracker=None)
        key = router._cache_key(TaskType.BUG_FIX, None)
        decision = _make_decision("model-a")

        from datetime import datetime

        router._cache[key] = decision
        router._cache_time = datetime.now()

        result = await router.route(TaskType.BUG_FIX)

        assert result is decision

    async def test_unhealthy_eviction_allows_subsequent_healthy_hit(self):
        """After eviction + recompute, a second call with model-b healthy uses the new cache."""
        tracker = MagicMock()
        # First call: model-a unhealthy → evict; model-b healthy for recompute.
        # Second call: model-b healthy → serve from new cache.
        call_count = {"n": 0}

        def _is_healthy(mid, admit_probe=True):
            call_count["n"] += 1
            return mid != "model-a"

        tracker.is_healthy.side_effect = _is_healthy
        repo = _mock_repo_for("model-b")
        router = AdaptiveRouter(benchmark_repo=repo, health_tracker=tracker)
        key = router._cache_key(TaskType.BUG_FIX, None)
        stale = _make_decision("model-a")

        from datetime import datetime

        router._cache[key] = stale
        router._cache_time = datetime.now()

        first = await router.route(TaskType.BUG_FIX)
        second = await router.route(TaskType.BUG_FIX)

        assert first.selected_model_profile_id == "model-b"
        assert second.selected_model_profile_id == "model-b"
        # Second call must have been served from cache (repo called exactly once during recompute).
        assert repo.get_aggregate_scores.call_count == 1
