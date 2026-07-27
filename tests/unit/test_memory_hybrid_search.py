"""Unit tests for hybrid/keyword search in MemoryEmbeddingStore (S53.32)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from general_ludd.memory.embedding_store import (
    MemoryEmbeddingStore,
    _compute_keyword_scores,
    _tokenize,
)


class TestTokenize:
    def test_basic(self):
        tokens = _tokenize("Hello World 123")
        assert "hello" in tokens
        assert "world" in tokens
        assert "123" in tokens

    def test_punctuation_removed(self):
        tokens = _tokenize("fix: race-condition!")
        assert "fix" in tokens
        assert "race" in tokens
        assert "condition" in tokens
        assert ":" not in tokens

    def test_empty(self):
        assert _tokenize("") == []


class TestComputeKeywordScores:
    def test_empty_keywords(self):
        scores = _compute_keyword_scores({"a": "text"}, [], {})
        assert scores == {}

    def test_exact_match(self):
        scores = _compute_keyword_scores(
            {"r1": "deploy kubernetes cluster"},
            ["kubernetes"],
            {"r1": {"agent_id": "a1"}},
        )
        assert "r1" in scores
        assert scores["r1"] > 0

    def test_no_match(self):
        scores = _compute_keyword_scores(
            {"r1": "python test"},
            ["kubernetes"],
            {"r1": {}},
        )
        assert scores == {}

    def test_agent_filter(self):
        scores = _compute_keyword_scores(
            {"r1": "deploy", "r2": "deploy"},
            ["deploy"],
            {"r1": {"agent_id": "a1"}, "r2": {"agent_id": "a2"}},
            agent_id="a1",
        )
        assert "r1" in scores
        assert "r2" not in scores

    def test_namespace_filter(self):
        scores = _compute_keyword_scores(
            {"r1": "text", "r2": "text"},
            ["text"],
            {"r1": {"namespace": "ns1"}, "r2": {"namespace": "ns2"}},
            namespace="ns1",
        )
        assert "r1" in scores
        assert "r2" not in scores


class TestHybridSearch:
    @pytest.fixture
    def store(self):
        repo = MagicMock()
        embedder = MagicMock()
        embedder.embed.return_value = [0.1, 0.2, 0.3]
        s = MemoryEmbeddingStore(repo, embedder=embedder)
        return s

    @pytest.mark.asyncio
    async def test_empty_index(self, store):
        results = await store.hybrid_search("query")
        assert results == []

    @pytest.mark.asyncio
    async def test_empty_query(self, store):
        results = await store.hybrid_search("")
        assert results == []

    @pytest.mark.asyncio
    async def test_returns_combined_scores(self, store):
        store._embeddings["r1"] = [0.1, 0.2, 0.3]
        store._embeddings["r2"] = [0.4, 0.5, 0.6]
        store._record_texts["r1"] = "deploy kubernetes cluster"
        store._record_texts["r2"] = "configure database connection"
        store._record_meta["r1"] = {"agent_id": "a1"}
        store._record_meta["r2"] = {"agent_id": "a1"}

        results = await store.hybrid_search(
            "kubernetes",
            keywords=["deploy"],
            vector_weight=0.5,
            keyword_weight=0.5,
        )
        assert len(results) >= 1
        assert results[0]["record_id"] in ("r1", "r2")
        assert "vector_score" in results[0]
        assert "keyword_score" in results[0]

    @pytest.mark.asyncio
    async def test_respects_weights(self, store):
        store._embeddings["r1"] = [0.1, 0.2, 0.3]
        store._record_texts["r1"] = "deploy kubernetes"
        store._record_meta["r1"] = {"agent_id": "a1"}

        results = await store.hybrid_search(
            "kubernetes",
            keywords=["deploy"],
            vector_weight=1.0,
            keyword_weight=0.0,
        )
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_filters_by_agent(self, store):
        store._embeddings["r1"] = [0.1, 0.2, 0.3]
        store._embeddings["r2"] = [0.4, 0.5, 0.6]
        store._record_texts["r1"] = "deploy"
        store._record_texts["r2"] = "deploy"
        store._record_meta["r1"] = {"agent_id": "a1"}
        store._record_meta["r2"] = {"agent_id": "a2"}

        results = await store.hybrid_search("deploy", agent_id="a1")
        assert len(results) == 1
        assert results[0]["record_id"] == "r1"

    @pytest.mark.asyncio
    async def test_min_score_filter(self, store):
        store._embeddings["r1"] = [0.1, 0.2, 0.3]
        store._record_texts["r1"] = "unrelated text"
        store._record_meta["r1"] = {"agent_id": "a1"}

        results = await store.hybrid_search(
            "completely different query zzz",
            min_score=0.9,
        )
        assert len(results) == 0


class TestKeywordSearch:
    @pytest.fixture
    def store(self):
        repo = MagicMock()
        s = MemoryEmbeddingStore(repo)
        return s

    @pytest.mark.asyncio
    async def test_empty_index(self, store):
        results = await store.keyword_search("query")
        assert results == []

    @pytest.mark.asyncio
    async def test_empty_query(self, store):
        results = await store.keyword_search("")
        assert results == []

    @pytest.mark.asyncio
    async def test_pure_keyword_match(self, store):
        store._embeddings["r1"] = [0.1]
        store._embeddings["r2"] = [0.2]
        store._record_texts["r1"] = "deploy kubernetes cluster"
        store._record_texts["r2"] = "configure database"
        store._record_meta["r1"] = {"agent_id": "a1"}
        store._record_meta["r2"] = {"agent_id": "a1"}

        results = await store.keyword_search("kubernetes")
        assert len(results) >= 1
        assert results[0]["record_id"] == "r1"

    @pytest.mark.asyncio
    async def test_no_match(self, store):
        store._embeddings["r1"] = [0.1]
        store._record_texts["r1"] = "deploy"
        store._record_meta["r1"] = {"agent_id": "a1"}

        results = await store.keyword_search("zzz_nonexistent")
        assert results == []

    @pytest.mark.asyncio
    async def test_top_k_limit(self, store):
        for i in range(5):
            store._embeddings[f"r{i}"] = [0.1]
            store._record_texts[f"r{i}"] = f"item {i} test"
            store._record_meta[f"r{i}"] = {"agent_id": "a1"}

        results = await store.keyword_search("test item", top_k=2)
        assert len(results) <= 2
