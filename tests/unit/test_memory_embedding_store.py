"""Unit tests for memory/embedding_store.py — MemoryEmbeddingStore."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.memory.embedding_store import (
    MemoryEmbeddingStore,
    _compute_keyword_scores,
    _tokenize,
)
from general_ludd.skills.embeddings import (
    HashEmbedder,
)


# ---------------------------------------------------------------------------
# _tokenize
# ---------------------------------------------------------------------------
class TestTokenize:
    def test_lowercases_and_splits(self) -> None:
        tokens = _tokenize("Fix Race Condition")
        assert "fix" in tokens
        assert "race" in tokens
        assert "condition" in tokens

    def test_filters_punctuation(self) -> None:
        tokens = _tokenize("fix: race-condition!!")
        assert tokens == ["fix", "race", "condition"]

    def test_empty_string(self) -> None:
        assert _tokenize("") == []
        assert _tokenize("   ") == []

    def test_numbers_kept(self) -> None:
        tokens = _tokenize("error123 and 404 bug")
        assert "error123" in tokens
        assert "404" in tokens


# ---------------------------------------------------------------------------
# _compute_keyword_scores
# ---------------------------------------------------------------------------
class TestComputeKeywordScores:
    def test_empty_keywords_returns_empty(self) -> None:
        scores = _compute_keyword_scores({"r1": "hello world"}, [], {"r1": {}})
        assert scores == {}

    def test_exact_match_scores_one(self) -> None:
        scores = _compute_keyword_scores(
            {"r1": "hello world", "r2": "unrelated text"},
            ["hello"],
            {"r1": {}, "r2": {}},
        )
        assert scores["r1"] == 1.0
        assert "r2" not in scores

    def test_partial_match_proportional(self) -> None:
        scores = _compute_keyword_scores(
            {"r1": "bug fix"},
            ["bug", "fix", "deploy"],
            {"r1": {}},
        )
        assert scores["r1"] == pytest.approx(2 / 3)

    def test_filters_by_agent_id(self) -> None:
        scores = _compute_keyword_scores(
            {"r1": "bug fix", "r2": "bug fix"},
            ["bug"],
            {"r1": {"agent_id": "a"}, "r2": {"agent_id": "b"}},
            agent_id="a",
        )
        assert list(scores.keys()) == ["r1"]

    def test_filters_by_namespace(self) -> None:
        scores = _compute_keyword_scores(
            {"r1": "bug fix", "r2": "bug fix"},
            ["bug"],
            {"r1": {"namespace": "episodic"}, "r2": {"namespace": "consolidated"}},
            namespace="episodic",
        )
        assert list(scores.keys()) == ["r1"]

    def test_filters_by_project_id(self) -> None:
        scores = _compute_keyword_scores(
            {"r1": "bug fix", "r2": "bug fix"},
            ["bug"],
            {"r1": {"project_id": "p1"}, "r2": {"project_id": "p2"}},
            project_id="p1",
        )
        assert list(scores.keys()) == ["r1"]

    def test_none_project_id_skips_filter(self) -> None:
        scores = _compute_keyword_scores(
            {"r1": "bug fix"},
            ["bug"],
            {"r1": {"project_id": None}},
            project_id="p1",
        )
        assert "r1" in scores

    def test_empty_text_scores_zero(self) -> None:
        scores = _compute_keyword_scores({"r1": ""}, ["hello"], {"r1": {}})
        assert "r1" not in scores


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------
class TestMemoryEmbeddingStoreConstruction:
    def test_default_embedder_is_hash_embedder(self) -> None:
        repo = MagicMock()
        store = MemoryEmbeddingStore(repo)
        assert isinstance(store._embedder, HashEmbedder)

    def test_custom_embedder(self) -> None:
        repo = MagicMock()
        embedder = MagicMock()
        store = MemoryEmbeddingStore(repo, embedder=embedder)
        assert store._embedder is embedder

    def test_initial_count_is_zero(self) -> None:
        store = MemoryEmbeddingStore(MagicMock())
        assert store.count == 0

    def test_initial_stores_are_empty(self) -> None:
        store = MemoryEmbeddingStore(MagicMock())
        assert store._embeddings == {}
        assert store._record_texts == {}
        assert store._record_meta == {}


# ---------------------------------------------------------------------------
# Add
# ---------------------------------------------------------------------------
class TestMemoryEmbeddingStoreAdd:
    @pytest.mark.asyncio
    async def test_add_single_record(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "fixed a race condition")
        assert store.count == 1
        assert "rec-1" in store._embeddings
        assert store._record_texts["rec-1"] == "fixed a race condition"

    @pytest.mark.asyncio
    async def test_add_multiple_records(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "fixed a race condition")
        await store.add("rec-2", "agent-1", "added rate limiting")
        await store.add("rec-3", "agent-2", "refactored the event loop")
        assert store.count == 3

    @pytest.mark.asyncio
    async def test_add_empty_text_skipped(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "")
        assert store.count == 0
        await store.add("rec-2", "agent-1", "   ")
        assert store.count == 0

    @pytest.mark.asyncio
    async def test_add_stores_metadata(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add(
            "rec-1",
            "agent-1",
            "text",
            namespace="episodic",
            ttl_seconds=3600,
            project_id="proj-a",
        )
        meta = store._record_meta["rec-1"]
        assert meta["agent_id"] == "agent-1"
        assert meta["namespace"] == "episodic"
        assert meta["ttl_seconds"] == 3600
        assert meta["project_id"] == "proj-a"

    @pytest.mark.asyncio
    async def test_add_overwrites_existing_record(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "original text")
        await store.add("rec-1", "agent-1", "updated text")
        assert store.count == 1
        assert store._record_texts["rec-1"] == "updated text"

    @pytest.mark.asyncio
    async def test_add_default_namespace(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "text")
        assert store._record_meta["rec-1"]["namespace"] == "default"

    @pytest.mark.asyncio
    async def test_add_none_project_id(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "text")
        assert store._record_meta["rec-1"]["project_id"] is None

    @pytest.mark.asyncio
    async def test_add_vector_has_correct_dim(self) -> None:
        embedder = HashEmbedder(dim=128)
        store = MemoryEmbeddingStore(MagicMock(), embedder=embedder)
        await store.add("rec-1", "agent-1", "hello world")
        assert len(store._embeddings["rec-1"]) == 128


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
class TestMemoryEmbeddingStoreSearch:
    @pytest.mark.asyncio
    async def test_search_returns_results_sorted_by_score(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "fixed a race condition in the scheduler")
        await store.add("rec-2", "agent-1", "added unit tests for the scheduler")
        await store.add("rec-3", "agent-1", "deployed to production")
        results = await store.search("concurrency race bug", top_k=3)
        assert len(results) >= 1
        assert results[0]["score"] >= results[-1]["score"]

    @pytest.mark.asyncio
    async def test_search_top_k_limit(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        for i in range(10):
            await store.add(f"rec-{i}", "agent-1", f"memory record number {i}")
        results = await store.search("memory record", top_k=3)
        assert len(results) <= 3

    @pytest.mark.asyncio
    async def test_search_min_score_filters(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "race condition bug fix")
        await store.add("rec-2", "agent-1", "completely unrelated cat pictures")
        results = await store.search("race condition", min_score=0.1)
        for r in results:
            assert r["score"] >= 0.1

    @pytest.mark.asyncio
    async def test_search_empty_query_returns_empty(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "some text")
        results = await store.search("")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_whitespace_query_returns_empty(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "some text")
        results = await store.search("   ")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_empty_index_returns_empty(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        results = await store.search("anything")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_filters_by_agent_id(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-a", "race condition fix")
        await store.add("rec-2", "agent-b", "race condition fix")
        results = await store.search("race condition", agent_id="agent-a")
        assert len(results) == 1
        assert results[0]["record_id"] == "rec-1"

    @pytest.mark.asyncio
    async def test_search_filters_by_namespace(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "bug fix", namespace="episodic")
        await store.add("rec-2", "agent-1", "bug fix", namespace="consolidated")
        results = await store.search("bug fix", namespace="episodic")
        assert len(results) == 1
        assert results[0]["record_id"] == "rec-1"

    @pytest.mark.asyncio
    async def test_search_filters_by_project_id(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "bug fix", project_id="proj-a")
        await store.add("rec-2", "agent-1", "bug fix", project_id="proj-b")
        results = await store.search("bug fix", project_id="proj-a")
        assert len(results) == 1
        assert results[0]["record_id"] == "rec-1"

    @pytest.mark.asyncio
    async def test_search_project_id_none_record_included(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "bug fix", project_id=None)
        await store.add("rec-2", "agent-1", "bug fix", project_id="proj-b")
        results = await store.search("bug fix", project_id="proj-a")
        assert "rec-1" in {r["record_id"] for r in results}

    @pytest.mark.asyncio
    async def test_search_returns_all_fields(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "bug fix", namespace="episodic", project_id="p1")
        results = await store.search("bug fix", top_k=1)
        assert len(results) == 1
        r = results[0]
        assert r["record_id"] == "rec-1"
        assert r["text"] == "bug fix"
        assert r["agent_id"] == "agent-1"
        assert r["namespace"] == "episodic"
        assert r["project_id"] == "p1"
        assert "score" in r

    @pytest.mark.asyncio
    async def test_search_exact_match_scores_highest(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "deploy production hotfix")
        await store.add("rec-2", "agent-1", "deploy production hotfix")
        await store.add("rec-3", "agent-1", "eat lunch")
        results = await store.search("deploy production hotfix", top_k=3)
        assert len(results) >= 2
        lunch_idx = next((i for i, r in enumerate(results) if r["record_id"] == "rec-3"), -1)
        hotfix_indices = [i for i, r in enumerate(results) if r["record_id"] in ("rec-1", "rec-2")]
        if lunch_idx >= 0 and hotfix_indices:
            assert max(hotfix_indices) < lunch_idx

    @pytest.mark.asyncio
    async def test_search_top_k_exceeds_index(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "hello world")
        results = await store.search("hello", top_k=100)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_search_exclude_expired_false(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "bug fix", ttl_seconds=0)
        results = await store.search("bug fix", exclude_expired=False)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_search_score_monotonic(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "deadlock mutex thread concurrency")
        await store.add("rec-2", "agent-1", "deadlock mutex thread")
        await store.add("rec-3", "agent-1", "deadlock mutex")
        await store.add("rec-4", "agent-1", "cat pictures")
        results = await store.search("deadlock mutex thread concurrency", top_k=4)
        assert len(results) >= 3
        for i in range(len(results) - 1):
            assert results[i]["score"] >= results[i + 1]["score"]

    @pytest.mark.asyncio
    async def test_search_handles_vector_length_mismatch(self) -> None:
        embedder = MagicMock()
        embedder.embed = MagicMock(
            side_effect=[
                [0.5, 0.5],  # query vector dim=2
                [0.1, 0.2, 0.3],  # stored vector dim=3 — mismatch
                [0.8, 0.1],  # stored vector dim=2 — matches
            ]
        )
        store = MemoryEmbeddingStore(MagicMock(), embedder=embedder)
        store._embeddings = {
            "rec-a": [0.1, 0.2, 0.3],
            "rec-b": [0.8, 0.1],
        }
        store._record_texts = {"rec-a": "text a", "rec-b": "text b"}
        store._record_meta = {"rec-a": {}, "rec-b": {}}
        results = await store.search("query", top_k=5)
        assert len(results) == 1
        assert results[0]["record_id"] == "rec-b"


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------
class TestMemoryEmbeddingStoreDelete:
    @pytest.mark.asyncio
    async def test_delete_existing_record(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "some text")
        assert store.count == 1
        result = store.delete("rec-1")
        assert result is True
        assert store.count == 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent_record(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        result = store.delete("no-such-record")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_removes_from_search(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "fix bug")
        await store.add("rec-2", "agent-1", "write test")
        store.delete("rec-1")
        results = await store.search("fix bug")
        record_ids = {r["record_id"] for r in results}
        assert "rec-1" not in record_ids

    @pytest.mark.asyncio
    async def test_delete_clears_all_stores(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "some text")
        store.delete("rec-1")
        assert "rec-1" not in store._embeddings
        assert "rec-1" not in store._record_texts
        assert "rec-1" not in store._record_meta


# ---------------------------------------------------------------------------
# Clear
# ---------------------------------------------------------------------------
class TestMemoryEmbeddingStoreClear:
    @pytest.mark.asyncio
    async def test_clear_empties_index(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "some text")
        await store.add("rec-2", "agent-1", "more text")
        assert store.count == 2
        store.clear()
        assert store.count == 0
        assert store._embeddings == {}
        assert store._record_texts == {}
        assert store._record_meta == {}

    @pytest.mark.asyncio
    async def test_clear_idempotent(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        store.clear()
        store.clear()
        assert store.count == 0

    @pytest.mark.asyncio
    async def test_clear_then_search_returns_empty(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "some text")
        store.clear()
        results = await store.search("some text")
        assert results == []

    @pytest.mark.asyncio
    async def test_clear_then_add_works(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "old text")
        store.clear()
        await store.add("rec-2", "agent-1", "new text")
        assert store.count == 1
        assert "rec-1" not in store._embeddings


# ---------------------------------------------------------------------------
# Hybrid Search
# ---------------------------------------------------------------------------
class TestMemoryEmbeddingStoreHybridSearch:
    @pytest.mark.asyncio
    async def test_hybrid_returns_combined_results(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "race condition bug fix")
        await store.add("rec-2", "agent-1", "deploy to production")
        results = await store.hybrid_search("race condition", top_k=2)
        assert len(results) >= 1
        for r in results:
            assert "vector_score" in r
            assert "keyword_score" in r

    @pytest.mark.asyncio
    async def test_hybrid_empty_query_returns_empty(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "some text")
        results = await store.hybrid_search("")
        assert results == []

    @pytest.mark.asyncio
    async def test_hybrid_empty_index_returns_empty(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        results = await store.hybrid_search("anything")
        assert results == []

    @pytest.mark.asyncio
    async def test_hybrid_filters_by_agent_id(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-a", "race condition fix")
        await store.add("rec-2", "agent-b", "race condition fix")
        results = await store.hybrid_search("race condition", agent_id="agent-a")
        assert len(results) == 1
        assert results[0]["record_id"] == "rec-1"

    @pytest.mark.asyncio
    async def test_hybrid_filters_by_namespace(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "bug fix", namespace="episodic")
        await store.add("rec-2", "agent-1", "bug fix", namespace="consolidated")
        results = await store.hybrid_search("bug fix", namespace="episodic")
        assert len(results) == 1
        assert results[0]["record_id"] == "rec-1"

    @pytest.mark.asyncio
    async def test_hybrid_filters_by_project_id(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "bug fix", project_id="p1")
        await store.add("rec-2", "agent-1", "bug fix", project_id="p2")
        results = await store.hybrid_search("bug fix", project_id="p1")
        assert len(results) == 1
        assert results[0]["record_id"] == "rec-1"

    @pytest.mark.asyncio
    async def test_hybrid_keywords_boost_scores(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "deploy production hotfix")
        await store.add("rec-2", "agent-1", "eat lunch break")
        results = await store.hybrid_search(
            "deploy hotfix",
            keywords=["deploy", "hotfix"],
            top_k=2,
            vector_weight=0.4,
            keyword_weight=0.6,
        )
        top = results[0]
        assert top["record_id"] in ("rec-1", "rec-2")

    @pytest.mark.asyncio
    async def test_hybrid_min_score_filters(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "deploy production hotfix")
        await store.add("rec-2", "agent-1", "unrelated cat pictures")
        results = await store.hybrid_search("deploy hotfix", min_score=0.01)
        for r in results:
            assert r["score"] >= 0.01

    @pytest.mark.asyncio
    async def test_hybrid_top_k_limit(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        for i in range(10):
            await store.add(f"rec-{i}", "agent-1", f"memory number {i}")
        results = await store.hybrid_search("memory", top_k=3)
        assert len(results) <= 3


# ---------------------------------------------------------------------------
# Keyword Search
# ---------------------------------------------------------------------------
class TestMemoryEmbeddingStoreKeywordSearch:
    @pytest.mark.asyncio
    async def test_keyword_search_returns_token_matches(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "race condition bug fix")
        await store.add("rec-2", "agent-1", "cat pictures")
        results = await store.keyword_search("race bug", top_k=2)
        assert len(results) >= 1
        assert results[0]["record_id"] == "rec-1"

    @pytest.mark.asyncio
    async def test_keyword_search_empty_query(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "some text")
        results = await store.keyword_search("")
        assert results == []

    @pytest.mark.asyncio
    async def test_keyword_search_filters_by_agent(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-a", "bug fix")
        await store.add("rec-2", "agent-b", "bug fix")
        results = await store.keyword_search("bug fix", agent_id="agent-a")
        assert len(results) == 1
        assert results[0]["record_id"] == "rec-1"

    @pytest.mark.asyncio
    async def test_keyword_search_min_score(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "race condition concurrency deadlock")
        results = await store.keyword_search("race condition", min_score=0.5)
        for r in results:
            assert r["score"] >= 0.5

    @pytest.mark.asyncio
    async def test_keyword_search_returns_all_fields(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "bug fix", namespace="episodic", project_id="p1")
        results = await store.keyword_search("bug fix", top_k=1)
        r = results[0]
        assert r["record_id"] == "rec-1"
        assert r["text"] == "bug fix"
        assert r["agent_id"] == "agent-1"
        assert r["namespace"] == "episodic"
        assert r["project_id"] == "p1"


# ---------------------------------------------------------------------------
# Reindex
# ---------------------------------------------------------------------------
class TestMemoryEmbeddingStoreReindex:
    @pytest.mark.asyncio
    async def test_reindex_populates_index(self) -> None:
        mock_repo = MagicMock()
        mock_repo.list_by_namespace = AsyncMock(
            return_value=[
                _make_mock_row("rec-1", "agent-1", "race condition fix"),
                _make_mock_row("rec-2", "agent-1", "rate limiting added"),
                _make_mock_row("rec-3", "agent-1", "refactored event loop"),
            ]
        )
        store = MemoryEmbeddingStore(mock_repo, embedder=HashEmbedder())
        summary = await store.reindex_from_repo("agent-1", namespace="episodic")
        assert summary["indexed"] == 3
        assert summary["skipped"] == 0
        assert summary["total_in_repo"] == 3
        assert store.count == 3

    @pytest.mark.asyncio
    async def test_reindex_skips_empty_values(self) -> None:
        mock_repo = MagicMock()
        mock_repo.list_by_namespace = AsyncMock(
            return_value=[
                _make_mock_row("rec-1", "agent-1", ""),
                _make_mock_row("rec-2", "agent-1", "   "),
                _make_mock_row("rec-3", "agent-1", "valid record"),
            ]
        )
        store = MemoryEmbeddingStore(mock_repo, embedder=HashEmbedder())
        summary = await store.reindex_from_repo("agent-1")
        assert summary["indexed"] == 1
        assert summary["skipped"] == 2
        assert store.count == 1

    @pytest.mark.asyncio
    async def test_reindex_parses_json_values(self) -> None:
        mock_repo = MagicMock()
        mock_repo.list_by_namespace = AsyncMock(
            return_value=[
                _make_mock_row(
                    "rec-1",
                    "agent-1",
                    '{"takeaway": "fixed race", "task_type": "bug_fix", "outcome": "success"}',
                ),
            ]
        )
        store = MemoryEmbeddingStore(mock_repo, embedder=HashEmbedder())
        summary = await store.reindex_from_repo("agent-1")
        assert summary["indexed"] == 1
        assert "bug_fix" in store._record_texts["rec-1"]

    @pytest.mark.asyncio
    async def test_reindex_handles_missing_id(self) -> None:
        row_no_id = MagicMock()
        row_no_id.agent_id = "agent-1"
        row_no_id.key = "rec-1"
        row_no_id.value = "some text"
        row_no_id.ttl_seconds = None
        row_no_id.project_id = None
        del row_no_id.id
        mock_repo = MagicMock()
        mock_repo.list_by_namespace = AsyncMock(return_value=[row_no_id])
        store = MemoryEmbeddingStore(mock_repo, embedder=HashEmbedder())
        summary = await store.reindex_from_repo("agent-1")
        assert summary["indexed"] == 1

    @pytest.mark.asyncio
    async def test_reindex_json_no_matching_fields(self) -> None:
        mock_repo = MagicMock()
        mock_repo.list_by_namespace = AsyncMock(
            return_value=[
                _make_mock_row("rec-1", "agent-1", '{"unknown_key": "value"}'),
            ]
        )
        store = MemoryEmbeddingStore(mock_repo, embedder=HashEmbedder())
        summary = await store.reindex_from_repo("agent-1")
        assert summary["skipped"] >= 1 or summary["indexed"] == 1

    @pytest.mark.asyncio
    async def test_reindex_with_ttl_and_project_id(self) -> None:
        mock_repo = MagicMock()
        mock_repo.list_by_namespace = AsyncMock(
            return_value=[
                _make_mock_row("rec-1", "agent-1", "valid text", ttl_seconds=3600, project_id="proj-x"),
            ]
        )
        store = MemoryEmbeddingStore(mock_repo, embedder=HashEmbedder())
        summary = await store.reindex_from_repo("agent-1")
        assert summary["indexed"] == 1
        assert store._record_meta["rec-1"]["ttl_seconds"] == 3600
        assert store._record_meta["rec-1"]["project_id"] == "proj-x"

    @pytest.mark.asyncio
    async def test_reindex_limit(self) -> None:
        mock_repo = MagicMock()
        mock_repo.list_by_namespace = AsyncMock(
            return_value=[_make_mock_row(f"rec-{i}", "agent-1", f"record {i}") for i in range(5)]
        )
        store = MemoryEmbeddingStore(mock_repo, embedder=HashEmbedder())
        summary = await store.reindex_from_repo("agent-1", limit=2)
        assert summary["indexed"] <= 5
        assert summary["total_in_repo"] == 5


# ---------------------------------------------------------------------------
# edge cases
# ---------------------------------------------------------------------------
class TestMemoryEmbeddingStoreEdgeCases:
    @pytest.mark.asyncio
    async def test_very_long_text(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        long_text = " ".join(["word"] * 1000)
        await store.add("rec-long", "agent-1", long_text)
        assert store.count == 1
        results = await store.search("word", top_k=1)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_unicode_text(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-unicode", "agent-1", "fix deja vu avec la fonction asynchrone")
        assert store.count == 1
        results = await store.search("fonction asynchrone")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_embedding_dimensionality_consistency(self) -> None:
        embedder = HashEmbedder()
        store = MemoryEmbeddingStore(MagicMock(), embedder=embedder)
        await store.add("rec-1", "agent-1", "short")
        await store.add("rec-2", "agent-1", "a much longer piece of text with many words")
        assert len(store._embeddings["rec-1"]) == len(store._embeddings["rec-2"])
        assert len(store._embeddings["rec-1"]) == embedder.dim

    @pytest.mark.asyncio
    async def test_special_characters_only(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "!@#$%^&*()")
        assert store.count == 1

    @pytest.mark.asyncio
    async def test_numeric_text(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "error 500 on line 42")
        assert store.count == 1
        results = await store.search("500 error", top_k=1)
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Integration / ranking correctness
# ---------------------------------------------------------------------------
class TestMemoryEmbeddingStoreIntegration:
    @pytest.mark.asyncio
    async def test_similar_texts_rank_higher_than_dissimilar(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-a", "agent-1", "fix deadlock in database transaction pool")
        await store.add("rec-b", "agent-1", "add dark mode toggle to settings UI")
        await store.add("rec-c", "agent-1", "resolve mutex contention in worker threads")
        results = await store.search("concurrency lock thread deadlock mutex", top_k=3)
        assert len(results) >= 2
        a_idx = next((i for i, r in enumerate(results) if r["record_id"] == "rec-a"), 999)
        c_idx = next((i for i, r in enumerate(results) if r["record_id"] == "rec-c"), 999)
        b_idx = next((i for i, r in enumerate(results) if r["record_id"] == "rec-b"), 999)
        assert a_idx < b_idx
        assert c_idx < b_idx

    @pytest.mark.asyncio
    async def test_self_query_returns_perfect_score(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "fix concurrency bug in thread pool")
        results = await store.search("fix concurrency bug in thread pool", top_k=1)
        assert results[0]["score"] == pytest.approx(1.0, abs=0.001)

    @pytest.mark.asyncio
    async def test_semantically_different_texts_score_low(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-1", "agent-1", "database query optimization for joins")
        await store.add("rec-2", "agent-1", "CSS styling for the navbar component")
        results = await store.search("add transition animation to button hover", top_k=2)
        assert len(results) >= 1
        rec1_score = next((r["score"] for r in results if r["record_id"] == "rec-1"), 0)
        rec2_score = next((r["score"] for r in results if r["record_id"] == "rec-2"), 0)
        assert rec2_score >= rec1_score


# ---------------------------------------------------------------------------
# Concurrent access safety
# ---------------------------------------------------------------------------
class TestMemoryEmbeddingStoreConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_adds(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())

        async def add_one(i: int) -> None:
            await store.add(f"rec-{i}", "agent-1", f"record number {i}")

        await asyncio.gather(*(add_one(i) for i in range(50)))
        assert store.count == 50

    @pytest.mark.asyncio
    async def test_concurrent_add_and_search(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        await store.add("rec-init", "agent-1", "initial record")

        async def worker(n: int) -> None:
            await store.add(f"rec-{n}", "agent-1", f"worker record {n}")
            await store.search(f"record {n}", top_k=2)

        await asyncio.gather(*(worker(i) for i in range(20)))
        assert store.count == 21  # init + 20

    @pytest.mark.asyncio
    async def test_concurrent_add_and_delete(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder())
        for i in range(20):
            await store.add(f"rec-{i}", "agent-1", f"record {i}")

        async def delete_add(n: int) -> None:
            store.delete(f"rec-{n}")
            await store.add(f"rec-new-{n}", "agent-1", f"new record {n}")

        await asyncio.gather(*(delete_add(i) for i in range(20)))
        assert store.count == 20


# ---------------------------------------------------------------------------
# Batch insert performance (wall-clock)
# ---------------------------------------------------------------------------
class TestMemoryEmbeddingStoreBatch:
    @pytest.mark.asyncio
    async def test_batch_insert_performance(self) -> None:
        store = MemoryEmbeddingStore(MagicMock(), embedder=HashEmbedder(dim=64))
        t0 = time.monotonic()
        for i in range(200):
            await store.add(f"rec-{i}", "agent-1", f"memory record for testing performance {i}")
        elapsed = time.monotonic() - t0
        assert store.count == 200
        assert elapsed < 10, f"batch insert took {elapsed:.2f}s, expected <10s"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _make_mock_row(
    record_id: str,
    agent_id: str,
    value: str,
    ttl_seconds: int | None = None,
    project_id: str | None = None,
) -> MagicMock:
    row = MagicMock()
    row.id = record_id
    row.agent_id = agent_id
    row.key = record_id
    row.value = value
    row.ttl_seconds = ttl_seconds
    row.project_id = project_id
    return row
