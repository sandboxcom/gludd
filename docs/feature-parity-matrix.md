# Hottentot-Agent Feature Parity Matrix

Competitive analysis of agentic coding tools, mapping features to hottentot-agent
status (EXISTS / PARTIAL / MISSING / N/A) with source references.

## Legend
- **EXISTS**: Fully implemented and tested
- **PARTIAL**: Skeleton or incomplete implementation
- **MISSING**: Not implemented
- **N/A**: UI-only feature, irrelevant to hottentot-agent's CLI/todo interface

---

## 1. MCP (Model Context Protocol)

| Feature | Aider | Goose | OpenCode | Codex | Cline | Zed | OpenHands | hottentot |
|---------|-------|-------|----------|-------|-------|-----|-----------|-----------|
| MCP client (connect to MCP servers) | MISSING | EXISTS | EXISTS | EXISTS | EXISTS | EXISTS | EXISTS | MISSING |
| MCP server (expose tools via MCP) | MISSING | MISSING | MISSING | PARTIAL | MISSING | MISSING | MISSING | MISSING |
| stdio transport | N/A | EXISTS | EXISTS | EXISTS | EXISTS | EXISTS | EXISTS | MISSING |
| HTTP/SSE transport | N/A | EXISTS | EXISTS | EXISTS | EXISTS | EXISTS | EXISTS | MISSING |
| OAuth for remote MCP | N/A | MISSING | EXISTS | EXISTS | N/A | EXISTS | N/A | MISSING |
| Tool schema caching | N/A | N/A | PARTIAL | N/A | N/A | N/A | N/A | MISSING |
| Tool discovery (lazy load) | N/A | N/A | PARTIAL | N/A | N/A | N/A | N/A | MISSING |
| Per-agent MCP enable/disable | N/A | N/A | EXISTS | N/A | N/A | EXISTS | N/A | MISSING |

**Key insights from issues:**
- OpenCode #8625: MCP tool descriptions can consume 10%+ of context window; lazy `MCPSearch` tool needed
- OpenCode #29939: MCP servers spawn duplicate processes per session (24 node processes for 5 MCPs); need singleton
- Goose #9332: stdio MCP subprocesses killed by `PR_SET_PDEATHSIG` on multi-threaded runtimes
- Goose #9469: MCP proxy for governance/audit (Vaara) — tamper-evident audit logs at protocol boundary

**Priority: HIGH** — MCP is the universal tool integration standard. Without it, hottentot-agent
cannot connect to external tools (databases, APIs, cloud infra). Implementation plan:
1. `src/agentic_harness/mcp/client.py` — MCP client with stdio + HTTP transports
2. `src/agentic_harness/mcp/registry.py` — MCP server registry from YAML config
3. `src/agentic_harness/mcp/tool_adapter.py` — adapt MCP tools to playbook calls
4. `config/mcp_servers/` — YAML server definitions
5. Integrate MCP tool discovery into event loop tick

---

## 2. Skills / Patterns / Prompt Templates

| Feature | Aider | Goose | OpenCode | Fabric | hottentot |
|---------|-------|-------|----------|--------|-----------|
| Skill/template loading | MISSING | EXISTS | EXISTS | EXISTS | PARTIAL |
| SKILL.md with frontmatter | N/A | N/A | EXISTS | N/A | MISSING |
| Pattern library (100+ templates) | N/A | N/A | N/A | EXISTS | MISSING |
| Per-pattern model routing | N/A | N/A | N/A | EXISTS | MISSING |
| Skill discovery by description | N/A | N/A | EXISTS | EXISTS | MISSING |
| Jinja2 prompt rendering | N/A | N/A | N/A | N/A | EXISTS |
| Prompt registry | N/A | N/A | N/A | N/A | EXISTS |
| Community skill marketplace | N/A | PARTIAL | N/A | EXISTS | MISSING |

**What exists:** `PromptRegistry` with Jinja2 rendering (`src/agentic_harness/prompts/registry.py`).
**What's missing:** No SKILL.md format, no discovery by description, no per-skill model routing,
no community marketplace, no skill → agent binding.

**Priority: MEDIUM** — The Jinja2 registry is a good foundation. Extend with:
1. SKILL.md format with YAML frontmatter (name, description, model_profile, tools)
2. Discovery paths: `~/.config/hottentot/skills/`, `.hottentot/skills/`
3. Skill → agent binding in agent config
4. Per-skill model routing via `model_profile` field

---

## 3. Multi-Model Routing

