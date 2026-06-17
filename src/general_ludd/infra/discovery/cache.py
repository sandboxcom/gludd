"""Per-source last-good discovery cache with a max-staleness TTL.

On every OK/PARTIAL result the service stores ``(ts, result)`` per source; on an
OFFLINE/ERROR/open-breaker outcome it serves the cached result (stamped
``from_cache=True`` + ``meta['stale_age_s']``) so a transient outage degrades
gracefully instead of emptying the candidate pool. Entries past ``ttl_s`` are
DROPPED (not served) so ``route_task`` never targets a ghost resource.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import replace

from general_ludd.infra.discovery.base import DiscoveryResult, DiscoverySource


class LastGoodCache:
    def __init__(
        self,
        *,
        ttl_s: float = 900.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._ttl_s = ttl_s
        self._clock: Callable[[], float] = clock or time.monotonic
        self._store: dict[DiscoverySource, tuple[float, DiscoveryResult]] = {}
        self._lock = threading.Lock()

    def put(self, result: DiscoveryResult) -> None:
        with self._lock:
            self._store[result.source] = (self._clock(), result)

    def get(self, source: DiscoverySource) -> DiscoveryResult | None:
        """Return the cached last-good result for ``source``, or None.

        A non-stale hit is returned with ``from_cache=True`` and
        ``meta['stale_age_s']`` set. A stale entry is dropped and None returned.
        """
        with self._lock:
            entry = self._store.get(source)
            if entry is None:
                return None
            ts, result = entry
            age = self._clock() - ts
            if age > self._ttl_s:
                # Past max staleness: drop, never serve a ghost.
                del self._store[source]
                return None
            meta = dict(result.meta)
            meta["stale_age_s"] = age
            return replace(result, from_cache=True, meta=meta)
