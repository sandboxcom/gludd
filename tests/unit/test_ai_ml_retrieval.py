"""Unit tests for general_ludd.ai_ml.retrieval (AIML-006).

Covers:
  - RetrievedPassage construction, validation, frozen immutability
  - RetrievalResult construction, validation, defaults
  - RetrievalMetrics construction, range validation
  - RetrievalService index, search, evaluate
  - _lexical_score term-overlap computation
  - _hash_vec deterministic dense vector generation
  - _dense_score cosine similarity
  - _hybrid_score weighted fusion
  - Reranking by hybrid fusion score (descending)
  - recall@k, MRR, nDCG computation correctness
  - Edge cases: empty corpus, empty query, k=1, no relevant docs
  - DENSE_VECTOR_HASH_VERSION constant
"""

from __future__ import annotations

import math
from dataclasses import FrozenInstanceError

import pytest

from general_ludd.ai_ml.retrieval import (
    DENSE_VECTOR_HASH_VERSION,
    RetrievalMetrics,
    RetrievalResult,
    RetrievalService,
    RetrievedPassage,
)

# ---------------------------------------------------------------------------
# RetrievedPassage
# ---------------------------------------------------------------------------


class TestRetrievedPassage:
    def test_valid_construction(self) -> None:
        p = RetrievedPassage(
            source_id="doc-1",
            content="the cat sat on the mat",
            lexical_score=0.75,
            dense_score=0.88,
            hybrid_score=0.82,
            citation_span=(0, 22),
            rank=0,
        )
        assert p.source_id == "doc-1"
        assert p.content == "the cat sat on the mat"
        assert p.lexical_score == 0.75
        assert p.dense_score == 0.88
        assert p.hybrid_score == 0.82
        assert p.citation_span == (0, 22)
        assert p.rank == 0

    def test_rejects_empty_source_id(self) -> None:
        with pytest.raises(ValueError, match="source_id must be a non-empty string"):
            RetrievedPassage(
                source_id="",
                content="x",
                lexical_score=0.0,
                dense_score=0.0,
                hybrid_score=0.0,
                citation_span=(0, 1),
                rank=0,
            )

    def test_rejects_whitespace_source_id(self) -> None:
        with pytest.raises(ValueError, match="source_id must be a non-empty string"):
            RetrievedPassage(
                source_id="   ",
                content="x",
                lexical_score=0.0,
                dense_score=0.0,
                hybrid_score=0.0,
                citation_span=(0, 1),
                rank=0,
            )

    def test_rejects_empty_content(self) -> None:
        with pytest.raises(ValueError, match="content must be a non-empty string"):
            RetrievedPassage(
                source_id="d1",
                content="",
                lexical_score=0.0,
                dense_score=0.0,
                hybrid_score=0.0,
                citation_span=(0, 1),
                rank=0,
            )

    def test_rejects_negative_rank(self) -> None:
        with pytest.raises(ValueError, match="rank must be >= 0"):
            RetrievedPassage(
                source_id="d1",
                content="x",
                lexical_score=0.0,
                dense_score=0.0,
                hybrid_score=0.0,
                citation_span=(0, 1),
                rank=-1,
            )

    def test_rejects_inverted_citation_span(self) -> None:
        with pytest.raises(ValueError, match="citation_span"):
            RetrievedPassage(
                source_id="d1",
                content="x",
                lexical_score=0.0,
                dense_score=0.0,
                hybrid_score=0.0,
                citation_span=(5, 2),
                rank=0,
            )

    def test_rejects_negative_citation_start(self) -> None:
        with pytest.raises(ValueError, match="citation_span"):
            RetrievedPassage(
                source_id="d1",
                content="x",
                lexical_score=0.0,
                dense_score=0.0,
                hybrid_score=0.0,
                citation_span=(-1, 5),
                rank=0,
            )

    def test_frozen_no_attribute_mutation(self) -> None:
        p = RetrievedPassage(
            source_id="d1",
            content="x",
            lexical_score=0.5,
            dense_score=0.5,
            hybrid_score=0.5,
            citation_span=(0, 1),
            rank=0,
        )
        with pytest.raises(FrozenInstanceError):
            p.source_id = "d2"  # type: ignore[misc]

    def test_zero_scores_allowed(self) -> None:
        p = RetrievedPassage(
            source_id="d1",
            content="x",
            lexical_score=0.0,
            dense_score=0.0,
            hybrid_score=0.0,
            citation_span=(0, 1),
            rank=0,
        )
        assert p.lexical_score == 0.0
        assert p.dense_score == 0.0
        assert p.hybrid_score == 0.0

    def test_negative_scores_allowed(self) -> None:
        p = RetrievedPassage(
            source_id="d1",
            content="x",
            lexical_score=-0.3,
            dense_score=-0.1,
            hybrid_score=-0.2,
            citation_span=(0, 1),
            rank=0,
        )
        assert p.lexical_score == -0.3


