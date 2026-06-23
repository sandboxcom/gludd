"""TDD tests for SpendLimiter.token_cost_usd(): PricingCatalog primary, static fallback.

Covers the integration point documented at catalog.py:12:
    SpendLimiter.token_cost_usd() -> use catalog.model_price(provider, model_id)

Resolution order:
  1. If a PricingCatalog is injected, query it (primary).
  2. On any miss / error / absent catalog, fall back to the static
     token_cost_usd() in infra/pricing.py.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from general_ludd.controllers.spend_limiter import SpendLimiter
from general_ludd.pricing_intel.models import ModelPrice


class _FakeCatalog:
    """Deterministic PricingCatalog stand-in (no network)."""

    def __init__(
        self,
        prices: Sequence[ModelPrice | None] | None = None,
        slugs: Sequence[str] = ("anthropic", "openai", "openrouter"),
        boom: bool = False,
    ) -> None:
        self._prices = list(prices) if prices is not None else []
        self._slugs = list(slugs)
        self._boom = boom
        self.calls: list[tuple[str, str]] = []

    def provider_slugs(self) -> list[str]:
        return list(self._slugs)

    def model_price(
        self, provider: str, model_id: str, refresh: bool = False
    ) -> ModelPrice | None:
        self.calls.append((provider, model_id))
        if self._boom:
            raise RuntimeError("network down")
        if not self._prices:
            return None
        return self._prices.pop(0)


def _mp(provider: str, model_id: str, inp: float, out: float) -> ModelPrice:
    return ModelPrice(
        provider=provider,
        model_id=model_id,
        input_usd_per_1k=inp,
        output_usd_per_1k=out,
        source="fake-test-source",
    )


class TestTokenCostUsdFallback:
    """When no catalog is injected, the static pricing table must be used."""

    def test_no_catalog_uses_static_table(self) -> None:
        sl = SpendLimiter(limit_usd=10.0, window_seconds=60.0)
        # static PRICING["claude-3-5-sonnet-20241022"] = (0.003, 0.015)
        cost = sl.token_cost_usd("claude-3-5-sonnet-20241022", 1000, 1000)
        assert cost == pytest.approx(0.003 + 0.015)

    def test_no_catalog_unknown_model_uses_default(self) -> None:
        sl = SpendLimiter(limit_usd=10.0, window_seconds=60.0)
        cost = sl.token_cost_usd("totally-fake-model-xyz", 1000, 1000)
        # static PRICING["__default__"] = (0.005, 0.015)
        assert cost == pytest.approx(0.005 + 0.015)


class TestTokenCostUsdCatalogPrimary:
    """When a catalog is injected and returns a hit, it overrides the static table."""

    def test_catalog_hit_overrides_static(self) -> None:
        # Static says 0.003/0.015; catalog says 10x.
        cat = _FakeCatalog(
            prices=[_mp("anthropic", "claude-3-5-sonnet-20241022", 0.03, 0.15)]
        )
        sl = SpendLimiter(limit_usd=10.0, window_seconds=60.0, catalog=cat)
        cost = sl.token_cost_usd("claude-3-5-sonnet-20241022", 1000, 1000)
        assert cost == pytest.approx(0.03 + 0.15)
        assert cost != pytest.approx(0.003 + 0.015)

    def test_catalog_miss_falls_back_to_static(self) -> None:
        cat = _FakeCatalog(prices=[None, None, None])  # every provider misses
        sl = SpendLimiter(limit_usd=10.0, window_seconds=60.0, catalog=cat)
        cost = sl.token_cost_usd("claude-3-5-sonnet-20241022", 1000, 1000)
        assert cost == pytest.approx(0.003 + 0.015)

    def test_catalog_raising_falls_back_to_static(self) -> None:
        sl = SpendLimiter(
            limit_usd=10.0, window_seconds=60.0, catalog=_FakeCatalog(boom=True)
        )
        cost = sl.token_cost_usd("claude-3-5-sonnet-20241022", 1000, 1000)
        assert cost == pytest.approx(0.003 + 0.015)


class TestTokenCostUsdMath:
    def test_input_and_output_tokens_priced_separately(self) -> None:
        cat = _FakeCatalog(prices=[_mp("anthropic", "m", 0.010, 0.020)])
        sl = SpendLimiter(limit_usd=10.0, window_seconds=60.0, catalog=cat)
        cost = sl.token_cost_usd("m", 2500, 500)
        assert cost == pytest.approx(0.010 * 2.5 + 0.020 * 0.5)

    def test_zero_tokens_is_zero_cost(self) -> None:
        cat = _FakeCatalog(prices=[_mp("anthropic", "m", 0.010, 0.020)])
        sl = SpendLimiter(limit_usd=10.0, window_seconds=60.0, catalog=cat)
        assert sl.token_cost_usd("m", 0, 0) == pytest.approx(0.0)


class TestTokenCostUsdProviderResolution:
    def test_explicit_provider_skips_inference(self) -> None:
        cat = _FakeCatalog(
            prices=[_mp("openrouter", "claude-3-5-sonnet-20241022", 0.005, 0.025)]
        )
        sl = SpendLimiter(limit_usd=10.0, window_seconds=60.0, catalog=cat)
        cost = sl.token_cost_usd(
            "claude-3-5-sonnet-20241022", 1000, 1000, provider="openrouter"
        )
        assert cost == pytest.approx(0.005 + 0.025)
        assert cat.calls == [("openrouter", "claude-3-5-sonnet-20241022")]

    def test_claude_prefix_tries_anthropic_first(self) -> None:
        cat = _FakeCatalog(prices=[_mp("anthropic", "claude-x", 0.001, 0.002)])
        sl = SpendLimiter(limit_usd=10.0, window_seconds=60.0, catalog=cat)
        sl.token_cost_usd("claude-x", 1000, 1000)
        assert cat.calls[0][0] == "anthropic"

    def test_gpt_prefix_tries_openai_first(self) -> None:
        cat = _FakeCatalog(prices=[None, _mp("openai", "gpt-99", 0.004, 0.016)])
        sl = SpendLimiter(limit_usd=10.0, window_seconds=60.0, catalog=cat)
        sl.token_cost_usd("gpt-99", 1000, 1000)
        assert cat.calls[0][0] == "openai"

    def test_unknown_prefix_tries_all_slugs(self) -> None:
        cat = _FakeCatalog(
            prices=[None, None, _mp("openrouter", "weird-model", 0.001, 0.002)],
            slugs=("anthropic", "openai", "openrouter"),
        )
        sl = SpendLimiter(limit_usd=10.0, window_seconds=60.0, catalog=cat)
        cost = sl.token_cost_usd("weird-model", 1000, 1000)
        assert cost == pytest.approx(0.001 + 0.002)
        # All three providers were consulted before the hit.
        assert len(cat.calls) == 3


class TestCatalogInjectionDefault:
    def test_construct_without_catalog_is_backwards_compatible(self) -> None:
        """Existing callers that omit `catalog` must continue to work."""
        sl = SpendLimiter(limit_usd=5.0, window_seconds=60.0)
        assert sl.token_cost_usd("__default__", 1000, 0) == pytest.approx(0.005)
