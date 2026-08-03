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
"""

from __future__ import annotations

from dataclasses import dataclass

from general_ludd.hardware.survey import GpuInfo, HardwareInventory, HardwareSurvey

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
# Known local model profiles (VRAM sizing)
# ---------------------------------------------------------------------------

_KNOWN_MODELS: dict[str, dict[str, int | float | bool]] = {
    "deepseek-v3": {"params_b": 671.0, "is_moe": True, "active_params_b": 37.0},
    "deepseek-r1": {"params_b": 671.0, "is_moe": True, "active_params_b": 37.0},
    "llama-3.1-8b": {"params_b": 8.0},
    "llama-3.1-70b": {"params_b": 70.0},
    "llama-3.1-405b": {"params_b": 405.0},
    "mistral-7b": {"params_b": 7.0},
    "mixtral-8x7b": {"params_b": 47.0, "is_moe": True, "active_params_b": 13.0},
    "qwen-2.5-72b": {"params_b": 72.0},
    "qwen-2.5-7b": {"params_b": 7.0},
    "gemma-2-2b": {"params_b": 2.0},
    "gemma-2-9b": {"params_b": 9.0},
    "codestral-22b": {"params_b": 22.0},
    "phi-3-mini": {"params_b": 3.8},
    "phi-3-medium": {"params_b": 14.0},
}

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


def can_run_model(inventory: HardwareInventory, model_name: str) -> FitResult:
    model_key = model_name.lower().strip()
    spec = _KNOWN_MODELS.get(model_key)

    if spec is None:
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