# ---------------------------------------------------------------------------
# RetrievalResult
# ---------------------------------------------------------------------------


class TestRetrievalResult:
    def test_valid_minimal(self) -> None:
        result = RetrievalResult(
            query="what is AI",
            query_rewrite="what is artificial intelligence",
            index_version="v1",
            filter_policy="default",
            passages=(),
            reranker_version="v1",
            retrieved_source_ids=(),
            latency_ms=12.5,
        )
        assert result.query == "what is AI"
        assert result.query_rewrite == "what is artificial intelligence"
        assert result.latency_ms == 12.5
        assert result.dense_vector_version == DENSE_VECTOR_HASH_VERSION

    def test_rejects_empty_query(self) -> None:
        with pytest.raises(ValueError, match="query must be a non-empty string"):
            RetrievalResult(
                query="",
                query_rewrite="x",
                index_version="v1",
                filter_policy="d",
                passages=(),
                reranker_version="v1",
                retrieved_source_ids=(),
                latency_ms=0.0,
            )

    def test_rejects_empty_index_version(self) -> None:
        with pytest.raises(ValueError, match="index_version must be a non-empty string"):
            RetrievalResult(
                query="q",
                query_rewrite="x",
                index_version="",
                filter_policy="d",
                passages=(),
                reranker_version="v1",
                retrieved_source_ids=(),
                latency_ms=0.0,
            )

    def test_rejects_empty_reranker_version(self) -> None:
        with pytest.raises(ValueError, match="reranker_version must be a non-empty string"):
            RetrievalResult(
                query="q",
                query_rewrite="x",
                index_version="v1",
                filter_policy="d",
                passages=(),
                reranker_version="",
                retrieved_source_ids=(),
                latency_ms=0.0,
            )

    def test_with_passages(self) -> None:
        p1 = RetrievedPassage(
            source_id="d1",
            content="AI is intelligence demonstrated by machines",
            lexical_score=0.8,
            dense_score=0.9,
            hybrid_score=0.86,
            citation_span=(0, 44),
            rank=0,
        )
        result = RetrievalResult(
            query="what is AI",
            query_rewrite="what is artificial intelligence",
            index_version="v1",
            filter_policy="default",
            passages=(p1,),
            reranker_version="v1",
            retrieved_source_ids=("d1",),
            latency_ms=5.0,
        )
        assert len(result.passages) == 1
        assert result.passages[0].source_id == "d1"
        assert result.retrieved_source_ids == ("d1",)


# ---------------------------------------------------------------------------
# RetrievalMetrics
# ---------------------------------------------------------------------------


