"""Unit tests for memory/embedding_store.py — MemoryEmbeddingStore."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.memory.embedding_store import MemoryEmbeddingStore
from general_ludd.skills.embeddings import HashEmbedder


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
            "rec-1", "agent-1", "text",
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


class TestMemoryEmbeddingStoreReindex:
    @pytest.mark.asyncio
    async def test_reindex_populates_index(self) -> None:
        mock_repo = MagicMock()
        mock_repo.list_by_namespace = AsyncMock(return_value=[
            _make_mock_row("rec-1", "agent-1", "race condition fix"),
            _make_mock_row("rec-2", "agent-1", "rate limiting added"),
            _make_mock_row("rec-3", "agent-1", "refactored event loop"),
        ])
        store = MemoryEmbeddingStore(mock_repo, embedder=HashEmbedder())
        summary = await store.reindex_from_repo("agent-1", namespace="episodic")
        assert summary["indexed"] == 3
        assert summary["skipped"] == 0
        assert summary["total_in_repo"] == 3
        assert store.count == 3

    @pytest.mark.asyncio
    async def test_reindex_skips_empty_values(self) -> None:
        mock_repo = MagicMock()
        mock_repo.list_by_namespace = AsyncMock(return_value=[
            _make_mock_row("rec-1", "agent-1", ""),
            _make_mock_row("rec-2", "agent-1", "   "),
            _make_mock_row("rec-3", "agent-1", "valid record"),
        ])
        store = MemoryEmbeddingStore(mock_repo, embedder=HashEmbedder())
        summary = await store.reindex_from_repo("agent-1")
        assert summary["indexed"] == 1
        assert summary["skipped"] == 2
        assert store.count == 1

    @pytest.mark.asyncio
    async def test_reindex_parses_json_values(self) -> None:
        mock_repo = MagicMock()
        mock_repo.list_by_namespace = AsyncMock(return_value=[
            _make_mock_row("rec-1", "agent-1", '{"takeaway": "fixed race", "task_type": "bug_fix", "outcome": "success"}'),
        ])
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


def _make_mock_row(record_id: str, agent_id: str, value: str) -> MagicMock:
    row = MagicMock()
    row.id = record_id
    row.agent_id = agent_id
    row.key = record_id
    row.value = value
    row.ttl_seconds = None
    row.project_id = None
    return row
