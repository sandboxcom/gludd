"""Research index — local persistent index of research topics with freshness TTL.

Maintains a persistent index of previously researched topics, their sources,
a citation graph (which research references which sources), and re-indexing
triggers so stale topics are automatically flagged for re-research.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from general_ludd.security.safe_diskcache import open_safe_diskcache

logger = logging.getLogger(__name__)

DEFAULT_INDEX_DIR = os.path.join(
    os.path.expanduser("~"), ".local", "share", "general-ludd", "research_index"
)
DEFAULT_FRESHNESS_TTL_DAYS: int = 7
DEFAULT_MAX_TOPICS: int = 10_000
DEFAULT_STALE_THRESHOLD_DAYS: int = 3


def _json_cache_value(value: object) -> str | bytes | bytearray | None:
    if isinstance(value, (str, bytes, bytearray)):
        return value
    return None


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class SourceEntry(BaseModel):
    """A single source indexed by domain and topic."""

    source_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    url: str
    domain: str = ""
    title: str = ""
    snippet: str = ""
    quality_score: float = Field(default=0.5, ge=0.0, le=1.0)
    indexed_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    last_verified_at: str | None = None
    citation_count: int = 0
    tags: list[str] = Field(default_factory=list)


class CitationEdge(BaseModel):
    """A directed edge in the citation graph: topic → references → source."""

    edge_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    topic_id: str
    source_id: str
    finding_id: str = ""
    relevance_score: float = Field(default=0.5, ge=0.0, le=1.0)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class ResearchTopic(BaseModel):
    """A previously researched topic with freshness metadata."""

    topic_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    query: str
    normalized_query: str = ""
    domain: str = ""
    last_researched_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    freshness_ttl_days: int = DEFAULT_FRESHNESS_TTL_DAYS
    finding_count: int = 0
    high_confidence_findings: int = 0
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    report_snapshot: dict[str, Any] = Field(default_factory=dict)

    def is_stale(self, *, stale_threshold_days: int | None = None) -> bool:
        threshold = stale_threshold_days or self.freshness_ttl_days
        try:
            last = datetime.fromisoformat(self.last_researched_at)
            ago = datetime.now(UTC) - last
            return ago > timedelta(days=threshold)
        except (ValueError, TypeError):
            return True

    def age_days(self) -> float:
        try:
            last = datetime.fromisoformat(self.last_researched_at)
            seconds = (datetime.now(UTC) - last).total_seconds()
            return seconds / 86400.0
        except (ValueError, TypeError):
            return float("inf")

    def freshness_score(self) -> float:
        days = self.age_days()
        if days <= 0:
            return 1.0
        if days >= self.freshness_ttl_days:
            return 0.0
        return max(0.0, 1.0 - (days / self.freshness_ttl_days))


# ---------------------------------------------------------------------------
# Research index
# ---------------------------------------------------------------------------


class ResearchIndex:
    """Local persistent index of research topics, sources, and citation graph.

    Stores:
      - Topics indexed by query hash with freshness TTL.
      - Sources indexed by domain and topic.
      - Citation graph edges linking topics to their sources.
      - Re-indexing triggers that flag stale topics for re-research.

    Backed by ``diskcache`` on the local filesystem. All writes are JSON-serialized
    and keyed by topic / source / edge ID.
    """

    def __init__(
        self,
        *,
        index_dir: str | None = None,
        freshness_ttl_days: int = DEFAULT_FRESHNESS_TTL_DAYS,
        max_topics: int = DEFAULT_MAX_TOPICS,
    ) -> None:
        cache_dir = os.path.expanduser(os.path.expandvars(
            str(index_dir or DEFAULT_INDEX_DIR)
        ))
        self._cache = open_safe_diskcache(cache_dir)
        self._freshness_ttl = freshness_ttl_days
        self._max_topics = max_topics

    def close(self) -> None:
        self._cache.close()

    # ------------------------------------------------------------------
    # Topic operations
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_query(query: str) -> str:
        return query.strip().lower()

    def _topic_key(self, topic_id: str) -> str:
        return f"topic:{topic_id}"

    def _topic_by_query_key(self, normalized: str) -> str:
        return f"topic:q:{normalized}"

    def get_topic(self, topic_id: str) -> ResearchTopic | None:
        raw = self._cache.get(self._topic_key(topic_id))
        json_value = _json_cache_value(raw)
        if json_value is None:
            return None
        try:
            return ResearchTopic.model_validate_json(json_value)
        except Exception:
            return None

    def get_topic_by_query(self, query: str) -> ResearchTopic | None:
        normalized = self.normalize_query(query)
        topic_id = self._cache.get(self._topic_by_query_key(normalized))
        if topic_id is None:
            return None
        return self.get_topic(str(topic_id))

    def upsert_topic(self, topic: ResearchTopic) -> str:
        topic.normalized_query = self.normalize_query(topic.query)
        key = self._topic_key(topic.topic_id)
        self._cache.set(key, topic.model_dump_json())
        self._cache.set(
            self._topic_by_query_key(topic.normalized_query),
            topic.topic_id,
        )
        self._evict_if_needed()
        return topic.topic_id

    def list_topics(
        self,
        *,
        domain: str | None = None,
        tag: str | None = None,
        stale_only: bool = False,
        limit: int = 100,
    ) -> list[ResearchTopic]:
        results: list[ResearchTopic] = []
        for key in self._cache.iterkeys():
            if not str(key).startswith("topic:") or str(key).startswith("topic:q:"):
                continue
            topic_id = str(key).removeprefix("topic:")
            topic = self.get_topic(topic_id)
            if topic is None:
                continue
            if domain and topic.domain != domain:
                continue
            if tag and tag not in topic.tags:
                continue
            if stale_only and not topic.is_stale(stale_threshold_days=self._freshness_ttl):
                continue
            results.append(topic)
            if len(results) >= limit:
                break
        return results

    def find_stale_topics(
        self,
        *,
        stale_threshold_days: int | None = None,
        limit: int = 50,
    ) -> list[ResearchTopic]:
        threshold = stale_threshold_days or DEFAULT_STALE_THRESHOLD_DAYS
        stale: list[ResearchTopic] = []
        for key in self._cache.iterkeys():
            if not str(key).startswith("topic:") or str(key).startswith("topic:q:"):
                continue
            topic_id = str(key).removeprefix("topic:")
            topic = self.get_topic(topic_id)
            if topic is not None and topic.is_stale(stale_threshold_days=threshold):
                stale.append(topic)
                if len(stale) >= limit:
                    break
        stale.sort(key=lambda t: t.freshness_score())
        return stale

    def topic_count(self) -> int:
        count = 0
        for key in self._cache.iterkeys():
            if str(key).startswith("topic:") and not str(key).startswith("topic:q:"):
                count += 1
        return count

    def delete_topic(self, topic_id: str) -> bool:
        topic = self.get_topic(topic_id)
        if topic is None:
            return False
        self._cache.delete(self._topic_key(topic_id))
        self._cache.delete(self._topic_by_query_key(topic.normalized_query))
        return True

    def _evict_if_needed(self) -> None:
        count = self.topic_count()
        if count <= self._max_topics:
            return
        logger.warning(
            "research index exceeded max topics (%d > %d), evicting oldest",
            count,
            self._max_topics,
        )
        all_topics = self.list_topics(limit=count)
        all_topics.sort(key=lambda t: t.age_days(), reverse=True)
        to_evict = count - self._max_topics
        for topic in all_topics[:to_evict]:
            self.delete_topic(topic.topic_id)

    # ------------------------------------------------------------------
    # Source operations
    # ------------------------------------------------------------------

    def _source_key(self, source_id: str) -> str:
        return f"source:{source_id}"

    def _source_by_url_key(self, url: str) -> str:
        return f"source:u:{url}"

    def get_source(self, source_id: str) -> SourceEntry | None:
        raw = self._cache.get(self._source_key(source_id))
        json_value = _json_cache_value(raw)
        if json_value is None:
            return None
        try:
            return SourceEntry.model_validate_json(json_value)
        except Exception:
            return None

    def get_source_by_url(self, url: str) -> SourceEntry | None:
        source_id = self._cache.get(self._source_by_url_key(url))
        if source_id is None:
            return None
        return self.get_source(str(source_id))

    def upsert_source(self, source: SourceEntry) -> str:
        key = self._source_key(source.source_id)
        self._cache.set(key, source.model_dump_json())
        self._cache.set(self._source_by_url_key(source.url), source.source_id)
        return source.source_id

    def list_sources_by_domain(self, domain: str, limit: int = 100) -> list[SourceEntry]:
        results: list[SourceEntry] = []
        for key in self._cache.iterkeys():
            if not str(key).startswith("source:") or str(key).startswith("source:u:"):
                continue
            source_id = str(key).removeprefix("source:")
            source = self.get_source(source_id)
            if source is not None and source.domain == domain:
                results.append(source)
                if len(results) >= limit:
                    break
        return results

    def list_sources_for_topic(self, topic_id: str) -> list[SourceEntry]:
        edges = self.list_citation_edges(topic_id=topic_id)
        sources: list[SourceEntry] = []
        for edge in edges:
            source = self.get_source(edge.source_id)
            if source is not None:
                sources.append(source)
        return sources

    def increment_citation_count(self, source_id: str) -> None:
        source = self.get_source(source_id)
        if source is not None:
            source.citation_count += 1
            source.last_verified_at = datetime.now(UTC).isoformat()
            self.upsert_source(source)

    # ------------------------------------------------------------------
    # Citation graph operations
    # ------------------------------------------------------------------

    def _citation_key(self, edge_id: str) -> str:
        return f"citation:{edge_id}"

    def add_citation_edge(self, edge: CitationEdge) -> str:
        self._cache.set(self._citation_key(edge.edge_id), edge.model_dump_json())
        self.increment_citation_count(edge.source_id)
        return edge.edge_id

    def list_citation_edges(
        self,
        *,
        topic_id: str | None = None,
        source_id: str | None = None,
        limit: int = 200,
    ) -> list[CitationEdge]:
        results: list[CitationEdge] = []
        for key in self._cache.iterkeys():
            if not str(key).startswith("citation:"):
                continue
            raw = self._cache.get(key)
            json_value = _json_cache_value(raw)
            if json_value is None:
                continue
            try:
                edge = CitationEdge.model_validate_json(json_value)
            except Exception:
                continue
            if topic_id and edge.topic_id != topic_id:
                continue
            if source_id and edge.source_id != source_id:
                continue
            results.append(edge)
            if len(results) >= limit:
                break
        return results

    def topic_citation_count(self, topic_id: str) -> int:
        return len(self.list_citation_edges(topic_id=topic_id))

    # ------------------------------------------------------------------
    # Re-indexing triggers
    # ------------------------------------------------------------------

    def needs_reindex(self, query: str, *, stale_threshold_days: int | None = None) -> bool:
        topic = self.get_topic_by_query(query)
        if topic is None:
            return True
        return topic.is_stale(stale_threshold_days=stale_threshold_days or self._freshness_ttl)

    def get_reindex_queue(
        self,
        *,
        limit: int = 50,
        min_confidence: float | None = None,
    ) -> list[ResearchTopic]:
        stale = self.find_stale_topics(limit=limit)
        if min_confidence is not None:
            stale = [t for t in stale if t.overall_confidence < min_confidence]
        stale.sort(key=lambda t: t.freshness_score())
        return stale

    def topic_needs_reindex(
        self,
        topic_id: str,
        *,
        stale_threshold_days: int | None = None,
    ) -> bool:
        topic = self.get_topic(topic_id)
        if topic is None:
            return False
        return topic.is_stale(stale_threshold_days=stale_threshold_days)

    # ------------------------------------------------------------------
    # Index ingestion from a ResearchReport
    # ------------------------------------------------------------------

    def ingest_report(
        self,
        *,
        topic: ResearchTopic,
        findings: list[dict[str, Any]],
        sources_used: int = 0,
    ) -> str:
        topic_id = self.upsert_topic(topic)
        for f_data in findings:
            for cit in f_data.get("citations", []):
                url = cit.get("url", "")
                if not url:
                    continue
                domain = cit.get("domain", "")
                source = self.get_source_by_url(url)
                if source is None:
                    source = SourceEntry(
                        url=url,
                        domain=domain,
                        title=cit.get("title", ""),
                        snippet=cit.get("snippet", ""),
                        indexed_at=datetime.now(UTC).isoformat(),
                    )
                self.upsert_source(source)
                edge = CitationEdge(
                    topic_id=topic_id,
                    source_id=source.source_id,
                    finding_id=f_data.get("finding_id", ""),
                    relevance_score=f_data.get("confidence", 0.5),
                )
                self.add_citation_edge(edge)
        if sources_used:
            topic.finding_count = len(findings)
        self.upsert_topic(topic)
        return topic_id

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        topics = self.topic_count()
        stale = len(self.find_stale_topics())
        source_count = 0
        edge_count = 0
        for key in self._cache.iterkeys():
            ks = str(key)
            if ks.startswith("source:") and not ks.startswith("source:u:"):
                source_count += 1
            elif ks.startswith("citation:"):
                edge_count += 1
        return {
            "topics": topics,
            "stale_topics": stale,
            "sources": source_count,
            "citation_edges": edge_count,
            "freshness_ttl_days": self._freshness_ttl,
            "index_dir": str(self._cache.directory),
        }
