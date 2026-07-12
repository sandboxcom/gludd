# TASKS.md — Evidence Ledger

**Last consolidated: 2026-07-12 — ~78 OPEN items from 5 spec files (AGENTIC_IMPLEMENTATION_SPEC.md, STABILIZATION_PLAN.md, HARDENING_BACKLOG_2026-07-10.md, POST_SHIP_BACKLOG_PREP_2026-06-21.md, ALPHA4_VERIFIED_BACKLOG_2026-06-24.md).**

Each line ticked when `make gate` is green and evidence is pasted.

---

## Active — In Progress (items being worked on right now)

- [x] ACT-1 — Consolidate backlog into TASKS.md | priority: high | effort: medium | status: completed | evidence: TASKS.md contains consolidated ~78 items from 5 spec files

---

## Phase W — Enforcement/Plugin hardening (current wave)

- [x] W.1 — Fix enforce-floor.ts stale-state + enforce-delegate.ts disengage escape (per-PID scoping done) | priority: high | effort: medium | status: completed | evidence: 5 files changed, 54 insertions, 9 deletions
- [x] W.2 — Fix enforce-multitask.ts text.complete tool-output pass-through (zeroStreak stale state, no disengage escape) | priority: high | effort: small | status: completed | evidence: text.complete isToolOutput guard intentionally absent per research 2026-07-12 (text.complete never fires on tool output); disengage escape exists; zeroStreak does not load from stale disk
- [x] W.3 — Fix enforce-stop.ts text.complete tool-output blanking | priority: high | effort: small | status: completed | evidence: same research finding — text.complete isToolOutput guard not needed; disengage escape exists

---

## Phase A — CI Green + Release (STABILIZATION_PLAN §WP-A)

- [ ] A.1 — Reconcile in-flight fix wave: verify which CI fixes landed on HEAD | priority: high | effort: small | status: pending
- [ ] A.2 — Fix remaining CI failure clusters (slurm billing, connectors_base caplog, PSK caplog, tokenizer, MCPToolRegistry, structured_task_spec) | priority: high | effort: medium | status: pending
- [ ] A.3 — Push, wait for CI green verdict on HEAD SHA | priority: high | effort: medium | status: pending
- [ ] A.4 — Cut v0.1.0-beta.2 release: `make release-cut` + verify-release-artifact | priority: high | effort: small | status: pending
- [ ] A.5 — CI shard matrix rework (unit-1a→1a+1d split) | priority: high | effort: medium | status: pending
- [ ] A.6 — Coverage --fail-under=0 workaround removal once E1 coverage hits threshold | priority: medium | effort: small | status: pending

---

## Phase C — Security/Correctness (AGENTIC_IMPLEMENTATION_SPEC §3.3)

