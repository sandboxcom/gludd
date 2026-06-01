# Features to Decide

Features discovered in competitive research that need user decision before implementation.
Each entry has: what it is, who has it, what it would look like in general-ludd-agent,
and arguments for/against.

---

## 1. Durable Checkpointing (LangGraph Pattern)

**What:** Agent state serialized at every execution step. Survives process crashes.
Can resume from any historical checkpoint. Time-travel debugging by rewinding state.

**Who has it:** LangGraph (primary), Codex (session persistence), Plandex (plan versioning).

**In general-ludd-agent:** Would mean snapshotting the entire event loop state
(todos, leases, decisions, controller outputs) after every tick. Resume would
restore from last snapshot. Branching would let you explore different strategies.

**For:** Long-running autonomous sessions could survive worker restarts. Debugging
failed ticks by replaying from checkpoint. Exploring alternative strategies via branches.

**Against:** general-ludd-agent's PostgreSQL persistence already provides durable state
for todos and task returns. The event loop is stateless between ticks (reads fresh
from DB). Checkpointing adds complexity with no clear win since the DB IS the checkpoint.

**Recommendation:** DEFER. The PostgreSQL database already serves as the durable
checkpoint. The event loop is designed to be stateless between ticks. If we need
tick-level replay, add an `EventLoopTick` audit table instead of full state snapshots.

---

## 2. Agents-as-Tools Recursive Composition (AutoGen Pattern)

**What:** Any agent can be wrapped as a callable tool for another agent. Enables
recursive composition: a build agent can invoke an explore agent as a tool call.

**Who has it:** AutoGen (primary), LangGraph (subgraphs), Zed (`spawn_agent`).

**In general-ludd-agent:** The `AgentDispatcher` already dispatches subagents, but
they run as separate LLM calls with their own context. "Agents-as-tools" would
mean the parent agent sees the subagent as a tool in its tool list, calls it
with a natural language description, and gets structured output back.

**For:** Cleaner agent composition. Parent agent decides when to delegate.
Subagent output is structured (not just appended to conversation). Matches how
Zed/Codex/OpenCode implement subagents.

**Against:** general-ludd-agent's tool boundary is Ansible playbooks, not LLM function
calls. Adding LLM-to-LLM tool calls would be a different execution model. Current
dispatcher pattern works for the event loop's phase-based architecture.

**Recommendation:** IMPLEMENT when MCP client is ready. MCP tools ARE the agent-as-tool
pattern — an MCP server wrapping a subagent is cleaner than direct LLM-to-LLM calls.

---

## 3. Graph-as-Agent State Machine (LangGraph Pattern)

**What:** Agents ARE directed graphs. Nodes are functions, edges are conditional
transitions. Execution follows the graph topology. Supports cycles, branching,
parallel paths.

**Who has it:** LangGraph (primary), AutoGPT (block-based DAGs), CrewAI (Flows).

