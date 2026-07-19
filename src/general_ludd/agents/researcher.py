"""Researcher agent — query construction, source verification, confidence scoring, structured output.

Provides a ``ResearcherAgent`` that integrates with SearXNG for live web research,
validates sources (real URLs only, citation tracking), computes confidence scores on
findings based on source multiplicity and recency, and produces structured research
reports with full citation chains.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)

# URL pattern that rejects obviously malformed or placeholder URLs.
_VALID_URL_RE = re.compile(
    r"^https?://[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?"
    r"(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)*"
    r"(:\d{1,5})?"
    r"(/[^\s]*)?$",
)

# Known link-farm / aggregator domains that rarely produce primary sources.
_LOW_QUALITY_DOMAINS: frozenset[str] = frozenset({
    "pinterest.com", "pinterest.ca", "pinterest.co.uk",
    "quora.com",
    "answers.com",
    "yahooanswers.com",
    "ehow.com",
    "buzzfeed.com",
    "thespruce.com",
    "wikihow.com",
})

# Domains that are generally high-quality primary or secondary sources.
_HIGH_QUALITY_DOMAINS: frozenset[str] = frozenset({
    "arxiv.org",
    "pubmed.ncbi.nlm.nih.gov",
    "scholar.google.com",
    "dl.acm.org",
    "ieeexplore.ieee.org",
    "github.com",
    "gitlab.com",
    "pypi.org",
    "crates.io",
    "npmjs.com",
    "docs.python.org",
    "developer.mozilla.org",
    "wikipedia.org",
    "stackoverflow.com",
    "supabase.com",
    "redis.io",
    "postgresql.org",
    "kubernetes.io",
    "docker.com",
    "aws.amazon.com",
    "cloud.google.com",
    "azure.microsoft.com",
    "terraform.io",
    "ansible.com",
    "nixos.org",
    "llvm.org",
    "gcc.gnu.org",
    "kernel.org",
    "openssl.org",
    "ietf.org",
    "w3.org",
    "iso.org",
    "cve.org",
    "nvd.nist.gov",
})


def _domain_from_url(url: str) -> str:
    """Extract a lowercased, ``www.``-stripped hostname from *url*."""
    m = re.match(r"https?://([^/:]+)", url)
    if not m:
        return ""
    host = m.group(1).lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _is_valid_url(url: str) -> bool:
    return bool(_VALID_URL_RE.match(url))


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class ResearchQuery(BaseModel):
    """A structured research intent produced from a natural-language query."""

    model_config = ConfigDict(strict=True)

    original_query: str = ""
    refined_query: str = ""
    target_domains: list[str] = Field(default_factory=list)
    exclude_domains: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=lambda: ["general", "science", "it"])
    time_range: str | None = None
    max_results: int = 20
    priority: str = "medium"

    @field_validator("max_results")
    @classmethod
    def _cap_max_results(cls, v: int) -> int:
        return max(1, min(v, 100))


class Citation(BaseModel):
    """A verified citation linking a finding to its source URL."""

    url: str
    title: str = ""
    snippet: str = ""
    domain: str = ""
    engine: str = ""
    published_date: str | None = None
    retrieved_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        if not _is_valid_url(v):
            raise ValueError(f"Invalid URL: {v!r}")
        return v


class ResearchFinding(BaseModel):
    """A single research finding with confidence scoring and citations."""

    finding_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    claim: str = ""
    confidence: float = Field(default=0.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, v: Any) -> float:
        """Validate and clamp confidence to [0.0, 1.0]."""
        if not isinstance(v, (int, float)):
            raise ValueError(f"confidence must be a float, got {v!r}")
        return max(0.0, min(float(v), 1.0))
    citations: list[Citation] = Field(default_factory=list)
    corroborating_sources: int = 0
    contradictory_sources: int = 0
    tags: list[str] = Field(default_factory=list)
    reasoning: str = ""


class ResearchReport(BaseModel):
    """Structured research output with full citation chain."""

    report_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    query: str = ""
    findings: list[ResearchFinding] = Field(default_factory=list)
    sources_consulted: int = 0
    sources_used: int = 0
    search_engines_used: list[str] = Field(default_factory=list)
    elapsed_seconds: float = 0.0
    generated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    summary: str = ""
    confidence_overall: float = Field(default=0.0, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------


@dataclass
class _ConfidenceFactors:
    source_count: int = 0
    high_quality_sources: int = 0
    low_quality_sources: int = 0
    has_recent: bool = False
    has_authoritative: bool = False


def _compute_confidence(factors: _ConfidenceFactors) -> float:
    """Compute a confidence score (0.0-1.0) from source-quality factors.

    The score starts at a baseline and is adjusted by:
      - Number of corroborating sources (log-scaled, max +0.3)
      - Share of high-quality sources (max +0.3)
      - Penalty for low-quality-only sources (-0.2)
      - Recency bonus (+0.1)
      - Authoritative source bonus (+0.1)
    """
    score = 0.25  # baseline — one source by itself is still uncertain
    if factors.source_count == 0:
        return 0.0

    score += min(0.3, math.log2(factors.source_count + 1) * 0.15)
    if factors.source_count > 0:
        high_ratio = factors.high_quality_sources / factors.source_count
        score += high_ratio * 0.3
    if factors.low_quality_sources > 0 and factors.high_quality_sources == 0:
        score -= 0.2
    if factors.has_recent:
        score += 0.1
    if factors.has_authoritative:
        score += 0.1
    return max(0.0, min(1.0, round(score, 2)))


# ---------------------------------------------------------------------------
# Researcher agent
# ---------------------------------------------------------------------------


class ResearcherAgent:
    """Agentic researcher that queries SearXNG, validates sources, scores findings.

    Designed to be dispatched as a subagent that:
      1. Receives a natural-language research question
      2. Constructs structured queries
      3. Queries SearXNG (and optionally web pages) for sources
      4. Verifies sources have real URLs
      5. Computes confidence scores per finding
      6. Returns a structured ``ResearchReport`` with full citations
    """

    FRESHNESS_WINDOW_DAYS: int = 90

    def __init__(
        self,
        *,
        searx_client: Any | None = None,
        web_retriever: Any | None = None,
    ) -> None:
        self._searx = searx_client
        self._web = web_retriever

    async def research(
        self,
        query: str,
        *,
        categories: list[str] | None = None,
        time_range: str | None = None,
        max_results: int = 20,
        fetch_pages: bool = False,
        target_domains: list[str] | None = None,
    ) -> ResearchReport:
        """Execute a research query and return a structured report.

        Args:
            query: Natural-language research question.
            categories: SearXNG categories to search.
            time_range: Time filter (day, week, month, year).
            max_results: Maximum search results to consider.
            fetch_pages: If True, fetch the full content of result pages.
            target_domains: Optional list of domains to prefer.

        Returns:
            ResearchReport with scored findings and citations.
        """
        research_query = self._construct_query(
            query,
            categories=categories,
            time_range=time_range,
            max_results=max_results,
            target_domains=target_domains,
        )
        logger.info(
            "research query refined: %r → %r (categories=%s, time_range=%s)",
            query,
            research_query.refined_query,
            research_query.categories,
            research_query.time_range,
        )

        start = time.monotonic()
        search_results = await self._execute_search(research_query)
        logger.info("search returned %d results for %r", len(search_results), query)

        findings = self._extract_findings(research_query, search_results)

        if fetch_pages and self._web is not None:
            findings = await self._enrich_with_page_content(findings)

        elapsed = time.monotonic() - start
        report = self._build_report(research_query, findings, elapsed)
        logger.info(
            "research complete: %d findings, confidence=%.2f, elapsed=%.1fs",
            len(report.findings),
            report.confidence_overall,
            elapsed,
        )
        return report

    def _construct_query(
        self,
        query: str,
        *,
        categories: list[str] | None = None,
        time_range: str | None = None,
        max_results: int = 20,
        target_domains: list[str] | None = None,
    ) -> ResearchQuery:
        selected_categories = categories or ["general", "science", "it"]
        exclude = list(_LOW_QUALITY_DOMAINS)
        return ResearchQuery(
            original_query=query,
            refined_query=query,
            target_domains=target_domains or [],
            exclude_domains=exclude,
            categories=selected_categories,
            time_range=time_range,
            max_results=max_results,
        )

    async def _execute_search(self, rq: ResearchQuery) -> list[dict[str, Any]]:
        if self._searx is None:
            return []

        try:
            from general_ludd.retrieval.searx_client import SearxNGClient

            is_searx = isinstance(self._searx, SearxNGClient)
        except Exception:
            is_searx = False

        if not is_searx:
            return []

        raw_results: list[dict[str, Any]] = []
        try:
            response = await self._searx.search(
                rq.refined_query,
                categories=rq.categories,
                time_range=rq.time_range,
                page=1,
            )
            for r in response.results:
                url = r.url.strip()
                if not _is_valid_url(url):
                    continue
                domain = _domain_from_url(url)
                if domain in rq.exclude_domains:
                    continue
                raw_results.append({
                    "url": url,
                    "title": r.title,
                    "content": r.content,
                    "engine": r.engine,
                    "score": r.score,
                    "category": r.category,
                    "published_date": r.published_date,
                    "domain": domain,
                })
        except Exception:
            logger.exception("SearXNG search failed for query %r", rq.refined_query)

        if len(raw_results) > rq.max_results:
            raw_results = raw_results[: rq.max_results]

        return raw_results

    def _extract_findings(
        self,
        rq: ResearchQuery,
        search_results: list[dict[str, Any]],
    ) -> list[ResearchFinding]:
        if not search_results:
            return []

        findings: list[ResearchFinding] = []
        for _idx, raw in enumerate(search_results):
            citation = Citation(
                url=raw["url"],
                title=raw.get("title", ""),
                snippet=raw.get("content", ""),
                domain=raw.get("domain", ""),
                engine=raw.get("engine", ""),
                published_date=raw.get("published_date"),
            )
            tags = self._classify_tags(raw)
            claim = self._synthesize_claim(raw, citation)
            confidence = self._score_single_finding([raw])

            finding = ResearchFinding(
                finding_id=hashlib.sha256(
                    raw["url"].encode()
                ).hexdigest()[:12],
                claim=claim,
                confidence=confidence,
                citations=[citation],
                corroborating_sources=1,
                contradictory_sources=0,
                tags=tags,
                reasoning=f"Source: {citation.domain} (engine={citation.engine})",
            )
            findings.append(finding)

        self._cross_corroborate(findings)
        return findings

    @staticmethod
    def _classify_tags(raw: dict[str, Any]) -> list[str]:
        tags: list[str] = []
        domain = raw.get("domain", "")
        if domain in _HIGH_QUALITY_DOMAINS:
            tags.append("authoritative")
        if domain in {"arxiv.org", "pubmed.ncbi.nlm.nih.gov", "dl.acm.org", "ieeexplore.ieee.org"}:
            tags.append("academic")
        if domain in {"github.com", "gitlab.com", "pypi.org", "crates.io", "npmjs.com"}:
            tags.append("code")
        if domain in {"docs.python.org", "developer.mozilla.org", "wikipedia.org"}:
            tags.append("reference")
        if domain in _LOW_QUALITY_DOMAINS:
            tags.append("low-quality")
        if raw.get("published_date"):
            tags.append("dated")
        category = raw.get("category", "")
        if category:
            tags.append(f"cat:{category}")
        return tags

    @staticmethod
    def _synthesize_claim(raw: dict[str, Any], citation: Citation) -> str:
        title: str = raw.get("title", "").strip()
        snippet: str = raw.get("content", "").strip()
        if title and snippet:
            return f"{title}: {snippet[:300]}"
        if title:
            return title[:500]
        if snippet:
            return snippet[:500]
        return f"Result from {citation.domain}"

    def _score_single_finding(
        self, sources: list[dict[str, Any]]
    ) -> float:
        factors = _ConfidenceFactors(source_count=len(sources))
        for s in sources:
            domain = s.get("domain", "")
            if domain in _HIGH_QUALITY_DOMAINS:
                factors.high_quality_sources += 1
            if domain in _LOW_QUALITY_DOMAINS:
                factors.low_quality_sources += 1
            if s.get("published_date"):
                try:
                    pub_dt = datetime.fromisoformat(s["published_date"])
                    if (datetime.now(UTC) - pub_dt).days <= self.FRESHNESS_WINDOW_DAYS:
                        factors.has_recent = True
                except (ValueError, TypeError):
                    pass
        factors.has_authoritative = any(
            "authoritative" in self._classify_tags(s) for s in sources
        )
        return _compute_confidence(factors)

    def _cross_corroborate(self, findings: list[ResearchFinding]) -> None:
        if len(findings) < 2:
            return
        for i, f_i in enumerate(findings):
            for j, f_j in enumerate(findings):
                if i >= j:
                    continue
                if self._claims_overlap(f_i.claim, f_j.claim):
                    f_i.corroborating_sources += 1
                    f_j.corroborating_sources += 1
        for finding in findings:
            extra = min(0.3, math.log2(finding.corroborating_sources) * 0.1)
            finding.confidence = min(1.0, round(finding.confidence + extra, 2))

    @staticmethod
    def _claims_overlap(claim_a: str, claim_b: str) -> bool:
        words_a = set(re.findall(r"\b[a-zA-Z]{4,}\b", claim_a.lower()))
        words_b = set(re.findall(r"\b[a-zA-Z]{4,}\b", claim_b.lower()))
        if not words_a or not words_b:
            return False
        overlap = words_a & words_b
        return len(overlap) / min(len(words_a), len(words_b)) >= 0.3

    async def _enrich_with_page_content(
        self, findings: list[ResearchFinding]
    ) -> list[ResearchFinding]:
        if not self._web:
            return findings
        enriched: list[ResearchFinding] = []
        for f in findings:
            for cit in f.citations:
                try:
                    result = self._web.fetch_web_page(cit.url)
                    if result.status_code == 200 and result.content:
                        cit.snippet = result.content[:2000]
                        if result.title:
                            cit.title = result.title
                except Exception:
                    logger.debug("Failed to fetch page content for %s", cit.url)
            enriched.append(f)
        return enriched

    def _build_report(
        self,
        rq: ResearchQuery,
        findings: list[ResearchFinding],
        elapsed: float,
    ) -> ResearchReport:
        sources_consulted = sum(f.corroborating_sources for f in findings)
        sources_used = sum(1 for f in findings if f.corroborating_sources > 0)
        engines = sorted({
            c.engine for f in findings for c in f.citations if c.engine
        })
        if findings:
            confidences = [f.confidence for f in findings]
            overall = round(sum(confidences) / len(confidences), 2)
        else:
            overall = 0.0

        high_conf = [f for f in findings if f.confidence >= 0.7]
        low_conf = [f for f in findings if f.confidence < 0.4]
        summary_parts: list[str] = [
            f"Research on '{rq.original_query}' found {len(findings)} findings "
            f"(overall confidence: {overall:.2f}).",
        ]
        if high_conf:
            summary_parts.append(
                f"{len(high_conf)} high-confidence findings (≥0.7)."
            )
        if low_conf:
            summary_parts.append(
                f"{len(low_conf)} low-confidence findings (<0.4) — consider deeper research."
            )
        if not findings:
            summary_parts.append("No relevant sources found.")

        return ResearchReport(
            query=rq.original_query,
            findings=findings,
            sources_consulted=sources_consulted,
            sources_used=sources_used,
            search_engines_used=engines,
            elapsed_seconds=round(elapsed, 2),
            summary=" ".join(summary_parts),
            confidence_overall=overall,
        )
