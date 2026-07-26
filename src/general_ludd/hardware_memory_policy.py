"""Unified-memory and discrete-VRAM policy for local model smoke tests."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

MemoryKind = Literal["discrete", "unified", "unknown"]
FitStatus = Literal["fit", "reject", "unknown"]


@dataclass(frozen=True)
class MemoryBudget:
    """Total and safely usable memory for a local model run."""

    kind: MemoryKind
    total_bytes: int | None
    reserve_fraction: float
    reserve_bytes: int | None
    usable_bytes: int | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ModelFit:
    """Result of checking a quantized model's estimated footprint."""

    status: FitStatus
    params_b: float
    quant_bits: int
    footprint_bytes: int
    budget_bytes: int | None
    reason: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def classify_memory_kind(backend: str, device_name: str, *, is_integrated: bool | None = None) -> MemoryKind:
    """Classify a GPU as discrete VRAM or shared/unified memory."""
    if is_integrated is True:
        return "unified"
    normalized = device_name.lower()
    unified_markers = ("integrated", "apu", "unified", "radeon graphics", "vega")
    if any(marker in normalized for marker in unified_markers):
        return "unified"
    if backend.lower() in {"cuda", "rocm"}:
        return "discrete"
    return "unknown"


def memory_budget(total_memory_bytes: int | None, *, kind: MemoryKind, reserve_fraction: float = 0.20) -> MemoryBudget:
    """Reserve headroom for the runtime and return the usable model budget."""
    if not 0.05 <= reserve_fraction <= 0.50:
        raise ValueError("reserve_fraction must be between 0.05 and 0.50")
    if total_memory_bytes is None or total_memory_bytes <= 0:
        return MemoryBudget(kind, None, reserve_fraction, None, None)
    reserve = int(total_memory_bytes * reserve_fraction)
    return MemoryBudget(kind, total_memory_bytes, reserve_fraction, reserve, total_memory_bytes - reserve)


def estimate_model_bytes(params_b: float, quant_bits: int, *, overhead: float = 1.20) -> int:
    """Estimate weight plus runtime overhead for a parameter count and quantization."""
    if params_b <= 0:
        raise ValueError("params_b must be greater than zero")
    if quant_bits not in {2, 4, 8, 16, 32}:
        raise ValueError("quant_bits must be one of 2, 4, 8, 16, or 32")
    if overhead < 1.0:
        raise ValueError("overhead must be at least 1.0")
    return int(params_b * 1_000_000_000 * quant_bits / 8 * overhead)


def assess_model_fit(
    total_memory_bytes: int | None,
    params_b: float,
    quant_bits: int,
    *,
    kind: MemoryKind = "unknown",
    reserve_fraction: float = 0.20,
) -> ModelFit:
    """Reject a model that exceeds safe capacity; unknown capacity fails closed."""
    footprint = estimate_model_bytes(params_b, quant_bits)
    budget = memory_budget(total_memory_bytes, kind=kind, reserve_fraction=reserve_fraction)
    if budget.usable_bytes is None:
        return ModelFit("unknown", params_b, quant_bits, footprint, None, "device memory capacity is unknown")
    if footprint > budget.usable_bytes:
        return ModelFit(
            "reject", params_b, quant_bits, footprint, budget.usable_bytes,
            "estimated model footprint exceeds memory after reserved headroom",
        )
    return ModelFit("fit", params_b, quant_bits, footprint, budget.usable_bytes, "model fits reserved budget")


def recommend_models(total_memory_bytes: int | None, *, reserve_fraction: float = 0.20) -> list[dict[str, object]]:
    """Return conservative model/quantization choices for the usable budget."""
    candidates = ((3.0, 4, "3B Q4"), (7.0, 4, "7B Q4"), (13.0, 4, "13B Q4"), (34.0, 4, "34B Q4"))
    return [
        {
            "label": label,
            "params_b": params_b,
            "quant_bits": bits,
            "status": assess_model_fit(total_memory_bytes, params_b, bits, reserve_fraction=reserve_fraction).status,
        }
        for params_b, bits, label in candidates
        if total_memory_bytes is None
        or assess_model_fit(total_memory_bytes, params_b, bits, reserve_fraction=reserve_fraction).status == "fit"
    ]
