# Agentic Memory: Research & Gap Analysis

**Status**: v0.1 — initial research (2026-07-25)
**Scope**: Survey of agentic memory approaches + audit of gludd's existing memory subsystem

---

## 1. Approaches Considered

| # | Approach | Description | Pros | Cons | Maturity |
|---|----------|-------------|------|------|----------|
| 1 | **Vector/Embedding-based** (Chroma, Pinecone, pgvector, FAISS) | Store dense vector embeddings + cosine/L2 similarity search | Semantic matching; scalable; mature ecosystems (pgvector for SQL co-location) | Embedding cost (if using OpenAI); cold-start for new terms; dim mismatch across embedders | Production |
| 2 | **Knowledge Graph** (Neo4j, RDFlib) | Entity-relationship memory with reasoning over typed edges | Explainable retrieval; supports multi-hop reasoning; schema-enforced | Complex ingestion pipeline; entity extraction requires separate ML step; query latency grows with graph depth | Production |
| 3 | **RAG / Hybrid Search** (LangChain retrievers, document chunking, BM25+embeddings) | Combine keyword (lexical) with semantic (embedding) retrieval | Best of both worlds; handles rare terms and synonyms; chunking = fine-grained recall | Chunk boundary problems; requires two indices; latency doubles | Production |
| 4 | **Episodic Memory** (conversation history, session summaries, timeline reconstruction) | Structured records of past executions with outcomes and takeaways | Proven in gludd; straightforward; easy to query by task_type/outcome | No semantic search; manual metadata labeling required; linear scan over all episodes | Production |
| 5 | **Semantic Memory** (facts, concepts, learned knowledge) | Consolidated abstractions extracted from episodic records | Compact; preserves insight while discarding detail; queryable by concept | Consolidation is lossy; timing/trigger heuristics are brittle; requires model calls for summarization | Research → Production |
| 6 | **Procedural Memory** (task patterns, workflows, checklists) | Reusable execution templates: "when X, do Y then Z" | Directly actionable; reduces repeated mistakes; encodes institutional knowledge | Requires structured extraction from episodes; brittle if task descriptions shift | Research |
| 7 | **Working Memory** (active context, scratchpad, todo state) | Scoped key-value state for the current session/task | Essential for agent orchestration; prevents context loss across tool calls | Volatile by design; must be supplemented by persistent memory for long-term recall | Production |
| 8 | **Hierarchical Memory / Summarization Cascades** (raw → compressed → abstract) | Multi-level compaction: detail → summary → abstraction | Preserves both granular recall and high-level insight; tunable depth | Cascade triggers are heuristic; risk of over-compaction (lost signal); model cost per level | Research |
| 9 | **MemGPT-style / OS-paging** (virtual context management) | Treat LLM context window as "RAM"; page memories in/out from persistent store | Solves context-length problem; interrupt-driven; proven in multi-session chat and document analysis | Complex control flow; paging decisions require model calls; page-fault latency | Research → Production |
| 10 | **Recurrent / Sliding-window** (attention sinks, streaming memory) | Fixed-size token budget with FIFO eviction + attention-sink retention of first N tokens | Simple; no external storage; predictable latency | No long-term recall; critical early context can be evicted; no cross-session persistence | Production |
| 11 | **Reflection / Self-critique** (Reflexion, Self-Refine) | Agent evaluates its own outputs and stores corrected versions as memory | Self-improving; catches errors without human feedback; compact (only stores corrections) | Requires strong base model; reflection quality degrades with model weakness; can reinforce wrong patterns | Research |
| 12 | **Retrieval-Augmented Thought (RAT)** (retrieve → reason → act) | Interleave retrieval steps with reasoning steps | More accurate than single-retrieve-then-act; multi-hop reasoning over memory | Latency multiplies with retrieval depth; requires careful prompt engineering | Research |

### Embedding Model Selection

| Model | Dim | Cost/1M tokens | Latency | Local? | Notes |
|-------|-----|---------------|---------|--------|-------|
| `text-embedding-3-small` (OpenAI) | 1536 | $0.02 | ~50ms | No | Best quality/cost ratio; requires network + API key |
| `text-embedding-3-large` (OpenAI) | 3072 | $0.13 | ~80ms | No | Highest quality; overkill for most agent memory |
| `all-MiniLM-L6-v2` (sentence-transformers) | 384 | Free | ~5ms | Yes | Good quality for local use; 80MB model |
| `all-mpnet-base-v2` (sentence-transformers) | 768 | Free | ~15ms | Yes | Best local quality; 420MB model |
| HashEmbedder (gludd built-in) | 256 | Free | <1ms | Yes | Deterministic; vocabulary-overlap only; no synonym awareness |
| `nomic-embed-text` (Ollama) | 768 | Free | ~20ms | Yes | Good local quality; Ollama required |

