"""Daemon-backed embedding compatibility utilities for Ansible collections."""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

from ansible_collections.general_ludd.agent.plugins.module_utils.model_client import (
    ModelClient,
)


class Embedder(Protocol):
    """Minimal embedding interface used by the collection RAG adapter."""

    def embed(self, text: str) -> list[float]: ...


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Return bounded cosine similarity without importing core Python."""
    if len(a) != len(b):
        raise ValueError("vector length mismatch")
    if not a:
        return 0.0
    dot = sum(left * right for left, right in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(value * value for value in a))
    norm_b = math.sqrt(sum(value * value for value in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class DaemonEmbedder:
    """Compatibility embedder that delegates all vector creation to Gludd."""

    def __init__(
        self,
        dim: int | None = None,
        *,
        model_profile: str = "default",
        daemon_url: str = "http://localhost:8000",
        psk: str = "",
        timeout: int = 60,
        **_kwargs: object,
    ) -> None:
        self.dim = dim
        self._client = ModelClient(
            model_profile,
            daemon_url=daemon_url,
            psk=psk,
            timeout=timeout,
        )

    def embed(self, text: str) -> list[float]:
        """Return one vector or fail closed on a daemon transport error."""
        response = self._client.embed(text)
        vector = response.get("embedding")
        if not isinstance(vector, list):
            raise RuntimeError("daemon embedding response omitted embedding")
        result = [float(value) for value in vector]
        if self.dim is not None and result and len(result) != self.dim:
            raise RuntimeError(
                f"daemon embedding dimension {len(result)} does not match expected {self.dim}"
            )
        return result


class HashEmbedder:
    """Deterministic stdlib-only lexical fallback for unauthenticated use."""

    def __init__(self, dim: int = 384, **_kwargs: object) -> None:
        if dim < 1:
            raise ValueError("embedding dimension must be positive")
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        """Feature-hash bounded lexical tokens into a normalized vector."""
        normalized = " ".join(text.lower().split())[:8192]
        vector = [0.0] * self.dim
        if not normalized:
            return vector
        features = normalized.split()
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dim
            vector[bucket] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector


class OpenAIEmbedder(DaemonEmbedder):
    """Backward-compatible name; provider choice remains daemon-owned."""


class EmbeddingClient:
    """Ansible adapter for the authenticated daemon embedding endpoint."""

    def __init__(
        self,
        model_profile: str | None = None,
        *,
        use_openai_if_available: bool = False,
        timeout: int = 60,
        daemon_url: str = "http://localhost:8000",
        psk: str = "",
    ) -> None:
        del use_openai_if_available
        self._profile = model_profile or "default"
        self._timeout = timeout
        if psk:
            self._embedder: Embedder = DaemonEmbedder(
                model_profile=self._profile,
                daemon_url=daemon_url,
                psk=psk,
                timeout=timeout,
            )
        else:
            self._embedder = HashEmbedder()

    def embed_text(self, text: str) -> list[float]:
        return self._embedder.embed(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._embedder.embed(text) for text in texts]

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        return cosine_similarity(a, b)


class VectorStore:
    """Small in-memory vector index; vector generation stays daemon-owned."""

    def __init__(self) -> None:
        self._entries: dict[str, list[float]] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, item_id: str) -> bool:
        return item_id in self._entries

    def add(self, item_id: str, vector: list[float]) -> None:
        self._entries[item_id] = list(vector)

    def remove(self, item_id: str) -> None:
        self._entries.pop(item_id, None)

    def clear(self) -> None:
        self._entries.clear()

    def search(self, query: list[float], k: int = 5) -> list[tuple[str, float]]:
        scored = [
            (item_id, cosine_similarity(query, vector))
            for item_id, vector in self._entries.items()
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        limit = min(max(1, k), len(scored))
        return scored[:limit]

    def similarity(self, query: list[float], item_id: str) -> float:
        return cosine_similarity(query, self._entries[item_id])

    def get(self, item_id: str) -> list[float]:
        return list(self._entries[item_id])

    def list_ids(self) -> list[str]:
        return list(self._entries)


__all__ = (
    "DaemonEmbedder",
    "Embedder",
    "EmbeddingClient",
    "HashEmbedder",
    "OpenAIEmbedder",
    "VectorStore",
    "cosine_similarity",
)
