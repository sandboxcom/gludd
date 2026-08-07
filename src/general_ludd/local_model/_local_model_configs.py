from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class LocalModelConfig:
    name: str
    repo: str
    filename: str
    context_size: int = 2048


_LOCAL_MODELS: list[LocalModelConfig] = [
    LocalModelConfig(
        name="qwen-0.5b",
        repo="bartowski/Qwen2.5-0.5B-Instruct-GGUF",
        filename="Qwen2.5-0.5B-Instruct-Q4_K_M.gguf",
    ),
    LocalModelConfig(
        name="tinyllama-1.1b",
        repo="bartowski/TinyLlama-1.1B-Chat-v1.0-GGUF",
        filename="TinyLlama-1.1B-Chat-v1.0-Q4_K_M.gguf",
    ),
    LocalModelConfig(
        name="smollm2-135m",
        repo="bartowski/SmolLM2-135M-Instruct-GGUF",
        filename="SmolLM2-135M-Instruct-Q4_K_M.gguf",
    ),
]


def get_e2e_models() -> list[LocalModelConfig]:
    filter_name = os.environ.get("E2E_LOCAL_MODEL")
    if filter_name:
        return [m for m in _LOCAL_MODELS if m.name == filter_name]
    return list(_LOCAL_MODELS)
