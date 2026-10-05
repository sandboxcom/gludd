"""Bounded, budget-aware orchestration for injected compute probes.

This module extends :mod:`general_ludd.infra.discovery` without importing a
cloud SDK.  Callers inject the probes they already own, while this layer adds
structured outcomes, concurrent bounded refresh, a last-good TTL, a per-probe
circuit breaker, cost-safe selection, and explicit registration into the
existing utilization tracker.

Discovery never starts a background task and registration is never implicit.
Those properties make refreshes safe to deploy with zero downtime: an old
process can continue routing its existing endpoint set while a new process
warms discovery state, and failed refreshes cannot silently erase that state.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Protocol

from general_ludd.connectors.base import is_safe_endpoint
from general_ludd.infra.discovery import DiscoveredResource

logger = logging.getLogger(__name__)


class DiscoveryStatus(StrEnum):
    """Stable, non-secret status for one provider refresh."""

    OK = "ok"
    PARTIAL = "partial"
    OFFLINE = "offline"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    AUTH_FAILED = "auth_failed"
    ERROR = "error"


@dataclass(frozen=True)
class DiscoveryResult:
    """Structured result returned by every discovery operation."""

    provider: str
    status: DiscoveryStatus
    resources: tuple[DiscoveredResource, ...] = ()
    error: str | None = None
    from_cache: bool = False
    meta: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Return whether the provider supplied usable current resources."""
        return self.status in (DiscoveryStatus.OK, DiscoveryStatus.PARTIAL)


@dataclass(frozen=True)
class WorkSpec:
    """Capacity and spend constraints for one resource selection."""

    model: str = ""
    task_type: object | None = None
    needs_gpu: bool = False
    gpu: str | None = None
    gpu_count: int = 0
    cpu: float = 0.0
    mem_gb: float = 0.0
    max_cost_usd: float | None = None
    project_id: str | None = None


class SpendLimiterLike(Protocol):
    """Narrow atomic charge contract used by :func:`select_resource`."""

    def try_charge(
        self,
        cost_usd: float | None,
        *,
        kind: str,
        model: str | None = None,
        project_id: str | None = None,
    ) -> bool:
        """Atomically accept and record a projected charge when it fits."""


class _CircuitBreaker:
    """Small thread-safe closed/open/half-open provider circuit breaker."""

    def __init__(
        self,
        *,
        failure_threshold: int,
        cooldown_s: float,
        clock: Callable[[], float],
    ) -> None:
        self._threshold = max(1, failure_threshold)
        self._cooldown_s = max(0.0, cooldown_s)
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False
        self._lock = threading.Lock()

    def allow(self) -> bool:
        """Admit closed calls and exactly one half-open probe after cooldown."""
        with self._lock:
            if self._opened_at is None:
                return True
            if self._clock() - self._opened_at < self._cooldown_s:
                return False
            if self._probe_in_flight:
                return False
            self._probe_in_flight = True
            return True

    def success(self) -> None:
        """Close the breaker after a successful provider call."""
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._probe_in_flight = False

    def failure(self) -> None:
        """Record a failure and open the breaker at the configured threshold."""
        with self._lock:
            self._failures += 1
            self._probe_in_flight = False
            if self._failures >= self._threshold:
                self._opened_at = self._clock()


def _projected_cost(resource: DiscoveredResource, runtime_hours: float) -> float | None:
    rate = resource.cost_per_hour
    if rate is None or not math.isfinite(rate) or rate < 0.0:
        return None
    return rate * runtime_hours


def _fits_need(resource: DiscoveredResource, work: WorkSpec) -> bool:
    if not resource.available:
        return False
    if work.needs_gpu:
        if resource.gpu_count < max(1, work.gpu_count):
            return False
        if work.gpu is not None and resource.gpu.casefold() != work.gpu.casefold():
            return False
    if resource.cpu < work.cpu or resource.mem_gb < work.mem_gb:
        return False
    return not resource.endpoint_url or is_safe_endpoint(resource.endpoint_url)


def _capacity_quality(resource: DiscoveredResource, work: WorkSpec) -> float:
    score = 0.4 if work.needs_gpu and resource.gpu_count else 0.3
    if work.gpu is not None and resource.gpu.casefold() == work.gpu.casefold():
        score += 0.1

    def tightness(have: float, need: float) -> float:
        if need <= 0.0:
            return 0.1
        if have <= 0.0:
            return 0.0
        return min(1.0, need / have)

    score += 0.25 * tightness(resource.cpu, work.cpu)
    score += 0.25 * tightness(resource.mem_gb, work.mem_gb)
    if resource.endpoint_url:
        score += 0.1
    return min(1.0, score)


