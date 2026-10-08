from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

Category = Literal["coding", "general"]


@dataclass(frozen=True)
class LocalModelConfig:
    name: str
    repo: str
    filename: str
    context_size: int = 2048
    huggingface_url: str = ""
    ollama_tag: str | None = None
    quant_level: str = "Q4_K_M"
    size_mb: int = 0
    category: Category = "general"
    ci_safe: bool = False
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.huggingface_url and self.repo:
            object.__setattr__(self, "huggingface_url", f"https://huggingface.co/{self.repo}")
        if not self.quant_level and self.filename:
            for level in ("Q8_0", "Q6_K", "Q5_K_M", "Q5_0", "Q4_K_M", "Q3_K_L", "Q3_K_M", "Q3_K_S", "Q2_K", "Q2_K_L"):
                if level in self.filename:
                    object.__setattr__(self, "quant_level", level)
                    break


_LOCAL_MODELS: list[LocalModelConfig] = [
    # ── coding-specialized (8 models) ──────────────────────────────────────
    LocalModelConfig(
        name="qwen2.5-coder-0.5b",
        repo="bartowski/Qwen2.5-Coder-0.5B-Instruct-GGUF",
        filename="Qwen2.5-Coder-0.5B-Instruct-Q4_K_M.gguf",
        context_size=32768,
        ollama_tag="qwen2.5-coder:0.5b",
        size_mb=312,
        category="coding",
        ci_safe=True,
        aliases=("qwen-coder-0.5b",),
    ),
    LocalModelConfig(
        name="deepseek-coder-1.3b",
        repo="TheBloke/deepseek-coder-1.3b-instruct-GGUF",
        filename="deepseek-coder-1.3b-instruct.Q4_K_M.gguf",
        context_size=16384,
        ollama_tag="deepseek-coder:1.3b",
        size_mb=792,
        category="coding",
        aliases=("deepseek-coder",),
    ),
    LocalModelConfig(
        name="qwen2.5-coder-1.5b",
        repo="bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF",
        filename="Qwen2.5-Coder-1.5B-Instruct-Q4_K_M.gguf",
        context_size=32768,
        ollama_tag="qwen2.5-coder:1.5b",
        size_mb=936,
        category="coding",
        aliases=("qwen-coder-1.5b",),
    ),
    LocalModelConfig(
        name="starcoder2-3b",
        repo="QuantFactory/starcoder2-3b-instruct-GGUF",
        filename="starcoder2-3b-instruct.Q4_K_M.gguf",
        context_size=16384,
        ollama_tag="starcoder2:3b",
        size_mb=1808,
        category="coding",
        aliases=("starcoder2",),
    ),
    LocalModelConfig(
        name="codellama-7b",
        repo="TheBloke/CodeLlama-7B-Instruct-GGUF",
        filename="codellama-7b-instruct.Q4_K_M.gguf",
        context_size=16384,
        ollama_tag="codellama:7b",
        size_mb=4084,
        category="coding",
        aliases=("codellama",),
    ),
    LocalModelConfig(
        name="qwen2.5-coder-3b",
        repo="bartowski/Qwen2.5-Coder-3B-Instruct-GGUF",
        filename="Qwen2.5-Coder-3B-Instruct-Q4_K_M.gguf",
        context_size=32768,
        ollama_tag="qwen2.5-coder:3b",
        size_mb=1892,
        category="coding",
        aliases=("qwen-coder-3b",),
    ),
    LocalModelConfig(
        name="phi-3-mini-4k",
        repo="bartowski/Phi-3-mini-4k-instruct-GGUF",
        filename="Phi-3-mini-4k-instruct-Q4_K_M.gguf",
        context_size=4096,
        ollama_tag="phi3:mini",
        size_mb=2172,
        category="coding",
        aliases=("phi-3-mini", "phi3-mini"),
    ),
    LocalModelConfig(
        name="smollm2-1.7b",
        repo="bartowski/SmolLM2-1.7B-Instruct-GGUF",
        filename="SmolLM2-1.7B-Instruct-Q4_K_M.gguf",
        context_size=8192,
        ollama_tag="smollm2:1.7b",
        size_mb=1064,
        category="coding",
        aliases=("smollm2-1.7b",),
    ),
    # ── general / reasoning (16 models) ────────────────────────────────────
    LocalModelConfig(
        name="qwen-0.5b",
        repo="bartowski/Qwen2.5-0.5B-Instruct-GGUF",
        filename="Qwen2.5-0.5B-Instruct-Q4_K_M.gguf",
        context_size=32768,
        ollama_tag="qwen2.5:0.5b",
        size_mb=316,
        category="general",
        ci_safe=True,
        aliases=("qwen-0.5b",),
    ),
    LocalModelConfig(
        name="smollm2-360m",
        repo="bartowski/SmolLM2-360M-Instruct-GGUF",
        filename="SmolLM2-360M-Instruct-Q4_K_M.gguf",
        context_size=8192,
        ollama_tag="smollm2:360m",
        size_mb=224,
        category="general",
        ci_safe=True,
        aliases=("smollm2-360m",),
    ),
    LocalModelConfig(
        name="smollm2-135m",
        repo="bartowski/SmolLM2-135M-Instruct-GGUF",
        filename="SmolLM2-135M-Instruct-Q4_K_M.gguf",
        context_size=8192,
        ollama_tag="smollm2:135m",
        size_mb=88,
        category="general",
        ci_safe=True,
        aliases=("smollm2-135m",),
    ),
    LocalModelConfig(
        name="tinyllama-1.1b",
        repo="TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF",
        filename="tinyllama-1.1b-chat-v1.0.Q3_K_M.gguf",
        context_size=2048,
        ollama_tag="tinyllama:latest",
        quant_level="Q3_K_M",
        size_mb=496,
        category="general",
        ci_safe=True,
        aliases=("tinyllama",),
    ),
    LocalModelConfig(
        name="llama-3.2-1b",
        repo="bartowski/Llama-3.2-1B-Instruct-GGUF",
        filename="Llama-3.2-1B-Instruct-Q4_K_M.gguf",
        context_size=131072,
        ollama_tag="llama3.2:1b",
        size_mb=712,
        category="general",
        aliases=("llama-1b",),
    ),
    LocalModelConfig(
        name="gemma-2-2b",
        repo="bartowski/gemma-2-2b-it-GGUF",
        filename="gemma-2-2b-it-Q4_K_M.gguf",
        context_size=8192,
        ollama_tag="gemma2:2b",
        size_mb=1380,
        category="general",
        aliases=("gemma-2b", "gemma2-2b"),
    ),
    LocalModelConfig(
        name="phi-2",
        repo="bartowski/phi-2-GGUF",
        filename="phi-2-Q2_K.gguf",
        context_size=2048,
        ollama_tag="phi:2.7b",
        quant_level="Q2_K",
        size_mb=487,
        category="general",
        ci_safe=True,
        aliases=("phi2",),
    ),
    LocalModelConfig(
        name="qwen2.5-1.5b",
        repo="bartowski/Qwen2.5-1.5B-Instruct-GGUF",
        filename="Qwen2.5-1.5B-Instruct-Q4_K_M.gguf",
        context_size=32768,
        ollama_tag="qwen2.5:1.5b",
        size_mb=940,
        category="general",
        aliases=("qwen-1.5b",),
    ),
    LocalModelConfig(
        name="llama-3.2-3b",
        repo="bartowski/Llama-3.2-3B-Instruct-GGUF",
        filename="Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        context_size=131072,
        ollama_tag="llama3.2:3b",
        size_mb=1964,
        category="general",
        aliases=("llama-3b",),
    ),
    LocalModelConfig(
        name="mistral-7b",
        repo="TheBloke/Mistral-7B-Instruct-v0.2-GGUF",
        filename="mistral-7b-instruct-v0.2.Q4_K_M.gguf",
        context_size=32768,
        ollama_tag="mistral:7b",
        size_mb=4368,
        category="general",
        aliases=("mistral",),
    ),
    LocalModelConfig(
        name="qwen2.5-3b",
        repo="bartowski/Qwen2.5-3B-Instruct-GGUF",
        filename="Qwen2.5-3B-Instruct-Q4_K_M.gguf",
        context_size=32768,
        ollama_tag="qwen2.5:3b",
        size_mb=1896,
        category="general",
        aliases=("qwen-3b",),
    ),
    LocalModelConfig(
        name="phi-3.5-mini",
        repo="bartowski/Phi-3.5-mini-instruct-GGUF",
        filename="Phi-3.5-mini-instruct-Q4_K_M.gguf",
        context_size=131072,
        ollama_tag="phi3.5:mini",
        size_mb=2176,
        category="general",
        aliases=("phi-3.5-mini",),
    ),
    LocalModelConfig(
        name="qwen2.5-7b",
        repo="bartowski/Qwen2.5-7B-Instruct-GGUF",
        filename="Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        context_size=131072,
        ollama_tag="qwen2.5:7b",
        size_mb=4372,
        category="general",
        aliases=("qwen-7b",),
    ),
    LocalModelConfig(
        name="olmoe-1b-7b",
        repo="allenai/OLMoE-1B-7B-0125-Instruct-GGUF",
        filename="olmoe-1b-7b-0125-instruct.Q4_K_M.gguf",
        context_size=32768,
        size_mb=1386,
        category="general",
        aliases=("olmoe",),
    ),
    LocalModelConfig(
        name="internlm3-8b",
        repo="bartowski/internlm3-8b-instruct-GGUF",
        filename="internlm3-8b-instruct-Q4_K_M.gguf",
        context_size=131072,
        ollama_tag="internlm3:8b",
        size_mb=4892,
        category="general",
        aliases=("internlm3",),
    ),
    LocalModelConfig(
        name="stablelm-3b",
        repo="bartowski/StableLM-3B-4E1T-Instruct-GGUF",
        filename="StableLM-3B-4E1T-Instruct-Q4_K_M.gguf",
        context_size=32768,
        ollama_tag="stablelm:3b",
        size_mb=1856,
        category="general",
        aliases=("stablelm",),
    ),
]


def get_e2e_models() -> list[LocalModelConfig]:
    filter_name = os.environ.get("E2E_LOCAL_MODEL")
    if filter_name:
        return [m for m in _LOCAL_MODELS if m.name == filter_name]
    return list(_LOCAL_MODELS)
