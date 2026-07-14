from __future__ import annotations

import asyncio

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
        import pytest

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