def select_resource(
    work: WorkSpec,
    discovered: Sequence[DiscoveredResource],
    headroom: float,
    *,
    spend_limiter: SpendLimiterLike | None = None,
    quality_weight: float = 0.8,
    cost_weight: float = 0.2,
    runtime_hours: float = 1.0,
) -> DiscoveredResource | None:
    """Return the best resource that satisfies capacity and atomic spend gates.

    Unknown, negative, or non-finite costs are rejected whenever a finite cap
    exists.  When a limiter is supplied, candidates are charged atomically in
    rank order; a race-lost candidate is dropped and the next fit is tried.
    """
    if runtime_hours <= 0.0 or not math.isfinite(runtime_hours):
        return None
    candidates = [resource for resource in discovered if _fits_need(resource, work)]
    if not candidates:
        return None

    caps = [
        cap
        for cap in (work.max_cost_usd, headroom)
        if cap is not None and math.isfinite(cap)
    ]
    effective_cap = min(caps) if caps else None
    projected = [_projected_cost(resource, runtime_hours) for resource in candidates]
    finite_costs = [cost for cost in projected if cost is not None]
    peer_max = max(finite_costs, default=0.0)

    q_weight, c_weight = quality_weight, cost_weight
    if work.task_type is not None:
        try:
            from general_ludd.routing_roles.weights import weights_for
            from general_ludd.schemas.benchmark import TaskType

            task_type = (
                work.task_type
                if isinstance(work.task_type, TaskType)
                else TaskType(str(work.task_type))
            )
            weights = weights_for(task_type)
            q_weight, c_weight = weights.quality, weights.cost
        except (AttributeError, KeyError, TypeError, ValueError):
            logger.debug("using default discovery weights for unknown task type")

    def fits_cap(index: int) -> bool:
        cost = projected[index]
        if effective_cap is None:
            return True
        return cost is not None and cost <= effective_cap

    def score(index: int) -> float:
        cost = projected[index]
        if cost is None:
            normalized_cost = 1.0
        elif peer_max <= 0.0:
            normalized_cost = 0.0
        else:
            normalized_cost = cost / peer_max
        return (
            q_weight * _capacity_quality(candidates[index], work)
            - c_weight * normalized_cost
        )

    ranked = sorted(range(len(candidates)), key=score, reverse=True)
    ranked_fits = [index for index in ranked if fits_cap(index)]
    if not ranked_fits:
        logger.info(
            "compute discovery selection found no candidate within cap=%s",
            effective_cap,
        )
        return None

    for index in ranked_fits:
        candidate = candidates[index]
        if spend_limiter is None:
            return candidate
        if spend_limiter.try_charge(
            projected[index],
            kind="infra",
            model=work.model,
            project_id=work.project_id,
        ):
            return candidate
        logger.info(
            "compute discovery atomic charge refused provider=%s resource=%s",
            candidate.provider,
            candidate.id or candidate.kind,
        )
    return None


