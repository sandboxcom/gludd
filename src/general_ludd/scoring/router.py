"""Adaptive router — selects best prompt+model combo based on historical benchmark scores."""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any

from general_ludd.routing_roles import weights_for
from general_ludd.schemas.benchmark import (
    RoutingCandidate,
    RoutingDecision,
    TaskType,
)
from general_ludd.scoring.task_embeddings import TaskEmbeddingStore

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
        embedding_store: TaskEmbeddingStore | None = None,
        similarity_alpha: float = 1.0,
        similarity_floor: float = 0.0,
    ) -> None:
        self._repo = benchmark_repo
        self._min_samples = min_samples
        self._cost_weight = cost_weight
        self._quality_weight = quality_weight
        self._quantization_map = quantization_map or {}
        self._health_tracker = health_tracker
        self._embedding_store = embedding_store
        self._similarity_alpha = similarity_alpha
        self._similarity_floor = similarity_floor
        self._cache: dict[str, RoutingDecision] = {}
        self._cache_time: datetime | None = None
        self._cache_ttl_seconds: float = 300.0

    def _cache_key(self, task_type: TaskType, max_cost_usd: float | None) -> str:
        """Cache key incorporates task type and cost cap so different constraints
        never share a cached decision."""
        return f"{task_type.value}:{max_cost_usd}"

    def _cache_valid(self) -> bool:
        """True if there is a cache timestamp within the TTL window."""
        if self._cache_time is None:
            return False
        elapsed = (datetime.now() - self._cache_time).total_seconds()
        return elapsed < self._cache_ttl_seconds

    async def route(
        self,
        task_type: TaskType,
        default_prompt_profile: str | None = None,
        default_model_profile: str = "default",
        max_cost_usd: float | None = None,
    ) -> RoutingDecision:
        cache_key = self._cache_key(task_type, max_cost_usd)
        if self._cache_valid() and cache_key in self._cache:
            cached = self._cache[cache_key]
            if self._health_tracker is not None:
                model_id = cached.selected_model_profile_id
                if not self._health_tracker.is_healthy(model_id, admit_probe=False):
                    log.debug(
                        "route(): cache hit for key=%s but model=%s is unhealthy,"
                        " dropping cache entry and recomputing",
                        cache_key,
                        model_id,
                    )
                    del self._cache[cache_key]
                else:
                    log.debug("route(): cache hit for key=%s", cache_key)
                    return cached
            else:
                log.debug("route(): cache hit for key=%s", cache_key)
                return cached

        best = await self._get_best_from_history(task_type)
        if best is not None:
            if max_cost_usd is not None and self._exceeds_cap(
                best.avg_cost_usd, max_cost_usd
            ):
                cheaper = await self._get_cheapest_for_task(task_type, max_cost_usd)
                if cheaper is not None:
                    decision = RoutingDecision(
                        selected_prompt_profile_id=cheaper.prompt_profile_id,
                        selected_model_profile_id=cheaper.model_profile_id,
                        composite_score=cheaper.composite_score,
                        estimated_cost_usd=cheaper.avg_cost_usd,
                        sample_count=cheaper.sample_count,
                        fallback=False,
                        reason="cost_constrained",
                    )
                    return self._cache_and_return(cache_key, decision)
                # FAIL CLOSED (#69/#59): the best is over the cap and no cheaper
                # candidate fits under budget. Never return the over-cap best —
                # deny spend we cannot prove is in budget and fall back to the
                # safe default model.
                decision = RoutingDecision(
                    selected_prompt_profile_id=default_prompt_profile,
                    selected_model_profile_id=default_model_profile,
                    composite_score=0.0,
                    estimated_cost_usd=0.0,
                    sample_count=0,
                    fallback=True,
                    reason="cost_cap_no_fit",
                )
                return self._cache_and_return(cache_key, decision)
            decision = RoutingDecision(
                selected_prompt_profile_id=best.prompt_profile_id,
                selected_model_profile_id=best.model_profile_id,
                composite_score=best.composite_score,
                estimated_cost_usd=best.avg_cost_usd,
                sample_count=best.sample_count,
                fallback=False,
                reason=(
                    "best_historical_score_similarity"
                    if best.task_type != task_type
                    else "best_historical_score"
                ),
            )
            return self._cache_and_return(cache_key, decision)

        decision = RoutingDecision(
            selected_prompt_profile_id=default_prompt_profile,
            selected_model_profile_id=default_model_profile,
            composite_score=0.0,
            estimated_cost_usd=0.0,
            sample_count=0,
            fallback=True,
            reason="insufficient_historical_data",
        )
        return self._cache_and_return(cache_key, decision)

    def _cache_and_return(self, key: str, decision: RoutingDecision) -> RoutingDecision:
        """Write *decision* to the in-memory cache and return it."""
        self._cache[key] = decision
        self._cache_time = datetime.now()
        return decision

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
        if self._embedding_store is not None:
            # Tier 2 RAG: borrow strength from neighboring task types via
            # cosine similarity. If the query task has no embedding (KeyError),
            # silently fall through to the exact-match path so the router is
            # always usable even with a partially-seeded store.
            try:
                sims = await self._embedding_store.similarity_to(task_type)
            except KeyError:
                sims = None
            if sims is not None:
                return await self._get_best_with_embeddings(task_type, sims)
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
        max_cost = max((c.avg_cost_usd for c in candidates), default=0.0)
        ranked = [
            (
                self._cost_adjusted_rank(
                    c, self._apply_quantization_penalty(c), max_cost
                ),
                c,
            )
            for c in candidates
        ]
        return max(ranked, key=lambda pair: pair[0])[1]

    def _similarity_weight(self, similarity: float) -> float:
        """Quality multiplier applied to a candidate based on task-type similarity.

        ``similarity_floor + similarity_alpha * similarity``. Exact-match
        candidates (similarity=1.0) receive the full ``floor + alpha``; cross-type
        candidates receive a fraction scaled by cosine similarity. With defaults
        (alpha=1.0, floor=0.0) exact-match weight is 1.0 and a perfectly-similar
        neighbor's weight approaches 1.0.
        """
        return self._similarity_floor + self._similarity_alpha * similarity

    async def _get_best_with_embeddings(
        self,
        task_type: TaskType,
        sims: dict[str, float],
    ) -> RoutingCandidate | None:
        """Cross-task-type candidate selection weighted by embedding similarity.

        Queries ALL task types (``task_type=None``) rather than just the exact
        match, then scales each candidate's quality by ``_similarity_weight`` so
        that neighbors can lend evidence to the routing decision when direct
        history is thin. The returned candidate keeps its RAW ``composite_score``
        — the weighting affects ranking only, not the reported score.
        """
        aggregates = await self._repo.get_aggregate_scores(task_type=None)  # type: ignore[union-attr]
        if not aggregates:
            return None
        weighted: list[tuple[RoutingCandidate, float]] = []
        for agg in aggregates:
            sample_count = int(agg.get("sample_count", 0))
            if sample_count < self._min_samples:
                continue
            model_id = agg["model_profile_id"]
            if (
                self._health_tracker is not None
                and not self._health_tracker.is_healthy(model_id, admit_probe=False)
            ):
                continue
            agg_task_str = agg.get("task_type", task_type.value)
            try:
                agg_task_type = TaskType(agg_task_str)
            except ValueError:
                continue
            composite = float(agg.get("composite_score", 0.0))
            avg_cost = float(agg.get("avg_cost", 0.0))
            similarity = (
                1.0 if agg_task_str == task_type.value else sims.get(agg_task_str, 0.0)
            )
            candidate = RoutingCandidate(
                prompt_profile_id=agg.get("prompt_profile_id"),
                model_profile_id=model_id,
                composite_score=composite,
                avg_cost_usd=avg_cost,
                sample_count=sample_count,
                task_type=agg_task_type,
            )
            quality = self._apply_quantization_penalty(candidate) * (
                self._similarity_weight(similarity)
            )
            weighted.append((candidate, quality))
        if not weighted:
            return None
        max_cost = max((c.avg_cost_usd for c, _ in weighted), default=0.0)
        ranked = [
            (self._cost_adjusted_rank(c, q, max_cost), c) for c, q in weighted
        ]
        return max(ranked, key=lambda pair: pair[0])[1]

    @staticmethod
    def _cost_adjusted_rank(
        candidate: RoutingCandidate,
        quality: float,
        max_cost: float,
    ) -> float:
        """Rank key: quality reward minus normalized-cost penalty, per task role.

        Internal RANKING key only — does NOT mutate composite_score.
        """
        weights = weights_for(candidate.task_type)
        cost = candidate.avg_cost_usd
        cost_norm = (cost / max_cost) if (max_cost > 0 and math.isfinite(cost)) else 0.0
        return weights.quality * quality - weights.cost * cost_norm

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
