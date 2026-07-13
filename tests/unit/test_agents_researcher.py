"""Unit tests for agents/researcher.py — ResearcherAgent, confidence scoring, validation."""

from __future__ import annotations

import pytest

from general_ludd.agents.researcher import (
    _LOW_QUALITY_DOMAINS,
    Citation,
    ResearcherAgent,
    ResearchFinding,
    ResearchQuery,
    ResearchReport,
    _compute_confidence,
    _ConfidenceFactors,
    _domain_from_url,
    _is_valid_url,
)


class TestDomainFromURL:
    def test_standard_url(self):
        assert _domain_from_url("https://github.com/user/repo") == "github.com"

    def test_strips_www(self):
        assert _domain_from_url("https://www.example.com/page") == "example.com"

    def test_http_url(self):
        assert _domain_from_url("http://arxiv.org/abs/1234") == "arxiv.org"

    def test_url_with_port(self):
        assert _domain_from_url("https://localhost:8080/path") == "localhost"

    def test_invalid_url_returns_empty(self):
        assert _domain_from_url("not-a-url") == ""

    def test_empty_string(self):
        assert _domain_from_url("") == ""


class TestIsValidURL:
    def test_standard_url(self):
        assert _is_valid_url("https://example.com/page") is True

    def test_complex_url(self):
        assert _is_valid_url("https://docs.python.org/3/library/re.html") is True

    def test_invalid_no_scheme(self):
        assert _is_valid_url("example.com") is False

    def test_invalid_placeholder(self):
        assert _is_valid_url("not a url") is False


class TestResearchQuery:
    def test_defaults(self):
        q = ResearchQuery()
        assert q.max_results == 20
        assert q.priority == "medium"
        assert q.categories == ["general", "science", "it"]

    def test_max_results_capped_at_100(self):
        q = ResearchQuery(max_results=200)
        assert q.max_results == 100

    def test_max_results_minimum_1(self):
        q = ResearchQuery(max_results=0)
        assert q.max_results == 1

    def test_max_results_negative_caps_to_1(self):
        q = ResearchQuery(max_results=-5)
        assert q.max_results == 1


class TestCitation:
    def test_valid_url_accepted(self):
        c = Citation(url="https://example.com", title="Test")
        assert c.url == "https://example.com"

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            Citation(url="not-a-url")

    def test_retrieved_at_auto_set(self):
        c = Citation(url="https://example.com")
        assert c.retrieved_at != ""


class TestResearchFinding:
    def test_defaults(self):
        f = ResearchFinding()
        assert f.confidence == 0.0
        assert f.corroborating_sources == 0

    def test_confidence_clamped(self):
        f = ResearchFinding(confidence=1.5)
        assert f.confidence == 1.0

    def test_finding_id_auto_generated(self):
        f = ResearchFinding()
        assert len(f.finding_id) == 12


class TestResearchReport:
    def test_defaults(self):
        r = ResearchReport()
        assert r.confidence_overall == 0.0
        assert r.findings == []

    def test_report_id_auto_generated(self):
        r = ResearchReport()
        assert len(r.report_id) == 12


