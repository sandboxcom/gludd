# Research Mechanisms — gludd Agentic Research Backend

## Overview

gludd uses **SearXNG** as its privacy-respecting, self-hosted meta-search backend for
agentic research. SearXNG aggregates results from 80+ search engines — academic
databases (arXiv, PubMed, Semantic Scholar, CORE), technical sources (GitHub,
StackOverflow, MDN, PyPI), and general web engines (DuckDuckGo, Brave) — returning
structured JSON that agents consume directly.

This replaces ad-hoc web scraping and brittle search-engine wrappers with one
search contract. Controller-local work uses `NativeSearxRuntime` in
`general_ludd.searx`; the older HTTP client remains an explicit compatibility
path for an operator-managed remote service.

## Architecture

```text
┌─────────┐   direct WSGI    ┌─────────────┐    80+ engines   ┌──────────────┐
│  gludd  │ ────────────────→│ searx.webapp│ ────────────────→│ arXiv        │
│  daemon │  /search + JSON  │ in process  │                  │ PubMed       │
│  agents │ ←─────────────── │ no listener │                  │ GitHub       │
└─────────┘                  └─────────────┘                  │ DuckDuckGo   │
                                                             │ ...          │
                                                             └──────────────┘
```

### Components

| Component | Location | Purpose |
|-----------|----------|---------|
| Native lifecycle | `src/general_ludd/searx/native.py` | Direct WSGI startup, search, health, and cleanup |
| Generated config | `src/general_ludd/searx/config.py` | Namespaced secure upstream settings |
| Ansible actions | `collections/ansible_collections/general_ludd/travel/plugins/action/` | Idempotent controller-local lifecycle and search |
| Remote compatibility | `src/general_ludd/connectors/searx.py` | Explicit HTTP adapter with URL validation |

## Engine Weighting

Engines are weighted to prioritise authoritative, peer-reviewed sources:

| Tier | Weight | Engines | Use Case |
|------|--------|---------|----------|
| Academic | 2.0 | arXiv, PubMed, Semantic Scholar, CORE, OpenAire, Crossref, DBLP | Primary research: papers, pre-prints, citations |
| Technical | 1.5 | GitHub, GitLab, StackOverflow, Wikipedia, MDN, PyPI, npm | Implementation: code, docs, Q&A |
| General | 1.0 | DuckDuckGo, Brave, Qwant, Startpage | Fallback web search |
| News | 0.5 | Google News, Bing News | Time-sensitive queries (disabled by default) |
| Disabled | — | Google, Google Images, Maps, Videos | Available via `engines=` parameter |

Google web search is disabled by default (privacy + rate-limit posture) but agents can
opt in by passing `engines=["google"]` to request it explicitly.

## Category-Based Querying

The `SearxNGClient.search()` method accepts `categories` to target specific domains:

```python
from general_ludd.retrieval import SearxNGClient

client = SearxNGClient()

# Research-focused query (default)
result = await client.search(
    "transformer architecture attention mechanism",
    categories=["science"],
    time_range="year",
)

# Technical implementation query
result = await client.search(
    "fastapi lifespan event async pattern",
    categories=["it"],
)

# Broad search
result = await client.search(
    "llm agent autonomous sdlc",
    categories=["science", "it", "general"],
)
```

**Available categories:**
- `science` — academic/technical databases (arXiv, PubMed, S2, CORE, etc.)
- `it` — programming/IT (GitHub, StackOverflow, MDN, PyPI, etc.)
- `general` — web search fallback
- `news`, `files`, `images`, `videos`, `map`, `music`, `social media`, `packages`

## How Agents Use It

### 1. Pre-implementation research

Before writing code for an unfamiliar domain, an agent dispatches a multi-time-range
search to gather both recent and foundational context:

```python
results = await client.multi_search(
    "sparc robotics control framework plugin architecture",
    time_ranges=[None, "year", "month"],
)
```

This returns three result sets — all-time (foundational papers), last-year (active
research), and last-month (breaking changes / recent discussions) — giving the agent
a complete picture of the domain.

### 2. Error-resolution lookup

When a test or build fails with an unfamiliar error, the agent searches for it:

```python
result = await client.search(
    "quoted error message exact match",
    categories=["it", "general"],
    time_range="month",
)
```

