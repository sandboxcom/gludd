"""Local model quantization pipeline — GGUF conversion and llama.cpp quantization.

Tools expected on the path or configurable:
- ``convert_hf_to_gguf.py`` (from llama.cpp) — converts FP16 safetensors/checkpoints
  to FP16 GGUF format.
- ``llama-quantize`` (from llama.cpp) — quantizes an FP16 GGUF file to q4_0, q4_K_M,
  q8_0, etc.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from general_ludd.hardware_memory_policy import (
    MemoryInfo,
    detect_memory,
    estimate_model_bytes,
)

logger = logging.getLogger(__name__)


class QuantMethod(StrEnum):
    """Quantization methods supported by llama.cpp quantize."""

    Q4_0 = "q4_0"
    Q4_K_M = "q4_K_M"
    Q8_0 = "q8_0"
    FP16 = "f16"

    def bits(self) -> int:
        _BITS: dict[str, int] = {
            "q4_0": 4,
            "q4_K_M": 4,
            "q8_0": 8,
            "f16": 16,
        }
        return _BITS.get(self.value, 16)

    def quality_score(self) -> float:
        _SCORES: dict[str, float] = {
            "q4_0": 0.55,
            "q4_K_M": 0.65,
            "q8_0": 0.80,
            "f16": 1.0,
        }
        return _SCORES.get(self.value, 0.50)

    @classmethod
    def from_string(cls, raw: str) -> QuantMethod:
        normalized = raw.strip().lower()
        _MAP: dict[str, QuantMethod] = {
            "q4_0": cls.Q4_0,
            "q4_k_m": cls.Q4_K_M,
            "q4km": cls.Q4_K_M,
            "q8_0": cls.Q8_0,
            "f16": cls.FP16,
            "fp16": cls.FP16,
        }
        if normalized in _MAP:
            return _MAP[normalized]
        raise ValueError(f"Unknown quant method: {raw!r}")


@dataclass(frozen=True)
class HardwareCapacity:
    memory_kind: str
    total_memory_gb: float
    backend: str
    device: str

    @classmethod
    def from_probe(cls, backend: str | None = None) -> HardwareCapacity:
        mem: MemoryInfo = detect_memory(backend)
        gb = (mem.total_bytes or 0) / (1024**3)
        kind = "discrete" if mem.kind == "vram" else "unified" if mem.kind == "unified" else "unknown"
        return cls(
            memory_kind=kind,
            total_memory_gb=gb,
            backend=mem.backend,
            device=mem.device,
        )


@dataclass(frozen=True)
class QuantSelection:
    method: QuantMethod | None
    fits: bool
    reason: str
    bits: int

    def as_dict(self) -> dict[str, object]:
        return {
            "method": self.method.value if self.method else None,
            "fits": self.fits,
            "reason": self.reason,
            "bits": self.bits,
        }


_RESERVE_FRACTION: float = 0.20

_QUANT_METHODS_BY_QUALITY: list[QuantMethod] = [
    QuantMethod.Q8_0,
    QuantMethod.Q4_K_M,
    QuantMethod.Q4_0,
]


def select_quant_for_hardware(
    capacity: HardwareCapacity,
    params_b: float,
    *,
    reserve_fraction: float = _RESERVE_FRACTION,
) -> QuantSelection:
    if capacity.total_memory_gb <= 0 or capacity.memory_kind == "unknown":
        return QuantSelection(
            method=None,
            fits=False,
            reason="device memory capacity is unknown; cannot select quant",
            bits=0,
        )

    total_bytes = int(capacity.total_memory_gb * 1024**3)
    reserve_bytes = int(total_bytes * reserve_fraction)
    usable = total_bytes - reserve_bytes

    for candidate in _QUANT_METHODS_BY_QUALITY:
        footprint = estimate_model_bytes(params_b, candidate.bits())
        if footprint <= usable:
            return QuantSelection(
                method=candidate,
                fits=True,
                reason=(
                    f"model fits with {candidate.value} ({candidate.bits()}-bit) in {usable // (1024**2)} MiB usable"
                ),
                bits=candidate.bits(),
            )

    q4_footprint = estimate_model_bytes(params_b, QuantMethod.Q4_0.bits())
    return QuantSelection(
        method=QuantMethod.Q4_0,
        fits=False,
        reason=f"model needs ~{q4_footprint // (1024**2)} MiB even at 4-bit; usable={usable // (1024**2)} MiB",
        bits=4,
    )


class ModelQuantizer:
    _GGUF_CONVERT_SCRIPT: str = "convert_hf_to_gguf.py"
    _LLAMA_QUANTIZE_BIN: str = "llama-quantize"
    _QUANT_MAP: ClassVar[dict[QuantMethod, str]] = {
        QuantMethod.Q4_0: "q4_0",
        QuantMethod.Q4_K_M: "q4_K_M",
        QuantMethod.Q8_0: "q8_0",
    }

    def __init__(
        self,
        *,
        convert_script_path: str | None = None,
        llama_cpp_quantize_path: str | None = None,
    ) -> None:
        self.convert_script_path = convert_script_path or shutil.which(self._GGUF_CONVERT_SCRIPT)
        self.llama_cpp_quantize_path = llama_cpp_quantize_path or shutil.which(self._LLAMA_QUANTIZE_BIN)

    def available_methods(self) -> set[QuantMethod]:
        methods: set[QuantMethod] = {QuantMethod.FP16}
        if self._can_quantize_locally():
            methods.update(self._QUANT_MAP.keys())
        return methods

    def _can_quantize_locally(self) -> bool:
        return bool(self.llama_cpp_quantize_path)

    def convert_to_gguf(self, input_path: str, output_path: str) -> bool:
        if not input_path:
            raise ValueError("input_path must not be empty")
        if not os.path.isdir(input_path) and not os.path.isfile(input_path):
            logger.warning("Input path does not exist: %s", input_path)
            return False

        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        script = self.convert_script_path or self._GGUF_CONVERT_SCRIPT
        cmd = [
            "python3",
            script,
            input_path,
            "--outfile",
            output_path,
            "--outtype",
            "f16",
        ]

        logger.info("Converting %s to GGUF: %s", input_path, " ".join(cmd))

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                logger.error(
                    "GGUF conversion failed (exit %d): %s",
                    result.returncode,
                    result.stderr.strip() or result.stdout.strip(),
                )
                return False
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            logger.error("GGUF conversion error: %s", exc)
            return False

    def quantize(
        self,
        input_gguf: str,
        output_gguf: str,
        method: QuantMethod,
        *,
        threads: int | None = None,
    ) -> bool:
        if method == QuantMethod.FP16:
            raise ValueError("FP16 is not a llama.cpp quantize target — use convert_to_gguf instead")
        if not self._can_quantize_locally():
            logger.warning("llama-quantize not found; cannot quantize")
            return False

        if method not in self._QUANT_MAP:
            raise ValueError(f"Unsupported quant method for llama.cpp: {method}")

        out_dir = os.path.dirname(output_gguf)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        quant_type = self._QUANT_MAP[method]
        cmd = [
            self.llama_cpp_quantize_path or self._LLAMA_QUANTIZE_BIN,
            input_gguf,
            output_gguf,
            quant_type,
        ]
        if threads is not None and threads > 0:
            cmd.insert(1, str(threads))
            cmd.insert(1, "-t")

        logger.info("Quantizing %s -> %s (%s)", input_gguf, output_gguf, quant_type)

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if result.returncode != 0:
                logger.error(
                    "Quantization failed (exit %d): %s",
                    result.returncode,
                    result.stderr.strip() or result.stdout.strip(),
                )
                return False
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            logger.error("Quantization error: %s", exc)
            return False
