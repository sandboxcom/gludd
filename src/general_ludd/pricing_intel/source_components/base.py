"""Shared contracts and validation for pricing sources."""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol, cast, runtime_checkable

from general_ludd.pricing_intel.models import ComputePrice, ModelPrice, ProviderBilling

logger = logging.getLogger(__name__)

class PricingSourceAuthenticationError(RuntimeError):
    """A pricing endpoint rejected the configured provider credentials."""


class PricingSourceDataError(ValueError):
    """A reachable pricing endpoint returned an invalid pricing contract."""


class UnavailableModelPrices(list[ModelPrice]):
    """Empty list marker for a transient fetch failure that must not replace cache."""


def _openrouter_models(response: Any) -> list[Any]:
    """Decode and validate the model list from an OpenRouter response."""
    try:
        data = response.json()
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("OpenRouter pricing response not JSON: %s", exc)
        raise PricingSourceDataError(
            "invalid OpenRouter pricing response: expected JSON"
        ) from exc

    if not isinstance(data, dict) or not isinstance(data.get("data", []), list):
        raise PricingSourceDataError(
            "invalid OpenRouter pricing response: data must be a list"
        )
    return cast(list[Any], data.get("data", []))


# ---------------------------------------------------------------------------
# Protocol (interface) for all pricing sources
# ---------------------------------------------------------------------------


@runtime_checkable
class PricingSource(Protocol):
    """Protocol every pricing source must implement.

    Sources are fail-soft for transient availability errors. Authentication and
    invalid pricing data remain visible so callers cannot treat bad data as free.
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
        """Fetch prices; return [] on outages and raise on auth/data errors."""
        ...

    def fetch_compute_prices(self) -> list[ComputePrice]:
        """Fetch current compute prices. Returns [] on any error (fail-soft)."""
        ...

__all__ = [
    "PricingSource",
    "PricingSourceAuthenticationError",
    "PricingSourceDataError",
    "UnavailableModelPrices",
]
