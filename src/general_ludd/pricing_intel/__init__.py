"""Pricing intelligence: live model API pricing, GPU compute pricing, billing semantics.

This package provides structured access to:
- Model API pricing (per-token) from OpenAI, Anthropic, OpenRouter, HuggingFace, Fireworks
- Cloud GPU compute pricing from RunPod, Lambda Labs, AWS, GCP
- Billing semantics: granularity (per-second/per-hour/per-token), terms (prepaid vs postpaid),
  minimum charges, spot availability

Integration: PricingCatalog is wired into SpendLimiter (catalog=...) and used
as the primary source for token_cost_usd(); a /api/pricing facet serves live
rates to clients.

Public API:
    from general_ludd.pricing_intel import PricingCatalog, BillingGranularity, BillingTerms
    from general_ludd.pricing_intel import ModelPrice, ComputePrice, ProviderBilling
"""

from general_ludd.pricing_intel.catalog import PricingCatalog
from general_ludd.pricing_intel.models import (
    BillingGranularity,
    BillingTerms,
    ComputePrice,
    ModelInfo,
    ModelPrice,
    ProviderBilling,
)

__all__ = [
    "BillingGranularity",
    "BillingTerms",
    "ComputePrice",
    "ModelInfo",
    "ModelPrice",
    "PricingCatalog",
    "ProviderBilling",
]
