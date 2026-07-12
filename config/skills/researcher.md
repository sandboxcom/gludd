---
name: researcher
description: Execute agentic web research via SearXNG with verified sources, confidence scoring, and structured citation output
model_profile: null
tools: [read, write, bash]
trigger_patterns: ["research", "look up", "find out about", "search for", "what is known about", "cite sources", "verify facts"]
tags: [research, retrieval, searx, citations, verification]
---

# Researcher Skill

## Purpose

Execute live web research via SearXNG with real, verifiable sources. Returns
structured findings with confidence scores and full citation chains so agents
can use research results with trust.

## When to Use

Trigger when:
- An agent needs to look up facts, APIs, documentation, or current events
- The task requires cited, verifiable information
- A research topic has gone stale and needs re-indexing
- Cross-referencing findings from multiple sources

## How It Works

The researcher agent pipeline:

```
User intent → ResearchQuery construction → SearXNG search (multi-engine)
    → Source verification (real URLs only, no placeholder/blocked)
    → Confidence scoring (source quality, recency, corroboration)
    → ResearchReport with full citation chain
    → ResearchIndex updated (freshness TTL, citation graph)
    → AgenticContextInjector → agent prompt
```

## Integration Points

### 1. As a Subagent (`research` agent type)

Registered in `config/agents/default_agents.yml` as:
```yaml
- name: research
  type: subagent
  permissions:
    can_read: true
  max_concurrent: 3
```

Primary agents can dispatch `research` subagents for any web research task.

### 2. Via ResearchIndex

The `ResearchIndex` persists research topics with freshness TTL (default 7 days).
Before dispatching a research subagent, check `research_index.needs_reindex(query)`
— if the topic is fresh enough, use the cached results directly.

### 3. Via AgenticContextInjector

Before dispatching a working agent (build, plan, general), inject research
findings into its system prompt via:

```python
from general_ludd.retrieval.agentic_context import AgenticContextInjector

injector = AgenticContextInjector()
context = injector.build_context(query="How does X work?", findings=report.findings)
prompt = injector.inject_into_system_prompt(system_prompt, context)
```

## Confidence Model

| Label    | Range   | Meaning                                    |
|----------|---------|--------------------------------------------|
| HIGH     | ≥ 0.8   | Multiple authoritative sources agree; fact is well-established |
| MEDIUM   | 0.5–0.79| Some corroboration; reasonable but verify   |
| LOW      | < 0.5   | Single source or low-quality domain; treat as hint only |

Confidence factors:
- Number of corroborating sources (log-scaled bonus)
- Share of high-quality/authoritative domains (arxiv, IEEE, MDN, etc.)
- Recency bonus for sources published within 90 days
- Penalty for link farms and low-quality aggregators

## Source Quality Tiers

| Tier        | Example domains                                    |
|-------------|---------------------------------------------------|
| Authoritative | arxiv.org, pubmed, IEEE, IETF, W3C, MDN, docs.python.org |
| Standard    | stackoverflow.com, github.com, medium.com, dev.to |
| Low-quality  | pinterest.com, quora.com, answers.com, wikihow.com |

Low-quality domains are excluded from search results by default.

## Re-Indexing Triggers

Topics are re-queued for research when:
1. Freshness TTL expired (default 7 days since last research)
2. Overall confidence score below threshold (default 0.5)
3. User explicitly requests a fresh search

## Output Format

A `ResearchReport` contains:
- **query**: the original research question
- **summary**: natural-language synthesis of findings
- **findings**: list of `ResearchFinding` objects, each with:
  - `claim`: the synthesized factual statement
  - `confidence`: 0.0–1.0 score
  - `citations`: list of `Citation` objects with URL, title, domain
  - `corroborating_sources`: how many other findings support this one
- **confidence_overall**: average confidence across all findings
- **sources_consulted/used**: transparency on source breadth

## Example

```python
from general_ludd.agents.researcher import ResearcherAgent
from general_ludd.retrieval.searx_client import SearxNGClient

searx = SearxNGClient()
researcher = ResearcherAgent(searx_client=searx)

report = await researcher.research(
    "What are the latest best practices for Rust async error handling?",
    categories=["it", "science"],
    time_range="year",
)

print(report.summary)
for finding in report.findings:
    print(f"[{finding.confidence:.2f}] {finding.claim}")
    for cit in finding.citations:
        print(f"  -> {cit.url}")
```
