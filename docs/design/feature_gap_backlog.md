# Feature-Gap Backlog — gludd vs. State-of-the-Art Autonomous Coding Platforms

> **Issue:** #38 — identify agentic-product capabilities gludd is missing vs. the current
> state of the art, and produce a prioritized implementation backlog.
>
> **Method:** read-only inventory of `src/general_ludd/`, `README.md`, `AGENTS.md`,
> `docs/ORCHESTRATION.md`, `pyproject.toml`, and the daemon import map
> (`src/general_ludd/daemon.py:19-61`, the authoritative wiring list). Every "already
> exists" claim below cites a file. Nothing here is implemented — this is the next wave's
> backlog.
>
> **Status:** DESIGN / BACKLOG ONLY. Do not treat any row as done.

---

## 0. What gludd already has (grounded inventory)

This is the baseline. The point of the inventory is to *not* re-propose things that exist.
Cited from the daemon wiring map and module reads:

| Capability | Where it lives | Notes |
|---|---|---|
| Autonomous SDLC event loop (claim→dispatch→review→reconcile) | `event_loop/loop.py` (`EventLoop`), wired `daemon.py:35` | The core agent loop. |
| Ansible-as-tool-call execution layer | `ansible/runner.py` (`AnsibleRunnerAdapter`), `daemon.py:20` | ~34 roles + ~12 modules (README "Roles"/"Modules"). |
| Multi-model gateway + providers | `models/gateway.py` (`ModelGateway`), `daemon.py:44` | Z.AI/OpenAI/Anthropic/OpenRouter/vLLM/llama.cpp (README). |
| Adaptive / role-based model routing | `scoring/router.py` (`AdaptiveRouter`), `daemon.py:54` | Role/quality/latency routing + fallback chain (README). |
| Cost / budget guard | `controllers/budget.py` (`RunBudgetGuard`), `daemon.py:26` | Per-run budget ceiling + warn %. |
| Benchmark scoring + prompt×model leaderboard | `db/repository.py` (`BenchmarkRepository`) `daemon.py:27`; `observability/recorder.py` (`AutoBenchmarkRecorder`) `daemon.py:48`; CLI `scores`/`leaderboard` (`cli.py:1519-1571`) | Composite score keyed by `prompt_profile_id`+`model_profile_id`+`task_type`. |
| Observability of own runs (metrics/traces) | `metrics/collector.py` (`MetricsCollector`) `daemon.py:43`; `observability/dashboard_data.py` `daemon.py:46`; OTel `observability/otel_bridge.py` (`OTelBridge`) `daemon.py:47`; `/api/metrics`,`/api/traces` (README) | Per-phase trace aggregates, Prometheus export. |
| Human-in-the-loop approval (file integrity) | `integrity/scanner.py` (`FileIntegrityScanner`) `cli.py:22`; CLI `integrity approve/reject/log` (`cli.py:496-509`) | Approve/reject with signer + reason + audit log. |
| Inter-agent message queue | `gludd_message` module + `/api/messages` (README); prompt blurb `prompts/registry.py:97` | Send/receive/ack between dispatched agents. |
| Prompt template registry + hot reload | `prompts/registry.py` (`PromptRegistry`) `daemon.py:51` | jinja2, work-type→template map, `refresh()` rediscovery. |
| Skills + MCP tool catalogs | `skills/registry.py`,`skills/loader.py` `daemon.py:60-61`; `mcp/loader.py` `daemon.py:42`; `gludd_mcp_tool` module | Search/list/install skills; MCP catalog. |
| Code intelligence (call-graph + search) | CLI `code graph`/`code search` (`cli.py:579-591`); tree-sitter dep (`pyproject.toml:32-33`) | tree-sitter AST graph + textual search. |
| Process isolation for Ansible runs | `ansible/isolation.py` (`ProcessIsolationConfig`) `daemon.py:19`; `config/ansible/isolation.yml` (README) | Process-level isolation config. |
| Self-improvement harness | `self_improve/harness.py` (`SelfImprovementHarness`) | Static gap finder (missing tests / dead code / coverage). |
| Event bus + hook system | `events/bus.py` (`EventBus`), `events/hooks.py` (`HookSystem`) `daemon.py:36-37`; CLI `hooks` (`cli.py:511-525`) | Pub/sub + registerable event hooks. |
| Secrets (OpenBao) incl. per-project scoping | `secrets/manager.py`,`secrets/project_secrets.py` `daemon.py:57-59` | External/auto/disabled modes; per-project paths. |
| Multi-project workspaces + weighted scheduling | `projects/workspace.py` (`ProjectWorkspace`) `daemon.py:50`; CLI `project add --weight/--dispatch-mode` (`cli.py:335-345`) | active / passive_external / worktree_monitor modes. |
| Worktree monitor (parallel agents) | CLI `worktree scan/status` (`cli.py:318-329`); `docs/ORCHESTRATION.md` | Detects abandoned worktrees w/ AGENTS.md. |
| Compute/GPU orchestration + local inference | CLI `compute launch`,`local-serve` (`cli.py:308-424`) | Spot GPU launch, vLLM/llama.cpp serving. |
| Evidence-based claim auditing | `review/evidence_checker.py` (`EvidenceChecker`) | Regex auditor for unsupported claims in responses. |

