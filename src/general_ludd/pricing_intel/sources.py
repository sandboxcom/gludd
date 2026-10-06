"""Compatibility facade for model API and cloud compute pricing sources.

Implementations live in the source_components package. Historic imports,
object identities, registry order, and the httpx.Client monkeypatch seam remain
stable through explicit re-exports.
"""

from __future__ import annotations

import httpx as httpx

from general_ludd.pricing_intel.source_components.base import (
    PricingSource as PricingSource,
)
from general_ludd.pricing_intel.source_components.base import (
    PricingSourceAuthenticationError as PricingSourceAuthenticationError,
)
from general_ludd.pricing_intel.source_components.base import (
    PricingSourceDataError as PricingSourceDataError,
)
from general_ludd.pricing_intel.source_components.base import (
    UnavailableModelPrices as UnavailableModelPrices,
)
from general_ludd.pricing_intel.source_components.base import (
    _openrouter_models as _openrouter_models,
)
from general_ludd.pricing_intel.source_components.cache import (
    _DEFAULT_CACHE_TTL as _DEFAULT_CACHE_TTL,
)
from general_ludd.pricing_intel.source_components.cache import (
    CachedSource as CachedSource,
)
from general_ludd.pricing_intel.source_components.cloud_compute import (
    _AWS_FETCHED_AT as _AWS_FETCHED_AT,
)
from general_ludd.pricing_intel.source_components.cloud_compute import (
    _AWS_GPU_INSTANCES as _AWS_GPU_INSTANCES,
)
from general_ludd.pricing_intel.source_components.cloud_compute import (
    _AWS_SOURCE as _AWS_SOURCE,
)
from general_ludd.pricing_intel.source_components.cloud_compute import (
    _AWS_SPOT_SOURCE as _AWS_SPOT_SOURCE,
)
from general_ludd.pricing_intel.source_components.cloud_compute import (
    _GCP_FETCHED_AT as _GCP_FETCHED_AT,
)
from general_ludd.pricing_intel.source_components.cloud_compute import (
    _GCP_GPU_INSTANCES as _GCP_GPU_INSTANCES,
)
from general_ludd.pricing_intel.source_components.cloud_compute import (
    _GCP_SOURCE as _GCP_SOURCE,
)
from general_ludd.pricing_intel.source_components.cloud_compute import (
    _LAMBDA_FETCHED_AT as _LAMBDA_FETCHED_AT,
)
from general_ludd.pricing_intel.source_components.cloud_compute import (
    _LAMBDA_ONDEMAND as _LAMBDA_ONDEMAND,
)
from general_ludd.pricing_intel.source_components.cloud_compute import (
    _LAMBDA_SOURCE as _LAMBDA_SOURCE,
)
from general_ludd.pricing_intel.source_components.cloud_compute import (
    _RUNPOD_FETCHED_AT as _RUNPOD_FETCHED_AT,
)
from general_ludd.pricing_intel.source_components.cloud_compute import (
    _RUNPOD_ONDEMAND as _RUNPOD_ONDEMAND,
)
from general_ludd.pricing_intel.source_components.cloud_compute import (
    _RUNPOD_SOURCE as _RUNPOD_SOURCE,
)
from general_ludd.pricing_intel.source_components.cloud_compute import (
    _RUNPOD_SPOT as _RUNPOD_SPOT,
)
from general_ludd.pricing_intel.source_components.cloud_compute import (
    AWSPricingSource as AWSPricingSource,
)
from general_ludd.pricing_intel.source_components.cloud_compute import (
    AWSSource as AWSSource,
)
from general_ludd.pricing_intel.source_components.cloud_compute import (
    GCPPricingSource as GCPPricingSource,
)
from general_ludd.pricing_intel.source_components.cloud_compute import (
    GCPSource as GCPSource,
)
from general_ludd.pricing_intel.source_components.cloud_compute import (
    LambdaLabsPricingSource as LambdaLabsPricingSource,
)
from general_ludd.pricing_intel.source_components.cloud_compute import (
    LambdaLabsSource as LambdaLabsSource,
)
from general_ludd.pricing_intel.source_components.cloud_compute import (
    RunPodPricingSource as RunPodPricingSource,
)
from general_ludd.pricing_intel.source_components.cloud_compute import (
    RunPodSource as RunPodSource,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    _ANTHROPIC_FETCHED_AT as _ANTHROPIC_FETCHED_AT,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    _ANTHROPIC_PRICES_STATIC as _ANTHROPIC_PRICES_STATIC,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    _ANTHROPIC_SOURCE as _ANTHROPIC_SOURCE,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    _HF_DEDICATED_PRICES_STATIC as _HF_DEDICATED_PRICES_STATIC,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    _HF_ENDPOINT as _HF_ENDPOINT,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    _HF_FETCHED_AT as _HF_FETCHED_AT,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    _HF_PRICE_RE as _HF_PRICE_RE,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    _HF_SOURCE as _HF_SOURCE,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    _OPENAI_FETCHED_AT as _OPENAI_FETCHED_AT,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    _OPENAI_PRICES_STATIC as _OPENAI_PRICES_STATIC,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    _OPENAI_SOURCE as _OPENAI_SOURCE,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    _ZAI_ENDPOINT as _ZAI_ENDPOINT,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    _ZAI_FETCHED_AT as _ZAI_FETCHED_AT,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    _ZAI_PRICE_RE as _ZAI_PRICE_RE,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    _ZAI_PRICES_PER_1M as _ZAI_PRICES_PER_1M,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    _ZAI_SOURCE as _ZAI_SOURCE,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    _ZAI_TABLE_RE as _ZAI_TABLE_RE,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    AnthropicSource as AnthropicSource,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    HuggingFacePricingSource as HuggingFacePricingSource,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    HuggingFaceSource as HuggingFaceSource,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    LiteLLMJSONSource as LiteLLMJSONSource,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    OpenAISource as OpenAISource,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    OpenRouterSource as OpenRouterSource,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    ZAIPricingSource as ZAIPricingSource,
)
from general_ludd.pricing_intel.source_components.model_apis import (
    ZAISource as ZAISource,
)
from general_ludd.pricing_intel.source_components.registry import (
    all_sources as all_sources,
)
from general_ludd.pricing_intel.source_components.registry import (
    staleness_text as staleness_text,
)

__all__ = [
    "AWSPricingSource",
    "AWSSource",
    "AnthropicSource",
    "CachedSource",
    "GCPPricingSource",
    "GCPSource",
    "HuggingFacePricingSource",
    "HuggingFaceSource",
    "LambdaLabsPricingSource",
    "LambdaLabsSource",
    "LiteLLMJSONSource",
    "OpenAISource",
    "OpenRouterSource",
    "PricingSource",
    "PricingSourceAuthenticationError",
    "PricingSourceDataError",
    "RunPodPricingSource",
    "RunPodSource",
    "UnavailableModelPrices",
    "ZAIPricingSource",
    "ZAISource",
    "all_sources",
    "staleness_text",
]
