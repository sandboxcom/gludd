from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from general_ludd.infra.deployment_optimizer import HardwareProfile

__all__ = ["DeploymentOptimizationConfig"]


_QUANT_BYTES_PER_PARAM: dict[str, float] = {
    "fp16": 2.0,
    "bf16": 2.0,
    "fp8": 1.0,
    "int8": 1.0,
    "awq": 0.5,
    "gptq": 0.5,
    "q4_k_m": 0.5,
    "q5_k_m": 0.625,
    "q6_k": 0.75,
    "q8_0": 1.0,
}


@dataclass
class DeploymentOptimizationConfig:
    vllm_defaults: dict[str, Any] = field(default_factory=dict)
    llamacpp_defaults: dict[str, Any] = field(default_factory=dict)
    hardware_presets: dict[str, dict[str, Any]] = field(default_factory=dict)
    quantization_recommendations: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str | Path) -> DeploymentOptimizationConfig:
        p = Path(path)
        with open(p) as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
        return cls(
            vllm_defaults=data.get("vllm", {}).get("defaults", {}),
            llamacpp_defaults=data.get("llamacpp", {}).get("defaults", {}),
            hardware_presets=data.get("hardware_presets", {}),
            quantization_recommendations=data.get("quantization_recommendations", []),
        )

    def get_preset(self, engine: str, gpu_type: str, **kwargs: object) -> dict[str, object]:
        engine = engine.lower()
        if engine not in ("vllm", "llamacpp"):
            raise ValueError(f"unknown engine {engine!r}")

        gpu = gpu_type.lower()
        defaults: dict[str, Any] = dict(self.vllm_defaults if engine == "vllm" else self.llamacpp_defaults)

        preset: dict[str, Any] | None = self.hardware_presets.get(gpu)
        if preset is None:
            raise ValueError(f"unknown gpu_type {gpu!r}")

        engine_preset: dict[str, Any] = preset.get(engine, {})
        result: dict[str, object] = {**defaults, **engine_preset}
        result["vram_tier"] = preset.get("vram_tier", "")
        result.update(kwargs)
        return result

    def validate_against_hardware(
        self, config: dict[str, object], hardware_profile: HardwareProfile
    ) -> None:
        tp_raw: object = config.get("tensor_parallel_size", 1)
        if isinstance(tp_raw, (int, float)):
            tp_i: int = int(tp_raw)
            if tp_i > 1 and not hardware_profile.has_nvlink:
                raise ValueError(
                    f"tensor_parallel_size={tp_i} requires NVLink, "
                    f"but {hardware_profile.gpu_type} does not support it"
                )

        quant: object = config.get("quantization")
        dtype: object = config.get("dtype")
        if (quant == "fp8" or dtype == "fp8") and not hardware_profile.supports_fp8:
            raise ValueError(
                f"fp8 quantization/dtype requires fp8 support, "
                f"but {hardware_profile.gpu_type} does not support it"
            )

        params_b_raw: object = config.get("params_b", 0)
        if isinstance(params_b_raw, (int, float)) and params_b_raw > 0:
            params_b: float = float(params_b_raw)
            quant_key: str = str(
                quant or ("fp8" if dtype == "fp8" else "bf16" if dtype == "bf16" else "fp16")
            )
            weight_gb: float = params_b * _QUANT_BYTES_PER_PARAM.get(
                quant_key.lower(), 2.0
            )
            util_raw: object = config.get("gpu_memory_utilization", 0.9)
            utilization: float = float(util_raw) if isinstance(util_raw, (int, float, str)) else 0.9
            if weight_gb > hardware_profile.total_vram_gb * utilization:
                raise ValueError(
                    f"model ({params_b}B) does not fit on {hardware_profile.gpu_count}x"
                    f"{hardware_profile.gpu_type} ({hardware_profile.total_vram_gb:.0f}GB)"
                )

    def recommend_quantization(self, params_b: float, vram_gb: float) -> str | None:
        for rule in self.quantization_recommendations:
            min_p: Any = rule.get("min_params_b")
            max_p: Any = rule.get("max_params_b")
            min_v: Any = rule.get("min_vram_gb")
            max_v: Any = rule.get("max_vram_gb")

            if min_p is not None and params_b < float(min_p):
                continue
            if max_p is not None and params_b > float(max_p):
                continue
            if min_v is not None and vram_gb < float(min_v):
                continue
            if max_v is not None and vram_gb > float(max_v):
                continue

            return str(rule["recommendation"])

        return None
