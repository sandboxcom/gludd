from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from general_ludd.models.performance_router import (
    DEFAULT_STRATEGIES,
    ModelPerformanceRouter,
    _scale,
)


class TestScale:
    def test_scale_empty(self):
        assert _scale([]) == []

    def test_scale_single(self):
        assert _scale([5.0]) == [0.5]

    def test_scale_identical(self):
        assert _scale([3.0, 3.0, 3.0]) == [0.5, 0.5, 0.5]

    def test_scale_range(self):
        result = _scale([1.0, 3.0, 5.0])
        assert result == [0.0, 0.5, 1.0]

    def test_scale_reversed(self):
        result = _scale([10.0, 0.0])
        assert result == [1.0, 0.0]


class TestModelPerformanceRouterInit:
    def test_init_no_repo(self):
        router = ModelPerformanceRouter()
        assert router._repo is None
        assert router._config["min_calls"] == 3
        assert router._config["default_fallback"] == "openai/gpt-4o"

    def test_init_with_config(self):
        router = ModelPerformanceRouter(
            config={"min_calls": 5, "default_fallback": "anthropic/claude-3"},
        )
        assert router._config["min_calls"] == 5
        assert router._config["default_fallback"] == "anthropic/claude-3"


class TestSelectModel:
    async def test_select_model_no_repo_returns_fallback(self):
        router = ModelPerformanceRouter()
        result = await router.select_model("code")
        assert result["fallback"] is True
        assert result["reason"] == "no_performance_repo"
        assert result["service"] == "openai"
        assert result["model_name"] == "gpt-4o"

    async def test_select_model_no_repo_custom_fallback(self):
        router = ModelPerformanceRouter()
        result = await router.select_model("code", fallback="anthropic/claude-sonnet")
        assert result["service"] == "anthropic"
        assert result["model_name"] == "claude-sonnet"
        assert result["fallback"] is True

    async def test_select_model_with_repo_returns_best(self):
        repo = AsyncMock()
        repo.get_best_model.return_value = {
            "service": "openai",
            "model_name": "gpt-4o",
            "composite_score": 0.95,
        }
        router = ModelPerformanceRouter(perf_repo=repo)
        result = await router.select_model("code")
        assert result["fallback"] is False
        assert result["reason"] == "historical_best"
        assert result["score"] == 0.95
        repo.get_best_model.assert_called_once_with("code", min_calls=3, prefer_cost=False)

    async def test_select_model_cheapest_strategy(self):
        repo = AsyncMock()
        repo.get_best_model.return_value = None
        repo.get_ranking.return_value = [
            {"service": "openai", "model_name": "gpt-4o",
             "success_rate": 0.95, "avg_latency_ms": 500.0, "avg_cost_usd": 0.01,
             "sample_count": 10},
            {"service": "openai", "model_name": "gpt-3.5-turbo",
             "success_rate": 0.85, "avg_latency_ms": 200.0, "avg_cost_usd": 0.001,
             "sample_count": 20},
        ]
        router = ModelPerformanceRouter(perf_repo=repo)
        result = await router.select_model("summary", strategy="cheapest")
        assert result["fallback"] is False
        assert result["reason"] == "strategy_ranked"

    async def test_select_model_repo_get_best_model_exception(self):
        repo = AsyncMock()
        repo.get_best_model.side_effect = RuntimeError("DB down")
        repo.get_ranking.return_value = []
        router = ModelPerformanceRouter(perf_repo=repo)
        result = await router.select_model("code")
        assert result["fallback"] is True
        assert result["reason"] == "no_historical_data"

    async def test_select_model_with_set_strategy(self):
        repo = AsyncMock()
        repo.get_best_model.return_value = {
            "service": "google",
            "model_name": "gemini-pro",
            "composite_score": 0.88,
        }
        router = ModelPerformanceRouter(perf_repo=repo)
        router.set_strategy("code", "quality")
        result = await router.select_model("code")
        assert result["fallback"] is False
        assert result["strategy"] == "quality"
        assert result["service"] == "google"


