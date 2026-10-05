"""Comprehensive local GGUF model registry for E2E testing.

Categorised by capability: coding models (proven code generators) and general/reasoning
models (for planning, review, and chat phases).  Every entry carries an estimated file
size so CI runners can filter to <500 MB models, and a context size for prompt fitting.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from general_ludd.local_model._local_model_configs import LocalModelConfig

Category = Literal["coding", "general"]
Role = Literal["PLANNER", "CODER", "REVIEWER"]


@dataclass(frozen=True)
class E2EModelEntry:
    name: str
    repo: str
    filename: str
    size_mb: int
    category: Category
    context_size: int = 2048
    ci_safe: bool = True
    aliases: tuple[str, ...] = ()

    def to_local_model_config(self) -> LocalModelConfig:
        return LocalModelConfig(
            name=self.name,
            repo=self.repo,
            filename=self.filename,
            context_size=self.context_size,
            size_mb=self.size_mb,
            category=self.category,
            ci_safe=self.ci_safe,
            aliases=self.aliases,
        )


_MODELS: list[E2EModelEntry] = [
    E2EModelEntry(
        name="Qwen2.5-Coder-0.5B",
        repo="bartowski/Qwen2.5-Coder-0.5B-Instruct-GGUF",
        filename="Qwen2.5-Coder-0.5B-Instruct-Q4_K_M.gguf",
        size_mb=312,
        category="coding",
        context_size=32768,
        ci_safe=True,
        aliases=("qwen-coder-0.5b",),
    ),
    E2EModelEntry(
        name="DeepSeek-Coder-1.3B",
        repo="bartowski/DeepSeek-Coder-1.3B-Instruct-GGUF",
        filename="DeepSeek-Coder-1.3B-Instruct-Q4_K_M.gguf",
        size_mb=792,
        category="coding",
        context_size=16384,
        ci_safe=False,
        aliases=("deepseek-coder",),
    ),
    E2EModelEntry(
        name="Qwen2.5-Coder-1.5B",
        repo="bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF",
        filename="Qwen2.5-Coder-1.5B-Instruct-Q4_K_M.gguf",
        size_mb=936,
        category="coding",
        context_size=32768,
        ci_safe=False,
        aliases=("qwen-coder-1.5b",),
    ),
    E2EModelEntry(
        name="StarCoder2-3B",
        repo="bartowski/StarCoder2-3B-Instruct-GGUF",
        filename="StarCoder2-3B-Instruct-Q4_K_M.gguf",
        size_mb=1808,
        category="coding",
        context_size=16384,
        ci_safe=False,
        aliases=("starcoder2",),
    ),
    E2EModelEntry(
        name="CodeLlama-7B",
        repo="TheBloke/CodeLlama-7B-Instruct-GGUF",
        filename="codellama-7b-instruct.Q4_K_M.gguf",
        size_mb=4084,
        category="coding",
        context_size=16384,
        ci_safe=False,
        aliases=("codellama",),
    ),
    E2EModelEntry(
        name="Qwen2.5-Coder-3B",
        repo="bartowski/Qwen2.5-Coder-3B-Instruct-GGUF",
        filename="Qwen2.5-Coder-3B-Instruct-Q4_K_M.gguf",
        size_mb=1892,
        category="coding",
        context_size=32768,
        ci_safe=False,
        aliases=("qwen-coder-3b",),
    ),
    E2EModelEntry(
        name="Phi-3-mini-4k",
        repo="bartowski/Phi-3-mini-4k-instruct-GGUF",
        filename="Phi-3-mini-4k-instruct-Q4_K_M.gguf",
        size_mb=2172,
        category="coding",
        context_size=4096,
        ci_safe=False,
        aliases=("phi-3-mini", "phi3-mini"),
    ),
    E2EModelEntry(
        name="SmolLM2-1.7B",
        repo="bartowski/SmolLM2-1.7B-Instruct-GGUF",
        filename="SmolLM2-1.7B-Instruct-Q4_K_M.gguf",
        size_mb=1064,
        category="coding",
        context_size=8192,
        ci_safe=False,
        aliases=("smollm2-1.7b",),
    ),
    E2EModelEntry(
        name="Qwen2.5-0.5B",
        repo="bartowski/Qwen2.5-0.5B-Instruct-GGUF",
        filename="Qwen2.5-0.5B-Instruct-Q4_K_M.gguf",
        size_mb=316,
        category="general",
        context_size=32768,
        ci_safe=True,
        aliases=("qwen-0.5b", "Qwen2.5-0.5B-Instruct"),
    ),
    E2EModelEntry(
        name="SmolLM2-360M",
        repo="bartowski/SmolLM2-360M-Instruct-GGUF",
        filename="SmolLM2-360M-Instruct-Q4_K_M.gguf",
        size_mb=224,
        category="general",
        context_size=8192,
        ci_safe=True,
        aliases=("smollm2-360m",),
    ),
    E2EModelEntry(
        name="SmolLM2-135M",
        repo="bartowski/SmolLM2-135M-Instruct-GGUF",
        filename="SmolLM2-135M-Instruct-Q4_K_M.gguf",
        size_mb=88,
        category="general",
        context_size=8192,
        ci_safe=True,
        aliases=("smollm2-135m",),
    ),
    E2EModelEntry(
        name="TinyLlama-1.1B",
        repo="TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF",
        filename="tinyllama-1.1b-chat-v1.0.Q3_K_M.gguf",
        size_mb=496,
        category="general",
        context_size=2048,
        ci_safe=True,
        aliases=("tinyllama",),
    ),
    E2EModelEntry(
        name="Llama-3.2-1B",
        repo="bartowski/Llama-3.2-1B-Instruct-GGUF",
        filename="Llama-3.2-1B-Instruct-Q4_K_M.gguf",
        size_mb=712,
        category="general",
        context_size=131072,
        ci_safe=False,
        aliases=("llama-1b",),
    ),
    E2EModelEntry(
        name="Gemma-2-2B",
        repo="bartowski/gemma-2-2b-it-GGUF",
        filename="gemma-2-2b-it-Q4_K_M.gguf",
        size_mb=1380,
        category="general",
        context_size=8192,
        ci_safe=False,
        aliases=("gemma-2b", "gemma2-2b"),
    ),
    E2EModelEntry(
        name="Phi-2",
        repo="bartowski/phi-2-GGUF",
        filename="phi-2-Q2_K.gguf",
        size_mb=487,
        category="general",
        context_size=2048,
        ci_safe=True,
        aliases=("phi2",),
    ),
    E2EModelEntry(
        name="Qwen2.5-1.5B",
        repo="bartowski/Qwen2.5-1.5B-Instruct-GGUF",
        filename="Qwen2.5-1.5B-Instruct-Q4_K_M.gguf",
        size_mb=940,
        category="general",
        context_size=32768,
        ci_safe=False,
        aliases=("qwen-1.5b",),
    ),
    E2EModelEntry(
        name="Llama-3.2-3B",
        repo="bartowski/Llama-3.2-3B-Instruct-GGUF",
        filename="Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        size_mb=1964,
        category="general",
        context_size=131072,
        ci_safe=False,
        aliases=("llama-3b",),
    ),
    E2EModelEntry(
        name="Mistral-7B",
        repo="TheBloke/Mistral-7B-Instruct-v0.2-GGUF",
        filename="mistral-7b-instruct-v0.2.Q4_K_M.gguf",
        size_mb=4368,
        category="general",
        context_size=32768,
        ci_safe=False,
        aliases=("mistral",),
    ),
    E2EModelEntry(
        name="Qwen2.5-3B",
        repo="bartowski/Qwen2.5-3B-Instruct-GGUF",
        filename="Qwen2.5-3B-Instruct-Q4_K_M.gguf",
        size_mb=1896,
        category="general",
        context_size=32768,
        ci_safe=False,
        aliases=("qwen-3b",),
    ),
    E2EModelEntry(
        name="Phi-3.5-mini",
        repo="bartowski/Phi-3.5-mini-instruct-GGUF",
        filename="Phi-3.5-mini-instruct-Q4_K_M.gguf",
        size_mb=2176,
        category="general",
        context_size=131072,
        ci_safe=False,
        aliases=("phi-3.5-mini",),
    ),
    E2EModelEntry(
        name="Qwen2.5-7B",
        repo="bartowski/Qwen2.5-7B-Instruct-GGUF",
        filename="Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        size_mb=4372,
        category="general",
        context_size=131072,
        ci_safe=False,
        aliases=("qwen-7b",),
    ),
    E2EModelEntry(
        name="OLMoE-1B-7B",
        repo="allenai/OLMoE-1B-7B-0125-Instruct-GGUF",
        filename="olmoe-1b-7b-0125-instruct.Q4_K_M.gguf",
        size_mb=1386,
        category="general",
        context_size=32768,
        ci_safe=False,
        aliases=("olmoe",),
    ),
    E2EModelEntry(
        name="InternLM3-8B",
        repo="bartowski/internlm3-8b-instruct-GGUF",
        filename="internlm3-8b-instruct-Q4_K_M.gguf",
        size_mb=4892,
        category="general",
        context_size=131072,
        ci_safe=False,
        aliases=("internlm3",),
    ),
    E2EModelEntry(
        name="StableLM-3B",
        repo="bartowski/StableLM-3B-4E1T-Instruct-GGUF",
        filename="StableLM-3B-4E1T-Instruct-Q4_K_M.gguf",
        size_mb=1856,
        category="general",
        context_size=32768,
        ci_safe=False,
        aliases=("stablelm",),
    ),
]


_MODEL_BY_NAME: dict[str, E2EModelEntry] = {m.name: m for m in _MODELS}
_MODEL_BY_ALIAS: dict[str, E2EModelEntry] = {}
for m in _MODELS:
    _MODEL_BY_ALIAS[m.name.lower()] = m
    for a in m.aliases:
        _MODEL_BY_ALIAS[a.lower()] = m


def _resolve(name_or_alias: str) -> E2EModelEntry | None:
    return _MODEL_BY_NAME.get(name_or_alias) or _MODEL_BY_ALIAS.get(name_or_alias.lower())


def require_model(name_or_alias: str) -> E2EModelEntry:
    """Resolve one registry identity, failing closed when it is unknown."""
    resolved = _resolve(name_or_alias)
    if resolved is None:
        raise ValueError(f"Unknown local model: {name_or_alias}")
    return resolved


def _apply_filters(models: list[E2EModelEntry]) -> list[E2EModelEntry]:
    raw = os.environ.get("LOCAL_MODEL_FILTER", "").strip()
    if not raw:
        return models

    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    out = list(models)

    for p in parts:
        if p in ("coding", "general"):
            out = [m for m in out if m.category == p]
        elif p in ("ci-safe", "<500mb"):
            out = [m for m in out if m.ci_safe]
        else:
            resolved = _resolve(p)
            if resolved is not None:
                out = [m for m in out if m.name == resolved.name]

    return out


def list_models(
    category: Category | None = None,
    ci_safe: bool | None = None,
) -> list[E2EModelEntry]:
    models = list(_MODELS)
    if category is not None:
        models = [m for m in models if m.category == category]
    if ci_safe is not None:
        models = [m for m in models if m.ci_safe == ci_safe]
    return _apply_filters(models)


def select_models(*, ci_safe: bool, target: str = "") -> list[E2EModelEntry]:
    """Select a bounded pipeline model set and reject unknown identities."""
    models = list_models(ci_safe=True) if ci_safe else list_models()
    if not target:
        return models

    resolved = require_model(target)

    selected = [model for model in models if model.name == resolved.name]
    if not selected:
        raise ValueError(f"Local model is excluded by the active filters: {target}")
    return selected


def get_all_configs() -> list[LocalModelConfig]:
    return [m.to_local_model_config() for m in _apply_filters(list(_MODELS))]


def get_e2e_configs() -> list[LocalModelConfig]:
    configs = [m.to_local_model_config() for m in _MODELS]
    filter_name = os.environ.get("E2E_LOCAL_MODEL")
    if filter_name:
        configs = [c for c in configs if c.name == filter_name]
    return configs


def get_models_by_role() -> dict[str, list[E2EModelEntry]]:
    coders = [m for m in _MODELS if m.category == "coding"]
    reviewers = [m for m in _MODELS if m.category == "general" and m.context_size >= 8192]
    if not reviewers:
        reviewers = [m for m in _MODELS if m.category == "general" and m.size_mb >= 1000]
    planners = [m for m in _MODELS if m.category == "general" and m.name != "SmolLM2-135M"]
    if not planners:
        planners = [m for m in _MODELS if m.category == "general" and m.size_mb >= 500]
    return {"PLANNER": planners, "CODER": coders, "REVIEWER": reviewers}


def model_count() -> int:
    return len(_MODELS)


def category_counts() -> dict[str, int]:
    all_m = list(_MODELS)
    return {
        "total": len(all_m),
        "coding": sum(1 for m in all_m if m.category == "coding"),
        "general": sum(1 for m in all_m if m.category == "general"),
        "ci_safe": sum(1 for m in all_m if m.ci_safe),
    }


_ALIAS_MAP: dict[str, str] = {}
for m in _MODELS:
    for alias in m.aliases:
        _ALIAS_MAP[alias] = m.name


LOCAL_GGUF_MODELS: dict[str, tuple[str, str, int]] = {m.name: (m.repo, m.filename, m.size_mb) for m in _MODELS}
