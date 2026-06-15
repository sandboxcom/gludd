"""Data models for pricing intelligence: billing semantics, model prices, compute prices.

KEY CONCEPTS:
- BillingGranularity: the unit of billing — per-second, per-minute, per-hour, per-token, etc.
  This matters for cost estimation: a 5-minute job on a per-second billed platform costs
  differently than on a per-hour billed platform (no rounding to the next hour).
- BillingTerms: PREPAID vs POSTPAID.
  PREPAID (e.g. RunPod, Lambda Labs) = you maintain a balance; the provider deducts from it.
  POSTPAID (e.g. AWS, GCP, Anthropic) = you're billed at end of month or per-use.
  This affects cash-flow planning and whether a zero-balance means service interruption.
- min_charge: the minimum you pay per invocation/session regardless of actual usage.
  Matters for short jobs where the minimum exceeds actual usage.
- spot_available: whether spot (interruptible, deeply discounted) instances are offered.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class BillingGranularity(StrEnum):
    """The unit at which resource usage is billed.

    per_second  — usage is metered and billed to the second (RunPod, Lambda Labs GPU,
                  AWS EC2 Linux, GCP Compute Engine). Cheapest for short-burst workloads.
    per_minute  — billed to the minute (legacy AWS Windows instances; some specialized
                  GPU vendors).
    per_hour    — billed in full-hour increments (traditional cloud, some bare-metal
                  providers). Minimum 1 hour charge even for 1-minute jobs.
    per_token   — billed per input/output token (all LLM API providers: OpenAI,
                  Anthropic, OpenRouter routed models).
    per_request — flat fee per API call regardless of size (some embedding endpoints,
                  image generation APIs).
    flat        — fixed subscription or reservation fee independent of usage.
    """

    per_second = "per_second"
    per_minute = "per_minute"
    per_hour = "per_hour"
    per_token = "per_token"
    per_request = "per_request"
    flat = "flat"


class BillingTerms(StrEnum):
    """How and when the customer is charged.

    prepaid_balance   — customer must maintain a credit balance; usage is deducted
                        continuously. Balance exhaustion = immediate service stop.
                        Examples: RunPod, Lambda Labs, Replicate, Together AI.
    postpaid_monthly  — usage is aggregated over the calendar month and invoiced
                        at month-end. No upfront deposit required; risk is borne
                        by the provider until billing date.
                        Examples: AWS, GCP, Azure, Anthropic (business/API).
    postpaid_per_use  — billed immediately after each request or each session ends
                        (some pay-as-you-go APIs with per-call invoicing).
                        Examples: OpenAI (consumer pay-as-you-go), HuggingFace
                        Inference Endpoints one-off calls.
    """

    prepaid_balance = "prepaid_balance"
    postpaid_monthly = "postpaid_monthly"
    postpaid_per_use = "postpaid_per_use"


@dataclass
class ProviderBilling:
    """Complete billing semantics descriptor for a provider.

    Attributes:
        provider:       Canonical provider slug (e.g. "runpod", "aws", "anthropic").
        granularity:    Unit of billing (per_second, per_token, etc.).
        terms:          When and how the customer is charged.
        currency:       ISO 4217 currency code (almost always "USD").
        min_charge:     Minimum charge per unit (session/request/invocation) in USD.
                        None = no documented minimum. 0.0 = no minimum enforced.
        spot_available: Whether spot/preemptible/interruptible instances are offered.
        notes:          Free-text notes for billing caveats and source references.
    """

    provider: str
    granularity: BillingGranularity
    terms: BillingTerms
    currency: str = "USD"
    min_charge: float | None = None
    spot_available: bool = False
    notes: str = ""


@dataclass
class ModelPrice:
    """Token pricing for a specific model from a specific provider.

    Attributes:
        provider:          Provider slug (e.g. "anthropic", "openai", "openrouter").
        model_id:          Model identifier as used by the provider's API.
        input_usd_per_1k:  Cost in USD per 1,000 input tokens.
        output_usd_per_1k: Cost in USD per 1,000 output tokens.
        fetched_at:        UNIX timestamp when this price was fetched/recorded.
        source:            URL or description of where this price was obtained.
                           REQUIRED — never store a price without documenting its source.
        context_window:    Maximum context window in tokens (if known).
        notes:             Optional caveats (e.g. "cached input at 50% discount").
    """

    provider: str
    model_id: str
    input_usd_per_1k: float
    output_usd_per_1k: float
    fetched_at: float = field(default_factory=time.time)
    source: str = ""
    context_window: int | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError(
                f"ModelPrice for {self.provider}/{self.model_id} has no source. "
                "All prices must document their source URL or method."
            )


@dataclass
class ComputePrice:
    """GPU/compute instance pricing from a cloud provider.

    Attributes:
        provider:       Provider slug (e.g. "runpod", "lambda_labs", "aws", "gcp").
        sku:            SKU or instance type (e.g. "A100-80GB", "p4d.24xlarge",
                        "a2-highgpu-1g").
        usd_per_unit:   Price per billing unit. The unit is defined by granularity:
                        per_second -> USD/second; per_hour -> USD/hour.
        granularity:    Billing granularity for this SKU.
        spot:           True if this is a spot/preemptible/interruptible price.
        terms:          Billing terms (prepaid_balance, postpaid_monthly, etc.).
        fetched_at:     UNIX timestamp when this price was fetched/recorded.
        source:         URL or description of where this price was obtained.
        gpu_count:      Number of GPUs in this instance (if applicable).
        gpu_type:       GPU model/family (e.g. "A100", "H100", "RTX 4090").
        notes:          Optional caveats.
    """

    provider: str
    sku: str
    usd_per_unit: float
    granularity: BillingGranularity
    spot: bool
    terms: BillingTerms
    fetched_at: float = field(default_factory=time.time)
    source: str = ""
    gpu_count: int | None = None
    gpu_type: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError(
                f"ComputePrice for {self.provider}/{self.sku} has no source. "
                "All prices must document their source URL or method."
            )

    def usd_per_hour(self) -> float:
        """Normalize price to USD/hour for comparison across granularities."""
        if self.granularity == BillingGranularity.per_second:
            return self.usd_per_unit * 3600
        if self.granularity == BillingGranularity.per_minute:
            return self.usd_per_unit * 60
        if self.granularity == BillingGranularity.per_hour:
            return self.usd_per_unit
        # per_token / per_request / flat: not directly comparable to hourly
        return self.usd_per_unit


@dataclass
class ModelInfo:
    """Combined model intelligence: identity, context, quantization, quality descriptors.

    Attributes:
        model_id:              Canonical model identifier.
        provider:              Provider slug.
        context_window:        Maximum context window in tokens.
        quantization:          Precision/quantization info if known (links to
                               QuantizationInfo from models.quantization).
                               Stored as a dict for decoupled serialization.
        quality_descriptors:   Provider-reported or community quality descriptors.
                               e.g. {"reasoning": "strong", "code": "excellent",
                                     "context_handling": "good"}.
        pricing:               Latest ModelPrice if known.
        notes:                 Free-text notes.
    """

    model_id: str
    provider: str
    context_window: int | None = None
    quantization: dict[str, Any] | None = None
    quality_descriptors: dict[str, Any] = field(default_factory=dict)
    pricing: ModelPrice | None = None
    notes: str = ""