| Feature | Aider | Goose | OpenCode | Codex | hottentot |
|---------|-------|-------|----------|-------|-----------|
| Multiple providers | EXISTS | EXISTS | EXISTS | EXISTS | EXISTS |
| Per-agent model selection | N/A | EXISTS | EXISTS | N/A | EXISTS |
| Role-based routing (review vs build) | EXISTS | EXISTS | EXISTS | N/A | EXISTS |
| Fallback chain execution | N/A | N/A | N/A | N/A | PARTIAL |
| Model cost tracking | EXISTS | EXISTS | EXISTS | N/A | EXISTS |
| Weak/cheap model for minor tasks | EXISTS | N/A | EXISTS | N/A | MISSING |
| Reasoning effort control | EXISTS | PARTIAL | N/A | EXISTS | MISSING |

**What exists:** `ModelGateway`, `ModelRouter`, `ProviderRegistry`, 5 YAML profiles, budget tracking.
**What's missing:** Fallback chains declared in YAML but never chased. No weak-model pattern.
No reasoning effort parameter pass-through.

**Priority: MEDIUM** — Foundation is solid. Extend:
1. Implement fallback chain execution in `ModelGateway.call_model()`
2. Add `weak_model_profile` config for commit messages, summaries
3. Pass through `reasoning_effort` parameter to LangChain providers

---

## 4. Agent Architecture

| Feature | AutoGen | CrewAI | LangGraph | MetaGPT | hottentot |
|---------|---------|--------|-----------|---------|-----------|
| Agent registry | EXISTS | EXISTS | N/A | EXISTS | EXISTS |
| Subagent dispatch | EXISTS | EXISTS | N/A | EXISTS | EXISTS |
| Agents-as-tools | EXISTS | N/A | N/A | N/A | MISSING |
| Concurrent execution | EXISTS | EXISTS | EXISTS | EXISTS | EXISTS |
| Max steps per agent | EXISTS | EXISTS | N/A | EXISTS | PARTIAL |
| Permission scoping | N/A | N/A | N/A | EXISTS | EXISTS |
| SOP-based coordination | N/A | N/A | N/A | EXISTS | MISSING |
| Graph-as-agent (state machine) | N/A | N/A | EXISTS | N/A | MISSING |
| Durable checkpointing | N/A | N/A | EXISTS | N/A | MISSING |
| Cryptographic role delegation | N/A | N/A | N/A | EXISTS | MISSING |

**What exists:** `AgentRegistry`, `AgentDispatcher`, `AgentPermission`, 4 built-in agents, semaphore-based concurrency.
**What's missing:** No actual agent step execution loop (max_steps not enforced). No agents-as-tools
pattern. No SOP coordination. No durable checkpointing.

**Priority: HIGH for step loop, LOW for exotic patterns.**
1. Implement agent step execution loop that enforces `max_steps`
2. Add agents-as-tools wrapper (`AgentTool`) for recursive composition
3. Durable checkpointing is deferred (see features-to-decide.md)

---

## 5. Context Management

| Feature | Aider | Goose | OpenCode | Plandex | hottentot |
|---------|-------|-------|----------|---------|-----------|
| Token window tracking | EXISTS | EXISTS | EXISTS | N/A | EXISTS |
| Auto-compaction | N/A | EXISTS | EXISTS | N/A | STUB |
| Smart truncation | N/A | EXISTS | EXISTS | N/A | MISSING |
| Tree-sitter repo map | EXISTS | N/A | N/A | EXISTS | MISSING |
| Selective file loading | N/A | N/A | N/A | EXISTS | MISSING |
| Context caching awareness | EXISTS | N/A | EXISTS | EXISTS | MISSING |

**What exists:** `TokenWindowManager` tracks per-agent budgets, estimates tokens.
**What's missing:** `compact_context()` is a stub. No summarization. No repo map.
No context caching awareness.

**Priority: HIGH** — Context is the #1 bottleneck from all issue research.
1. Implement `compact_context()` with summarization via weak model
2. Add tree-sitter-based repo map for selective context loading
3. Track context cache boundaries for Anthropic/OpenAI caching APIs

---

## 6. Session Management

| Feature | Goose | OpenCode | Codex | hottentot |
|---------|-------|----------|-------|-----------|
| Session persistence | EXISTS | EXISTS | EXISTS | MISSING |
| Session resume | EXISTS | EXISTS | EXISTS | MISSING |
| Session fork | EXISTS | EXISTS | N/A | MISSING |
| Session search | EXISTS | EXISTS | N/A | MISSING |
| Session undo/redo | N/A | EXISTS | N/A | MISSING |
| Multi-session parallel | EXISTS | EXISTS | EXISTS | MISSING |

**Priority: MEDIUM** — Sessions are important for long-running work but hottentot-agent's
todo-driven model means the "session" is effectively the todo list state in PostgreSQL.
The main gap is agent conversation history persistence.

---

## 7. Planning

| Feature | Plandex | MetaGPT | Goose | hottentot |
|---------|---------|---------|-------|-----------|
| Plan-then-execute | EXISTS | EXISTS | EXISTS | MISSING |
| Plan diffing/review | EXISTS | N/A | N/A | MISSING |
| Plan branching | EXISTS | N/A | N/A | MISSING |
| Task decomposition | N/A | EXISTS | EXISTS | PARTIAL |
| Goal constraints | N/A | N/A | N/A | MISSING |

