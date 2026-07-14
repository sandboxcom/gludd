"""Structural tests for retrieval/agentic_context.py — AgenticContextInjector."""

from __future__ import annotations

from general_ludd.retrieval.agentic_context import (
    AgenticContextInjector,
    AgenticResearchContext,
    ResearchContextItem,
    SourceAnnotation,
    estimate_tokens,
)


class TestSourceAnnotation:
    def test_minimal(self):
        sa = SourceAnnotation(url="https://example.com")
        assert sa.url == "https://example.com"
        assert sa.title == ""
        assert sa.domain == ""
        assert sa.quality_label == "unknown"

    def test_full(self):
        sa = SourceAnnotation(url="https://docs.python.org", title="Python Docs", domain="docs.python.org",
                              retrieved_at="2024-01-01", quality_label="authoritative")
        assert sa.quality_label == "authoritative"


class TestResearchContextItem:
    def test_minimal(self):
        item = ResearchContextItem(claim="Python is great")
        assert item.claim == "Python is great"
        assert item.confidence == 0.5
        assert item.sources == []

    def test_confidence_label_high(self):
        item = ResearchContextItem(claim="x", confidence=0.9)
        assert item.confidence_label == "high"

    def test_confidence_label_medium(self):
        item = ResearchContextItem(claim="x", confidence=0.6)
        assert item.confidence_label == "medium"

    def test_confidence_label_low(self):
        item = ResearchContextItem(claim="x", confidence=0.2)
        assert item.confidence_label == "low"

    def test_confidence_label_boundary_medium(self):
        item = ResearchContextItem(claim="x", confidence=0.5)
        assert item.confidence_label == "medium"


class TestAgenticResearchContext:
    def test_defaults(self):
        ctx = AgenticResearchContext()
        assert ctx.query == ""
        assert ctx.items == []
        assert ctx.overall_confidence == 0.0
        assert ctx.source_count == 0
        assert ctx.freshness_score == 0.0
        assert ctx.caveats == []

    def test_with_items(self):
        item = ResearchContextItem(claim="Fact", confidence=0.8)
        ctx = AgenticResearchContext(query="test", items=[item], overall_confidence=0.8, source_count=1)
        assert len(ctx.items) == 1


class TestEstimateTokens:
    def test_short_text(self):
        assert estimate_tokens("hello world") == 2

    def test_empty_text_returns_one(self):
        assert estimate_tokens("") == 1

    def test_long_text(self):
        assert estimate_tokens("a" * 100) == 25


class TestAgenticContextInjectorInit:
    def test_defaults(self):
        inj = AgenticContextInjector()
        assert inj._max_tokens == 4096
        assert inj._min_confidence == 0.3
        assert inj._context is None

    def test_custom(self):
        inj = AgenticContextInjector(max_research_tokens=2048, min_confidence=0.5)
        assert inj._max_tokens == 2048
        assert inj._min_confidence == 0.5


class TestClassifySource:
    def test_authoritative_domains(self):
        for domain in ("arxiv.org", "docs.python.org", "github.com", "ietf.org"):
            assert AgenticContextInjector._classify_source({"domain": domain}) == "authoritative"

    def test_low_quality_domains(self):
        for domain in ("quora.com", "buzzfeed.com"):
            assert AgenticContextInjector._classify_source({"domain": domain}) == "low-quality"

    def test_standard_domain(self):
        assert AgenticContextInjector._classify_source({"domain": "example.com"}) == "standard"

    def test_missing_domain(self):
        assert AgenticContextInjector._classify_source({}) == "standard"


class TestBuildContext:
    def test_empty_findings(self):
        inj = AgenticContextInjector()
        ctx = inj.build_context("test", [])
        assert ctx.query == "test"
        assert ctx.items == []

    def test_filters_low_confidence(self):
        inj = AgenticContextInjector(min_confidence=0.5)
        ctx = inj.build_context("test", [{"claim": "low", "confidence": 0.1}])
        assert len(ctx.items) == 0

    def test_passes_high_confidence(self):
        inj = AgenticContextInjector(min_confidence=0.3)
        ctx = inj.build_context("test", [{"claim": "high", "confidence": 0.9}])
        assert len(ctx.items) == 1

    def test_includes_sources(self):
        inj = AgenticContextInjector()
        ctx = inj.build_context("test", [{"claim": "fact", "confidence": 0.8, "citations": [{"url": "https://x.com"}]}])
        assert len(ctx.items) == 1
        assert ctx.items[0].sources[0].url == "https://x.com"


