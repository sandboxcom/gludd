"""DiscoveryService — orchestrates providers, caching, breakers, registration.

Owns: a provider registry, a per-source last-good cache, a per-source circuit
breaker, async ``discover()`` / ``discover_all()``, a refresh-loop body, and
``auto_register()`` (the shared sink both the daemon refresh path and the
on-demand endpoints call). It pulls the UtilizationTracker + secrets resolver
that the daemon already built — it never constructs its own.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from general_ludd.infra.discovery.base import (
    DiscoveredResource,
    DiscoveryProvider,
    DiscoveryResult,
    DiscoverySource,
    DiscoveryStatus,
    WorkSpec,
)
from general_ludd.infra.discovery.cache import LastGoodCache
from general_ludd.infra.discovery.circuit import ProviderCircuitBreaker
from general_ludd.infra.discovery.selector import register_discovered

logger = logging.getLogger(__name__)


class DiscoveryService:
    def __init__(
        self,
        *,
        registry: dict[DiscoverySource, DiscoveryProvider],
        tracker: Any | None = None,
        cache_ttl_s: float = 900.0,
        breaker_threshold: int = 3,
        breaker_cooldown_s: float = 60.0,
    ) -> None:
        self._registry = registry
        self._tracker = tracker
        self._cache = LastGoodCache(ttl_s=cache_ttl_s)
        self._breakers: dict[DiscoverySource, ProviderCircuitBreaker] = {
            src: ProviderCircuitBreaker(
                failure_threshold=breaker_threshold,
                cooldown_s=breaker_cooldown_s,
            )
            for src in registry
        }

    @property
    def sources(self) -> list[DiscoverySource]:
        return list(self._registry)

    async def discover(self, source: DiscoverySource) -> DiscoveryResult:
        """Discover one source. Serves cached last-good on outage/open-breaker.

        Total: never raises. Populates the cache on OK/PARTIAL.
        """
        provider = self._registry.get(source)
        if provider is None:
            return DiscoveryResult(
                source=source,
                status=DiscoveryStatus.ERROR,
                error="no provider registered for source",
            )
        breaker = self._breakers.get(source)

        # Fast-skip a tripped provider: serve the cached last-good without
        # touching the network until a half-open probe is admitted.
        if breaker is not None and not breaker.allow():
            cached = self._cache.get(source)
            if cached is not None:
                return cached
            return DiscoveryResult(
                source=source,
                status=DiscoveryStatus.OFFLINE,
                error="circuit breaker open; no cached result",
            )

        try:
            result = await provider.discover()
        except Exception as exc:
            logger.warning("provider %s raised unexpectedly: %s", source, type(exc).__name__)
            if breaker is not None:
                breaker.record_failure()
            cached = self._cache.get(source)
            return cached or DiscoveryResult(
                source=source, status=DiscoveryStatus.ERROR, error=type(exc).__name__
            )

        if result.ok:
            if breaker is not None:
                breaker.record_success()
            self._cache.put(result)
            return result

        # Outage statuses: count toward the breaker and degrade to last-good.
        if result.status in (DiscoveryStatus.OFFLINE, DiscoveryStatus.ERROR):
            if breaker is not None:
                breaker.record_failure()
            cached = self._cache.get(source)
            if cached is not None:
                return cached
        return result

    async def discover_all(self) -> dict[DiscoverySource, DiscoveryResult]:
        """Discover every source concurrently. POPULATES the cache only.

        Does NOT auto-register resources (that would flood the tracker);
        auto-registration happens at SELECT time.
        """
        results = await asyncio.gather(
            *(self.discover(src) for src in self._registry),
            return_exceptions=False,
        )
        out = {res.source: res for res in results}
        cached_count = sum(1 for r in out.values() if r.from_cache)
        ok_count = sum(1 for r in out.values() if r.ok)
        logger.info(
            "compute discovery refresh tick: %d sources (%d ok, %d from cache)",
            len(out),
            ok_count,
            cached_count,
        )
        return out

    def cached(self, source: DiscoverySource) -> DiscoveryResult | None:
        return self._cache.get(source)

    def all_cached_resources(self) -> list[DiscoveredResource]:
        """Flatten the cached last-good resources across all sources."""
        out: list[DiscoveredResource] = []
        for source in self._registry:
            cached = self._cache.get(source)
            if cached is not None:
                out.extend(cached.resources)
        return out

    def auto_register(
        self, pick: DiscoveredResource, work_spec: WorkSpec
    ) -> dict[str, Any]:
        """Shared sink: SSRF-guard + dedupe + tracker.register_endpoint.

        Returns a structured dict (registered / needs_deploy). Registers nothing
        when there is no tracker or no SSRF-safe endpoint_url.
        """
        if self._tracker is None:
            return {"registered": False, "reason": "no utilization tracker"}
        return register_discovered(self._tracker, pick, work_spec)

    async def refresh_once(self) -> None:
        """One refresh tick — caches per source, NEVER raises (loop body)."""
        try:
            await self.discover_all()
        except Exception as exc:
            logger.warning("discovery refresh failed: %s", type(exc).__name__)
