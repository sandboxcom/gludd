"""Model scoring — ranks models by cost, performance, and hardware fit."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from general_ludd.models.cost_router import CostAwareRouter

logger = logging.getLogger(__name__)

_LOCAL = "local"
_CLOUD = "cloud"


class _HardwareInfoProtocol(Protocol):
    def gpu_count(self) -> int: ...

    def vram_gb_per_gpu(self) -> float: ...

    def system_ram_gb(self) -> float: ...


@dataclass(frozen=True)
class BudgetProfile:
    max_cost_usd: float
    prefer_local: bool = False
    max_latency_ms: float | None = None


@dataclass(frozen=True)
class ModelScore:
    model_id: str
    score: float
    cost_estimate: float
    latency_estimate: float
    source: str

    def __post_init__(self) -> None:
        if self.source not in (_LOCAL, _CLOUD):
            raise ValueError(f"source must be '{_LOCAL}' or '{_CLOUD}', got {self.source!r}")
        if self.cost_estimate < 0:
            raise ValueError(f"cost_estimate must be >= 0, got {self.cost_estimate}")
        if self.latency_estimate < 0:
            raise ValueError(f"latency_estimate must be >= 0, got {self.latency_estimate}")


# ---------- local-model config helpers ----------


def _local_model_base_url() -> str:
    return os.environ.get("LOCAL_MODEL_BASE_URL", "http://localhost:11434/v1")


def _local_model_available() -> bool:
    return os.environ.get("GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS") == "1"


def _is_local_model(model_id: str) -> bool:
    if not _local_model_available():
        return False
    base = _local_model_base_url()
    return base.startswith("http://localhost") or base.startswith("http://127.")


def _source_for(model_id: str) -> str:
    return _LOCAL if _is_local_model(model_id) else _CLOUD


# ---------- hardware profile ----------


def _detect_hardware() -> _HardwareInfoProtocol:
    class _DetectedHardware:
        def gpu_count(self) -> int:
            try:
                import subprocess

                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0 and result.stdout.strip():
                    return len(result.stdout.strip().splitlines())
            except Exception:
                pass
            return 0

        def vram_gb_per_gpu(self) -> float:
            try:
                import subprocess

                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0 and result.stdout.strip():
                    first = float(result.stdout.strip().splitlines()[0])
                    return first / 1024.0
            except Exception:
                pass
            return 0.0

        def system_ram_gb(self) -> float:
            try:
                import subprocess

                result = subprocess.run(
                    ["sysctl", "-n", "hw.memsize"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0 and result.stdout.strip():
                    return float(result.stdout.strip()) / (1024**3)
            except Exception:
                pass
            return 0.0

    return _DetectedHardware()


# ---------- model capability database ----------

_MODEL_CAPABILITIES: dict[str, dict[str, dict[str, float | int]]] = {
    "bug_fix": {
        "openai/gpt-4o": {"success": 0.92, "latency_ms": 1200, "cost_usd_per_1k": 0.005},
        "openai/gpt-4o-mini": {"success": 0.85, "latency_ms": 600, "cost_usd_per_1k": 0.0006},
        "openai/o1": {"success": 0.94, "latency_ms": 3000, "cost_usd_per_1k": 0.015},
        "openai/o3-mini": {"success": 0.88, "latency_ms": 400, "cost_usd_per_1k": 0.00055},
        "anthropic/claude-4": {"success": 0.93, "latency_ms": 1800, "cost_usd_per_1k": 0.015},
        "anthropic/claude-haiku": {"success": 0.82, "latency_ms": 500, "cost_usd_per_1k": 0.00125},
        "deepseek/deepseek-chat": {"success": 0.87, "latency_ms": 900, "cost_usd_per_1k": 0.00027},
        "deepseek/deepseek-v4": {"success": 0.95, "latency_ms": 1600, "cost_usd_per_1k": 0.004},
        "google/gemini-2.5-flash": {"success": 0.84, "latency_ms": 450, "cost_usd_per_1k": 0.0003},
        "google/gemini-2.5-pro": {"success": 0.91, "latency_ms": 1100, "cost_usd_per_1k": 0.0035},
        "meta/llama-4-maverick": {"success": 0.83, "latency_ms": 700, "cost_usd_per_1k": 0.0008},
        "groq/llama-4": {"success": 0.86, "latency_ms": 350, "cost_usd_per_1k": 0.0004},
        "qwen/qwen3-coder": {"success": 0.89, "latency_ms": 800, "cost_usd_per_1k": 0.0005},
    },
    "feature": {
        "openai/gpt-4o": {"success": 0.91, "latency_ms": 1500, "cost_usd_per_1k": 0.005},
        "openai/gpt-4o-mini": {"success": 0.80, "latency_ms": 700, "cost_usd_per_1k": 0.0006},
        "openai/o1": {"success": 0.93, "latency_ms": 4000, "cost_usd_per_1k": 0.015},
        "openai/o3-mini": {"success": 0.85, "latency_ms": 500, "cost_usd_per_1k": 0.00055},
        "anthropic/claude-4": {"success": 0.94, "latency_ms": 2200, "cost_usd_per_1k": 0.015},
        "anthropic/claude-haiku": {"success": 0.78, "latency_ms": 600, "cost_usd_per_1k": 0.00125},
        "deepseek/deepseek-chat": {"success": 0.82, "latency_ms": 1000, "cost_usd_per_1k": 0.00027},
        "deepseek/deepseek-v4": {"success": 0.92, "latency_ms": 1800, "cost_usd_per_1k": 0.004},
        "google/gemini-2.5-flash": {"success": 0.79, "latency_ms": 500, "cost_usd_per_1k": 0.0003},
        "google/gemini-2.5-pro": {"success": 0.88, "latency_ms": 1300, "cost_usd_per_1k": 0.0035},
        "meta/llama-4-maverick": {"success": 0.77, "latency_ms": 800, "cost_usd_per_1k": 0.0008},
        "groq/llama-4": {"success": 0.81, "latency_ms": 400, "cost_usd_per_1k": 0.0004},
        "qwen/qwen3-coder": {"success": 0.84, "latency_ms": 900, "cost_usd_per_1k": 0.0005},
    },
    "review": {
        "openai/gpt-4o": {"success": 0.93, "latency_ms": 1000, "cost_usd_per_1k": 0.005},
        "openai/gpt-4o-mini": {"success": 0.86, "latency_ms": 500, "cost_usd_per_1k": 0.0006},
        "openai/o1": {"success": 0.95, "latency_ms": 2500, "cost_usd_per_1k": 0.015},
        "openai/o3-mini": {"success": 0.89, "latency_ms": 400, "cost_usd_per_1k": 0.00055},
        "anthropic/claude-4": {"success": 0.94, "latency_ms": 1500, "cost_usd_per_1k": 0.015},
        "anthropic/claude-haiku": {"success": 0.83, "latency_ms": 450, "cost_usd_per_1k": 0.00125},
        "deepseek/deepseek-chat": {"success": 0.87, "latency_ms": 850, "cost_usd_per_1k": 0.00027},
        "deepseek/deepseek-v4": {"success": 0.91, "latency_ms": 1400, "cost_usd_per_1k": 0.004},
        "google/gemini-2.5-flash": {"success": 0.85, "latency_ms": 400, "cost_usd_per_1k": 0.0003},
        "google/gemini-2.5-pro": {"success": 0.90, "latency_ms": 1000, "cost_usd_per_1k": 0.0035},
        "meta/llama-4-maverick": {"success": 0.82, "latency_ms": 650, "cost_usd_per_1k": 0.0008},
        "groq/llama-4": {"success": 0.84, "latency_ms": 300, "cost_usd_per_1k": 0.0004},
        "qwen/qwen3-coder": {"success": 0.88, "latency_ms": 750, "cost_usd_per_1k": 0.0005},
    },
    "chat": {
        "openai/gpt-4o": {"success": 0.95, "latency_ms": 1000, "cost_usd_per_1k": 0.005},
        "openai/gpt-4o-mini": {"success": 0.90, "latency_ms": 500, "cost_usd_per_1k": 0.0006},
        "openai/o1": {"success": 0.96, "latency_ms": 2500, "cost_usd_per_1k": 0.015},
        "openai/o3-mini": {"success": 0.92, "latency_ms": 400, "cost_usd_per_1k": 0.00055},
        "anthropic/claude-4": {"success": 0.96, "latency_ms": 1500, "cost_usd_per_1k": 0.015},
        "anthropic/claude-haiku": {"success": 0.87, "latency_ms": 450, "cost_usd_per_1k": 0.00125},
        "deepseek/deepseek-chat": {"success": 0.89, "latency_ms": 800, "cost_usd_per_1k": 0.00027},
        "deepseek/deepseek-v4": {"success": 0.94, "latency_ms": 1400, "cost_usd_per_1k": 0.004},
        "google/gemini-2.5-flash": {"success": 0.88, "latency_ms": 400, "cost_usd_per_1k": 0.0003},
        "google/gemini-2.5-pro": {"success": 0.93, "latency_ms": 1000, "cost_usd_per_1k": 0.0035},
        "meta/llama-4-maverick": {"success": 0.85, "latency_ms": 600, "cost_usd_per_1k": 0.0008},
        "groq/llama-4": {"success": 0.87, "latency_ms": 300, "cost_usd_per_1k": 0.0004},
        "qwen/qwen3-coder": {"success": 0.91, "latency_ms": 700, "cost_usd_per_1k": 0.0005},
    },
    "generate": {
        "openai/gpt-4o": {"success": 0.90, "latency_ms": 2000, "cost_usd_per_1k": 0.005},
        "openai/gpt-4o-mini": {"success": 0.78, "latency_ms": 800, "cost_usd_per_1k": 0.0006},
        "openai/o1": {"success": 0.92, "latency_ms": 5000, "cost_usd_per_1k": 0.015},
        "openai/o3-mini": {"success": 0.82, "latency_ms": 600, "cost_usd_per_1k": 0.00055},
        "anthropic/claude-4": {"success": 0.93, "latency_ms": 3000, "cost_usd_per_1k": 0.015},
        "anthropic/claude-haiku": {"success": 0.75, "latency_ms": 700, "cost_usd_per_1k": 0.00125},
        "deepseek/deepseek-chat": {"success": 0.80, "latency_ms": 1200, "cost_usd_per_1k": 0.00027},
        "deepseek/deepseek-v4": {"success": 0.91, "latency_ms": 2200, "cost_usd_per_1k": 0.004},
        "google/gemini-2.5-flash": {"success": 0.77, "latency_ms": 600, "cost_usd_per_1k": 0.0003},
        "google/gemini-2.5-pro": {"success": 0.87, "latency_ms": 1500, "cost_usd_per_1k": 0.0035},
        "meta/llama-4-maverick": {"success": 0.76, "latency_ms": 1000, "cost_usd_per_1k": 0.0008},
        "groq/llama-4": {"success": 0.79, "latency_ms": 500, "cost_usd_per_1k": 0.0004},
        "qwen/qwen3-coder": {"success": 0.83, "latency_ms": 1100, "cost_usd_per_1k": 0.0005},
    },
}

_DEFAULT_CAPABILITY: dict[str, float] = {"success": 0.80, "latency_ms": 1000.0, "cost_usd_per_1k": 0.005}


# ---------- scoring ----------


def _get_capabilities(task_type: str, model_id: str) -> dict[str, float]:
    task_models = _MODEL_CAPABILITIES.get(task_type, {})
    caps = task_models.get(model_id)
    if caps is None:
        return dict(_DEFAULT_CAPABILITY)
    return dict(caps)


def _compute_score(
    task_caps: dict[str, float],
    budget: BudgetProfile,
    cost_multiplier: float = 1.0,
) -> float:
    success = float(task_caps.get("success", 0.80))
    latency_ms = float(task_caps.get("latency_ms", 1000))
    base_cost = float(task_caps.get("cost_usd_per_1k", 0.005))
    adj_cost = base_cost * cost_multiplier

    score = success * 60.0

    if adj_cost > 0:
        score += (1.0 / (adj_cost * 1000)) * 20.0
    else:
        score += 20.0

    if latency_ms > 0:
        score += (1.0 / (latency_ms / 1000)) * 20.0
    else:
        score += 20.0

    if budget.prefer_local:
        score += 5.0

    if budget.max_latency_ms is not None and latency_ms > budget.max_latency_ms:
        score *= 0.3

    if adj_cost > budget.max_cost_usd:
        score *= 0.4

    return round(score, 2)


def score_model(
    model_id: str,
    task_type: str,
    budget_profile: BudgetProfile,
    *,
    cost_router: CostAwareRouter | None = None,
    hardware: _HardwareInfoProtocol | None = None,
    now: object | None = None,
) -> ModelScore:
    task_caps = _get_capabilities(task_type, model_id)
    cost_multiplier = 1.0

    if cost_router is not None:
        import datetime as _dt

        cost_multiplier = cost_router._multiplier(
            now if isinstance(now, _dt.datetime) else None
        )

    base_cost = float(task_caps.get("cost_usd_per_1k", 0.005))
    cost_estimate = round(base_cost * cost_multiplier * 10, 6)
    latency_estimate = float(task_caps.get("latency_ms", 1000))

    if hardware is not None and _local_model_available():
        gpu_count = hardware.gpu_count()
        vram = hardware.vram_gb_per_gpu()
        if gpu_count > 0 and vram >= 4.0:
            latency_estimate *= 0.6

    score = _compute_score(task_caps, budget_profile, cost_multiplier)
    source = _source_for(model_id)

    return ModelScore(
        model_id=model_id,
        score=score,
        cost_estimate=cost_estimate,
        latency_estimate=round(latency_estimate, 2),
        source=source,
    )


def rank_models(
    task_type: str,
    budget_profile: BudgetProfile,
    *,
    cost_router: CostAwareRouter | None = None,
    hardware: _HardwareInfoProtocol | None = None,
    now: object | None = None,
) -> list[ModelScore]:
    task_models = _MODEL_CAPABILITIES.get(task_type, {})
    if not task_models:
        return []

    scores: list[ModelScore] = []
    for model_id in task_models:
        scores.append(
            score_model(
                model_id=model_id,
                task_type=task_type,
                budget_profile=budget_profile,
                cost_router=cost_router,
                hardware=hardware,
                now=now,
            )
        )

    scores.sort(key=lambda s: s.score, reverse=True)
    return scores


def best_model(
    task_type: str,
    budget_profile: BudgetProfile,
    *,
    cost_router: CostAwareRouter | None = None,
    hardware: _HardwareInfoProtocol | None = None,
    now: object | None = None,
) -> ModelScore | None:
    ranked = rank_models(
        task_type=task_type,
        budget_profile=budget_profile,
        cost_router=cost_router,
        hardware=hardware,
        now=now,
    )
    return ranked[0] if ranked else None
