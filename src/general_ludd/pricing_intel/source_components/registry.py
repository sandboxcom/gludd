"""Deterministic pricing-source registry and staleness presentation."""

from __future__ import annotations

import time

from general_ludd.pricing_intel.source_components.base import PricingSource
from general_ludd.pricing_intel.source_components.cache import CachedSource
from general_ludd.pricing_intel.source_components.cloud_compute import (
    AWSPricingSource,
    AWSSource,
    GCPPricingSource,
    GCPSource,
    LambdaLabsPricingSource,
    LambdaLabsSource,
    RunPodPricingSource,
    RunPodSource,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    AnthropicSource,
    HuggingFacePricingSource,
    HuggingFaceSource,
    LiteLLMJSONSource,
    OpenAISource,
    OpenRouterSource,
    ZAIPricingSource,
    ZAISource,
)


def staleness_text(fetched_at: float) -> str:
    """Return a human-readable staleness label for a price's fetch timestamp.

    Used by budget decision-makers to see data age at a glance.
    Thresholds: "fresh" <= 1 hour, "stale" <= 24 hours, "very_stale" > 24h.
    """
    age_seconds = time.time() - fetched_at
    if age_seconds < 0:
        return "fresh"
    if age_seconds <= 3600:
        return "fresh"
    if age_seconds <= 86400:
        return "stale"
    return "very_stale"

# ---------------------------------------------------------------------------
# Registry: all available sources
# ---------------------------------------------------------------------------


def all_sources() -> list[PricingSource]:
    """Return one instance of every registered PricingSource.

    Live sources are wrapped in CachedSource with their static counterparts
    as fallbacks, so production code always gets TTL caching + static
    fallback on network failure.
    """
    return [
        OpenRouterSource(),
        AnthropicSource(),
        OpenAISource(),
        CachedSource(LiteLLMJSONSource("anthropic"), AnthropicSource()),
        CachedSource(LiteLLMJSONSource("openai"), OpenAISource()),
        LiteLLMJSONSource("fireworks_ai"),
        CachedSource(RunPodPricingSource(), RunPodSource()),
        RunPodSource(),
        LambdaLabsSource(),
        CachedSource(LambdaLabsPricingSource(), LambdaLabsSource()),
        AWSSource(),
        CachedSource(AWSPricingSource(), AWSSource()),
        GCPSource(),
        CachedSource(GCPPricingSource(), GCPSource()),
        HuggingFaceSource(),
        CachedSource(HuggingFacePricingSource(), HuggingFaceSource()),
        ZAISource(),
        CachedSource(ZAIPricingSource(), ZAISource()),
    ]

__all__ = ["all_sources", "staleness_text"]