class TestComputeConfidence:
    def test_no_sources_returns_zero(self):
        assert _compute_confidence(_ConfidenceFactors(source_count=0)) == 0.0

    def test_single_source_baseline(self):
        score = _compute_confidence(_ConfidenceFactors(source_count=1))
        assert score >= 0.25

    def test_multiple_sources_higher(self):
        low = _compute_confidence(_ConfidenceFactors(source_count=1))
        high = _compute_confidence(_ConfidenceFactors(source_count=10))
        assert high > low

    def test_high_quality_sources_boost(self):
        low = _compute_confidence(_ConfidenceFactors(source_count=2, high_quality_sources=0))
        high = _compute_confidence(_ConfidenceFactors(source_count=2, high_quality_sources=2))
        assert high > low

    def test_low_quality_only_penalty(self):
        score_low_only = _compute_confidence(_ConfidenceFactors(
            source_count=2, low_quality_sources=2, high_quality_sources=0
        ))
        score_normal = _compute_confidence(_ConfidenceFactors(
            source_count=2, low_quality_sources=0, high_quality_sources=2
        ))
        assert score_low_only < score_normal

    def test_recent_bonus(self):
        base = _compute_confidence(_ConfidenceFactors(source_count=2))
        with_recent = _compute_confidence(_ConfidenceFactors(source_count=2, has_recent=True))
        assert with_recent > base

    def test_authoritative_bonus(self):
        base = _compute_confidence(_ConfidenceFactors(source_count=2))
        with_auth = _compute_confidence(_ConfidenceFactors(source_count=2, has_authoritative=True))
        assert with_auth > base

    def test_capped_at_one(self):
        score = _compute_confidence(_ConfidenceFactors(
            source_count=100, high_quality_sources=100, has_recent=True, has_authoritative=True
        ))
        assert score <= 1.0

    def test_capped_at_zero(self):
        score = _compute_confidence(_ConfidenceFactors(
            source_count=0, low_quality_sources=10, high_quality_sources=0
        ))
        assert score == 0.0


class TestResearcherAgentConstructQuery:
    def test_default_categories(self):
        agent = ResearcherAgent()
        rq = agent._construct_query("test query")
        assert rq.categories == ["general", "science", "it"]
        assert _LOW_QUALITY_DOMAINS.issubset(set(rq.exclude_domains))

    def test_custom_categories(self):
        agent = ResearcherAgent()
        rq = agent._construct_query("test", categories=["science"])
        assert rq.categories == ["science"]

    def test_target_domains_passed_through(self):
        agent = ResearcherAgent()
        rq = agent._construct_query("test", target_domains=["github.com"])
        assert rq.target_domains == ["github.com"]


class TestResearcherAgentClassifyTags:
    def test_authoritative_domain(self):
        tags = ResearcherAgent._classify_tags({"domain": "arxiv.org"})
        assert "authoritative" in tags
        assert "academic" in tags

    def test_code_domain(self):
        tags = ResearcherAgent._classify_tags({"domain": "github.com"})
        assert "code" in tags

    def test_reference_domain(self):
        tags = ResearcherAgent._classify_tags({"domain": "docs.python.org"})
        assert "reference" in tags

    def test_low_quality_domain(self):
        tags = ResearcherAgent._classify_tags({"domain": "pinterest.com"})
        assert "low-quality" in tags

    def test_dated_when_published_date(self):
        tags = ResearcherAgent._classify_tags({"domain": "github.com", "published_date": "2024-01-01"})
        assert "dated" in tags

    def test_category_tag(self):
        tags = ResearcherAgent._classify_tags({"domain": "example.com", "category": "science"})
        assert "cat:science" in tags


class TestResearcherAgentSynthesizeClaim:
    def test_title_and_snippet(self):
        result = ResearcherAgent._synthesize_claim(
            {"title": "Hello World", "content": "Some content"},
            Citation(url="https://example.com"),
        )
        assert "Hello World" in result
        assert "Some content" in result

    def test_title_only(self):
        result = ResearcherAgent._synthesize_claim(
            {"title": "Hello World", "content": ""},
            Citation(url="https://example.com"),
        )
        assert result == "Hello World"

    def test_snippet_only(self):
        result = ResearcherAgent._synthesize_claim(
            {"title": "", "content": "Some content"},
            Citation(url="https://example.com"),
        )
        assert result == "Some content"

    def test_fallback_to_domain(self):
        cit = Citation(url="https://example.com", domain="example.com")
        result = ResearcherAgent._synthesize_claim({"title": "", "content": ""}, cit)
        assert "example.com" in result


class TestResearcherAgentClaimsOverlap:
    def test_no_overlap(self):
        assert ResearcherAgent._claims_overlap(
            "apple banana cherry date", "xylophone zebra"
        ) is False

    def test_overlap(self):
        assert ResearcherAgent._claims_overlap(
            "apple banana cherry date", "apple banana xylophone"
        ) is True

    def test_empty_strings(self):
        assert ResearcherAgent._claims_overlap("", "") is False

    def test_too_few_words(self):
        assert ResearcherAgent._claims_overlap("a b c", "a b c") is False


