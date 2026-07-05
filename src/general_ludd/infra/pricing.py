"""Pricing tables and cost helpers for the spend-limiter subsystem.

All rates are documented constants — do not fabricate numbers; update this
table when Anthropic/OpenAI publish new pricing.

Token pricing is stored as USD per 1 000 tokens:
    PRICING[model_id] = (input_usd_per_1k, output_usd_per_1k)

Infra pricing is stored as USD per unit (e.g. per gpu-second):
    INFRA_PRICING[kind] = usd_per_unit

Both tables have a "__default__" fallback used when a model_id / kind is
not found in the respective table.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from general_ludd.pricing_intel.models import BillingGranularity, ComputePrice

if TYPE_CHECKING:
    from general_ludd.pricing_intel.catalog import PricingCatalog

logger = logging.getLogger(__name__)

# Seconds covered by one billing unit, for the time-based granularities.
# Used to normalize a ComputePrice of any granularity to a per-second rate.
# Non-time granularities (per_token, per_request, flat) are intentionally
# absent — they are not applicable to GPU-second billing and trigger the
# static-table fallback instead.
_GRANULARITY_SECONDS: dict[BillingGranularity, float] = {
    BillingGranularity.per_second: 1.0,
    BillingGranularity.per_minute: 60.0,
    BillingGranularity.per_hour: 3600.0,
}

# ---------------------------------------------------------------------------
# Token pricing  (USD per 1 000 tokens, as of 2025-Q4)
# ---------------------------------------------------------------------------
# Source: https://www.anthropic.com/pricing  and  https://openai.com/pricing
#
# Format: model_id -> (input_usd_per_1k, output_usd_per_1k)
# ---------------------------------------------------------------------------
PRICING: dict[str, tuple[float, float]] = {
    # Anthropic — Claude 3.5 family
    "claude-3-5-sonnet-20241022": (0.003, 0.015),
    "claude-3-5-haiku-20241022": (0.0008, 0.004),
    # Anthropic — Claude 3 family
    "claude-3-opus-20240229": (0.015, 0.075),
    "claude-3-sonnet-20240229": (0.003, 0.015),
    "claude-3-haiku-20240307": (0.00025, 0.00125),
    # OpenAI — GPT-4o family
    "gpt-4o": (0.005, 0.015),
    "gpt-4o-mini": (0.00015, 0.0006),
    # OpenAI — GPT-4 family
    "gpt-4-turbo": (0.01, 0.03),
    "gpt-4": (0.03, 0.06),
    # OpenAI — GPT-3.5
    "gpt-3.5-turbo": (0.0005, 0.0015),
    # Fallback for unknown / future models — set to a conservative estimate
    "__default__": (0.005, 0.015),
}

# ---------------------------------------------------------------------------
# Cloud-infra pricing  (USD per unit)
# ---------------------------------------------------------------------------
# Format: kind -> usd_per_unit
#
# Supported kinds:
#   gpu_second   — one second of A100/H100 GPU time (~$3/hr ÷ 3600 s)
#   cpu_second   — one second of vCPU compute time
#   api_call     — one API call overhead (provider surcharge)
#   __default__  — fallback for unknown kinds
# ---------------------------------------------------------------------------
INFRA_PRICING: dict[str, float] = {
    "gpu_second": 0.00083,   # ≈ $3.00 / hour (A100 spot estimate)
    "cpu_second": 0.0000028, # ≈ $0.01 / hour (light vCPU)
    "api_call": 0.000001,    # negligible per-call overhead
    "__default__": 0.0000028,
}


def token_cost_usd(model: str, in_tokens: int, out_tokens: int) -> float:
    """Compute the USD cost for a model call given token counts.

    Falls back to the ``__default__`` entry when ``model`` is not in the
    pricing table.

    Args:
        model:      Model identifier (e.g. ``"claude-3-5-sonnet-20241022"``).
        in_tokens:  Number of input/prompt tokens.
        out_tokens: Number of output/completion tokens.

    Returns:
        Total cost in USD as a float.
    """
    inp_per_1k, out_per_1k = PRICING.get(model, PRICING["__default__"])
    return inp_per_1k * (in_tokens / 1000.0) + out_per_1k * (out_tokens / 1000.0)


def infra_cost_usd(kind: str, units: float) -> float:
    """Compute the USD cost for cloud-infra usage.

    Falls back to the ``__default__`` entry when ``kind`` is not in the
    infra pricing table.

    Args:
        kind:  Infra resource kind (e.g. ``"gpu_second"``).
        units: Number of units consumed.

    Returns:
        Total cost in USD as a float.
    """
    rate = INFRA_PRICING.get(kind, INFRA_PRICING["__default__"])
    return rate * units


class InfraTracker:
    """GPU/compute cost projector and accumulator: PricingCatalog primary, static fallback.

    Wraps :func:`infra_cost_usd` so callers get live catalog prices when a
    :class:`~general_ludd.pricing_intel.catalog.PricingCatalog` is available,
    and the static ``INFRA_PRICING`` table otherwise.  Mirrors the fail-soft
    pattern of :meth:`SpendLimiter.token_cost_usd`: a missing or erroring
    catalog price NEVER breaks cost projection — the static table is used.

    Accumulation methods (:meth:`record_gpu_seconds`, :meth:`get_total_infra_cost`,
    :meth:`get_infra_cost_by_provider`) maintain a thread-safe running total of
    infrastructure spend for observability and cost-accounting endpoints.

    Args:
        catalog:          Optional :class:`PricingCatalog` used as the PRIMARY
                          source for :meth:`gpu_cost_usd`.  When the catalog
                          returns no price for a SKU (or is omitted, or raises,
                          or returns a non-time granularity), the static
                          ``INFRA_PRICING`` table is used as a fallback.
        default_provider: Provider slug queried when no explicit ``provider``
                          is passed to :meth:`gpu_cost_usd`.  Defaults to
                          ``"runpod"``.
    """

    def __init__(
        self,
        catalog: PricingCatalog | None = None,
        default_provider: str = "runpod",
    ) -> None:
        self._catalog = catalog
        self._default_provider = default_provider
        import threading

        self._total_infra_cost: float = 0.0
        self._infra_cost_by_provider: dict[str, float] = {}
        self._infra_cost_by_project: dict[str, float] = {}
        self._cost_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Cost projection
    # ------------------------------------------------------------------

    def gpu_cost_usd(
        self,
        sku: str,
        gpu_seconds: float,
        *,
        provider: str | None = None,
        spot: bool = False,
    ) -> float:
        """USD cost for GPU compute, PricingCatalog primary, static fallback.

        Resolution order:

        1. If a :class:`PricingCatalog` is configured, query it via
           ``compute_price(provider, sku, spot)``.  The provider is taken
           from the explicit ``provider`` arg when given; otherwise
           ``default_provider`` is used.  The returned :class:`ComputePrice`
           is normalized to a per-second rate from its billing granularity
           (per_second / per_minute / per_hour).
        2. On any miss, catalog error, non-time granularity, or when no
           catalog is configured, fall back to
           :func:`infra_cost_usd` with ``kind="gpu_second"`` (the static
           ``INFRA_PRICING`` table).  This guarantees cost projection never
           breaks because a live price is unavailable.

        Args:
            sku:          Compute SKU (e.g. ``"A100-SXM4-80GB-1x"``).
            gpu_seconds:  Seconds of GPU time consumed.
            provider:     Optional explicit provider slug (e.g. ``"aws"``).
                          When omitted, ``default_provider`` is used.
            spot:         If True, prefer spot pricing from the catalog.

        Returns:
            Total cost in USD as a float.
        """
        price = self._catalog_compute_price(sku, provider=provider, spot=spot)
        if price is not None:
            per_second = self._per_second_rate(price)
            if per_second is not None:
                return per_second * gpu_seconds
        return infra_cost_usd("gpu_second", gpu_seconds)

    # ------------------------------------------------------------------
    # Cost accumulation
    # ------------------------------------------------------------------

    def record_gpu_seconds(
        self,
        provider: str,
        gpu_type: str,
        seconds: float,
        spot: bool = False,
        project_id: str | None = None,
    ) -> None:
        """Compute the cost of GPU seconds and add to the running infra total.

        Uses :meth:`gpu_cost_usd` (PricingCatalog primary, static table
        fallback) to determine the unit price, so live catalog rates are
        picked up automatically.

        Args:
            provider:   Provider slug (e.g. ``"runpod"``, ``"aws"``).
            gpu_type:   GPU SKU (e.g. ``"A100-SXM4-80GB-1x"``).
            seconds:    Seconds of GPU time consumed.
            spot:       If True, apply spot pricing discount.
            project_id: Optional project the cost is attributed to.
        """
        import math

        if not isinstance(seconds, (int, float)):
            return
        if not math.isfinite(seconds) or seconds < 0:
            return
        cost = self.gpu_cost_usd(
            sku=gpu_type,
            gpu_seconds=seconds,
            provider=provider,
            spot=spot,
        )
        with self._cost_lock:
            self._total_infra_cost += cost
            self._infra_cost_by_provider[provider] = (
                self._infra_cost_by_provider.get(provider, 0.0) + cost
            )
            if project_id is not None:
                self._infra_cost_by_project[project_id] = (
                    self._infra_cost_by_project.get(project_id, 0.0) + cost
                )

    def get_total_infra_cost(self) -> float:
        """Return the total accumulated infrastructure spend in USD."""
        with self._cost_lock:
            return self._total_infra_cost

    def get_infra_cost_by_provider(self) -> dict[str, float]:
        """Return infrastructure spend broken down by provider slug."""
        with self._cost_lock:
            return dict(self._infra_cost_by_provider)

    def get_infra_cost_by_project(self) -> dict[str, float]:
        """Return infrastructure spend broken down by project_id."""
        with self._cost_lock:
            return dict(self._infra_cost_by_project)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _catalog_compute_price(
        self,
        sku: str,
        *,
        provider: str | None,
        spot: bool,
    ) -> ComputePrice | None:
        """Return a :class:`ComputePrice` from the catalog, or ``None``.

        Returns ``None`` when no catalog is configured, the catalog misses,
        or the catalog raises.  All exceptions are swallowed so the caller
        falls back to the static table — a missing live price must never
        break cost projection.
        """
        catalog = self._catalog
        if catalog is None:
            return None
        slug = provider if provider is not None else self._default_provider
        try:
            return catalog.compute_price(slug, sku, spot=spot)
        except Exception as exc:
            logger.warning(
                "InfraTracker: catalog.compute_price(%s, %s, spot=%s) raised %s; "
                "falling back to static infra pricing.",
                slug,
                sku,
                spot,
                exc,
            )
            return None

    @staticmethod
    def _per_second_rate(price: ComputePrice) -> float | None:
        """Normalize a :class:`ComputePrice` to USD-per-second.

        Returns ``None`` for non-time granularities (per_token, per_request,
        flat) which are not applicable to GPU-second billing; callers fall
        back to the static table in that case.
        """
        seconds_per_unit = _GRANULARITY_SECONDS.get(price.granularity)
        if seconds_per_unit is None:
            return None
        return price.usd_per_unit / seconds_per_unit
