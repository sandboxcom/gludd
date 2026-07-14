"""Structural tests for retrieval/research_index.py — ResearchIndex."""

from __future__ import annotations

import shutil
import tempfile
from datetime import UTC, datetime, timedelta

import pytest

from general_ludd.retrieval.research_index import (
    CitationEdge,
    ResearchIndex,
    ResearchTopic,
    SourceEntry,
)


@pytest.fixture
def index_dir():
    d = tempfile.mkdtemp(prefix="research_index_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def index(index_dir: str):
    idx = ResearchIndex(index_dir=index_dir)
    yield idx
    idx.close()


class TestResearchTopic:
    def test_default_construction(self):
        t = ResearchTopic(query="test query")
        assert t.query == "test query"
        assert t.topic_id
        assert t.freshness_ttl_days == 7

    def test_is_stale_recent(self):
        t = ResearchTopic(
            query="q", last_researched_at=datetime.now(UTC).isoformat(),
        )
        assert t.is_stale(stale_threshold_days=1) is False

    def test_is_stale_old(self):
        old = datetime.now(UTC) - timedelta(days=30)
        t = ResearchTopic(query="q", last_researched_at=old.isoformat())
        assert t.is_stale(stale_threshold_days=7) is True

    def test_is_stale_invalid_ts(self):
        t = ResearchTopic(query="q", last_researched_at="not-a-date")
        assert t.is_stale() is True

    def test_age_days(self):
        t = ResearchTopic(query="q", last_researched_at=datetime.now(UTC).isoformat())
        assert t.age_days() < 1.0

    def test_age_days_invalid_returns_inf(self):
        t = ResearchTopic(query="q", last_researched_at="bad")
        assert t.age_days() == float("inf")

    def test_freshness_score_recent(self):
        t = ResearchTopic(query="q", last_researched_at=datetime.now(UTC).isoformat(), freshness_ttl_days=7)
        assert t.freshness_score() > 0.9

    def test_freshness_score_expired(self):
        old = datetime.now(UTC) - timedelta(days=30)
        t = ResearchTopic(query="q", last_researched_at=old.isoformat(), freshness_ttl_days=7)
        assert t.freshness_score() == 0.0


class TestSourceEntry:
    def test_default_construction(self):
        s = SourceEntry(url="https://example.com")
        assert s.url == "https://example.com"
        assert s.source_id
        assert s.quality_score == 0.5

    def test_with_domain(self):
        s = SourceEntry(url="https://docs.python.org", domain="docs.python.org", title="Python Docs")
        assert s.domain == "docs.python.org"
        assert s.title == "Python Docs"


class TestCitationEdge:
    def test_construction(self):
        e = CitationEdge(topic_id="t1", source_id="s1")
        assert e.topic_id == "t1"
        assert e.source_id == "s1"
        assert e.edge_id
        assert e.relevance_score == 0.5


class TestResearchIndexTopicOps:
    def test_normalize_query(self):
        assert ResearchIndex.normalize_query("  Hello WORLD  ") == "hello world"

    def test_upsert_and_get_topic(self, index):
        t = ResearchTopic(query="hello world")
        tid = index.upsert_topic(t)
        assert tid == t.topic_id
        got = index.get_topic(tid)
        assert got is not None
        assert got.query == "hello world"

    def test_get_topic_by_query(self, index):
        t = ResearchTopic(query="python async")
        index.upsert_topic(t)
        got = index.get_topic_by_query("  Python Async  ")
        assert got is not None
        assert got.topic_id == t.topic_id

    def test_get_topic_not_found(self, index):
        assert index.get_topic("nonexistent") is None

    def test_get_topic_by_query_not_found(self, index):
        assert index.get_topic_by_query("no such query") is None

    def test_delete_topic(self, index):
        t = ResearchTopic(query="to delete")
        tid = index.upsert_topic(t)
        assert index.delete_topic(tid) is True
        assert index.get_topic(tid) is None
        assert index.get_topic_by_query("to delete") is None

    def test_delete_topic_not_found(self, index):
        assert index.delete_topic("nonexistent") is False

    def test_topic_count(self, index):
        assert index.topic_count() == 0
        index.upsert_topic(ResearchTopic(query="t1"))
        index.upsert_topic(ResearchTopic(query="t2"))
        assert index.topic_count() == 2

    def test_list_topics_all(self, index):
        index.upsert_topic(ResearchTopic(query="a"))
        index.upsert_topic(ResearchTopic(query="b"))
        topics = index.list_topics()
        assert len(topics) == 2

    def test_list_topics_by_domain(self, index):
        t1 = ResearchTopic(query="a", domain="lang")
        t2 = ResearchTopic(query="b", domain="infra")
        index.upsert_topic(t1)
        index.upsert_topic(t2)
        lang = index.list_topics(domain="lang")
        assert len(lang) == 1
        assert lang[0].domain == "lang"

    def test_list_topics_by_tag(self, index):
        t1 = ResearchTopic(query="a", tags=["urgent"])
        t2 = ResearchTopic(query="b", tags=["backlog"])
        index.upsert_topic(t1)
        index.upsert_topic(t2)
        urgent = index.list_topics(tag="urgent")
        assert len(urgent) == 1
        assert "urgent" in urgent[0].tags

    def test_list_topics_stale_only(self, index):
        old = datetime.now(UTC) - timedelta(days=30)
        recent = datetime.now(UTC)
        t1 = ResearchTopic(query="stale", last_researched_at=old.isoformat(), freshness_ttl_days=7)
        t2 = ResearchTopic(query="fresh", last_researched_at=recent.isoformat(), freshness_ttl_days=7)
        index.upsert_topic(t1)
        index.upsert_topic(t2)
        stale = index.list_topics(stale_only=True)
        assert len(stale) == 1
        assert stale[0].query == "stale"

    def test_find_stale_topics(self, index):
        old = datetime.now(UTC) - timedelta(days=30)
        recent = datetime.now(UTC)
        index.upsert_topic(ResearchTopic(query="stale1", last_researched_at=old.isoformat(), freshness_ttl_days=7))
        index.upsert_topic(ResearchTopic(query="fresh1", last_researched_at=recent.isoformat(), freshness_ttl_days=7))
        index.upsert_topic(ResearchTopic(query="stale2", last_researched_at=old.isoformat(), freshness_ttl_days=7))
        stale = index.find_stale_topics(stale_threshold_days=7)
        assert len(stale) >= 2

    def test_find_stale_topics_sorted_by_freshness(self, index):
        very_old = datetime.now(UTC) - timedelta(days=60)
        kinda_old = datetime.now(UTC) - timedelta(days=14)
        index.upsert_topic(ResearchTopic(query="very old", last_researched_at=very_old.isoformat(), freshness_ttl_days=30))
        index.upsert_topic(ResearchTopic(query="kinda old", last_researched_at=kinda_old.isoformat(), freshness_ttl_days=30))
        stale = index.find_stale_topics(stale_threshold_days=10)
        assert len(stale) >= 2
        # very old (60d past, freshness 0.0) sorts before kinda old (14d past, freshness > 0.0)
        assert stale[0].freshness_score() <= stale[-1].freshness_score()


class TestResearchIndexSourceOps:
    def test_upsert_and_get_source(self, index):
        s = SourceEntry(url="https://example.com", domain="example.com")
        sid = index.upsert_source(s)
        assert sid == s.source_id
        got = index.get_source(sid)
        assert got is not None
        assert got.url == "https://example.com"

    def test_get_source_by_url(self, index):
        s = SourceEntry(url="https://docs.python.org", domain="docs.python.org")
        index.upsert_source(s)
        got = index.get_source_by_url("https://docs.python.org")
        assert got is not None
        assert got.url == "https://docs.python.org"

    def test_get_source_not_found(self, index):
        assert index.get_source("nonexistent") is None

    def test_list_sources_by_domain(self, index):
        index.upsert_source(SourceEntry(url="https://a.example.com", domain="example.com"))
        index.upsert_source(SourceEntry(url="https://b.example.com", domain="example.com"))
        index.upsert_source(SourceEntry(url="https://other.org", domain="other.org"))
        found = index.list_sources_by_domain("example.com")
        assert len(found) == 2

    def test_increment_citation_count(self, index):
        s = SourceEntry(url="https://example.com")
        sid = index.upsert_source(s)
        index.increment_citation_count(sid)
        updated = index.get_source(sid)
        assert updated is not None
        assert updated.citation_count == 1
        assert updated.last_verified_at is not None


class TestResearchIndexCitationOps:
    def test_add_and_list_citation_edges(self, index):
        t = ResearchTopic(query="q")
        tid = index.upsert_topic(t)
        s = SourceEntry(url="https://example.com")
        sid = index.upsert_source(s)

        edge = CitationEdge(topic_id=tid, source_id=sid)
        index.add_citation_edge(edge)
        edges = index.list_citation_edges(topic_id=tid)
        assert len(edges) == 1
        assert edges[0].topic_id == tid

    def test_list_citation_edges_by_source(self, index):
        t = ResearchTopic(query="q")
        tid = index.upsert_topic(t)
        s = SourceEntry(url="https://x.com")
        sid = index.upsert_source(s)

        index.add_citation_edge(CitationEdge(topic_id=tid, source_id=sid))
        edges = index.list_citation_edges(source_id=sid)
        assert len(edges) == 1

    def test_topic_citation_count(self, index):
        t = ResearchTopic(query="q")
        tid = index.upsert_topic(t)
        s1 = SourceEntry(url="https://a.com")
        s2 = SourceEntry(url="https://b.com")
        sid1 = index.upsert_source(s1)
        sid2 = index.upsert_source(s2)
        index.add_citation_edge(CitationEdge(topic_id=tid, source_id=sid1))
        index.add_citation_edge(CitationEdge(topic_id=tid, source_id=sid2))
        assert index.topic_citation_count(tid) == 2


class TestResearchIndexReindex:
    def test_needs_reindex_new_query(self, index):
        assert index.needs_reindex("new query") is True

    def test_needs_reindex_stale_topic(self, index):
        old = datetime.now(UTC) - timedelta(days=30)
        t = ResearchTopic(query="old query", last_researched_at=old.isoformat(), freshness_ttl_days=7)
        index.upsert_topic(t)
        assert index.needs_reindex("old query", stale_threshold_days=7) is True

    def test_needs_reindex_fresh_topic(self, index):
        recent = datetime.now(UTC)
        t = ResearchTopic(query="fresh query", last_researched_at=recent.isoformat(), freshness_ttl_days=7)
        index.upsert_topic(t)
        assert index.needs_reindex("fresh query", stale_threshold_days=7) is False

    def test_topic_needs_reindex_exists(self, index):
        old = datetime.now(UTC) - timedelta(days=30)
        t = ResearchTopic(query="q", last_researched_at=old.isoformat(), freshness_ttl_days=7)
        tid = index.upsert_topic(t)
        assert index.topic_needs_reindex(tid, stale_threshold_days=7) is True

    def test_topic_needs_reindex_nonexistent(self, index):
        assert index.topic_needs_reindex("nonexistent") is False

    def test_get_reindex_queue(self, index):
        old = datetime.now(UTC) - timedelta(days=30)
        index.upsert_topic(ResearchTopic(query="s1", last_researched_at=old.isoformat(), freshness_ttl_days=7))
        index.upsert_topic(ResearchTopic(query="s2", last_researched_at=old.isoformat(), freshness_ttl_days=7))
        queue = index.get_reindex_queue()
        assert len(queue) >= 2

    def test_get_reindex_queue_min_confidence(self, index):
        old = datetime.now(UTC) - timedelta(days=30)
        t1 = ResearchTopic(query="low", last_researched_at=old.isoformat(), freshness_ttl_days=7, overall_confidence=0.3)
        t2 = ResearchTopic(query="high", last_researched_at=old.isoformat(), freshness_ttl_days=7, overall_confidence=0.9)
        index.upsert_topic(t1)
        index.upsert_topic(t2)
        queue = index.get_reindex_queue(min_confidence=0.5)
        assert len(queue) == 1
        assert queue[0].query == "low"


class TestResearchIndexIngest:
    def test_ingest_report(self, index):
        t = ResearchTopic(query="python genserver")
        findings = [
            {
                "finding_id": "f1",
                "confidence": 0.9,
                "citations": [
                    {"url": "https://docs.python.org", "domain": "docs.python.org", "title": "Python Docs", "snippet": "..."},
                ],
            }
        ]
        tid = index.ingest_report(topic=t, findings=findings, sources_used=1)
        assert tid == t.topic_id
        assert index.topic_count() >= 1
        assert index.topic_citation_count(tid) >= 1

    def test_ingest_report_reuses_existing_source(self, index):
        s = SourceEntry(url="https://reuse.com", domain="reuse.com")
        index.upsert_source(s)

        t = ResearchTopic(query="reuse test")
        findings = [
            {
                "finding_id": "f1",
                "citations": [
                    {"url": "https://reuse.com", "domain": "reuse.com"},
                ],
            }
        ]
        index.ingest_report(topic=t, findings=findings)
        sources = index.list_sources_by_domain("reuse.com")
        assert len(sources) == 1


class TestResearchIndexStats:
    def test_stats(self, index):
        t = ResearchTopic(query="q")
        tid = index.upsert_topic(t)
        s = SourceEntry(url="https://example.com")
        sid = index.upsert_source(s)
        index.add_citation_edge(CitationEdge(topic_id=tid, source_id=sid))

        stats = index.stats()
        assert stats["topics"] >= 1
        assert stats["sources"] >= 1
        assert stats["citation_edges"] >= 1
        assert "freshness_ttl_days" in stats
        assert "index_dir" in stats
