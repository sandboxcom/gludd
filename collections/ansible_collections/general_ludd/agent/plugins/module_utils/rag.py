"""
RAG (Retrieval-Augmented Generation) pipeline for general_ludd.agent module_utils.

-- classes --
:class:`RAGPipeline`  -- ties together embeddings + vector store + model
:class:`Chunker`       -- splits text into overlapping chunks
:class:`VectorStore`   -- in-memory vector index with cosine-similarity search

Usage in module_utils
---------------------
    from ansible_collections.general_ludd.agent.plugins.module_utils.rag import (
        RAGPipeline,
    )

    pipeline = RAGPipeline(model_client=client)
    pipeline.add_document("Chunking breaks long documents into pieces.", {"source": "docs"})
    answer = pipeline.query("What does chunking do?", top_k=3)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ansible_collections.general_ludd.agent.plugins.module_utils.embeddings import (
    Embedder,
    HashEmbedder,
    cosine_similarity,
)
from ansible_collections.general_ludd.agent.plugins.module_utils.model_client import (
    Message,
    ModelClient,
)

# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------


@dataclass
class Chunk:
    """A single text chunk with metadata."""

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    index: int = 0


class Chunker:
    """Split text into overlapping chunks.

    Parameters
    ----------
    chunk_size:
        Target size of each chunk in characters.
    chunk_overlap:
        Number of characters each chunk overlaps with the next.
    """

    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if chunk_overlap < 0:
            raise ValueError("chunk_overlap must be non-negative")
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be less than chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split(self, text: str, metadata: dict[str, Any] | None = None) -> list[Chunk]:
        """Split ``text`` into a list of :class:`Chunk` objects."""
        if not text:
            return []

        meta = metadata or {}
        step = self.chunk_size - self.chunk_overlap
        chunks: list[Chunk] = []

        for idx, start in enumerate(range(0, len(text), step)):
            end = min(start + self.chunk_size, len(text))
            chunk_text = text[start:end]
            chunks.append(Chunk(text=chunk_text, metadata=dict(meta), index=idx))

            if end >= len(text):
                break

        return chunks


# ---------------------------------------------------------------------------
# Vector store
# ---------------------------------------------------------------------------


@dataclass
class VectorEntry:
    """A single entry in the vector store."""

    chunk: Chunk
    vector: list[float]


class VectorStore:
    """In-memory vector index with cosine-similarity search.

    Stores :class:`VectorEntry` records and supports k-nearest-neighbor lookup
    via brute-force cosine similarity.
    """

    def __init__(self) -> None:
        self._entries: list[VectorEntry] = []

    @property
    def size(self) -> int:
        return len(self._entries)

    def add(self, entry: VectorEntry) -> None:
        self._entries.append(entry)

    def add_all(self, entries: list[VectorEntry]) -> None:
        self._entries.extend(entries)

    def search(self, query_vector: list[float], top_k: int = 5) -> list[VectorEntry]:
        """Return the ``top_k`` entries closest to ``query_vector`` by cosine similarity."""
        if not self._entries:
            return []

        scored: list[tuple[float, VectorEntry]] = []
        for entry in self._entries:
            sim = cosine_similarity(query_vector, entry.vector)
            scored.append((sim, entry))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [entry for _, entry in scored[:top_k]]


# ---------------------------------------------------------------------------
# RAG Pipeline
# ---------------------------------------------------------------------------


def _build_prompt(question: str, context_chunks: list[VectorEntry]) -> str:
    lines: list[str] = [
        "Answer the question using only the provided context.",
        "If the context does not contain enough information, say so.",
        "",
        "--- context ---",
    ]
    for i, entry in enumerate(context_chunks, start=1):
        meta_str = ", ".join(f"{k}={v}" for k, v in entry.chunk.metadata.items())
        lines.append(f"[{i}] {entry.chunk.text}")
        if meta_str:
            lines.append(f"    (source: {meta_str})")

    lines.append("---")
    lines.append(f"Question: {question}")
    lines.append("Answer:")
    return "\n".join(lines)


class RAGPipeline:
    """Retrieval-Augmented Generation pipeline.

    Ties together a :class:`Chunker`, an :class:`Embedder`, a
    :class:`VectorStore`, and a :class:`ModelClient` to:

    * **add_document** — chunk + embed + store a text document.
    * **query** — embed a question, retrieve top-k context chunks,
      construct a prompt, and ask the model.

    Parameters
    ----------
    model_client:
        Used to send the constructed prompt to the model.
    embedder:
        A pluggable embedder (defaults to :class:`HashEmbedder`(256)).
    chunk_size:
        Characters per chunk (default 1000).
    chunk_overlap:
        Overlap between chunks in characters (default 200).
    """

    def __init__(
        self,
        model_client: ModelClient,
        *,
        embedder: Embedder | None = None,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
    ) -> None:
        self._model = model_client
        self._embedder: Embedder = embedder or HashEmbedder(dim=256)
        self._chunker = Chunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        self._store = VectorStore()

    def add_document(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        """Chunk ``text``, embed each chunk, and store in the vector index.

        Returns the list of created :class:`Chunk` objects.
        """
        chunks = self._chunker.split(text, metadata=metadata)
        entries: list[VectorEntry] = []
        for chunk in chunks:
            vec = self._embedder.embed(chunk.text)
            entries.append(VectorEntry(chunk=chunk, vector=vec))

        self._store.add_all(entries)
        return chunks

    def query(
        self,
        question: str,
        *,
        top_k: int = 5,
    ) -> str:
        """Run a RAG query against stored documents.

        1. Embed ``question``.
        2. Search the vector store for the ``top_k`` closest chunks.
        3. Build a prompt with the chunks as context.
        4. Send to ``model_client`` and return the answer.
        """
        query_vec = self._embedder.embed(question)
        results = self._store.search(query_vec, top_k=top_k)
        prompt = _build_prompt(question, results)
        return self._model.chat(messages=[Message(role="user", content=prompt)])

    @property
    def stored_count(self) -> int:
        """Number of stored vector entries."""
        return self._store.size

    def clear(self) -> None:
        """Remove all stored entries."""
        self._store = VectorStore()
