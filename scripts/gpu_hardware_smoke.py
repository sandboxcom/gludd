#!/usr/bin/env python3
"""Run a bounded, local GPU smoke test for CUDA or ROCm devices.

The harness never provisions cloud resources.  Dry-run mode is safe on every
machine and prints the checks that a live run would perform.  ``--live`` uses
PyTorch's local CUDA interface (which also exposes ROCm devices) to execute a
small sparse matrix inference workload, record device identity, and emit
latency, throughput, and memory telemetry.
"""

from __future__ import annotations

import argparse
import importlib
import json
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from general_ludd.hardware_memory_policy import (
        assess_model_fit,
        classify_memory_kind,
        memory_budget,
        recommend_models,
    )
except ModuleNotFoundError:  # direct checkout invocation before editable install
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from general_ludd.hardware_memory_policy import (
        assess_model_fit,
        classify_memory_kind,
        memory_budget,
        recommend_models,
    )


class HardwareSmokeError(RuntimeError):
    """Expected hardware/configuration failure with a stable CLI exit code."""

    def __init__(self, message: str, *, code: int = 3) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SmokeArgs:
    backend: str = "auto"
    size: int = 256
    iterations: int = 3
    sparsity: float = 0.90
    model_params_b: float = 0.0
    quant_bits: int = 4
    reserve_headroom: float = 0.20
    memory_kind: str = "auto"


def _validate_args(args: SmokeArgs) -> None:
    if args.backend not in {"auto", "cuda", "rocm"}:
        raise HardwareSmokeError("backend must be auto, cuda, or rocm", code=2)
    if not 32 <= args.size <= 4096:
        raise HardwareSmokeError("size must be between 32 and 4096", code=2)
    if not 1 <= args.iterations <= 20:
        raise HardwareSmokeError("iterations must be between 1 and 20", code=2)
    if not 0.50 <= args.sparsity < 1.0:
        raise HardwareSmokeError("sparsity must be at least 0.50 and below 1.0", code=2)
    if not 0.0 <= args.model_params_b <= 200.0:
        raise HardwareSmokeError("model_params_b must be between 0 and 200", code=2)
    if args.quant_bits not in {2, 4, 8, 16, 32}:
        raise HardwareSmokeError("quant_bits must be 2, 4, 8, 16, or 32", code=2)
    if not 0.05 <= args.reserve_headroom <= 0.50:
        raise HardwareSmokeError("reserve_headroom must be between 0.05 and 0.50", code=2)
    if args.memory_kind not in {"auto", "discrete", "unified"}:
        raise HardwareSmokeError("memory_kind must be auto, discrete, or unified", code=2)


def _command_output(command: list[str]) -> str | None:
    """Return a short diagnostic command result without invoking a shell."""
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = (completed.stdout or completed.stderr).strip()
    return output[:500] if output else None


def _host_diagnostics() -> dict[str, Any]:
    """Collect non-invasive host hints used to troubleshoot a failed run."""
    system = platform.system().lower()
    diagnostics: dict[str, Any] = {
        "os": system,
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    }
    if system == "windows":
        diagnostics["nvidia_smi"] = _command_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"]
        )
        diagnostics["rocminfo"] = _command_output(["rocminfo"])
    else:
        diagnostics["nvidia_smi"] = _command_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"]
        )
        diagnostics["rocminfo"] = _command_output(["rocminfo"])
    return diagnostics


def _torch_backend(torch_module: Any) -> str | None:
    if not bool(torch_module.cuda.is_available()):
        return None
    hip = getattr(getattr(torch_module, "version", None), "hip", None)
    return "rocm" if hip else "cuda"


def _memory_metric(torch_module: Any, name: str) -> int | None:
    value = getattr(torch_module.cuda, name, None)
    if value is None:
        return None
    try:
        return int(value())
    except (TypeError, RuntimeError):
        return None


def plan_smoke(args: SmokeArgs) -> dict[str, Any]:
    """Describe the bounded local workload without importing PyTorch."""
    _validate_args(args)
    dense_elements = args.size * args.size
    nonzero = max(1, int(dense_elements * (1.0 - args.sparsity)))
    return {
        "ok": True,
        "mode": "dry-run",
        "backend": args.backend,
        "workload": {
            "kind": "sparse-linear-inference",
            "size": args.size,
            "iterations": args.iterations,
            "sparsity": args.sparsity,
            "nonzero_elements": nonzero,
            "max_dense_bytes": dense_elements * 4,
        },
        "memory_policy": {
            "kind": None if args.memory_kind == "auto" else args.memory_kind,
            "model_fit": (
                assess_model_fit(
                    None,
                    args.model_params_b,
                    args.quant_bits,
                    kind="unknown" if args.memory_kind == "auto" else args.memory_kind,
                    reserve_fraction=args.reserve_headroom,
                ).as_dict()
                if args.model_params_b
                else None
            ),
            "recommendations": recommend_models(None, reserve_fraction=args.reserve_headroom),
            "note": "live mode is required to identify capacity and reject an oversized model",
        },
        "supported": {
            "linux": ["cuda", "rocm"],
            "windows": ["cuda", "rocm"],
            "hardware": ["NVIDIA CUDA", "AMD ROCm (including ASUS systems)"],
        },
        "host": _host_diagnostics(),
    }


