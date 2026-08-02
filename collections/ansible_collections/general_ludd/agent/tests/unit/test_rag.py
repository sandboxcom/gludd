"""
Tests for the RAG pipeline module_utils.

Covers: Chunker, VectorStore, HashEmbedder (via VectorStore), RAGPipeline
(when the model client is not actually called).

Run with:
    ANSIBLE_COLLECTIONS_PATH=collections uv run python -m pytest \
      collections/ansible_collections/general_ludd/agent/tests/unit/test_rag.py -v
"""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "collections")

from ansible_collections.general_ludd.agent.plugins.module_utils.embeddings import (
    HashEmbedder,
    cosine_similarity,
)
from ansible_collections.general_ludd.agent.plugins.module_utils.rag import (
    Chunk,
    Chunker,
    RAGPipeline,
    VectorEntry,
    VectorStore,
    _build_prompt,
)

# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------


class TestChunker:
    def test_default_params(self):
        c = Chunker()
        assert c.chunk_size == 1000
        assert c.chunk_overlap == 200

    def test_invalid_chunk_size(self):
        with pytest.raises(ValueError, match="chunk_size must be positive"):
            Chunker(chunk_size=0)
        with pytest.raises(ValueError, match="chunk_size must be positive"):
            Chunker(chunk_size=-1)

    def test_invalid_chunk_overlap(self):
        with pytest.raises(ValueError, match="chunk_overlap must be non-negative"):
            Chunker(chunk_overlap=-1)

    def test_overlap_gte_size(self):
        with pytest.raises(ValueError, match="chunk_overlap must be less than chunk_size"):
            Chunker(chunk_size=100, chunk_overlap=100)
        with pytest.raises(ValueError, match="chunk_overlap must be less than chunk_size"):
            Chunker(chunk_size=100, chunk_overlap=200)

    def test_empty_text(self):
        c = Chunker()
        assert c.split("") == []

    def test_text_shorter_than_chunk_size(self):
        c = Chunker(chunk_size=50, chunk_overlap=10)
        chunks = c.split("hello world")
        assert len(chunks) == 1
        assert chunks[0].text == "hello world"
        assert chunks[0].index == 0

    def test_multiple_chunks_no_overlap(self):
        c = Chunker(chunk_size=5, chunk_overlap=0)
        chunks = c.split("abcdefghij")
        assert len(chunks) == 2
        assert chunks[0].text == "abcde"
        assert chunks[1].text == "fghij"

    def test_multiple_chunks_with_overlap(self):
        c = Chunker(chunk_size=5, chunk_overlap=2)
        chunks = c.split("abcdefghij")
        assert len(chunks) == 3
        assert chunks[0].text == "abcde"
        assert chunks[1].text == "defgh"
        assert chunks[2].text == "ghij"

    def test_exact_chunk_size(self):
        c = Chunker(chunk_size=5, chunk_overlap=0)
        chunks = c.split("abcde")
        assert len(chunks) == 1
        assert chunks[0].text == "abcde"

    def test_metadata_passed_to_chunks(self):
        c = Chunker(chunk_size=5, chunk_overlap=0)
        chunks = c.split("abcdefghij", metadata={"source": "test.txt"})
        assert len(chunks) == 2
        for ch in chunks:
            assert ch.metadata == {"source": "test.txt"}

    def test_independent_metadata_copies(self):
        c = Chunker(chunk_size=5, chunk_overlap=0)
        chunks = c.split("abcdefghij", metadata={"source": "test.txt"})
        chunks[0].metadata["source"] = "modified.txt"
        assert chunks[1].metadata["source"] == "test.txt"


# ---------------------------------------------------------------------------
# VectorStore
# ---------------------------------------------------------------------------


