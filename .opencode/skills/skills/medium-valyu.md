---
{
  "name": "medium-valyu",
  "description": "Real-time web search and specialized data access. 36+ data sources: SEC filings, PubMed, ChEMBL, FRED, patent databases, academic publishers. Grounded answers with citations. Source: unicodeveloper/medium-2026.",
  "tags": [
    "search",
    "data",
    "research",
    "sec",
    "pubmed",
    "finance",
    "medium-2026"
  ],
  "category": "data"
}
---

# Valyu: Web Search and Specialized Data Access

Connect to 36+ specialised data sources through a single API for authoritative,
current information beyond cached training data.

## Data Sources

- **SEC filings**: 10-K, 10-Q risk factors and financial disclosures
- **PubMed**: biomedical research literature
- **ChEMBL**: 2.5M bioactive compound database
- **ClinicalTrials.gov**: clinical trial data and outcomes
- **FRED**: Federal Reserve economic indicators
- **BLS**: Bureau of Labor Statistics
- **Patent databases**: USPTO and international patents
- **Academic publishers**: peer-reviewed research

## Usage Patterns

**Targeted search** — specify data sources for precision:
```python
result = client.search(
    query="risk factors in latest 10-K filings for semiconductor companies",
    search_type="proprietary",
    included_sources=["valyu/valyu-sec-filings"],
    max_num_results=5
)
```

**Cross-source search** — combine multiple sources:
```python
result = client.search(
    query="GLP-1 receptor agonists drug interactions",
    search_type="all",
    included_sources=["valyu/valyu-pubmed", "valyu/valyu-chembl"],
    max_num_results=10
)
```

**Grounded answers with citations**:
```python
answer = client.context(
    query="Key risk factors disclosed by NVIDIA in recent 10-K?",
    search_type="proprietary"
)
```

## Performance

- FreshQA benchmark: 79% vs Google 39% vs Exa 24%
- Finance queries: 73% vs Google 55%
- MedAgent (562 queries): 48% (leading)

## Rules

- Always surface sources to users — trustworthiness comes from citation trail
- Use targeted sources when the domain is known
- Use the Answer API when users need conclusions, not raw documents
- Never fabricate data — if the source doesn't have it, say so

## Install reference

Original: `npx skills add https://github.com/valyuai/skills --skill valyu-best-practices`