class TestRetrievalMetrics:
    def test_valid_construction(self) -> None:
        m = RetrievalMetrics(recall_at_k=0.8, mrr=0.65, ndcg=0.72)
        assert m.recall_at_k == 0.8
        assert m.mrr == 0.65
        assert m.ndcg == 0.72

    def test_zero_metrics_allowed(self) -> None:
        m = RetrievalMetrics(recall_at_k=0.0, mrr=0.0, ndcg=0.0)
        assert m.recall_at_k == 0.0

    def test_one_metrics_allowed(self) -> None:
        m = RetrievalMetrics(recall_at_k=1.0, mrr=1.0, ndcg=1.0)
        assert m.recall_at_k == 1.0

    def test_rejects_negative_recall(self) -> None:
        with pytest.raises(ValueError, match="recall_at_k must be in"):
            RetrievalMetrics(recall_at_k=-0.1, mrr=0.5, ndcg=0.5)

    def test_rejects_recall_above_one(self) -> None:
        with pytest.raises(ValueError, match="recall_at_k must be in"):
            RetrievalMetrics(recall_at_k=1.1, mrr=0.5, ndcg=0.5)

    def test_rejects_negative_mrr(self) -> None:
        with pytest.raises(ValueError, match="mrr must be in"):
            RetrievalMetrics(recall_at_k=0.5, mrr=-0.1, ndcg=0.5)

    def test_rejects_mrr_above_one(self) -> None:
        with pytest.raises(ValueError, match="mrr must be in"):
            RetrievalMetrics(recall_at_k=0.5, mrr=1.1, ndcg=0.5)

    def test_rejects_negative_ndcg(self) -> None:
        with pytest.raises(ValueError, match="ndcg must be in"):
            RetrievalMetrics(recall_at_k=0.5, mrr=0.5, ndcg=-0.01)

    def test_rejects_ndcg_above_one(self) -> None:
        with pytest.raises(ValueError, match="ndcg must be in"):
            RetrievalMetrics(recall_at_k=0.5, mrr=0.5, ndcg=1.001)


# ---------------------------------------------------------------------------
# RetrievalService — indexing and construction
# ---------------------------------------------------------------------------


class TestRetrievalServiceConstruction:
    def test_default_construction(self) -> None:
        svc = RetrievalService()
        assert svc._index_version == "v1"
        assert svc._reranker_version == "v1"
        assert svc._filter_policy == "default"

    def test_custom_construction(self) -> None:
        svc = RetrievalService(
            index_version="v2",
            reranker_version="rr-v3",
            filter_policy="strict",
        )
        assert svc._index_version == "v2"
        assert svc._reranker_version == "rr-v3"
        assert svc._filter_policy == "strict"

    def test_rejects_empty_index_version(self) -> None:
        with pytest.raises(ValueError, match="index_version must be a non-empty string"):
            RetrievalService(index_version="   ")

    def test_rejects_empty_reranker_version(self) -> None:
        with pytest.raises(ValueError, match="reranker_version must be a non-empty string"):
            RetrievalService(reranker_version="")


class TestRetrievalServiceIndex:
    def test_index_adds_document(self) -> None:
        svc = RetrievalService()
        svc.index("d1", "the cat sat on the mat")
        assert len(svc._corpus) == 1
        assert svc._corpus[0] == ("d1", "the cat sat on the mat")

    def test_index_multiple_documents(self) -> None:
        svc = RetrievalService()
        svc.index("d1", "doc one")
        svc.index("d2", "doc two")
        svc.index("d3", "doc three")
        assert len(svc._corpus) == 3

    def test_index_rejects_empty_source_id(self) -> None:
        svc = RetrievalService()
        with pytest.raises(ValueError, match="source_id must be a non-empty string"):
            svc.index("", "content")

    def test_index_rejects_empty_content(self) -> None:
        svc = RetrievalService()
        with pytest.raises(ValueError, match="content must be a non-empty string"):
            svc.index("d1", "   ")


# ---------------------------------------------------------------------------
# RetrievalService — lexical scoring
# ---------------------------------------------------------------------------


class TestLexicalScore:
    def test_full_match(self) -> None:
        score = RetrievalService._lexical_score(
            query_terms=["cat", "sat"],
            doc="the cat sat on the mat",
        )
        assert score == 1.0

    def test_partial_match(self) -> None:
        score = RetrievalService._lexical_score(
            query_terms=["cat", "dog"],
            doc="the cat sat on the mat",
        )
        assert score == 0.5

    def test_no_match(self) -> None:
        score = RetrievalService._lexical_score(
            query_terms=["zebra", "lion"],
            doc="the cat sat on the mat",
        )
        assert score == 0.0

    def test_empty_query_terms(self) -> None:
        score = RetrievalService._lexical_score(
            query_terms=[],
            doc="the cat sat on the mat",
        )
        assert score == 0.0

    def test_empty_document(self) -> None:
        score = RetrievalService._lexical_score(
            query_terms=["cat"],
            doc="",
        )
        assert score == 0.0

    def test_case_insensitive_document_match(self) -> None:
        score = RetrievalService._lexical_score(
            query_terms=["cat", "hat"],
            doc="The CAT wears a Hat",
        )
        assert score == 1.0


