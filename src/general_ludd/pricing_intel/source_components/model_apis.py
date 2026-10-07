"""Model API and hosted-inference pricing sources."""

from __future__ import annotations

import logging
import math
import re
import time
from typing import Any

import httpx

from general_ludd.pricing_intel.models import (
    BillingGranularity,
    BillingTerms,
    ComputePrice,
    ModelPrice,
    ProviderBilling,
)
from general_ludd.pricing_intel.source_components.base import (
    PricingSourceAuthenticationError,
    PricingSourceDataError,
    UnavailableModelPrices,
    _openrouter_models,
)

logger = logging.getLogger(__name__)

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
        """Return this provider's canonical slug."""
        return "openrouter"

    def billing(self) -> ProviderBilling:
        """Return this provider's billing terms."""
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

        Transient network and provider availability errors return ``[]``.
        Authentication failures and invalid pricing payloads raise because
        treating either as an empty/free price catalog would be unsafe.
        """
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(self._ENDPOINT)
        except (httpx.TransportError, ConnectionError, TimeoutError, OSError) as exc:
            logger.warning("OpenRouter pricing fetch failed: %s", exc)
            return UnavailableModelPrices()
        if resp.status_code in {401, 403}:
            raise PricingSourceAuthenticationError(
                f"OpenRouter pricing authentication failed with HTTP {resp.status_code}"
            )
        if resp.status_code >= 500 or resp.status_code in {408, 429}:
            logger.warning("OpenRouter pricing API returned HTTP %s", resp.status_code)
            return UnavailableModelPrices()
        if resp.status_code != 200:
            raise PricingSourceDataError(
                f"OpenRouter pricing API returned unexpected HTTP {resp.status_code}"
            )
        models = _openrouter_models(resp)
        fetched_at = time.time()
        results: list[ModelPrice] = []

        for m in models:
            if not isinstance(m, dict):
                raise PricingSourceDataError(
                    "invalid OpenRouter pricing response: model entry must be an object"
                )
            model_id = m.get("id", "")
            pricing = m.get("pricing", {})
            if not isinstance(pricing, dict):
                raise PricingSourceDataError(
                    f"invalid OpenRouter pricing for {model_id!r}: pricing must be an object"
                )
            try:
                prompt_per_token = float(pricing.get("prompt", 0) or 0)
                completion_per_token = float(pricing.get("completion", 0) or 0)
            except (TypeError, ValueError) as exc:
                raise PricingSourceDataError(
                    f"invalid OpenRouter pricing for {model_id!r}: non-numeric token rate"
                ) from exc
            if not math.isfinite(prompt_per_token) or not math.isfinite(
                completion_per_token
            ):
                raise PricingSourceDataError(
                    f"invalid OpenRouter pricing for {model_id!r}: token rates must be finite"
                )
            if prompt_per_token < 0 or completion_per_token < 0:
                logger.info(
                    "Omitting OpenRouter model %s with unavailable sentinel pricing",
                    model_id,
                )
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
                    notes=str(m.get("description", ""))[:200],
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
        """Return this provider's canonical slug."""
        return "anthropic"

    def billing(self) -> ProviderBilling:
        """Return this provider's billing terms."""
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
        """Return this provider's canonical slug."""
        return "openai"

    def billing(self) -> ProviderBilling:
        """Return this provider's billing terms."""
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
        """Return this provider's canonical slug."""
        return "huggingface"

    def billing(self) -> ProviderBilling:
        """Return this provider's billing terms."""
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
        """Return this provider's canonical slug."""
        return "zai"

    def billing(self) -> ProviderBilling:
        """Return this provider's billing terms."""
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

    _ENDPOINT = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"

    def __init__(self, provider: str) -> None:
        """Initialize the source for the given upstream litellm provider.

        ``provider`` is the upstream litellm_provider slug to filter on
        (e.g. ``"anthropic"``, ``"openai"``).
        """
        self._provider = provider

    def provider_slug(self) -> str:
        """Return this provider's canonical slug."""
        return f"litellm_{self._provider}"

    def billing(self) -> ProviderBilling:
        """Return this provider's billing terms."""
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
                    logger.warning("LiteLLM JSON catalog returned HTTP %s", resp.status_code)
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
# HuggingFace — LIVE compute prices via HTML scrape
# ---------------------------------------------------------------------------
# Source: https://huggingface.co/pricing (public HTML page; no auth required)
# The dedicated-endpoints pricing table lists GPU types with USD/hour rates.
# There is no machine-readable public catalog for dedicated endpoint pricing,
# so this source scrapes the HTML page and extracts GPU names + prices via
# regex. The static ``HuggingFaceSource`` is the fallback when scraping fails.
# ---------------------------------------------------------------------------


_HF_PRICE_RE = re.compile(
    r"(?:NVIDIA\s*)?((?:RTX\s*)?(?:Tesla\s*)?[ATVLHR]\d+\s*(?:SXM\d+\s*)?\d*\s*GB?)"
    r".*?\$(\d+\.?\d*)\s*/?\s*hr",
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
        """Return this provider's canonical slug."""
        return "huggingface_live"

    def billing(self) -> ProviderBilling:
        """Return this provider's billing terms."""
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
        """Return this provider's model prices (fail-soft: empty on error)."""
        return []

    def fetch_compute_prices(self) -> list[ComputePrice]:
        """Return this provider's compute prices (fail-soft: empty on error)."""
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
            logger.warning("HuggingFace pricing scrape: no GPU price matches found")
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
                    notes=(f"Dedicated Endpoint (reserved). ${usd_per_hour:.2f}/hr. Scraped from HF pricing page."),
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
    r"([Gg][Ll][Mm][-\u2013\u2014\s]*[\d.]+).*?"
    r"\$(\d+\.?\d*)\s*/\s*1M.*?input.*?"
    r"\$(\d+\.?\d*)\s*/\s*1M.*?output",
    re.IGNORECASE | re.DOTALL,
)

_ZAI_TABLE_RE = re.compile(
    r"([Gg][Ll][Mm][-\u2013\u2014\s]*[\d.]+).*?\$(\d+\.?\d*).*?\$(\d+\.?\d*)",
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
        """Return this provider's canonical slug."""
        return "zai_live"

    def billing(self) -> ProviderBilling:
        """Return this provider's billing terms."""
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
        """Return this provider's model prices (fail-soft: empty on error)."""
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(_ZAI_ENDPOINT)
                if resp.status_code != 200:
                    logger.warning("Z.AI pricing page returned HTTP %s", resp.status_code)
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
            logger.warning("Z.AI pricing scrape: no GLM price matches found")
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
        """Return this provider's compute prices (fail-soft: empty on error)."""
        return []

__all__ = [
    "AnthropicSource",
    "HuggingFacePricingSource",
    "HuggingFaceSource",
    "LiteLLMJSONSource",
    "OpenAISource",
    "OpenRouterSource",
    "ZAIPricingSource",
    "ZAISource",
]
