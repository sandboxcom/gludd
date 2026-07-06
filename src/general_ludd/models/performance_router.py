"""Performance-based model router — selects best model for a task type."""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class ModelPerformanceRepository(Protocol):
    """Interface for the model performance repository.

    Implemented by the parallel task building ModelPerformanceRepository.
    """

    async def record_call(
        self,
        service: str,
        model_name: str,
        task_type: str,
        success: bool,
        latency_ms: float,
        cost_usd: float,
        tokens_in: int = 0,
        tokens_out: int = 0,
        error: str | None = None,
    ) -> str: ...

    async def get_best_model(
        self,
        task_type: str,
        min_calls: int = 3,
        prefer_cost: bool = False,
    ) -> dict[str, Any] | None: ...

    async def get_ranking(self, task_type: str) -> list[dict[str, Any]]: ...

    async def get_summary(
        self,
        service: str | None = None,
        task_type: str | None = None,
    ) -> list[dict[str, Any]]: ...

    async def refresh_recent_stats(self) -> None: ...


DEFAULT_STRATEGIES: dict[str, dict[str, float]] = {
    "balanced": {"success_rate": 0.5, "latency": 0.25, "cost": 0.25},
    "quality": {"success_rate": 1.0, "latency": 0.0, "cost": 0.0},
    "cheapest": {"success_rate": 0.0, "latency": 0.0, "cost": 1.0},
    "fastest": {"success_rate": 0.0, "latency": 1.0, "cost": 0.0},
}


def _scale(values: list[float]) -> list[float]:
    """Min-max scale a list of values to [0, 1]."""
    if not values:
        return []
    mn = min(values)
    mx = max(values)
    if mx == mn:
        return [0.5] * len(values)
    return [(v - mn) / (mx - mn) for v in values]


class ModelPerformanceRouter:
    """Selects the best (service, model_name) for a task type based on
    historical performance.

    Uses ModelPerformanceRepository to get rankings and applies
    configurable strategies (balanced, quality, cheapest, fastest).
    """

    def __init__(
        self,
        perf_repo: Any | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._repo = perf_repo
        self._config: dict[str, Any] = {
            "min_calls": 3,
            "default_fallback": "openai/gpt-4o",
            ** (config or {}),
        }
        self._strategies: dict[str, str] = {}

    def set_strategy(self, task_type: str, strategy: str) -> None:
        if strategy not in DEFAULT_STRATEGIES:
            raise ValueError(
                f"Unknown strategy {strategy!r}. "
                f"Valid: {', '.join(sorted(DEFAULT_STRATEGIES))}"
            )
        self._strategies[task_type] = strategy

    def get_strategy(self, task_type: str) -> str:
        return self._strategies.get(task_type, "balanced")

    def get_config(self) -> dict[str, Any]:
        return {
            "strategies": dict(self._strategies),
            "defaults": dict(self._config),
        }

    async def select_model(
        self,
        task_type: str,
        strategy: str | None = None,
        fallback: str | None = None,
    ) -> dict[str, Any]:
        """Select the best (service, model_name) for a task type.

        Strategies:
        - "balanced": Weighted combination of success_rate, latency, cost
        - "quality": Highest success_rate
        - "cheapest": Lowest cost per call
        - "fastest": Lowest latency

        Returns a dict with service, model_name, score, and strategy info.
        """
        effective_strategy = strategy or self._strategies.get(task_type, "balanced")
        effective_fallback = fallback or self._config["default_fallback"]

        if self._repo is None:
            parts = effective_fallback.split("/", 1)
            return {
                "service": parts[0] if len(parts) > 1 else "openai",
                "model_name": parts[-1],
                "score": 0.0,
                "strategy": effective_strategy,
                "fallback": True,
                "reason": "no_performance_repo",
            }

        try:
            best = await self._repo.get_best_model(
                task_type,
                min_calls=self._config["min_calls"],
                prefer_cost=(effective_strategy == "cheapest"),
            )
        except Exception:
            logger.exception("get_best_model failed for %s", task_type)
            best = None

        if best is not None:
            return {
                "service": best.get("service", "openai"),
                "model_name": best.get("model_name", "gpt-4o"),
                "score": float(best.get("composite_score", 0.0)),
                "strategy": effective_strategy,
                "fallback": False,
                "reason": "historical_best",
            }

        ranking = await self.get_rankings(task_type, strategy=effective_strategy)
        if ranking:
            top = ranking[0]
            return {
                "service": top.get("service", "openai"),
                "model_name": top.get("model_name", "gpt-4o"),
                "score": float(top.get("score", 0.0)),
                "strategy": effective_strategy,
                "fallback": False,
                "reason": "strategy_ranked",
            }

        parts = effective_fallback.split("/", 1)
        return {
            "service": parts[0] if len(parts) > 1 else "openai",
            "model_name": parts[-1],
            "score": 0.0,
            "strategy": effective_strategy,
            "fallback": True,
            "reason": "no_historical_data",
        }

    async def get_rankings(
        self,
        task_type: str,
        strategy: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get ranked list of models for a task type with scores."""
        effective_strategy = strategy or self._strategies.get(task_type, "balanced")

        if self._repo is None:
            return []

        try:
            raw = await self._repo.get_ranking(task_type)
        except Exception:
            logger.exception("get_ranking failed for %s", task_type)
            return []

        if not raw:
            return []

        weights = DEFAULT_STRATEGIES.get(effective_strategy, DEFAULT_STRATEGIES["balanced"])

        scores = [
            float(r.get("success_rate", 0.0))
            for r in raw
        ]
        latencies = [
            float(r.get("avg_latency_ms", 0.0))
            for r in raw
        ]
        costs = [
            float(r.get("avg_cost_usd", 0.0))
            for r in raw
        ]

        norm_scores = _scale(scores)
        norm_latencies = _scale(latencies)
        norm_costs = _scale(costs)

        ranked: list[dict[str, Any]] = []
        for i, r in enumerate(raw):
            w_score = norm_scores[i] if i < len(norm_scores) else 0.5
            w_lat = (1 - norm_latencies[i]) if i < len(norm_latencies) else 0.5
            w_cost = (1 - norm_costs[i]) if i < len(norm_costs) else 0.5

            composite = (
                weights["success_rate"] * w_score
                + weights["latency"] * w_lat
                + weights["cost"] * w_cost
            )

            ranked.append({
                "service": r.get("service", ""),
                "model_name": r.get("model_name", ""),
                "success_rate": r.get("success_rate", 0.0),
                "avg_latency_ms": r.get("avg_latency_ms", 0.0),
                "avg_cost_usd": r.get("avg_cost_usd", 0.0),
                "sample_count": r.get("sample_count", 0),
                "score": round(composite, 4),
                "strategy": effective_strategy,
            })

        ranked.sort(key=lambda x: x["score"], reverse=True)
        return ranked
