"""Local model discovery and routing."""

from __future__ import annotations

from general_ludd.local_model._local_model_configs import (
    _LOCAL_MODELS,
    Category,
    LocalModelConfig,
    get_e2e_models,
)

__all__ = [
    "Category",
    "LocalModelConfig",
    "codellama_7b",
    "deepseek_coder_1_3b",
    "gemma_2_2b",
    "get_e2e_models",
    "get_model",
    "internlm3_8b",
    "list_models",
    "llama_3_2_1b",
    "llama_3_2_3b",
    "mistral_7b",
    "olmoe_1b_7b",
    "phi_2",
    "phi_3_5_mini",
    "phi_3_mini_4k",
    "qwen2_5_1_5b",
    "qwen2_5_3b",
    "qwen2_5_7b",
    "qwen2_5_coder_0_5b",
    "qwen2_5_coder_1_5b",
    "qwen2_5_coder_3b",
    "qwen_0_5b",
    "smollm2_1_7b",
    "smollm2_135m",
    "smollm2_360m",
    "stablelm_3b",
    "starcoder2_3b",
    "tinyllama_1_1b",
]

_MODEL_BY_NAME: dict[str, LocalModelConfig] = {m.name: m for m in _LOCAL_MODELS}


def _identifier_to_name(key: str) -> str:
    return key.replace("-", "_").replace(".", "_")


qwen2_5_coder_0_5b = _MODEL_BY_NAME["qwen2.5-coder-0.5b"]
deepseek_coder_1_3b = _MODEL_BY_NAME["deepseek-coder-1.3b"]
qwen2_5_coder_1_5b = _MODEL_BY_NAME["qwen2.5-coder-1.5b"]
starcoder2_3b = _MODEL_BY_NAME["starcoder2-3b"]
codellama_7b = _MODEL_BY_NAME["codellama-7b"]
qwen2_5_coder_3b = _MODEL_BY_NAME["qwen2.5-coder-3b"]
phi_3_mini_4k = _MODEL_BY_NAME["phi-3-mini-4k"]
smollm2_1_7b = _MODEL_BY_NAME["smollm2-1.7b"]
qwen_0_5b = _MODEL_BY_NAME["qwen-0.5b"]
smollm2_360m = _MODEL_BY_NAME["smollm2-360m"]
smollm2_135m = _MODEL_BY_NAME["smollm2-135m"]
tinyllama_1_1b = _MODEL_BY_NAME["tinyllama-1.1b"]
llama_3_2_1b = _MODEL_BY_NAME["llama-3.2-1b"]
gemma_2_2b = _MODEL_BY_NAME["gemma-2-2b"]
phi_2 = _MODEL_BY_NAME["phi-2"]
qwen2_5_1_5b = _MODEL_BY_NAME["qwen2.5-1.5b"]
llama_3_2_3b = _MODEL_BY_NAME["llama-3.2-3b"]
mistral_7b = _MODEL_BY_NAME["mistral-7b"]
qwen2_5_3b = _MODEL_BY_NAME["qwen2.5-3b"]
phi_3_5_mini = _MODEL_BY_NAME["phi-3.5-mini"]
qwen2_5_7b = _MODEL_BY_NAME["qwen2.5-7b"]
olmoe_1b_7b = _MODEL_BY_NAME["olmoe-1b-7b"]
internlm3_8b = _MODEL_BY_NAME["internlm3-8b"]
stablelm_3b = _MODEL_BY_NAME["stablelm-3b"]


def list_models(
    category: Category | None = None,
    ci_safe_only: bool = False,
) -> list[LocalModelConfig]:
    """List configured local models, optionally filtered by category or CI safety."""
    configs = list(_LOCAL_MODELS)
    if category is not None:
        configs = [c for c in configs if c.category == category]
    if ci_safe_only:
        configs = [c for c in configs if c.ci_safe]
    return configs


def get_model(model_id: str) -> LocalModelConfig | None:
    """Look up a local model config by id or identifier-derived name."""
    if model_id in _MODEL_BY_NAME:
        return _MODEL_BY_NAME[model_id]
    key = _identifier_to_name(model_id)
    if key in globals():
        val = globals().get(key)
        if isinstance(val, LocalModelConfig):
            return val
    for cfg in _LOCAL_MODELS:
        if model_id == cfg.ollama_tag:
            return cfg
        if model_id in cfg.aliases:
            return cfg
    return None
