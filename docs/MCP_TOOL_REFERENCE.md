# MCP Tool Reference

**Generated:** 2026-07-28 20:19 UTC | **Version:** `v0.1.0-beta.2-185-g99266b56-dirty` | **Tools:** 39

Every `gludd_*` Ansible module in the `general_ludd.agent` collection is automatically surfaced as an MCP tool with a JSON-schema input contract. This reference is regenerated via `make gen-mcp-tool-ref` (which calls `gen-mcp-tools` then this generator).

---

## Tool Index

1. [`gludd_abtest`](#1-gludd-abtest) — Crash-isolated A/B test of a candidate code variant in a fresh subprocess — Runs a baseline (A = current C(src)) and a candidate (B = candidate worktree) under the SAME workload, each in a FRESH interpreter child process via C(general_ludd
2. [`gludd_accounting`](#2-gludd-accounting) — Fetch per-project accounting snapshots via the daemon — C(state=all) calls C(GET /api/accounting) and returns accounting snapshots for ALL known projects as C(ansible_facts
3. [`gludd_agent_run`](#3-gludd-agent-run) — Run the agent tool-call loop (prompt + tools → answer) — W6
4. [`gludd_break_glass`](#4-gludd-break-glass) — OpenBao raft snapshot and restore (break-glass backup) — Wraps the OpenBao HTTP API for the two break-glass endpoints C(/v1/sys/storage/raft/snapshot) (GET — snapshot) and C(/v1/sys/storage/raft/restore) (POST — restore)
5. [`gludd_db`](#5-gludd-db) — Todo/resource CRUD via daemon HTTP API (never raw SQLite) — Performs todo and resource operations against the daemon's REST API
6. [`gludd_dispatch`](#6-gludd-dispatch) — Interact with the daemon's dynamic-dispatch API — C(state=dispatch) POSTs a tool-call to C(POST /api/dispatch) with a C(kind)/C(name)/C(args) body and returns the dispatch result as C(ansible_facts
7. [`gludd_embed`](#7-gludd-embed) — Embedding similarity over the daemon's bert surface — With C(op=similar) (the default) queries the daemon's read-only C(POST /api/embeddings/similar) endpoint and returns the ranked similar canonical task types under C(ansible_facts
8. [`gludd_environment`](#8-gludd-environment) — Inject the consolidated environment + optimization brief as ansible_facts — Queries the daemon's read-only C(GET /api/environment) endpoint and returns the consolidated environment brief under C(ansible_facts
9. [`gludd_facts`](#9-gludd-facts) — Inject live daemon facts (work/todo/model/history/messages) as ansible_facts — Queries the daemon's read-only C(GET /api/facts) aggregation endpoint and returns the structured snapshot under C(ansible_facts
10. [`gludd_features`](#10-gludd-features) — Fetch and verify the feature database via the daemon — C(state=list) calls C(GET /api/features) and returns all features (optionally filtered by status/category) as C(ansible_facts
11. [`gludd_gate_check`](#11-gludd-gate-check) — Check whether a
12. [`gludd_git`](#12-gludd-git) — Git control-plane ops (commit/branch/worktree/merge/push) via git_automation — Exposes gludd's hardened C(general_ludd
13. [`gludd_human_todo`](#13-gludd-human-todo) — File or resolve a bot→human request (HumanTodo) via the daemon — Agents use this module to ask a human for something they cannot get on their own — a permission escalation, an external action, a decision, missing input, or another blocker
14. [`gludd_introspect`](#14-gludd-introspect) — Inject codebase self-knowledge facts (churn/complexity/coverage/debt) as ansible_facts — Queries the daemon's read-only C(GET /api/facts) endpoint and returns the C(codebase) self-introspection block under C(ansible_facts
15. [`gludd_langchain_generate`](#15-gludd-langchain-generate) — Generate text (optionally structured JSON) via the daemon — Sends a prompt to the daemon's POST /admin/models/call endpoint, which runs the generation through the LangChain-backed model gateway
16. [`gludd_langgraph_decision`](#16-gludd-langgraph-decision) — Ask the model to choose one option from a fixed set — Sends a decision prompt plus a list of allowed option tokens to the daemon's POST /admin/models/call endpoint and asks the model to reply with JSON of the form {"decision":
17. [`gludd_langgraph_workflow`](#17-gludd-langgraph-workflow) — Run a multi-step LangGraph generate/review workflow — Sends a message list to the daemon's POST /admin/models/workflow endpoint, which executes a LangGraph generate -> review -> retry loop server-side and returns the best content plus quality metadata
18. [`gludd_make`](#18-gludd-make) — Run a make target via the MakeRunner abstraction — Runs a C(make) target through the C(MakeRunner) subprocess wrapper with proper sanitized environment, bounded output capture, and per-target timeout
19. [`gludd_mcp_tool`](#19-gludd-mcp-tool) — Invoke an MCP tool (honest placeholder — not yet wired) — Per the W3
20. [`gludd_message`](#20-gludd-message) — Inter-agent message queue — send, receive, or ack messages via the daemon — Talks to the daemon message-queue API so agents/roles can coordinate
21. [`gludd_metrics`](#21-gludd-metrics) — Inject live daemon metrics (agents/usage/cost/benchmarks) as ansible_facts — Queries the daemon's read-only C(GET /api/metrics) endpoint and returns the metrics snapshot under C(ansible_facts
22. [`gludd_model_call`](#22-gludd-model-call) — Run a model generation via the daemon API — Sends a prompt to the daemon's POST /admin/models/call endpoint
23. [`gludd_observe`](#23-gludd-observe) — Correlate telemetry across daemon-registered observability sources — Discovers operator-configured sources through the Gludd daemon
24. [`gludd_open_code`](#24-gludd-open-code) — Batched opencode agent tool patterns — gate, push, commit, test, status — Codifies the repeated back-and-forth tool-call patterns opencode agents perform by bundling multiple tool calls into single Ansible tasks
25. [`gludd_ornith`](#25-gludd-ornith) — Pull rejected Ornith training pairs and invoke improvement rollouts — {'Bidirectional seam for the gludd × Ornith symbiotic loop
26. [`gludd_osquery`](#26-gludd-osquery) — Query live system state via osquery and inject rows as ansible_facts — Runs a B(read-only) C(SELECT) query against C(osqueryi --json) and returns the result rows under C(ansible_facts
27. [`gludd_ping`](#27-gludd-ping) — Verify daemon reachability — Pings the general_ludd daemon by calling /healthz
28. [`gludd_proc_monitor`](#28-gludd-proc-monitor) — Report resource utilization, I/O and locks for gludd-managed processes — Queries the daemon's managed-process stats API and returns per-process resource utilization under C(ansible_facts
29. [`gludd_process`](#29-gludd-process) — List, signal, and inspect daemon-managed processes — Talks to the general_ludd daemon's managed-process API so a playbook (or the model running a job) can enumerate the processes the daemon launched, inspect a live process's resource usage, or deliver a signal to one — all without ever calling C(kill)/C(ps) directly on the managed node
30. [`gludd_push_guard`](#30-gludd-push-guard) — Enforce push-rate guard via force-push bypass tracking — Wraps the ForcePushTracker (scripts/push_rate_guard
31. [`gludd_reload`](#31-gludd-reload) — Hot-rotate a validated leaf code module with health-gated auto-rollback — Wraps C(general_ludd
32. [`gludd_scapy`](#32-gludd-scapy) — Packet crafting, sniffing, and pcap manipulation via Scapy — Wraps the C(general_ludd
33. [`gludd_schedule`](#33-gludd-schedule) — Compute concurrency-safe execution batches via the daemon scheduler — Posts a list of work-item descriptors to C(POST /api/schedule) and returns the ordered concurrency-safe batches as C(ansible_facts
34. [`gludd_skill`](#34-gludd-skill) — Select and render a skill with Jinja2 variables — Looks up a skill by name or trigger pattern and renders its body with Jinja2 C(StrictUndefined) — an unknown variable is an error, not silent empty text
35. [`gludd_slurm_deploy`](#35-gludd-slurm-deploy) — Deploy a vLLM or llama
36. [`gludd_spend`](#36-gludd-spend) — Fetch and configure the daemon spend-limiter — C(state=get) calls C(GET /api/spend) and returns the current spend snapshot as C(ansible_facts
37. [`gludd_stream`](#37-gludd-stream) — Stream input signals (video/audio/text/binary) and dispatch buffered chunks to a cloned role — Opens a device (e
38. [`gludd_traces`](#38-gludd-traces) — Inject recent execution traces (spans/cost/phase) as ansible_facts — Queries the daemon's read-only C(GET /api/traces) endpoint and returns the recent execution-trace snapshot under C(ansible_facts
39. [`gludd_worktree`](#39-gludd-worktree) — Manage git worktrees (idempotent) — Creates or removes a git worktree via git_automation

---

## Tool Reference

### 1. `gludd_abtest`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_abtest`

> Crash-isolated A/B test of a candidate code variant in a fresh subprocess — Runs a baseline (A = current C(src)) and a candidate (B = candidate worktree) under the SAME workload, each in a FRESH interpreter child process via C(general_ludd.abtest.run_ab). The candidate is NEVER imported into this process, so a candidate built to crash the whole app (C(os._exit), segfault, infinite loop, OOM) CANNOT take down the Ansible controller or the daemon. Fail-closed: B is promoted ONLY if A passed and B ran ok, did not crash, did not time out, and stayed within a duration slack. Any crash/timeout yields C(promote=false). Runs in-process (same venv as C(general_ludd)); does NOT call the daemon.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `baseline_root` | str | |  |
| `candidate_root` | str | **required** |  |
| `expect_attr` | str | |  |
| `mem_limit_mb` | int | | `512` |
| `module` | str | **required** |  |
| `repo_root` | str | | `"."` |
| `timeout` | float | | `60.0` |
| `verdict_path` | str | |  |

### 2. `gludd_accounting`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_accounting`

> Fetch per-project accounting snapshots via the daemon — C(state=all) calls C(GET /api/accounting) and returns accounting snapshots for ALL known projects as C(ansible_facts.gludd_accounting). C(state=project) calls C(GET /api/accounting/{project_id}) and returns the accounting snapshot for a single project. Both operations are read-only. PSK-authed; check-mode safe.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `daemon_url` | str | | `"http://localhost:8000"` |
| `project_id` | str | |  |
| `psk` | str | | `""` |
| `state` | str | | `"all"` |
| `timeout` | int | | `30` |

### 3. `gludd_agent_run`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_agent_run`

> Run the agent tool-call loop (prompt + tools → answer) — W6.8 decision: uses the existing C(ToolCallLoop) from C(execution.tool_loop) — langgraph/langchain are declared deps with zero production callers, so option (b) was chosen (keep ToolCallLoop; note that langgraph removal is deferred to W4.5 deps-audit). Accepts a prompt and optional tool list, iterates model/tool calls up to C(max_iterations) times, and returns the final answer and tool call history. Check mode skips the model call and returns a placeholder.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `daemon_url` | str | | `"http://localhost:8000"` |
| `max_iterations` | int | | `10` |
| `model_profile` | str | | `""` |
| `prompt` | str | **required** |  |
| `psk` | str | | `""` |
| `system_prompt` | str | | `""` |
| `timeout` | int | | `120` |
| `tools` | list | | `[]` |

### 4. `gludd_break_glass`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_break_glass`

> OpenBao raft snapshot and restore (break-glass backup) — Wraps the OpenBao HTTP API for the two break-glass endpoints C(/v1/sys/storage/raft/snapshot) (GET — snapshot) and C(/v1/sys/storage/raft/restore) (POST — restore). Mode C(snapshot) fetches the current raft snapshot bytes and writes them to C(output_path). Mode C(restore) POSTs the bytes from C(restore_source) back into a running OpenBao server. The C(token) argument is marked C(no_log=True); OpenBao tokens MUST NOT leak into Ansible task output. This module is safe to call from C(check_mode) for snapshot (it does nothing destructive); restore is refused in check_mode.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `mode` | str | | `"snapshot"` |
| `openbao_addr` | str | **required** |  |
| `output_path` | str | | `""` |
| `restore_source` | str | | `""` |
| `token` | str | **required** |  |

### 5. `gludd_db`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_db`

> Todo/resource CRUD via daemon HTTP API (never raw SQLite) — Performs todo and resource operations against the daemon's REST API. {'Supported ops': 'C(todo_get), C(todo_create), C(todo_update_status), C(resource_preference).'} C(todo_create) issues POST /api/todos on the daemon (never raw SQLite). NEVER opens the SQLite file directly (single-writer rule). Check mode skips write operations.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `daemon_url` | str | | `"http://localhost:8000"` |
| `description` | str | | `""` |
| `op` | str | **required** |  |
| `priority` | str | | `"medium"` |
| `project_id` | str | |  |
| `psk` | str | | `""` |
| `queue` | str | | `"core"` |
| `resource_type` | str | |  |
| `role` | str | | `""` |
| `status` | str | |  |
| `timeout` | int | | `30` |
| `title` | str | |  |
| `todo_id` | str | |  |
| `work_type` | str | | `"code"` |

### 6. `gludd_dispatch`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_dispatch`

> Interact with the daemon's dynamic-dispatch API — C(state=dispatch) POSTs a tool-call to C(POST /api/dispatch) with a C(kind)/C(name)/C(args) body and returns the dispatch result as C(ansible_facts.gludd_dispatch). C(state=available) calls C(GET /api/dispatch/available) and returns the list of dispatchable tool handlers. C(state=recent) calls C(GET /api/dispatch/recent) and returns the most recent dispatch records. PSK-authed; check-mode safe on C(state=available) and C(state=recent). C(state=dispatch) skips the API call in check mode and returns an empty result.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `args` | dict | |  |
| `daemon_url` | str | | `"http://localhost:8000"` |
| `kind` | str | |  |
| `name` | str | |  |
| `psk` | str | | `""` |
| `state` | str | | `"available"` |
| `timeout` | int | | `30` |

### 7. `gludd_embed`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_embed`

> Embedding similarity over the daemon's bert surface — With C(op=similar) (the default) queries the daemon's read-only C(POST /api/embeddings/similar) endpoint and returns the ranked similar canonical task types under C(ansible_facts.gludd_embed) so a playbook (or the model running a job) can borrow a good model/prompt from a semantically-neighboring task type. With C(op=compare) queries C(POST /api/embeddings/compare) to measure the pairwise similarity of two strings (C(text_a)/C(text_b)) produced by separate bots/agents — so a role can decide how to proceed (near-duplicate strings -> merge/dedupe; divergent -> escalate). Supply C(texts) (2+) instead for the full pairwise similarity matrix. The snapshot is injected under C(ansible_facts.gludd_embed). With C(op=search) queries C(POST /api/embeddings/search) to take a string a bot produced (C(text)) and search a real corpus with it (RAG search), returning the C(top_k) most-similar items ranked by cosine similarity. C(corpus) selects the corpus — C(skills) (the live skill registry, descriptions matched on the fly), C(task_types) (the canonical task types), C(prompts) (the persisted prompt profiles), C(traces) (recent execution traces, work_type/phase/span descriptions matched on the fly), or C(events) (recent audit events, event_type/entity_type and a summary of the details JSON matched on the fly). The snapshot is injected under C(ansible_facts.gludd_embed). Read-only and check-mode safe — it performs no writes (C(changed=False)). Similarity is computed over the same embedding layer the adaptive router uses (HashEmbedder offline, OpenAIEmbedder when C(OPENAI_API_KEY) is set on the daemon).

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `corpus` | str | | `"skills"` |
| `daemon_url` | str | | `"http://localhost:8000"` |
| `include_embedding` | bool | | `false` |
| `include_embeddings` | bool | | `false` |
| `op` | str | | `"similar"` |
| `psk` | str | | `""` |
| `text` | str | |  |
| `text_a` | str | |  |
| `text_b` | str | |  |
| `texts` | list | |  |
| `timeout` | int | | `30` |
| `top_k` | int | | `5` |
| `work_type` | str | |  |

### 8. `gludd_environment`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_environment`

> Inject the consolidated environment + optimization brief as ansible_facts — Queries the daemon's read-only C(GET /api/environment) endpoint and returns the consolidated environment brief under C(ansible_facts.gludd_environment) so a playbook (or the model running a job) can see the environment it runs inside and how to optimize for the task. Read-only and check-mode safe — it performs no writes. Exposes C(models) (roster, NO secrets), C(routing), C(budget), C(compute), C(tools), C(skills), C(queues), C(system), and C(optimization) (advisor hints + per-work-type recommended profiles). The model roster NEVER contains api keys, tokens, or credential aliases.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `daemon_url` | str | | `"http://localhost:8000"` |
| `priority` | str | | `"quality"` |
| `prompt_tokens` | int | |  |
| `psk` | str | | `""` |
| `timeout` | int | | `30` |
| `work_type` | str | |  |

### 9. `gludd_facts`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_facts`

> Inject live daemon facts (work/todo/model/history/messages) as ansible_facts — Queries the daemon's read-only C(GET /api/facts) aggregation endpoint and returns the structured snapshot under C(ansible_facts.gludd) so a playbook can branch on live data in C(when:) / C(vars:). Read-only and check-mode safe — it performs no writes. Exposes C(gludd.work), C(gludd.todos), C(gludd.models), C(gludd.history), and C(gludd.messages).

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `daemon_url` | str | | `"http://localhost:8000"` |
| `project_id` | str | |  |
| `psk` | str | | `""` |
| `timeout` | int | | `30` |

### 10. `gludd_features`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_features`

> Fetch and verify the feature database via the daemon — C(state=list) calls C(GET /api/features) and returns all features (optionally filtered by status/category) as C(ansible_facts.gludd_features). C(state=verify) calls C(POST /api/features/verify) to trigger the server-side FeatureVerifier pass and returns the verification summary + per-feature results. Both operations are read-only from the controller perspective; the daemon may persist verification results internally on a C(state=verify) call. PSK-authed; check-mode safe on C(state=list). C(state=verify) skips the API call in check mode and returns an empty summary.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `category` | str | |  |
| `daemon_url` | str | | `"http://localhost:8000"` |
| `project_id` | str | |  |
| `psk` | str | | `""` |
| `state` | str | | `"list"` |
| `status` | str | |  |
| `timeout` | int | | `30` |

### 11. `gludd_gate_check`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_gate_check`

> Check whether a .gate-status file is complete and passed — Reads the C(.gate-status) file written by C(make gate) and determines whether the gate run is complete (a terminal marker is present) and whether it passed. C(gate_complete) is C(true) when the file exists and contains either C(=== GATE: PASSED ===) or C(=== GATE: FAILED ===). C(gate_passed) is C(true) when the file exists, is complete, and contains C(=== GATE: PASSED ===). Check-mode safe — this module performs no writes. Wraps the same logic as C(scripts/gate_fresh_check.py).

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `gate_path` | str | | `".gate-status"` |
| `state` | str | | `"check"` |

### 12. `gludd_git`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_git`

> Git control-plane ops (commit/branch/worktree/merge/push) via git_automation — Exposes gludd's hardened C(general_ludd.git_automation.GitAutomation) control plane to roles/playbooks so an agent-authored job can perform git operations WITHOUT reimplementing the safety logic. This is a thin B(delegating wrapper) — it does not reimplement git. The Python core provides per-repo C(.git/index.lock) serialization (issue #63), a bounded subprocess timeout, a non-interactive git environment, leading-dash ref rejection, C(--) end-of-options separators, worktree-path traversal guards, and typed results the daemon also consumes synchronously. Keeping that core in Python (rather than a pure role) preserves those guarantees; this module simply makes the same operations available on the Ansible execution seam. Idempotent where git is: C(branch) is a no-op if the branch already exists; C(commit) reports C(changed=false) when there is nothing to commit. Check-mode safe — read-only C(worktree_list) runs; mutating ops are skipped in check mode and report the change they WOULD make.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `branch` | str | |  |
| `clone_allow_local` | bool | | `true` |
| `clone_url` | str | |  |
| `files` | list | | `[]` |
| `gate_cmd` | list | | `[]` |
| `git_clone_timeout` | int | | `120` |
| `message` | str | |  |
| `op` | str | **required** |  |
| `path` | str | **required** |  |
| `remote` | str | | `"origin"` |
| `sha` | str | |  |
| `source` | str | |  |
| `state_assert_clean` | bool | | `false` |
| `state_assert_gha_matches_local` | bool | | `false` |
| `state_assert_merge_ready` | bool | | `false` |
| `state_assert_no_feature_on_master` | bool | | `false` |
| `state_assert_no_unintegrated_branches` | bool | | `false` |
| `state_assert_no_unintegrated_worktrees` | bool | | `false` |
| `state_assert_remote_head` | bool | | `false` |
| `state_gha_head_sha` | str | | `""` |
| `state_preserve_branch_patterns` | list | | `[]` |
| `state_reconciled_preserve_head_file` | str | | `"config/reconciled_preserved_heads.txt"` |
| `state_reconciled_preserve_heads` | list | | `[]` |
| `state_ref` | str | | `""` |
| `state_worktree_target_ref` | str | | `"HEAD"` |
| `strategy` | str | | `"ff"` |
| `tag` | str | |  |
| `target` | str | |  |
| `target_dir` | str | |  |
| `todo_id` | str | |  |
| `worktree_path` | str | |  |

### 13. `gludd_human_todo`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_human_todo`

> File or resolve a bot→human request (HumanTodo) via the daemon — Agents use this module to ask a human for something they cannot get on their own — a permission escalation, an external action, a decision, missing input, or another blocker. It is the structured replacement for "I gave up" log lines and event errors. C(state=present) files a new human-todo via POST /api/human-todos. When C(parent_agent_todo_id) is set, the parent agent todo is moved to C(blocked_on_human) and will not be dispatched until the human resolves the request. C(state=done) marks an existing human-todo done (human provided what was asked). C(state=dismissed) records that the human declined, so the agent knows to try a different approach. Check mode skips write operations.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `agent_id` | str | | `"agent"` |
| `body` | str | |  |
| `category` | str | | `"blocker"` |
| `daemon_url` | str | | `"http://localhost:8000"` |
| `human_resolution` | str | |  |
| `human_resolver` | str | | `"operator"` |
| `id` | str | |  |
| `parent_agent_todo_id` | str | |  |
| `priority` | str | | `"medium"` |
| `psk` | str | | `""` |
| `reason` | str | |  |
| `state` | str | | `"present"` |
| `tags` | list | | `[]` |
| `timeout` | int | | `30` |
| `title` | str | |  |

### 14. `gludd_introspect`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_introspect`

> Inject codebase self-knowledge facts (churn/complexity/coverage/debt) as ansible_facts — Queries the daemon's read-only C(GET /api/facts) endpoint and returns the C(codebase) self-introspection block under C(ansible_facts.gludd.codebase) so a self-improvement playbook can pick a high-value target (low coverage intersect high churn intersect debt). Read-only and check-mode safe — performs no writes. Exposes C(gludd.codebase.churn), C(gludd.codebase.complexity), C(gludd.codebase.coverage), C(gludd.codebase.debt), C(gludd.codebase.dead_code), C(gludd.codebase.missing_tests), C(gludd.codebase.perf_cost), and C(gludd.codebase.recent_failures). Each facet is C(null) when its source is unavailable — nothing is faked.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `daemon_url` | str | | `"http://localhost:8000"` |
| `psk` | str | | `""` |
| `timeout` | int | | `30` |

### 15. `gludd_langchain_generate`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_langchain_generate`

> Generate text (optionally structured JSON) via the daemon — Sends a prompt to the daemon's POST /admin/models/call endpoint, which runs the generation through the LangChain-backed model gateway. When C(response_schema) is supplied the returned text is fence-stripped and parsed as JSON; a parse failure is returned as a clean module failure rather than a traceback. This module is stdlib-only; it never imports LangChain itself. All LangChain work happens server-side in the daemon.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `daemon_url` | str | | `"http://localhost:8000"` |
| `max_tokens` | int | | `2048` |
| `model_profile` | str | |  |
| `prompt` | str | **required** |  |
| `psk` | str | | `""` |
| `response_schema` | dict | |  |
| `route_task_type` | str | |  |
| `system` | str | | `""` |
| `timeout` | int | | `120` |

### 16. `gludd_langgraph_decision`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_langgraph_decision`

> Ask the model to choose one option from a fixed set — Sends a decision prompt plus a list of allowed option tokens to the daemon's POST /admin/models/call endpoint and asks the model to reply with JSON of the form {"decision": ..., "rationale": ...}. The reply is fence-stripped and JSON-parsed; the chosen decision is validated against C(options). On any failure (bad JSON, decision not in the option set) the module falls back to the first option, marks C(valid)=false, and records a warning rather than crashing. This module is stdlib-only; it never imports LangGraph/LangChain.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `daemon_url` | str | | `"http://localhost:8000"` |
| `model_profile` | str | |  |
| `options` | list | **required** |  |
| `prompt` | str | **required** |  |
| `psk` | str | | `""` |
| `route_task_type` | str | |  |
| `system` | str | |  |
| `timeout` | int | | `120` |

### 17. `gludd_langgraph_workflow`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_langgraph_workflow`

> Run a multi-step LangGraph generate/review workflow — Sends a message list to the daemon's POST /admin/models/workflow endpoint, which executes a LangGraph generate -> review -> retry loop server-side and returns the best content plus quality metadata. This module is stdlib-only; it never imports LangGraph itself. All graph execution happens in the daemon.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `daemon_url` | str | | `"http://localhost:8000"` |
| `enable_graph` | bool | | `true` |
| `max_retries` | int | | `2` |
| `model_profile` | str | | `"default"` |
| `prompt` | str | **required** |  |
| `psk` | str | | `""` |
| `quality_threshold` | float | | `0.6` |
| `system` | str | | `""` |
| `timeout` | int | | `300` |
| `work_type` | str | | `"code"` |

### 18. `gludd_make`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_make`

> Run a make target via the MakeRunner abstraction — Runs a C(make) target through the C(MakeRunner) subprocess wrapper with proper sanitized environment, bounded output capture, and per-target timeout. Supports both blocking and streaming modes. Returns structured result (rc, stdout_tail, stderr_tail, success, phases).

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `cwd` | str | |  |
| `env_extra` | dict | |  |
| `extra_args` | list | | `[]` |
| `no_log` | bool | | `false` |
| `stream` | bool | | `false` |
| `target` | str | **required** |  |
| `timeout_s` | int | |  |

### 19. `gludd_mcp_tool`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_mcp_tool`

> Invoke an MCP tool (honest placeholder — not yet wired) — Per the W3.9 decision in TASKS.md (MCP honestly fenced): the daemon loads C(mcp_servers) config but passes C(mcp_client=None) — no MCP tools can be called through the daemon today. This module exists so playbooks can reference C(general_ludd.agent.gludd_mcp_tool) without import errors; it always returns C(not_implemented=true) and C(failed=false) so callers can C(when: not mcp_result.not_implemented) gate around it cleanly. When MCP wiring (W3.9 option a) is completed, replace the body of this module and remove this note.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `arguments` | dict | | `{}` |
| `daemon_url` | str | | `"http://localhost:8000"` |
| `psk` | str | | `""` |
| `server` | str | **required** |  |
| `timeout` | int | | `30` |
| `tool` | str | **required** |  |

### 20. `gludd_message`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_message`

> Inter-agent message queue — send, receive, or ack messages via the daemon — Talks to the daemon message-queue API so agents/roles can coordinate. C(state=send) posts a message to a recipient role/agent (or C(broadcast)). C(state=receive) fetches the inbox for C(recipient); messages are returned both as C(ansible_facts.gludd_inbox) and a C(messages) list. Pass C(ack=true) to mark every received message read in the same task. C(state=ack) marks a single C(message_id) read. Check mode skips the write side of C(send)/C(ack); C(receive) is always safe.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `ack` | bool | | `false` |
| `body` | str | | `""` |
| `daemon_url` | str | | `"http://localhost:8000"` |
| `message_id` | str | |  |
| `priority` | str | | `"normal"` |
| `project_id` | str | |  |
| `psk` | str | | `""` |
| `recipient` | str | |  |
| `sender` | str | |  |
| `state` | str | **required** |  |
| `timeout` | int | | `30` |
| `topic` | str | | `""` |
| `ttl_seconds` | int | |  |
| `unread` | bool | | `true` |

### 21. `gludd_metrics`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_metrics`

> Inject live daemon metrics (agents/usage/cost/benchmarks) as ansible_facts — Queries the daemon's read-only C(GET /api/metrics) endpoint and returns the metrics snapshot under C(ansible_facts.gludd_metrics) so a playbook can branch on live cost/usage/benchmark data in C(when:) / C(vars:). Read-only and check-mode safe — it performs no writes. Exposes agent-level metrics, global per-model usage, per-project cost, and benchmark rankings (when benchmark data is available). Reuses the daemon's MetricsCollector / BenchmarkRepository — no stat logic is recomputed client-side.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `agent_id` | str | |  |
| `daemon_url` | str | | `"http://localhost:8000"` |
| `project_id` | str | |  |
| `psk` | str | | `""` |
| `timeout` | int | | `30` |

### 22. `gludd_model_call`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_model_call`

> Run a model generation via the daemon API — Sends a prompt to the daemon's POST /admin/models/call endpoint. Supports direct model profile selection or adaptive routing by task type. Returns the generated text plus usage metadata.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `daemon_url` | str | | `"http://localhost:8000"` |
| `max_tokens` | int | | `2048` |
| `model_profile` | str | |  |
| `prompt` | str | **required** |  |
| `psk` | str | | `""` |
| `route_task_type` | str | |  |
| `timeout` | int | | `120` |

### 23. `gludd_observe`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_observe`

> Correlate telemetry across daemon-registered observability sources — Discovers operator-configured sources through the Gludd daemon. Adapts those named sources to C(GluddObserve) without accepting arbitrary URLs. Runs query, timeline, incident-correlation, and topology workflows. Isolates a failing source so healthy source results still return. All operations are read-only and check-mode safe.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `by` | str | | `"trace_id"` |
| `correlate_by` | str | | `""` |
| `daemon_url` | str | | `"http://localhost:8000"` |
| `end` | float | |  |
| `kinds` | list | | `[]` |
| `op` | str | **required** |  |
| `psk` | str | | `""` |
| `role` | str | | `""` |
| `seed` | dict | | `{}` |
| `spec` | dict | | `{}` |
| `start` | float | |  |
| `timeout` | int | | `30` |
| `window` | list | | `[]` |
| `window_s` | float | | `300.0` |

### 24. `gludd_open_code`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_open_code`

> Batched opencode agent tool patterns — gate, push, commit, test, status — Codifies the repeated back-and-forth tool-call patterns opencode agents perform by bundling multiple tool calls into single Ansible tasks. Each action maps to a make target that composites the underlying checks. Check-mode safe — all actions report the change they WOULD make without executing the underlying command. All actions run locally via C(ansible.builtin.command) executing make targets in the repository root; no daemon round-trips are needed.

_No parameters._

### 25. `gludd_ornith`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_ornith`

> Pull rejected Ornith training pairs and invoke improvement rollouts — {'Bidirectional seam for the gludd × Ornith symbiotic loop. Two states': None} C(state=pairs) — fetch the most-recent training pairs whose outcome matches a comma-separated status list (e.g. C(rejected_by_gate,rejected_by_review,reverted)). These are the artifacts that NEED improvement. Hits the daemon's C(GET /admin/ornith/pairs) endpoint. C(state=improve) — invoke the daemon's model gateway (C(POST /admin/models/call)) with a structured "improve this artifact" prompt and return the proposed diff. The caller is responsible for writing the diff to disk, opening a PR, and filing a human-todo for review. The PR is NEVER auto-merged — the human-todo is the gate. Check mode skips both the network call and the model invocation.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `agent_id` | str | | `"ornith_self_improve"` |
| `daemon_url` | str | | `"http://localhost:8000"` |
| `limit` | int | | `10` |
| `lookback_days` | int | | `14` |
| `max_iterations` | int | | `5` |
| `model_profile` | str | | `""` |
| `project_id` | str | | `""` |
| `psk` | str | | `""` |
| `state` | str | | `"improve"` |
| `status` | str | | `"rejected_by_gate,rejected_by_review,reverted"` |
| `target_files` | list | | `[]` |
| `task_description` | str | | `""` |
| `timeout` | int | | `120` |

### 26. `gludd_osquery`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_osquery`

> Query live system state via osquery and inject rows as ansible_facts — Runs a B(read-only) C(SELECT) query against C(osqueryi --json) and returns the result rows under C(ansible_facts.gludd_osquery) so a playbook (or the model running a job) can branch on real system state — processes, users, mounts, network interfaces, installed packages, system_info, etc. SECURITY — only C(SELECT) queries are permitted. The query is validated to start with C(SELECT) (after optional C(WITH ...) CTEs) and is rejected if it contains any mutating / side-effecting keyword (C(INSERT)/C(UPDATE)/C(DELETE)/C(DROP)/C(CREATE)/C(ALTER)/C(ATTACH)/ C(DETACH)/C(PRAGMA)/C(REPLACE)/C(VACUUM)). osquery's virtual tables are mostly read-only, but this module refuses to even hand a write-shaped query to the binary. The osquery binary is resolved from the daemon's filestore (C(binaries/osquery), downloaded on first use by the BinaryBootstrapper) when running in the same venv as the daemon; otherwise it falls back to an C(osqueryi) on the system C(PATH). Runs the binary via an explicit argument list (never C(shell=True)). Read-only and check-mode safe — it performs no writes. In check mode the query is validated and the binary located, but osquery is not executed.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `daemon_url` | str | | `"http://localhost:8000"` |
| `osquery_path` | str | | `""` |
| `psk` | str | | `""` |
| `query` | str | **required** |  |
| `timeout` | int | | `10` |

### 27. `gludd_ping`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_ping`

> Verify daemon reachability — Pings the general_ludd daemon by calling /healthz. Returns C(pong=true) and C(daemon_reachable) flag. Safe to use in check mode (read-only).

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `daemon_url` | str | | `"http://localhost:8000"` |
| `psk` | str | | `""` |
| `timeout` | int | | `10` |

### 28. `gludd_proc_monitor`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_proc_monitor`

> Report resource utilization, I/O and locks for gludd-managed processes — Queries the daemon's managed-process stats API and returns per-process resource utilization under C(ansible_facts.gludd_proc_monitor) so a playbook (or the model running a job) can branch on real process health — CPU, memory (RSS/VMS), I/O counters, open file descriptors, thread count, context switches, open files and held locks. When C(pid) is C(0) (the default) the module enumerates every gludd-managed process via C(GET /admin/processes) and then fetches stats for each B(alive) process. A process that exits mid-scan (its per-pid stats call returns C(404)) is skipped rather than failing the whole task. When C(pid) is greater than C(0) only that single process's stats are returned. B(Read-only) and B(check-mode safe) — it performs no writes. In check mode no daemon call is made and an empty fact set is returned (mirroring C(gludd_osquery)). Talks to the daemon over HTTP via the shared C(GluddClient) (stdlib C(urllib) only — no third-party deps in the managed-node venv).

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `daemon_url` | str | | `"http://localhost:8000"` |
| `pid` | int | | `0` |
| `psk` | str | | `""` |
| `timeout` | int | | `10` |

### 29. `gludd_process`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_process`

> List, signal, and inspect daemon-managed processes — Talks to the general_ludd daemon's managed-process API so a playbook (or the model running a job) can enumerate the processes the daemon launched, inspect a live process's resource usage, or deliver a signal to one — all without ever calling C(kill)/C(ps) directly on the managed node. C(action=list) calls C(GET /admin/processes) and returns the registry of managed processes under C(ansible_facts.gludd_process). C(action=status) calls C(GET /admin/processes/{pid}/stats) and returns a live psutil snapshot for one process under C(ansible_facts.gludd_process.stats). C(action=signal) calls C(POST /admin/processes/{pid}/signal) to deliver a signal (optionally to the whole process group). Signal delivery is a state change, so it reports C(changed=True) on success. SECURITY — for C(action=signal) the signal name is validated client-side against a small allow-list before any request is sent (defence in depth; the daemon enforces the same set server-side). Disallowed signal names are rejected without contacting the daemon. Read-only and check-mode safe for C(list)/C(status). In check mode a C(signal) request is B(not) sent — the module reports the change it would make (C(changed=True)) instead.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `action` | str | **required** |  |
| `daemon_url` | str | | `"http://localhost:8000"` |
| `group` | bool | | `false` |
| `pid` | int | | `0` |
| `psk` | str | | `""` |
| `signal` | str | | `"SIGTERM"` |
| `timeout` | int | | `10` |

### 30. `gludd_push_guard`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_push_guard`

> Enforce push-rate guard via force-push bypass tracking — Wraps the ForcePushTracker (scripts/push_rate_guard.py) as an idempotent Ansible module. Tracks consecutive GLUDD_FORCE_PUSH bypasses in a JSON state file and rejects further bypasses when the configured C(max_bypasses) threshold is exceeded within C(window_hours). Supports three states: C(check) to query whether a bypass is allowed, C(record) to persist a bypass event, and C(reset) to clear the counter (normal push). Check-mode safe — C(check) and C(record) report what would change without mutating the state file.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `max_bypasses` | int | | `5` |
| `state` | str | | `"check"` |
| `state_file` | str | | `".gate-logs/force-push-track.json"` |
| `window_hours` | float | | `12.0` |

### 31. `gludd_reload`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_reload`

> Hot-rotate a validated leaf code module with health-gated auto-rollback — Wraps C(general_ludd.reload.hot_reloader.HotReloader.reload_code_module): snapshots the live module bytes, C(os.replace)s the candidate source over the live path, C(importlib.reload)s the module, then runs a health gate (a C(/readyz) poll). If the health gate fails or the reload raises, the original bytes are restored and the module is reloaded again — the live module ends up exactly as it started. Fail-closed: a missing/non-importable target, a missing candidate, or a failed health gate all yield C(success=false). Runs in-process (same venv as C(general_ludd)).

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `candidate_source_path` | str | **required** |  |
| `config_dir` | str | | `"config"` |
| `health_timeout` | float | | `5.0` |
| `health_url` | str | |  |
| `module_name` | str | **required** |  |
| `result_path` | str | |  |

### 32. `gludd_scapy`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_scapy`

> Packet crafting, sniffing, and pcap manipulation via Scapy — Wraps the C(general_ludd.networking.scapy_adapter) so networking playbooks can craft, send, sniff, and analyze packets directly. Read-only actions (C(read_pcap), C(analyze_pcap), C(dissect_packet)) are check-mode safe and return C(changed=False). Mutating actions (C(write_pcap), C(craft_packet), C(send_packet), C(sniff_packets)) require the adapter binary (C(scapy)) and return C(changed=True) on success. All actions run via the adapter's Python API (never C(shell=True)).

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `action` | str | **required** |  |
| `count` | int | | `1` |
| `interface` | str | | `"eth0"` |
| `output_format` | str | | `"json"` |
| `packet_fields` | dict | |  |
| `packets` | list | |  |
| `pcap_path` | str | |  |
| `protocol_stack` | list | |  |
| `timeout` | int | | `30` |

### 33. `gludd_schedule`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_schedule`

> Compute concurrency-safe execution batches via the daemon scheduler — Posts a list of work-item descriptors to C(POST /api/schedule) and returns the ordered concurrency-safe batches as C(ansible_facts.gludd_schedule). Each work item specifies its exclusive resource requirements, upstream dependencies, and whether it is greenfield (no shared-resource conflicts). The daemon scheduler topologically sorts items, then groups them into batches where items within a batch may run concurrently and all dependencies are satisfied by strictly earlier batches. PSK-authed; check-mode safe (skips the API call and returns empty batches).

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `daemon_url` | str | | `"http://localhost:8000"` |
| `items` | list | **required** |  |
| `psk` | str | | `""` |
| `timeout` | int | | `30` |

### 34. `gludd_skill`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_skill`

> Select and render a skill with Jinja2 variables — Looks up a skill by name or trigger pattern and renders its body with Jinja2 C(StrictUndefined) — an unknown variable is an error, not silent empty text. Uses the shared C(render_skill) renderer also wired into C(execution.engine) so playbook and prompt paths render identically. Frontmatter C(required_vars) list is checked before rendering; missing vars fail the task with the variable name in the error message.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `name` | str | |  |
| `skills_path` | str | | `""` |
| `trigger` | str | |  |
| `variables` | dict | | `{}` |

### 35. `gludd_slurm_deploy`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_slurm_deploy`

> Deploy a vLLM or llama.cpp model server on a Slurm cluster — Submits a Slurm batch job that launches C(vllm serve) or C(llama_cpp.server) on an allocated GPU node and polls until the server is servable (writes a servable.json artifact with servable_url). Wraps C(general_ludd.infra.slurm_deployment.VllmSlurmDeployment) and C(LlamacppSlurmDeployment). Use this when the operator has a Slurm cluster and Slurm should arbitrate GPU access (fairshare + accounting). For cloud GPU, use Terraform; for local dev, use C(make local-model-vllm).

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `artifact_dir` | str | **required** |  |
| `engine` | str | **required** |  |
| `extra_args` | list | | `[]` |
| `gpu_count` | int | | `1` |
| `gpu_type` | str | | `"a100"` |
| `max_ctx` | int | | `32768` |
| `max_hours` | int | | `4` |
| `mem_gb` | int | | `32` |
| `model_id` | str | **required** |  |
| `module_loads` | list | | `[]` |
| `partition` | str | | `"gpu"` |
| `poll_interval` | float | | `5.0` |
| `poll_timeout` | int | | `300` |
| `port` | int | | `8000` |

### 36. `gludd_spend`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_spend`

> Fetch and configure the daemon spend-limiter — C(state=get) calls C(GET /api/spend) and returns the current spend snapshot as C(ansible_facts.gludd_spend). C(state=configure) calls C(POST /api/spend/configure) to update the spend-limiter settings (limit_usd and/or window_seconds). PSK-authed; check-mode safe on C(state=get). C(state=configure) skips the API call in check mode and returns an empty diff.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `daemon_url` | str | | `"http://localhost:8000"` |
| `limit_usd` | float | |  |
| `psk` | str | | `""` |
| `state` | str | | `"get"` |
| `timeout` | int | | `30` |
| `window_seconds` | int | |  |

### 37. `gludd_stream`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_stream`

> Stream input signals (video/audio/text/binary) and dispatch buffered chunks to a cloned role — Opens a device (e.g. C(/dev/video0), C(hw:0,0), C(pulse), C(rtsp://...), a file path) via the appropriate capture tool (ffmpeg / tail / cat) and streams bytes into a rolling in-memory buffer. When the configured C(dispatch_trigger) fires (size threshold, interval, silence detection, or external MQ message), the current buffer is written to C(artifact_dir/chunk-<n>.bin) and POSTed to the daemon's C(/admin/stream/dispatch) endpoint. The dispatch clones the *calling role* (the role invoking this module), injects the chunk as a named variable, and runs the clone on that chunk. An optional C(external_processor) (whisper.cpp / ffmpeg / agent) is invoked on the chunk before the cloned role's tasks run. The module stops when the C(stop_condition) fires (timeout, EOF, external MQ message, or max dispatches), drains the remaining buffer with a final dispatch, closes the device subprocess, and returns. Check mode skips device capture and HTTP dispatch; it returns a synthetic result describing the would-have-run pipeline.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `artifact_dir` | str | | `""` |
| `buffer_size` | int | | `1048576` |
| `daemon_url` | str | **required** |  |
| `device` | str | **required** |  |
| `device_kind` | str | **required** |  |
| `dispatch_role_clone` | dict | |  |
| `dispatch_trigger` | dict | |  |
| `external_processor` | dict | |  |
| `psk` | str | | `""` |
| `stop_condition` | dict | |  |

### 38. `gludd_traces`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_traces`

> Inject recent execution traces (spans/cost/phase) as ansible_facts — Queries the daemon's read-only C(GET /api/traces) endpoint and returns the recent execution-trace snapshot under C(ansible_facts.gludd_traces) so a playbook can branch on live trace data in C(when:) / C(vars:). Read-only and check-mode safe — it performs no writes. Exposes a bounded list of recent traces (trace_id, todo_id, work_type, total_cost_usd, total_tokens, success_rate, span_count, spans), a by-phase aggregate summary, and the OpenTelemetry exporter status (C(available) / C(disabled)). Traces are sourced ONLY from the daemon's in-process recent-traces buffer (genuinely-captured telemetry); no spans are fabricated. When no OTLP collector is configured the otel exporter status is honestly C(disabled).

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `daemon_url` | str | | `"http://localhost:8000"` |
| `limit` | int | | `20` |
| `psk` | str | | `""` |
| `timeout` | int | | `30` |
| `todo_id` | str | |  |

### 39. `gludd_worktree`

**Server:** `ansible`  
**FQCN:** `general_ludd.agent.gludd_worktree`

> Manage git worktrees (idempotent) — Creates or removes a git worktree via git_automation.repo.GitAutomation. Idempotent — C(changed=false) when the desired state already exists. In check mode reports what would change without modifying the filesystem.

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `branch` | str | **required** |  |
| `repo_path` | str | **required** |  |
| `state` | str | | `"present"` |
| `worktree_path` | str | **required** |  |

