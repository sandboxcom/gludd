"""
RAG pipeline — thin Ansible wrapper delegating to core modules.

Embeddings  → authenticated daemon embedding endpoint
LLM calls   → authenticated daemon model endpoint
Store       → in-memory VectorStore following general_ludd.memory.embedding_store
               pattern (dict-backed, cosine-similarity search)
Doc loading → document_loader (Document, DocumentLoader)

Only Chunker stays local — simple text processing, no core equivalent.

Usage::

    from ansible_collections.general_ludd.agent.plugins.module_utils.rag import (
        RAGPipeline,
    )

    pipeline = RAGPipeline(model_client=client)
    pipeline.add_document("Chunking breaks long documents.", {"source": "docs"})
    pipeline.add_document_file("/path/to/file.md")
    answer = pipeline.query("What does chunking do?", top_k=3)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ansible_collections.general_ludd.agent.plugins.module_utils.embeddings import (
    HashEmbedder,
    cosine_similarity,
)
from ansible_collections.general_ludd.agent.plugins.module_utils.model_client import (
    ModelClient,
)

if TYPE_CHECKING:
    from ansible_collections.general_ludd.agent.plugins.module_utils.document_loader import (
        Document,
    )

# ---------------------------------------------------------------------------
# Chunker — stays local (no core equivalent)
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
# Vector store — follows MemoryEmbeddingStore pattern
# ---------------------------------------------------------------------------


@dataclass
class VectorEntry:
    """A single entry in the vector store."""

    chunk: Chunk
    vector: list[float]


class VectorStore:
    """In-memory vector index following MemoryEmbeddingStore pattern.

    Stores :class:`VectorEntry` records and supports k-nearest-neighbor
    lookup via brute-force cosine similarity.
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
# Prompt builder
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


# ---------------------------------------------------------------------------
# RAG Pipeline
# ---------------------------------------------------------------------------


class RAGPipeline:
    """Retrieval-Augmented Generation pipeline.

    Ties together a :class:`Chunker`, a
    :class:`general_ludd.skills.embeddings.HashEmbedder`, a :class:`VectorStore`,
    and a model backend.

    Delegation:
        * **add_document** — chunk + :meth:`HashEmbedder.embed` + :meth:`VectorStore.add`.
        * **query** — :meth:`HashEmbedder.embed` question, :meth:`VectorStore.search`
          for top-k context chunks, :func:`_build_prompt`, then either
          ``model_client.chat`` (backward-compat) or
          :class:`general_ludd.models.gateway.ModelGateway.call_model_with_retry`.

    Parameters
    ----------
    model_client:
        Optional HTTP model client (backward-compat). When absent, delegates
        to the shared daemon model service.
    embedder:
        A pluggable embedder (defaults to ``HashEmbedder(dim=256)``).
    chunk_size:
        Characters per chunk (default 1000).
    chunk_overlap:
        Overlap between chunks in characters (default 200).
    """

    def __init__(
        self,
        model_client: Any = None,
        *,
        embedder: Any = None,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        daemon_url: str = "http://localhost:8000",
        psk: str = "",
    ) -> None:
        self._model = model_client or ModelClient(daemon_url=daemon_url, psk=psk)
        self._embedder = embedder if embedder is not None else HashEmbedder(
            dim=256,
            daemon_url=daemon_url,
            psk=psk,
        )
        self._chunker = Chunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        self._store = VectorStore()

    def add_document(
        self,
        content: str | Document,
        metadata: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        """Chunk ``content``, embed each chunk, and store in the vector index.

        ``content`` may be a plain ``str`` or a ``Document`` instance. When a
        ``Document`` is passed its ``.content`` is used and its ``.metadata`` is
        merged under any explicit ``metadata`` dict.

        Returns the list of created :class:`Chunk` objects.
        """
        from ansible_collections.general_ludd.agent.plugins.module_utils.document_loader import (
            Document,
        )

        if isinstance(content, Document):
            doc_meta = dict(content.metadata)
            if metadata:
                doc_meta.update(metadata)
            return self._ingest_text(content.content, doc_meta)

        return self._ingest_text(content, metadata)

    def add_document_file(self, path: str | Path) -> list[Chunk]:
        """Load a file via :class:`DocumentLoader` and ingest it.

        Auto-detects the file format from the extension and uses the resolved
        path as ``source`` metadata.

        Returns the list of created :class:`Chunk` objects.
        """
        from ansible_collections.general_ludd.agent.plugins.module_utils.document_loader import (
            DocumentLoader,
        )

        loader = DocumentLoader()
        doc = loader.load(path)
        return self.add_document(doc)

    # -- internal ---------------------------------------------------------------

    def _ingest_text(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[Chunk]:
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

        1. Embed ``question`` via :meth:`HashEmbedder.embed`.
        2. Search the vector store for the ``top_k`` closest chunks.
        3. Build a prompt with the chunks as context.
        4. Call the model backend and return the answer.
        """
        query_vec = self._embedder.embed(question)
        results = self._store.search(query_vec, top_k=top_k)
        prompt = _build_prompt(question, results)

        response = self._model.chat(messages=[{"role": "user", "content": prompt}])
        if isinstance(response, str):
            return response
        if isinstance(response, dict):
            response_text = response.get("text")
            if isinstance(response_text, str):
                return response_text
        raise RuntimeError("daemon model response omitted text")

    @property
    def stored_count(self) -> int:
        """Number of stored vector entries."""
        return self._store.size

    def clear(self) -> None:
        """Remove all stored entries."""
        self._store = VectorStore()
