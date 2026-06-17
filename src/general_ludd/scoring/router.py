"""Adaptive router — selects best prompt+model combo based on historical benchmark scores."""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any

from general_ludd.schemas.benchmark import (
    RoutingCandidate,
    RoutingDecision,
    TaskType,
)

log = logging.getLogger(__name__)


class AdaptiveRouter:
    def __init__(
        self,
        benchmark_repo: Any | None = None,
        min_samples: int = 3,
        cost_weight: float = 0.2,
        quality_weight: float = 0.8,
        quantization_map: dict[str, tuple[str, float]] | None = None,
        health_tracker: Any | None = None,
    ) -> None:
        self._repo = benchmark_repo
        self._min_samples = min_samples
        self._cost_weight = cost_weight
        self._quality_weight = quality_weight
        self._quantization_map = quantization_map or {}
        self._health_tracker = health_tracker
        self._cache: dict[str, RoutingDecision] = {}
        self._cache_time: datetime | None = None
        self._cache_ttl_seconds: float = 300.0

    async def route(
        self,
        task_type: TaskType,
        default_prompt_profile: str | None = None,
        default_model_profile: str = "default",
        max_cost_usd: float | None = None,
    ) -> RoutingDecision:
        best = await self._get_best_from_history(task_type)
        if best is not None:
            if max_cost_usd is not None and self._exceeds_cap(
                best.avg_cost_usd, max_cost_usd
            ):
                cheaper = await self._get_cheapest_for_task(task_type, max_cost_usd)
                if cheaper is not None:
                    return RoutingDecision(
                        selected_prompt_profile_id=cheaper.prompt_profile_id,
                        selected_model_profile_id=cheaper.model_profile_id,
                        composite_score=cheaper.composite_score,
                        estimated_cost_usd=cheaper.avg_cost_usd,
                        sample_count=cheaper.sample_count,
                        fallback=False,
                        reason="cost_constrained",
                    )
                # FAIL CLOSED (#69/#59): the best is over the cap and no cheaper
                # candidate fits under budget. Never return the over-cap best —
                # deny spend we cannot prove is in budget and fall back to the
                # safe default model.
                return RoutingDecision(
                    selected_prompt_profile_id=default_prompt_profile,
                    selected_model_profile_id=default_model_profile,
                    composite_score=0.0,
                    estimated_cost_usd=0.0,
                    sample_count=0,
                    fallback=True,
                    reason="cost_cap_no_fit",
                )
            return RoutingDecision(
                selected_prompt_profile_id=best.prompt_profile_id,
                selected_model_profile_id=best.model_profile_id,
                composite_score=best.composite_score,
                estimated_cost_usd=best.avg_cost_usd,
                sample_count=best.sample_count,
                fallback=False,
                reason="best_historical_score",
            )

        return RoutingDecision(
            selected_prompt_profile_id=default_prompt_profile,
            selected_model_profile_id=default_model_profile,
            composite_score=0.0,
            estimated_cost_usd=0.0,
            sample_count=0,
            fallback=True,
            reason="insufficient_historical_data",
        )

    @staticmethod
    def _exceeds_cap(cost: float, cap: float) -> bool:
        """True if ``cost`` is over ``cap`` — treating non-finite cost as over.

        A NaN or inf ``avg_cost`` must be treated as OVER the cap (fail closed):
        ``nan > cap`` and ``inf`` comparisons would otherwise let an unprovable
        cost slip through. Any cost we cannot prove is finite-and-under-budget is
        rejected.
        """
        if not math.isfinite(cost):
            return True
        return cost > cap

    async def _get_best_from_history(
        self, task_type: TaskType
    ) -> RoutingCandidate | None:
        if self._repo is None:
            return None
        aggregates = await self._repo.get_aggregate_scores(task_type=task_type.value)
        if not aggregates:
            return None
        candidates = []
        for agg in aggregates:
            sample_count = int(agg.get("sample_count", 0))
            if sample_count < self._min_samples:
                continue
            model_id = agg["model_profile_id"]
            if (
                self._health_tracker is not None
                # admit_probe=False: this is a candidate-filtering status read,
                # not a call attempt — it must NOT consume the single half-open
                # probe slot (that belongs to the actual gateway call).
                and not self._health_tracker.is_healthy(model_id, admit_probe=False)
            ):
                continue
            composite = float(agg.get("composite_score", 0.0))
            avg_cost = float(agg.get("avg_cost", 0.0))
            candidates.append(
                RoutingCandidate(
                    prompt_profile_id=agg.get("prompt_profile_id"),
                    model_profile_id=model_id,
                    composite_score=composite,
                    avg_cost_usd=avg_cost,
                    sample_count=sample_count,
                    task_type=task_type,
                )
            )
        if not candidates:
            return None
        adjusted = [(self._apply_quantization_penalty(c), c) for c in candidates]
        return max(adjusted, key=lambda pair: pair[0])[1]

    def _apply_quantization_penalty(self, candidate: RoutingCandidate) -> float:
        score = candidate.composite_score
        model_id = candidate.model_profile_id
        if model_id in self._quantization_map:
            _prec, confidence = self._quantization_map[model_id]
            if confidence < 0.5:
                score *= 0.6
            elif confidence < 0.7:
                score *= 0.8
        return score

    async def _get_cheapest_for_task(
        self, task_type: TaskType, max_cost: float
    ) -> RoutingCandidate | None:
        if self._repo is None:
            return None
        aggregates = await self._repo.get_aggregate_scores(task_type=task_type.value)
        candidates = []
        for agg in aggregates:
            sample_count = int(agg.get("sample_count", 0))
            if sample_count < self._min_samples:
                continue
            avg_cost = float(agg.get("avg_cost", 0.0))
            if self._exceeds_cap(avg_cost, max_cost):
                continue
            model_id = agg["model_profile_id"]
            if (
                self._health_tracker is not None
                # admit_probe=False: this is a candidate-filtering status read,
                # not a call attempt — it must NOT consume the single half-open
                # probe slot (that belongs to the actual gateway call).
                and not self._health_tracker.is_healthy(model_id, admit_probe=False)
            ):
                continue
            composite = float(agg.get("composite_score", 0.0))
            candidates.append(
                RoutingCandidate(
                    prompt_profile_id=agg.get("prompt_profile_id"),
                    model_profile_id=model_id,
                    composite_score=composite,
                    avg_cost_usd=avg_cost,
                    sample_count=sample_count,
                    task_type=task_type,
                )
            )
        if not candidates:
            return None
        return max(candidates, key=lambda c: c.composite_score)

    async def get_leaderboard(
        self, task_type: TaskType | None = None
    ) -> list[RoutingCandidate]:
        if self._repo is None:
            return []
        task_types = [task_type.value] if task_type else None
        aggregates = await self._repo.get_aggregate_scores(
            task_type=task_types[0] if task_types else None
        )
        candidates = []
        for agg in aggregates:
            sample_count = int(agg.get("sample_count", 0))
            composite = float(agg.get("composite_score", 0.0))
            avg_cost = float(agg.get("avg_cost", 0.0))
            candidates.append(
                RoutingCandidate(
                    prompt_profile_id=agg.get("prompt_profile_id"),
                    model_profile_id=agg["model_profile_id"],
                    composite_score=composite,
                    avg_cost_usd=avg_cost,
                    sample_count=sample_count,
                    task_type=TaskType(agg["task_type"]),
                )
            )
        return sorted(candidates, key=lambda c: c.composite_score, reverse=True)

    def invalidate_cache(self) -> None:
        self._cache.clear()
        self._cache_time = None