**gludd default**: HashEmbedder (256-dim, deterministic, zero-cost). **Recommendation**: support pluggable embedders — HashEmbedder for deterministic tests, sentence-transformers for local semantic search, OpenAI for best quality.

---

## 2. gludd Existing Memory Code — Capability Map

### 2.1 What exists (by memory type)

| Memory Type | gludd Module | What it does | Quality |
|-------------|-------------|--------------|---------|
| **Episodic** | `memory/episodic.py` | Structured records of task executions (agent_id, task_type, outcome, context, takeaway, error_message, duration) | Good — well-structured, stored in `memory_records` table under namespace `episodic` |
| **Semantic** | `memory/consolidation.py` | Periodic summarization of old episodes grouped by task_type; stores in `consolidated` namespace | Good — rule-based grouping, optional model-driven consolidation |
| **Retrieval** | `memory/retrieval.py` | Keyword-overlap scoring (Jaccard) + recency boost + exact match bonuses | **Limited** — no embedding/semantic search; linear scan over all episodes |
| **Cross-task learning** | `memory/cross_task.py` | Extracts patterns across episodes and consolidated summaries; generates recommendations per task type | Good — statistical pattern extraction |
| **Local memory** | `memory/local.py` | diskcache-backed key-value store; drop-in replacement for SQL-backed MemoryRepository | Good — no-db alternative |
| **Cross-conversation** | `memory/cross_conversation.py` | LangGraph Store API wrapper; put/get/search/delete with TTL and namespace isolation | Good — with graceful degradation |
| **Cross-conversation memory** | `memory/cross_convo_memory.py` | Conversation lifecycle, working memory, context injection, summaries, decision logging | Good — working memory scoped per conversation |
| **Working memory** | `memory/cross_convo_memory.py` (NAMESPACE_WORKING) | Scoped key-value persistence per conversation | Good |
| **Task embeddings** | `scoring/task_embeddings.py` | Canonical task-type embeddings for adaptive router; cosine similarity between task types | Good — but only for task TYPES, not individual memory records |
| **Skill embeddings** | `skills/embeddings.py` | HashEmbedder + OpenAIEmbedder + cosine_similarity | Good — pluggable embedder interface |
| **Embedding API** | `routers/embeddings.py` | POST /api/embeddings/similar, /compare, /search for skills/task_types/prompts/traces | Good — corpuses exist but memory records are not one of them |

### 2.2 Database schema

- **`memory_records`** table: `(id, project_id, agent_id, key, value, namespace, ttl_seconds, created_at, updated_at)` — key-value store with namespace isolation and TTL
- **`task_embeddings`** table: `(task_type, canonical_text, embedding [JSON float array], dim, updated_at)` — one row per task type

### 2.3 Existing embedders

- **`HashEmbedder`** (256-dim): SHA256-based feature hashing of tokens → L2-normalized vector
- **`OpenAIEmbedder`** (1536-dim): `text-embedding-3-small` via OpenAI API
- **`cosine_similarity()`**: pure Python, no numpy dependency

---

## 3. Gap Analysis

### HIGH Priority

| # | Gap | Impact | Current workaround | Recommendation |
|---|-----|--------|-------------------|----------------|
| G1 | **No embedding-based semantic search over memory records** | Retrieval is keyword-only; "fix concurrency bug" won't find "race condition fix" episodes | `MemoryRetriever.query()` uses Jaccard overlap | Build `MemoryEmbeddingStore`: embed memory record values → cosine similarity search |
| G2 | **No vector database integration** | Linear scan over all records for every query; O(n) per search | None — all searches are linear scans | Add pluggable vector backend: in-memory first, pgvector/chroma optional |
| G3 | **No embedding storage for individual memory records** | Each search requires re-embedding record values or scanning raw text | Not done — keyword-only | Store embeddings alongside records (in-memory index or DB column) |
| G4 | **No RAG pipeline for memory** | Cannot answer questions like "what did we learn about X?" with retrieved + generated response | Not done | Build RAG chain: embed query → retrieve top-k → generate answer |

### MEDIUM Priority

| # | Gap | Impact | Current workaround | Recommendation |
|---|-----|--------|-------------------|----------------|
| G5 | **Procedural memory missing** | No reusable task templates; each session reinvents workflows | None | Extract procedural patterns from episodes: "when task_type=X and error=Y, apply fix Z" |
| G6 | **No hierarchical summarization cascade** | Consolidation is single-level: raw episode → one summary; no multi-level abstraction | Single consolidation step in `MemoryConsolidator` | Add cascade: raw → compressed → abstract → cross-domain insight |
| G7 | **No MemGPT-style context paging** | Context window management is manual; no automatic memory paging | `CrossConversationMemory.import_context()` is manual keyword matching | Add virtual context management: page relevant memories into context window before tool calls |
| G8 | **No hybrid search (BM25 + embeddings)** | Pure embedding search can miss exact keyword matches; pure keyword misses synonyms | `MemoryRetriever` is keyword-only; no embedding path exists | Add hybrid: BM25 score + cosine similarity → weighted combination |
| G9 | **No knowledge graph** | Cannot answer multi-hop questions like "what tool caused error X after task Y?" | None | Extract entities from memory records; build graph with typed relations |

