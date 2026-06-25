"""Adaptive router — selects best prompt+model combo based on historical benchmark scores."""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, ClassVar

from general_ludd.routing_roles import weights_for
from general_ludd.schemas.benchmark import (
    RoutingCandidate,
    RoutingDecision,
    TaskType,
)
from general_ludd.scoring.task_embeddings import TaskEmbeddingStore

log = logging.getLogger(__name__)


class AdaptiveRouter:
    # Base project-relationship weight per relation type (own=1.0 ≥ parent ≥
    # sibling ≥ child ≥ external). Own-project history always carries weight 1.0
    # so a borrowed pick can never outrank an equally-scored own pick.
    _REL_BASE: ClassVar[dict[str, float]] = {
        "own": 1.0,
        "parent": 0.8,
        "sibling": 0.7,
        "child": 0.6,
        "external": 0.4,
    }

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
        project_id: str | None = None,
        relationship_repo: Any | None = None,
        enable_cross_project_borrowing: bool = False,
        edge_decay: float = 0.5,
        external_penalty: float = 0.5,
        min_borrow_weight: float = 0.05,
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
        # Project-hierarchy phase 3 (cross-project knowledge borrowing). All
        # default OFF/None so an unconfigured router behaves EXACTLY as before:
        # project_id=None feeds a global get_aggregate_scores, and the borrow
        # path is gated entirely behind enable_cross_project_borrowing.
        self._project_id = project_id
        self._relationship_repo = relationship_repo
        self._enable_cross_project_borrowing = enable_cross_project_borrowing
        self._edge_decay = edge_decay
        self._external_penalty = external_penalty
        self._min_borrow_weight = min_borrow_weight
        self._cache: dict[str, RoutingDecision] = {}
        self._cache_time: datetime | None = None
        self._cache_ttl_seconds: float = 300.0
        # When the winning candidate from the most recent history lookup was
        # BORROWED from a related project, this holds "inherited_<relation>_history"
        # so route() can report it; None means the winner was own/global history.
        self._last_borrow_reason: str | None = None

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
                    self._last_borrow_reason
                    if self._last_borrow_reason is not None
                    else "best_historical_score_similarity"
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

    async def _aggregate_scores(
        self, task_type: str | None, project_id: str | None
    ) -> list[dict[str, Any]]:
        """Call ``get_aggregate_scores``, omitting the ``project_id`` kwarg when
        it is None.

        BACKWARD COMPAT: a router with no project (the default) calls the repo
        with EXACTLY ``get_aggregate_scores(task_type=...)`` — the identical
        signature used before phase 3 — so existing tests that assert the call
        shape (``assert_called_with(task_type=...)``) keep passing unchanged.
        The ``project_id`` kwarg is only added when a project is actually set.
        """
        if project_id is None:
            return await self._repo.get_aggregate_scores(task_type=task_type)  # type: ignore[union-attr,no-any-return]
        return await self._repo.get_aggregate_scores(  # type: ignore[union-attr,no-any-return]
            task_type=task_type, project_id=project_id
        )

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
        # Own (or global when project_id is None) history. project_id=None
        # reproduces today's global query exactly — backward compatible.
        aggregates = await self._aggregate_scores(task_type.value, self._project_id)
        own_sample_total = sum(int(a.get("sample_count", 0)) for a in aggregates)
        self._last_borrow_reason = None
        # weighted entries carry (candidate, quality, borrow_reason). Own picks
        # use borrow_reason=None and weight = quantization-penalized composite.
        # Borrowed picks (only when borrowing is ON) carry their inherited reason.
        # When borrowing is OFF only own picks are added → behaviour identical
        # to before phase 3.
        weighted: list[tuple[RoutingCandidate, float, str | None]] = []
        for agg in aggregates:
            cand = self._candidate_from_agg(agg, task_type)
            if cand is None:
                continue
            weighted.append((cand, self._apply_quantization_penalty(cand), None))

        # Borrow path: ONLY when borrowing is ON and own history is thin. When
        # OFF this entire block is skipped → behaviour identical to before.
        if (
            self._enable_cross_project_borrowing
            and own_sample_total < self._min_samples
        ):
            rel_map = await self._build_relationship_map()
            for neighbor_id, (rel, _dist, _ctl) in rel_map.items():
                borrowed = await self._aggregate_scores(
                    task_type.value, neighbor_id
                )
                for agg in borrowed:
                    cand = self._candidate_from_agg(agg, task_type)
                    if cand is None:
                        continue
                    weight = self._composite_similarity_weight(
                        1.0, neighbor_id, rel_map
                    )
                    if weight <= 0.0:
                        continue
                    weighted.append(
                        (
                            cand,
                            self._apply_quantization_penalty(cand) * weight,
                            f"inherited_{rel}_history",
                        )
                    )

        if not weighted:
            return None
        max_cost = max((c.avg_cost_usd for c, _, _ in weighted), default=0.0)
        ranked = [
            (self._cost_adjusted_rank(c, q, max_cost), c, reason)
            for c, q, reason in weighted
        ]
        _best_rank, best_cand, best_reason = max(ranked, key=lambda t: t[0])
        self._last_borrow_reason = best_reason
        return best_cand

    def _candidate_from_agg(
        self, agg: dict[str, Any], task_type: TaskType
    ) -> RoutingCandidate | None:
        """Build a RoutingCandidate from an aggregate row, applying the
        min-samples and health filters. Returns None if filtered out."""
        sample_count = int(agg.get("sample_count", 0))
        if sample_count < self._min_samples:
            return None
        model_id = agg["model_profile_id"]
        if (
            self._health_tracker is not None
            # admit_probe=False: candidate-filtering status read, not a call
            # attempt — must NOT consume the single half-open probe slot.
            and not self._health_tracker.is_healthy(model_id, admit_probe=False)
        ):
            return None
        return RoutingCandidate(
            prompt_profile_id=agg.get("prompt_profile_id"),
            model_profile_id=model_id,
            composite_score=float(agg.get("composite_score", 0.0)),
            avg_cost_usd=float(agg.get("avg_cost", 0.0)),
            sample_count=sample_count,
            task_type=task_type,
        )

    def _similarity_weight(self, similarity: float) -> float:
        """Quality multiplier applied to a candidate based on task-type similarity.

        ``similarity_floor + similarity_alpha * similarity``. Exact-match
        candidates (similarity=1.0) receive the full ``floor + alpha``; cross-type
        candidates receive a fraction scaled by cosine similarity. With defaults
        (alpha=1.0, floor=0.0) exact-match weight is 1.0 and a perfectly-similar
        neighbor's weight approaches 1.0.
        """
        return self._similarity_floor + self._similarity_alpha * similarity

    def _project_rel_weight(
        self, relation_type: str, edge_distance: int, controlled: bool
    ) -> float:
        """Project-relationship axis weight for a borrowed candidate.

        ``base[relation] * edge_decay**(distance-1) * control_factor`` where
        ``control_factor`` is 1.0 for a gludd-controlled neighbor and
        ``external_penalty`` otherwise. Own-project (relation ``"own"``) is base
        1.0, distance 0 → weight 1.0. Unknown relation types fall back to the
        ``external`` base (most conservative).
        """
        base = self._REL_BASE.get(relation_type, self._REL_BASE["external"])
        dist_exp = max(0, edge_distance - 1)
        control_factor = 1.0 if controlled else self._external_penalty
        return base * (self._edge_decay**dist_exp) * control_factor

    def _composite_similarity_weight(
        self,
        task_similarity: float,
        candidate_project_id: str | None,
        relationship_map: dict[str, tuple[str, int, bool]] | None = None,
    ) -> float:
        """Quality multiplier combining the task-type axis and the project axis.

        BACKWARD-COMPAT: returns ``_similarity_weight(task_similarity)`` ALONE —
        i.e. exactly today's behaviour — when ANY of the following holds:
          * cross-project borrowing is OFF (the default), or
          * the candidate belongs to this router's own project (or own/global
            history where project_id matches / both are None), or
          * no ``relationship_map`` was supplied.

        Otherwise the borrowed candidate's task weight is multiplied by the
        project-relationship weight derived from the map entry
        ``(relation_type, edge_distance, controlled)``. A final multiplier below
        ``min_borrow_weight`` collapses to 0.0 so very distant / uncontrolled
        edges are dropped rather than adding noise.
        """
        task_w = self._similarity_weight(task_similarity)
        if (
            not self._enable_cross_project_borrowing
            or relationship_map is None
            or candidate_project_id == self._project_id
        ):
            return task_w
        entry = relationship_map.get(candidate_project_id) if candidate_project_id else None
        if entry is None:
            # Not a declared neighbor — own-project weight 1.0 (no borrowing).
            return task_w
        relation_type, edge_distance, controlled = entry
        rel_w = self._project_rel_weight(relation_type, edge_distance, controlled)
        composite = task_w * rel_w
        if composite < self._min_borrow_weight:
            return 0.0
        return composite

    async def _build_relationship_map(
        self,
    ) -> dict[str, tuple[str, int, bool]]:
        """BFS the declared project graph from ``self._project_id``.

        Returns ``{neighbor_project_id: (relation_type, edge_distance, controlled)}``
        for every RESOLVED gludd-project neighbor reachable through declared
        edges, up to a small depth bound, using the REAL
        ``ProjectRelationshipRepository`` methods (``list_for_project`` /
        ``get_parent`` / ``list_children``). Cycle-safe via a visited set. The
        nearest edge wins when a project is reachable by multiple paths. Returns
        an empty map when borrowing is off, there is no project / repo, or the
        project has no resolved neighbors.
        """
        if (
            not self._enable_cross_project_borrowing
            or self._project_id is None
            or self._relationship_repo is None
        ):
            return {}
        result: dict[str, tuple[str, int, bool]] = {}
        visited: set[str] = {self._project_id}
        # frontier entries: (project_id, relation_type_to_reach_it, distance)
        frontier: list[tuple[str, str, int]] = [(self._project_id, "own", 0)]
        max_depth = 3
        while frontier:
            cur_id, _cur_rel, cur_dist = frontier.pop(0)
            if cur_dist >= max_depth:
                continue
            try:
                edges = await self._relationship_repo.list_for_project(cur_id)
            except Exception:  # repo failure must never break routing
                log.debug("relationship_map: list_for_project failed for %s", cur_id)
                continue
            for edge in edges:
                neighbor_id = getattr(edge, "related_project_id", None)
                if not neighbor_id or neighbor_id in visited:
                    continue
                relation_type = getattr(edge, "relation_type", "external")
                controlled = bool(getattr(edge, "controlled_by_gludd", False))
                dist = cur_dist + 1
                visited.add(neighbor_id)
                # The relation type recorded is the DIRECT edge's type. Beyond
                # the first hop the relation is treated as its own type at the
                # accumulated distance (decay handles the dilution).
                result[neighbor_id] = (relation_type, dist, controlled)
                frontier.append((neighbor_id, relation_type, dist))
        return result

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
        # Own (project_id=self._project_id) view of ALL task types. When
        # borrowing is OFF and project_id is None this is the exact global query
        # used before phase 3 — backward compatible. relationship_map stays None
        # unless borrowing is ON, so _composite_similarity_weight collapses to
        # the plain task-similarity weight (today's behaviour).
        aggregates = await self._aggregate_scores(None, self._project_id)
        own_sample_total = sum(int(a.get("sample_count", 0)) for a in aggregates)
        relationship_map: dict[str, tuple[str, int, bool]] | None = None
        borrowed_aggs: list[dict[str, Any]] = []
        if (
            self._enable_cross_project_borrowing
            and own_sample_total < self._min_samples
        ):
            relationship_map = await self._build_relationship_map()
            for neighbor_id in relationship_map:
                borrowed_aggs.extend(
                    await self._aggregate_scores(None, neighbor_id)
                )
        all_aggs = list(aggregates) + borrowed_aggs
        if not all_aggs:
            return None
        self._last_borrow_reason = None
        weighted: list[tuple[RoutingCandidate, float, str | None]] = []
        for agg in all_aggs:
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
            cand_project = agg.get("project_id")
            weight = self._composite_similarity_weight(
                similarity, cand_project, relationship_map
            )
            if weight <= 0.0:
                continue
            borrow_reason: str | None = None
            if (
                relationship_map is not None
                and cand_project is not None
                and cand_project != self._project_id
                and cand_project in relationship_map
            ):
                borrow_reason = f"inherited_{relationship_map[cand_project][0]}_history"
            quality = self._apply_quantization_penalty(candidate) * weight
            weighted.append((candidate, quality, borrow_reason))
        if not weighted:
            return None
        max_cost = max((c.avg_cost_usd for c, _, _ in weighted), default=0.0)
        ranked = [
            (self._cost_adjusted_rank(c, q, max_cost), c, reason)
            for c, q, reason in weighted
        ]
        _best_rank, best_cand, best_reason = max(ranked, key=lambda t: t[0])
        self._last_borrow_reason = best_reason
        return best_cand

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
        # project_id=None (default) reproduces today's global cheapest query.
        aggregates = await self._aggregate_scores(task_type.value, self._project_id)
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