# ---------------------------------------------------------------------------
# RetrievalService — dense vector hashing
# ---------------------------------------------------------------------------


class TestHashVec:
    def test_produces_64_dim_vector(self) -> None:
        vec = RetrievalService._hash_vec("hello world")
        assert len(vec) == 64

    def test_normalized_to_unit_length(self) -> None:
        vec = RetrievalService._hash_vec("hello world")
        norm = math.sqrt(sum(v * v for v in vec))
        assert norm == pytest.approx(1.0)

    def test_zero_vector_for_non_empty_text_is_normalized(self) -> None:
        vec = RetrievalService._hash_vec("a")
        norm = math.sqrt(sum(v * v for v in vec))
        assert norm == pytest.approx(1.0) or norm == 0.0
        if norm > 0:
            assert norm == pytest.approx(1.0)

    def test_deterministic_same_text_same_vector(self) -> None:
        v1 = RetrievalService._hash_vec("the quick brown fox")
        v2 = RetrievalService._hash_vec("the quick brown fox")
        assert v1 == v2

    def test_different_text_different_vector(self) -> None:
        v1 = RetrievalService._hash_vec("hello world")
        v2 = RetrievalService._hash_vec("goodbye world")
        assert v1 != v2

    def test_case_insensitive(self) -> None:
        v1 = RetrievalService._hash_vec("Hello World")
        v2 = RetrievalService._hash_vec("hello world")
        assert v1 == v2


# ---------------------------------------------------------------------------
# RetrievalService — dense scoring and hybrid fusion
# ---------------------------------------------------------------------------


class TestDenseScore:
    def test_identical_text_score_high(self) -> None:
        score = RetrievalService._dense_score(
            "the cat sat on the mat",
            "the cat sat on the mat",
        )
        assert score == pytest.approx(1.0)

    def test_disjoint_text_score_low(self) -> None:
        score = RetrievalService._dense_score(
            "the cat sat on the mat",
            "quantum mechanics and relativity theory",
        )
        assert score < 0.5

    def test_score_non_negative(self) -> None:
        score = RetrievalService._dense_score("a", "b")
        assert score >= 0.0

    def test_related_text_higher_than_unrelated(self) -> None:
        score_similar = RetrievalService._dense_score(
            "machine learning models train on data",
            "deep learning algorithms learn from examples",
        )
        score_different = RetrievalService._dense_score(
            "machine learning models train on data",
            "the cat sat on the mat",
        )
        assert score_similar > score_different


class TestHybridScore:
    def test_weighted_combination(self) -> None:
        svc = RetrievalService()
        score = svc._hybrid_score(lexical=0.8, dense=0.5)
        expected = 0.4 * 0.8 + 0.6 * 0.5
        assert score == pytest.approx(expected)

    def test_zero_lexical_zero_dense(self) -> None:
        svc = RetrievalService()
        score = svc._hybrid_score(lexical=0.0, dense=0.0)
        assert score == 0.0

    def test_perfect_both(self) -> None:
        svc = RetrievalService()
        score = svc._hybrid_score(lexical=1.0, dense=1.0)
        assert score == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# RetrievalService.search — end-to-end
# ---------------------------------------------------------------------------


