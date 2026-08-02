"""Embedding utilities for the general_ludd.agent collection.

Thin Ansible-compatible wrapper.  All core logic — tokenizer, stemmer,
HashEmbedder, OpenAIEmbedder, cosine_similarity — delegates to
``src/general_ludd/skills/embeddings.py``.  This file only adapts the
interface for ansible module consumption; it contains ZERO algorithmic
reimplementations.

Exports
-------
* ``Embedder``, ``HashEmbedder``, ``OpenAIEmbedder``, ``cosine_similarity``
  — re-exported directly from ``general_ludd.skills.embeddings``.
* ``EmbeddingClient`` — thin ansible wrapper around ``HashEmbedder``.
* ``VectorStore`` — in-memory index delegating ``cosine_similarity`` to core.

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
"""

from __future__ import annotations

from general_ludd.skills.embeddings import (  # type: ignore[import]  # ansible runtime path
    Embedder,
    HashEmbedder,
    OpenAIEmbedder,
    cosine_similarity,
)

# ---------------------------------------------------------------------------
# Re-exports from core — no reimplementations
# ---------------------------------------------------------------------------

__all__ = (
    "Embedder",
    "EmbeddingClient",
    "HashEmbedder",
    "OpenAIEmbedder",
    "VectorStore",
    "cosine_similarity",
)

# ---------------------------------------------------------------------------
# EmbeddingClient — thin ansible wrapper
# ---------------------------------------------------------------------------


class EmbeddingClient:
    """Thin ansible adapter around the core embedder interface.

    Delegates all embedding work to :class:`HashEmbedder` (or
    :class:`OpenAIEmbedder` when an API key is available and
    ``use_openai_if_available=True``).  No ``ModelGateway`` dependency —
    the core embedder is stdlib-only.

    Parameters
    ----------
    model_profile:
        Hint for backend selection.  Any profile containing ``"openai"``
        is treated the same as ``use_openai_if_available=True``.
    use_openai_if_available:
        When ``True`` and ``OPENAI_API_KEY`` is set, use
        :class:`OpenAIEmbedder`; otherwise fall back to
        :class:`HashEmbedder`.
    timeout:
        Preserved for call-site compatibility (unused by the default
        embedders but reserved for future backends).
    """

    def __init__(
        self,
        model_profile: str | None = None,
        *,
        use_openai_if_available: bool = False,
        timeout: int = 60,
    ) -> None:
        if model_profile and "openai" in model_profile:
            use_openai_if_available = True
        self._profile = model_profile
        self._timeout = timeout

        import os

        if use_openai_if_available and os.environ.get("OPENAI_API_KEY"):
            try:
                self._embedder: Embedder = OpenAIEmbedder()
            except RuntimeError:
                self._embedder = HashEmbedder()
        else:
            self._embedder = HashEmbedder()

    # ------------------------------------------------------------------
    # delegate every embedding operation to the core embedder
    # ------------------------------------------------------------------

    def embed_text(self, text: str) -> list[float]:
        """Return an embedding vector for a single text string."""
        return self._embedder.embed(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return embedding vectors for multiple texts.

        Each element corresponds positionally to ``texts``.
        """
        if not texts:
            return []
        return [self._embedder.embed(t) for t in texts]

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """Cosine similarity — delegates to core."""
        return cosine_similarity(a, b)


# ---------------------------------------------------------------------------
# VectorStore — in-memory index delegating cosine_similarity to core
# ---------------------------------------------------------------------------


class VectorStore:
    """In-memory vector store for small-to-medium RAG workloads.

    Stores ``(id, vector)`` pairs and supports brute-force
    cosine-similarity search.  The similarity calculation delegates to
    the core :func:`cosine_similarity` — no reimplementation lives here.

    Not designed for datasets beyond ~100k vectors; for those, use a
    dedicated vector DB (pgvector, Qdrant, etc.).

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
        """Remove a vector entry.  No-op when *item_id* is absent."""
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
            scored.append((item_id, cosine_similarity(query, vec)))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[: max(1, min(k, len(scored)))]

    def similarity(
        self,
        query: list[float],
        item_id: str,
    ) -> float:
        """Return the cosine similarity between *query* and a stored vector.

        Raises ``KeyError`` when *item_id* is absent.
        """
        return cosine_similarity(query, self._entries[item_id])

    def get(self, item_id: str) -> list[float]:
        """Return the stored vector for *item_id*.

        Raises ``KeyError`` when *item_id* is absent.
        """
        return list(self._entries[item_id])

    def list_ids(self) -> list[str]:
        """Return all stored item ids."""
        return list(self._entries.keys())
