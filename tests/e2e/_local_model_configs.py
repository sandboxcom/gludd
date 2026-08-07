"""Local GGUF model configurations for E2E testing.

Maps model keys to (huggingface_repo, gguf_filename, approx_size_mb).
All models are <500MB GGUF for fast local testing.
"""

from __future__ import annotations

import os

from general_ludd.local_model._local_model_configs import LocalModelConfig

LOCAL_GGUF_MODELS: dict[str, tuple[str, str, int]] = {
    "SmolLM2-360M": (
        "bartowski/SmolLM2-360M-Instruct-GGUF",
        "SmolLM2-360M-Instruct-Q4_K_M.gguf",
        224,
    ),
    "TinyLlama-1.1B": (
        "TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF",
        "tinyllama-1.1b-chat-v1.0.Q3_K_M.gguf",
        496,
    ),
    "Phi-2": (
        "bartowski/phi-2-GGUF",
        "phi-2-Q2_K.gguf",
        487,
    ),
}


def get_e2e_configs() -> list[LocalModelConfig]:
    """Return LocalModelConfig entries for all LOCAL_GGUF_MODELS, filtered by E2E_LOCAL_MODEL."""
    configs = [LocalModelConfig(name=k, repo=r, filename=f) for k, (r, f, _) in LOCAL_GGUF_MODELS.items()]
    filter_name = os.environ.get("E2E_LOCAL_MODEL")
    if filter_name:
        return [c for c in configs if c.name == filter_name]
    return configs
