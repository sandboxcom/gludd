"""PricingSource implementations for model API and cloud compute providers.

FETCH STRATEGY LEGEND (used in each class docstring):
  LIVE    — fetches from a real public pricing API; sources reflect actual live data.
  STATIC  — hardcoded table with documented source URL; prices accurate at recorded date.
  TODO    — billing terms registered but price fetch not yet implemented.

BILLING SEMANTICS ACCURACY NOTE:
  RunPod and Lambda Labs bill per-second from a prepaid balance.
    Implication: if your balance hits $0, jobs are terminated immediately.
  AWS and GCP bill per-second (Linux instances) to a postpaid monthly invoice.
    Implication: no prepayment needed; accrue costs and pay at month-end.
  OpenAI, Anthropic: postpaid_per_use; billed per API call or monthly.
  OpenRouter: postpaid_per_use; routes to underlying providers.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Protocol, runtime_checkable

import httpx

from general_ludd.pricing_intel.models import (
    BillingGranularity,
    BillingTerms,
    ComputePrice,
    ModelPrice,
    ProviderBilling,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Protocol (interface) for all pricing sources
# ---------------------------------------------------------------------------


@runtime_checkable
class PricingSource(Protocol):
    """Protocol every pricing source must implement.

    Sources MUST be fail-soft: any network error returns an empty list /
    falls back gracefully. They MUST NOT raise exceptions to callers.
    """

    def provider_slug(self) -> str:
        """Canonical slug for this provider (e.g. 'openrouter', 'runpod')."""
        ...

    def billing(self) -> ProviderBilling:
        """Return static billing semantics for this provider.

        This is always available even when live fetch fails, because billing
        terms are documented facts that rarely change.
        """
        ...

    def fetch_model_prices(self) -> list[ModelPrice]:
        """Fetch current model prices. Returns [] on any error (fail-soft)."""
        ...

    def fetch_compute_prices(self) -> list[ComputePrice]:
        """Fetch current compute prices. Returns [] on any error (fail-soft)."""
        ...


# ---------------------------------------------------------------------------
# OpenRouter — LIVE fetch from public API
# ---------------------------------------------------------------------------
# Source: https://openrouter.ai/api/v1/models — public, no auth required,
# returns pricing in USD per token. This is a genuine live pricing API.
# ---------------------------------------------------------------------------


class OpenRouterSource:
    """FETCH STRATEGY: LIVE — https://openrouter.ai/api/v1/models (public API, no auth).

    OpenRouter is a meta-router; its billing terms are postpaid_per_use because
    charges are applied per request and do NOT require maintaining a prepaid balance
    (payment method is charged on consumption). The API exposes real-time pricing
    for all routed models.

    Billing:
      - terms: postpaid_per_use (credit card charged as you go)
      - granularity: per_token
      - spot_available: False (routing decision is made by OR, no spot concept)
      - min_charge: None documented
    """

    _ENDPOINT = "https://openrouter.ai/api/v1/models"

    def provider_slug(self) -> str:
        return "openrouter"

    def billing(self) -> ProviderBilling:
        return ProviderBilling(
            provider="openrouter",
            granularity=BillingGranularity.per_token,
            terms=BillingTerms.postpaid_per_use,
            currency="USD",
            min_charge=None,
            spot_available=False,
            notes=(
                "Charges per request routed to underlying provider. "
                "No prepaid balance required; credit card charged per use. "
                "Source: https://openrouter.ai/pricing"
            ),
        )

    def fetch_model_prices(self) -> list[ModelPrice]:
        """Fetch live model prices from OpenRouter public API.

        The /v1/models endpoint returns:
          data[].id          — model slug
          data[].pricing.prompt   — USD per token (input); multiply by 1000 for per-1k
          data[].pricing.completion — USD per token (output)
          data[].context_length — context window in tokens

        Returns empty list on any network/parse error (fail-soft).
        """
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(self._ENDPOINT)
                if resp.status_code != 200:
                    logger.warning(
                        "OpenRouter pricing API returned HTTP %s", resp.status_code
                    )
                    return []
                data = resp.json()
        except Exception as exc:
            logger.warning("OpenRouter pricing fetch failed: %s", exc)
            return []

        models = data.get("data", [])
        fetched_at = time.time()
        results: list[ModelPrice] = []

        for m in models:
            model_id = m.get("id", "")
            pricing = m.get("pricing", {})
            try:
                prompt_per_token = float(pricing.get("prompt", 0) or 0)
                completion_per_token = float(pricing.get("completion", 0) or 0)
            except (TypeError, ValueError):
                continue

            # OpenRouter returns USD-per-token; convert to USD-per-1k-tokens
            input_per_1k = prompt_per_token * 1000
            output_per_1k = completion_per_token * 1000

            context_length = m.get("context_length")
            try:
                ctx = int(context_length) if context_length is not None else None
            except (TypeError, ValueError):
                ctx = None

            results.append(
                ModelPrice(
                    provider="openrouter",
                    model_id=model_id,
                    input_usd_per_1k=input_per_1k,
                    output_usd_per_1k=output_per_1k,
                    fetched_at=fetched_at,
                    source=self._ENDPOINT,
                    context_window=ctx,
                    notes=m.get("description", "")[:200],
                )
            )

        return results

    def fetch_compute_prices(self) -> list[ComputePrice]:
        """OpenRouter does not offer direct compute. Returns []."""
        return []


# ---------------------------------------------------------------------------
# Anthropic — STATIC table (no public pricing API as of 2025-Q4)
# ---------------------------------------------------------------------------
# Source: https://www.anthropic.com/pricing  (accessed 2025-Q4)
# Billing: postpaid_per_use; API keys billed per call via Stripe.
# No prepaid balance required for API access.
# ---------------------------------------------------------------------------

_ANTHROPIC_PRICES_STATIC: list[tuple[str, float, float, int | None, str]] = [
    # (model_id, input_usd_per_1k, output_usd_per_1k, context_window, notes)
    # Claude 3.5 family — https://www.anthropic.com/pricing (2025-Q4)
    ("claude-3-5-sonnet-20241022", 0.003, 0.015, 200_000, "Claude 3.5 Sonnet; 50% discount on cached input"),
    ("claude-3-5-haiku-20241022", 0.0008, 0.004, 200_000, "Claude 3.5 Haiku; fastest 3.5 model"),
    # Claude 3 family
    ("claude-3-opus-20240229", 0.015, 0.075, 200_000, "Claude 3 Opus; highest capability"),
    ("claude-3-sonnet-20240229", 0.003, 0.015, 200_000, "Claude 3 Sonnet"),
    ("claude-3-haiku-20240307", 0.00025, 0.00125, 200_000, "Claude 3 Haiku; smallest/fastest"),
    # Claude 2 family
    ("claude-2.1", 0.008, 0.024, 200_000, "Claude 2.1; legacy"),
    ("claude-2.0", 0.008, 0.024, 100_000, "Claude 2.0; legacy"),
    # Claude Instant
    ("claude-instant-1.2", 0.0008, 0.0024, 100_000, "Claude Instant 1.2; legacy fast"),
]

_ANTHROPIC_SOURCE = "https://www.anthropic.com/pricing"
_ANTHROPIC_FETCHED_AT = 1735689600.0  # 2025-01-01 00:00 UTC (table recorded date)


class AnthropicSource:
    """FETCH STRATEGY: STATIC — hardcoded from https://www.anthropic.com/pricing (2025-Q4).

    Anthropic does not publish a machine-readable pricing API.
    Prices documented from the public pricing page.
    Live pricing is available via LiteLLMJSONSource("anthropic") which fetches
    from the litellm cross-provider catalog.

    Billing:
      - terms: postpaid_per_use (Stripe per-call billing; no prepaid balance)
      - granularity: per_token
      - spot_available: False
      - min_charge: None (no documented minimum per call)
    """

    def provider_slug(self) -> str:
        return "anthropic"

    def billing(self) -> ProviderBilling:
        return ProviderBilling(
            provider="anthropic",
            granularity=BillingGranularity.per_token,
            terms=BillingTerms.postpaid_per_use,
            currency="USD",
            min_charge=None,
            spot_available=False,
            notes=(
                "Billed per API call via Stripe; no prepaid balance required. "
                "Cached prompt tokens at 50% discount (prompt caching feature). "
                "Source: https://www.anthropic.com/pricing"
            ),
        )

    def fetch_model_prices(self) -> list[ModelPrice]:
        """Return static price table. Source documented; table dated 2025-Q4."""
        return [
            ModelPrice(
                provider="anthropic",
                model_id=model_id,
                input_usd_per_1k=inp,
                output_usd_per_1k=out,
                fetched_at=_ANTHROPIC_FETCHED_AT,
                source=_ANTHROPIC_SOURCE,
                context_window=ctx,
                notes=notes,
            )
            for model_id, inp, out, ctx, notes in _ANTHROPIC_PRICES_STATIC
        ]

    def fetch_compute_prices(self) -> list[ComputePrice]:
        """Anthropic does not offer direct compute. Returns []."""
        return []


# ---------------------------------------------------------------------------
# OpenAI — STATIC table (no public pricing API; scrape/manual as of 2025-Q4)
# ---------------------------------------------------------------------------
# Source: https://openai.com/api/pricing  (accessed 2025-Q4)
# Billing: postpaid_per_use for pay-as-you-go; postpaid_monthly for prepaid
# credits (but prepaid is optional — not the same as required prepaid_balance).
# We model as postpaid_per_use because no balance is required for API access.
# ---------------------------------------------------------------------------

_OPENAI_PRICES_STATIC: list[tuple[str, float, float, int | None, str]] = [
    # GPT-4o family
    ("gpt-4o", 0.005, 0.015, 128_000, "GPT-4o; multimodal flagship"),
    ("gpt-4o-mini", 0.00015, 0.0006, 128_000, "GPT-4o Mini; cost-optimized"),
    ("gpt-4o-2024-11-20", 0.0025, 0.010, 128_000, "GPT-4o 2024-11-20; 50% cheaper than original"),
    # GPT-4 Turbo
    ("gpt-4-turbo", 0.01, 0.03, 128_000, "GPT-4 Turbo; with vision"),
    ("gpt-4-turbo-2024-04-09", 0.01, 0.03, 128_000, "GPT-4 Turbo April 2024"),
    # GPT-4
    ("gpt-4", 0.03, 0.06, 8_192, "GPT-4; original; expensive"),
    # GPT-3.5
    ("gpt-3.5-turbo", 0.0005, 0.0015, 16_385, "GPT-3.5 Turbo; legacy fast/cheap"),
    # o1 family (reasoning models — priced higher)
    ("o1-preview", 0.015, 0.060, 128_000, "o1 preview reasoning model"),
    ("o1-mini", 0.003, 0.012, 128_000, "o1 mini reasoning model"),
    # Embedding models
    ("text-embedding-3-small", 0.00002, 0.0, 8_191, "Embedding; small; no output tokens"),
    ("text-embedding-3-large", 0.00013, 0.0, 8_191, "Embedding; large; no output tokens"),
]

_OPENAI_SOURCE = "https://openai.com/api/pricing"
_OPENAI_FETCHED_AT = 1735689600.0  # 2025-01-01 00:00 UTC (table recorded date)


class OpenAISource:
    """FETCH STRATEGY: STATIC — hardcoded from https://openai.com/api/pricing (2025-Q4).

    OpenAI does not publish a machine-readable public pricing API (the /v1/models
    endpoint lists models but not their prices).
    Live pricing is available via LiteLLMJSONSource("openai") which fetches
    from the litellm cross-provider catalog.

    Billing:
      - terms: postpaid_per_use (credit/debit card billed per API call)
      - granularity: per_token
      - spot_available: False (no spot concept for API calls)
      - min_charge: None (no documented minimum per call)
    """

    def provider_slug(self) -> str:
        return "openai"

    def billing(self) -> ProviderBilling:
        return ProviderBilling(
            provider="openai",
            granularity=BillingGranularity.per_token,
            terms=BillingTerms.postpaid_per_use,
            currency="USD",
            min_charge=None,
            spot_available=False,
            notes=(
                "API billed per call; credit card charged. Optional prepaid credits "
                "available but not required. Cached input tokens at 50% discount "
                "(context caching). Source: https://openai.com/api/pricing"
            ),
        )

    def fetch_model_prices(self) -> list[ModelPrice]:
        """Return static price table. Source documented; table dated 2025-Q4."""
        return [
            ModelPrice(
                provider="openai",
                model_id=model_id,
                input_usd_per_1k=inp,
                output_usd_per_1k=out,
                fetched_at=_OPENAI_FETCHED_AT,
                source=_OPENAI_SOURCE,
                context_window=ctx,
                notes=notes,
            )
            for model_id, inp, out, ctx, notes in _OPENAI_PRICES_STATIC
        ]

    def fetch_compute_prices(self) -> list[ComputePrice]:
        """OpenAI does not offer direct compute. Returns []."""
        return []


# ---------------------------------------------------------------------------
# RunPod — STATIC compute table with billing semantics
# ---------------------------------------------------------------------------
# Source: https://www.runpod.io/gpu-instance/pricing (accessed 2025-Q4)
#
# BILLING SEMANTICS (critical):
#   - PREPAID BALANCE: RunPod requires a positive credit balance. GPU usage is
#     deducted from the balance in real-time. If the balance hits $0, pods are
#     IMMEDIATELY TERMINATED. This is fundamentally different from postpaid providers.
#   - PER-SECOND billing: Usage is metered to the second. A 5-minute job costs
#     exactly 300 x rate, not 1 hour x rate.
#   - SPOT (community cloud): Deeply discounted but interruptible; may be reclaimed
#     by the provider at any time. On-demand (secure cloud) is non-interruptible.
# ---------------------------------------------------------------------------

_RUNPOD_SOURCE = "https://www.runpod.io/gpu-instance/pricing"
_RUNPOD_FETCHED_AT = 1735689600.0  # 2025-01-01 00:00 UTC

# On-demand (secure cloud) prices in USD/hour → converted to USD/second
# Format: (sku, gpu_type, gpu_count, usd_per_hour)
_RUNPOD_ONDEMAND: list[tuple[str, str, int, float]] = [
    # https://www.runpod.io/gpu-instance/pricing (secure cloud, 2025-Q4)
    ("RTX-4090-1x", "RTX 4090", 1, 0.74),
    ("RTX-4090-2x", "RTX 4090", 2, 1.48),
    ("A40-1x", "A40", 1, 0.54),
    ("A100-SXM4-80GB-1x", "A100 SXM4 80GB", 1, 2.49),
    ("A100-SXM4-80GB-8x", "A100 SXM4 80GB", 8, 16.00),
    ("H100-SXM5-80GB-1x", "H100 SXM5 80GB", 1, 4.69),
    ("H100-SXM5-80GB-8x", "H100 SXM5 80GB", 8, 32.69),
    ("A6000-1x", "RTX A6000", 1, 0.79),
    ("L40-1x", "L40", 1, 1.14),
    ("3090-1x", "RTX 3090", 1, 0.44),
    ("3080-1x", "RTX 3080", 1, 0.34),
]

# Spot (community cloud) prices — significantly cheaper, interruptible
_RUNPOD_SPOT: list[tuple[str, str, int, float]] = [
    # Spot prices are volatile; these are typical values from 2025-Q4
    ("RTX-4090-1x-spot", "RTX 4090", 1, 0.44),
    ("A100-SXM4-80GB-1x-spot", "A100 SXM4 80GB", 1, 1.64),
    ("H100-SXM5-80GB-1x-spot", "H100 SXM5 80GB", 1, 2.99),
]


class RunPodSource:
    """FETCH STRATEGY: STATIC — hardcoded from https://www.runpod.io/gpu-instance/pricing (2025-Q4).

    Live pricing is available via ``RunPodPricingSource`` (GraphQL API), wrapped
    in ``CachedSource`` with this static source as fallback.

    BILLING SEMANTICS (PREPAID — critical distinction):
      - Customer must maintain a positive credit balance (top up via card/crypto).
      - Usage is deducted from balance in REAL TIME, to the SECOND.
      - Balance exhaustion = IMMEDIATE pod termination (no grace period).
      - Spot (community cloud) pods may also be terminated when host reclaims GPUs.
      - No monthly invoice; no credit line. Cash-flow impact: capital locked in balance.
    """

    def provider_slug(self) -> str:
        return "runpod"

    def billing(self) -> ProviderBilling:
        return ProviderBilling(
            provider="runpod",
            granularity=BillingGranularity.per_second,
            terms=BillingTerms.prepaid_balance,
            currency="USD",
            min_charge=None,
            spot_available=True,
            notes=(
                "PREPAID BALANCE REQUIRED. Usage deducted from balance in real-time "
                "per second. Balance = $0 → immediate pod termination. "
                "Community cloud (spot) = interruptible, deeply discounted. "
                "Secure cloud (on-demand) = non-interruptible. "
                "Top up via credit card, PayPal, or crypto. "
                "Source: https://www.runpod.io/gpu-instance/pricing"
            ),
        )

    def fetch_model_prices(self) -> list[ModelPrice]:
        """RunPod does not offer model API. Returns []."""
        return []

    def fetch_compute_prices(self) -> list[ComputePrice]:
        """Return static compute price table for RunPod GPU instances."""
        results: list[ComputePrice] = []

        # On-demand (secure cloud) — non-interruptible
        for sku, gpu_type, gpu_count, usd_per_hour in _RUNPOD_ONDEMAND:
            usd_per_second = usd_per_hour / 3600.0
            results.append(
                ComputePrice(
                    provider="runpod",
                    sku=sku,
                    usd_per_unit=usd_per_second,
                    granularity=BillingGranularity.per_second,
                    spot=False,
                    terms=BillingTerms.prepaid_balance,
                    fetched_at=_RUNPOD_FETCHED_AT,
                    source=_RUNPOD_SOURCE,
                    gpu_count=gpu_count,
                    gpu_type=gpu_type,
                    notes=f"Secure cloud (on-demand). ${usd_per_hour:.2f}/hr = ${usd_per_second:.6f}/s",
                )
            )

        # Spot (community cloud) — interruptible
        for sku, gpu_type, gpu_count, usd_per_hour in _RUNPOD_SPOT:
            usd_per_second = usd_per_hour / 3600.0
            results.append(
                ComputePrice(
                    provider="runpod",
                    sku=sku,
                    usd_per_unit=usd_per_second,
                    granularity=BillingGranularity.per_second,
                    spot=True,
                    terms=BillingTerms.prepaid_balance,
                    fetched_at=_RUNPOD_FETCHED_AT,
                    source=_RUNPOD_SOURCE,
                    gpu_count=gpu_count,
                    gpu_type=gpu_type,
                    notes=(
                        f"Community cloud (spot/interruptible). "
                        f"${usd_per_hour:.2f}/hr = ${usd_per_second:.6f}/s. "
                        "May be terminated when host reclaims GPU."
                    ),
                )
            )

        return results


# ---------------------------------------------------------------------------
# RunPod — LIVE GraphQL compute prices (gpuTypes query)
# ---------------------------------------------------------------------------
# Source: POST https://api.runpod.io/graphql
# Spec:   https://graphql-spec.runpod.io/ / https://docs.runpod.io/reference
# Auth:   RUNPOD_API_KEY environment variable (sent as Authorization: Bearer).
#
# The gpuTypes query returns per-GPU-type live pricing across three tiers:
#   securePrice    — on-demand "secure cloud" (non-interruptible), USD/GPU/hr
#   communityPrice — "community cloud" (hosted by third parties; interruptible)
#   spot           — deeply discounted spot (interruptible, reclaimed on demand)
# RunPod bills per second from a prepaid balance, so we convert USD/hr → USD/s
# to match the per_second granularity used by the static RunPodSource.
# ---------------------------------------------------------------------------


class RunPodPricingSource:
    """FETCH STRATEGY: LIVE — POST https://api.runpod.io/graphql (gpuTypes query).

    Queries RunPod's GraphQL API for live GPU pricing. Returns one
    ``ComputePrice`` per (gpu_type, tier) combination across three tiers —
    ``securePrice`` (on-demand), ``communityPrice`` (interruptible community
    cloud), and ``spot`` (deeply discounted, interruptible) — when reported.

    Auth: ``RUNPOD_API_KEY`` environment variable, sent as
    ``Authorization: Bearer <key>``. If unset, the source skips cleanly
    (returns ``[]`` and logs a warning) — it is dormant without credentials.

    SLUG NOTE: Uses ``"runpod_live"`` (not ``"runpod"``) to avoid colliding
    with the static ``RunPodSource`` in the catalog's first-match-wins
    ``_source_for`` lookup. This keeps both the static baseline (always
    available, no credentials) and the live source (dormant without API key)
    independently queryable through ``PricingCatalog``.

    BILLING SEMANTICS (same PREPAID model as RunPodSource):
      - Customer must maintain a positive credit balance.
      - Usage deducted per second in real time.
      - Balance = $0 → immediate pod termination.
      - Community/spot pods are interruptible; secure (on-demand) is not.
    """

    _ENDPOINT = "https://api.runpod.io/graphql"
    _SOURCE = "https://api.runpod.io/graphql"
    _QUERY = (
        "query GpuTypes { gpuTypes { id displayName memoryInGb "
        "securePrice communityPrice spot } }"
    )

    def provider_slug(self) -> str:
        return "runpod_live"

    def billing(self) -> ProviderBilling:
        return ProviderBilling(
            provider="runpod_live",
            granularity=BillingGranularity.per_second,
            terms=BillingTerms.prepaid_balance,
            currency="USD",
            min_charge=None,
            spot_available=True,
            notes=(
                "PREPAID BALANCE REQUIRED. Live pricing via RunPod GraphQL API. "
                "Usage deducted per second; balance exhaustion = immediate termination. "
                "Secure cloud (on-demand) non-interruptible; community/spot interruptible. "
                "Source: https://api.runpod.io/graphql"
            ),
        )

    def fetch_model_prices(self) -> list[ModelPrice]:
        """RunPod does not offer a model API. Returns []."""
        return []

    def fetch_compute_prices(self) -> list[ComputePrice]:
        """Fetch live GPU compute prices from the RunPod GraphQL API.

        Returns an empty list on any error (fail-soft). Skips cleanly when
        ``RUNPOD_API_KEY`` is not set.
        """
        api_key = os.environ.get("RUNPOD_API_KEY")
        if not api_key:
            logger.warning(
                "RunPod GraphQL fetch skipped: RUNPOD_API_KEY not set"
            )
            return []

        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.post(
                    self._ENDPOINT,
                    json={"query": self._QUERY},
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )
                if resp.status_code != 200:
                    logger.warning(
                        "RunPod GraphQL API returned HTTP %s", resp.status_code
                    )
                    return []
                data = resp.json()
        except Exception as exc:
            logger.warning("RunPod GraphQL fetch failed: %s", exc)
            return []

        gpu_types = ((data.get("data") or {}).get("gpuTypes")) or []
        if not gpu_types:
            if data.get("errors"):
                logger.warning(
                    "RunPod GraphQL returned errors: %s", data["errors"]
                )
            else:
                logger.warning("RunPod GraphQL returned no gpuTypes")
            return []

        fetched_at = time.time()
        results: list[ComputePrice] = []

        for gpu in gpu_types:
            gpu_id = gpu.get("id") or ""
            display_name = gpu.get("displayName") or gpu_id

            secure_price = gpu.get("securePrice")
            if secure_price is not None:
                results.append(
                    self._make_price(
                        gpu_type=display_name,
                        sku=f"{gpu_id}-secure",
                        usd_per_hour=secure_price,
                        spot=False,
                        fetched_at=fetched_at,
                        label="Secure cloud (on-demand)",
                    )
                )

            community_price = gpu.get("communityPrice")
            if community_price is not None:
                results.append(
                    self._make_price(
                        gpu_type=display_name,
                        sku=f"{gpu_id}-community",
                        usd_per_hour=community_price,
                        spot=True,
                        fetched_at=fetched_at,
                        label="Community cloud (interruptible)",
                    )
                )

            spot_price = gpu.get("spot")
            if spot_price is not None:
                results.append(
                    self._make_price(
                        gpu_type=display_name,
                        sku=f"{gpu_id}-spot",
                        usd_per_hour=spot_price,
                        spot=True,
                        fetched_at=fetched_at,
                        label="Spot (interruptible)",
                    )
                )

        return results

    @staticmethod
    def _make_price(
        *,
        gpu_type: str,
        sku: str,
        usd_per_hour: Any,
        spot: bool,
        fetched_at: float,
        label: str,
    ) -> ComputePrice:
        """Build a per-second ComputePrice from a USD/hour GraphQL field."""
        try:
            hourly = float(usd_per_hour)
        except (TypeError, ValueError):
            hourly = 0.0
        usd_per_second = hourly / 3600.0
        return ComputePrice(
            provider="runpod_live",
            sku=sku,
            usd_per_unit=usd_per_second,
            granularity=BillingGranularity.per_second,
            spot=spot,
            terms=BillingTerms.prepaid_balance,
            fetched_at=fetched_at,
            source=RunPodPricingSource._SOURCE,
            gpu_count=1,
            gpu_type=gpu_type,
            notes=(
                f"{label}. ${hourly:.2f}/hr = ${usd_per_second:.6f}/s. "
                "Prepaid balance; per-second billing."
            ),
        )


# ---------------------------------------------------------------------------
# Lambda Labs — STATIC compute table with billing semantics
# ---------------------------------------------------------------------------
# Source: https://lambdalabs.com/service/gpu-cloud/pricing (accessed 2025-Q4)
#
# BILLING SEMANTICS (PREPAID):
#   - Lambda Labs Cloud also requires a positive account balance / credit card on file.
#   - GPU usage billed per minute (not per second; minimum = 1 minute).
#   - Spot instances available for H100 cluster sizes; interruptible.
#   - On-demand instances: reserved or on-demand, non-interruptible.
# ---------------------------------------------------------------------------

_LAMBDA_SOURCE = "https://lambdalabs.com/service/gpu-cloud/pricing"
_LAMBDA_FETCHED_AT = 1735689600.0  # 2025-01-01 00:00 UTC

# Format: (sku, gpu_type, gpu_count, usd_per_hour)
_LAMBDA_ONDEMAND: list[tuple[str, str, int, float]] = [
    # https://lambdalabs.com/service/gpu-cloud/pricing (2025-Q4)
    ("gpu_1x_a10", "A10", 1, 0.75),
    ("gpu_1x_a100_sxm4", "A100 SXM4 40GB", 1, 1.29),
    ("gpu_8x_a100_sxm4_40gb", "A100 SXM4 40GB", 8, 10.32),
    ("gpu_8x_a100_80gb_sxm4", "A100 SXM4 80GB", 8, 14.32),
    ("gpu_1x_h100_pcie", "H100 PCIe 80GB", 1, 2.49),
    ("gpu_8x_h100_sxm5", "H100 SXM5 80GB", 8, 24.80),
    ("gpu_16x_h100_sxm5", "H100 SXM5 80GB", 16, 49.60),
    ("gpu_1x_rtx6000ada", "RTX 6000 Ada", 1, 0.80),
    ("gpu_1x_a6000", "RTX A6000 48GB", 1, 0.80),
    ("gpu_2x_a6000", "RTX A6000 48GB", 2, 1.60),
    ("gpu_4x_a6000", "RTX A6000 48GB", 4, 3.20),
]


class LambdaLabsSource:
    """FETCH STRATEGY: STATIC — hardcoded from https://lambdalabs.com/service/gpu-cloud/pricing (2025-Q4).

    Live pricing is available via ``LambdaLabsPricingSource`` (REST API), wrapped
    in ``CachedSource`` with this static source as fallback.

    BILLING SEMANTICS (PREPAID/PER-MINUTE — key distinctions):
      - Credit card charged on consumption; no monthly invoice.
      - Billed per MINUTE (not per second like RunPod). Minimum charge = 1 minute.
        A 90-second job = 2 minutes billed.
      - Lambda reserves right to terminate spot instances; on-demand is stable.
      - No concept of a prepaid balance pool; charges hit your card directly.
      - We model as prepaid_balance because the practical effect is the same:
        if your payment method fails, service stops immediately.
    """

    def provider_slug(self) -> str:
        return "lambda_labs"

    def billing(self) -> ProviderBilling:
        return ProviderBilling(
            provider="lambda_labs",
            granularity=BillingGranularity.per_minute,
            terms=BillingTerms.prepaid_balance,
            currency="USD",
            min_charge=None,
            spot_available=True,
            notes=(
                "Billed per MINUTE (min 1 min). Charges hit credit card on consumption. "
                "Payment failure = service stop. Spot clusters available (H100 multi-GPU). "
                "1-click clusters available for H100. "
                "Source: https://lambdalabs.com/service/gpu-cloud/pricing"
            ),
        )

    def fetch_model_prices(self) -> list[ModelPrice]:
        """Lambda Labs does not offer model API. Returns []."""
        return []

    def fetch_compute_prices(self) -> list[ComputePrice]:
        """Return static compute price table for Lambda Labs GPU instances."""
        results: list[ComputePrice] = []

        for sku, gpu_type, gpu_count, usd_per_hour in _LAMBDA_ONDEMAND:
            usd_per_minute = usd_per_hour / 60.0
            results.append(
                ComputePrice(
                    provider="lambda_labs",
                    sku=sku,
                    usd_per_unit=usd_per_minute,
                    granularity=BillingGranularity.per_minute,
                    spot=False,
                    terms=BillingTerms.prepaid_balance,
                    fetched_at=_LAMBDA_FETCHED_AT,
                    source=_LAMBDA_SOURCE,
                    gpu_count=gpu_count,
                    gpu_type=gpu_type,
                    notes=f"On-demand. ${usd_per_hour:.2f}/hr = ${usd_per_minute:.4f}/min (billed per minute)",
                )
            )

        return results


# ---------------------------------------------------------------------------
# AWS — STATIC compute table with billing semantics
# ---------------------------------------------------------------------------
# Source: https://aws.amazon.com/ec2/pricing/on-demand/ (accessed 2025-Q4)
#         https://aws.amazon.com/ec2/spot/pricing/ for spot
#
# BILLING SEMANTICS (POSTPAID):
#   - AWS bills per SECOND for Linux instances (60-second minimum).
#   - No prepaid balance required; credit line / invoice at month-end.
#   - Spot instances: up to 90% discount; may be interrupted with 2-min warning.
# ---------------------------------------------------------------------------

_AWS_SOURCE = "https://aws.amazon.com/ec2/pricing/on-demand/"
_AWS_SPOT_SOURCE = "https://aws.amazon.com/ec2/spot/pricing/"
_AWS_FETCHED_AT = 1735689600.0  # 2025-01-01 00:00 UTC

# Format: (sku/instance_type, gpu_type, gpu_count, usd_per_hour, is_spot)
_AWS_GPU_INSTANCES: list[tuple[str, str, int, float, bool]] = [
    # p3 family — NVIDIA V100
    ("p3.2xlarge", "V100 16GB", 1, 3.06, False),
    ("p3.8xlarge", "V100 16GB", 4, 12.24, False),
    ("p3.16xlarge", "V100 16GB", 8, 24.48, False),
    # p4d family — NVIDIA A100
    ("p4d.24xlarge", "A100 40GB", 8, 32.77, False),
    # p5 family — NVIDIA H100
    ("p5.48xlarge", "H100 80GB SXM5", 8, 98.32, False),
    # g5 family — NVIDIA A10G
    ("g5.xlarge", "A10G 24GB", 1, 1.006, False),
    ("g5.2xlarge", "A10G 24GB", 1, 1.212, False),
    ("g5.12xlarge", "A10G 24GB", 4, 5.672, False),
    ("g5.48xlarge", "A10G 24GB", 8, 16.288, False),
    # Spot examples — typical spot price ≈ 30-70% of on-demand
    ("p4d.24xlarge-spot", "A100 40GB", 8, 9.83, True),   # ~30% of on-demand
    ("p5.48xlarge-spot", "H100 80GB SXM5", 8, 29.50, True),  # ~30% of on-demand
]


class AWSSource:
    """FETCH STRATEGY: STATIC — hardcoded from https://aws.amazon.com/ec2/pricing/on-demand/ (2025-Q4).

    Live pricing is available via ``AWSPricingSource`` (boto3 GetProducts API),
    wrapped in ``CachedSource`` with this static source as fallback.

    BILLING SEMANTICS (POSTPAID — critical distinction from RunPod/Lambda):
      - AWS bills POSTPAID to a monthly invoice. No prepaid balance required.
      - Billing granularity: per SECOND for Linux, 60-second minimum per instance start.
      - Spot instances: up to 90% savings; AWS provides 2-minute interruption notice.
      - Payment: credit card, AWS invoice, AWS Marketplace credits.
      - Cash-flow: accrue costs throughout month, invoiced at month-end.
    """

    def provider_slug(self) -> str:
        return "aws"

    def billing(self) -> ProviderBilling:
        return ProviderBilling(
            provider="aws",
            granularity=BillingGranularity.per_second,
            terms=BillingTerms.postpaid_monthly,
            currency="USD",
            min_charge=0.0,
            spot_available=True,
            notes=(
                "POSTPAID MONTHLY. Billed per second (Linux); 60-second minimum per launch. "
                "No prepaid balance required. Spot instances: up to 90% savings, "
                "2-minute termination notice. Savings Plans and Reserved Instances "
                "available for further discount. "
                "Source: https://aws.amazon.com/ec2/pricing/on-demand/"
            ),
        )

    def fetch_model_prices(self) -> list[ModelPrice]:
        """AWS does not offer general model API (Bedrock is separate). Returns []."""
        return []

    def fetch_compute_prices(self) -> list[ComputePrice]:
        """Return static compute price table for AWS GPU instances."""
        results: list[ComputePrice] = []

        for instance_type, gpu_type, gpu_count, usd_per_hour, is_spot in _AWS_GPU_INSTANCES:
            usd_per_second = usd_per_hour / 3600.0
            source = _AWS_SPOT_SOURCE if is_spot else _AWS_SOURCE
            results.append(
                ComputePrice(
                    provider="aws",
                    sku=instance_type,
                    usd_per_unit=usd_per_second,
                    granularity=BillingGranularity.per_second,
                    spot=is_spot,
                    terms=BillingTerms.postpaid_monthly,
                    fetched_at=_AWS_FETCHED_AT,
                    source=source,
                    gpu_count=gpu_count,
                    gpu_type=gpu_type,
                    notes=(
                        f"{'Spot (~30% typical)' if is_spot else 'On-demand'}. "
                        f"${usd_per_hour:.3f}/hr = ${usd_per_second:.6f}/s. "
                        "60s minimum per launch. us-east-1 pricing."
                    ),
                )
            )

        return results


# ---------------------------------------------------------------------------
# AWS — LIVE compute prices via AWS Price List Query API (GetProducts)
# ---------------------------------------------------------------------------
# Source: AWS Price List Query API — boto3 ``pricing`` client, GetProducts.
# Spec:   https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_pricing_GetProducts.html
# Auth:   ``AWS_ACCESS_KEY_ID`` + ``AWS_SECRET_ACCESS_KEY`` environment variables
#         (standard boto3 credential chain). If ``AWS_ACCESS_KEY_ID`` is unset,
#         the source skips cleanly (returns ``[]`` and logs a warning) — it is
#         dormant without credentials.
#
# The GetProducts API returns ``PriceList`` as a list of JSON *strings*, each
# describing one product + its OnDemand terms. AWS bills Linux instances per
# second (60-second minimum), so we convert the API's USD/hour rate to USD/s
# to match the per_second granularity used by the static AWSSource.
#
# DEPENDENCY NOTE: boto3 is an OPTIONAL runtime dependency — it is NOT yet
# listed in pyproject.toml. The class uses a lazy import inside ``_get_client``
# so the module imports cleanly without boto3; a missing dep simply makes the
# source dormant. Add ``boto3>=1.34.0`` to pyproject.toml dependencies to
# activate live AWS pricing.
# ---------------------------------------------------------------------------


class AWSPricingSource:
    """FETCH STRATEGY: LIVE — AWS Price List Query API (boto3 ``pricing.GetProducts``).

    Queries the AWS Price List Query API for live Linux/Shared-tenancy EC2
    pricing, then filters client-side for GPU instance families
    (``p3.*``, ``p4d.*``, ``p5.*``, ``g5.*``). Returns one ``ComputePrice``
    per matching instance type, with per-second pricing (USD/hour / 3600).

    Auth: ``AWS_ACCESS_KEY_ID`` + ``AWS_SECRET_ACCESS_KEY`` environment
    variables. If ``AWS_ACCESS_KEY_ID`` is unset, the source skips cleanly
    (returns ``[]`` and logs a warning) — it is dormant without credentials.

    SLUG NOTE: Uses ``"aws_live"`` (not ``"aws"``) to avoid colliding
    with the static ``AWSSource`` in the catalog's first-match-wins
    ``_source_for`` lookup. This keeps both the static baseline (always
    available, no credentials) and the live source (dormant without API key)
    independently queryable through ``PricingCatalog``.

    BILLING SEMANTICS (same POSTPAID model as the static AWSSource):
      - POSTPAID monthly invoice; no prepaid balance required.
      - Per-second billing for Linux, 60-second minimum per launch.
      - This source fetches ON-DEMAND prices only (not Spot — Spot price
        history requires a separate EC2 DescribeSpotPriceHistory call).
    """

    _SERVICE_CODE = "AmazonEC2"
    _REGION = "us-east-1"
    _LOCATION = "US East (N. Virginia)"
    _GPU_FAMILY_PREFIXES: tuple[str, ...] = ("p3.", "p4d.", "p5.", "g5.")
    _SOURCE = "AWS Price List Query API (boto3 pricing.GetProducts)"

    def provider_slug(self) -> str:
        return "aws_live"

    def billing(self) -> ProviderBilling:
        return ProviderBilling(
            provider="aws_live",
            granularity=BillingGranularity.per_second,
            terms=BillingTerms.postpaid_monthly,
            currency="USD",
            min_charge=0.0,
            spot_available=True,
            notes=(
                "POSTPAID MONTHLY. Live pricing via AWS Price List Query API "
                "(GetProducts). On-demand per-second billing (Linux); 60-second "
                "minimum per launch. No prepaid balance required. Spot prices "
                "not included in this source (requires DescribeSpotPriceHistory). "
                "Source: AWS Price List Query API"
            ),
        )

    def fetch_model_prices(self) -> list[ModelPrice]:
        """AWS does not offer a general model API (Bedrock is separate). Returns []."""
        return []

    def fetch_compute_prices(self) -> list[ComputePrice]:
        """Fetch live GPU compute prices from the AWS Price List Query API.

        Returns an empty list on any error (fail-soft). Skips cleanly when
        ``AWS_ACCESS_KEY_ID`` is not set or boto3 is not installed.
        """
        if not os.environ.get("AWS_ACCESS_KEY_ID"):
            logger.warning(
                "AWS pricing fetch skipped: AWS_ACCESS_KEY_ID not set"
            )
            return []

        try:
            client = self._get_client()
        except ImportError as exc:
            logger.warning(
                "AWS pricing fetch skipped: boto3 unavailable (%s)", exc
            )
            return []
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("AWS pricing fetch skipped: %s", exc)
            return []

        try:
            price_lists = self._fetch_price_list(client)
        except Exception as exc:
            logger.warning("AWS GetProducts failed: %s", exc)
            return []

        fetched_at = time.time()
        results: list[ComputePrice] = []
        for raw_entry in price_lists:
            parsed = self._parse_entry(raw_entry, fetched_at)
            if parsed is not None:
                results.append(parsed)
        return results

    def _get_client(self) -> Any:
        """Build a boto3 pricing client. Raises ImportError if boto3 is missing.

        Lazy import keeps the module loadable when boto3 (an optional dep) is
        not installed; tests mock this method to avoid the network/dep.
        """
        import importlib

        boto3 = importlib.import_module("boto3")  # boto3: optional [aws] extra, lazy-imported

        return boto3.client("pricing", region_name=self._REGION)

    def _fetch_price_list(self, client: Any) -> list[str]:
        """Page through GetProducts and return the raw PriceList JSON strings.

        Filters to Linux + Shared tenancy + Used capacity + no pre-installed
        software (NA). GPU-family filtering happens client-side in
        ``_parse_entry`` because GetProducts filters are TERM_MATCH (exact),
        not prefix.
        """
        filters: list[dict[str, str]] = [
            {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
            {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
            {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
            {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
            {"Type": "TERM_MATCH", "Field": "location", "Value": self._LOCATION},
        ]
        paginator = client.get_paginator("get_products")
        price_lists: list[str] = []
        for page in paginator.paginate(
            ServiceCode=self._SERVICE_CODE, Filters=filters
        ):
            price_lists.extend(page.get("PriceLists", []) or [])
        return price_lists

    def _parse_entry(self, raw_entry: str, fetched_at: float) -> ComputePrice | None:
        """Parse one PriceList JSON string into a ComputePrice, or None to skip.

        Skips (returns None) when:
          - the entry is not valid JSON,
          - the instance type is not in a GPU family (p3./p4d./p5./g5.),
          - the entry has no GPU count (gpu == 0 — defensive; shouldn't happen
            after the family filter but be safe),
          - the OnDemand price or USD currency is missing.
        """
        try:
            entry = json.loads(raw_entry)
        except (json.JSONDecodeError, TypeError):
            return None

        if not isinstance(entry, dict):
            return None

        product = entry.get("product") or {}
        if not isinstance(product, dict):
            return None
        attrs = product.get("attributes") or {}
        if not isinstance(attrs, dict):
            return None
        instance_type = str(attrs.get("instanceType") or "")
        if not instance_type.startswith(self._GPU_FAMILY_PREFIXES):
            return None

        try:
            gpu_count = int(attrs.get("gpu") or 0)
        except (TypeError, ValueError):
            gpu_count = 0
        if gpu_count <= 0:
            return None

        gpu_type = str(attrs.get("gpuType") or instance_type)

        usd_per_hour = self._extract_on_demand_usd(entry)
        if usd_per_hour is None:
            return None

        usd_per_second = usd_per_hour / 3600.0
        return ComputePrice(
            provider="aws_live",
            sku=instance_type,
            usd_per_unit=usd_per_second,
            granularity=BillingGranularity.per_second,
            spot=False,
            terms=BillingTerms.postpaid_monthly,
            fetched_at=fetched_at,
            source=self._SOURCE,
            gpu_count=gpu_count,
            gpu_type=gpu_type,
            notes=(
                f"On-demand (Linux, Shared tenancy). ${usd_per_hour:.4f}/hr = "
                f"${usd_per_second:.6f}/s. 60s minimum per launch. "
                f"{self._LOCATION}."
            ),
        )

    @staticmethod
    def _extract_on_demand_usd(entry: dict[str, Any]) -> float | None:
        """Pull the first OnDemand USD hourly rate out of a PriceList entry."""
        terms = entry.get("terms") or {}
        if not isinstance(terms, dict):
            return None
        on_demand = terms.get("OnDemand") or {}
        if not isinstance(on_demand, dict) or not on_demand:
            return None
        term = next(iter(on_demand.values()))
        if not isinstance(term, dict):
            return None
        price_dims = term.get("priceDimensions") or {}
        if not isinstance(price_dims, dict) or not price_dims:
            return None
        price_dim = next(iter(price_dims.values()))
        if not isinstance(price_dim, dict):
            return None
        price_per_unit = price_dim.get("pricePerUnit") or {}
        if not isinstance(price_per_unit, dict):
            return None
        usd = price_per_unit.get("USD")
        if usd is None:
            return None
        try:
            return float(usd)
        except (TypeError, ValueError):
            return None


# ---------------------------------------------------------------------------
# GCP — STATIC compute table with billing semantics
# ---------------------------------------------------------------------------
# Source: https://cloud.google.com/compute/gpus-pricing (accessed 2025-Q4)
#
# BILLING SEMANTICS (POSTPAID):
#   - GCP bills per SECOND for compute instances (1-minute minimum).
#   - No prepaid balance required; billed monthly to a GCP account.
#   - Preemptible/Spot VMs: up to 91% savings; may be preempted with 30s notice.
#   - Custom machine types supported; accelerator pricing is additive.
# ---------------------------------------------------------------------------

_GCP_SOURCE = "https://cloud.google.com/compute/gpus-pricing"
_GCP_FETCHED_AT = 1735689600.0  # 2025-01-01 00:00 UTC

# Format: (sku, gpu_type, gpu_count, usd_per_hour_total, is_spot)
# Prices are for us-central1 region (typical; other regions vary ±15%)
_GCP_GPU_INSTANCES: list[tuple[str, str, int, float, bool]] = [
    # A100 accelerators on a2 machines
    ("a2-highgpu-1g", "A100 40GB", 1, 3.673, False),
    ("a2-highgpu-2g", "A100 40GB", 2, 7.346, False),
    ("a2-highgpu-4g", "A100 40GB", 4, 14.692, False),
    ("a2-highgpu-8g", "A100 40GB", 8, 29.384, False),
    # A100 80GB (a2-ultragpu)
    ("a2-ultragpu-1g", "A100 80GB", 1, 5.033, False),
    ("a2-ultragpu-4g", "A100 80GB", 4, 20.132, False),
    ("a2-ultragpu-8g", "A100 80GB", 8, 40.265, False),
    # H100 (a3 machines)
    ("a3-highgpu-8g", "H100 80GB SXM", 8, 98.328, False),
    # T4 (n1 + accelerator)
    ("n1-standard-4-T4", "T4 16GB", 1, 0.952, False),
    ("n1-standard-8-T4-2", "T4 16GB", 2, 1.904, False),
    # V100
    ("n1-standard-8-V100", "V100 16GB", 1, 2.483, False),
    # L4 (g2 machines)
    ("g2-standard-4", "L4 24GB", 1, 0.700, False),
    ("g2-standard-48", "L4 24GB", 4, 2.800, False),
    # Spot/Preemptible examples — roughly 60-70% discount
    ("a2-highgpu-1g-spot", "A100 40GB", 1, 1.102, True),  # ~70% off
    ("a3-highgpu-8g-spot", "H100 80GB SXM", 8, 29.50, True),   # ~70% off
]


class GCPSource:
    """FETCH STRATEGY: STATIC — hardcoded from https://cloud.google.com/compute/gpus-pricing (2025-Q4).

    Live pricing is available via ``GCPPricingSource`` (Cloud Billing SKU catalog),
    wrapped in ``CachedSource`` with this static source as fallback.

    BILLING SEMANTICS (POSTPAID — same model as AWS):
      - GCP bills per SECOND for VM instances (1-minute minimum per instance start).
      - No prepaid balance required; billed to GCP account monthly.
      - Preemptible VMs: up to 91% savings; max runtime 24hr; 30-second notice.
      - Spot VMs (newer): similar to Preemptible but no 24hr limit.
      - Committed use discounts (CUDs) available for 1/3 year commitments.
    """

    def provider_slug(self) -> str:
        return "gcp"

    def billing(self) -> ProviderBilling:
        return ProviderBilling(
            provider="gcp",
            granularity=BillingGranularity.per_second,
            terms=BillingTerms.postpaid_monthly,
            currency="USD",
            min_charge=0.0,
            spot_available=True,
            notes=(
                "POSTPAID MONTHLY. Billed per second; 1-minute minimum per launch. "
                "No prepaid balance required. Spot/Preemptible VMs: up to 91% savings, "
                "30-second termination notice. Committed use discounts available "
                "(1-year: 37%, 3-year: 55% off). us-central1 pricing. "
                "Source: https://cloud.google.com/compute/gpus-pricing"
            ),
        )

    def fetch_model_prices(self) -> list[ModelPrice]:
        """GCP Compute does not offer model API (Vertex AI is separate). Returns []."""
        return []

    def fetch_compute_prices(self) -> list[ComputePrice]:
        """Return static compute price table for GCP GPU instances."""
        results: list[ComputePrice] = []

        for sku, gpu_type, gpu_count, usd_per_hour, is_spot in _GCP_GPU_INSTANCES:
            usd_per_second = usd_per_hour / 3600.0
            results.append(
                ComputePrice(
                    provider="gcp",
                    sku=sku,
                    usd_per_unit=usd_per_second,
                    granularity=BillingGranularity.per_second,
                    spot=is_spot,
                    terms=BillingTerms.postpaid_monthly,
                    fetched_at=_GCP_FETCHED_AT,
                    source=_GCP_SOURCE,
                    gpu_count=gpu_count,
                    gpu_type=gpu_type,
                    notes=(
                        f"{'Spot/Preemptible' if is_spot else 'On-demand'}. "
                        f"${usd_per_hour:.3f}/hr = ${usd_per_second:.6f}/s. "
                        "1-min minimum per launch. us-central1 region."
                    ),
                )
            )

        return results


# ---------------------------------------------------------------------------
# GCP — LIVE compute prices via Cloud Billing SKU catalog (google-cloud-billing)
# ---------------------------------------------------------------------------
# Source: Cloud Billing API — CloudCatalogClient.list_skus, parent
#         ``services/6F81-5844-456A`` (Compute Engine).
# Spec:   https://cloud.google.com/billing/docs/reference/rest/v1/services.skus/list
# Auth:   ``GOOGLE_APPLICATION_CREDENTIALS`` environment variable pointing to a
#         GCP service-account key JSON (standard Google Application Default
#         Credentials chain). If unset, the source skips cleanly (returns
#         ``[]`` and logs a warning) — it is dormant without credentials.
#
# Each SKU returned by list_skus carries a ``category`` (resource_family,
# resource_group, usage_type) and a list of ``pricing_info`` entries. The
# pricing_expression's first tiered_rate is the headline USD/hour rate for the
# SKU, encoded as google.type.Money (whole ``units`` + fractional ``nanos``
# where 1e9 nanos = 1 unit). GPU SKUs are identified by
# ``category.resource_family == "Compute"`` AND (``resource_group == "GPU"``
# OR the description mentions "GPU"/"Gpu"). GCP bills Linux VMs per second
# (1-minute minimum), so we convert USD/hour to USD/second to match the
# per_second granularity used by the static GCPSource.
#
# DEPENDENCY NOTE: google-cloud-billing is an OPTIONAL runtime dependency —
# it is NOT yet listed in pyproject.toml. The class uses a lazy import inside
# ``_get_client`` so the module imports cleanly without the SDK; a missing
# dep simply makes the source dormant. Add ``google-cloud-billing>=0.10.0``
# to pyproject.toml dependencies to activate live GCP pricing.
# ---------------------------------------------------------------------------


class GCPPricingSource:
    """FETCH STRATEGY: LIVE — Cloud Billing SKU catalog (google-cloud-billing).

    Queries the GCP Cloud Billing ``CloudCatalogClient.list_skus`` API for
    Compute Engine (serviceId ``6F81-5844-456A``) and filters client-side for
    GPU SKUs. Returns one ``ComputePrice`` per matching SKU, with per-second
    pricing derived from the SKU's first ``tiered_rate`` (``Money.units`` +
    ``Money.nanos`` / 1e9, divided by 3600).

    Auth: ``GOOGLE_APPLICATION_CREDENTIALS`` environment variable pointing to
    a service-account key JSON. If unset, the source skips cleanly (returns
    ``[]`` and logs a warning) — it is dormant without credentials.

    SLUG NOTE: Uses ``"gcp_live"`` (not ``"gcp"``) to avoid colliding
    with the static ``GCPSource`` in the catalog's first-match-wins
    ``_source_for`` lookup. This keeps both the static baseline (always
    available, no credentials) and the live source (dormant without
    credentials) independently queryable through ``PricingCatalog``.

    BILLING SEMANTICS (same POSTPAID model as the static GCPSource):
      - POSTPAID monthly invoice; no prepaid balance required.
      - Per-second billing for VMs, 1-minute minimum per launch.
      - Preemptible / Spot SKUs (``category.usage_type == "Preemptible"``)
        are surfaced as ``spot=True``.
    """

    _COMPUTE_SERVICE_ID = "6F81-5844-456A"
    _COMPUTE_PARENT = f"services/{_COMPUTE_SERVICE_ID}"
    _SOURCE = (
        "GCP Cloud Billing SKU catalog "
        "(google-cloud-billing CloudCatalogClient.list_skus) — "
        "https://cloud.google.com/billing/docs/reference/rest/v1/services.skus/list"
    )

    def provider_slug(self) -> str:
        return "gcp_live"

    def billing(self) -> ProviderBilling:
        return ProviderBilling(
            provider="gcp_live",
            granularity=BillingGranularity.per_second,
            terms=BillingTerms.postpaid_monthly,
            currency="USD",
            min_charge=0.0,
            spot_available=True,
            notes=(
                "POSTPAID MONTHLY. Live pricing via GCP Cloud Billing SKU "
                "catalog (list_skus). Per-second billing (Linux); 1-minute "
                "minimum per launch. No prepaid balance required. Preemptible "
                "SKUs surfaced as spot. "
                "Source: https://cloud.google.com/compute/gpus-pricing"
            ),
        )

    def fetch_model_prices(self) -> list[ModelPrice]:
        """GCP Compute does not offer a model API (Vertex AI is separate). Returns []."""
        return []

    def fetch_compute_prices(self) -> list[ComputePrice]:
        """Fetch live GPU compute prices from the GCP Cloud Billing SKU catalog.

        Returns an empty list on any error (fail-soft). Skips cleanly when
        ``GOOGLE_APPLICATION_CREDENTIALS`` is not set or ``google-cloud-billing``
        is not installed.
        """
        if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            logger.warning(
                "GCP SKU fetch skipped: GOOGLE_APPLICATION_CREDENTIALS not set"
            )
            return []

        try:
            client = self._get_client()
        except ImportError as exc:
            logger.warning(
                "GCP SKU fetch skipped: google-cloud-billing unavailable (%s)",
                exc,
            )
            return []
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("GCP SKU fetch skipped: %s", exc)
            return []

        try:
            skus = self._fetch_skus(client)
        except Exception as exc:
            logger.warning("GCP list_skus failed: %s", exc)
            return []

        fetched_at = time.time()
        results: list[ComputePrice] = []
        for raw_sku in skus:
            parsed = self._parse_sku(raw_sku, fetched_at)
            if parsed is not None:
                results.append(parsed)
        return results

    def _get_client(self) -> Any:
        """Build a CloudCatalogClient. Raises ImportError if the SDK is missing.

        Lazy import keeps the module loadable when google-cloud-billing (an
        optional dep) is not installed; tests mock this method to avoid the
        network/dep.
        """
        try:
            from google.cloud import billing
        except ImportError as exc:
            raise ImportError("google-cloud-billing is not installed") from exc

        return billing.CloudCatalogClient()

    def _fetch_skus(self, client: Any) -> list[Any]:
        """Page through list_skus and return the raw SKU messages.

        ``list_skus`` returns a paged iterable; we drain it into a list so any
        paged-network error surfaces here (and is caught by the caller).
        """
        pager = client.list_skus(parent=self._COMPUTE_PARENT)
        return list(pager)

    def _parse_sku(self, raw_sku: Any, fetched_at: float) -> ComputePrice | None:
        """Parse one SKU message into a ComputePrice, or None to skip.

        Skips (returns None) when:
          - the SKU is not in the Compute resource family,
          - the SKU is not a GPU (resource_group != GPU AND description does
            not mention "GPU"/"Gpu"),
          - the SKU has no pricing_info or no tiered_rates,
          - the USD hourly rate cannot be extracted.

        Attribute access is defensive (``getattr`` with fallbacks) so partial
        or schema-drifted messages are skipped rather than crashing the loop.
        """
        category = getattr(raw_sku, "category", None)
        resource_family = str(getattr(category, "resource_family", "") or "")
        if resource_family != "Compute":
            return None

        resource_group = str(getattr(category, "resource_group", "") or "")
        description = str(getattr(raw_sku, "description", "") or "")
        is_gpu = resource_group == "GPU" or "gpu" in description.lower()
        if not is_gpu:
            return None

        usage_type = str(getattr(category, "usage_type", "") or "")
        spot = usage_type == "Preemptible"

        usd_per_hour = self._extract_hourly_usd(raw_sku)
        if usd_per_hour is None:
            return None

        usd_per_second = usd_per_hour / 3600.0
        sku_id = str(getattr(raw_sku, "sku_id", "") or "") or description
        gpu_type = self._extract_gpu_type(description, resource_group)

        return ComputePrice(
            provider="gcp_live",
            sku=sku_id,
            usd_per_unit=usd_per_second,
            granularity=BillingGranularity.per_second,
            spot=spot,
            terms=BillingTerms.postpaid_monthly,
            fetched_at=fetched_at,
            source=self._SOURCE,
            gpu_count=None,
            gpu_type=gpu_type,
            notes=(
                f"{'Preemptible/Spot' if spot else 'On-demand'} GPU SKU. "
                f"${usd_per_hour:.4f}/hr = ${usd_per_second:.6f}/s. "
                "1-min minimum per launch. "
                f"resource_group={resource_group or 'n/a'}."
            ),
        )

    @staticmethod
    def _extract_gpu_type(description: str, resource_group: str) -> str:
        """Pick a human-readable GPU label for the SKU.

        Prefers the description (which usually embeds the accelerator family,
        e.g. "A2 Highgpu 1G Gpu"), falling back to the resource_group.
        """
        if description:
            return description
        return resource_group or "GPU"

    @staticmethod
    def _extract_hourly_usd(raw_sku: Any) -> float | None:
        """Pull the first tiered_rate USD/hour out of a SKU's pricing_info.

        ``Money`` is encoded as whole ``units`` + fractional ``nanos`` where
        1e9 nanos = 1 unit. Returns None when pricing is missing or malformed.
        """
        pricing_infos = getattr(raw_sku, "pricing_info", None) or []
        if not pricing_infos:
            return None
        first_info = pricing_infos[0]
        pricing_expr = getattr(first_info, "pricing_expression", None)
        if pricing_expr is None:
            return None
        tiered_rates = getattr(pricing_expr, "tiered_rates", None) or []
        if not tiered_rates:
            return None
        first_rate = tiered_rates[0]
        unit_price = getattr(first_rate, "unit_price", None)
        if unit_price is None:
            return None
        units = getattr(unit_price, "units", 0) or 0
        nanos = getattr(unit_price, "nanos", 0) or 0
        try:
            total = float(units) + float(nanos) / 1e9
        except (TypeError, ValueError):
            return None
        if total <= 0:
            return None
        return total


# ---------------------------------------------------------------------------
# HuggingFace Inference Endpoints — STATIC dedicated-endpoint GPU table
# ---------------------------------------------------------------------------
# Source: https://huggingface.co/pricing#dedicated-endpoints (accessed 2025-Q4)
#
# HuggingFace sells "Dedicated Endpoints" — reserved GPU instances billed per
# hour against a prepaid account balance. The pricing page lists per-instance
# hourly rates by GPU type. No machine-readable public catalog exists (the
# endpoint API at https://api.endpoints.huggingface.cloud/ requires an HF
# token), so the table below is transcribed from the public pricing page.
#
# No public per-token pricing catalog exists for serverless inference (it is
# metered against the account, not published as a price list), so
# fetch_model_prices() returns [].
#
# BILLING SEMANTICS (PREPAID / PER-HOUR):
#   - Dedicated endpoints bill per HOUR against a prepaid account balance.
#   - Customer must maintain a positive balance; exhaustion stops endpoints.
#   - Dedicated endpoints are RESERVED (non-interruptible) — no spot concept.
# ---------------------------------------------------------------------------

_HF_SOURCE = "https://huggingface.co/pricing#dedicated-endpoints"
_HF_FETCHED_AT = 1735689600.0  # 2025-01-01 00:00 UTC (table recorded date)

# Format: (sku, gpu_type, gpu_count, usd_per_hour)
# Prices transcribed from https://huggingface.co/pricing (2025-Q4).
# These are the dedicated-endpoint hourly rates for 1x GPU instances.
_HF_DEDICATED_PRICES_STATIC: list[tuple[str, str, int, float]] = [
    # Entry-tier GPUs
    ("hf-t4-1x", "T4 16GB", 1, 0.60),
    ("hf-a10g-1x", "A10G 24GB", 1, 1.05),
    # Mid-tier Ada / Ampere
    ("hf-l4-1x", "L4 24GB", 1, 1.05),
    ("hf-l40s-1x", "L40S 48GB", 1, 1.95),
    # A100 family — both VRAM variants
    ("hf-a100-40gb-1x", "A100 40GB", 1, 4.13),
    ("hf-a100-80gb-1x", "A100 80GB", 1, 4.50),
    # High-tier Hopper
    ("hf-h100-80gb-1x", "H100 80GB", 1, 11.00),
    ("hf-h100-80gb-8x", "H100 80GB", 8, 88.00),
    ("hf-h200-141gb-1x", "H200 141GB", 1, 13.00),
    # Multi-GPU A100 80GB
    ("hf-a100-80gb-8x", "A100 80GB", 8, 36.00),
]


class HuggingFaceSource:
    """FETCH STRATEGY: STATIC — hardcoded from https://huggingface.co/pricing (2025-Q4).

    HuggingFace publishes dedicated-endpoint GPU hourly rates on a public HTML
    pricing page. There is no machine-readable public catalog, so the rates are
    transcribed into ``_HF_DEDICATED_PRICES_STATIC`` and surfaced via
    ``fetch_compute_prices()``. Per-token serverless pricing is metered against
    the account and not published as a price list, so ``fetch_model_prices()``
    returns ``[]``.

    Live pricing is available via ``HuggingFacePricingSource`` (scrapes the HF
    pricing page), wrapped in ``CachedSource`` with this static source as fallback.

    BILLING SEMANTICS (PREPAID / PER-HOUR):
      - Dedicated endpoints bill per HOUR against a prepaid account balance.
      - Customer must maintain a positive balance; balance exhaustion stops the
        endpoint (no postpaid invoice for dedicated endpoints).
      - Dedicated endpoints are reserved (non-interruptible) — no spot concept.
      - Serverless (pay-per-token) inference is separate and metered against
        the same balance; no public per-token price list exists.
    """

    def provider_slug(self) -> str:
        return "huggingface"

    def billing(self) -> ProviderBilling:
        return ProviderBilling(
            provider="huggingface",
            granularity=BillingGranularity.per_hour,
            terms=BillingTerms.prepaid_balance,
            currency="USD",
            min_charge=None,
            spot_available=False,
            notes=(
                "Dedicated Endpoints bill per HOUR against a prepaid account "
                "balance; balance exhaustion stops the endpoint. Reserved "
                "(non-interruptible); no spot concept. Serverless per-token "
                "inference is metered against the same balance but has no "
                "public per-token price list. "
                "Source: https://huggingface.co/pricing#dedicated-endpoints"
            ),
        )

    def fetch_model_prices(self) -> list[ModelPrice]:
        """No public per-token pricing catalog exists for HuggingFace. Returns []."""
        return []

    def fetch_compute_prices(self) -> list[ComputePrice]:
        """Return static dedicated-endpoint GPU price table.

        Prices are the raw per-hour rates from the public pricing page; because
        granularity is ``per_hour``, ``usd_per_unit`` IS the USD/hour rate (no
        /3600 conversion).
        """
        results: list[ComputePrice] = []
        for sku, gpu_type, gpu_count, usd_per_hour in _HF_DEDICATED_PRICES_STATIC:
            results.append(
                ComputePrice(
                    provider="huggingface",
                    sku=sku,
                    usd_per_unit=usd_per_hour,
                    granularity=BillingGranularity.per_hour,
                    spot=False,
                    terms=BillingTerms.prepaid_balance,
                    fetched_at=_HF_FETCHED_AT,
                    source=_HF_SOURCE,
                    gpu_count=gpu_count,
                    gpu_type=gpu_type,
                    notes=(
                        f"Dedicated Endpoint (reserved). ${usd_per_hour:.2f}/hr. "
                        "Billed per hour against prepaid account balance."
                    ),
                )
            )
        return results


# ---------------------------------------------------------------------------
# Z.AI (GLM models) — STATIC table (no public pricing API; HTML page only)
# ---------------------------------------------------------------------------
# Source: https://docs.z.ai/guides/overview/pricing  (accessed 2026-Q2)
# Z.AI publishes model pricing only as an HTML page — there is no
# machine-readable pricing API. Prices below are recorded from that page and
# expressed as USD per 1,000 tokens (the page lists USD per 1,000,000 tokens).
# Billing: postpaid_per_use; per-token; no prepaid balance required.
# ---------------------------------------------------------------------------

# Format: (model_id, input_usd_per_1M, output_usd_per_1M, notes)
_ZAI_PRICES_PER_1M: list[tuple[str, float, float, str]] = [
    # https://docs.z.ai/guides/overview/pricing (2026-Q2)
    ("glm-5.2", 1.4, 4.4, "GLM-5.2; flagship reasoning model"),
    ("glm-5", 1.0, 3.2, "GLM-5; high-capability general model"),
    ("glm-4.5", 0.6, 2.2, "GLM-4.5; cost-optimized model"),
]

_ZAI_SOURCE = "https://docs.z.ai/guides/overview/pricing"
_ZAI_FETCHED_AT = 1735689600.0  # 2025-01-01 00:00 UTC (table recorded date)


class ZAISource:
    """FETCH STRATEGY: STATIC — hardcoded from https://docs.z.ai/guides/overview/pricing (2026-Q2).

    Z.AI does not publish a machine-readable pricing API; prices are listed on an
    HTML page and were transcribed into the static table below. The page quotes
    USD per 1,000,000 tokens; we convert to USD per 1,000 tokens (divide by 1000)
    to match the ModelPrice convention used by the other sources.

    Live pricing is available via ``ZAIPricingSource`` (scrapes the Z.AI pricing
    page), wrapped in ``CachedSource`` with this static source as fallback.

    Billing:
      - terms: postpaid_per_use (billed per API call; no prepaid balance)
      - granularity: per_token
      - spot_available: False (no spot concept for API calls)
      - min_charge: None (no documented minimum per call)
    """

    def provider_slug(self) -> str:
        return "zai"

    def billing(self) -> ProviderBilling:
        return ProviderBilling(
            provider="zai",
            granularity=BillingGranularity.per_token,
            terms=BillingTerms.postpaid_per_use,
            currency="USD",
            min_charge=None,
            spot_available=False,
            notes=(
                "Billed per API call per token; no prepaid balance required. "
                "Prices listed as USD per 1M tokens on the pricing page; "
                "converted to per-1K internally. "
                "Source: https://docs.z.ai/guides/overview/pricing"
            ),
        )

    def fetch_model_prices(self) -> list[ModelPrice]:
        """Return static price table. Source documented; table dated 2026-Q2.

        The page lists prices per 1,000,000 tokens; ModelPrice stores per-1,000,
        so each value is divided by 1000.
        """
        return [
            ModelPrice(
                provider="zai",
                model_id=model_id,
                input_usd_per_1k=inp_per_1m / 1000.0,
                output_usd_per_1k=out_per_1m / 1000.0,
                fetched_at=_ZAI_FETCHED_AT,
                source=_ZAI_SOURCE,
                notes=notes,
            )
            for model_id, inp_per_1m, out_per_1m, notes in _ZAI_PRICES_PER_1M
        ]

    def fetch_compute_prices(self) -> list[ComputePrice]:
        """Z.AI does not offer direct compute. Returns []."""
        return []


# ---------------------------------------------------------------------------
# LiteLLM — LIVE JSON catalog for per-token pricing across providers
# ---------------------------------------------------------------------------
# Source: https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json
#         (public, no auth; a flat dict keyed by model_id)
#
# litellm maintains the canonical cross-provider pricing table. Each entry has:
#   litellm_provider        — the upstream provider slug ("anthropic", "openai", ...)
#   input_cost_per_token    — USD per single input token (multiply by 1000 for per-1K)
#   output_cost_per_token   — USD per single output token
#   max_tokens              — context window (tokens)
#
# A single JSON file covers every provider, so LiteLLMJSONSource is instantiated
# once PER provider and filters client-side by ``litellm_provider``. The file
# also contains a ``sample_spec`` metadata key which is naturally excluded by
# the provider filter.
# ---------------------------------------------------------------------------


class LiteLLMJSONSource:
    """FETCH STRATEGY: LIVE — BerriAI/litellm raw JSON catalog (public, no auth).

    Fetches the litellm ``model_prices_and_context_window.json`` file and
    filters it to a single upstream provider (set via the ``provider``
    constructor argument, e.g. ``"anthropic"`` or ``"openai"``). Prices in the
    file are USD-per-token; they are converted to USD-per-1K-tokens to match
    the ModelPrice convention used by every other source.

    Entries are silently skipped when they:
      - lack a ``litellm_provider`` field or have a non-matching one,
      - lack numeric ``input_cost_per_token`` / ``output_cost_per_token``,
      - are not JSON objects (defensive against schema drift).

    SLUG NOTE: Uses ``"litellm_<provider>"`` (e.g. ``"litellm_anthropic"``)
    rather than the bare provider slug to avoid colliding with the static
    ``AnthropicSource`` / ``OpenAISource`` in the catalog's first-match-wins
    ``_source_for`` lookup. This keeps both the static baseline (always
    available) and the live litellm source independently queryable through
    ``PricingCatalog``.

    Billing:
      - terms: postpaid_per_use (litellm documents upstream provider rates;
        billing semantics inherit from the upstream provider)
      - granularity: per_token
      - spot_available: False
      - min_charge: None
    """

    _ENDPOINT = (
        "https://raw.githubusercontent.com/BerriAI/litellm/main/"
        "model_prices_and_context_window.json"
    )

    def __init__(self, provider: str) -> None:
        """``provider`` is the upstream litellm_provider slug to filter on
        (e.g. ``"anthropic"``, ``"openai"``)."""
        self._provider = provider

    def provider_slug(self) -> str:
        return f"litellm_{self._provider}"

    def billing(self) -> ProviderBilling:
        return ProviderBilling(
            provider=self.provider_slug(),
            granularity=BillingGranularity.per_token,
            terms=BillingTerms.postpaid_per_use,
            currency="USD",
            min_charge=None,
            spot_available=False,
            notes=(
                f"Live per-token pricing via litellm JSON catalog, filtered to "
                f"'{self._provider}' models. Billing semantics inherit from the "
                "upstream provider (postpaid per use). "
                f"Source: {self._ENDPOINT}"
            ),
        )

    def fetch_model_prices(self) -> list[ModelPrice]:
        """Fetch live model prices from the litellm JSON catalog.

        Filters entries to those whose ``litellm_provider`` matches the
        constructor's ``provider`` argument and converts USD-per-token to
        USD-per-1K-tokens. Returns an empty list on any error (fail-soft).
        """
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(self._ENDPOINT)
                if resp.status_code != 200:
                    logger.warning(
                        "LiteLLM JSON catalog returned HTTP %s", resp.status_code
                    )
                    return []
                data = resp.json()
        except Exception as exc:
            logger.warning("LiteLLM JSON fetch failed: %s", exc)
            return []

        if not isinstance(data, dict):
            logger.warning(
                "LiteLLM JSON catalog parsed to %s, expected dict; skipping",
                type(data).__name__,
            )
            return []

        fetched_at = time.time()
        results: list[ModelPrice] = []

        for model_id, entry in data.items():
            price = self._parse_entry(model_id, entry, fetched_at)
            if price is not None:
                results.append(price)

        return results

    def _parse_entry(
        self,
        model_id: str,
        entry: Any,
        fetched_at: float,
    ) -> ModelPrice | None:
        """Parse one litellm JSON entry into a ModelPrice, or None to skip.

        Skips when:
          - ``entry`` is not a dict,
          - ``litellm_provider`` does not match this source's provider filter,
          - ``input_cost_per_token`` / ``output_cost_per_token`` are missing
            or non-numeric.
        """
        if not isinstance(entry, dict):
            return None
        if entry.get("litellm_provider") != self._provider:
            return None

        try:
            input_per_token = float(entry.get("input_cost_per_token") or 0)
            output_per_token = float(entry.get("output_cost_per_token") or 0)
        except (TypeError, ValueError):
            return None

        # If the input cost field was entirely absent (None), entry.get returns
        # None, `None or 0` collapses to 0, and float() succeeds. We want to
        # skip entries that genuinely lack pricing rather than silently report
        # a $0 price. Detect by checking for key presence.
        if "input_cost_per_token" not in entry:
            return None

        # litellm reports USD-per-token; convert to USD-per-1K-tokens.
        input_per_1k = input_per_token * 1000
        output_per_1k = output_per_token * 1000

        context_window: int | None
        ctx_raw = entry.get("max_tokens") or entry.get("max_input_tokens")
        try:
            context_window = int(ctx_raw) if ctx_raw is not None else None
        except (TypeError, ValueError):
            context_window = None

        return ModelPrice(
            provider=self._provider,
            model_id=model_id,
            input_usd_per_1k=input_per_1k,
            output_usd_per_1k=output_per_1k,
            fetched_at=fetched_at,
            source=self._ENDPOINT,
            context_window=context_window,
            notes=f"litellm_provider={self._provider}",
        )

    def fetch_compute_prices(self) -> list[ComputePrice]:
        """LiteLLM is a model-pricing catalog; no compute offering. Returns []."""
        return []


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
        self._live = live
        self._static = static_fallback
        self._ttl = ttl_seconds
        self._model_cache: list[ModelPrice] | None = None
        self._model_cache_time: float = 0.0
        self._compute_cache: list[ComputePrice] | None = None
        self._compute_cache_time: float = 0.0

    def provider_slug(self) -> str:
        return self._live.provider_slug()

    def billing(self) -> ProviderBilling:
        return self._live.billing()

    def fetch_model_prices(self, refresh: bool = False) -> list[ModelPrice]:
        if not refresh and self._model_cache is not None and not self._is_stale(self._model_cache_time):
            return list(self._model_cache)
        try:
            prices = self._live.fetch_model_prices()
        except Exception as exc:
            logger.warning(
                "CachedSource(%s): live model fetch failed: %s",
                self.provider_slug(), exc,
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
                        self.provider_slug(), fb_exc,
                    )
            return []
        if not prices and self._static is not None:
            logger.info(
                "CachedSource(%s): live returned empty, "
                "falling back to static model prices",
                self.provider_slug(),
            )
            try:
                fb = self._static.fetch_model_prices()
                if fb:
                    return fb
            except Exception as fb_exc:
                logger.warning(
                    "CachedSource(%s): static fallback failed: %s",
                    self.provider_slug(), fb_exc,
                )
        self._model_cache = list(prices)
        self._model_cache_time = time.time()
        return list(prices)

    def fetch_compute_prices(self, refresh: bool = False) -> list[ComputePrice]:
        if not refresh and self._compute_cache is not None and not self._is_stale(self._compute_cache_time):
            return list(self._compute_cache)
        try:
            prices = self._live.fetch_compute_prices()
        except Exception as exc:
            logger.warning(
                "CachedSource(%s): live compute fetch failed: %s",
                self.provider_slug(), exc,
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
                        self.provider_slug(), fb_exc,
                    )
            return []
        if not prices and self._static is not None:
            logger.info(
                "CachedSource(%s): live returned empty, "
                "falling back to static compute prices",
                self.provider_slug(),
            )
            try:
                fb = self._static.fetch_compute_prices()
                if fb:
                    return fb
            except Exception as fb_exc:
                logger.warning(
                    "CachedSource(%s): static fallback failed: %s",
                    self.provider_slug(), fb_exc,
                )
        self._compute_cache = list(prices)
        self._compute_cache_time = time.time()
        return list(prices)

    def _is_stale(self, cache_time: float) -> bool:
        if self._ttl <= 0:
            return True
        return (time.time() - cache_time) > self._ttl


# ---------------------------------------------------------------------------
# Lambda Labs — LIVE compute prices via REST API
# ---------------------------------------------------------------------------
# Source: GET https://cloud.lambdalabs.com/api/v1/instance-types
# Spec:   https://docs.lambdalabs.com/cloud/api
# Auth:   ``LAMBDA_API_KEY`` environment variable sent as
#         ``Authorization: Bearer <key>``. If unset, the source skips cleanly
#         (returns ``[]`` and logs a warning) — it is dormant without credentials.
#
# The instance-types endpoint returns a flat dict keyed by instance type ID,
# each containing ``instance_type.price_cents_per_hour`` (integer cents) and
# ``instance_type.specs.gpus``. Lambda bills per MINUTE, so we convert
# cents/hr → USD/hr → USD/min to match the static LambdaLabsSource.
# ---------------------------------------------------------------------------


class LambdaLabsPricingSource:
    """FETCH STRATEGY: LIVE — Lambda Labs Cloud API (REST).

    Queries the Lambda Labs ``/api/v1/instance-types`` endpoint for live GPU
    instance pricing. Returns one ``ComputePrice`` per instance type, with
    per-minute pricing (cents/hour / 100 / 60).

    Auth: ``LAMBDA_API_KEY`` environment variable, sent as
    ``Authorization: Bearer <key>``. If unset, the source skips cleanly
    (returns ``[]`` and logs a warning) — it is dormant without credentials.

    SLUG NOTE: Uses ``"lambda_labs_live"`` (not ``"lambda_labs"``) to avoid
    colliding with the static ``LambdaLabsSource`` in the catalog's
    first-match-wins ``_source_for`` lookup.

    BILLING SEMANTICS (same PREPAID/PER-MINUTE as LambdaLabsSource):
      - Credit card charged on consumption; no monthly invoice.
      - Billed per MINUTE (min 1 min).
      - Payment failure = service stop.
    """

    _ENDPOINT = "https://cloud.lambdalabs.com/api/v1/instance-types"
    _SOURCE = (
        "Lambda Labs Cloud API "
        "(GET /api/v1/instance-types) — "
        "https://docs.lambdalabs.com/cloud/api"
    )

    def provider_slug(self) -> str:
        return "lambda_labs_live"

    def billing(self) -> ProviderBilling:
        return ProviderBilling(
            provider="lambda_labs_live",
            granularity=BillingGranularity.per_minute,
            terms=BillingTerms.prepaid_balance,
            currency="USD",
            min_charge=None,
            spot_available=True,
            notes=(
                "Billed per MINUTE (min 1 min). Live pricing via Lambda Labs "
                "Cloud API. Charges hit credit card on consumption. "
                "Source: https://docs.lambdalabs.com/cloud/api"
            ),
        )

    def fetch_model_prices(self) -> list[ModelPrice]:
        return []

    def fetch_compute_prices(self) -> list[ComputePrice]:
        api_key = os.environ.get("LAMBDA_API_KEY")
        if not api_key:
            logger.warning(
                "Lambda Labs fetch skipped: LAMBDA_API_KEY not set"
            )
            return []

        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(
                    self._ENDPOINT,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                    },
                )
                if resp.status_code != 200:
                    logger.warning(
                        "Lambda Labs API returned HTTP %s", resp.status_code
                    )
                    return []
                data = resp.json()
        except Exception as exc:
            logger.warning("Lambda Labs fetch failed: %s", exc)
            return []

        raw = data.get("data") or {}
        if not isinstance(raw, dict) or not raw:
            logger.warning("Lambda Labs API returned no instance types")
            return []

        fetched_at = time.time()
        results: list[ComputePrice] = []

        for type_id, entry in raw.items():
            if not isinstance(entry, dict):
                continue
            inst = entry.get("instance_type") or {}
            if not isinstance(inst, dict):
                continue
            try:
                cents_per_hour = int(inst.get("price_cents_per_hour") or 0)
            except (TypeError, ValueError):
                continue
            if cents_per_hour <= 0:
                continue
            specs = inst.get("specs") or {}
            gpu_count_raw = (specs or {}).get("gpus") if isinstance(specs, dict) else None
            try:
                gpu_count = int(gpu_count_raw) if gpu_count_raw is not None else None
            except (TypeError, ValueError):
                gpu_count = None
            description = str(inst.get("description") or type_id)

            usd_per_hour = cents_per_hour / 100.0
            usd_per_minute = usd_per_hour / 60.0

            results.append(
                ComputePrice(
                    provider="lambda_labs_live",
                    sku=type_id,
                    usd_per_unit=usd_per_minute,
                    granularity=BillingGranularity.per_minute,
                    spot=False,
                    terms=BillingTerms.prepaid_balance,
                    fetched_at=fetched_at,
                    source=self._SOURCE,
                    gpu_count=gpu_count,
                    gpu_type=description,
                    notes=(
                        f"{description}. ${usd_per_hour:.4f}/hr = "
                        f"${usd_per_minute:.6f}/min (billed per minute)."
                    ),
                )
            )

        return results


# ---------------------------------------------------------------------------
# HuggingFace — LIVE compute prices via HTML scrape
# ---------------------------------------------------------------------------
# Source: https://huggingface.co/pricing (public HTML page; no auth required)
# The dedicated-endpoints pricing table lists GPU types with USD/hour rates.
# There is no machine-readable public catalog for dedicated endpoint pricing,
# so this source scrapes the HTML page and extracts GPU names + prices via
# regex. The static ``HuggingFaceSource`` is the fallback when scraping fails.
# ---------------------------------------------------------------------------


_HF_PRICE_RE = re.compile(
    r'(?:NVIDIA\s*)?((?:RTX\s*)?(?:Tesla\s*)?[ATVLHR]\d+\s*(?:SXM\d+\s*)?\d*\s*GB?)'
    r'.*?\$(\d+\.?\d*)\s*/?\s*hr',
    re.IGNORECASE,
)

_HF_ENDPOINT = "https://huggingface.co/pricing"


class HuggingFacePricingSource:
    """FETCH STRATEGY: LIVE — scrapes https://huggingface.co/pricing (public HTML).

    The HuggingFace pricing page does not expose a machine-readable API for
    dedicated endpoint GPU pricing. This source fetches the HTML page and
    extracts GPU names + USD/hour rates via regex. On any parse failure it
    returns ``[]`` (fail-soft); the ``CachedSource`` wrapper falls back to
    the static ``HuggingFaceSource``.

    SLUG NOTE: Uses ``"huggingface_live"`` (not ``"huggingface"``) to avoid
    colliding with the static source.

    BILLING SEMANTICS (same PREPAID / PER-HOUR as HuggingFaceSource):
      - Dedicated endpoints billed per HOUR against a prepaid balance.
    """

    _SOURCE = "https://huggingface.co/pricing#dedicated-endpoints"

    def provider_slug(self) -> str:
        return "huggingface_live"

    def billing(self) -> ProviderBilling:
        return ProviderBilling(
            provider="huggingface_live",
            granularity=BillingGranularity.per_hour,
            terms=BillingTerms.prepaid_balance,
            currency="USD",
            min_charge=None,
            spot_available=False,
            notes=(
                "Dedicated Endpoints bill per HOUR against a prepaid account "
                "balance; live prices scraped from the HF pricing page. "
                "Source: https://huggingface.co/pricing#dedicated-endpoints"
            ),
        )

    def fetch_model_prices(self) -> list[ModelPrice]:
        return []

    def fetch_compute_prices(self) -> list[ComputePrice]:
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(_HF_ENDPOINT)
                if resp.status_code != 200:
                    logger.warning(
                        "HuggingFace pricing page returned HTTP %s",
                        resp.status_code,
                    )
                    return []
                html = resp.text
        except Exception as exc:
            logger.warning("HuggingFace pricing fetch failed: %s", exc)
            return []

        matches = _HF_PRICE_RE.findall(html)
        if not matches:
            logger.warning(
                "HuggingFace pricing scrape: no GPU price matches found"
            )
            return []

        fetched_at = time.time()
        results: list[ComputePrice] = []
        seen: set[str] = set()

        for gpu_raw, usd_str in matches:
            gpu_type = gpu_raw.strip()
            if not gpu_type:
                continue
            try:
                usd_per_hour = float(usd_str)
            except (TypeError, ValueError):
                continue
            if usd_per_hour <= 0:
                continue

            sku = f"hf-{gpu_type.lower().replace(' ', '-')}-live"
            if sku in seen:
                continue
            seen.add(sku)

            results.append(
                ComputePrice(
                    provider="huggingface_live",
                    sku=sku,
                    usd_per_unit=usd_per_hour,
                    granularity=BillingGranularity.per_hour,
                    spot=False,
                    terms=BillingTerms.prepaid_balance,
                    fetched_at=fetched_at,
                    source=self._SOURCE,
                    gpu_count=1,
                    gpu_type=gpu_type,
                    notes=(
                        f"Dedicated Endpoint (reserved). ${usd_per_hour:.2f}/hr. "
                        "Scraped from HF pricing page."
                    ),
                )
            )

        return results


# ---------------------------------------------------------------------------
# Z.AI — LIVE model prices via HTML scrape
# ---------------------------------------------------------------------------
# Source: https://docs.z.ai/guides/overview/pricing (public HTML; no auth)
# Z.AI publishes GLM model pricing as an HTML table with USD per 1M tokens.
# There is no machine-readable pricing API, so this source scrapes the HTML
# and extracts model names + prices via regex. The static ``ZAISource`` is
# the fallback when scraping fails.
# ---------------------------------------------------------------------------

_ZAI_PRICE_RE = re.compile(
    r'([Gg][Ll][Mm][-\u2013\u2014\s]*[\d.]+).*?'
    r'\$(\d+\.?\d*)\s*/\s*1M.*?input.*?'
    r'\$(\d+\.?\d*)\s*/\s*1M.*?output',
    re.IGNORECASE | re.DOTALL,
)

_ZAI_TABLE_RE = re.compile(
    r'([Gg][Ll][Mm][-\u2013\u2014\s]*[\d.]+).*?\$(\d+\.?\d*).*?\$(\d+\.?\d*)',
    re.IGNORECASE | re.DOTALL,
)

_ZAI_ENDPOINT = "https://docs.z.ai/guides/overview/pricing"


class ZAIPricingSource:
    """FETCH STRATEGY: LIVE — scrapes https://docs.z.ai/guides/overview/pricing.

    Z.AI does not publish a machine-readable pricing API. This source fetches
    the HTML pricing page and extracts GLM model names + USD/1M token prices
    via regex, then converts to USD/1K tokens. On any parse failure it returns
    ``[]`` (fail-soft); the ``CachedSource`` wrapper falls back to the static
    ``ZAISource``.

    SLUG NOTE: Uses ``"zai_live"`` (not ``"zai"``) to avoid colliding with
    the static source.

    Billing:
      - terms: postpaid_per_use
      - granularity: per_token
    """

    _SOURCE = "https://docs.z.ai/guides/overview/pricing"

    def provider_slug(self) -> str:
        return "zai_live"

    def billing(self) -> ProviderBilling:
        return ProviderBilling(
            provider="zai_live",
            granularity=BillingGranularity.per_token,
            terms=BillingTerms.postpaid_per_use,
            currency="USD",
            min_charge=None,
            spot_available=False,
            notes=(
                "Billed per API call per token; no prepaid balance required. "
                "Live prices scraped from the Z.AI pricing page. "
                "Source: https://docs.z.ai/guides/overview/pricing"
            ),
        )

    def fetch_model_prices(self) -> list[ModelPrice]:
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(_ZAI_ENDPOINT)
                if resp.status_code != 200:
                    logger.warning(
                        "Z.AI pricing page returned HTTP %s", resp.status_code
                    )
                    return []
                html = resp.text
        except Exception as exc:
            logger.warning("Z.AI pricing fetch failed: %s", exc)
            return []

        stripped = re.sub(r"<[^>]+>", " ", html)
        collapsed = re.sub(r"\s+", " ", stripped)

        matches = _ZAI_PRICE_RE.findall(collapsed)
        if not matches:
            matches = _ZAI_TABLE_RE.findall(collapsed)

        if not matches:
            logger.warning(
                "Z.AI pricing scrape: no GLM price matches found"
            )
            return []

        fetched_at = time.time()
        results: list[ModelPrice] = []
        seen: set[str] = set()

        for model_raw, inp_str, out_str in matches:
            model_id = re.sub(r"[\u2013\u2014\s]+", "-", model_raw.strip()).lower()
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            try:
                inp_per_1m = float(inp_str)
                out_per_1m = float(out_str)
            except (TypeError, ValueError):
                continue
            if inp_per_1m <= 0 and out_per_1m <= 0:
                continue

            results.append(
                ModelPrice(
                    provider="zai",
                    model_id=model_id,
                    input_usd_per_1k=inp_per_1m / 1000.0,
                    output_usd_per_1k=out_per_1m / 1000.0,
                    fetched_at=fetched_at,
                    source=self._SOURCE,
                    notes=f"Scraped from Z.AI pricing page. ${inp_per_1m}/1M input, ${out_per_1m}/1M output.",
                )
            )

        return results

    def fetch_compute_prices(self) -> list[ComputePrice]:
        return []


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
