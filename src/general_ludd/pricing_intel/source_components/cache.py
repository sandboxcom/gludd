"""TTL caching and static fallback for pricing sources."""

from __future__ import annotations

import logging
import time

from general_ludd.pricing_intel.models import ComputePrice, ModelPrice, ProviderBilling
from general_ludd.pricing_intel.source_components.base import PricingSource

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CachedSource — TTL cache + static fallback for live pricing fetchers
# ---------------------------------------------------------------------------
# Wraps any PricingSource-compatible live source with:
#   - In-memory TTL cache: second call within TTL window returns cached data.
#   - Static fallback: if the live fetch fails (exception returned), falls
#     back to the static source's data (offline-safe).
#   - Staleness metadata: fetched_at on every price records age; the
#     staleness_text() helper renders a human-readable age description.
#
# Model and compute caches are independent. ``refresh=True`` keyword on
# ``fetch_model_prices`` or ``fetch_compute_prices`` forces a re-fetch.
# ---------------------------------------------------------------------------

_DEFAULT_CACHE_TTL = 3600.0  # 1 hour


class CachedSource:
    """Wraps a live PricingSource with TTL cache + optional static fallback.

    On live-fetch failure:
        1. Returns stale cache if available.
        2. Falls back to static source data.
        3. Returns [] if no fallback is available.

    ``provider_slug()`` and ``billing()`` delegate to the live source.
    """

    def __init__(
        self,
        live: PricingSource,
        static_fallback: PricingSource | None = None,
        ttl_seconds: float = _DEFAULT_CACHE_TTL,
    ) -> None:
        """Initialize the cache wrapper around a live and optional static source."""
        self._live = live
        self._static = static_fallback
        self._ttl = ttl_seconds
        self._model_cache: list[ModelPrice] | None = None
        self._model_cache_time: float = 0.0
        self._compute_cache: list[ComputePrice] | None = None
        self._compute_cache_time: float = 0.0

    def provider_slug(self) -> str:
        """Return this provider's canonical slug."""
        return self._live.provider_slug()

    def billing(self) -> ProviderBilling:
        """Return this provider's billing terms."""
        return self._live.billing()

    def fetch_model_prices(self, refresh: bool = False) -> list[ModelPrice]:
        """Return cached model prices, refetching live when stale or requested."""
        if not refresh and self._model_cache is not None and not self._is_stale(self._model_cache_time):
            return list(self._model_cache)
        try:
            prices = self._live.fetch_model_prices()
        except Exception as exc:
            logger.warning(
                "CachedSource(%s): live model fetch failed: %s",
                self.provider_slug(),
                exc,
            )
            if self._model_cache is not None:
                return list(self._model_cache)
            if self._static is not None:
                logger.info(
                    "CachedSource(%s): falling back to static model prices",
                    self.provider_slug(),
                )
                try:
                    return self._static.fetch_model_prices()
                except Exception as fb_exc:
                    logger.warning(
                        "CachedSource(%s): static fallback also failed: %s",
                        self.provider_slug(),
                        fb_exc,
                    )
            return []
        if not prices and self._static is not None:
            logger.info(
                "CachedSource(%s): live returned empty, falling back to static model prices",
                self.provider_slug(),
            )
            try:
                fb = self._static.fetch_model_prices()
                if fb:
                    return fb
            except Exception as fb_exc:
                logger.warning(
                    "CachedSource(%s): static fallback failed: %s",
                    self.provider_slug(),
                    fb_exc,
                )
        self._model_cache = list(prices)
        self._model_cache_time = time.time()
        return list(prices)

    def fetch_compute_prices(self, refresh: bool = False) -> list[ComputePrice]:
        """Return cached compute prices, refetching live when stale or requested."""
        if not refresh and self._compute_cache is not None and not self._is_stale(self._compute_cache_time):
            return list(self._compute_cache)
        try:
            prices = self._live.fetch_compute_prices()
        except Exception as exc:
            logger.warning(
                "CachedSource(%s): live compute fetch failed: %s",
                self.provider_slug(),
                exc,
            )
            if self._compute_cache is not None:
                return list(self._compute_cache)
            if self._static is not None:
                logger.info(
                    "CachedSource(%s): falling back to static compute prices",
                    self.provider_slug(),
                )
                try:
                    return self._static.fetch_compute_prices()
                except Exception as fb_exc:
                    logger.warning(
                        "CachedSource(%s): static fallback also failed: %s",
                        self.provider_slug(),
                        fb_exc,
                    )
            return []
        if not prices and self._static is not None:
            logger.info(
                "CachedSource(%s): live returned empty, falling back to static compute prices",
                self.provider_slug(),
            )
            try:
                fb = self._static.fetch_compute_prices()
                if fb:
                    return fb
            except Exception as fb_exc:
                logger.warning(
                    "CachedSource(%s): static fallback failed: %s",
                    self.provider_slug(),
                    fb_exc,
                )
        self._compute_cache = list(prices)
        self._compute_cache_time = time.time()
        return list(prices)

    def _is_stale(self, cache_time: float) -> bool:
        if self._ttl <= 0:
            return True
        return (time.time() - cache_time) > self._ttl

__all__ = ["CachedSource"]