class TestResearcherAgentCrossCorroborate:
    def test_single_finding_no_change(self):
        agent = ResearcherAgent()
        f = ResearchFinding(confidence=0.5)
        agent._cross_corroborate([f])
        assert f.confidence == 0.5

    def test_overlapping_findings_boost_confidence(self):
        agent = ResearcherAgent()
        f1 = ResearchFinding(claim="apple banana cherry date", confidence=0.5)
        f2 = ResearchFinding(claim="apple banana xylophone zebra", confidence=0.5)
        agent._cross_corroborate([f1, f2])
        assert f1.corroborating_sources >= 1
        assert f1.confidence > 0.5


class TestResearcherAgentBuildReport:
    def test_empty_findings(self):
        agent = ResearcherAgent()
        rq = ResearchQuery(original_query="test")
        report = agent._build_report(rq, [], 1.0)
        assert report.confidence_overall == 0.0
        assert "No relevant sources" in report.summary

    def test_with_findings(self):
        agent = ResearcherAgent()
        rq = ResearchQuery(original_query="test")
        f1 = ResearchFinding(confidence=0.8, corroborating_sources=2)
        f2 = ResearchFinding(confidence=0.3, corroborating_sources=0)
        report = agent._build_report(rq, [f1, f2], 1.0)
        assert len(report.findings) == 2
        assert report.confidence_overall == 0.55
        assert report.sources_consulted == 2

    def test_search_engines_deduplicated(self):
        agent = ResearcherAgent()
        rq = ResearchQuery(original_query="test")
        cit1 = Citation(url="https://a.com", engine="google")
        cit2 = Citation(url="https://b.com", engine="google")
        f1 = ResearchFinding(confidence=0.5, citations=[cit1], corroborating_sources=1)
        f2 = ResearchFinding(confidence=0.5, citations=[cit2], corroborating_sources=1)
        report = agent._build_report(rq, [f1, f2], 1.0)
        assert report.search_engines_used == ["google"]


class TestResearcherAgentExecuteSearch:
    def test_no_searx_returns_empty(self):
        agent = ResearcherAgent(searx_client=None)
        import asyncio
        rq = ResearchQuery(refined_query="test")
        result = asyncio.run(agent._execute_search(rq))
        assert result == []


class TestResearcherAgentResearch:
    def test_no_searx_returns_empty_report(self):
        agent = ResearcherAgent(searx_client=None)
        import asyncio
        report = asyncio.run(agent.research("test query"))
        assert isinstance(report, ResearchReport)
        assert report.findings == []
        assert report.confidence_overall == 0.0


class TestResearcherAgentScoreSingleFinding:
    def test_high_quality_source(self):
        agent = ResearcherAgent()
        score = agent._score_single_finding([{"domain": "arxiv.org"}])
        assert score > 0.25

    def test_low_quality_source(self):
        agent = ResearcherAgent()
        score = agent._score_single_finding([{"domain": "pinterest.com"}])
        assert score < 0.5

    def test_multiple_mixed_sources(self):
        agent = ResearcherAgent()
        score = agent._score_single_finding([
            {"domain": "arxiv.org"},
            {"domain": "github.com"},
            {"domain": "pinterest.com"},
        ])
        assert 0.0 <= score <= 1.0


class TestResearcherAgentExtractFindings:
    def test_no_results_returns_empty(self):
        agent = ResearcherAgent()
        rq = ResearchQuery(refined_query="test")
        assert agent._extract_findings(rq, []) == []

    def test_single_result_produces_finding(self):
        agent = ResearcherAgent()
        rq = ResearchQuery(refined_query="test")
        results = [{
            "url": "https://example.com",
            "title": "Example",
            "content": "Example content",
            "domain": "example.com",
            "engine": "google",
        }]
        findings = agent._extract_findings(rq, results)
        assert len(findings) == 1
        assert findings[0].citations[0].url == "https://example.com"