class TestVectorStore:
    def test_empty_store(self):
        store = VectorStore()
        assert store.size == 0
        assert store.search([1.0, 0.0], top_k=5) == []

    def test_add_and_size(self):
        store = VectorStore()
        chunk = Chunk(text="hello", metadata={}, index=0)
        entry = VectorEntry(chunk=chunk, vector=[1.0, 0.0, 0.0])
        store.add(entry)
        assert store.size == 1

    def test_add_all(self):
        store = VectorStore()
        entries = [
            VectorEntry(chunk=Chunk(text="a", index=0), vector=[1.0, 0.0]),
            VectorEntry(chunk=Chunk(text="b", index=1), vector=[0.0, 1.0]),
        ]
        store.add_all(entries)
        assert store.size == 2

    def test_search_returns_top_k(self):
        store = VectorStore()
        entries = [
            VectorEntry(chunk=Chunk(text="first", index=0), vector=[1.0, 0.0]),
            VectorEntry(chunk=Chunk(text="second", index=1), vector=[0.0, 1.0]),
            VectorEntry(chunk=Chunk(text="third", index=2), vector=[0.5, 0.5]),
        ]
        store.add_all(entries)
        results = store.search([1.0, 0.0], top_k=2)
        assert len(results) == 2
        assert results[0].chunk.text == "first"

    def test_search_all_when_top_k_exceeds_entries(self):
        store = VectorStore()
        entries = [
            VectorEntry(chunk=Chunk(text="first", index=0), vector=[1.0, 0.0]),
            VectorEntry(chunk=Chunk(text="second", index=1), vector=[0.0, 1.0]),
        ]
        store.add_all(entries)
        results = store.search([1.0, 0.0], top_k=10)
        assert len(results) == 2


# ---------------------------------------------------------------------------
# cosine_similarity
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_opposite_vectors(self):
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_orthogonal_vectors(self):
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_zero_vector_returns_zero(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0
        assert cosine_similarity([1.0, 2.0], [0.0, 0.0]) == 0.0

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="vector length mismatch"):
            cosine_similarity([1.0], [1.0, 2.0])


# ---------------------------------------------------------------------------
# HashEmbedder
# ---------------------------------------------------------------------------


class TestHashEmbedder:
    def test_default_dim(self):
        h = HashEmbedder()
        assert h.dim == 256

    def test_invalid_dim(self):
        with pytest.raises(ValueError, match="dim must be positive"):
            HashEmbedder(dim=0)

    def test_embed_returns_correct_length(self):
        h = HashEmbedder(dim=256)
        vec = h.embed("hello world")
        assert len(vec) == 256

    def test_embed_normalized_to_unit_length(self):
        h = HashEmbedder(dim=64)
        vec = h.embed("some sample text with enough words")
        norm = sum(v * v for v in vec)
        assert norm == pytest.approx(1.0, rel=1e-5)

    def test_empty_text_returns_zero_vector(self):
        h = HashEmbedder(dim=16)
        vec = h.embed("")
        assert vec == [0.0] * 16

    def test_different_texts_produce_different_vectors(self):
        h = HashEmbedder(dim=64)
        v1 = h.embed("machine learning")
        v2 = h.embed("deep learning models")
        assert v1 != v2

    def test_same_text_same_vector(self):
        h = HashEmbedder(dim=64)
        v1 = h.embed("hello world")
        v2 = h.embed("hello world")
        assert v1 == v2


# ---------------------------------------------------------------------------
# _build_prompt
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_prompt_contains_question_and_context(self):
        entries = [
            VectorEntry(
                chunk=Chunk(text="context one", metadata={"source": "doc1"}, index=0),
                vector=[0.0, 0.0],
            ),
        ]
        prompt = _build_prompt("what is this?", entries)
        assert "what is this?" in prompt
        assert "context one" in prompt
        assert "[1]" in prompt
        assert "source=doc1" in prompt

    def test_prompt_with_multiple_chunks(self):
        entries = [
            VectorEntry(
                chunk=Chunk(text="first chunk", metadata={}, index=0),
                vector=[0.0, 0.0],
            ),
            VectorEntry(
                chunk=Chunk(text="second chunk", metadata={}, index=0),
                vector=[0.0, 0.0],
            ),
        ]
        prompt = _build_prompt("test question", entries)
        assert "[1]" in prompt
        assert "[2]" in prompt
        assert "first chunk" in prompt
        assert "second chunk" in prompt

    def test_empty_context(self):
        prompt = _build_prompt("test question", [])
        assert "test question" in prompt
        assert "Answer:" in prompt


# ---------------------------------------------------------------------------
# RAGPipeline (unit tests — no actual model call)
# ---------------------------------------------------------------------------


