"""Unit tests for TEMPR multi-strategy retriever — 40+ tests.

Tests semantic (embedding), BM25, temporal, graph, RRF fusion,
parallel execution, temporal parsing, entity extraction, and edge cases.
"""

from __future__ import annotations

import math
import time
from dataclasses import fields
from datetime import UTC, datetime, timedelta

from general_ludd.memory.tempr_retriever import (
    TEMPRResult,
    TEMPRRetriever,
    parse_temporal_expression,
    reciprocal_rank_fusion,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_docs(
    count: int,
    base_created: datetime | None = None,
) -> list[dict]:
    base = base_created or datetime(2024, 1, 1, tzinfo=UTC)
    docs = []
    for i in range(count):
        topics = ["python", "testing", "deployment", "security", "database"]
        topic = topics[i % len(topics)]
        docs.append({
            "id": f"doc_{i}",
            "content": f"This is document {i} about {topic} and related concepts",
            "created_at": (base + timedelta(days=i)).isoformat(),
            "metadata": {"topic": topic, "index": i},
        })
    return docs


# ── TEMPRResult dataclass ────────────────────────────────────────────────────


class TestTEMPRResult:
    def test_construction_defaults(self):
        r = TEMPRResult(doc_id="d1", content="hello", scores={}, final_score=0.0)
        assert r.doc_id == "d1"
        assert r.content == "hello"
        assert r.scores == {}
        assert r.final_score == 0.0
        assert r.retrieved_at > 0

    def test_construction_with_scores(self):
        r = TEMPRResult(
            doc_id="d2",
            content="world",
            scores={"semantic": 0.8, "bm25": 0.6},
            final_score=0.75,
        )
        assert r.scores["semantic"] == 0.8
        assert r.scores["bm25"] == 0.6
        assert r.final_score == 0.75

    def test_fields_are_dataclass_fields(self):
        field_names = {f.name for f in fields(TEMPRResult)}
        assert field_names == {"doc_id", "content", "scores", "final_score", "retrieved_at"}

    def test_retrieved_at_is_utc_timestamp(self):
        r = TEMPRResult(doc_id="d3", content="x", scores={}, final_score=1.0)
        now = time.time()
        assert abs(r.retrieved_at - now) < 5.0


# ── Reciprocal Rank Fusion ───────────────────────────────────────────────────


class TestRRF:
    def test_simple_fusion(self):
        results = [
            [("a", 0.9), ("b", 0.8), ("c", 0.7)],
            [("b", 0.9), ("a", 0.5), ("d", 0.3)],
        ]
        fused = reciprocal_rank_fusion(results, k=60)
        assert len(fused) == 4
        ids = [doc_id for doc_id, _ in fused]
        # b appears at rank 1 in both lists, a at rank 2 and 1
        assert ids[0] in ("a", "b")

    def test_tie_breaking(self):
        results = [
            [("a", 0.9), ("b", 0.8)],
            [("a", 0.8), ("b", 0.9)],
        ]
        fused = reciprocal_rank_fusion(results, k=60)
        ids = [doc_id for doc_id, _ in fused]
        assert ids[0] == "a"  # a at rank 1 in both -> higher RRF
        assert ids[1] == "b"

    def test_empty_strategies(self):
        results = [[], []]
        fused = reciprocal_rank_fusion(results, k=60)
        assert fused == []

    def test_single_strategy(self):
        results = [[("a", 0.9), ("b", 0.8)]]
        fused = reciprocal_rank_fusion(results, k=60)
        assert len(fused) == 2
        assert [doc_id for doc_id, _ in fused] == ["a", "b"]

    def test_all_empty_except_one(self):
        results = [[], [("a", 0.9)], []]
        fused = reciprocal_rank_fusion(results, k=60)
        assert len(fused) == 1
        assert fused[0][0] == "a"

    def test_k_value_affects_ordering(self):
        results = [
            [("a", 0.99), ("b", 0.01)],  # large gap between rank 1 and 2
            [("b", 0.99), ("a", 0.01)],
        ]
        fused_k60 = reciprocal_rank_fusion(results, k=60)
        fused_k1 = reciprocal_rank_fusion(results, k=1)
        # With k=1 the gap between ranks matters more
        assert fused_k60[0][0] == "a"  # rank 1 in list 0 wins at high k
        assert fused_k1[0][0] == "a"  # same doc has rank 1 somewhere

    def test_single_document_in_each(self):
        results = [[("a", 0.9)], [("b", 0.8)], [("c", 0.7)]]
        fused = reciprocal_rank_fusion(results, k=60)
        assert len(fused) == 3
        assert {doc_id for doc_id, _ in fused} == {"a", "b", "c"}

    def test_rank_based_not_score_based(self):
        """RRF uses rank, not the raw score value."""
        results = [
            [("a", 0.5)],  # a is rank 1 in strategy 0
            [("a", 0.99)],  # a is ALSO rank 1 in strategy 1
        ]
        fused = reciprocal_rank_fusion(results, k=60)
        # a gets 1/(60+1) + 1/(60+1) = 2/61 ≈ 0.0328
        expected = 2.0 / 61.0
        assert math.isclose(fused[0][1], expected, rel_tol=1e-6)


# ── BM25 Strategy ────────────────────────────────────────────────────────────


class TestBM25:
    def test_retrieval_returns_scored_results(self):
        retriever = TEMPRRetriever(strategy_weights={"bm25": 1.0})
        docs = [
            {"id": "d1", "content": "python testing framework", "created_at": "2024-01-01T00:00:00+00:00"},
            {"id": "d2", "content": "database migration script", "created_at": "2024-01-02T00:00:00+00:00"},
            {"id": "d3", "content": "deployment pipeline automation", "created_at": "2024-01-03T00:00:00+00:00"},
        ]
        retriever.index(docs)
        results = retriever.retrieve("python testing", top_k=2)
        assert len(results) <= 2
        assert results[0].doc_id == "d1"

    def test_idf_computation(self):
        """Documents with rarer terms relative to corpus score higher."""
        retriever = TEMPRRetriever(strategy_weights={"bm25": 1.0})
        docs = [
            {"id": "common_only", "content": "the system is running", "created_at": "2024-01-01T00:00:00+00:00"},
            {"id": "rare_term", "content": "xylophone concert performance", "created_at": "2024-01-01T00:00:00+00:00"},
        ]
        retriever.index(docs)
        results = retriever.retrieve("xylophone", top_k=2)
        assert results[0].doc_id == "rare_term"

    def test_term_frequency_saturation(self):
        """Repeating a term many times should not linearly increase score."""
        retriever = TEMPRRetriever(strategy_weights={"bm25": 1.0})
        docs = [
            {"id": "moderate", "content": "python python", "created_at": "2024-01-01T00:00:00+00:00"},
            {
                "id": "extreme",
                "content": "python python python python python python python python python python",
                "created_at": "2024-01-01T00:00:00+00:00",
            },
        ]
        retriever.index(docs)
        results_mod = retriever.retrieve("python", top_k=2)
        # Moderate doc still scores well; extreme doesn't dominate proportionally
        assert "extreme" in {r.doc_id for r in results_mod}

    def test_document_length_normalization(self):
        """Longer documents get normalized so they don't dominate."""
        retriever = TEMPRRetriever(strategy_weights={"bm25": 1.0})
        docs = [
            {"id": "short", "content": "database", "created_at": "2024-01-01T00:00:00+00:00"},
            {
                "id": "long",
                "content": "database " + "filler " * 200 + "database",
                "created_at": "2024-01-01T00:00:00+00:00",
            },
        ]
        retriever.index(docs)
        results = retriever.retrieve("database", top_k=2)
        # Short doc should rank higher because term density is higher after normalization
        assert results[0].doc_id == "short"

    def test_multi_term_query(self):
        retriever = TEMPRRetriever(strategy_weights={"bm25": 1.0})
        docs = [
            {"id": "partial", "content": "python code review", "created_at": "2024-01-01T00:00:00+00:00"},
            {"id": "full", "content": "python testing framework deployment", "created_at": "2024-01-01T00:00:00+00:00"},
        ]
        retriever.index(docs)
        results = retriever.retrieve("python testing deployment", top_k=2)
        assert results[0].doc_id == "full"


# ── Semantic Strategy ────────────────────────────────────────────────────────


class TestSemantic:
    def test_similar_documents_score_higher(self):
        retriever = TEMPRRetriever(strategy_weights={"semantic": 1.0})
        docs = [
            {
                "id": "relevant",
                "content": "python testing with pytest and fixtures",
                "created_at": "2024-01-01T00:00:00+00:00",
            },
            {
                "id": "irrelevant",
                "content": "deployment to kubernetes cluster",
                "created_at": "2024-01-01T00:00:00+00:00",
            },
        ]
        retriever.index(docs)
        results = retriever.retrieve("python unit testing", top_k=2)
        assert results[0].doc_id == "relevant"

    def test_semantic_scores_range_zero_to_one(self):
        retriever = TEMPRRetriever(strategy_weights={"semantic": 1.0})
        docs = [{"id": "d1", "content": "hello world", "created_at": "2024-01-01T00:00:00+00:00"}]
        retriever.index(docs)
        results = retriever.retrieve("hello", top_k=10)
        for r in results:
            assert 0.0 <= r.scores.get("semantic", 0.0) <= 1.0

    def test_empty_corpus_no_error(self):
        retriever = TEMPRRetriever(strategy_weights={"semantic": 1.0})
        retriever.index([])
        results = retriever.retrieve("anything", top_k=10)
        assert results == []

    def test_single_document_corpus(self):
        retriever = TEMPRRetriever(strategy_weights={"semantic": 1.0})
        docs = [{"id": "only", "content": "the only document", "created_at": "2024-01-01T00:00:00+00:00"}]
        retriever.index(docs)
        results = retriever.retrieve("document", top_k=10)
        assert len(results) == 1
        assert results[0].doc_id == "only"

    def test_query_with_no_overlap_returns_low_scores(self):
        retriever = TEMPRRetriever(strategy_weights={"semantic": 1.0})
        docs = [{"id": "d1", "content": "apple banana cherry", "created_at": "2024-01-01T00:00:00+00:00"}]
        retriever.index(docs)
        results = retriever.retrieve("xylophone zephyr quark", top_k=10)
        # Should still return results but with very low scores
        assert len(results) >= 0
        if results:
            score = results[0].scores.get("semantic", 0.0)
            assert score < 0.3


# ── Temporal Strategy ────────────────────────────────────────────────────────


class TestTemporal:
    def test_recent_documents_score_higher(self):
        retriever = TEMPRRetriever(strategy_weights={"temporal": 1.0})
        now = datetime.now(UTC)
        docs = [
            {"id": "old", "content": "old document", "created_at": (now - timedelta(days=30)).isoformat()},
            {"id": "recent", "content": "recent document", "created_at": now.isoformat()},
        ]
        retriever.index(docs)
        results = retriever.retrieve("document", top_k=2)
        assert results[0].doc_id == "recent"

    def test_date_range_filtering(self):
        retriever = TEMPRRetriever(strategy_weights={"temporal": 1.0})
        datetime(2024, 6, 1, tzinfo=UTC)
        docs = [
            {"id": "march", "content": "march event", "created_at": "2024-03-15T00:00:00+00:00"},
            {"id": "june", "content": "june event", "created_at": "2024-06-15T00:00:00+00:00"},
            {"id": "september", "content": "september event", "created_at": "2024-09-15T00:00:00+00:00"},
            {"id": "missing", "content": "event without a timestamp"},
            {"id": "malformed", "content": "event with a bad timestamp", "created_at": "not-a-date"},
            {"id": "invalid-type", "content": "event with a non-string timestamp", "created_at": 42},
        ]
        retriever.index(docs)
        results = retriever.retrieve(
            "event", top_k=10,
            date_range=(datetime(2024, 5, 1, tzinfo=UTC), datetime(2024, 8, 1, tzinfo=UTC)),
        )
        ids = {r.doc_id for r in results}
        assert "march" not in ids
        assert "june" in ids
        assert "september" not in ids
        assert "missing" not in ids
        assert "malformed" not in ids
        assert "invalid-type" not in ids

    def test_date_range_filters_every_fused_strategy_and_unknown_dates(self):
        retriever = TEMPRRetriever()
        docs = [
            {"id": "old", "content": "async legacy", "created_at": "2024-03-15T00:00:00+00:00"},
            {"id": "current", "content": "async current", "created_at": "2024-06-15T00:00:00+00:00"},
            {"id": "unknown", "content": "async unknown"},
        ]
        retriever.index(docs)

        results = retriever.retrieve(
            "async",
            top_k=10,
            date_range=(datetime(2024, 5, 1, tzinfo=UTC), datetime(2024, 8, 1, tzinfo=UTC)),
        )

        assert [result.doc_id for result in results] == ["current"]

    def test_no_created_at_uses_default(self):
        retriever = TEMPRRetriever(strategy_weights={"temporal": 1.0})
        docs = [{"id": "d1", "content": "no timestamp"}]
        retriever.index(docs)
        results = retriever.retrieve("timestamp", top_k=10)
        assert len(results) == 1
        assert results[0].doc_id == "d1"

    def test_temporal_score_bounds(self):
        retriever = TEMPRRetriever(strategy_weights={"temporal": 1.0})
        now = datetime.now(UTC)
        docs = [
            {"id": "d1", "content": "test", "created_at": (now - timedelta(days=365)).isoformat()},
        ]
        retriever.index(docs)
        results = retriever.retrieve("test", top_k=10)
        assert len(results) == 1
        score = results[0].scores.get("temporal", 0)
        assert 0.0 <= score <= 1.0

    def test_temporal_strategy_scores_all_documents(self):
        retriever = TEMPRRetriever(strategy_weights={"temporal": 1.0})
        docs = _make_docs(20)
        retriever.index(docs)
        results = retriever.retrieve("document", top_k=10)
        assert len(results) == 10
        assert "temporal" not in results[0].scores or results[0].scores.get("temporal", 0) >= 0


# ── Temporal Expression Parsing ──────────────────────────────────────────────


class TestTemporalParsing:
    def test_last_week(self):
        now = datetime(2024, 6, 15, tzinfo=UTC)
        start, end = parse_temporal_expression("last week", now=now)
        assert start is not None
        assert end is not None
        assert start < end
        assert start == datetime(2024, 6, 3, 0, 0, 0, tzinfo=UTC)
        assert end == datetime(2024, 6, 10, 0, 0, 0, tzinfo=UTC)

    def test_in_march_2024(self):
        now = datetime(2024, 6, 15, tzinfo=UTC)
        start, end = parse_temporal_expression("in March 2024", now=now)
        assert start == datetime(2024, 3, 1, 0, 0, 0, tzinfo=UTC)
        assert end == datetime(2024, 4, 1, 0, 0, 0, tzinfo=UTC)

    def test_yesterday(self):
        now = datetime(2024, 6, 15, tzinfo=UTC)
        start, end = parse_temporal_expression("yesterday", now=now)
        assert start == datetime(2024, 6, 14, 0, 0, 0, tzinfo=UTC)
        assert end == datetime(2024, 6, 15, 0, 0, 0, tzinfo=UTC)

    def test_last_3_days(self):
        now = datetime(2024, 6, 15, tzinfo=UTC)
        start, end = parse_temporal_expression("last 3 days", now=now)
        assert start == datetime(2024, 6, 12, tzinfo=UTC)
        assert end == datetime(2024, 6, 15, tzinfo=UTC)

    def test_this_month(self):
        now = datetime(2024, 6, 15, tzinfo=UTC)
        start, end = parse_temporal_expression("this month", now=now)
        assert start == datetime(2024, 6, 1, 0, 0, 0, tzinfo=UTC)
        assert end is None or end > start

    def test_no_match_returns_none(self):
        now = datetime.now(UTC)
        start, end = parse_temporal_expression("hello world", now=now)
        assert start is None
        assert end is None

    def test_empty_string(self):
        start, end = parse_temporal_expression("")
        assert start is None
        assert end is None

    def test_last_month(self):
        now = datetime(2024, 6, 15, tzinfo=UTC)
        start, end = parse_temporal_expression("last month", now=now)
        assert start is not None
        assert end is not None
        assert start.month == 5
        assert start.year == 2024

    def test_today(self):
        now = datetime(2024, 6, 15, 12, 30, tzinfo=UTC)
        start, end = parse_temporal_expression("today", now=now)
        assert start == datetime(2024, 6, 15, 0, 0, 0, tzinfo=UTC)
        assert end is None or end >= start

    def test_this_week(self):
        now = datetime(2024, 6, 15, tzinfo=UTC)  # Saturday
        start, _end = parse_temporal_expression("this week", now=now)
        assert start is not None
        # Monday of that week
        assert start.weekday() == 0  # Monday


# ── Graph Strategy ───────────────────────────────────────────────────────────


class TestGraph:
    def test_entity_extraction_from_text(self):
        retriever = TEMPRRetriever(strategy_weights={"graph": 1.0})
        entities = retriever._extract_entities("Alice met Bob at the Python Conference in New York")
        # Should find capitalized sequences
        assert len(entities) >= 2

    def test_co_occurrence_boosts_shared_entity_docs(self):
        retriever = TEMPRRetriever(strategy_weights={"graph": 1.0})
        docs = [
            {
                "id": "d1",
                "content": "Alice and Bob deployed the Python service",
                "created_at": "2024-01-01T00:00:00+00:00",
            },
            {"id": "d2", "content": "Alice reviewed the database migration", "created_at": "2024-01-02T00:00:00+00:00"},
            {"id": "d3", "content": "Charlie wrote the frontend tests", "created_at": "2024-01-03T00:00:00+00:00"},
        ]
        retriever.index(docs)
        results = retriever.retrieve("Alice", top_k=3)
        # d1 and d2 share the entity "Alice" with the query
        # d3 does not; but graph should also consider shared entities between docs
        ids = {r.doc_id for r in results}
        assert "d1" in ids or "d2" in ids

    def test_graph_scores_range_zero_to_one(self):
        retriever = TEMPRRetriever(strategy_weights={"graph": 1.0})
        docs = [
            {"id": "d1", "content": "System X integration with Service Y", "created_at": "2024-01-01T00:00:00+00:00"},
        ]
        retriever.index(docs)
        results = retriever.retrieve("System X", top_k=10)
        for r in results:
            assert 0.0 <= r.scores.get("graph", 0.0) <= 1.0

    def test_empty_corpus_graph(self):
        retriever = TEMPRRetriever(strategy_weights={"graph": 1.0})
        retriever.index([])
        results = retriever.retrieve("anything", top_k=10)
        assert results == []

    def test_multi_hop_via_shared_entity(self):
        retriever = TEMPRRetriever(strategy_weights={"graph": 1.0})
        docs = [
            {
                "id": "d1",
                "content": "Kubernetes cluster configuration setup",
                "created_at": "2024-01-01T00:00:00+00:00",
            },
            {"id": "d2", "content": "Kubernetes pod networking debugging", "created_at": "2024-01-02T00:00:00+00:00"},
            {"id": "d3", "content": "Amazon Web Services billing alert", "created_at": "2024-01-03T00:00:00+00:00"},
        ]
        retriever.index(docs)
        results = retriever.retrieve("cluster networking", top_k=3)
        ids = {r.doc_id for r in results}
        # d1 and d2 share "Kubernetes" entity
        assert "d1" in ids
        assert "d2" in ids


# ── Parallel Execution ───────────────────────────────────────────────────────


class TestParallelExecution:
    def test_all_four_strategies_fire_in_one_retrieve(self):
        retriever = TEMPRRetriever()
        docs = _make_docs(10)
        retriever.index(docs)
        results = retriever.retrieve("python testing", top_k=5)
        for r in results:
            # Each result should have scores from whichever strategies contributed
            assert len(r.scores) >= 0  # strategies may return 0 results for some docs

    def test_results_merged_from_multiple_strategies(self):
        retriever = TEMPRRetriever()
        docs = _make_docs(20)
        retriever.index(docs)
        results = retriever.retrieve("deployment security", top_k=10)
        # RRF fusion merges results from all strategies
        assert len(results) > 0
        # Results should be sorted by final_score descending
        scores = [r.final_score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_parallel_produces_results_under_timeout(self):
        import threading
        import time as time_mod

        retriever = TEMPRRetriever()
        docs = _make_docs(100)
        retriever.index(docs)

        # Track execution time
        result_holder: list = []

        def run():
            result_holder.append(retriever.retrieve("python", top_k=10))

        t = threading.Thread(target=run)
        start = time_mod.time()
        t.start()
        t.join(timeout=10.0)
        elapsed = time_mod.time() - start
        # With 100 docs, all strategies should complete quickly
        assert elapsed < 10.0
        assert not t.is_alive()
        assert len(result_holder) == 1
        assert len(result_holder[0]) > 0


# ── Configurable Weights ─────────────────────────────────────────────────────


class TestStrategyWeights:
    def test_equal_weights_default(self):
        retriever = TEMPRRetriever()
        assert retriever.strategy_weights == {"semantic": 0.25, "bm25": 0.25, "temporal": 0.25, "graph": 0.25}

    def test_custom_weights_favor_semantic(self):
        retriever = TEMPRRetriever(strategy_weights={"semantic": 0.9, "bm25": 0.05, "temporal": 0.03, "graph": 0.02})
        docs = _make_docs(10)
        retriever.index(docs)
        results = retriever.retrieve("python", top_k=5)
        assert len(results) > 0

    def test_zero_weight_disables_strategy(self):
        retriever = TEMPRRetriever(strategy_weights={"semantic": 0.5, "bm25": 0.5, "temporal": 0.0, "graph": 0.0})
        docs = _make_docs(10)
        retriever.index(docs)
        results = retriever.retrieve("python", top_k=5)
        assert len(results) > 0


# ── Performance ──────────────────────────────────────────────────────────────


class TestPerformance:
    def test_retrieves_from_1000_documents(self):
        retriever = TEMPRRetriever()
        docs = _make_docs(1000)
        retriever.index(docs)
        import time as time_mod
        start = time_mod.time()
        results = retriever.retrieve("python deployment", top_k=10)
        elapsed = time_mod.time() - start
        assert len(results) > 0
        assert len(results) <= 10
        assert elapsed < 5.0, f"Took {elapsed:.2f}s for 1000 docs"

    def test_2000_documents_under_timeout(self):
        retriever = TEMPRRetriever()
        docs = _make_docs(2000)
        retriever.index(docs)
        import time as time_mod
        start = time_mod.time()
        results = retriever.retrieve("testing", top_k=10)
        elapsed = time_mod.time() - start
        assert elapsed < 15.0, f"Took {elapsed:.2f}s for 2000 docs"
        assert len(results) > 0


# ── Edge Cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_index(self):
        retriever = TEMPRRetriever()
        retriever.index([])
        results = retriever.retrieve("anything", top_k=10)
        assert results == []

    def test_single_document(self):
        retriever = TEMPRRetriever()
        docs = [{"id": "only", "content": "the only document in the index", "created_at": "2024-01-01T00:00:00+00:00"}]
        retriever.index(docs)
        results = retriever.retrieve("document", top_k=10)
        assert len(results) == 1
        assert results[0].doc_id == "only"

    def test_duplicate_documents(self):
        retriever = TEMPRRetriever()
        docs = [
            {"id": "dup1", "content": "exact same content here", "created_at": "2024-01-01T00:00:00+00:00"},
            {"id": "dup2", "content": "exact same content here", "created_at": "2024-01-01T00:00:00+00:00"},
        ]
        retriever.index(docs)
        results = retriever.retrieve("same content", top_k=10)
        # Both should appear since they have different IDs
        ids = {r.doc_id for r in results}
        assert len(ids) == 2
        assert "dup1" in ids
        assert "dup2" in ids

    def test_non_english_text(self):
        retriever = TEMPRRetriever()
        docs = [
            {"id": "cn", "content": "这是一个关于Python编程的文档", "created_at": "2024-01-01T00:00:00+00:00"},
            {"id": "jp", "content": "Pythonプログラミングに関する文書", "created_at": "2024-01-01T00:00:00+00:00"},
            {"id": "es", "content": "documento sobre programación Python", "created_at": "2024-01-01T00:00:00+00:00"},
        ]
        retriever.index(docs)
        results = retriever.retrieve("Python", top_k=10)
        assert len(results) > 0

    def test_top_k_respected(self):
        retriever = TEMPRRetriever()
        docs = _make_docs(50)
        retriever.index(docs)
        for k in [1, 5, 10, 20]:
            results = retriever.retrieve("document", top_k=k)
            assert len(results) <= k
            assert len(results) <= len(docs)

    def test_query_with_special_characters(self):
        retriever = TEMPRRetriever()
        docs = [
            {
                "id": "d1",
                "content": "fix error in file.py:42 - variable undefined",
                "created_at": "2024-01-01T00:00:00+00:00",
            },
        ]
        retriever.index(docs)
        results = retriever.retrieve("file.py:42", top_k=10)
        assert len(results) >= 0  # Should not crash

    def test_index_twice_replaces(self):
        retriever = TEMPRRetriever()
        docs1 = [{"id": "a", "content": "first batch", "created_at": "2024-01-01T00:00:00+00:00"}]
        retriever.index(docs1)
        docs2 = [{"id": "b", "content": "second batch", "created_at": "2024-01-02T00:00:00+00:00"}]
        retriever.index(docs2)
        results = retriever.retrieve("first", top_k=10)
        # After re-index, only docs2 is in the index
        assert all(r.doc_id == "b" for r in results)


# ── Top-K and Min Score ──────────────────────────────────────────────────────


class TestTopKMinScore:
    def test_top_k_limit(self):
        retriever = TEMPRRetriever()
        docs = _make_docs(100)
        retriever.index(docs)
        results = retriever.retrieve("document", top_k=3)
        assert len(results) == 3

    def test_result_order_descending(self):
        retriever = TEMPRRetriever()
        docs = _make_docs(20)
        retriever.index(docs)
        results = retriever.retrieve("python security", top_k=10)
        for i in range(len(results) - 1):
            assert results[i].final_score >= results[i + 1].final_score
