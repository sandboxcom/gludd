"""PricingSource implementations for model API and cloud compute providers.

FETCH STRATEGY LEGEND (used in each class docstring):
  LIVE    — fetches from a real public pricing API; sources reflect actual live data.
  STATIC  — hardcoded table with documented source URL; prices accurate at recorded date.
             A TODO(integration) note marks where live fetch should be added.
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

import logging
import time
from typing import Protocol, runtime_checkable

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

    # TODO(integration): Add web-scraping or poll Anthropic SDK metadata for live rates.

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

    # TODO(integration): Scrape https://openai.com/api/pricing or use OpenAI SDK
    # metadata to get live prices; alternatively parse the pricing page HTML.

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

    # TODO(integration): RunPod GraphQL API (https://graphql-spec.runpod.io/) exposes
    # live GPU availability and pricing. Use the gpuTypes query with minMemoryInGb filter.
    # Auth: RunPod API key in Authorization header.
    # Example: https://www.runpod.io/docs/references/graphql/queries/gpu-types

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

    # TODO(integration): Lambda Labs exposes a REST API for instance pricing.
    # See: https://docs.lambdalabs.com/cloud/rate-limits-and-quotas/
    # API endpoint: GET https://cloud.lambdalabs.com/api/v1/instance-types
    # Auth: Lambda API key in Authorization header (Bearer).
    # Returns available instance types with pricing per USD/hour.

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

    # TODO(integration): AWS publishes machine-readable pricing at:
    # https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/index.json
    # This is a ~1GB JSON file. Use the filtered endpoint or AWS Pricing API:
    # https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_pricing_GetProducts.html
    # Filter by: serviceCode=AmazonEC2, instanceType=p3/p4d/p5/g5, tenancy=Shared, os=Linux

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

    # TODO(integration): GCP Cloud Billing API (SKU-based):
    # https://cloud.google.com/billing/docs/reference/rest/v1/services.skus/list
    # Filter by: serviceId=6F81-5844-456A (Compute Engine), resourceFamily=Compute,
    # description contains "GPU". Requires GCP service account credentials.
    # Alternatively use: https://cloudpricingcalculator.appspot.com/static/data/pricelist.json

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
# HuggingFace Inference Endpoints — TODO (billing terms registered)
# ---------------------------------------------------------------------------

class HuggingFaceSource:
    """FETCH STRATEGY: TODO — billing terms registered; live price fetch not implemented.

    # TODO(integration): HuggingFace Inference Endpoints pricing is available at:
    # https://huggingface.co/pricing — per instance type, per-hour billing.
    # API: GET https://api.endpoints.huggingface.cloud/v2/endpoint/
    # (requires HF token, so not a public API)
    # For hosted inference (serverless), see:
    # https://huggingface.co/docs/inference-providers/en/index

    BILLING SEMANTICS:
      - Serverless inference: per-token, postpaid_per_use. Free tier available.
      - Dedicated endpoints: per-hour from a prepaid account balance.
    """

    def provider_slug(self) -> str:
        return "huggingface"

    def billing(self) -> ProviderBilling:
        return ProviderBilling(
            provider="huggingface",
            granularity=BillingGranularity.per_token,
            terms=BillingTerms.postpaid_per_use,
            currency="USD",
            min_charge=None,
            spot_available=False,
            notes=(
                "Serverless inference: per-token postpaid. "
                "Dedicated endpoints: per-hour billed to account balance (prepaid model). "
                "Free tier available for low-volume inference. "
                "Source: https://huggingface.co/pricing"
            ),
        )

    def fetch_model_prices(self) -> list[ModelPrice]:
        """TODO(integration): live fetch not implemented. Returns []."""
        return []

    def fetch_compute_prices(self) -> list[ComputePrice]:
        """TODO(integration): live fetch not implemented. Returns []."""
        return []


# ---------------------------------------------------------------------------
# Fireworks AI — TODO (billing terms registered)
# ---------------------------------------------------------------------------

class FireworksSource:
    """FETCH STRATEGY: TODO — billing terms registered; live price fetch not implemented.

    # TODO(integration): Fireworks AI pricing at https://fireworks.ai/pricing
    # Fireworks exposes pricing for hosted models (per-token). Use:
    # GET https://api.fireworks.ai/inference/v1/models
    # with an API key for the full model list. Pricing is in the model metadata.

    BILLING SEMANTICS:
      - Per-token billing, postpaid_per_use (credit card per call).
      - Focus on fast inference (throughput-optimized).
    """

    def provider_slug(self) -> str:
        return "fireworks"

    def billing(self) -> ProviderBilling:
        return ProviderBilling(
            provider="fireworks",
            granularity=BillingGranularity.per_token,
            terms=BillingTerms.postpaid_per_use,
            currency="USD",
            min_charge=None,
            spot_available=False,
            notes=(
                "Per-token billing. Postpaid per use (credit card). "
                "Throughput-optimized; typically cheaper than OpenAI for OSS models. "
                "Source: https://fireworks.ai/pricing"
            ),
        )

    def fetch_model_prices(self) -> list[ModelPrice]:
        """TODO(integration): live fetch not implemented. Returns []."""
        return []

    def fetch_compute_prices(self) -> list[ComputePrice]:
        """TODO(integration): live fetch not implemented. Returns []."""
        return []


# ---------------------------------------------------------------------------
# Registry: all available sources
# ---------------------------------------------------------------------------


def all_sources() -> list[PricingSource]:
    """Return one instance of every registered PricingSource."""
    return [
        OpenRouterSource(),
        AnthropicSource(),
        OpenAISource(),
        RunPodSource(),
        LambdaLabsSource(),
        AWSSource(),
        GCPSource(),
        HuggingFaceSource(),
        FireworksSource(),
    ]
