"""Hardware-to-model fit bridge.

Unifies the three parallel GPU probing systems (nvidia-smi, system_profiler/Metal,
rocm-smi) into a single flow and answers: "Can this model run on this hardware?"

Core types
----------
* :class:`FitResult` — the answer.
* :func:`gpu_info_to_gpu_table` — maps :class:`GpuInfo` to a
  :data:`deployment_optimizer.GPU_TABLE` key.
* :func:`unified_probe` — runs all 3 probes, returns best-match
  :class:`HardwareInventory`.
* :func:`can_run_model` — the main public API.
* :func:`_extract_model_params` — dynamic model parameter extraction from
  model names; no hardcoded MODEL_PARAMS dict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from general_ludd.hardware.survey import GpuInfo, HardwareInventory, HardwareSurvey

if TYPE_CHECKING:
    from general_ludd.pricing_intel import PricingCatalog
    from general_ludd.small_models import CapabilityEvidenceStore

# ---------------------------------------------------------------------------
# FitResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FitResult:
    can_run: bool = False
    estimated_vram_gb: float = 0.0
    quant_method: str = ""
    backend: str = ""
    reason: str = ""


# ---------------------------------------------------------------------------
# GPU name → GPU_TABLE key adapter
# ---------------------------------------------------------------------------

_GPU_NAME_PATTERNS: tuple[tuple[str, str], ...] = (
    ("h200", "h200"),
    ("h100", "h100"),
    ("a100", "a100_80"),
    ("a40", "a40"),
    ("a10g", "a10g"),
    ("a10", "a10"),
    ("l40s", "l40s"),
    ("l4", "l4"),
    ("tesla t4", "t4"),
    ("t4", "t4"),
    ("rtx 6000 ada", "rtx_6000_ada"),
    ("rtx 4090", "rtx_4090"),
    ("rtx 3090", "rtx_3090"),
)


def _gpu_name_to_table_key(name: str) -> str | None:
    lower = name.lower()
    for pattern, key in _GPU_NAME_PATTERNS:
        if pattern in lower:
            return key
    return None


def gpu_info_to_gpu_table(gpu: GpuInfo) -> str | None:
    return _gpu_name_to_table_key(gpu.name)


# ---------------------------------------------------------------------------
# Dynamic model parameter extraction
# ---------------------------------------------------------------------------

_PARAM_RE = re.compile(r"(\d+\.?\d*)\s*[bB]\b")
_MOE_RE = re.compile(r"(\d+)\s*x\s*(\d+\.?\d*)\s*[bB]\b")

_MOE_KEYWORDS: frozenset[str] = frozenset({"moe", "mixtral", "expert"})

# Minimal overrides for models whose names don't carry usable param counts.
_NAME_OVERRIDES: dict[str, dict[str, int | float | bool]] = {
    "phi-3-mini": {"params_b": 3.8},
    "phi-3-medium": {"params_b": 14.0},
    "deepseek-v3": {"params_b": 671.0, "is_moe": True, "active_params_b": 37.0},
    "deepseek-r1": {"params_b": 671.0, "is_moe": True, "active_params_b": 37.0},
    "mixtral-8x7b": {"params_b": 47.0, "is_moe": True, "active_params_b": 13.0},
    "dbrx-16x12b": {"params_b": 132.0, "is_moe": True},
}

# Fallback overlap ratio for unknown MoE "NxMB" patterns.
_MOE_FALLBACK_OVERLAP = 0.75


def _extract_model_params(model_name: str) -> dict[str, int | float | bool] | None:
    """Extract parameter count and MoE status from a model name.

    Returns a dict with ``params_b``, ``is_moe``, and optionally
    ``active_params_b``, or ``None`` if the name can't be parsed.
    """
    key = model_name.lower().strip()
    if not key:
        return None

    override = _NAME_OVERRIDES.get(key)
    if override is not None:
        return dict(override)

    is_moe = False
    params_b: float | None = None

    moe_match = _MOE_RE.search(key)
    if moe_match:
        expert_count = float(moe_match.group(1))
        base_params = float(moe_match.group(2))
        is_moe = True
        params_b = expert_count * base_params * _MOE_FALLBACK_OVERLAP
    else:
        param_match = _PARAM_RE.search(key)
        if param_match:
            params_b = float(param_match.group(1))

    if params_b is None:
        return None

    if any(kw in key for kw in _MOE_KEYWORDS):
        is_moe = True

    result: dict[str, int | float | bool] = {"params_b": params_b}
    if is_moe:
        result["is_moe"] = True
    return result


# Quant ladder: bytes-per-param, highest quality first.
_QUANT_LADDER: tuple[tuple[str, float], ...] = (
    ("fp16", 2.0),
    ("q8_0", 1.0),
    ("q6_k", 0.75),
    ("q5_k_m", 0.625),
    ("q4_k_m", 0.5),
)


def _estimate_vram_gb(params_b: float, bpp: float) -> float:
    return params_b * bpp


# ---------------------------------------------------------------------------
# Unified probe
# ---------------------------------------------------------------------------


def _build_inventory(survey: HardwareSurvey, gpus: list[GpuInfo]) -> HardwareInventory:
    return HardwareInventory(
        gpus=list(gpus),
        total_ram_gb=survey.probe_ram(),
        disk_free_gb=survey.probe_disk(),
        cpu_cores=survey.probe_cpu(),
    )


def unified_probe(*, survey: HardwareSurvey | None = None) -> HardwareInventory:
    s = survey if survey is not None else HardwareSurvey()

    nvidia = s.probe_gpu_nvidia()
    if nvidia:
        return _build_inventory(s, nvidia)

    metal = s.probe_gpu_metal()
    if metal:
        return _build_inventory(s, metal)

    rocm = s.probe_gpu_rocm()
    if rocm:
        return _build_inventory(s, rocm)

    return _build_inventory(s, [])


# ---------------------------------------------------------------------------
# can_run_model
# ---------------------------------------------------------------------------


def can_run_model(
    inventory: HardwareInventory,
    model_name: str,
    *,
    pricing_catalog: PricingCatalog | None = None,
    evidence_store: CapabilityEvidenceStore | None = None,
) -> FitResult:
    model_key = model_name.lower().strip()
    spec = _extract_model_params(model_key)

    if spec is None:
        known = _model_is_known(model_key, pricing_catalog, evidence_store)
        if known:
            return FitResult(reason=f"cannot determine parameters for known model {model_name!r}")
        return FitResult(reason=f"unknown model {model_name!r}")

    if not inventory.gpus:
        return FitResult(reason="no GPU detected")

    backend = inventory.gpus[0].backend if inventory.gpus else ""
    total_vram = inventory.total_vram_gb
    params_b = float(spec["params_b"])

    for quant, bpp in _QUANT_LADDER:
        estimated = _estimate_vram_gb(params_b, bpp)
        if estimated <= total_vram * 0.85:
            return FitResult(
                can_run=True,
                estimated_vram_gb=round(estimated, 2),
                quant_method=quant,
                backend=backend,
                reason=f"model fits at {quant} ({estimated:.1f}GB ≤ {total_vram:.1f}GB VRAM pool)",
            )

    return FitResult(
        estimated_vram_gb=round(params_b * 2.0, 2),
        backend=backend,
        reason=f"model requires {params_b * 2.0:.1f}GB at fp16, only {total_vram:.1f}GB VRAM available",
    )


def _model_is_known(
    name: str,
    pricing_catalog: PricingCatalog | None,
    evidence_store: CapabilityEvidenceStore | None,
) -> bool:
    if pricing_catalog is not None:
        for info in pricing_catalog.all_model_info():
            if info.model_id.lower() == name:
                return True
    if evidence_store is not None:
        for record in evidence_store.list_all():
            if record.get("model_profile_id", "").lower() == name:
                return True
    return False