**What exists:** Plan agent in config, dogfood sprint parser decomposes objectives into tasks.
**What's missing:** No plan-then-execute phase in event loop. No plan diffing.

**Priority: MEDIUM** — The event loop already sequences work; add an explicit planning phase
that decomposes complex todos into child todos before execution.

---

## 8. Memory

| Feature | AutoGPT | LangGraph | Codex | hottentot |
|---------|---------|-----------|-------|-----------|
| Short-term (working) memory | EXISTS | EXISTS | EXISTS | PARTIAL |
| Long-term (cross-session) memory | EXISTS | EXISTS | EXISTS | MISSING |
| Vector/embedding search | EXISTS | N/A | N/A | MISSING |
| Repo-local state files | N/A | N/A | EXISTS | MISSING |

**What exists:** Variable namespacing in DB, todo state machine, audit events.
**What's missing:** No conversation memory. No vector search. No cross-session knowledge.

**Priority: LOW** — The todo list IS the memory. Variable namespaces provide config memory.
Vector search is overkill for a todo-driven agent.

---

## 9. Self-Improvement / Dogfood

| Feature | hottentot | Industry |
|---------|-----------|----------|
| Self-improvement workflow | EXISTS | UNIQUE |
| Validation gate before apply | EXISTS | UNIQUE |
| Reload with rollback | EXISTS | RARE |
| Gap analysis | EXISTS | UNIQUE |
| Sprint parser | EXISTS | UNIQUE |
| Smoke task execution | EXISTS | UNIQUE |
| Bypass detection | EXISTS | UNIQUE |

**This is hottentot-agent's unique differentiator.** No other tool has this level of
self-improvement infrastructure. Keep investing here.

---

## 10. Security / Governance

| Feature | Codex | Goose | MetaGPT | hottentot |
|---------|-------|-------|---------|-----------|
| Tool-level permissions | EXISTS | EXISTS | N/A | EXISTS |
| Sandbox execution | EXISTS | EXISTS | N/A | MISSING |
| Cryptographic role delegation | N/A | N/A | EXISTS | MISSING |
| MCP proxy for audit | N/A | PARTIAL | N/A | MISSING |
| Force-push rejection | N/A | N/A | N/A | EXISTS |
| Secret redaction | N/A | N/A | N/A | EXISTS |
| Action policy (deny lists) | N/A | N/A | N/A | EXISTS |
| Budget/spend caps | N/A | N/A | N/A | EXISTS |

**Priority: LOW** — Permission system is solid. Sandbox and crypto-delegation are overkill
for a CLI tool. MCP audit proxy can be added when MCP client is built.

---

## 11. Observability

| Feature | Codex | OpenHands | hottentot |
|---------|-------|-----------|-----------|
| Structured logging | EXISTS | EXISTS | PARTIAL |
| Audit events | N/A | EXISTS | EXISTS |
| OpenTelemetry tracing | EXISTS | N/A | MISSING |
| Evidence checker | N/A | N/A | EXISTS |
| Log auditor | N/A | N/A | EXISTS |
| Cost tracking | N/A | EXISTS | EXISTS |

**Priority: LOW** — Audit events + evidence checker + cost tracking is sufficient.
OTEL can be added later if needed.

---

## 12. Unique Features Found in Research

These features from other tools have no equivalent in any surveyed tool:

| Feature | Source | Relevance to hottentot |
|---------|--------|----------------------|
| Shared intermediate artifacts (shared_deps.md) | Smol Developer | HIGH — force LLM to commit to cross-file contracts before generating |
| Function dependency graph with triggers | BabyAGI | MEDIUM — auto-discover tool dependencies |
| Agents-as-tools recursive composition | AutoGen | HIGH — sub-agents as callable tools |
| Durable checkpointing with resume | LangGraph | MEDIUM — survive crashes during long tasks |
| Plan versioning with branching | Plandex | HIGH — explore multiple solution approaches |
| Budget/spend/time caps on autonomous runs | AutoGPT | HIGH — prevent runaway costs |
| AFlow automated workflow discovery | MetaGPT | LOW — research-grade, not production ready |
| SPO self-play prompt optimization | MetaGPT | LOW — research-grade |
| Sandbox-as-a-service (E2B) | E2B | N/A — hottentot runs locally or in containers |
| Pattern library (100+ crowdsourced) | Fabric | MEDIUM — reusable prompt templates |
| Per-pattern model routing | Fabric | MEDIUM — route different tasks to optimal models |
| Workflow DAG builder | AutoGPT | N/A — hottentot is todo-driven, not DAG-driven |
| Agent marketplace | AutoGPT | LOW — premature for current stage |
| EU AI Act compliance layer | AutoGPT | LOW — not needed for dev tool |