class _FakeModelClient:
    """A model client that captures the last prompt sent."""

    def __init__(self, response: str | None = None) -> None:
        self.last_messages: list[dict[str, str]] = []
        self._response = response or "fake answer"

    def chat(self, messages: list[dict[str, str]], **kwargs: object) -> str:
        self.last_messages = list(messages)
        return self._response


class TestRAGPipeline:
    def test_add_document_stores_correct_count(self):
        mc = _FakeModelClient()
        pipeline = RAGPipeline(model_client=mc, chunk_size=200, chunk_overlap=0)
        pipeline.add_document("hello " * 100)
        assert pipeline.stored_count > 0

    def test_add_document_returns_chunks(self):
        mc = _FakeModelClient()
        pipeline = RAGPipeline(model_client=mc, chunk_size=50, chunk_overlap=0)
        chunks = pipeline.add_document("x" * 200)
        assert len(chunks) > 1
        assert all(isinstance(c, Chunk) for c in chunks)

    def test_add_empty_text_returns_empty(self):
        mc = _FakeModelClient()
        pipeline = RAGPipeline(model_client=mc)
        chunks = pipeline.add_document("")
        assert chunks == []
        assert pipeline.stored_count == 0

    def test_query_add_document_then_query(self):
        mc = _FakeModelClient(response="42")
        pipeline = RAGPipeline(model_client=mc)
        pipeline.add_document(
            "The meaning of life is 42, according to the guide.",
            metadata={"source": "guide"},
        )

        answer = pipeline.query("What is the meaning of life?", top_k=3)
        assert answer == "42"

    def test_query_captures_prompt(self):
        mc = _FakeModelClient(response="the answer")
        pipeline = RAGPipeline(model_client=mc)
        pipeline.add_document(
            "Rainbows form when sunlight refracts through water droplets in the air.",
            metadata={"source": "weather"},
        )

        pipeline.query("How do rainbows form?", top_k=3)
        assert len(mc.last_messages) == 1
        assert mc.last_messages[0]["role"] == "user"
        assert "How do rainbows form?" in mc.last_messages[0]["content"]
        assert "droplets" in mc.last_messages[0]["content"]

    def test_query_with_no_documents(self):
        mc = _FakeModelClient(response="I don't know")
        pipeline = RAGPipeline(model_client=mc)

        answer = pipeline.query("any question?")
        assert answer == "I don't know"

    def test_clear_removes_all_entries(self):
        mc = _FakeModelClient()
        pipeline = RAGPipeline(model_client=mc)
        pipeline.add_document("some text")
        assert pipeline.stored_count > 0
        pipeline.clear()
        assert pipeline.stored_count == 0

    def test_stored_count_before_document(self):
        mc = _FakeModelClient()
        pipeline = RAGPipeline(model_client=mc)
        assert pipeline.stored_count == 0

    def test_custom_embedder(self):
        mc = _FakeModelClient()
        embedder = HashEmbedder(dim=128)
        pipeline = RAGPipeline(model_client=mc, embedder=embedder)
        pipeline.add_document("custom embedder test text")
        assert pipeline.stored_count > 0

    def test_custom_chunk_params(self):
        mc = _FakeModelClient()
        pipeline = RAGPipeline(model_client=mc, chunk_size=100, chunk_overlap=50)
        pipeline.add_document("x" * 300)
        assert pipeline.stored_count > 1

    def test_query_respects_top_k(self):
        mc = _FakeModelClient()
        pipeline = RAGPipeline(model_client=mc)
        pipeline.add_document("doc one")
        pipeline.add_document("doc two")
        pipeline.add_document("doc three")
        pipeline.add_document("doc four")
        pipeline.add_document("doc five")
        pipeline.add_document("doc six")

        pipeline.query("query", top_k=2)
        prompt = mc.last_messages[0]["content"]
        assert "[1]" in prompt
        assert "[2]" in prompt
        assert "[3]" not in prompt

    def test_multiple_documents_accumulate(self):
        mc = _FakeModelClient()
        pipeline = RAGPipeline(model_client=mc)
        c1 = pipeline.add_document("first document")
        c2 = pipeline.add_document("second document")
        assert pipeline.stored_count == len(c1) + len(c2)