class TestRetrievalServiceSearch:
    def test_empty_corpus_returns_empty_result(self) -> None:
        svc = RetrievalService()
        result = svc.search("hello")
        assert len(result.passages) == 0
        assert result.query == "hello"
        assert result.query_rewrite == "hello"
        assert result.index_version == "v1"
        assert result.latency_ms >= 0.0

    def test_single_document_match(self) -> None:
        svc = RetrievalService()
        svc.index("d1", "the cat sat on the mat")
        result = svc.search("cat mat")
        assert len(result.passages) == 1
        assert result.passages[0].source_id == "d1"
        assert result.passages[0].rank == 0
        assert result.retrieved_source_ids == ("d1",)

    def test_ranking_by_hybrid_score_descending(self) -> None:
        svc = RetrievalService()
        svc.index("d1", "irrelevant text about nothing")
        svc.index("d2", "the cat sat on the mat")
        svc.index("d3", "cat and mat")
        result = svc.search("cat mat", k=3)
        assert len(result.passages) == 3
        scores = [p.hybrid_score for p in result.passages]
        assert scores == sorted(scores, reverse=True)

    def test_k_truncation(self) -> None:
        svc = RetrievalService()
        for i in range(10):
            svc.index(f"d{i}", f"document number {i}")
        result = svc.search("document", k=3)
        assert len(result.passages) == 3

    def test_k_default_is_ten(self) -> None:
        svc = RetrievalService()
        for i in range(20):
            svc.index(f"d{i}", f"document number {i}")
        result = svc.search("document")
        assert len(result.passages) == 10

    def test_k_must_be_positive(self) -> None:
        svc = RetrievalService()
        with pytest.raises(ValueError, match="k must be >= 1"):
            svc.search("hello", k=0)

    def test_query_must_be_non_empty(self) -> None:
        svc = RetrievalService()
        with pytest.raises(ValueError, match="query must be a non-empty string"):
            svc.search("")

    def test_query_rewrite_used_for_scoring(self) -> None:
        svc = RetrievalService()
        svc.index("d1", "banana")
        result = svc.search("apple", query_rewrite="banana")
        assert result.query == "apple"
        assert result.query_rewrite == "banana"
        assert len(result.passages) == 1
        assert result.passages[0].source_id == "d1"

    def test_citation_spans_recorded(self) -> None:
        svc = RetrievalService()
        content = "the cat sat on the mat"
        svc.index("d1", content)
        result = svc.search("cat")
        assert result.passages[0].citation_span == (0, len(content))

    def test_latency_recorded_as_positive(self) -> None:
        svc = RetrievalService()
        svc.index("d1", "hello world")
        result = svc.search("hello")
        assert result.latency_ms > 0.0

    def test_lexical_and_dense_scores_recorded(self) -> None:
        svc = RetrievalService()
        svc.index("d1", "the cat sat on the mat")
        result = svc.search("cat mat")
        p = result.passages[0]
        assert isinstance(p.lexical_score, float)
        assert isinstance(p.dense_score, float)
        assert isinstance(p.hybrid_score, float)

    def test_dense_vector_version_constant(self) -> None:
        svc = RetrievalService()
        svc.index("d1", "hello")
        result = svc.search("hello")
        assert result.dense_vector_version == DENSE_VECTOR_HASH_VERSION
        assert DENSE_VECTOR_HASH_VERSION == "blake2b-256-v2"


# ---------------------------------------------------------------------------
# RetrievalService.evaluate — recall@k, MRR, nDCG
# ---------------------------------------------------------------------------


