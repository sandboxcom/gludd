"""Reload-stable public contracts for model downloads."""

from enum import StrEnum


class DownloadSource(StrEnum):
    """Where a model file was fetched from."""

    HUGGINGFACE = "huggingface"
    GGUF = "gguf"
    OLLAMA = "ollama"
    CACHE = "cache"


__all__ = ["DownloadSource"]
