from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.models.performance_router import (
    DEFAULT_STRATEGIES,
    ModelPerformanceRouter,
    _scale,
)


class TestScale:
    def test_empty_returns_empty(self) -> None:
        assert _scale([]) == []

    def test_single_value(self) -> None:
        assert _scale([5.0]) == [0.5]

    def test_all_equal(self) -> None:
        assert _scale([3.0, 3.0, 3.0]) == [0.5, 0.5, 0.5]

    def test_varied_values(self) -> None:
        result = _scale([0.0, 10.0])
        assert result == [0.0, 1.0]


class TestDefaultStrategies:
    def test_all_strategies_present(self) -> None:
        assert "balanced" in DEFAULT_STRATEGIES
        assert "quality" in DEFAULT_STRATEGIES
        assert "cheapest" in DEFAULT_STRATEGIES
        assert "fastest" in DEFAULT_STRATEGIES

    def test_quality_weights_only_success(self) -> None:
        w = DEFAULT_STRATEGIES["quality"]
        assert w["success_rate"] == 1.0
        assert w["latency"] == 0.0
        assert w["cost"] == 0.0

    def test_cheapest_weights_only_cost(self) -> None:
        w = DEFAULT_STRATEGIES["cheapest"]
        assert w["success_rate"] == 0.0
        assert w["latency"] == 0.0
        assert w["cost"] == 1.0

    def test_fastest_weights_only_latency(self) -> None:
        w = DEFAULT_STRATEGIES["fastest"]
        assert w["success_rate"] == 0.0
        assert w["latency"] == 1.0
        assert w["cost"] == 0.0

    def test_balanced_weights_sum_to_1(self) -> None:
        w = DEFAULT_STRATEGIES["balanced"]
        total = w["success_rate"] + w["latency"] + w["cost"]
        assert total == 1.0


class TestModelPerformanceRouter:
    def test_default_construction(self) -> None:
        router = ModelPerformanceRouter()
        assert router.get_strategy("bug_fix") == "balanced"
        assert router._strategies == {}

    def test_set_and_get_strategy(self) -> None:
        router = ModelPerformanceRouter()
        router.set_strategy("bug_fix", "quality")
        assert router.get_strategy("bug_fix") == "quality"
        assert router.get_strategy("unknown") == "balanced"

    def test_set_invalid_strategy_raises(self) -> None:

        router = ModelPerformanceRouter()
        with pytest.raises(ValueError, match="Unknown strategy"):
            router.set_strategy("bug_fix", "nonexistent")

    def test_set_strategy_accepts_all_defaults(self) -> None:
        router = ModelPerformanceRouter()
        for name in DEFAULT_STRATEGIES:
            router.set_strategy(f"task_{name}", name)
            assert router.get_strategy(f"task_{name}") == name

    def test_get_config_returns_defaults(self) -> None:
        router = ModelPerformanceRouter()
        cfg = router.get_config()
        assert cfg["defaults"]["min_calls"] == 3
        assert "openai/gpt-4o" in str(cfg["defaults"]["default_fallback"])

    def test_get_config_returns_strategies(self) -> None:
        router = ModelPerformanceRouter()
        router.set_strategy("bug_fix", "fastest")
        cfg = router.get_config()
        assert cfg["strategies"]["bug_fix"] == "fastest"

    def test_custom_config_overrides_defaults(self) -> None:
        router = ModelPerformanceRouter(config={"min_calls": 10, "default_fallback": "custom/model"})
        cfg = router.get_config()
        assert cfg["defaults"]["min_calls"] == 10
        assert cfg["defaults"]["default_fallback"] == "custom/model"

    def test_select_model_without_repo_uses_fallback(self) -> None:
        router = ModelPerformanceRouter()
        result = asyncio.run(router.select_model("bug_fix"))
        assert result["fallback"] is True
        assert result["reason"] == "no_performance_repo"
        assert "model_name" in result

    def test_select_model_with_strategy_override(self) -> None:
        router = ModelPerformanceRouter()
        result = asyncio.run(router.select_model("bug_fix", strategy="quality"))
        assert result["strategy"] == "quality"
        assert result["fallback"] is True

    def test_select_model_with_explicit_fallback(self) -> None:
        router = ModelPerformanceRouter()
        result = asyncio.run(router.select_model("bug_fix", fallback="azure/gpt-4"))
        assert result["model_name"] == "gpt-4"
        assert result["service"] == "azure"

    def test_get_rankings_without_repo(self) -> None:
        router = ModelPerformanceRouter()
        result = asyncio.run(router.get_rankings("bug_fix"))
        assert result == []

    def test_strategy_isolation(self) -> None:
        router = ModelPerformanceRouter()
        router.set_strategy("bug_fix", "cheapest")
        assert router.get_strategy("feature") == "balanced"


