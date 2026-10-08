"""PricingCatalog: aggregates all PricingSource implementations.

Provides unified lookup of model prices, compute prices, and billing semantics.
Availability failures are fail-soft; authentication and corrupt pricing data raise.

Cache behavior:
  - Model prices are cached in memory after first fetch (per-source).
  - Compute prices are cached in memory after first fetch (per-source).
  - Billing semantics are always returned from static source data (no fetch needed).
  - Use refresh=True on any fetch method to force re-fetch from sources.

# Integration status:
#   [DONE] SpendLimiter.token_cost_usd() now uses catalog.model_price(provider,
#          model_id) as the primary source with the static PRICING table in
#          infra/pricing.py as fallback. See src/general_ludd/controllers/
#          spend_limiter.py and the daemon wiring in src/general_ludd/daemon.py.
#   [DONE] The /api/pricing router facet serves live catalog data as JSON. See
#          src/general_ludd/routers/observe.py: GET /api/pricing (model token
#          prices), GET /api/pricing/compute (compute instance prices), and
#          GET /api/pricing/catalog (the consolidated whole-catalog view:
#          model + compute + billing + provider slugs). All read the shared
#          instance published on app.state._pricing_catalog by daemon.py, and
#          degrade to empty results when no catalog is wired. Covered by
#          tests/unit/test_pricing_router.py.
#   [DONE] InfraTracker.gpu_cost_usd() -> use catalog.compute_price("runpod", sku)
#          to replace the static INFRA_PRICING dict. See src/general_ludd/infra/pricing.py
#          (InfraTracker.gpu_cost_usd at line 152, routing through
#          _catalog_compute_price -> catalog.compute_price).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence

from general_ludd.pricing_intel.models import (
    ComputePrice,
    ModelInfo,
    ModelPrice,
    ProviderBilling,
)
from general_ludd.pricing_intel.sources import (
    PricingSource,
    PricingSourceAuthenticationError,
    PricingSourceDataError,
    UnavailableModelPrices,
    all_sources,
)

logger = logging.getLogger(__name__)

# Default cache TTL: 1 hour. After this, refresh=True is recommended.
_DEFAULT_TTL_SECONDS = 3600.0


class PricingCatalog:
    """Aggregates all pricing sources into a single queryable catalog.

    Usage::

        catalog = PricingCatalog()
        # or with custom sources:
        catalog = PricingCatalog(sources=[OpenRouterSource(), AnthropicSource()])

        # Get billing terms for a provider
        billing = catalog.billing("runpod")

        # Get model price
        price = catalog.model_price("openrouter", "anthropic/claude-3-5-sonnet")

        # Get compute price
        compute = catalog.compute_price("runpod", "A100-SXM4-80GB-1x")

        # All billing terms (for cost planning UI)
        all_billing = catalog.all_billing()
    """

    def __init__(
        self,
        sources: Sequence[PricingSource] | None = None,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
    ) -> None:
        """Initialize the catalog with explicit sources and a cache TTL."""
        self._sources: list[PricingSource] = list(sources) if sources is not None else all_sources()
        self._ttl = ttl_seconds

        # Cache: provider_slug -> list[ModelPrice]
        self._model_cache: dict[str, list[ModelPrice]] = {}
        self._model_cache_time: dict[str, float] = {}

        # Cache: provider_slug -> list[ComputePrice]
        self._compute_cache: dict[str, list[ComputePrice]] = {}
        self._compute_cache_time: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Source access helpers
    # ------------------------------------------------------------------

    def _source_for(self, provider: str) -> PricingSource | None:
        """Return the registered source for a provider slug, or None."""
        for src in self._sources:
            if src.provider_slug() == provider:
                return src
        return None

    def _is_stale(self, cache_time: dict[str, float], key: str) -> bool:
        last_fetch = cache_time.get(key, 0.0)
        return (time.time() - last_fetch) > self._ttl

    # ------------------------------------------------------------------
    # Billing semantics — always from static source data, no network
    # ------------------------------------------------------------------

    def billing(self, provider: str) -> ProviderBilling | None:
        """Return billing semantics for a provider.

        Returns None if the provider is not registered.
        Never fetches from network; billing terms are static facts.
        """
        src = self._source_for(provider)
        if src is None:
            logger.debug("PricingCatalog: no source registered for provider %r", provider)
            return None
        return src.billing()

    def all_billing(self) -> list[ProviderBilling]:
        """Return billing semantics for every registered provider."""
        results: list[ProviderBilling] = []
        for src in self._sources:
            try:
                results.append(src.billing())
            except Exception as exc:
                logger.warning(
                    "PricingCatalog: billing() failed for %s: %s",
                    src.provider_slug(), exc,
                )
        return results

    # ------------------------------------------------------------------
    # Model prices
    # ------------------------------------------------------------------

    def _fetch_model_prices_for(self, provider: str, refresh: bool = False) -> list[ModelPrice]:
        """Fetch (or return cached) model prices for a single provider."""
        src = self._source_for(provider)
        if src is None:
            return []

        if not refresh and not self._is_stale(self._model_cache_time, provider):
            return self._model_cache.get(provider, [])

        try:
            prices = src.fetch_model_prices()
        except (PricingSourceAuthenticationError, PricingSourceDataError):
            raise
        except Exception as exc:
            logger.warning(
                "PricingCatalog: fetch_model_prices() failed for %s: %s",
                provider, exc,
            )
            return self._model_cache.get(provider, [])  # return stale cache on error

        if isinstance(prices, UnavailableModelPrices):
            return self._model_cache.get(provider, [])

        self._model_cache[provider] = prices
        self._model_cache_time[provider] = time.time()
        return prices

    def model_price(
        self, provider: str, model_id: str, refresh: bool = False
    ) -> ModelPrice | None:
        """Look up the price for a specific model.

        Args:
            provider: Provider slug (e.g. "anthropic", "openrouter").
            model_id: Model identifier as used by the provider's API.
            refresh:  If True, bypass cache and re-fetch from source.

        Returns:
            ModelPrice if found, None otherwise.
        """
        prices = self._fetch_model_prices_for(provider, refresh=refresh)
        for price in prices:
            if price.model_id == model_id:
                return price
        return None

    def all_model_prices(
        self, provider: str | None = None, refresh: bool = False
    ) -> list[ModelPrice]:
        """Return all known model prices, optionally filtered by provider."""
        if provider is not None:
            return self._fetch_model_prices_for(provider, refresh=refresh)

        results: list[ModelPrice] = []
        for src in self._sources:
            slug = src.provider_slug()
            results.extend(self._fetch_model_prices_for(slug, refresh=refresh))
        return results

    # ------------------------------------------------------------------
    # Compute prices
    # ------------------------------------------------------------------

    def _fetch_compute_prices_for(self, provider: str, refresh: bool = False) -> list[ComputePrice]:
        """Fetch (or return cached) compute prices for a single provider."""
        src = self._source_for(provider)
        if src is None:
            return []

        if not refresh and not self._is_stale(self._compute_cache_time, provider):
            return self._compute_cache.get(provider, [])

        try:
            prices = src.fetch_compute_prices()
        except Exception as exc:
            logger.warning(
                "PricingCatalog: fetch_compute_prices() failed for %s: %s",
                provider, exc,
            )
            return self._compute_cache.get(provider, [])  # return stale cache on error

        self._compute_cache[provider] = prices
        self._compute_cache_time[provider] = time.time()
        return prices

    def compute_price(
        self, provider: str, sku: str, spot: bool = False, refresh: bool = False
    ) -> ComputePrice | None:
        """Look up the price for a specific compute SKU.

        Args:
            provider: Provider slug (e.g. "runpod", "aws").
            sku:      SKU or instance type string (e.g. "A100-SXM4-80GB-1x").
            spot:     If True, prefer spot pricing when multiple entries match.
            refresh:  If True, bypass cache and re-fetch from source.

        Returns:
            ComputePrice if found, None otherwise. If spot=False and both
            on-demand and spot exist for the same SKU base name, returns on-demand.
        """
        prices = self._fetch_compute_prices_for(provider, refresh=refresh)
        if spot:
            spot_sku = sku if sku.endswith("-spot") else f"{sku}-spot"
            for candidate_sku in (spot_sku, sku):
                for price in prices:
                    if price.sku == candidate_sku and price.spot:
                        return price
            return None

        # Exact match for on-demand/default lookup. Keep direct spot SKU lookups working
        # for callers that pass the provider-specific spot SKU explicitly.
        for price in prices:
            if price.sku == sku and not price.spot:
                return price
        for price in prices:
            if price.sku == sku:
                return price
        return None

    def all_compute_prices(
        self, provider: str | None = None, spot: bool | None = None, refresh: bool = False
    ) -> list[ComputePrice]:
        """Return all known compute prices.

        Args:
            provider: Filter by provider slug (None = all providers).
            spot:     If True, return only spot prices. If False, on-demand only.
                      If None, return both.
            refresh:  If True, bypass cache and re-fetch from source.
        """
        if provider is not None:
            prices = self._fetch_compute_prices_for(provider, refresh=refresh)
        else:
            prices = []
            for src in self._sources:
                slug = src.provider_slug()
                prices.extend(self._fetch_compute_prices_for(slug, refresh=refresh))

        if spot is None:
            return prices
        return [p for p in prices if p.spot == spot]

    # ------------------------------------------------------------------
    # Convenience: cheapest options
    # ------------------------------------------------------------------

    def cheapest_compute(
        self,
        gpu_type_substr: str | None = None,
        spot: bool | None = None,
        refresh: bool = False,
    ) -> list[ComputePrice]:
        """Return compute prices sorted by USD/hour (cheapest first).

        Args:
            gpu_type_substr: Optional filter — only include SKUs where gpu_type
                             contains this substring (case-insensitive).
            spot:            If True, spot only; if False, on-demand only; None = both.
            refresh:         If True, bypass cache.
        """
        prices = self.all_compute_prices(spot=spot, refresh=refresh)
        if gpu_type_substr:
            sub = gpu_type_substr.lower()
            prices = [p for p in prices if p.gpu_type and sub in p.gpu_type.lower()]
        return sorted(prices, key=lambda p: p.usd_per_hour())

    def provider_slugs(self) -> list[str]:
        """Return slugs of all registered providers."""
        return [src.provider_slug() for src in self._sources]

    def all_model_info(
        self, provider: str | None = None, refresh: bool = False
    ) -> list[ModelInfo]:
        """Return ModelInfo records combining pricing, context windows, and quality descriptors.

        Each ModelInfo pairs a ModelPrice with metadata from the provider source.
        """
        prices = self.all_model_prices(provider=provider, refresh=refresh)
        result: list[ModelInfo] = []
        for p in prices:
            info = ModelInfo(
                model_id=p.model_id,
                provider=p.provider,
                context_window=p.context_window,
                pricing=p,
                notes=p.notes,
            )
            result.append(info)
        return result