The base is broad. The genuine gaps below are the capabilities **not** in that table, or
present only in a shallow form that the state of the art has moved well past.

---

## 1. Prioritized gap backlog

Priorities: **HIGH** = blocks autonomous quality/reliability or is table-stakes for modern
agent platforms; **MED** = meaningful capability uplift; **LOW** = nice-to-have / research.
Effort is rough engineering size (S ≤ 2d, M ≈ 3–5d, L ≈ 1–2wk, XL > 2wk), assuming the
existing role/controller/event-bus scaffolding.

### HIGH priority

#### G1. Persistent agent memory / context store
- **What it is:** A durable, queryable memory layer so agents recall prior task outcomes,
  decisions, file-area knowledge, and recurring failure patterns across runs — the
  long-term-memory layer every modern agent platform now ships.
- **Partial today?** No dedicated memory subsystem. The closest is the message queue
  (`gludd_message`, ephemeral inbox) and `SESSION.md` (a single flat file the *harness*
  agent maintains, not the daemon). Benchmark history (`BenchmarkRepository`,
  `daemon.py:27`) stores *scores*, not task knowledge. There is **no** `memory/` package.
- **Gap:** Agents start each dispatch context-blind beyond the todo text + facts snapshot.
  No "we tried X on this file last week and it broke" recall; no decision log; no
  cross-run dedup of work.
- **Implementation:** New `general_ludd/memory/` package: `MemoryStore` (SQLite-backed,
  reusing `db/session.py`) with typed records (task-outcome, decision, file-note,
  failure-signature) + an `agent_memory` Alembic migration. New `gludd_memory` Ansible
  module (write/recall, mirroring `gludd_message`) so playbooks persist and retrieve. Inject
  a "relevant memories" block into dispatch prompts via `prompts/registry.py`. Recall keyed
  by project_id + file-path + work_type.
- **Priority / effort:** HIGH / L.

#### G2. Evaluation / regression harness (offline eval suite, not live benchmark)
- **What it is:** A curated, versioned suite of task fixtures with graded expected outcomes,
  run on demand/CI to detect quality regressions when prompts, routing, or model versions
  change — the agent-platform analogue of a test suite for the *agent itself*.
- **Partial today?** `AutoBenchmarkRecorder` (`observability/recorder.py`, `daemon.py:48`)
  + `BenchmarkRepository` score *live* production runs by `prompt×model×task_type`. That is
  online telemetry, not a controlled offline eval. There is no fixed task corpus, no
  pass/fail gating, no "did this change make the agent worse" comparison.
- **Gap:** Cannot answer "is the agent better or worse after this prompt/routing change?"
  before shipping. Live scores are confounded by task mix and have no ground truth.
- **Implementation:** New `general_ludd/eval/` package: `EvalSuite` loading fixtures
  (`config/eval/*.yml`: prompt, repo state, graded assertion) + an `eval_runner` controller
  that dispatches each fixture through the real pipeline and scores via existing
  `BenchmarkRepository` scorers plus deterministic assertions. New `make eval` target and an
  `eval_regression` role. Store baselines; fail if composite score drops > threshold. Wire a
  daemon endpoint `/admin/eval/run` + CLI `gludd eval run`.
- **Priority / effort:** HIGH / L.

#### G3. Retrieval / codebase semantic indexing (RAG context)
- **What it is:** An embedding/retrieval index over the target repo so the agent retrieves
  the *most relevant* code/docs into context for a task, instead of relying on the todo text
  and whatever the playbook hard-codes.
