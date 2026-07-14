"""Structural tests for retrieval/research_index.py — research index with freshness TTL."""

from __future__ import annotations

from general_ludd.retrieval.research_index import (
    CitationEdge,
    ResearchIndex,
    ResearchTopic,
    SourceEntry,
)


class TestSourceEntry:
    def test_source_entry_constructor(self):
        se = SourceEntry(url="https://example.com", domain="example.com")
        assert se.url == "https://example.com"
        assert se.domain == "example.com"
        assert se.source_id
        assert 0.0 <= se.quality_score <= 1.0

    def test_source_entry_defaults(self):
        se = SourceEntry(url="https://x.com")
        assert se.domain == ""
        assert se.title == ""
        assert se.citation_count == 0
        assert se.tags == []


class TestCitationEdge:
    def test_citation_edge_constructor(self):
        ce = CitationEdge(topic_id="t1", source_id="s1")
        assert ce.topic_id == "t1"
        assert ce.source_id == "s1"
        assert ce.edge_id
        assert 0.0 <= ce.relevance_score <= 1.0


class TestResearchTopic:
    def test_topic_constructor(self):
        rt = ResearchTopic(query="test query")
        assert rt.query == "test query"
        assert rt.topic_id
        assert rt.finding_count == 0
        assert rt.overall_confidence == 0.0

    def test_is_stale_with_default_ttl(self):
        rt = ResearchTopic(query="q", freshness_ttl_days=365)
        assert not rt.is_stale()

    def test_is_stale_with_zero_ttl(self):
        rt = ResearchTopic(query="q", freshness_ttl_days=0)
        assert rt.is_stale()

    def test_age_days_returns_float(self):
        rt = ResearchTopic(query="q")
        assert isinstance(rt.age_days(), float)

    def test_freshness_score_returns_float(self):
        rt = ResearchTopic(query="q")
        assert isinstance(rt.freshness_score(), float)
        assert 0.0 <= rt.freshness_score() <= 1.0


class TestResearchIndex:
    def test_constructor(self):
        ri = ResearchIndex()
        assert ri._freshness_ttl == 7
        assert ri._max_topics == 10000
        ri.close()

    def test_normalize_query(self):
        assert ResearchIndex.normalize_query("  Hello WORLD ") == "hello world"

    def test_topic_count_zero_on_empty(self):
        import tempfile

        d = tempfile.mkdtemp(prefix="gludd-test-ri-")
        try:
            ri = ResearchIndex(index_dir=d)
            try:
                assert ri.topic_count() == 0
            finally:
                ri.close()
        finally:
            import shutil

            shutil.rmtree(d, ignore_errors=True)

    def test_upsert_and_get_topic(self):
        ri = ResearchIndex()
        rt = ResearchTopic(query="test", domain="example.com")
        try:
            tid = ri.upsert_topic(rt)
            assert tid
            retrieved = ri.get_topic(tid)
            assert retrieved is not None
            assert retrieved.query == "test"
            assert retrieved.domain == "example.com"
        finally:
            ri.close()

    def test_get_topic_by_query(self):
        ri = ResearchIndex()
        rt = ResearchTopic(query="unique query")
        try:
            ri.upsert_topic(rt)
            found = ri.get_topic_by_query("unique query")
            assert found is not None
            assert found.query == "unique query"
        finally:
            ri.close()

    def test_get_topic_nonexistent(self):
        ri = ResearchIndex()
        try:
            assert ri.get_topic("nonexistent") is None
        finally:
            ri.close()

    def test_delete_topic(self):
        ri = ResearchIndex()
        rt = ResearchTopic(query="deletable")
        try:
            tid = ri.upsert_topic(rt)
            assert ri.delete_topic(tid)
            assert ri.get_topic(tid) is None
        finally:
            ri.close()

    def test_delete_nonexistent_topic(self):
        ri = ResearchIndex()
        try:
            assert not ri.delete_topic("nonexistent")
        finally:
            ri.close()

    def test_needs_reindex_new_query(self):
        ri = ResearchIndex()
        try:
            assert ri.needs_reindex("never seen query")
        finally:
            ri.close()

    def test_upsert_source(self):
        ri = ResearchIndex()
        se = SourceEntry(url="https://a.com", domain="a.com")
        try:
            sid = ri.upsert_source(se)
            assert sid
            retrieved = ri.get_source(sid)
            assert retrieved is not None
            assert retrieved.url == "https://a.com"
        finally:
            ri.close()

    def test_add_citation_edge(self):
        ri = ResearchIndex()
        rt = ResearchTopic(query="cited topic")
        se = SourceEntry(url="https://b.com")
        try:
            tid = ri.upsert_topic(rt)
            sid = ri.upsert_source(se)
            ce = CitationEdge(topic_id=tid, source_id=sid)
            eid = ri.add_citation_edge(ce)
            assert eid
            assert ri.topic_citation_count(tid) == 1
        finally:
            ri.close()

    def test_ingest_report(self):
        ri = ResearchIndex()
        rt = ResearchTopic(query="report topic")
        findings = [{"confidence": 0.9, "citations": [{"url": "https://c.com", "title": "C"}]}]
        try:
            tid = ri.ingest_report(topic=rt, findings=findings, sources_used=1)
            assert tid
            assert ri.topic_citation_count(tid) >= 1
        finally:
            ri.close()

    def test_stats(self):
        ri = ResearchIndex()
        try:
            s = ri.stats()
            assert "topics" in s
            assert "stale_topics" in s
            assert "sources" in s
            assert "citation_edges" in s
        finally:
            ri.close()

    def test_increment_citation_count(self):
        ri = ResearchIndex()
        se = SourceEntry(url="https://d.com", domain="d.com")
        try:
            sid = ri.upsert_source(se)
            ri.increment_citation_count(sid)
            updated = ri.get_source(sid)
            assert updated is not None
            assert updated.citation_count == 1
        finally:
            ri.close()

    def test_find_stale_topics(self):
        ri = ResearchIndex()
        rt = ResearchTopic(query="stale test", freshness_ttl_days=0)
        try:
            ri.upsert_topic(rt)
            stale = ri.find_stale_topics()
            assert isinstance(stale, list)
        finally:
            ri.close()

    def test_get_reindex_queue(self):
        ri = ResearchIndex()
        try:
            q = ri.get_reindex_queue()
            assert isinstance(q, list)
        finally:
            ri.close()