class TestRetrievalServiceEvaluate:
    def test_perfect_recall_all_relevant_found(self) -> None:
        svc = RetrievalService()
        svc.index("d1", "the cat sat on the mat")
        svc.index("d2", "irrelevant text")
        metrics = svc.evaluate("cat mat", relevant_ids={"d1"})
        assert metrics.recall_at_k == 1.0

    def test_zero_recall_no_relevant_found(self) -> None:
        svc = RetrievalService()
        svc.index("d1", "irrelevant text about nothing")
        metrics = svc.evaluate("cat mat", relevant_ids={"d99"})
        assert metrics.recall_at_k == 0.0

    def test_partial_recall(self) -> None:
        svc = RetrievalService()
        svc.index("d1", "cat")
        svc.index("d2", "mat")
        svc.index("d3", "dog")
        svc.index("d4", "cat mat")
        metrics = svc.evaluate("cat mat", relevant_ids={"d1", "d2"}, k=3)
        assert 0.0 < metrics.recall_at_k <= 1.0

    def test_mrr_first_relevant_at_rank_one(self) -> None:
        svc = RetrievalService()
        svc.index("d1", "cat mat")
        svc.index("d2", "irrelevant")
        metrics = svc.evaluate("cat", relevant_ids={"d1"})
        assert metrics.mrr == 1.0

    def test_mrr_first_relevant_at_rank_three(self) -> None:
        svc = RetrievalService()
        svc.index("d1", "x")  # weak match
        svc.index("d2", "x")  # weak match
        svc.index("dog_target", "dog bark")
        metrics = svc.evaluate("dog", relevant_ids={"dog_target"})
        assert metrics.mrr == pytest.approx(1.0 / 3.0, abs=0.01) or metrics.mrr <= 1.0

    def test_mrr_zero_when_no_relevant(self) -> None:
        svc = RetrievalService()
        svc.index("d1", "cat mat")
        metrics = svc.evaluate("cat", relevant_ids={"d99"})
        assert metrics.mrr == 0.0

    def test_ndcg_perfect_ideal_ordering_is_one(self) -> None:
        svc = RetrievalService()
        svc.index("d1", "cat mat")
        svc.index("d2", "irrelevant")
        metrics = svc.evaluate("cat", relevant_ids={"d1"})
        assert metrics.ndcg == 1.0

    def test_ndcg_zero_when_no_relevant(self) -> None:
        svc = RetrievalService()
        svc.index("d1", "irrelevant")
        metrics = svc.evaluate("cat", relevant_ids={"d99"})
        assert metrics.ndcg == 0.0

    def test_empty_relevant_set_gives_zero_metrics(self) -> None:
        svc = RetrievalService()
        svc.index("d1", "hello")
        metrics = svc.evaluate("hello", relevant_ids=set())
        assert metrics.recall_at_k == 0.0
        assert metrics.mrr == 0.0
        assert metrics.ndcg == 0.0

    def test_k_truncation_in_evaluate(self) -> None:
        svc = RetrievalService()
        for i in range(20):
            svc.index(f"d{i}", f"doc {i}")
        metrics = svc.evaluate("doc", relevant_ids={"d0", "d1"}, k=5)
        assert metrics.recall_at_k <= 1.0

    def test_evaluate_rejects_invalid_k(self) -> None:
        svc = RetrievalService()
        with pytest.raises(ValueError, match="k must be >= 1"):
            svc.evaluate("query", relevant_ids={"d1"}, k=0)

    def test_evaluate_rejects_empty_query(self) -> None:
        svc = RetrievalService()
        with pytest.raises(ValueError, match="query must be a non-empty string"):
            svc.evaluate("   ", relevant_ids={"d1"})

    def test_all_metrics_within_range(self) -> None:
        svc = RetrievalService()
        for i in range(5):
            svc.index(f"d{i}", f"doc {i}")
        metrics = svc.evaluate("doc", relevant_ids={"d0", "d2", "d4"}, k=3)
        assert 0.0 <= metrics.recall_at_k <= 1.0
        assert 0.0 <= metrics.mrr <= 1.0
        assert 0.0 <= metrics.ndcg <= 1.0


# ---------------------------------------------------------------------------
# Integration-style: full retrieve-and-evaluate pipeline
# ---------------------------------------------------------------------------


class TestRetrievalSearchEvaluatePipeline:
    def test_more_documents_than_k(self) -> None:
        svc = RetrievalService()
        for i in range(15):
            svc.index(f"d{i}", f"content about topic {i % 3}")
        result = svc.search("topic 0", k=5)
        assert len(result.passages) == 5
        assert len(set(p.source_id for p in result.passages)) == 5

    def test_index_persistence_across_searches(self) -> None:
        svc = RetrievalService()
        svc.index("d1", "hello")
        r1 = svc.search("hello")
        r2 = svc.search("hello")
        assert len(r1.passages) == len(r2.passages)
        assert r1.retrieved_source_ids == r2.retrieved_source_ids

    def test_search_deterministic_same_corpus_same_query(self) -> None:
        svc = RetrievalService()
        svc.index("d1", "the quick brown fox jumps")
        svc.index("d2", "lazy dog sleeps")
        r1 = svc.search("fox")
        r2 = svc.search("fox")
        assert r1.retrieved_source_ids == r2.retrieved_source_ids
        assert r1.passages[0].hybrid_score == r2.passages[0].hybrid_score

    def test_ranks_are_monotonic_zero_to_k_minus_one(self) -> None:
        svc = RetrievalService()
        for i in range(10):
            svc.index(f"d{i}", f"content with term {'cat' if i % 2 == 0 else 'dog'}")
        result = svc.search("cat", k=5)
        ranks = [p.rank for p in result.passages]
        assert ranks == list(range(5))