**In general-ludd-agent:** Would replace the 10-phase tick pipeline with a configurable
graph. Each phase becomes a node. Edges define conditions (e.g., "if load > 80%,
skip dispatch and go to evaluate_pid"). Users could define custom graphs.

**For:** Much more flexible than the fixed 10-phase pipeline. Users could define
their own agent workflows. Conditional edges handle edge cases. Visual DAG representation
for complex workflows.

**Against:** The fixed pipeline is simple and debuggable. general-ludd-agent is a
todo-driven scheduler, not a general-purpose agent framework. Graph complexity
makes it harder to reason about behavior. No evidence from issues that other tools'
users are building custom graphs — they use the default pipeline.

**Recommendation:** DEFER. The phase pipeline is a good abstraction. If custom
phases are needed, make phases pluggable (register new phases via config) instead
of introducing a full graph engine.

---

## 4. Sandbox Execution (Codex/E2B Pattern)

**What:** Code and commands run in isolated environments (containers, microVMs,
cloud VMs). Prevents agent from damaging the host system.

**Who has it:** Codex (OS-native sandbox), E2B (cloud VMs), OpenHands (Docker),
SWE-agent (Docker).

**In general-ludd-agent:** Ansible Runner already provides some isolation (runs in
its own environment directory). Could add Docker-based playbook execution or
use the existing `RuntimeProfile.container` mode.

**For:** Safety net for autonomous execution. Prevents accidental host damage.
Required for multi-tenant deployment.

**Against:** general-ludd-agent runs locally (single user). Ansible Runner already
isolates execution in private data directories. Container mode exists for
production deployment. Adding Docker-in-Docker for sandboxing is complex.

**Recommendation:** DEFER for local development. The container `RuntimeProfile`
handles production isolation. If we add a hosted mode later, revisit.

---

## 5. Tree-sitter Repo Map (Aider/Plandex Pattern)

**What:** Parse the entire codebase with tree-sitter to build a symbol graph.
Use PageRank or similar to rank symbols by importance. Include only the most
relevant symbols in the LLM context window.

**Who has it:** Aider (primary, with PageRank), Plandex (30+ languages), CodeStory
(ported Aider's approach).

**In general-ludd-agent:** Would add a `RepoMap` class that indexes the project with
tree-sitter, builds a symbol graph, and provides the top-K most relevant symbols
for a given query. The event loop would inject repo map context into model calls.

**For:** Massive context window savings. Instead of reading entire files, the agent
gets a compact symbol summary. Proven to work at scale (Aider handles 100K+ line repos).
Directly addresses the #1 pain point from all issue research: context window efficiency.

**Against:** Requires tree-sitter grammars for all target languages. Adds a dependency.
 general-ludd-agent's playbook-driven model means the LLM doesn't directly navigate code —
it decides what playbooks to run. Context injection is less critical when tools
(playbooks) are the execution boundary.

**Recommendation:** IMPLEMENT when context compaction is needed. Start with Python-only
tree-sitter support. The repo map would be injected into return-review and
self-improvement prompts where the LLM needs to understand codebase structure.

---

## 6. Session/Conversation Persistence

**What:** Store the full conversation history between agent and LLM. Resume
conversations across restarts. Search past conversations.

**Who has it:** Goose (SQLite), OpenCode (multi-backend), Codex (history.jsonl),
AutoGPT (workspace).

**In general-ludd-agent:** Would add a `ConversationModel` to the DB with message
history per todo or per agent session. The event loop would load conversation
context when processing a todo.

**For:** Long-running tasks need conversation continuity. Currently, each playbook
invocation starts fresh — the LLM has no memory of what it tried before. Enables
"resume where I left off" after worker restart.

**Against:** general-ludd-agent's playbook-driven model means the LLM is invoked for
return reviews and self-improvement, not continuous conversation. Conversation
history is less valuable when the primary interface is todos, not chat.

**Recommendation:** IMPLEMENT for return review conversations. Add a
`ConversationModel` that stores the review prompt + LLM response per task return.
This gives the reviewer context from previous review attempts on the same todo.
Full chat-style sessions are not needed.

---

## 7. Plan Versioning and Branching (Plandex Pattern)

**What:** Every plan is versioned. Branch to explore multiple approaches.
Compare branches. Merge the winner.

**Who has it:** Plandex (primary).

**In general-ludd-agent:** Would add plan versioning to the todo system. A "plan" is
a set of child todos. Branching creates alternative child todo sets. Comparison
shows diffs between plans. Merging applies the winning plan.

**For:** Enables exploring multiple implementation strategies. The event loop
could evaluate competing plans and pick the best. Natural fit for the
self-improvement workflow (plan A vs plan B).

**Against:** general-ludd-agent's todo state machine is already versioned (optimistic
locking). Branching child todos adds significant complexity. No other tool has
adopted this pattern (Plandex is niche at 15K stars).

**Recommendation:** DEFER. The existing todo versioning + git branching provides
sufficient "plan exploration." If needed, implement as competing child todo sets
with a selection phase in the event loop.

---

## 8. Budget/Spend/Time Caps on Autonomous Runs (AutoGPT Pattern)

**What:** Hard limits on cost, token usage, and wall-clock time for autonomous
agent runs. Agent stops when caps are hit. Prevents runaway costs.

**Who has it:** AutoGPT (primary), general-ludd-agent (PARTIAL — BudgetController exists).

**In general-ludd-agent:** `BudgetController` already has cost checking per call.
Would add: total run budget cap, wall-clock timeout, per-todo budget allocation,
budget exhaustion as a todo status (BUDGET_EXCEEDED).

**For:** Critical for autonomous operation. Prevents infinite loops from consuming
API credits. Industry-standard practice (every major tool has this).

**Against:** Already partially implemented. The remaining work is small.

**Recommendation:** IMPLEMENT. Add run-level budget caps and wall-clock timeouts.
This is a small extension of existing `BudgetController`.

---

## 9. YAML-Driven Agent/Task Definitions (CrewAI Pattern)

**What:** Agents and tasks defined in YAML files separate from code. Roles,
goals, tools, and dependencies all declarative.

**Who has it:** CrewAI (primary), Goose (recipes), general-ludd-agent (PARTIAL).

**In general-ludd-agent:** Agent configs already in `config/agents/default_agents.yml`.
Would extend to: task definitions (what to do, tools, dependencies, validation),
workflow definitions (task ordering, parallelism), and prompt templates per task.

**For:** Already partially there. YAML-driven configuration is a general-ludd-agent
core principle. Extending to task definitions would make the system fully configurable
without code changes.

**Against:** Risk of YAML proliferation. Complex workflows in YAML can be harder
to debug than code.

**Recommendation:** IMPLEMENT incrementally. Extend existing YAML config with
task definitions when the planning engine is built.

---

## 10. Shared Intermediate Artifacts (Smol Developer Pattern)

**What:** Before generating code, the LLM produces a "shared_dependencies.md" that
defines cross-file contracts (function signatures, data structures, API boundaries).
Code generation then references this artifact for coherence.

**Who has it:** Smol Developer (primary).

**In general-ludd-agent:** Would mean: before dispatching a build job, the planning
agent generates a shared artifact describing the intended changes (files to modify,
new functions, API contracts). Build agents reference this artifact for coherence.

**For:** Forces LLM to commit to a plan before executing. Reduces incoherent
multi-file changes. Lightweight to implement (just a markdown file in the todo's
worktree). Proven effective in Smol Developer.

**Against:** Adds a planning step before every build. May slow down simple tasks.
 general-ludd-agent's playbook-driven model means changes are already scoped per playbook.

**Recommendation:** IMPLEMENT for complex todos. Add an optional `plan_artifact`
field to `TodoModel`. The plan agent generates it, build agents reference it.
Simple todos skip the planning step.

---

## 11. Function Dependency Graph with Triggers (BabyAGI Pattern)

**What:** Functions stored as nodes in a dependency graph. Triggers auto-execute
functions when dependencies change. Self-building agent that registers new functions.

**Who has it:** BabyAGI (primary).

**In general-ludd-agent:** Would mean: playbook results trigger follow-up actions
(e.g., test failure triggers gap analysis, dependency update triggers security scan).
The event loop already does some of this via phases, but triggers would be more dynamic.

**For:** Reactive automation. Events trigger appropriate responses without manual
phase ordering. Self-building capability (agent registers new playbooks).

**Against:** general-ludd-agent's phase-based event loop is simpler and more predictable.
Trigger systems can create unexpected cascading behavior. BabyAGI is a proof-of-concept,
not a production system.

**Recommendation:** DEFER. The phase pipeline already provides structured reactivity.
If needed, add a simple trigger table (event_type → playbook) instead of a full
dependency graph.

---

## 12. Per-Pattern Model Routing (Fabric Pattern)

**What:** Different prompt patterns route to different models. Complex analysis
routes to expensive model. Simple summarization routes to cheap model.

**Who has it:** Fabric (primary).

**In general-ludd-agent:** Would extend `ModelRouter` with pattern-based routing.
Return review routes to strong model. Commit message generation routes to weak model.
Gap analysis routes to cheap model.

**For:** Cost optimization. Already have the infrastructure (`ModelRouter`,
`ModelProfile`). Just needs config wiring.

**Against:** Very small implementation effort. Not a decision point — just do it.

**Recommendation:** IMPLEMENT. Extend `ModelRouter.add_role()` mapping to include
pattern/agent → model_profile bindings.

---

## Summary of Recommendations

| # | Feature | Decision | Priority |
|---|---------|----------|----------|
| 1 | Durable Checkpointing | DEFER | — |
| 2 | Agents-as-Tools | IMPLEMENT via MCP | HIGH |
| 3 | Graph-as-Agent | DEFER | — |
| 4 | Sandbox Execution | DEFER | — |
| 5 | Tree-sitter Repo Map | IMPLEMENT | MEDIUM |
| 6 | Session/Conversation Persistence | IMPLEMENT for reviews | MEDIUM |
| 7 | Plan Versioning/Branching | DEFER | — |
| 8 | Budget/Spend/Time Caps | IMPLEMENT | HIGH |
| 9 | YAML-Driven Task Definitions | IMPLEMENT incrementally | MEDIUM |
| 10 | Shared Intermediate Artifacts | IMPLEMENT for complex todos | MEDIUM |
| 11 | Function Dependency Graph | DEFER | — |
| 12 | Per-Pattern Model Routing | IMPLEMENT | HIGH |