### LOW Priority

| # | Gap | Impact | Current workaround | Recommendation |
|---|-----|--------|-------------------|----------------|
| G10 | **No memory deduplication** | Duplicate/similar memories accumulate; retrieval returns redundant results | None | Use cosine similarity to detect near-duplicates; merge or deduplicate |
| G11 | **No memory importance scoring** | All memories weighted equally; critical lessons lost in noise | Recency boost only | Add importance score: based on outcome severity, frequency of recall, user feedback |
| G12 | **No cross-agent memory sharing** | Each agent's memory is isolated; lessons learned by one agent are invisible to others | None | Add agent-group namespaces or federated memory query |

---

## 4. Recommended Implementation Plan

### Phase 1: Semantic Search (THIS PR)

- [x] Research: survey 12 agentic memory approaches
- [x] Audit: document existing gludd memory code
- [x] Build `MemoryEmbeddingStore`: in-memory vector index over memory records
  - Uses existing `HashEmbedder` (256-dim, zero-cost, deterministic)
  - Supports: `add()`, `search()`, `delete()`, `count()`
  - Cosine similarity via existing `cosine_similarity()`
  - TTL-aware: expired records are skipped
- [x] TDD: `tests/unit/test_memory_embedding_store.py`
- [x] Config: `config/examples/memory_config_example.yml`

### Phase 2: Pluggable Vector Backend

- [ ] Abstract `VectorStore` interface (same as `Embedder` Protocol pattern)
- [ ] `InMemoryVectorStore`: current implementation (reference)
- [ ] `PgVectorStore`: pgvector-backed (requires pgvector extension)
- [ ] `ChromaVectorStore`: ChromaDB-backed (requires `chromadb` package)
- [ ] `FAISSVectorStore`: FAISS-backed (requires `faiss-cpu`)
- [ ] Config-driven backend selection
- [ ] Benchmark: recall@k for each backend vs. keyword baseline

### Phase 3: RAG Pipeline

- [ ] `MemoryRAG` class: embed query → retrieve top-k → prompt LLM → return answer
- [ ] Hybrid search: BM25 token score + embedding cosine score → weighted rank
- [ ] Chunking strategy for long memory values
- [ ] Citation: each retrieved memory includes its record_id + agent_id + timestamp

### Phase 4: Advanced Memory

- [ ] Hierarchical summarization cascade
- [ ] Procedural memory extraction
- [ ] MemGPT-style virtual context management
- [ ] Knowledge graph construction from memory records

---

## 5. Key Design Decisions

### Storage Backend

**Decision**: In-memory vector index (dict-based) with optional persistence.

**Rationale**:
- gludd's memory records are typically O(1000-10000), fitting in memory
- Avoids dependency on pgvector, Chroma, FAISS for the MVP
- Follows same pattern as `CrossConversationStore` (in-memory + optional persistence)
- Future: abstract `VectorStore` interface for pluggable backends

### Embedding Model

**Decision**: `HashEmbedder` (256-dim) as default; pluggable to any `Embedder` Protocol.

**Rationale**:
- Zero cost (deterministic, no network calls)
- Deterministic (same text → same vector every time; good for testing)
- Lightweight (no ML model dependencies)
- Already exists in `skills/embeddings.py`
- Pluggable: swap in `OpenAIEmbedder`, sentence-transformers, or Ollama embedders

### Memory Record Embedding Strategy

**Decision**: Embed the `value` field of memory records (JSON string) using the same embedder.

**Rationale**:
- `value` is the richest semantic signal
- Optionally concatenate `agent_id`, `namespace`, and `value` for scoped search
- Store embeddings in-memory (dictionary keyed by record_id) — not in the DB
- When a record is deleted or expires, its embedding is removed from the index

### Search Algorithm

**Decision**: Cosine similarity over all stored embeddings, return top-k above threshold.

**Rationale**:
- Match existing `cosine_similarity()` in `skills/embeddings.py`
- O(n) linear scan is acceptable for O(10k) records
- Future: add approximate nearest neighbor (ANN) when record count exceeds 50k

### Chunking Strategy (Future)

**Decision**: Fixed-size token chunking (256 tokens) with 50-token overlap.

**Rationale**:
- Simple; works for most memory record sizes
- 50-token overlap prevents boundary-cut semantics
- Consistent with LangChain's `RecursiveCharacterTextSplitter`
