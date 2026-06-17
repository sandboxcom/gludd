"""Budget-aware resource selection + auto-registration into UtilizationTracker.

``select_resource`` is a PURE function (no I/O) that mirrors
``scoring.router.AdaptiveRouter``'s two-pass cost-aware pick:

* Step 0 — need-match filter (gpu/vcpu/mem + SSRF guard on any endpoint_url).
* Step 1 — cost projection; an unknown/non-finite cost is treated as OVER
  budget whenever a finite cap exists (mirrors ``_exceeds_cap``).
* Step 2 — PASS 1 score: ``weights.quality*quality - weights.cost*cost_norm``,
  weights per task via ``routing_roles.weights_for`` (default 0.8/0.2).
* Step 3 — PASS 2 hard budget gate: keep ``best`` only if it fits the effective
  cap, else fall back to the cheapest-that-fits, else return None (fail closed).
* Step 4 — atomic reserve via ``SpendLimiter.try_charge(kind='infra')`` closes
  the TOCTOU; on a lost race drop-and-retry the next-cheapest-that-fits.

It NEVER picks an over-budget resource and NEVER picks an unknown-cost resource
under a configured cap.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from typing import Any

from general_ludd.infra.discovery.base import DiscoveredResource, WorkSpec
from general_ludd.routing_roles.weights import weights_for

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "disc-"


def _projected_cost(resource: DiscoveredResource, runtime_hours: float) -> float | None:
    if resource.cost_per_hour is None:
        return None
    cost = resource.cost_per_hour * runtime_hours
    if not math.isfinite(cost):
        return None
    return cost


def _exceeds_cap(cost: float | None, cap: float | None) -> bool:
    """A None/NaN/inf cost exceeds whenever a finite cap exists (fail closed)."""
    if cap is None or not math.isfinite(cap):
        # No finite cap -> nothing to gate against; unknown cost allowed.
        return False
    if cost is None or not math.isfinite(cost):
        return True
    return cost > cap


def _fits_need(r: DiscoveredResource, spec: WorkSpec) -> bool:
    if not r.available:
        return False
    if spec.needs_gpu:
        if r.gpu_count < max(spec.gpu_count, 1):
            return False
        if spec.gpu_type is not None and r.gpu_type != spec.gpu_type:
            return False
    if r.vcpu < spec.vcpu:
        return False
    if r.mem_gb < spec.mem_gb:
        return False
    if r.endpoint_url:
        # An endpoint URL that fails the SSRF guard disqualifies the candidate.
        from general_ludd.infra.discovery.base import DiscoveryProvider

        if not DiscoveryProvider.guard_endpoint(r.endpoint_url):
            return False
    return True


def _capacity_quality(r: DiscoveredResource, spec: WorkSpec) -> float:
    """Capacity proxy in [0, 1]: gpu match + prefer exact-fit (avoid waste).

    Higher is better. Exact-fit beats heavy over-provisioning; an already-warm
    endpoint earns a small bonus.
    """
    score = 0.0
    # GPU match contributes when the work needs a GPU.
    if spec.needs_gpu and r.gpu_count > 0:
        score += 0.4
        if spec.gpu_type is not None and r.gpu_type == spec.gpu_type:
            score += 0.1
    elif not spec.needs_gpu:
        score += 0.3

    # Prefer the SMALLEST sufficient box: a tighter fit scores higher.
    def _tightness(have: float, need: float) -> float:
        if need <= 0:
            return 0.1  # no requirement -> mild preference for smaller boxes
        if have <= 0:
            return 0.0
        return min(1.0, need / have)

    score += 0.25 * _tightness(float(r.vcpu), float(spec.vcpu))
    score += 0.25 * _tightness(r.mem_gb, spec.mem_gb)

    if r.endpoint_url:  # already serving -> no deploy latency
        score += 0.1
    return min(1.0, score)


def select_resource(
    work_spec: WorkSpec,
    discovered: Sequence[DiscoveredResource],
    headroom: float,
    *,
    spend_limiter: Any | None = None,
    quality_weight: float = 0.8,
    cost_weight: float = 0.2,
    runtime_hours: float = 1.0,
) -> DiscoveredResource | None:
    """Pick the best budget-fitting resource, or None (fail closed).

    ``headroom`` is the caller-computed spend headroom (USD). Pass
    ``spend_limiter`` to ATOMICALLY reserve the projected cost (closes the
    TOCTOU); without it, no reservation is made.
    """
    # Step 0 — need-match filter.
    candidates = [r for r in discovered if _fits_need(r, work_spec)]
    if not candidates:
        return None

    # Effective cap = min of the per-pick cap and the headroom over finite vals.
    caps = [
        c
        for c in (work_spec.max_cost_usd, headroom)
        if c is not None and math.isfinite(c)
    ]
    effective_cap: float | None = min(caps) if caps else None

    # Weights per task role; fall back to the supplied defaults when no TaskType.
    if work_spec.task_type is not None:
        try:
            w = weights_for(work_spec.task_type)
            q_w, c_w = w.quality, w.cost
        except Exception:
            q_w, c_w = quality_weight, cost_weight
    else:
        q_w, c_w = quality_weight, cost_weight

    # Step 1 — project costs.
    projected: dict[int, float | None] = {
        idx: _projected_cost(r, runtime_hours) for idx, r in enumerate(candidates)
    }
    finite_costs = [c for c in projected.values() if c is not None and math.isfinite(c)]
    peer_max = max(finite_costs) if finite_costs else 0.0

    # Step 2 — PASS 1 score.
    def _score(idx: int) -> float:
        r = candidates[idx]
        cost = projected[idx]
        if cost is None:
            cost_norm = 1.0  # unknown cost ranks worst on the cost axis
        elif peer_max <= 0:
            cost_norm = 0.0
        else:
            cost_norm = cost / peer_max
        quality = _capacity_quality(r, work_spec)
        return q_w * quality - c_w * cost_norm

    order = sorted(range(len(candidates)), key=_score, reverse=True)
    best_idx = order[0]

    # Step 3 — PASS 2 hard budget gate. Build the cheapest-that-fits ordering up
    # front so Step 4's drop-and-retry can walk it.
    def _fits_cap(idx: int) -> bool:
        return not _exceeds_cap(projected[idx], effective_cap)

    if _fits_cap(best_idx):
        ranked_fitting = [best_idx] + [
            i for i in order if i != best_idx and _fits_cap(i)
        ]
    else:
        # best is over budget: fall back to cheapest-that-fits.
        fitting = [i for i in range(len(candidates)) if _fits_cap(i)]
        if not fitting:
            logger.info(
                "select_resource: no candidate fits cap=%s (fail closed)",
                effective_cap,
            )
            return None
        def _cost_key(i: int) -> float:
            c = projected[i]
            return c if c is not None else math.inf

        ranked_fitting = sorted(fitting, key=_cost_key)

    if not ranked_fitting:
        return None

    # Step 4 — atomic reserve (drop-and-retry on a lost race / refused charge).
    for idx in ranked_fitting:
        pick = candidates[idx]
        if spend_limiter is None:
            return pick
        cost = projected[idx]
        charged = spend_limiter.try_charge(
            cost,
            kind="infra",
            model=work_spec.model,
            project_id=work_spec.project_id,
        )
        if charged:
            return pick
        logger.info(
            "select_resource: try_charge refused %s (cost=%s) — trying next",
            pick.id,
            cost,
        )
    return None


def _slots_for(resource: DiscoveredResource) -> int:
    """Concurrency slots for a registered endpoint (>=1)."""
    if resource.gpu_count > 0:
        return max(1, resource.gpu_count * 4)
    return max(1, resource.vcpu // 2 or 1)


def register_discovered(
    tracker: Any,
    pick: DiscoveredResource,
    work_spec: WorkSpec,
) -> dict[str, Any]:
    """Register a picked resource into ``UtilizationTracker``.

    Only registers when ``pick.endpoint_url`` is a non-empty, SSRF-safe URL;
    otherwise returns a ``needs_deploy`` marker and registers NOTHING (never a
    dead ``url=''`` endpoint). DEDUPE: if the endpoint_id already exists and is
    active, metadata is left as-is (current_load/total_requests preserved) so a
    refresh mid-load does not reset ``route_task``'s least-utilized math.
    """
    from general_ludd.infra.discovery.base import DiscoveryProvider

    if not pick.endpoint_url or not DiscoveryProvider.guard_endpoint(pick.endpoint_url):
        return {
            "registered": False,
            "needs_deploy": True,
            "resource_id": pick.id,
            "source": pick.source.value,
            "reason": "no SSRF-safe endpoint_url (deploy required before routing)",
        }

    endpoint_id = f"{_CACHE_PREFIX}{pick.source.value}-{pick.id}"
    existing = tracker.get_endpoint(endpoint_id)
    if existing is not None and getattr(existing, "active", False):
        # Dedupe: keep live counters, do not blindly overwrite the endpoint.
        return {
            "registered": True,
            "deduped": True,
            "endpoint_id": endpoint_id,
            "url": existing.url,
        }

    ep = tracker.register_endpoint(
        endpoint_id=endpoint_id,
        url=pick.endpoint_url,
        model=work_spec.model,
        gpu_type=str(pick.gpu_type or ""),
        gpu_count=pick.gpu_count,
        max_concurrent=_slots_for(pick),
    )
    return {
        "registered": True,
        "deduped": False,
        "endpoint_id": ep.endpoint_id,
        "url": ep.url,
    }