### 3. Version-migration research

When upgrading a dependency, the agent searches for migration guides:

```python
result = await client.search(
    "pydantic v1 to v2 migration guide breaking changes",
    categories=["it"],
    time_range="year",
)
```

### 4. Evidence-gathering for code review

When reviewing a PR, the agent searches for best-practice patterns:

```python
result = await client.search(
    "sqlalchemy async session lifespan pattern",
    categories=["it"],
    engines=["github", "stackoverflow", "mdn"],
)
```

## Caching Strategy

The client caches results to disk via `diskcache` (the same library used by the codebase
indexer and web retriever). Cache behaviour:

- **TTL**: 30 minutes by default (`GLUDD_SEARX_CACHE_TTL` env var)
- **Key**: composite of query + categories + time_range + page + language
- **Bypass**: `bypass_cache=True` forces a live query
- **Location**: `.gludd/searx_cache/` (auto-created, `0o700` permissions)

This prevents redundant API calls when multiple agents research the same topic, and
respects SearXNG's upstream rate limits.

## Rate Limiting

Two layers of rate limiting:

1. **Client-side** (`SearxNGClient._rate_limit`): minimum 2.0s between requests
   (`GLUDD_SEARX_RATE_LIMIT` env var). Enforced with `time.monotonic()` sleep.
2. **Server-side** (SearXNG `settings.yml` `server.limiter: true`): SearXNG's built-in
   limiter prevents upstream engines from being overwhelmed.

The client-side limiter is cooperative — it waits rather than failing, so agent code
doesn't need retry logic.

## Health Checking

```python
health = await client.health()
# {"ok": True, "detail": "SearXNG reachable, engines: 23 results", "base_url": "..."}
```

The daemon's health endpoint should include SearXNG status so operators can verify
the research backend is available.

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `GLUDD_RESOURCE_NAMESPACE` | Project-path digest | Native resource namespace override |
| `GLUDD_SEARXNG_URL` | Unset | Explicit remote compatibility service URL |
| `GLUDD_SEARX_CACHE_TTL` | `1800` | Cache TTL in seconds |
| `GLUDD_SEARX_RATE_LIMIT` | `2.0` | Minimum seconds between requests |
| `GLUDD_SEARX_TIMEOUT` | `30.0` | HTTP request timeout in seconds |

## Setup

Install the official SearXNG source package on the controller so that
`searx.webapp` imports successfully. Gludd starts it through the native Python
lifecycle and exposes no local port. Ansible users manage the runtime with
`general_ludd.travel.searxng_instance`; check mode predicts the transition
without importing or starting the service.

See [Native SearXNG Integration](../features/SEARXNG_NATIVE_INTEGRATION.md) for
the supported upstream contract, examples, security posture, practitioner
reports, and the explicit remote rollback path.

## Search Strategies for Agents

### Strategy 1: Multi-Pass Refinement

1. **Broad search** (all categories, no time filter) → survey the landscape
2. **Narrow by category** (science for papers, it for code) → drill into specifics
3. **Time-filtered** (year, month) → find recent developments
4. **Engine-specific** (arXiv only, GitHub only) → exhaustive coverage in one domain

### Strategy 2: Query Decomposition

For complex questions, decompose into sub-queries and run them in parallel:

```python
import asyncio

sub_queries = [
    "transformer architecture overview",
    "attention mechanism variants",
    "transformer training efficiency techniques",
    "transformer inference optimization",
]

results = await asyncio.gather(*[
    client.search(q, categories=["science"], time_range="year")
    for q in sub_queries
])
```

### Strategy 3: Evidence Triangulation

For claims that need verification, query the same topic across multiple source types:

1. Academic (science category) → papers and pre-prints
2. Technical (it category) → implementations and documentation
3. General (general category) → broader context and news

If all three tiers corroborate, confidence is high. If only one tier has results,
treat with caution.

## Unresponsive Engine Handling

SearXNG returns `unresponsive_engines` in every response — a list of `[engine_name,
error_type]` pairs. The client surfaces this in `SearxResponse.unresponsive_engines`.
Agents should log unresponsive engines but continue with available results; a single
downstream outage (e.g., Semantic Scholar returning 503) does not break the search.
