"""Cost estimation for small model operations — inference, download, quantization, and off-peak scheduling."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from general_ludd.infra.pricing import INFRA_PRICING, PRICING

logger = logging.getLogger(__name__)

_STORAGE_USD_PER_GB_MONTH = 0.10
_DATA_EGRESS_USD_PER_GB = 0.09
_GPU_USD_PER_HOUR = INFRA_PRICING.get("gpu_second", 0.00083) * 3600.0
_LARGE_DOWNLOAD_GB = 5.0
_OFF_PEAK_START_HOUR = 18
_OFF_PEAK_END_HOUR = 6

_MODEL_SIZE_GB: dict[str, float] = {
    "phi-2": 2.7,
    "phi-3-mini": 3.8,
    "qwen2.5-0.5b": 1.0,
    "qwen2.5-1.5b": 3.0,
    "qwen2.5-7b": 14.0,
    "llama3.1-8b": 16.0,
    "llama3.1-70b": 140.0,
    "mistral-7b": 14.0,
    "gemma-2b": 4.0,
    "gemma-7b": 14.0,
}

_SMALL_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "phi-2": (0.0001, 0.0002),
    "phi-3-mini": (0.00015, 0.0003),
    "qwen2.5-0.5b": (0.00005, 0.0001),
    "qwen2.5-1.5b": (0.0001, 0.0002),
    "qwen2.5-7b": (0.0002, 0.0004),
    "llama3.1-8b": (0.0002, 0.0004),
    "llama3.1-70b": (0.0008, 0.0015),
    "mistral-7b": (0.0002, 0.0004),
    "gemma-2b": (0.0001, 0.0002),
    "gemma-7b": (0.0002, 0.0004),
}


def _resolve_model_size(model_id: str) -> float:
    lower = model_id.lower()
    for key, gb in _MODEL_SIZE_GB.items():
        if key in lower:
            return gb

    for token in lower.replace("-", " ").split():
        if token.endswith("b") and token[:-1].replace(".", "").isdigit():
            param_count = float(token[:-1])
            return param_count * 2.0
    return 4.0


def _infer_tier(model_id: str) -> str:
    lower = model_id.lower()
    if "70b" in lower or "gpt-4" in lower or "claude" in lower:
        return "large_api"
    if "8b" in lower or "7b" in lower or "13b" in lower:
        return "medium_api"
    if any(t in lower for t in ("1.5b", "2b", "3b", "0.5b", "mini", "phi-2", "phi-3")):
        return "small_local"
    if "gpt-3" in lower:
        return "medium_api"
    size = _resolve_model_size(model_id)
    if size <= 4.0:
        return "small_local"
    if size < 20.0:
        return "medium_api"
    return "large_api"


def estimate_inference_cost(model_id: str) -> dict[str, object]:
    """Estimate per-token and per-hour inference cost for a model id."""
    lower = model_id.lower()
    tier = _infer_tier(model_id)

    input_m1m = 0.0001
    output_m1m = 0.0002

    for key, (inp, out) in _SMALL_MODEL_PRICING.items():
        if key in lower:
            input_m1m = inp
            output_m1m = out
            break

    if any(api in lower for api in ("gpt", "claude", "opus", "sonnet", "haiku")):
        for key, (inp, out) in PRICING.items():
            if key in lower:
                input_m1m = inp
                output_m1m = out
                break

    gpu_usd_per_hour = round(_GPU_USD_PER_HOUR, 4)

    if tier == "large_api":
        tokens_per_hour = 200_000
    elif tier == "medium_api":
        tokens_per_hour = 500_000
    else:
        tokens_per_hour = 2_000_000

    token_cost_per_hour = round((tokens_per_hour / 1_000_000.0) * ((input_m1m + output_m1m) / 2.0), 4)
    if tier == "small_local":
        estimated_usd_per_hour = round(token_cost_per_hour, 6)
    else:
        estimated_usd_per_hour = round(max(token_cost_per_hour, gpu_usd_per_hour * 0.1), 6)

    return {
        "model_id": model_id,
        "tier": tier,
        "input_usd_per_1m_tokens": round(input_m1m, 6),
        "output_usd_per_1m_tokens": round(output_m1m, 6),
        "estimated_tokens_per_hour": tokens_per_hour,
        "estimated_usd_per_hour": estimated_usd_per_hour,
        "estimated_gpu_usd_per_hour": gpu_usd_per_hour,
    }


def estimate_download_cost(model_id: str, size_gb: float | None = None) -> dict[str, object]:
    """Estimate egress, storage, and off-peak preference for a model download."""
    size = size_gb if size_gb is not None else _resolve_model_size(model_id)
    data_transfer_usd = round(size * _DATA_EGRESS_USD_PER_GB, 4)
    storage_monthly = round(size * _STORAGE_USD_PER_GB_MONTH, 4)
    prefer_off_peak = size >= _LARGE_DOWNLOAD_GB
    return {
        "model_id": model_id,
        "size_gb": round(size, 2),
        "data_transfer_usd": data_transfer_usd,
        "estimated_storage_usd_per_month": storage_monthly,
        "prefer_off_peak": prefer_off_peak,
    }


def estimate_quantize_cost(model_id: str, size_gb: float, method: str = "q4_k_m") -> dict[str, object]:
    """Estimate GPU hours and cost for quantizing a model with a given method."""
    gpu_hours_per_gb: dict[str, float] = {
        "q4_0": 0.15,
        "q4_k_m": 0.2,
        "q5_k_m": 0.25,
        "q8_0": 0.35,
        "f16": 0.02,
    }
    hours_per_gb = gpu_hours_per_gb.get(method, 0.2)
    gpu_hours = round(size_gb * hours_per_gb, 4)
    cost_usd = round(gpu_hours * _GPU_USD_PER_HOUR, 6)
    return {
        "model_id": model_id,
        "method": method,
        "size_gb": round(size_gb, 2),
        "estimated_gpu_hours": gpu_hours,
        "estimated_cost_usd": cost_usd,
        "gpu_usd_per_hour": round(_GPU_USD_PER_HOUR, 4),
    }


def is_off_peak(now: datetime | None = None) -> bool:
    """Return True when the given time falls in the off-peak window."""
    if now is None:
        now = datetime.now(UTC)

    if now.weekday() >= 5:
        return True

    hour = now.hour
    return hour >= _OFF_PEAK_START_HOUR or hour < _OFF_PEAK_END_HOUR


def next_off_peak_window(now: datetime | None = None) -> dict[str, object]:
    """Describe the next off-peak window relative to the given time."""
    if now is None:
        now = datetime.now(UTC)

    if is_off_peak(now):
        return {
            "starts_at": now.isoformat(),
            "seconds_until": 0,
            "is_off_peak_now": True,
        }

    current_hour = now.hour
    current_weekday = now.weekday()

    if current_weekday == 4 and current_hour >= _OFF_PEAK_START_HOUR - 1:
        target = now.replace(hour=_OFF_PEAK_START_HOUR, minute=0, second=0, microsecond=0)
        seconds = (target - now).total_seconds()
        return {
            "starts_at": target.isoformat(),
            "seconds_until": max(0, int(seconds)),
            "is_off_peak_now": False,
        }

    if current_hour < _OFF_PEAK_END_HOUR:
        return {
            "starts_at": now.isoformat(),
            "seconds_until": 0,
            "is_off_peak_now": True,
        }

    if current_hour >= _OFF_PEAK_START_HOUR:
        return {
            "starts_at": now.isoformat(),
            "seconds_until": 0,
            "is_off_peak_now": True,
        }

    off_peak_start = now.replace(hour=_OFF_PEAK_START_HOUR, minute=0, second=0, microsecond=0)
    seconds = (off_peak_start - now).total_seconds()
    if seconds < 0:
        seconds += 24 * 3600
    return {
        "starts_at": off_peak_start.isoformat(),
        "seconds_until": int(seconds),
        "is_off_peak_now": False,
    }


def should_defer_download(
    size_gb: float,
    now: datetime | None = None,
    threshold_gb: float | None = None,
) -> dict[str, object]:
    """Advise whether a download of the given size should be deferred to off-peak."""
    off_peak = is_off_peak(now)
    window = next_off_peak_window(now)
    large_threshold = _LARGE_DOWNLOAD_GB if threshold_gb is None else threshold_gb

    if not off_peak and size_gb >= large_threshold:
        return {
            "defer": True,
            "reason": "large_download_during_peak",
            "size_gb": round(size_gb, 2),
            "next_off_peak": window,
        }

    return {
        "defer": False,
        "reason": "ok_to_proceed",
        "size_gb": round(size_gb, 2),
        "next_off_peak": window,
    }


def compute_cost_score(model_id: str) -> float:
    """Compute a 0-1 cost score for a model, weighted by tier."""
    cost_info = estimate_inference_cost(model_id)
    est_raw = cost_info.get("estimated_usd_per_hour", 0.01)
    est_usd_per_hour = float(est_raw) if isinstance(est_raw, (int, float)) else 0.01
    if est_usd_per_hour <= 0:
        est_usd_per_hour = 0.01

    tier_raw = cost_info.get("tier", "small_local")
    tier = str(tier_raw) if tier_raw else "small_local"
    tier_multiplier = {"small_local": 1.0, "medium_api": 0.7, "large_api": 0.4}.get(tier, 1.0)

    cost_score_raw = max(0.0, min(1.0, 0.01 / est_usd_per_hour))

    return round(cost_score_raw * tier_multiplier, 4)


__all__ = [
    "compute_cost_score",
    "estimate_download_cost",
    "estimate_inference_cost",
    "estimate_quantize_cost",
    "is_off_peak",
    "next_off_peak_window",
    "should_defer_download",
]