- **Partial today?** `codeintel` gives a tree-sitter **call graph + textual search**
  (CLI `code graph`/`code search`, `cli.py:579-591`; tree-sitter `pyproject.toml:32-33`).
  That is structural/lexical, not semantic retrieval, and it is exposed as a CLI query —
  it is **not** wired into dispatch context assembly. `diskcache` (`pyproject.toml:39`) and
  `langchain` (`pyproject.toml:29`) are present but no retriever uses them.
- **Gap:** No embedding index, no relevance ranking, no automatic context packing into the
  dispatched prompt. Large repos overflow context with no principled selection.
- **Implementation:** New `general_ludd/retrieval/` package: `CodeIndexer` (chunk via the
  existing tree-sitter parser, embed via the `ModelGateway` `daemon.py:44` embeddings path
  or a local model, persist in SQLite/`diskcache`) + `ContextRetriever.retrieve(task)`.
  Wire into the dispatch step so the event loop packs top-k chunks into the prompt
  (budget-aware via `RunBudgetGuard`). New `gludd_retrieve` module + `index_codebase` role.
- **Priority / effort:** HIGH / XL.

#### G4. Sandboxed code execution (true isolation for agent-run commands/tests)
- **What it is:** Run agent-generated code/tests inside a real sandbox (container, gVisor,
  ephemeral VM, or seccomp/namespace jail) so untrusted generated commands cannot touch the
  host, secrets, or network beyond an allowlist.
- **Partial today?** `ansible/isolation.py` (`ProcessIsolationConfig`, `daemon.py:19`) gives
  *process-level* isolation config for Ansible runs, and OpenBao scopes secrets
  (`secrets/`, `daemon.py:57-59`). But generated test/command execution is not run in a
  hardened sandbox boundary; there is no container/VM execution boundary for the produced
  code itself.
- **Gap:** A malicious or hallucinated command in a generated playbook/test runs with the
  daemon's privileges. No network egress control, no filesystem jail, no resource caps on
  the executed change.
- **Implementation:** New `general_ludd/sandbox/` package: `SandboxRunner` abstraction with
  backends (podman/docker container — runtime already detected in `cli.py:_gather_offline_status`;
  fallback bubblewrap/firejail). Route the "run tests / run command" phase of code roles
  through it with egress allowlist + CPU/mem/time caps. New `sandbox_exec` role +
  `config/sandbox/policy.yml`. Surface violations to the message queue + metrics.
- **Priority / effort:** HIGH / L.

#### G5. Outcome-driven self-improvement loop (close the eval→prompt feedback loop)
- **What it is:** Use measured outcomes (eval failures, low benchmark scores, repeated
  reconcile rejections) to *automatically* propose prompt/routing changes and validate them
  via the eval harness — a learning loop, not a static linter.
- **Partial today?** `self_improve/harness.py` (`SelfImprovementHarness`) exists but only
  does **static** checks: missing test files, dead classes, coverage < 85%
  (`harness.py:14-99`). It generates "add tests" / "wire dead class" todos. It does **not**
  consume runtime outcomes (benchmark scores, reconcile decisions) or touch prompts/routing.
- **Gap:** No loop from "the agent performed badly on task type T" → "adjust prompt/route
  for T" → "re-measure". Self-improvement is disconnected from the rich telemetry gludd
  already collects.
- **Implementation:** Extend `self_improve/` with an `OutcomeAnalyzer` that reads
  `BenchmarkRepository` + reconcile-decision history, identifies weak `task_type×prompt×model`
  cells, and proposes candidate prompt variants / route changes as todos gated behind the
  G2 eval harness (no change ships unless eval improves). New `improvement_cycle` role; emit
  proposals to the message queue for human review (ties into G7).
- **Priority / effort:** HIGH / M (depends on G2).

### MED priority

#### G6. Prompt/skill versioning + A/B with rollback
- **What it is:** Version every prompt template and skill (content hash + semantic version),
  run controlled A/B between versions, promote/rollback based on eval+benchmark deltas.
- **Partial today?** `PromptRegistry` (`prompts/registry.py`) loads/renders/hot-reloads
  templates but stores **no version, hash, or history** — `refresh()` just overwrites in
  place (`registry.py:50-71`). The leaderboard *does* key on `prompt_profile_id`
  (`cli.py:1560`), so A/B comparison data exists, but there is no mechanism to register a
  *second version* of the same template, route a fraction of traffic to it, or roll back.
- **Gap:** No prompt history, no atomic promote/rollback, no traffic-split experiment
  controller. A bad prompt edit is silently live with no diff/rollback.