class TestModelPerformanceRouterDeep:
    """Repository-interaction paths for select_model and get_rankings."""

    @staticmethod
    def _mock_repo(**overrides: object) -> object:
        repo = MagicMock()
        repo.get_best_model = AsyncMock(return_value=overrides.get("best_model"))
        repo.get_ranking = AsyncMock(return_value=overrides.get("ranking", []))
        repo.get_summary = AsyncMock(return_value=overrides.get("summary", []))
        return repo

    def test_select_model_with_repo_returns_historical_best(self) -> None:
        repo = self._mock_repo(
            best_model={
                "service": "openai",
                "model_name": "gpt-4o",
                "composite_score": 0.92,
            }
        )
        router = ModelPerformanceRouter(perf_repo=repo)
        router.set_strategy("bug_fix", "balanced")
        result = asyncio.run(router.select_model("bug_fix"))
        assert result["fallback"] is False
        assert result["reason"] == "historical_best"
        assert result["model_name"] == "gpt-4o"
        assert result["score"] == 0.92
        assert result["strategy"] == "balanced"
        repo.get_best_model.assert_awaited_once()

    def test_select_model_cheapest_prefer_cost(self) -> None:
        repo = self._mock_repo(
            best_model={
                "service": "openai",
                "model_name": "gpt-4o-mini",
                "composite_score": 0.85,
            }
        )
        router = ModelPerformanceRouter(perf_repo=repo)
        result = asyncio.run(router.select_model("bug_fix", strategy="cheapest"))
        assert result["fallback"] is False
        assert result["model_name"] == "gpt-4o-mini"
        repo.get_best_model.assert_awaited_once_with("bug_fix", min_calls=3, prefer_cost=True)

    def test_select_model_best_none_falls_to_ranking(self) -> None:
        repo = self._mock_repo(
            best_model=None,
            ranking=[
                {
                    "service": "anthropic",
                    "model_name": "claude-haiku",
                    "success_rate": 0.95,
                    "avg_latency_ms": 200.0,
                    "avg_cost_usd": 0.001,
                    "sample_count": 50,
                }
            ],
        )
        router = ModelPerformanceRouter(perf_repo=repo)
        result = asyncio.run(router.select_model("bug_fix"))
        assert result["fallback"] is False
        assert result["reason"] == "strategy_ranked"
        assert result["model_name"] == "claude-haiku"
        assert result["service"] == "anthropic"

    def test_select_model_best_none_and_empty_ranking_falls_back(self) -> None:
        repo = self._mock_repo(best_model=None, ranking=[])
        router = ModelPerformanceRouter(perf_repo=repo)
        result = asyncio.run(router.select_model("bug_fix"))
        assert result["fallback"] is True
        assert result["reason"] == "no_historical_data"

    def test_select_model_cross_task_reuse_when_task_unknown(self) -> None:
        """A task with no local history must still use the weight DB: the
        model that performed best on OTHER tasks wins the cross-task pick."""
        repo = self._mock_repo(
            best_model=None,
            ranking=[],
            summary=[
                {
                    "service": "local",
                    "task_type": "local_factoid",
                    "model_name": "qwen-0.5b",
                    "total_calls": 10,
                    "successful_calls": 9,
                    "success_rate": 0.9,
                    "total_cost_usd": 0.05,
                    "avg_duration_ms": 120.0,
                },
                {
                    "service": "local",
                    "task_type": "local_factoid",
                    "model_name": "qwen-0.5b-bad",
                    "total_calls": 10,
                    "successful_calls": 1,
                    "success_rate": 0.1,
                    "total_cost_usd": 1.0,
                    "avg_duration_ms": 300.0,
                },
            ],
        )
        router = ModelPerformanceRouter(perf_repo=repo)
        result = asyncio.run(router.select_model("never_seen_task"))
        assert result["fallback"] is False
        assert result["reason"] == "cross_task_reuse"
        assert result["model_name"] == "qwen-0.5b"
        assert result["service"] == "local"

    def test_select_model_cross_task_empty_summary_falls_back(self) -> None:
        repo = self._mock_repo(best_model=None, ranking=[], summary=[])
        router = ModelPerformanceRouter(perf_repo=repo)
        result = asyncio.run(router.select_model("never_seen_task"))
        assert result["fallback"] is True
        assert result["reason"] == "no_historical_data"

    def test_get_global_rankings_aggregates_across_tasks(self) -> None:
        repo = self._mock_repo(
            summary=[
                {
                    "service": "local",
                    "task_type": "task_a",
                    "model_name": "m1",
                    "total_calls": 8,
                    "successful_calls": 8,
                    "success_rate": 1.0,
                    "total_cost_usd": 0.4,
                    "avg_duration_ms": 100.0,
                },
                {
                    "service": "local",
                    "task_type": "task_b",
                    "model_name": "m1",
                    "total_calls": 2,
                    "successful_calls": 2,
                    "success_rate": 1.0,
                    "total_cost_usd": 0.1,
                    "avg_duration_ms": 100.0,
                },
                {
                    "service": "local",
                    "task_type": "task_a",
                    "model_name": "m2",
                    "total_calls": 10,
                    "successful_calls": 0,
                    "success_rate": 0.0,
                    "total_cost_usd": 5.0,
                    "avg_duration_ms": 900.0,
                },
            ],
        )
        router = ModelPerformanceRouter(perf_repo=repo)
        ranking = asyncio.run(router.get_global_rankings(strategy="quality"))
        assert ranking, "global rankings must aggregate per-model across tasks"
        assert ranking[0]["model_name"] == "m1"
        assert ranking[0]["sample_count"] == 10

    def test_select_model_repo_get_best_throws_falls_through(self) -> None:
        from unittest.mock import AsyncMock

        repo = AsyncMock()
        repo.get_best_model = AsyncMock(side_effect=RuntimeError("db down"))
        repo.get_ranking = AsyncMock(
            return_value=[
                {
                    "service": "openai",
                    "model_name": "gpt-4o",
                    "success_rate": 0.99,
                    "avg_latency_ms": 300.0,
                    "avg_cost_usd": 0.01,
                    "sample_count": 100,
                }
            ]
        )
        router = ModelPerformanceRouter(perf_repo=repo)
        result = asyncio.run(router.select_model("bug_fix"))
        assert result["fallback"] is False
        assert result["reason"] == "strategy_ranked"

    def test_select_model_complete_failure_falls_back(self) -> None:
        from unittest.mock import AsyncMock

        repo = AsyncMock()
        repo.get_best_model = AsyncMock(side_effect=RuntimeError("boom"))
        repo.get_ranking = AsyncMock(side_effect=RuntimeError("boom again"))
        router = ModelPerformanceRouter(perf_repo=repo)
        result = asyncio.run(router.select_model("bug_fix"))
        assert result["fallback"] is True
        assert result["reason"] == "no_historical_data"

    def test_select_model_fallback_no_slash(self) -> None:
        router = ModelPerformanceRouter()
        result = asyncio.run(router.select_model("bug_fix", fallback="just-model-name"))
        assert result["service"] == "openai"
        assert result["model_name"] == "just-model-name"

    def test_get_rankings_with_repo_computes_scores(self) -> None:
        repo = self._mock_repo(
            ranking=[
                {
                    "service": "openai",
                    "model_name": "gpt-4o",
                    "success_rate": 0.98,
                    "avg_latency_ms": 400.0,
                    "avg_cost_usd": 0.01,
                    "sample_count": 200,
                },
                {
                    "service": "openai",
                    "model_name": "gpt-3.5",
                    "success_rate": 0.85,
                    "avg_latency_ms": 150.0,
                    "avg_cost_usd": 0.001,
                    "sample_count": 500,
                },
            ]
        )
        router = ModelPerformanceRouter(perf_repo=repo)
        router.set_strategy("bug_fix", "balanced")
        ranked = asyncio.run(router.get_rankings("bug_fix"))
        assert len(ranked) == 2
        assert all("score" in r for r in ranked)
        assert all("strategy" in r for r in ranked)
        assert ranked[0]["score"] >= ranked[1]["score"]

    def test_get_rankings_quality_strategy_ranks_by_success(self) -> None:
        repo = self._mock_repo(
            ranking=[
                {
                    "service": "a",
                    "model_name": "low-success",
                    "success_rate": 0.60,
                    "avg_latency_ms": 10.0,
                    "avg_cost_usd": 0.001,
                    "sample_count": 10,
                },
                {
                    "service": "b",
                    "model_name": "high-success",
                    "success_rate": 1.0,
                    "avg_latency_ms": 1000.0,
                    "avg_cost_usd": 1.0,
                    "sample_count": 10,
                },
            ]
        )
        router = ModelPerformanceRouter(perf_repo=repo)
        ranked = asyncio.run(router.get_rankings("bug_fix", strategy="quality"))
        assert ranked[0]["model_name"] == "high-success"
        assert ranked[0]["strategy"] == "quality"

    def test_get_rankings_cheapest_strategy_ranks_by_cost(self) -> None:
        repo = self._mock_repo(
            ranking=[
                {
                    "service": "a",
                    "model_name": "expensive",
                    "success_rate": 1.0,
                    "avg_latency_ms": 10.0,
                    "avg_cost_usd": 1.0,
                    "sample_count": 10,
                },
                {
                    "service": "b",
                    "model_name": "cheap",
                    "success_rate": 0.01,
                    "avg_latency_ms": 1000.0,
                    "avg_cost_usd": 0.0,
                    "sample_count": 10,
                },
            ]
        )
        router = ModelPerformanceRouter(perf_repo=repo)
        ranked = asyncio.run(router.get_rankings("bug_fix", strategy="cheapest"))
        assert ranked[0]["model_name"] == "cheap"
        assert ranked[0]["strategy"] == "cheapest"

    def test_get_rankings_fastest_strategy_ranks_by_latency(self) -> None:
        repo = self._mock_repo(
            ranking=[
                {
                    "service": "a",
                    "model_name": "slow",
                    "success_rate": 1.0,
                    "avg_latency_ms": 5000.0,
                    "avg_cost_usd": 0.0,
                    "sample_count": 10,
                },
                {
                    "service": "b",
                    "model_name": "fast",
                    "success_rate": 0.01,
                    "avg_latency_ms": 1.0,
                    "avg_cost_usd": 1.0,
                    "sample_count": 10,
                },
            ]
        )
        router = ModelPerformanceRouter(perf_repo=repo)
        ranked = asyncio.run(router.get_rankings("bug_fix", strategy="fastest"))
        assert ranked[0]["model_name"] == "fast"
        assert ranked[0]["strategy"] == "fastest"

    def test_get_rankings_repo_throws_returns_empty(self) -> None:
        from unittest.mock import AsyncMock

        repo = AsyncMock()
        repo.get_ranking = AsyncMock(side_effect=RuntimeError("db down"))
        router = ModelPerformanceRouter(perf_repo=repo)
        ranked = asyncio.run(router.get_rankings("bug_fix"))
        assert ranked == []

    def test_get_rankings_repo_returns_empty(self) -> None:
        repo = self._mock_repo(ranking=[])
        router = ModelPerformanceRouter(perf_repo=repo)
        ranked = asyncio.run(router.get_rankings("bug_fix"))
        assert ranked == []

    def test_get_rankings_with_strategy_from_registry(self) -> None:
        repo = self._mock_repo(
            ranking=[
                {
                    "service": "a",
                    "model_name": "m",
                    "success_rate": 1.0,
                    "avg_latency_ms": 100.0,
                    "avg_cost_usd": 0.01,
                    "sample_count": 10,
                }
            ]
        )
        router = ModelPerformanceRouter(perf_repo=repo)
        router.set_strategy("bug_fix", "quality")
        ranked = asyncio.run(router.get_rankings("bug_fix"))
        assert len(ranked) == 1
        assert ranked[0]["strategy"] == "quality"
