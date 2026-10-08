#!/usr/bin/env python3
"""Run a bounded local sparse-model smoke test on Apple unified memory.

The command is intentionally local and credential-free.  ``--dry-run`` (the
default) only reports platform/backend capability information.  ``--live``
loads a small deterministic sparse PyTorch model, executes it on MPS/CUDA/CPU,
and emits latency, throughput, sparsity, and memory telemetry as JSON.  MPS is
selected automatically on Apple Silicon; the command fails closed when a
requested accelerator is unavailable instead of silently running on CPU.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import sys
import time
from typing import Any


class SmokeConfigError(ValueError):
    """Raised when a smoke-test bound is invalid."""


class SmokeCapabilityError(RuntimeError):
    """Raised when the requested local accelerator cannot run the test."""


class SmokeExecutionError(RuntimeError):
    """Raised when the model cannot execute on the selected device."""


class SmokeConfig:
    def __init__(
        self,
        backend: str,
        sparsity: float,
        hidden_size: int,
        batch_size: int,
        steps: int,
        max_memory_gb: float,
        model_parameters: int,
        headroom: float,
        allow_cpu: bool,
    ) -> None:
        self.backend = backend
        self.sparsity = sparsity
        self.hidden_size = hidden_size
        self.batch_size = batch_size
        self.steps = steps
        self.max_memory_gb = max_memory_gb
        self.model_parameters = model_parameters
        self.headroom = headroom
        self.allow_cpu = allow_cpu


def _load_torch() -> Any | None:
    """Import torch lazily so dry-run works on machines without PyTorch."""

    try:
        return importlib.import_module("torch")
    except ImportError:
        return None


def _float_env(env: dict[str, str], name: str, default: float) -> float:
    raw = env.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise SmokeConfigError(f"{name} must be numeric") from exc
    if not 0 < value < 1 and name == "GLUDD_SMOKE_SPARSITY":
        raise SmokeConfigError("sparsity must be greater than 0 and less than 1")
    if value <= 0:
        raise SmokeConfigError(f"{name} must be greater than zero")
    return value


def _int_env(env: dict[str, str], name: str, default: int, maximum: int) -> int:
    raw = env.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise SmokeConfigError(f"{name} must be an integer") from exc
    if value <= 0 or value > maximum:
        raise SmokeConfigError(f"{name} must be between 1 and {maximum}")
    return value


def _config(env: dict[str, str]) -> SmokeConfig:
    backend = env.get("GLUDD_SMOKE_BACKEND", "auto").strip().lower()
    if backend not in {"auto", "mps", "cuda", "cpu"}:
        raise SmokeConfigError("backend must be auto, mps, cuda, or cpu")
    hidden_size = _int_env(env, "GLUDD_SMOKE_HIDDEN_SIZE", 1024, 4096)
    return SmokeConfig(
        backend=backend,
        sparsity=_float_env(env, "GLUDD_SMOKE_SPARSITY", 0.8),
        hidden_size=hidden_size,
        batch_size=_int_env(env, "GLUDD_SMOKE_BATCH_SIZE", 4, 64),
        steps=_int_env(env, "GLUDD_SMOKE_STEPS", 10, 100),
        max_memory_gb=_float_env(env, "GLUDD_SMOKE_MAX_MEMORY_GB", 8.0),
        model_parameters=_int_env(env, "GLUDD_SMOKE_MODEL_PARAMS", 2 * hidden_size * hidden_size, 10**12),
        headroom=_float_env(env, "GLUDD_SMOKE_HEADROOM", 0.2),
        allow_cpu=env.get("GLUDD_SMOKE_ALLOW_CPU", "0").strip().lower() in {"1", "true", "yes"},
    )


def _system_memory_bytes() -> int | None:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, OSError, ValueError):
        return None
    return pages * page_size if pages > 0 and page_size > 0 else None


def _capabilities(torch_module: Any | None, requested: str) -> dict[str, Any]:
    mps = getattr(getattr(torch_module, "backends", None), "mps", None)
    cuda = getattr(torch_module, "cuda", None)
    mps_built = bool(mps and getattr(mps, "is_built", lambda: False)())
    mps_available = bool(mps and getattr(mps, "is_available", lambda: False)())
    cuda_available = bool(cuda and getattr(cuda, "is_available", lambda: False)())
    return {
        "platform": platform.system(),
        "machine": platform.machine(),
        "is_apple_silicon": platform.system() == "Darwin" and platform.machine() in {"arm64", "aarch64"},
        "is_container": os.path.exists("/.dockerenv") or bool(os.environ.get("GLUDD_CONTAINER_RUNTIME")),
        "backend_requested": requested,
        "mps_built": mps_built,
        "mps_available": mps_available,
        "cuda_available": cuda_available,
    }


def _memory_info(torch_module: Any | None, backend: str) -> dict[str, Any]:
    """Describe capacity semantics: MPS shares unified memory; CUDA/ROCm has VRAM."""

    try:
        policy = importlib.import_module("general_ludd.hardware_memory_policy")
        detected = policy.detect_memory(backend)
    except (ImportError, AttributeError, RuntimeError, TypeError):
        detected = None
    if detected is not None:
        kind = "discrete" if detected.kind == "vram" else detected.kind
        return {
            "kind": kind,
            "accelerator": detected.backend,
            "device": detected.device,
            "capacity_bytes": detected.total_bytes,
            "available_bytes": detected.available_bytes,
            "capacity_gb": detected.total_bytes / 1024**3,
            "model_guidance": policy.model_guidance(kind),
        }
    capacity = _system_memory_bytes()
    kind = "unified" if backend == "mps" else "discrete" if backend == "cuda" else "system"
    accelerator = "metal-mps" if backend == "mps" else "cuda-or-rocm" if backend == "cuda" else "cpu"
    if backend == "cuda" and torch_module is not None:
        try:
            capacity = int(torch_module.cuda.get_device_properties(0).total_memory)
            if getattr(getattr(torch_module, "version", None), "hip", None):
                accelerator = "rocm"
        except (AttributeError, RuntimeError, TypeError):
            capacity = None
    return {
        "kind": kind,
        "accelerator": accelerator,
        "capacity_bytes": capacity,
        "capacity_gb": capacity / 1024**3 if capacity is not None else None,
        "model_guidance": {
            "memory_kind": kind,
            "strategy": "capacity-first" if kind == "unified" else "fail-closed",
        },
    }


def _model_fit(config: SmokeConfig, memory: dict[str, Any]) -> dict[str, Any]:
    """Apply a conservative dense-storage fit policy before loading a model."""

    try:
        # Unit callers may provide only a capacity fixture; keep the legacy
        # max-memory bound for that shape. Live detection always supplies kind.
        if "kind" not in memory:
            raise AttributeError("memory kind unavailable")
        policy = importlib.import_module("general_ludd.hardware_memory_policy")
        capacity = int(memory.get("capacity_bytes") or 0)
        configured = int(config.max_memory_gb * 1024**3)
        info = policy.MemoryInfo(
            kind="vram" if memory.get("kind") == "discrete" else memory.get("kind", "unknown"),
            total_bytes=min(capacity, configured) if configured else capacity,
            available_bytes=(
                min(int(memory.get("available_bytes") or capacity), configured)
                if configured
                else int(memory.get("available_bytes") or capacity)
            ),
            backend=str(memory.get("accelerator", config.backend)),
            device=str(memory.get("device", "local")),
        )
        evaluated = policy.evaluate_model_fit(
            info,
            config.model_parameters,
            quantization_bits=32,
            reserve_ratio=config.headroom,
        )
    except (ImportError, AttributeError, RuntimeError, TypeError, ValueError):
        evaluated = None
    if evaluated is not None:
        return {
            "fits": bool(evaluated.fits),
            "model_parameters": config.model_parameters,
            "dense_storage_bytes_fp32": int(evaluated.required_bytes),
            "dense_storage_bytes_fp16": config.model_parameters * 2,
            "effective_budget_bytes": int(evaluated.reserved_bytes),
            "reserve_headroom": config.headroom,
            "recommendation": str(evaluated.reason),
        }
    requested_budget = int(config.max_memory_gb * 1024**3)
    capacity = memory.get("capacity_bytes")
    capacity_budget = int(capacity * (1 - config.headroom)) if capacity else requested_budget
    budget = min(requested_budget, capacity_budget)
    fp32_bytes = config.model_parameters * 4
    fp16_bytes = config.model_parameters * 2
    fits = fp32_bytes <= budget
    return {
        "fits": fits,
        "model_parameters": config.model_parameters,
        "dense_storage_bytes_fp32": fp32_bytes,
        "dense_storage_bytes_fp16": fp16_bytes,
        "effective_budget_bytes": budget,
        "reserve_headroom": config.headroom,
        "recommendation": (
            "run only models at or below the reported fp32 footprint"
            if fits
            else "choose a smaller or quantized sparse model; do not run this model"
        ),
    }


def _resolve_backend(config: SmokeConfig, capabilities: dict[str, Any]) -> str:
    requested = config.backend
    if requested == "mps":
        if not capabilities["mps_built"] or not capabilities["mps_available"]:
            raise SmokeCapabilityError("MPS/Metal is unavailable; install native arm64 PyTorch on macOS")
        return "mps"
    if requested == "cuda":
        if not capabilities["cuda_available"]:
            raise SmokeCapabilityError("CUDA is unavailable in the local PyTorch installation")
        return "cuda"
    if requested == "cpu":
        return "cpu"
    if capabilities["mps_available"]:
        return "mps"
    if capabilities["cuda_available"]:
        return "cuda"
    if config.allow_cpu:
        return "cpu"
    raise SmokeCapabilityError("no local accelerator available; pass --backend cpu or --allow-cpu explicitly")


def _memory_snapshot(torch_module: Any, backend: str) -> dict[str, int]:
    if backend == "mps":
        mps = getattr(torch_module, "mps", None)
        allocated = int(getattr(mps, "current_allocated_memory", lambda: 0)()) if mps else 0
        driver = int(getattr(mps, "driver_allocated_memory", lambda: allocated)()) if mps else allocated
        return {"allocated_bytes": allocated, "driver_bytes": driver}
    if backend == "cuda":
        return {
            "allocated_bytes": int(torch_module.cuda.memory_allocated()),
            "driver_bytes": int(torch_module.cuda.max_memory_allocated()),
        }
    return {"allocated_bytes": 0, "driver_bytes": 0}


def _apply_sparsity(model: Any, torch_module: Any, ratio: float) -> float:
    total = 0
    zeros = 0
    with torch_module.no_grad():
        for parameter in model.parameters():
            flat = parameter.view(-1)
            count = int(flat.numel())
            zero_count = int(count * ratio)
            if zero_count:
                flat[:zero_count] = 0
            total += count
            zeros += zero_count
    return zeros / total if total else 0.0


def _run_live(torch_module: Any, config: SmokeConfig, backend: str) -> dict[str, Any]:
    memory = _memory_info(torch_module, backend)
    fit = _model_fit(config, memory)
    if not fit["fits"]:
        raise SmokeCapabilityError("model does not fit within the memory budget")
    try:
        device = torch_module.device(backend)
        model = torch_module.nn.Sequential(
            torch_module.nn.Linear(config.hidden_size, config.hidden_size),
            torch_module.nn.ReLU(),
            torch_module.nn.Linear(config.hidden_size, config.hidden_size),
        )
        actual_sparsity = _apply_sparsity(model, torch_module, config.sparsity)
        model = model.to(device)
        inputs = torch_module.randn(config.batch_size, config.hidden_size, device=device)
        with torch_module.no_grad():
            for _ in range(2):
                model(inputs)
            synchronize = getattr(getattr(torch_module, "mps", None), "synchronize", None)
            if backend == "cuda":
                synchronize = torch_module.cuda.synchronize
            if synchronize:
                synchronize()
            started = time.perf_counter()
            for _ in range(config.steps):
                model(inputs)
            if synchronize:
                synchronize()
            elapsed = time.perf_counter() - started
    except Exception as exc:  # pragma: no cover - exercised on hardware
        raise SmokeExecutionError(f"sparse model execution failed on {backend}: {exc}") from exc
    latency_ms = elapsed * 1000 / config.steps
    return {
        "backend": backend,
        "latency_ms": latency_ms,
        "throughput_samples_per_second": config.batch_size * config.steps / elapsed,
        "memory": _memory_snapshot(torch_module, backend),
        "memory_policy": memory,
        "model_fit": fit,
        "sparsity": actual_sparsity,
        "steps": config.steps,
    }


def run_smoke(
    env: dict[str, str] | None = None,
    *,
    live: bool = False,
    backend: str | None = None,
    allow_cpu: bool = False,
) -> dict[str, Any]:
    values = dict(os.environ if env is None else env)
    if backend:
        values["GLUDD_SMOKE_BACKEND"] = backend
    if allow_cpu:
        values["GLUDD_SMOKE_ALLOW_CPU"] = "1"
    config = _config(values)
    torch_module = _load_torch() if live else None
    capabilities = _capabilities(torch_module, config.backend)
    preview_backend = config.backend
    if preview_backend == "auto" and platform.system() == "Darwin" and platform.machine() in {"arm64", "aarch64"}:
        preview_backend = "mps"
    memory = _memory_info(torch_module, preview_backend)
    result: dict[str, Any] = {
        "ok": True,
        "mode": "live" if live else "dry-run",
        "network": {"used": False},
        "capabilities": capabilities,
        "model": {
            "hidden_size": config.hidden_size,
            "batch_size": config.batch_size,
            "steps": config.steps,
            "sparsity": config.sparsity,
            "max_memory_gb": config.max_memory_gb,
            "model_parameters": config.model_parameters,
        },
        "memory_policy": memory,
        "model_fit": _model_fit(config, memory),
    }
    if not live:
        return result
    if torch_module is None:
        raise SmokeCapabilityError("PyTorch is required for --live local model execution")
    resolved = _resolve_backend(config, capabilities)
    result["telemetry"] = _run_live(torch_module, config, resolved)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="execute the local sparse model")
    mode.add_argument("--dry-run", action="store_true", help="report capabilities without loading a model")
    parser.add_argument("--backend", choices=("auto", "mps", "cuda", "cpu"), default=None)
    parser.add_argument("--allow-cpu", action="store_true", help="allow auto mode to fall back to CPU")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run_smoke(live=args.live, backend=args.backend, allow_cpu=args.allow_cpu), sort_keys=True))
    except SmokeConfigError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "kind": "config"}, sort_keys=True))
        return 2
    except SmokeCapabilityError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "kind": "capability"}, sort_keys=True))
        return 3
    except SmokeExecutionError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "kind": "execution"}, sort_keys=True))
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