def run_live(args: SmokeArgs, *, torch_module: Any | None = None) -> dict[str, Any]:
    """Execute sparse inference on the first locally visible GPU."""
    _validate_args(args)
    torch = torch_module
    if torch is None:
        try:
            torch = importlib.import_module("torch")
        except ImportError as exc:
            raise HardwareSmokeError(
                "PyTorch is required for --live; install a CUDA or ROCm build", code=3
            ) from exc

    backend = _torch_backend(torch)
    if backend is None:
        raise HardwareSmokeError("no CUDA/ROCm GPU is visible to PyTorch", code=3)
    if args.backend != "auto" and args.backend != backend:
        raise HardwareSmokeError(f"requested {args.backend} but detected {backend}", code=3)

    try:
        torch.cuda.set_device(0)
        device = torch.device("cuda")
        torch.manual_seed(0)
        nnz = max(1, int(args.size * args.size * (1.0 - args.sparsity)))
        rows = torch.randint(args.size, (nnz,), device=device)
        cols = torch.randint(args.size, (nnz,), device=device)
        values = torch.randn(nnz, device=device)
        sparse_weights = torch.sparse_coo_tensor(
            torch.stack((rows, cols)), values, (args.size, args.size), device=device
        ).coalesce()
        inputs = torch.randn((args.size, 1), device=device)

        for _ in range(1):
            torch.sparse.mm(sparse_weights, inputs)
        torch.cuda.synchronize()
        start = time.perf_counter()
        result = None
        for _ in range(args.iterations):
            result = torch.sparse.mm(sparse_weights, inputs)
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - start
    except (RuntimeError, ValueError, TypeError) as exc:
        raise HardwareSmokeError(f"GPU sparse inference failed: {exc}", code=4) from exc

    properties = torch.cuda.get_device_properties(0)
    total_memory = getattr(properties, "total_memory", None)
    device_name = str(torch.cuda.get_device_name(0))
    detected_kind = classify_memory_kind(
        backend,
        device_name,
        is_integrated=getattr(properties, "is_integrated", None),
    )
    if args.memory_kind != "auto":
        detected_kind = args.memory_kind
    budget = memory_budget(
        int(total_memory) if total_memory is not None else None,
        kind=detected_kind,
        reserve_fraction=args.reserve_headroom,
    )
    model_fit = (
        assess_model_fit(
            budget.total_bytes,
            args.model_params_b,
            args.quant_bits,
            kind=detected_kind,
            reserve_fraction=args.reserve_headroom,
        ).as_dict()
        if args.model_params_b
        else None
    )
    if model_fit is not None and model_fit["status"] == "reject":
        raise HardwareSmokeError(
            "requested model exceeds safe device memory budget; choose a smaller quantized model",
            code=3,
        )
    return {
        "ok": True,
        "mode": "live",
        "backend": backend,
        "device": {
            "index": 0,
            "name": device_name,
            "vendor": "AMD" if backend == "rocm" else "NVIDIA",
            "compute_capability": getattr(properties, "major", None),
            "total_memory_bytes": int(total_memory) if total_memory is not None else None,
            "memory_kind": detected_kind,
        },
        "memory_policy": {
            "budget": budget.as_dict(),
            "model_fit": model_fit,
            "recommendations": recommend_models(budget.total_bytes, reserve_fraction=args.reserve_headroom),
        },
        "workload": {
            "kind": "sparse-linear-inference",
            "size": args.size,
            "iterations": args.iterations,
            "sparsity": args.sparsity,
            "nonzero_elements": int(sparse_weights._nnz()),
            "output_elements": int(result.numel()) if result is not None else 0,
        },
        "telemetry": {
            "elapsed_seconds": elapsed,
            "mean_latency_ms": (elapsed * 1000.0) / args.iterations,
            "iterations_per_second": args.iterations / elapsed if elapsed else 0.0,
            "memory_allocated_bytes": _memory_metric(torch, "memory_allocated"),
            "memory_reserved_bytes": _memory_metric(torch, "memory_reserved"),
            "max_memory_allocated_bytes": _memory_metric(torch, "max_memory_allocated"),
        },
        "host": _host_diagnostics(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="run inference on a local GPU")
    parser.add_argument("--backend", choices=("auto", "cuda", "rocm"), default="auto")
    parser.add_argument("--size", type=int, default=256, help="square workload dimension (32-4096)")
    parser.add_argument("--iterations", type=int, default=3, help="timed iterations (1-20)")
    parser.add_argument("--sparsity", type=float, default=0.90, help="fraction of zero weights (0.50-<1.0)")
    parser.add_argument(
        "--model-params-b", type=float, default=0.0, help="optional model size in billions of parameters"
    )
    parser.add_argument("--quant-bits", type=int, default=4, choices=(2, 4, 8, 16, 32))
    parser.add_argument(
        "--reserve-headroom", type=float, default=0.20, help="fraction reserved for runtime (0.05-0.50)"
    )
    parser.add_argument("--memory-kind", choices=("auto", "discrete", "unified"), default="auto")
    parsed = parser.parse_args(argv)
    args = SmokeArgs(
        parsed.backend,
        parsed.size,
        parsed.iterations,
        parsed.sparsity,
        parsed.model_params_b,
        parsed.quant_bits,
        parsed.reserve_headroom,
        parsed.memory_kind,
    )
    try:
        result = run_live(args) if parsed.live else plan_smoke(args)
    except HardwareSmokeError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "code": exc.code}, sort_keys=True))
        return exc.code
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