class TestGetRankings:
    async def test_get_rankings_no_repo(self):
        router = ModelPerformanceRouter()
        ranking = await router.get_rankings("code")
        assert ranking == []

    async def test_get_rankings_empty(self):
        repo = AsyncMock()
        repo.get_ranking.return_value = []
        router = ModelPerformanceRouter(perf_repo=repo)
        ranking = await router.get_rankings("code")
        assert ranking == []

    async def test_get_rankings_balanced(self):
        repo = AsyncMock()
        repo.get_ranking.return_value = [
            {"service": "openai", "model_name": "gpt-4o",
             "success_rate": 0.95, "avg_latency_ms": 800.0, "avg_cost_usd": 0.03,
             "sample_count": 50},
            {"service": "openai", "model_name": "gpt-3.5-turbo",
             "success_rate": 0.85, "avg_latency_ms": 200.0, "avg_cost_usd": 0.002,
             "sample_count": 100},
            {"service": "anthropic", "model_name": "claude-3-haiku",
             "success_rate": 0.90, "avg_latency_ms": 300.0, "avg_cost_usd": 0.005,
             "sample_count": 30},
        ]
        router = ModelPerformanceRouter(perf_repo=repo)
        ranking = await router.get_rankings("code")
        assert len(ranking) == 3
        assert ranking[0]["score"] >= ranking[1]["score"] >= ranking[2]["score"]
        for r in ranking:
            assert "strategy" in r
            assert r["strategy"] == "balanced"
            assert "score" in r
            assert "success_rate" in r

    async def test_get_rankings_quality_strategy(self):
        repo = AsyncMock()
        repo.get_ranking.return_value = [
            {"service": "a", "model_name": "m1",
             "success_rate": 0.99, "avg_latency_ms": 1000.0, "avg_cost_usd": 0.1,
             "sample_count": 10},
            {"service": "b", "model_name": "m2",
             "success_rate": 0.50, "avg_latency_ms": 100.0, "avg_cost_usd": 0.001,
             "sample_count": 10},
        ]
        router = ModelPerformanceRouter(perf_repo=repo)
        ranking = await router.get_rankings("code", strategy="quality")
        assert ranking[0]["model_name"] == "m1"
        assert ranking[0]["strategy"] == "quality"

    async def test_get_rankings_fastest_strategy(self):
        repo = AsyncMock()
        repo.get_ranking.return_value = [
            {"service": "a", "model_name": "slow",
             "success_rate": 0.99, "avg_latency_ms": 5000.0, "avg_cost_usd": 0.1,
             "sample_count": 10},
            {"service": "b", "model_name": "fast",
             "success_rate": 0.60, "avg_latency_ms": 50.0, "avg_cost_usd": 0.01,
             "sample_count": 10},
        ]
        router = ModelPerformanceRouter(perf_repo=repo)
        ranking = await router.get_rankings("code", strategy="fastest")
        assert ranking[0]["model_name"] == "fast"

    async def test_get_rankings_cheapest_strategy(self):
        repo = AsyncMock()
        repo.get_ranking.return_value = [
            {"service": "a", "model_name": "expensive",
             "success_rate": 0.99, "avg_latency_ms": 100.0, "avg_cost_usd": 0.5,
             "sample_count": 10},
            {"service": "b", "model_name": "cheap",
             "success_rate": 0.50, "avg_latency_ms": 500.0, "avg_cost_usd": 0.0001,
             "sample_count": 10},
        ]
        router = ModelPerformanceRouter(perf_repo=repo)
        ranking = await router.get_rankings("code", strategy="cheapest")
        assert ranking[0]["model_name"] == "cheap"

    async def test_get_rankings_repo_exception(self):
        repo = AsyncMock()
        repo.get_ranking.side_effect = RuntimeError("fail")
        router = ModelPerformanceRouter(perf_repo=repo)
        ranking = await router.get_rankings("code")
        assert ranking == []


class TestStrategyManagement:
    def test_set_strategy_valid(self):
        router = ModelPerformanceRouter()
        router.set_strategy("code", "quality")
        assert router.get_strategy("code") == "quality"

    def test_set_strategy_default(self):
        router = ModelPerformanceRouter()
        assert router.get_strategy("unknown") == "balanced"

    def test_set_strategy_invalid(self):
        router = ModelPerformanceRouter()
        with pytest.raises(ValueError, match="Unknown strategy"):
            router.set_strategy("code", "nonexistent")

    def test_get_config(self):
        router = ModelPerformanceRouter()
        router.set_strategy("code", "fastest")
        cfg = router.get_config()
        assert "strategies" in cfg
        assert cfg["strategies"]["code"] == "fastest"
        assert "defaults" in cfg
        assert cfg["defaults"]["min_calls"] == 3


class TestDEFAULT_STRATEGIES:
    def test_strategies_defined(self):
        assert "balanced" in DEFAULT_STRATEGIES
        assert "quality" in DEFAULT_STRATEGIES
        assert "cheapest" in DEFAULT_STRATEGIES
        assert "fastest" in DEFAULT_STRATEGIES

    def test_balanced_weights(self):
        w = DEFAULT_STRATEGIES["balanced"]
        assert w["success_rate"] == 0.5
        assert w["latency"] == 0.25
        assert w["cost"] == 0.25
        assert sum(w.values()) == 1.0

    def test_quality_weights(self):
        w = DEFAULT_STRATEGIES["quality"]
        assert w["success_rate"] == 1.0
        assert w["latency"] == 0.0
        assert w["cost"] == 0.0