- **Implementation:** Add versioning to `PromptRegistry` (content hash + `prompt_versions`
  table/migration). New `experiments/` controller doing weighted version routing at dispatch,
  recording results against the existing leaderboard, and auto-promoting the winner once the
  eval harness (G2) confirms no regression. CLI `gludd prompts versions/promote/rollback`.
- **Priority / effort:** MED / M.

#### G7. Generalized human-in-the-loop approval gates (beyond file integrity)
- **What it is:** A first-class approval workflow that can gate *any* high-risk agent action
  (merge to protected branch, dependency major-bump, secret access, budget-exceeding run)
  with approve/reject/audit — not just file-integrity changes.
- **Partial today?** A solid HITL flow exists but is **scoped to file integrity only**:
  `integrity/scanner.py` + CLI `integrity approve/reject/log` (`cli.py:496-509`) with signer,
  reason, and audit log. The reconcile step (README event loop) is autonomous. There is no
  generic "pause and require human approval before action X" primitive.
- **Gap:** Cannot require approval for merges, releases (`release_build` role), or
  budget-breaching dispatches. The approval primitive is hard-wired to integrity changes.
- **Implementation:** Generalize the integrity approval into an `approvals/` package:
  `ApprovalGate(action, risk, payload)` that parks an action as `pending_approval`, emits to
  the message queue + a daemon endpoint, and only proceeds on signed approval. Wire risk
  hooks at: reconcile-merge, `release_build`, major dependency bump, budget-warn breach.
  Config `config/approvals/policy.yml`; CLI `gludd approvals list/approve/reject`.
- **Priority / effort:** MED / M.

#### G8. Cost/quality-aware routing optimizer (Pareto routing, not just fallback)
- **What it is:** Route each task to the model on the cost/quality Pareto frontier for that
  *task type*, learned from benchmark history — escalate hard tasks to strong models, send
  easy tasks to cheap/local models, respecting the budget.
- **Partial today?** `AdaptiveRouter` (`scoring/router.py`, `daemon.py:54`) + role routing +
  fallback chain + `RunBudgetGuard` (`daemon.py:26`) exist. Routing is by role/quality/latency
  with a static fallback chain (README). The benchmark leaderboard has the per-model cost +
  score data (`cli.py:1554-1566`) needed for optimization, but routing does **not** consume
  it to pick the cost-optimal model per task type.
- **Gap:** No closed loop from "model M is the cheapest one that passes task type T at score
  ≥ s" into the routing decision. Budget is a hard ceiling, not an optimization objective.
- **Implementation:** Extend `AdaptiveRouter` with a `CostQualityOptimizer` that reads
  `BenchmarkRepository`, builds a per-`task_type` Pareto frontier, and selects the
  min-cost model meeting a configurable quality floor, with confidence-based escalation on
  reconcile-reject. Config knobs in `model_routing.yml` (quality_floor, max_cost_per_task).
- **Priority / effort:** MED / M.

#### G9. Plan/critique/decomposition layer (explicit planner before dispatch)
- **What it is:** Decompose a large todo into a verified subtask DAG with a planner model,
  critique the plan, then dispatch subtasks — the standard "planner→executor→critic" pattern.
- **Partial today?** A `planner` role is referenced in routing (`model_routing.yml`
  `role_routing.planner`, README), and `langgraph` is a dependency (`pyproject.toml:35`), but
  there is no planner controller that decomposes a todo into a tracked subtask graph; the
  event loop dispatches todos largely 1:1. Multi-step is expressed as separate todos by hand.
- **Gap:** No automatic decomposition, no plan critique, no dependency-ordered subtask
  execution with rollup. Big tasks rely on the model doing it all in one dispatch.
- **Implementation:** New `planning/` controller: `Planner.decompose(todo) -> SubtaskGraph`
  (planner model via gateway), a `critic` pass, and event-loop support for parent/child todo
  linkage (new `parent_todo_id` column + migration) with completion rollup. Use `langgraph`
  for the graph execution. New `plan_task` role.
- **Priority / effort:** MED / L.

#### G10. Per-run replay / deterministic trace bundle
- **What it is:** Capture a complete, replayable bundle per run (prompt, model, params,
  retrieved context, tool calls, diffs, decisions) so any run can be inspected or replayed —
  essential for debugging autonomous failures and for audits.
- **Partial today?** Traces exist (`/api/traces`, `observability/`, `daemon.py:46-48`) with
  per-phase aggregates, and OTel export is wired (`OTelBridge`). But traces are *aggregate
  telemetry*, not a self-contained replay bundle pinning exact inputs/outputs of one run.
