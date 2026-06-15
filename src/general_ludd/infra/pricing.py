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
