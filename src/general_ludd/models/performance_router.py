"""Performance-based model router — selects best model for a task type."""

from __future__ import annotations

import logging
from typing import Any, Protocol, cast

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
    ) -> str:
        """Record one model call outcome into the weight DB."""

    async def get_best_model(
        self,
        task_type: str,
        min_calls: int = 3,
        prefer_cost: bool = False,
    ) -> dict[str, object] | None:
        """Return the historical best model for a task type, or None."""

    async def get_ranking(self, task_type: str) -> list[dict[str, object]]:
        """Return raw per-model stats for a task type."""

    async def get_summary(
        self,
        service: str | None = None,
        task_type: str | None = None,
    ) -> list[dict[str, object]]:
        """Return aggregated per-(service, task, model) outcome summaries."""

    async def refresh_recent_stats(self) -> None:
        """Refresh the repository's recent-stats cache."""


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
    """Selects the best (service, model_name) for a task type.

    Uses ModelPerformanceRepository to get rankings and applies
    configurable strategies (balanced, quality, cheapest, fastest).
    """

    def __init__(
        self,
        perf_repo: object | None = None,
        config: dict[str, object] | None = None,
    ) -> None:
        """Initialize the router with an optional repository and config."""
        self._repo = perf_repo
        self._config: dict[str, object] = {
            "min_calls": 3,
            "default_fallback": "openai/gpt-4o",
            **(config or {}),
        }
        self._strategies: dict[str, str] = {}

    def set_strategy(self, task_type: str, strategy: str) -> None:
        """Assign a scoring strategy (balanced/quality/cheapest/fastest) to a task type."""
        if strategy not in DEFAULT_STRATEGIES:
            raise ValueError(f"Unknown strategy {strategy!r}. Valid: {', '.join(sorted(DEFAULT_STRATEGIES))}")
        self._strategies[task_type] = strategy

    def get_strategy(self, task_type: str) -> str:
        """Return the strategy registered for a task type (default: balanced)."""
        return self._strategies.get(task_type, "balanced")

    def get_config(self) -> dict[str, object]:
        """Return the router's strategies and default config for inspection."""
        return {
            "strategies": dict(self._strategies),
            "defaults": dict(self._config),
        }

    async def select_model(
        self,
        task_type: str,
        strategy: str | None = None,
        fallback: str | None = None,
    ) -> dict[str, object]:
        """Select the best (service, model_name) for a task type.

        Strategies:
        - "balanced": Weighted combination of success_rate, latency, cost
        - "quality": Highest success_rate
        - "cheapest": Lowest cost per call
        - "fastest": Lowest latency

        Returns a dict with service, model_name, score, and strategy info.
        """
        effective_strategy = strategy or self._strategies.get(task_type, "balanced")
        effective_fallback = cast(str, fallback or self._config["default_fallback"])

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
            repo = cast(ModelPerformanceRepository, self._repo)
            best = await repo.get_best_model(
                task_type,
                min_calls=cast(int, self._config["min_calls"]),
                prefer_cost=(effective_strategy == "cheapest"),
            )
        except Exception:
            logger.exception("get_best_model failed for %s", task_type)
            best = None

        if best is not None:
            best_any = cast(dict[str, Any], best)
            return {
                "service": cast(str, best_any.get("service", "openai")),
                "model_name": cast(str, best_any.get("model_name", "gpt-4o")),
                "score": float(cast(float, best_any.get("composite_score", 0.0))),
                "strategy": effective_strategy,
                "fallback": False,
                "reason": "historical_best",
            }

        ranking = await self.get_rankings(task_type, strategy=effective_strategy)
        if ranking:
            top = cast(dict[str, Any], ranking[0])
            return {
                "service": cast(str, top.get("service", "openai")),
                "model_name": cast(str, top.get("model_name", "gpt-4o")),
                "score": float(cast(float, top.get("score", 0.0))),
                "strategy": effective_strategy,
                "fallback": False,
                "reason": "strategy_ranked",
            }

        # Cross-task reuse: a task with no local history still benefits from
        # the weight DB — the model that performed best across ALL recorded
        # tasks is the informed pick (per-model global aggregation).
        global_ranking = await self.get_global_rankings(strategy=effective_strategy)
        if global_ranking:
            top_global = cast(dict[str, Any], global_ranking[0])
            return {
                "service": cast(str, top_global.get("service", "openai")),
                "model_name": cast(str, top_global.get("model_name", "gpt-4o")),
                "score": float(cast(float, top_global.get("score", 0.0))),
                "strategy": effective_strategy,
                "fallback": False,
                "reason": "cross_task_reuse",
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
    ) -> list[dict[str, object]]:
        """Get ranked list of models for a task type with scores."""
        effective_strategy = strategy or self._strategies.get(task_type, "balanced")

        if self._repo is None:
            return []

        try:
            repo = cast(ModelPerformanceRepository, self._repo)
            raw = await repo.get_ranking(task_type)
        except Exception:
            logger.exception("get_ranking failed for %s", task_type)
            return []

        if not raw:
            return []

        weights = DEFAULT_STRATEGIES.get(effective_strategy, DEFAULT_STRATEGIES["balanced"])

        scores = [float(cast(float, r.get("success_rate", 0.0))) for r in raw]
        latencies = [float(cast(float, r.get("avg_latency_ms", 0.0))) for r in raw]
        costs = [float(cast(float, r.get("avg_cost_usd", 0.0))) for r in raw]

        norm_scores = _scale(scores)
        norm_latencies = _scale(latencies)
        norm_costs = _scale(costs)

        ranked: list[dict[str, object]] = []
        for i, r in enumerate(raw):
            r_any = cast(dict[str, Any], r)
            w_score = norm_scores[i] if i < len(norm_scores) else 0.5
            w_lat = (1 - norm_latencies[i]) if i < len(norm_latencies) else 0.5
            w_cost = (1 - norm_costs[i]) if i < len(norm_costs) else 0.5

            composite = weights["success_rate"] * w_score + weights["latency"] * w_lat + weights["cost"] * w_cost

            ranked.append(
                {
                    "service": r_any.get("service", ""),
                    "model_name": r_any.get("model_name", ""),
                    "success_rate": r_any.get("success_rate", 0.0),
                    "avg_latency_ms": r_any.get("avg_latency_ms", 0.0),
                    "avg_cost_usd": r_any.get("avg_cost_usd", 0.0),
                    "sample_count": r_any.get("sample_count", 0),
                    "score": round(composite, 4),
                    "strategy": effective_strategy,
                }
            )

        ranked.sort(key=lambda x: cast(float, x["score"]), reverse=True)
        return ranked

    async def get_global_rankings(
        self,
        strategy: str | None = None,
    ) -> list[dict[str, object]]:
        """Rank models by performance aggregated ACROSS ALL task types.

        A task with no local history still uses the weight DB: per-model
        (service, model_name) rows from :meth:`ModelPerformanceRepository.
        get_summary` are re-aggregated over every recorded task so one
        model's proven quality on other work informs selection for a
        never-before-seen task (cross-task reuse).
        """
        effective_strategy = strategy or "balanced"

        if self._repo is None:
            return []

        try:
            repo = cast(ModelPerformanceRepository, self._repo)
            summary_rows = await repo.get_summary()
        except Exception:
            logger.exception("get_summary failed for global rankings")
            return []

        if not summary_rows:
            return []

        # Re-aggregate per (service, model_name) across task types.
        buckets: dict[tuple[str, str], dict[str, float]] = {}
        for row in summary_rows:
            row_any = cast(dict[str, Any], row)
            service = str(row_any.get("service", ""))
            model_name = str(row_any.get("model_name", ""))
            if not service or not model_name:
                continue
            key = (service, model_name)
            bucket = buckets.setdefault(
                key,
                {"success": 0.0, "total": 0.0, "cost": 0.0, "latency_ms": 0.0},
            )
            bucket["success"] += float(row_any.get("successful_calls", 0.0))
            bucket["total"] += float(row_any.get("total_calls", 0.0))
            bucket["cost"] += float(row_any.get("total_cost_usd", 0.0))
            bucket["latency_ms"] += float(row_any.get("avg_duration_ms", 0.0)) * float(row_any.get("total_calls", 0.0))

        raw: list[dict[str, Any]] = []
        for (service, model_name), bucket in buckets.items():
            total = bucket["total"]
            if total <= 0:
                continue
            raw.append(
                {
                    "service": service,
                    "model_name": model_name,
                    "success_rate": bucket["success"] / total,
                    "avg_latency_ms": bucket["latency_ms"] / total,
                    "avg_cost_usd": bucket["cost"] / total,
                    "sample_count": int(total),
                }
            )
        if not raw:
            return []

        weights = DEFAULT_STRATEGIES.get(effective_strategy, DEFAULT_STRATEGIES["balanced"])
        norm_scores = _scale([float(r["success_rate"]) for r in raw])
        norm_latencies = _scale([float(r["avg_latency_ms"]) for r in raw])
        norm_costs = _scale([float(r["avg_cost_usd"]) for r in raw])

        ranked: list[dict[str, object]] = []
        for i, r in enumerate(raw):
            w_score = norm_scores[i] if i < len(norm_scores) else 0.5
            w_lat = (1 - norm_latencies[i]) if i < len(norm_latencies) else 0.5
            w_cost = (1 - norm_costs[i]) if i < len(norm_costs) else 0.5
            composite = weights["success_rate"] * w_score + weights["latency"] * w_lat + weights["cost"] * w_cost
            ranked.append(
                {
                    "service": r["service"],
                    "model_name": r["model_name"],
                    "success_rate": round(r["success_rate"], 4),
                    "avg_latency_ms": round(r["avg_latency_ms"], 2),
                    "avg_cost_usd": round(r["avg_cost_usd"], 6),
                    "sample_count": r["sample_count"],
                    "score": round(composite, 4),
                    "strategy": effective_strategy,
                }
            )

        ranked.sort(key=lambda x: cast(float, x["score"]), reverse=True)
        return ranked
