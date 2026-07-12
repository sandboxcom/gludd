"""Agentic context — inject research findings into agent prompts with source awareness.

Provides mechanisms for:
  - Injecting research findings into agent system prompts and context windows.
  - Source-aware context so agents know where every fact came from.
  - Confidence-weighted information so agents know how reliable each finding is.
  - Research context serialization for cross-session state transfer.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# Maximum token budget allocated to injected research findings.
_DEFAULT_MAX_RESEARCH_TOKENS: int = 4096


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class SourceAnnotation(BaseModel):
    """Source attribution for a fact — agents know where a fact came from."""

    url: str
    title: str = ""
    domain: str = ""
    retrieved_at: str | None = None
    quality_label: str = "unknown"


class ResearchContextItem(BaseModel):
    """A single research finding ready for prompt injection."""

    claim: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    sources: list[SourceAnnotation] = Field(default_factory=list)
    finding_id: str = ""
    tags: list[str] = Field(default_factory=list)
    fresh: bool = True

    @property
    def confidence_label(self) -> str:
        if self.confidence >= 0.8:
            return "high"
        if self.confidence >= 0.5:
            return "medium"
        return "low"


class AgenticResearchContext(BaseModel):
    """A bundle of research findings packaged for injection into an agent prompt."""

    model_config = ConfigDict(strict=True)

    query: str = ""
    generated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    items: list[ResearchContextItem] = Field(default_factory=list)
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_count: int = 0
    freshness_score: float = Field(default=0.0, ge=0.0, le=1.0)
    caveats: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Token estimation (character-count heuristic, matching ContextCompactor)
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Agentic context injector
# ---------------------------------------------------------------------------


class AgenticContextInjector:
    """Injects research findings into agent prompts with source-awareness and
    confidence-weighting.

    Designed to be used before dispatching an agent: the orchestrator queries
    the ``ResearchIndex`` for relevant topics, builds an ``AgenticResearchContext``,
    and injects it into the agent's system prompt or context window.
    """

    def __init__(
        self,
        *,
        max_research_tokens: int = _DEFAULT_MAX_RESEARCH_TOKENS,
        min_confidence: float = 0.3,
    ) -> None:
        self._max_tokens = max_research_tokens
        self._min_confidence = min_confidence
        self._context: AgenticResearchContext | None = None

    def build_context(
        self,
        query: str,
        findings: list[dict[str, Any]],
        *,
        overall_confidence: float = 0.0,
        source_count: int = 0,
        freshness_score: float = 1.0,
        caveats: list[str] | None = None,
    ) -> AgenticResearchContext:
        items: list[ResearchContextItem] = []
        for f_data in findings:
            confidence = float(f_data.get("confidence", 0.0))
            if confidence < self._min_confidence:
                continue
            sources: list[SourceAnnotation] = []
            for cit in f_data.get("citations", []):
                sources.append(SourceAnnotation(
                    url=cit.get("url", ""),
                    title=cit.get("title", ""),
                    domain=cit.get("domain", ""),
                    retrieved_at=cit.get("retrieved_at"),
                    quality_label=self._classify_source(cit),
                ))
            items.append(ResearchContextItem(
                claim=f_data.get("claim", ""),
                confidence=confidence,
                sources=sources,
                finding_id=f_data.get("finding_id", ""),
                tags=f_data.get("tags", []),
                fresh=freshness_score >= 0.7,
            ))

        return AgenticResearchContext(
            query=query,
            items=items,
            overall_confidence=overall_confidence,
            source_count=source_count,
            freshness_score=freshness_score,
            caveats=caveats or [],
        )

    @staticmethod
    def _classify_source(cit: dict[str, Any]) -> str:
        domain = cit.get("domain", "").lower()
        authoritative_domains = {
            "arxiv.org", "pubmed.ncbi.nlm.nih.gov", "dl.acm.org",
            "ieeexplore.ieee.org", "docs.python.org", "developer.mozilla.org",
            "wikipedia.org", "github.com", "ietf.org", "w3.org",
            "nvd.nist.gov", "cve.org",
        }
        if domain in authoritative_domains:
            return "authoritative"
        low_quality = {
            "pinterest.com", "quora.com", "answers.com", "ehow.com",
            "buzzfeed.com", "wikihow.com",
        }
        if domain in low_quality:
            return "low-quality"
        return "standard"

    # ------------------------------------------------------------------
    # Prompt injection
    # ------------------------------------------------------------------

    def inject_into_system_prompt(
        self,
        system_prompt: str,
        context: AgenticResearchContext,
        *,
        section_title: str = "## Pre-Researched Context",
    ) -> str:
        rendered = self._render_context_block(context, section_title)
        if not rendered:
            return system_prompt
        return f"{system_prompt}\n\n{rendered}"

    def enhance_prompt(self, prompt: str) -> str:
        return self.inject_into_system_prompt(prompt, self._context) if self._context is not None else prompt

    def inject_as_user_message(
        self,
        context: AgenticResearchContext,
    ) -> str:
        block = self._render_context_block(context, "## Research Findings")
        if not block:
            return ""
        return block

    def _render_context_block(
        self,
        context: AgenticResearchContext,
        section_title: str,
    ) -> str:
        if not context.items:
            return ""

        lines: list[str] = [section_title, ""]
        lines.append(
            f"Research on: **{context.query}** — "
            f"overall confidence: {context.overall_confidence:.2f}, "
            f"freshness: {context.freshness_score:.2f}, "
            f"sources: {context.source_count}"
        )
        lines.append("")

        # Sort items by confidence (highest first), limited by token budget.
        sorted_items = sorted(context.items, key=lambda i: i.confidence, reverse=True)
        token_budget = self._max_tokens
        for idx, item in enumerate(sorted_items):
            entry = self._render_finding(item)
            entry_tokens = estimate_tokens(entry)
            if token_budget - entry_tokens < 0:
                logger.debug(
                    "research context truncated at %d items (token budget %d)",
                    idx,
                    self._max_tokens,
                )
                remaining = len(sorted_items) - idx
                if remaining > 0:
                    lines.append(f"({remaining} lower-confidence findings omitted)")
                break
            lines.append(entry)
            lines.append("")
            token_budget -= entry_tokens

        if context.caveats:
            lines.append("### Caveats")
            for c in context.caveats:
                lines.append(f"- {c}")
            lines.append("")

        lines.append(
            "---\n"
            "**Instruction:** When referencing the above findings, cite the source URL "
            "and the confidence level. Prefer HIGH-confidence findings. "
            "Treat LOW-confidence findings as hints, not facts."
        )
        return "\n".join(lines)

    @staticmethod
    def _render_finding(item: ResearchContextItem) -> str:
        label = item.confidence_label.upper()
        sources_text = ""
        for s in item.sources:
            src_line = f"({s.quality_label}) {s.title or s.url}"
            if s.url:
                src_line += f" — {s.url}"
            sources_text += f"    - {src_line}\n"

        return (
            f"**[{label} confidence: {item.confidence:.2f}]** {item.claim}\n"
            f"  Sources:\n{sources_text}"
        )

    # ------------------------------------------------------------------
    # Cross-session serialization
    # ------------------------------------------------------------------

    def serialize_context(self, context: AgenticResearchContext) -> str:
        return context.model_dump_json()

    @staticmethod
    def deserialize_context(data: str) -> AgenticResearchContext:
        return AgenticResearchContext.model_validate_json(data)

    # ------------------------------------------------------------------
    # Context merger (combine multiple research contexts)
    # ------------------------------------------------------------------

    def merge_contexts(
        self,
        contexts: list[AgenticResearchContext],
    ) -> AgenticResearchContext:
        if not contexts:
            return AgenticResearchContext()
        if len(contexts) == 1:
            return contexts[0]

        all_items: list[ResearchContextItem] = []
        all_caveats: list[str] = []
        seen_finding_ids: set[str] = set()
        total_sources = 0
        total_confidence = 0.0
        min_freshness = 1.0

        for ctx in contexts:
            total_sources += ctx.source_count
            total_confidence += ctx.overall_confidence
            min_freshness = min(min_freshness, ctx.freshness_score)
            all_caveats.extend(ctx.caveats)
            for item in ctx.items:
                if item.finding_id and item.finding_id in seen_finding_ids:
                    continue
                if item.finding_id:
                    seen_finding_ids.add(item.finding_id)
                all_items.append(item)

        all_items.sort(key=lambda i: i.confidence, reverse=True)
        n = len(contexts)
        return AgenticResearchContext(
            query="; ".join(c.query for c in contexts if c.query),
            items=all_items,
            overall_confidence=round(total_confidence / n, 2) if n else 0.0,
            source_count=total_sources,
            freshness_score=round(min_freshness, 2),
            caveats=all_caveats[:10],
        )

    # ------------------------------------------------------------------
    # Confidence-weighted fact extraction
    # ------------------------------------------------------------------

    def extract_facts(
        self,
        context: AgenticResearchContext,
        *,
        min_confidence: float = 0.5,
        max_facts: int = 20,
    ) -> list[tuple[str, float, list[str]]]:
        """Extract (claim, confidence, source_urls) tuples suitable for inline
        injection into a prompt preamble."""
        facts: list[tuple[str, float, list[str]]] = []
        for item in sorted(context.items, key=lambda i: i.confidence, reverse=True):
            if item.confidence < min_confidence:
                continue
            urls = [s.url for s in item.sources if s.url]
            facts.append((item.claim, item.confidence, urls))
            if len(facts) >= max_facts:
                break
        return facts

    def render_fact_preamble(
        self,
        context: AgenticResearchContext,
        *,
        min_confidence: float = 0.5,
    ) -> str:
        facts = self.extract_facts(context, min_confidence=min_confidence)
        if not facts:
            return ""
        lines: list[str] = [
            "## Research-Sourced Facts",
            "",
            "The following facts were obtained from live research. "
            "Each includes its confidence score and source URL(s).",
            "",
        ]
        for i, (claim, conf, urls) in enumerate(facts, 1):
            url_str = ", ".join(urls) if urls else "no source URL"
            lines.append(f"{i}. [{conf:.0%} confidence] {claim}")
            lines.append(f"   Source(s): {url_str}")
            lines.append("")
        return "\n".join(lines)