class TestInjectIntoSystemPrompt:
    def test_empty_context_returns_prompt_unchanged(self):
        inj = AgenticContextInjector()
        ctx = AgenticResearchContext()
        result = inj.inject_into_system_prompt("base prompt", ctx)
        assert result == "base prompt"

    def test_injects_findings(self):
        inj = AgenticContextInjector()
        item = ResearchContextItem(claim="Key fact", confidence=0.9)
        ctx = AgenticResearchContext(query="test", items=[item], overall_confidence=0.9, source_count=1)
        result = inj.inject_into_system_prompt("base prompt", ctx)
        assert "base prompt" in result
        assert "Key fact" in result
        assert "Research" in result or "research" in result.lower()

    def test_custom_section_title(self):
        inj = AgenticContextInjector()
        item = ResearchContextItem(claim="x", confidence=0.9)
        ctx = AgenticResearchContext(query="q", items=[item])
        result = inj.inject_into_system_prompt("p", ctx, section_title="## Custom Title")
        assert "Custom Title" in result

    def test_token_budget_truncation(self):
        inj = AgenticContextInjector(max_research_tokens=50)
        items = [ResearchContextItem(claim=f"fact {i}", confidence=0.9) for i in range(50)]
        ctx = AgenticResearchContext(query="q", items=items)
        result = inj.inject_into_system_prompt("p", ctx)
        assert "omitted" in result.lower()


class TestEnhancePrompt:
    def test_no_context_returns_prompt(self):
        inj = AgenticContextInjector()
        assert inj.enhance_prompt("hello") == "hello"


class TestSerializeContext:
    def test_roundtrip(self):
        ctx = AgenticResearchContext(query="test")
        inj = AgenticContextInjector()
        data = inj.serialize_context(ctx)
        parsed = inj.deserialize_context(data)
        assert parsed.query == "test"


class TestMergeContexts:
    def test_empty_list(self):
        inj = AgenticContextInjector()
        ctx = inj.merge_contexts([])
        assert ctx.items == []

    def test_single_context(self):
        item = ResearchContextItem(claim="x", confidence=0.5)
        ctx1 = AgenticResearchContext(query="q1", items=[item])
        ctx = AgenticContextInjector().merge_contexts([ctx1])
        assert len(ctx.items) == 1

    def test_merges_two_contexts(self):
        item1 = ResearchContextItem(claim="a", confidence=0.9, finding_id="id1")
        item2 = ResearchContextItem(claim="b", confidence=0.7, finding_id="id2")
        ctx1 = AgenticResearchContext(query="q1", items=[item1], source_count=1, overall_confidence=0.9)
        ctx2 = AgenticResearchContext(query="q2", items=[item2], source_count=1, overall_confidence=0.7)
        ctx = AgenticContextInjector().merge_contexts([ctx1, ctx2])
        assert len(ctx.items) == 2
        assert ctx.source_count == 2

    def test_deduplicates_by_finding_id(self):
        item = ResearchContextItem(claim="dup", confidence=0.5, finding_id="same")
        ctx1 = AgenticResearchContext(query="q1", items=[item])
        ctx2 = AgenticResearchContext(query="q2", items=[item])
        ctx = AgenticContextInjector().merge_contexts([ctx1, ctx2])
        assert len(ctx.items) == 1


class TestExtractFacts:
    def test_empty(self):
        ctx = AgenticResearchContext()
        facts = AgenticContextInjector().extract_facts(ctx)
        assert facts == []

    def test_filters_by_confidence(self):
        item = ResearchContextItem(claim="x", confidence=0.4)
        ctx = AgenticResearchContext(query="q", items=[item])
        facts = AgenticContextInjector().extract_facts(ctx, min_confidence=0.5)
        assert facts == []

    def test_extracts_above_threshold(self):
        item = ResearchContextItem(claim="good", confidence=0.8)
        ctx = AgenticResearchContext(query="q", items=[item])
        facts = AgenticContextInjector().extract_facts(ctx, min_confidence=0.5)
        assert len(facts) == 1
        assert facts[0][0] == "good"

    def test_max_facts_limit(self):
        items = [ResearchContextItem(claim=f"f{i}", confidence=0.9) for i in range(30)]
        ctx = AgenticResearchContext(query="q", items=items)
        facts = AgenticContextInjector().extract_facts(ctx, max_facts=5)
        assert len(facts) == 5


class TestRenderFactPreamble:
    def test_empty_returns_empty_string(self):
        ctx = AgenticResearchContext()
        result = AgenticContextInjector().render_fact_preamble(ctx)
        assert result == ""

    def test_renders_facts(self):
        item = ResearchContextItem(claim="fact", confidence=0.8)
        ctx = AgenticResearchContext(query="q", items=[item])
        result = AgenticContextInjector().render_fact_preamble(ctx, min_confidence=0.5)
        assert "Research-Sourced Facts" in result
        assert "fact" in result