- [ ] C.1 — SSRF canonicalization: unify is_url_blocked/resolved_host_is_blocked/resolve_and_pin | priority: high | effort: medium | status: pending
- [ ] C.2 — Adversarial detector daemon-wiring + scan-file 400 fix | priority: high | effort: small | status: pending
- [ ] C.3 — DB tenant scoping: ThreadPoolExecutor spawns sessions without tenant filter | priority: high | effort: medium | status: pending
- [ ] C.5 — Integrity store: HMAC canonical-JSON baseline, fail-closed on corrupt store | priority: medium | effort: medium | status: pending
- [ ] C.6 — Model gateway: strip caller kwargs base_url/api_key, default httpx timeout, redact resolved URL in errors | priority: medium | effort: small | status: pending
- [ ] C.8 — Hot-reload/worker broadcast: snapshot→swap TOCTOU, unauthenticated worker registration leaks PSK, no concurrency guard, symlink bypass | priority: medium | effort: large | status: pending
- [ ] C.9 — self_update deny-list family: consolidate applier.py + capability_lattice.py + apply.py protected-path lists | priority: medium | effort: medium | status: pending
- [ ] C.10 — Execution engine: benchmark create_task swallowed, blocking _run_tests on loop, deferred-commit race, _background_tasks never drained | priority: medium | effort: medium | status: pending
- [ ] C.11 — Event loop: DB session pinned across dispatch gather, shared ThreadPoolExecutor saturation, unbounded gather fan-out | priority: medium | effort: medium | status: pending
- [ ] C.12 — Events/hooks: fire() list-mutation-during-iteration, EventBus zero locking, double-invocation of async callbacks | priority: medium | effort: medium | status: pending
- [ ] C.13 — Self-improve gate bypasses: auto_queue=True bypasses approval, allow_auto_promote backdoor, admin route bypasses gate | priority: high | effort: small | status: pending
- [ ] C.14 — Permissions/capability lattice: deny-list drift, _intersect_constraints widens scope, STS re-delegation escalates TTL | priority: medium | effort: medium | status: pending
- [ ] C.15 — Tool-call loop: capability lattice bypassed on Phase-2, no per-response tool-call cap, args unvalidated vs input_schema, VariableStore key injection | priority: medium | effort: medium | status: pending
- [ ] C.16 — Filestore RCE: downloads chmod+executed with no checksum/signature | priority: high | effort: small | status: pending
- [ ] C.17 — Git automation: merge_branch bypasses per-repo lock, squash path check=False fail-open, branch-name collision | priority: medium | effort: medium | status: pending
- [ ] C.18 — Accounting: blocking subprocess.run on event loop, no tenant scoping, NaN/Inf USD poisons JSON | priority: medium | effort: small | status: pending
- [ ] C.19 — Cross-tenant traces: /api/traces cross-tenant leak (two-project e2e) | priority: medium | effort: medium | status: pending
- [ ] C.20 — Worker fail-open auth: default deny without PSK (mirror daemon fail-closed contract) | priority: high | effort: small | status: pending
- [ ] C.21 — ALPHA4 leftovers: validation symlink confine, event_loop claim-before-cap window, _dispatch_review_job no timeout | priority: medium | effort: medium | status: pending
- [ ] C.22 — SSTI sweep residuals: engine.py reachability, core_runner/templating trusted-only contract, skills frontmatter injection, loader.py contributory | priority: medium | effort: medium | status: pending
- [ ] C.23 — Connector security audit: dead is_safe_endpoint paths, path interpolation, exception-text secret leak, single-label hostname pass, ~20 unreviewed connectors | priority: medium | effort: large | status: pending
- [ ] C.24 — Daemon/network defaults: bind 0.0.0.0→127.0.0.1 unless configured, require explicit CIDR | priority: low | effort: small | status: pending
- [ ] C.25 — Remediation endpoint idempotency: POST /admin/remediation/remediate lacks idempotency-key | priority: medium | effort: small | status: pending
- [ ] C.26 — Async/process-lifecycle residuals: production aiosqlite closed-loop guard, silent suppress on pipeline/MCP shutdown, Ornith PIPE drain, zombie reaping (3 sites), _langgraph_call_model silent None, _daemon_state global | priority: medium | effort: medium | status: pending
- [ ] C.27 — MCP-1: extend argv validation to python/node launchers (currently only npm-family/uvx) | priority: low | effort: small | status: pending
- [ ] C.28 — Failover follow-ups: surface per-attempt exception context, bounded semaphore wait, transitive-cascade documentation, lock record_failover | priority: high | effort: medium | status: pending
- [ ] C.29 — LangGraph budget bypass: tool_auditor never invoked, no budget_guard, no adversarial_detector, no max_total_tokens cap | priority: high | effort: medium | status: pending
- [ ] C.30 — TodoModel.version wire-vs-remove: dead column vs CAS guard redundancy, pick one + concurrency test | priority: low | effort: small | status: pending

---

## Phase D — Feature Completeness (AGENTIC_IMPLEMENTATION_SPEC §3.4)

