"""Unified-memory and discrete-VRAM policy for local model smoke tests."""

from __future__ import annotations

import importlib
import os
import platform
from contextlib import suppress
from dataclasses import asdict, dataclass
from typing import Literal

MemoryKind = Literal["discrete", "unified", "unknown"]
FitStatus = Literal["fit", "reject", "unknown"]


@dataclass(frozen=True)
class MemoryInfo:
    """Runtime memory facts shared by local GPU smoke harnesses.

    ``vram`` is a compatibility spelling for discrete device memory used by
    the Mac harness; the policy otherwise uses ``discrete`` internally.
    """

    kind: Literal["vram", "unified", "unknown"]
    total_bytes: int
    available_bytes: int
    backend: str
    device: str


@dataclass(frozen=True)
class ModelFitEvaluation:
    """Compatibility result with an explicit fail-closed ``fits`` boolean."""

    fits: bool
    status: FitStatus
    required_bytes: int
    reserved_bytes: int
    reason: str


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


def detect_memory(backend: str | None = None) -> MemoryInfo:
    """Detect local accelerator memory without making a device allocation."""
    requested = (backend or "auto").lower()
    torch = None
    with suppress(ImportError):
        torch = importlib.import_module("torch")

    cuda = getattr(torch, "cuda", None)
    cuda_available = bool(cuda and getattr(cuda, "is_available", lambda: False)())
    if requested in {"cuda", "rocm", "auto"} and cuda is not None and cuda_available:
        props = cuda.get_device_properties(0)
        total = int(getattr(props, "total_memory", 0))
        available = total
        with suppress(AttributeError, RuntimeError, TypeError, ValueError):
            available, _ = (int(v) for v in cuda.mem_get_info(0))
        hip = bool(getattr(getattr(torch, "version", None), "hip", None))
        actual_backend = "rocm" if hip else "cuda"
        if requested in {"cuda", "rocm"} and requested != actual_backend:
            return MemoryInfo("unknown", total, available, actual_backend, str(getattr(props, "name", "GPU")))
        return MemoryInfo("vram", total, available, actual_backend, str(getattr(props, "name", "GPU")))

    mps = getattr(getattr(torch, "backends", None), "mps", None)
    mps_available = bool(mps and getattr(mps, "is_available", lambda: False)())
    if requested in {"mps", "auto"} and mps_available:
        system_total = _system_memory_bytes()
        return MemoryInfo("unified", system_total or 0, system_total or 0, "mps", "Apple Silicon")

    is_apple_silicon = platform.system() == "Darwin" and platform.machine() in {
        "arm64", "aarch64"
    }
    if requested == "mps" or (requested == "auto" and is_apple_silicon):
        system_total = _system_memory_bytes()
        return MemoryInfo("unified", system_total or 0, system_total or 0, "mps", "Apple Silicon")
    return MemoryInfo("unknown", 0, 0, requested, "unknown")


def _system_memory_bytes() -> int | None:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, OSError, ValueError):
        return None
    return pages * page_size if pages > 0 and page_size > 0 else None


def evaluate_model_fit(
    memory: MemoryInfo,
    parameters: int,
    *,
    quantization_bits: int = 4,
    reserve_ratio: float = 0.20,
) -> ModelFitEvaluation:
    """Evaluate a model against detected memory before loading any tensors."""
    if parameters <= 0:
        raise ValueError("parameters must be greater than zero")
    capacity = memory.available_bytes or memory.total_bytes
    result = assess_model_fit(
        capacity or None,
        parameters / 1_000_000_000,
        quantization_bits,
        kind="unified" if memory.kind == "unified" else "discrete" if memory.kind == "vram" else "unknown",
        reserve_fraction=reserve_ratio,
    )
    reason = f"{memory.kind} memory: {result.reason}"
    return ModelFitEvaluation(
        fits=result.status == "fit",
        status=result.status,
        required_bytes=result.footprint_bytes,
        reserved_bytes=result.budget_bytes or 0,
        reason=reason,
    )


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


def model_guidance(kind: str) -> dict[str, object]:
    """Describe model choices that match unified-memory or discrete-VRAM hosts."""
    normalized = kind.lower()
    if normalized == "vram":
        normalized = "discrete"
    if normalized == "unified":
        return {
            "memory_kind": "unified",
            "strategy": "capacity-first",
            "preferred_models": ["3B Q4", "7B Q4"],
            "avoid": ["long-context", "concurrent-models", "13B+ dense"],
            "reason": "the operating system and accelerator share one memory pool",
        }
    if normalized == "discrete":
        return {
            "memory_kind": "discrete",
            "strategy": "throughput",
            "preferred_models": ["7B Q4", "13B Q4", "34B Q4 when fit"],
            "avoid": ["models exceeding usable VRAM", "unsupported driver/runtime builds"],
            "reason": "dedicated VRAM isolates model allocations from host system memory",
        }
    return {
        "memory_kind": "unknown",
        "strategy": "fail-closed",
        "preferred_models": [],
        "avoid": ["any live model"],
        "reason": "capacity and backend could not be proven",
    }