- **Gap:** Cannot reconstruct "exactly what prompt+context produced this bad diff" or replay
  a run against a new prompt/model. No per-run artifact bundle.
- **Implementation:** New `replay/` module: a `RunRecorder` that, per dispatch, writes a
  bundle (filestore-backed via `filestore/store.py`) capturing prompt, rendered template +
  version (ties to G6), model+params, retrieved context (G3), tool I/O, diff, reconcile
  decision. `gludd replay show <run_id>` + `gludd replay rerun <run_id>`.
- **Priority / effort:** MED / M.

### LOW priority

#### G11. Multi-agent debate / consensus for review
- **What it is:** For high-risk changes, run N independent reviewer models and require
  consensus (or a tie-break judge) rather than a single reviewer.
- **Partial today?** The pipeline reviews with *one* (possibly different) model (README event
  loop). The infrastructure to call multiple models (`ModelGateway`) and aggregate
  (message queue) exists, but no consensus/debate controller does.
- **Gap:** Single-reviewer blind spots on critical changes.
- **Implementation:** `review/consensus.py` controller that fans a review to K models via
  the gateway, aggregates verdicts, and escalates ties to a judge model or the G7 approval
  gate. Gate behind a risk threshold so cost stays bounded.
- **Priority / effort:** LOW / M.

#### G12. Live web/docs retrieval tool for agents
- **What it is:** Let agents fetch current library docs / web results during a task (e.g.
  for a dependency upgrade) instead of relying on training-cutoff knowledge.
- **Partial today?** MCP tool support (`gludd_mcp_tool`, `mcp/loader.py`) could host such a
  tool, and `requests` is a dep, but no first-party web/docs retrieval tool or role ships.
- **Gap:** `dependency_update`/`audit_dependencies` roles cannot consult current upstream
  changelogs/advisories at run time.
- **Implementation:** A `web_retrieve` MCP tool/role with domain allowlist + caching
  (`diskcache`), surfaced to dependency and security roles. Respect the G4 sandbox egress
  policy.
- **Priority / effort:** LOW / M.

#### G13. Agent-facing structured task spec / acceptance-criteria schema
- **What it is:** A structured todo schema with explicit acceptance criteria the reconcile
  step checks against, instead of free-text titles/descriptions.
- **Partial today?** Todos carry `title`, `description`, `work_type`, `priority`, `queue`,
  `project_id` (`cli.py:229-237`). Reconcile uses test/lint/review signals, not declared
  acceptance criteria. `EvidenceChecker` checks claims but not task acceptance.
- **Gap:** No machine-checkable "definition of done" per task; reconcile can't verify the
  task's *intent* was met, only that the gate passed.
- **Implementation:** Add an optional `acceptance_criteria` field (list of checkable
  assertions) to the todo schema + migration; have reconcile evaluate them (reusing eval-style
  assertions from G2) before approving. CLI `gludd add --criteria`.
- **Priority / effort:** LOW / S.

---

## 2. Suggested sequencing

1. **Wave 1 (foundations):** G2 (eval harness) and G1 (memory) first — both are prerequisites
   that unlock measurement and recall for everything else. G4 (sandbox) in parallel (security
   table-stakes, independent files → worktree-parallel per `docs/ORCHESTRATION.md`).
2. **Wave 2 (loops):** G3 (retrieval) and G5 (outcome-driven self-improve, needs G2), then
   G6 (prompt versioning, needs the eval signal) and G8 (cost/quality routing).
3. **Wave 3 (control & audit):** G7 (general HITL), G9 (planner), G10 (replay).
4. **Wave 4 (polish):** G11–G13.

## 3. Notes on grounding & honesty

- Every "partial today" row cites a real file/line or a README-documented capability.
- The largest genuine gaps (no `memory/`, no offline `eval/`, no semantic `retrieval/`, no
  hardened `sandbox/`) are **absences** in the daemon import map (`daemon.py:19-61`) — none of
  those packages are wired, confirming they are real gaps rather than undiscovered code.
- This analysis was read-only (Read tool only); directory enumeration was via the daemon
  import map and CLI rather than a filesystem walk, so a module could exist under a different
  name than guessed. Before implementing any row, confirm the absence with
  `make` collection/grep targets — but the daemon wiring map is strong evidence of what is
  actually instantiated at startup.
</content>
</invoke>
