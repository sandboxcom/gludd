"""
Embedding and vector-store utilities for the general_ludd.agent collection.

Provides a model-agnostic embedding client and an in-memory VectorStore that
any Ansible collection or Python code can import for RAG (Retrieval-Augmented
Generation) workflows.

Usage (in a module or module_utils)::

    from ansible_collections.general_ludd.agent.plugins.module_utils.embeddings import (
        EmbeddingClient,
        VectorStore,
    )

    client = EmbeddingClient(model_profile="openai/text-embedding-3-small")
    vec_a = client.embed_text("What is Kubernetes?")
    vec_b = client.embed_text("Kubernetes is a container orchestrator.")
    score = client.cosine_similarity(vec_a, vec_b)

    store = VectorStore()
    store.add("doc-1", client.embed_text("document text here"))
    results = store.search(client.embed_text("query"), k=3)

Dependencies
------------
Only stdlib.  The ``EmbeddingClient`` constructor is lazy — it does not
instantiate a model gateway until ``embed_text`` / ``embed_batch`` is first
called.  This allows the module_utils to be imported even when the daemon is
not running in the same venv.
"""

from __future__ import annotations

import math
from typing import Any

# ---------------------------------------------------------------------------
# EmbeddingClient — lazy model gateway adapter
# ---------------------------------------------------------------------------


class EmbeddingClient:
    """Get vector embeddings from any supported embedding model.

    Parameters
    ----------
    model_profile:
        Model profile string understood by ``ModelGateway``, e.g.
        ``"openai/text-embedding-3-small"``, ``"local/bge-small"``.
    timeout:
        Seconds before a gateway call is abandoned (default 60).
    """

    def __init__(self, model_profile: str, timeout: int = 60) -> None:
        self._profile = model_profile
        self._timeout = timeout
        self._gateway: Any = None

    def _ensure_gateway(self) -> Any:
        if self._gateway is not None:
            return self._gateway
        try:
            from general_ludd.models.gateway import ModelGateway

            self._gateway = ModelGateway()
        except ImportError as exc:
            raise RuntimeError(
                "EmbeddingClient requires the general_ludd daemon in the same venv; "
                "set GLUDD_DAEMON_URL or run inside the daemon environment."
            ) from exc
        return self._gateway

    def embed_text(self, text: str) -> list[float]:
        """Return an embedding vector for a single text string.

        Returns a ``list[float]`` whose dimensionality depends on the
        configured model (e.g. 1536 for text-embedding-3-small).
        """
        results = self.embed_batch([text])
        return results[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return embedding vectors for multiple texts in one call.

        Each element of the returned list corresponds positionally to
        ``texts``.  The gateway is responsible for batching; this method
        does not impose its own chunking.
        """
        if not texts:
            return []
        gw = self._ensure_gateway()
        raw = gw.embed(texts=texts, model_profile=self._profile, timeout=self._timeout)
        if isinstance(raw, list) and all(isinstance(v, list) for v in raw):
            return raw
        raise RuntimeError(f"Unexpected embed response shape: {type(raw).__name__}")

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """Return the cosine similarity between two equal-length vectors.

        Range: ``[-1.0, 1.0]`` where 1.0 = identical direction.
        Returns 0.0 when either vector has zero magnitude.
        """
        if len(a) != len(b):
            raise ValueError(f"Vector dimension mismatch: {len(a)} vs {len(b)}")
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(y * y for y in b))
        if mag_a == 0.0 or mag_b == 0.0:
            return 0.0
        return dot / (mag_a * mag_b)


# ---------------------------------------------------------------------------
# VectorStore — simple in-memory vector index
# ---------------------------------------------------------------------------


class VectorStore:
    """In-memory vector store for small-to-medium RAG workloads.

    Stores ``(id, vector)`` pairs and supports brute-force cosine-similarity
    search.  Not designed for datasets beyond ~100k vectors — for those,
    use a dedicated vector DB (pgvector, Qdrant, etc.).

    Usage::

        store = VectorStore()
        store.add("doc-1", [0.1, 0.2, 0.3])
        store.add("doc-2", [0.4, 0.5, 0.6])
        results = store.search([0.15, 0.25, 0.35], k=2)
        # [("doc-1", 0.999...), ("doc-2", 0.998...)]
    """

    def __init__(self) -> None:
        self._entries: dict[str, list[float]] = {}

    # -- container protocol -------------------------------------------------

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, item_id: str) -> bool:
        return item_id in self._entries

    # -- mutations -----------------------------------------------------------

    def add(self, item_id: str, vector: list[float]) -> None:
        """Insert or update a vector entry."""
        self._entries[item_id] = list(vector)

    def remove(self, item_id: str) -> None:
        """Remove a vector entry.  No-op if the id is not present."""
        self._entries.pop(item_id, None)

    def clear(self) -> None:
        """Remove all entries."""
        self._entries.clear()

    # -- query ---------------------------------------------------------------

    def search(
        self,
        query: list[float],
        k: int = 5,
    ) -> list[tuple[str, float]]:
        """Return the top-*k* entries sorted by cosine similarity (descending).

        Parameters
        ----------
        query:
            Query embedding vector.
        k:
            Number of results to return.  Clamped to the store size.

        Returns
        -------
        list[tuple[str, float]]
            Each element is ``(item_id, similarity_score)``.
        """
        if not self._entries:
            return []
        scored: list[tuple[str, float]] = []
        for item_id, vec in self._entries.items():
            sim = EmbeddingClient.cosine_similarity(query, vec)
            scored.append((item_id, sim))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[: max(1, min(k, len(scored)))]

    def similarity(
        self,
        query: list[float],
        item_id: str,
    ) -> float:
        """Return the cosine similarity between ``query`` and a stored vector.

        Raises ``KeyError`` if ``item_id`` is not in the store.
        """
        vec = self._entries[item_id]
        return EmbeddingClient.cosine_similarity(query, vec)

    def get(self, item_id: str) -> list[float]:
        """Return the stored vector for ``item_id``.

        Raises ``KeyError`` if ``item_id`` is not present.
        """
        return list(self._entries[item_id])

    def list_ids(self) -> list[str]:
        """Return all stored item ids."""
        return list(self._entries.keys())