def _slots_for(resource: DiscoveredResource) -> int:
    if resource.gpu_count > 0:
        return max(1, resource.gpu_count * 4)
    return max(1, int(resource.cpu) // 2 or 1)


def register_discovered(
    tracker: Any,
    candidate: DiscoveredResource,
    work: WorkSpec,
) -> dict[str, object]:
    """Explicitly register one public endpoint without resetting live load.

    A missing or SSRF-unsafe URL produces a ``needs_deploy`` result and leaves
    the tracker untouched.  Repeated refreshes preserve an active endpoint's
    counters instead of overwriting the object mid-request.
    """
    resource_id = candidate.id or candidate.kind
    if not candidate.endpoint_url or not is_safe_endpoint(candidate.endpoint_url):
        return {
            "registered": False,
            "needs_deploy": True,
            "resource_id": resource_id,
            "provider": candidate.provider,
            "reason": "no SSRF-safe endpoint_url (deploy required before routing)",
        }

    endpoint_id = f"disc-{candidate.provider}-{resource_id}"
    existing = tracker.get_endpoint(endpoint_id)
    if existing is not None and getattr(existing, "active", False):
        return {
            "registered": True,
            "deduped": True,
            "endpoint_id": endpoint_id,
            "url": existing.url,
        }
    endpoint = tracker.register_endpoint(
        endpoint_id=endpoint_id,
        url=candidate.endpoint_url,
        model=work.model,
        gpu_type=candidate.gpu,
        gpu_count=candidate.gpu_count,
        max_concurrent=_slots_for(candidate),
    )
    return {
        "registered": True,
        "deduped": False,
        "endpoint_id": endpoint.endpoint_id,
        "url": endpoint.url,
    }


class DiscoveryService:
    """Orchestrate injected probes with bounded refresh and last-good state."""

    def __init__(
        self,
        *,
        registry: Mapping[str, object],
        tracker: Any | None = None,
        cache_ttl_s: float = 900.0,
        timeout_s: float = 10.0,
        breaker_threshold: int = 3,
        breaker_cooldown_s: float = 60.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Initialize bounded provider, cache, breaker, and tracker state."""
        self._registry = dict(registry)
        self._tracker = tracker
        self._cache_ttl_s = max(0.0, cache_ttl_s)
        self._timeout_s = max(0.001, timeout_s)
        self._clock = clock or time.monotonic
        self._cache: dict[str, tuple[float, DiscoveryResult]] = {}
        self._cache_lock = threading.Lock()
        self._breakers = {
            provider: _CircuitBreaker(
                failure_threshold=breaker_threshold,
                cooldown_s=breaker_cooldown_s,
                clock=self._clock,
            )
            for provider in self._registry
        }

    @property
    def providers(self) -> tuple[str, ...]:
        """Return registered provider names in deterministic insertion order."""
        return tuple(self._registry)

    def _put_cache(self, result: DiscoveryResult) -> None:
        with self._cache_lock:
            self._cache[result.provider] = (self._clock(), result)

    def _get_cache(self, provider: str) -> DiscoveryResult | None:
        with self._cache_lock:
            entry = self._cache.get(provider)
            if entry is None:
                return None
            stored_at, result = entry
            age = self._clock() - stored_at
            if age > self._cache_ttl_s:
                del self._cache[provider]
                return None
            meta = dict(result.meta)
            meta["stale_age_s"] = age
            return replace(result, from_cache=True, meta=meta)

    async def _invoke(self, provider: str, probe: object) -> DiscoveryResult:
        discover = getattr(probe, "discover", None)
        probe_method = getattr(probe, "probe", None)
        if callable(discover) and inspect.iscoroutinefunction(discover):
            raw = await asyncio.wait_for(discover(), timeout=self._timeout_s)
        elif callable(probe_method):
            raw = await asyncio.wait_for(
                asyncio.to_thread(probe_method),
                timeout=self._timeout_s,
            )
        elif callable(discover):
            raw = await asyncio.wait_for(
                asyncio.to_thread(discover),
                timeout=self._timeout_s,
            )
        else:
            raise TypeError("provider has no probe or discover operation")

        if isinstance(raw, DiscoveryResult):
            return replace(raw, provider=provider)
        resources = tuple(raw)
        if not all(isinstance(item, DiscoveredResource) for item in resources):
            raise TypeError("provider returned an invalid resource")
        return DiscoveryResult(provider, DiscoveryStatus.OK, resources)

    async def discover(self, provider: str) -> DiscoveryResult:
        """Refresh one provider, returning a result or valid last-good entry."""
        probe = self._registry.get(provider)
        if probe is None:
            return DiscoveryResult(
                provider,
                DiscoveryStatus.ERROR,
                error="no probe registered for provider",
            )
        breaker = self._breakers[provider]
        if not breaker.allow():
            cached = self._get_cache(provider)
            return cached or DiscoveryResult(
                provider,
                DiscoveryStatus.OFFLINE,
                error="circuit breaker open; no cached result",
            )

        try:
            result = await self._invoke(provider, probe)
        except TimeoutError:
            result = DiscoveryResult(
                provider,
                DiscoveryStatus.OFFLINE,
                error="probe timeout",
            )
        except Exception as exc:
            logger.warning(
                "compute discovery provider=%s failed type=%s",
                provider,
                type(exc).__name__,
            )
            result = DiscoveryResult(
                provider,
                DiscoveryStatus.ERROR,
                error=type(exc).__name__,
            )

        if result.ok:
            breaker.success()
            self._put_cache(result)
            return result
        breaker.failure()
        if result.status in (DiscoveryStatus.ERROR, DiscoveryStatus.OFFLINE):
            return self._get_cache(provider) or result
        return result

    async def discover_all(self) -> dict[str, DiscoveryResult]:
        """Refresh every provider concurrently and emit one bounded heartbeat."""
        providers = tuple(self._registry)
        results = await asyncio.gather(*(self.discover(name) for name in providers))
        output = dict(zip(providers, results, strict=True))
        logger.info(
            "compute discovery refresh tick: %d providers (%d ok, %d from cache)",
            len(output),
            sum(result.ok for result in output.values()),
            sum(result.from_cache for result in output.values()),
        )
        return output

    def cached(self, provider: str) -> DiscoveryResult | None:
        """Return one valid last-good result, marked as cached."""
        return self._get_cache(provider)

    def all_cached_resources(self) -> list[DiscoveredResource]:
        """Return all resources whose last-good TTL has not expired."""
        resources: list[DiscoveredResource] = []
        for provider in self._registry:
            cached = self._get_cache(provider)
            if cached is not None:
                resources.extend(cached.resources)
        return resources

    def auto_register(
        self,
        candidate: DiscoveredResource,
        work: WorkSpec,
    ) -> dict[str, object]:
        """Register a selected candidate only when a tracker was injected."""
        if self._tracker is None:
            return {"registered": False, "reason": "no utilization tracker"}
        return register_discovered(self._tracker, candidate, work)

    async def refresh_once(self) -> None:
        """Run one observable refresh tick without propagating provider errors."""
        try:
            await self.discover_all()
        except Exception as exc:  # pragma: no cover - defensive orchestration guard
            logger.warning(
                "compute discovery refresh failed type=%s",
                type(exc).__name__,
            )