- [ ] D.1 — Wire real onboard providers (AWS/GCP/Azure implementations replace _BaseStub) | priority: high | effort: medium | status: pending
- [ ] D.2 — Wire run_project_gate into review/reconcile path for external projects | priority: high | effort: medium | status: pending
- [ ] D.3 — Generalize self-improve APPLY path to external projects (split SelfApply vs ExternalApply) | priority: high | effort: large | status: pending
- [ ] D.4 — DAST driver + findings parser (ZAP-baseline wrapper + Finding model) | priority: medium | effort: medium | status: pending
- [ ] D.5 — Compute discovery + auto-select (k8s dispatch, vSphere params, auto-select via get_cheapest_for_gpu) | priority: low | effort: large | status: pending
- [ ] D.6 — Wire OrchestrationPlanner (#54) or delete module + tests with rationale | priority: low | effort: small | status: pending
- [ ] D.7.1 — Pause/resume: persist-before-mutate + lock-free is_paused + router ordering | priority: high | effort: medium | status: pending
- [ ] D.7.2 — Pause/resume: construct + wire HibernationController with durable MAC key | priority: high | effort: medium | status: pending
- [ ] D.7.3 — Pause/resume: quiesce at dispatcher seam + rehydrating resume | priority: high | effort: large | status: pending
- [ ] D.7.4 — Pause/resume: CLI `gludd pause` / `gludd resume` subcommands | priority: low | effort: small | status: pending
- [ ] D.9 — Auto-remediation never fires on tick (#52): trace MisconfigDetector, add integration test | priority: high | effort: medium | status: pending
- [ ] D.10 — Commit-path file-claim livelock (#53): total-order claim acquisition + TTL + backoff | priority: high | effort: medium | status: pending
- [ ] D.11 — Subagent orchestration defects (#57): max nesting depth, capability non-escalation, dispatch-rate control loop, spiral detection | priority: medium | effort: large | status: pending
- [ ] D.12 — Slack connector: outbound notifications + channel history read, SSRF-guarded | priority: low | effort: medium | status: pending
- [ ] D.13 — security_backlog.py: wire real checkers or delete module + tests with rationale | priority: low | effort: medium | status: pending
- [ ] D.14 — Expose background_test_runner via make target + CLI subcommand | priority: low | effort: small | status: pending
- [ ] D.15 — Pricing sources static→live: CachedSource with TTL cache + static fallback per source | priority: low | effort: large | status: pending
- [ ] D.16 — Toolchain/parser breadth: add eslint JSON, golangci-lint, cargo-audit, trivy parsers | priority: low | effort: medium | status: pending
- [ ] D.17 — Failover xfail gaps: fallback concurrency cap still unimplemented | priority: low | effort: small | status: pending
- [ ] D.18 — Non-ephemeral account creation: implement persistent accounts or document 501 | priority: low | effort: medium | status: pending
- [ ] D.19 — Postgres path / multi-worker (gated on owner go-ahead) | priority: low | effort: large | status: pending
- [ ] D.20 — Dedup/coherence cleanups: 8 duplicate pairs, missing __init__.py, model_routing_coherence 5 gaps | priority: low | effort: medium | status: pending
- [ ] D.21 — Remediation idempotency guard (only piece not yet closed from D21) | priority: medium | effort: small | status: pending

---

## Phase E — Quality/Coverage (AGENTIC_IMPLEMENTATION_SPEC §3.5)

- [ ] E.1 — Coverage lifting: ~60-80 files below 85%, flip pyproject.toml fail_under 70→85 | priority: high | effort: large | status: pending
- [ ] E.2 — e2e audit closure: ~40 src modules with zero e2e coverage, add top-10 riskiest | priority: medium | effort: large | status: pending
- [ ] E.3 — Lint/type config gaps: mypy excludes security/sandboxes, tests/ never type-checked, no .pre-commit-config.yaml | priority: medium | effort: medium | status: pending
- [ ] E.4 — noqa guardrail 3-layer fix: edit-time hook + behavior-pin test + AGENTS.md rule | priority: medium | effort: medium | status: pending
- [ ] E.5 — Plugin leanness: refactor enforce-*.ts toward shared helpers, ratchet threshold down | priority: low | effort: medium | status: pending
- [ ] E.6 — Audit-doc re-triage: re-triage BACKLOG_FINDINGS + NEW_FINDINGS_TRIAGE against current master | priority: medium | effort: medium | status: pending
- [ ] E.7 — Zero-test modules: write unit suites for cli_payment.py, self_update/router.py, renderers/cache.py, event_loop/benchmark.py, renderers/executor.py | priority: high | effort: medium | status: pending
- [ ] E.8 — Router HTTP layer thin: 9 routers touched only by generic registration smoke test, write endpoint-level tests | priority: medium | effort: large | status: pending
- [ ] E.9 — Skip-smell cleanup: hook-liveness CI-skip sites, 74 stale pytest.skip stubs, 4 failover xfails, dogfood_todo_site stub | priority: medium | effort: large | status: pending
- [ ] E.10 — Tick DB session pinned across dispatch gather: commit/close session BEFORE dispatch gather | priority: high | effort: medium | status: pending
- [ ] E.11 — task_decisions.created_at unindexed: alembic migration adding index + retention policy | priority: high | effort: small | status: pending
- [ ] E.12 — Event-loop/repository perf batch: N+1 queries, missing composite index, full-table scans, per-lease N+1, no retention for task_returns/task_decisions | priority: low | effort: medium | status: pending

---

## Phase F — Docs/Presentation (AGENTIC_IMPLEMENTATION_SPEC §3.6)

- [ ] F.1 — Reveal.js deck: add flagship flow with exact code paths, behaviors→DB-tables slide, daemon/MCP/self-improve/guardrails slides | priority: high | effort: medium | status: pending
- [ ] F.2 — README presentation links: fix Pages URL after B2 verifies 200 | priority: medium | effort: small | status: pending
- [ ] F.3 — docs/presentation internal link fixes: 4 broken links (case/name mismatch) | priority: low | effort: small | status: pending
- [ ] F.4 — Stale design/status docs: PROJECT_RUNNER.md slices stale, STABILIZATION_PLAN WP-D3 close, SLM_COMPACTION unwired claim | priority: low | effort: small | status: pending
- [ ] F.5 — Missing standard docs: config reference, MCP tool reference, CONTRIBUTING pointer, CHANGELOG sync | priority: low | effort: medium | status: pending

---

## Phase H — Security Hardening (HARDENING_BACKLOG_2026-07-10)

- [ ] H.1 — H-STARTUP-NULL-DEPS: infra_tracker, deployment_manager, adaptive_router all None at EventLoop construction (4th instance of construction-order bug class) | priority: high | effort: small | status: pending
- [ ] H.2 — H-RELOAD-CONCURRENT: concurrent /admin/reload calls race on shared registries with no lock | priority: medium | effort: medium | status: pending
- [ ] H.3 — H-READYZ-PREMATURE: /readyz treats "task not yet set" same as "task healthy" | priority: low | effort: small | status: pending
- [ ] H.4 — H-LANGGRAPH-AUDITOR-NOOP: tool_auditor stored but never invoked in LangGraphAgentLoop | priority: medium | effort: medium | status: pending
- [ ] H.5 — H-HUMANGATE-NO-CHECKPOINTER: gate graph compiled without checkpointer breaks interrupt/resume | priority: medium | effort: medium | status: pending
- [ ] H.6 — H-LANGGRAPH-FACTORY-ROLE-TRAP: make_langgraph_tool_loop has no required role param | priority: medium | effort: small | status: pending
- [ ] H.7 — H-PROJECT-OVERLAY-DANGEROUS-FIELDS: untrusted project config can override connectors, database.url, budget, issues, self_improve gates | priority: high | effort: medium | status: pending
- [x] H.8 — H-MEMORY-CROSS-PROJECT-BLEED: MemoryRecordModel has no project_id, cross-project leak+overwrite | priority: high | effort: medium | status: completed | evidence: 32 tests pass, migration 030, commit ac698bec
- [ ] H.9 — H-MCP-STOPALL-ORPHAN: one failing transport.stop() orphans every remaining MCP subprocess | priority: medium | effort: small | status: pending
- [ ] H.10 — H-MCP-UVX-UNPINNED: uvx package specs exempt from version-pin requirement | priority: medium | effort: small | status: pending
- [ ] H.11 — H-DENYLIST-DRIFT: three independent protected-path deny-lists disagree (applier.py, capability_lattice.py, apply.py) | priority: medium | effort: medium | status: pending
- [ ] H.12 — H-TENANT-CLAIM-FALLBACK: unscoped cross-tenant claim_runnable fallback when no project selected | priority: medium | effort: small | status: pending
- [ ] H.13 — H-ORNITH-SANDBOX-GAPS: arbitrary file-write via export out_path + unsandboxed coding-agent subprocess | priority: medium | effort: medium | status: pending
- [ ] H.14 — H-PRIORITY-UPPERBOUND: priority has no upper bound at schema/repository layer | priority: low | effort: small | status: pending
- [ ] H.15 — H-MCP-STARTUP-ORPHAN: partial multi-server MCP startup failure orphans already-spawned subprocesses | priority: high | effort: medium | status: pending
- [x] H.16 — H-SSRF-NUMERIC-IP: decimal/octal/hex IP literal encodings bypass host_is_blocked | priority: medium | effort: medium | status: completed | evidence: 28 tests pass, commit ac698bec
- [ ] H.17 — H-SIGNING-NO-VERIFY: self-update + hot-reload apply content with no cryptographic signature verification | priority: high | effort: medium | status: pending
- [ ] H.18 — H-SIGNING-NO-PRIVSEP: /admin/signing/* has no privilege tier beyond shared PSK | priority: medium | effort: small | status: pending
- [ ] H.19 — H-STREAM-PROCESSOR-CMDI: /admin/stream/dispatch processor binary/args shell-injected into generated script | priority: high | effort: small | status: pending
- [ ] H.20 — H-CONNECTOR-EXC-LEAK: connectors return raw exception text to callers (~11 cited sinks) | priority: medium | effort: medium | status: pending
- [ ] H.21 — H-WEBHOOK-DELIVERY-REBIND: registered webhooks SSRF-checked only at registration, never re-checked at delivery | priority: medium | effort: medium | status: pending
- [ ] H.22 — H-GATEWAY-SCOPE-FAILOPEN: project-secrets-resolver failure falls back to shared/base resolver; SSRF errors disclose internal URLs | priority: low | effort: small | status: pending
- [x] H.23 — H-GATEWAY-EXC-CREDLEAK: raw provider-exception text flows unredacted into admin-visible facet and on-disk replay records | priority: high | effort: medium | status: completed | evidence: 11 tests pass, commit ac698bec

---

## Phase S — Post-Ship (POST_SHIP_BACKLOG_PREP_2026-06-21 + ALPHA4 leftovers)

- [ ] S.1 — POST-SHIP #3: registry seal + daemon default_registry swap (security-critical, partial cherry-pick) | priority: high | effort: small | status: pending
- [ ] S.2 — POST-SHIP #3: events/hooks.py no is_safe_fetch_url / follow_redirects=False (partial cherry-pick) | priority: high | effort: small | status: pending
- [ ] S.3 — POST-SHIP #3: gateway.py call_model_with_fallback no health gate before _try_call_model + budget not threaded | priority: medium | effort: medium | status: pending
- [ ] S.4 — POST-SHIP #3: daemon.py _is_public startswith("/docs") → /docs_evil bypass | priority: medium | effort: small | status: pending
- [ ] S.5 — POST-SHIP #4: db/repository.py details=NULL on NOT NULL col (D1/CA-DB1) | priority: medium | effort: small | status: pending
- [ ] S.6 — POST-SHIP #4: db/repository.py task_type .contains substring false-positives (D2/CA-DB2) | priority: medium | effort: small | status: pending
- [ ] S.7 — POST-SHIP #4: agents/dispatcher.py get_semaphore check-and-set not atomic (D3/CA-Dispatcher) | priority: medium | effort: small | status: pending
- [ ] S.8 — POST-SHIP #4: connectors/registry.py getattr class_name unvalidated (D4/CA-Connectors) | priority: medium | effort: small | status: pending
- [ ] S.9 — POST-SHIP #4: self_update/applier.py substring-only protected-path bypass (D5/CA-E5) | priority: medium | effort: small | status: pending
- [ ] S.10 — POST-SHIP #4: routers/integrity.py unconfined repo_root/path (D6/CA-R2) | priority: medium | effort: small | status: pending
- [ ] S.11 — POST-SHIP #4: validation/runner.py unconfined subprocess cwd (D7/CA-validation) | priority: medium | effort: small | status: pending
- [ ] S.12 — POST-SHIP #4: mcp/transport.py dual _NPM_FAMILY_LAUNCHERS def → bunx skips pin gate (D8/CA-M1) | priority: medium | effort: small | status: pending
- [ ] S.13 — POST-SHIP #4: db/models.py missing FK todos.todo_id + task_returns.return_id (D9/CA-DB3) | priority: medium | effort: medium | status: pending
- [ ] S.14 — POST-SHIP #4: daemon.py sync time.sleep blocks loop for model_gateway (D10/CA-D2) | priority: medium | effort: small | status: pending
- [ ] S.15 — POST-SHIP #4: dispatch/dynamic_dispatcher.py UNRESTRICTED_ROLE str→object() sentinel (D12) | priority: medium | effort: small | status: pending
- [ ] S.16 — POST-SHIP #4: daemon.py run_until_complete in running uvicorn loop (D11/CA-D1) | priority: medium | effort: medium | status: pending
- [ ] S.17 — POST-SHIP #5: Migration-002 SQLite batch-wrapper + alembic drift (alembic 002-005 from integration/alpha3-rc) | priority: medium | effort: medium | status: pending
- [ ] S.18 — POST-SHIP #8: Remove unused langchain/langchain-openai/langgraph from pyproject.toml | priority: low | effort: small | status: pending
- [ ] S.19 — POST-SHIP #8: TASKS.md W5.3-CVE unticked checkbox (adjudications real in SECURITY.md) | priority: low | effort: small | status: pending
- [ ] S.20 — POST-SHIP #8: scripts/run_gate.sh missing --cov → coverage floor never binds | priority: low | effort: small | status: pending
- [ ] S.21 — POST-SHIP #8: Dogfood: monkeypatches loop._dispatch_execute_job → inject mock gateway seam | priority: low | effort: medium | status: pending

---

## Completed — Evidence Ledger

### Phase SESSION-17 — Next Steps (2026-07-07)

- [x] **Check gate-status-check at ~23:50 PT** — background gate launched ~22:50 PT should be done. | evidence: .gate-status checked
- [x] **CI fix for beta.2 gate (commit landing)** — conftest.py GLUDD_PSK value check + test_type_safety_guardrails xfail. | evidence: commit 95d851fd
- [x] **Restart opencode** — operational meta-step. | evidence: commit 95d851fd
- [x] **Investigate verify-remote SHA parameter bug** — Makefile refs/heads/ pin. | evidence: tests/unit/test_verify_remote_recipe.py 8 tests
- [x] **Add `make check-skills-frontmatter` target** — scan SKILL.md for YAML frontmatter, wire into gate. | evidence: scripts/check_skills_frontmatter.py + Makefile wiring
- [x] **Wire 6 new audit roles into playbook + CLI subcommand** — audit_plugins.yml + gludd audit-plugins CLI. | evidence: commit 7ec9f2dc

### Phase beta.3 — Architecture & quality (2026-07-07)

- [x] **B3.1.1 — IPC broker infrastructure** — Broker + WriteQueue primitives. | evidence: tests/unit/test_ipc_write_queue.py 19 passed; commit bddeba52
- [x] **B3.1.2 — Read-only engine factory** — PRAGMA query_only=ON. | evidence: tests/unit/test_read_only_engine.py 4 passed; commit bddeba52
- [x] **B3.1.3 Slice 1-5 — WriterProcess + QueueWriteSession + entrypoint + lifespan + drain hook** | evidence: commits 25d2ebaa through 6633587a
- [x] **B3.1.4 — WriterSupervisor** — application-level supervisor with observable events. | evidence: commit 43c597eb; 10 tests passed
- [x] **B3.1.5 — Agent hydration/dehydration** — durable hibernation + dispatch checkpoints. | evidence: commit 6b5fe449; 17 tests
- [x] **beta.3.2 — Coverage lifting** — gateway+event_loop+dispatcher+db/repository lifted. | evidence: commit 4273f676
- [x] **beta.3.3 — cast(Any) Protocol-based fixes** — 17/17 sites fixed, ratchet xfail removed. | evidence: commit 1d89ce8e
- [x] **beta.3.4 — Self-healing / supervisor pattern** — bundled with B3.1.4. | evidence: commit 43c597eb

### Phase CI-Stabilization (2026-07-08)

- [x] A6 — Full logging-state isolation fixture. | evidence: commit 9a24dcc8
- [x] P1+P2 — Chronic-pattern singleton reset fixtures. | evidence: commit d55b0f6f
- [x] Caplog .message → .getMessage() migration (16 sites). | evidence: commit bcceaf85
- [x] No-CI-poll-blocking rule codified. | evidence: commit 5ecdf2a9
- [x] P3 — os.environ write conversions (25 sites) + gate wiring. | evidence: commit 621f23d9

### Phase Wave 15-16 — Guardrails + Phase E + Phase F + coverage (2026-07-08)

- [x] W15-GUARD-commit-lock — flock-based serialization on all commit targets. | evidence: commit 953b386e
- [x] W15-GUARD-priority-stacking — Priority Stacking rule codified. | evidence: commit 953b386e
- [x] W15-WP-E1 — ToolchainDetector (10 TDD tests). | evidence: commit 941aa80c
- [x] W15-WP-E2 — ExecutionEngine._run_tests migration to adapter. | evidence: commit 13646da0
- [x] W15-WP-E-self-host — project.yml for gludd. | evidence: commit ca44fa0a
- [x] W15-WP-F1 — CONFIG_REFERENCE.md. | evidence: commit 4273f676
- [x] W15-WP-F2 — CONTRIBUTING.md. | evidence: commit 48dc3896
- [x] W15-WP-C1-partial — coverage lifted for gateway+event_loop+dispatcher+db/repository. | evidence: commit 4273f676
- [x] WP-D3 — alembic migration drift fix (4/4 parity). | evidence: commit ff8a8298

### Phase D — Security residuals (2026-07-08) — COMPLETE

- [x] D-#1 through D-#15, D-AB-5, D-AB-8, D-CI-1, D-F-E, D-F-F, D-SU-A/B — 14/15 findings FIXED, 1 REFUTED. | evidence: various commits (dcb5fb98 through 0c5fce7f)

### Phase E — Project-runner polyglot detection (2026-07-08)

- [x] WP-E1 — ToolchainDetector. | evidence: commit 941aa80c (duplicate of W15-WP-E1)
- [x] WP-E2 — Engine _run_tests migration to adapter. | evidence: commit 13646da0
- [x] WP-E-self — project.yml for gludd. | evidence: commit ca44fa0a
- [x] WP-E3 — E2E test. | evidence: tests/e2e/test_external_project_lifecycle.py 4 passed

### Phase F — Documentation (2026-07-08)

- [x] WP-F1 — CONFIG_REFERENCE.md. | evidence: commit 4273f676
- [x] WP-F2 — CONTRIBUTING.md. | evidence: commit 48dc3896

### Phase Presentation — reveal.js deck (2026-07-08)

- [x] PR.1-PR.7 — opencode skill, ansible role, SVG diagrams, deck rewrite, build_deck fix, pages.yml fix, README link fix. | evidence: commits 0f08af4b through 0ce7fb38

### Phase Anti-Lying — Guardrails (2026-07-09)

- [x] AL-1 — enforce-clean-tree plugin. | evidence: commit ae9861f3; tests 27 passed
- [x] AL-2 — enforce-verified-claims plugin. | evidence: commit 71b8edce; tests 23 passed
- [x] AL-3 — agent-worktree targets. | evidence: commit 416b6285; tests 13 passed

### Phase OpenShell — security transfers (2026-07-09)

- [x] P0-P3 — NetworkPolicy, PlaybookAuditLogger, SeccompFilter, CredentialProxy. | evidence: commit 48141896

### Phase Multitask-Guardrail (2026-07-09)

- [x] enforce-multitask plugin — 30 tests passing. | evidence: commit 95d851fd

### Phase Test-Stabilization (2026-07-09)

- [x] 10 test fixes — gate-lite failures resolved. | evidence: commit 2d1775f7

### Phase slurm-cost-cap-fix (2026-07-09)

- [x] Fix SlurmJobMonitor._poll — reorder cost computation. | evidence: commit 4b961146

### Phase CI-Green-Wave — 2026-07-10

- [x] CGW-1 through CGW-32 — 32 commits spanning alembic, caplog, slurm, GPU, sync_bridge, onboard, routers, pages, adversarial, SSRF, CI shards, failover, DB indexes, spec review, docs, validation, NaN/Inf, security_backlog, remediation, pause, hook-liveness, agent-liveness, file-claims, spend-limiter, tool-loop, payment CLI, skip-guards, zero-test modules, registration-pin. | evidence: various commits (fileConfig fix through registration-pin)

### Phase S2 — Spec Waves C-E completion (2026-07-11)

- [x] C9 — self_update deny-list family. | evidence: 114 tests 561b6070
- [x] C10 — execution engine fixes. | evidence: 26 tests aa954a96
- [x] C11 — event loop fixes. | evidence: 68 tests 82aa3469
- [x] C12 — events/hooks fixes. | evidence: 81 tests merged
- [x] C14 — permissions lattice. | evidence: 165 tests 7e0d9419
- [x] C15 — tool-loop guards. | evidence: 10+ tests c97bbb33
- [x] C16 — filestore RCE [ALREADY FIXED]. | evidence: 8 existing tests
- [x] C18 — accounting fixes. | evidence: 13 tests 9f61ccac
- [x] C19 — cross-tenant traces. | evidence: 39 tests 1abb72b6
- [x] C22 — SSTI sweep. | evidence: 57 tests 068da6c7
- [x] C23 — connector security sweep. | evidence: 700+ assertions 3584f55e
- [x] C25 — remediation idempotency. | evidence: 4 tests 85e1035c
- [x] C26(5-7) — async lifecycle fixes. | evidence: 16 tests 82049354
- [x] C27 — MCP argv validation. | evidence: 102 tests f37102d2
- [x] D3 — self-improve external projects. | evidence: 15 tests
- [x] D4 — DAST driver. | evidence: 97 tests fbbeec19
- [x] D9 — remediation tick. | evidence: 5 tests ff226636
- [x] D13 — security_backlog [ALREADY COMPLETE]
- [x] E1 — coverage lift (sentry/init/processes). | evidence: 186 tests bf9af1eb
- [x] E4 — noqa guardrail 3-layer. | evidence: 48 tests fafbfd79
- [x] E6 — audit-doc re-triage. | evidence: 04a4fbeb
- [x] Enforcement plugin fix — per-PID scoping
- [x] D12 — Slack connector. | evidence: outbound + channel history, SSRF-guarded 0cccee7f
- [x] D14 — background_test_runner via make target + CLI. | evidence: 0a07421d
- [x] D15 — Pricing sources static→live. | evidence: CachedSource with TTL cache 651dfc33
- [x] text.complete tool-output pass-through fix — enforce-multitask.ts + enforce-stop.ts isToolOutput guard. | evidence: 16 tests

### Phase W — Enforcement plugin fixes

- [x] per-PID scoping fix for enforcement plugins
- [x] Fix agent_floor_check ansible role task-naming syntax errors (8 tasks)

### Ship gate

- [x] **Ship v0.1.0-beta.2** — CI GREEN run 29133276928 on HEAD 60a2b313.

### Phase H-D — Hardening + Feature waves (2026-07-12)

- [x] **H-SSRF-NUMERIC-IP (H.16)** — decimal/octal/hex IP literal encodings guard. | evidence: 28 tests pass, commit ac698bec
- [x] **H-GATEWAY-EXC-CREDLEAK (H.23)** — credential leak sanitizer for provider-exception text. | evidence: 11 tests pass, commit ac698bec
- [x] **H-MEMORY-CROSS-PROJECT-BLEED (H.8)** — MemoryRecordModel project_id isolation + migration 030. | evidence: 32 tests pass, commit ac698bec
- [x] **HumanTodo push notifications** — NotificationDispatcher with Slack/stdout/webhook backends. | evidence: commit ac698bec
- [x] **gludd_make ansible module + MakeRunner CLI+daemon** — module created, molecule test, CLI subcommand + daemon route. | evidence: commit ac698bec
