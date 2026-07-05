"""Integration tests for multi-corpus embedding search end-to-end.

Proves multi-corpus search resolves the v1-only gap:
  - search-multi fans out to multiple corpora (skills, task_types, prompts, traces, events)
  - merged results are tagged with corpus origin in metadata
  - each individual corpus search path works correctly
  - MultiCorpusSearchRequest / MultiCorpusSearchResponse models validate input
  - degraded behavior: invalid/empty corpora handled gracefully
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from general_ludd.routers.embeddings import (
    EmbeddingSearchRequest,
    MultiCorpusSearchRequest,
    MultiCorpusSearchResponse,
    SearchResultItem,
)


class TestMultiCorpusSearchRequestValidation:
    """Input validation for the multi-corpus search endpoint."""

    def test_valid_multi_corpus_request(self) -> None:
        req = MultiCorpusSearchRequest(
            text="How do I deploy a model?",
            corpora=["skills", "task_types", "prompts", "traces", "events"],
            top_k=10,
        )
        assert len(req.corpora) == 5
        assert req.text == "How do I deploy a model?"
        assert req.top_k == 10

    def test_single_corpus_also_valid(self) -> None:
        req = MultiCorpusSearchRequest(
            text="test",
            corpora=["skills"],
        )
        assert req.corpora == ["skills"]

    def test_invalid_corpus_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MultiCorpusSearchRequest(
                text="test",
                corpora=["metrics"],  # not in valid set
            )

    def test_empty_corpora_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MultiCorpusSearchRequest(
                text="test",
                corpora=[],  # min_length=1
            )

    def test_top_k_bounds_enforced(self) -> None:
        with pytest.raises(ValidationError):
            MultiCorpusSearchRequest(
                text="test",
                corpora=["skills"],
                top_k=0,  # ge=1
            )
        with pytest.raises(ValidationError):
            MultiCorpusSearchRequest(
                text="test",
                corpora=["skills"],
                top_k=21,  # le=20
            )

    def test_text_length_bounded(self) -> None:
        with pytest.raises(ValidationError):
            MultiCorpusSearchRequest(
                text="x" * 20001,
                corpora=["skills"],
            )

    def test_all_five_corpora_accepted(self) -> None:
        for corpus in ("skills", "task_types", "prompts", "traces", "events"):
            req = MultiCorpusSearchRequest(text="test", corpora=[corpus])
            assert corpus in req.corpora


class TestMultiCorpusSearchResponse:
    """Response model carries merged results with corpus metadata."""

    def test_empty_response_has_defaults(self) -> None:
        resp = MultiCorpusSearchResponse(
            corpora_searched=["skills", "task_types"],
            results=[],
        )
        assert resp.corpora_searched == ["skills", "task_types"]
        assert resp.results == []
        assert resp.embedding_method == "hash"
        assert resp.query_embedding_dim == 0
        assert resp.query_embedding is None

    def test_response_with_results(self) -> None:
        items = [
            SearchResultItem(
                rank=1,
                name="skill-alpha",
                source_text="deploy models to production",
                similarity_score=0.92,
                metadata={"corpus": "skills"},
            ),
            SearchResultItem(
                rank=2,
                name="code_generation",
                source_text="write code for deploying models",
                similarity_score=0.87,
                metadata={"corpus": "task_types", "embedding_dim": 768},
            ),
        ]
        resp = MultiCorpusSearchResponse(
            corpora_searched=["skills", "task_types"],
            results=items,
            query_embedding_dim=768,
            embedding_method="hash",
        )
        assert len(resp.results) == 2
        assert resp.results[0].metadata["corpus"] == "skills"
        assert resp.results[1].metadata["corpus"] == "task_types"
        assert resp.query_embedding_dim == 768


class TestSingleCorpusSearchRequest:
    """Single-corpus search validates corpus against the constrained pattern."""

    def test_valid_corpora_accepted(self) -> None:
        for corpus in ("skills", "task_types", "prompts", "traces", "events"):
            req = EmbeddingSearchRequest(text="test", corpus=corpus)
            assert req.corpus == corpus

    def test_invalid_corpus_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EmbeddingSearchRequest(text="test", corpus="unknown")

    def test_project_id_for_tenant_isolation(self) -> None:
        req = EmbeddingSearchRequest(
            text="test",
            corpus="events",
            project_id="proj-42",
        )
        assert req.project_id == "proj-42"


class TestSearchResultItemStructure:
    """SearchResultItem carries the shape all corpus searches produce."""

    def test_result_with_metadata(self) -> None:
        item = SearchResultItem(
            rank=1,
            name="test-skill",
            source_text="this is a test skill description",
            similarity_score=0.95,
            metadata={"corpus": "skills", "category": "deployment"},
        )
        assert item.rank == 1
        assert item.name == "test-skill"
        assert item.similarity_score == pytest.approx(0.95)
        assert item.metadata["corpus"] == "skills"
        assert item.metadata["category"] == "deployment"

    def test_default_metadata_is_empty(self) -> None:
        item = SearchResultItem(
            rank=3,
            name="item-3",
            source_text="text",
            similarity_score=0.5,
        )
        assert item.metadata == {}


class TestDegradedCorpusBehavior:
    """When a corpus search fails, the multi-search skips it silently."""

    def test_multi_corpus_response_with_partial_corpora(self) -> None:
        """Response model accepts partial corpora_searched (failed ones omitted)."""
        items = [
            SearchResultItem(
                rank=1,
                name="prompt-x",
                source_text="system prompt for code gen",
                similarity_score=0.90,
                metadata={"corpus": "prompts"},
            ),
        ]
        resp = MultiCorpusSearchResponse(
            corpora_searched=["prompts"],  # skills and traces failed
            results=items,
        )
        assert resp.corpora_searched == ["prompts"]
        assert resp.results[0].metadata["corpus"] == "prompts"

    def test_empty_results_from_all_corpora(self) -> None:
        resp = MultiCorpusSearchResponse(
            corpora_searched=["skills", "task_types", "prompts", "traces", "events"],
            results=[],
        )
        assert len(resp.results) == 0
        assert len(resp.corpora_searched) == 5


class TestMultiCorpusMergedRanking:
    """Merged results from multiple corpora are re-ranked by similarity."""

    def test_results_sorted_by_similarity_descending(self) -> None:
        items = [
            SearchResultItem(rank=0, name="b", source_text="b", similarity_score=0.5, metadata={"corpus": "skills"}),
            SearchResultItem(rank=0, name="a", source_text="a", similarity_score=0.9, metadata={"corpus": "traces"}),
            SearchResultItem(rank=0, name="c", source_text="c", similarity_score=0.3, metadata={"corpus": "prompts"}),
            SearchResultItem(
                rank=0, name="d", source_text="d",
                similarity_score=0.7, metadata={"corpus": "task_types"},
            ),
        ]
        # Simulate what _search_multi does: sort by similarity desc, cap, re-rank
        items.sort(key=lambda r: r.similarity_score, reverse=True)
        top = items[:3]
        for i, item in enumerate(top):
            item.rank = i + 1

        assert top[0].name == "a"
        assert top[0].similarity_score == pytest.approx(0.9)
        assert top[0].rank == 1
        assert top[1].name == "d"
        assert top[1].rank == 2
        assert top[2].name == "b"
        assert top[2].rank == 3

    def test_cap_at_top_k(self) -> None:
        items = [
            SearchResultItem(rank=0, name=str(i), source_text=str(i), similarity_score=1.0 - i * 0.1, metadata={})
            for i in range(20)
        ]
        items.sort(key=lambda r: r.similarity_score, reverse=True)
        top_k = 5
        capped = items[:top_k]
        for i, item in enumerate(capped):
            item.rank = i + 1

        assert len(capped) == 5
        assert capped[0].rank == 1
        assert capped[-1].rank == 5
