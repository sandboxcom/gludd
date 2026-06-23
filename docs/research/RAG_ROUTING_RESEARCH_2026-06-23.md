# Semantic-Aware RAG Research — TrojiRAG / PathRAG / GraphRAG / LightRAG

**Date:** 2026-06-23
**Question:** Can semantic-aware shortest-path RAG systems improve gludd's task→model/skill/prompt routing?

## TL;DR

**Steal the substrate (embeddings + similarity-weighted retrieval), not the systems.**

The named RAG systems solve document QA at a cost structure (LLM-driven KG indexing) that's wrong for routing a ~10-arm bandit. But gludd's literal-substring skill matching and flat 10-bucket task taxonomy ARE real gaps.

## Systems Researched

| System | Core Algorithm | GitHub | Maturity | Fit for Gludd |
|---|---|---|---|---|
| **"TrojiRAG"** | Likely meant **S-Path-RAG** (semantic shortest-path) or **TrojRAG** (security attack — not a retriever) | furonglegend/spathrag (1★, no license) | Research prototype | Skip |
| **PathRAG** | Flow-based resource propagation with α-decay + θ-threshold pruning | BUPT-GAMMA/PathRAG (368★, MIT) | Research code (27 commits) | Interesting algorithm, thin code |
| **GraphRAG (MS)** | Hierarchical Leiden community detection + LLM community reports | microsoft/graphrag (33.9k★, MIT) | Mature but expensive (281 min indexing for 1M tokens) | Overkill for ~10 task types |
| **LightRAG** | Dual-level vector-keyword retrieval + incremental updates | HKUDS/LightRAG (36.9k★, MIT) | Most production-ready | Best to study, don't import |

## Gludd's Current Routing (gaps identified)

| Component | Current | Gap |
|---|---|---|
| Skill matching | Literal substring `pattern.lower() in text_lower` | Fails on synonyms ("concurrency bug" ≠ "race condition") |
| Task classification | Flat 10-bucket enum, caller-assigned | No similarity between BUG_FIX and DEBUGGING |
| Prompt template | Hardcoded dict keyed on work_type | No learning, no semantic match |
| AdaptiveRouter | Contextual bandit per task_type | Good bandit, but flat — no cross-type bootstrapping |

## Recommended Integration (3 Tiers)

### Tier 1 (~1 day, HIGH ROI): Embedding-based skill matching
Replace `SkillRegistry.match_trigger` substring logic with embedding cosine similarity over skill descriptions. Uses existing OpenAI client (text-embedding-3-small) or local sentence-transformer. Keeps substring as fallback.

### Tier 2 (~1 week, MEDIUM ROI): Task-similarity graph in Postgres
Add `pgvector`, a `task_embeddings` table, extend `AdaptiveRouter._get_best_from_history` to weight aggregates by cosine similarity to the current task. Turns flat 10-arm bandit into a soft cluster.

### Tier 3 (~1 month, SPECULATIVE): PathRAG-style flow pruning
Build a routing graph {tasks, skills, prompts, models, outcomes} with typed edges. Run α-decay resource propagation from current task node. Only worth doing if Tiers 1+2 don't capture most value — and they probably will at N=12 candidates.

## What NOT to do

- Do NOT import GraphRAG/LightRAG/PathRAG as dependencies
- Do NOT use LLM-driven entity extraction for a ~10-node routing graph
- Do NOT adopt S-Path-RAG (research prototype, no license, partial code)

## Effort Estimate

| Tier | LOC | Files | New deps | Effort |
|---|---|---|---|---|
| 1 | ~60-80 | skills/registry.py, skills/embeddings.py (new), tests | none (reuse OpenAI client) | 1 day |
| 2 | ~300 | scoring/router.py, scoring/task_embeddings.py (new), db/models.py, migration | pgvector | 3-5 days |
| 3 | ~600 | scoring/routing_graph.py (new), scoring/flow_prune.py (new) | networkx | 2-4 weeks |
