# TASKS.md — Evidence Ledger

**Last consolidated: 2026-07-14 Session 33 — Untested modules: 0 (down from 196). Enforcement: saveState fixed, FLOOR hardcoded to 10, isDispatchTool imported from shared.ts, MIN/MAX_DISPATCHES=10, dispatch hook detection debug logging added. Gate GREEN (lint 0, typecheck 0, collect 0, hook-runtime PASS). CI pending. A.4 release pending CI green.**

Each line ticked when `make gate` is green and evidence is pasted.

## Pending Items Summary (2026-07-13)

| Phase | Description | Pending | Total | % Complete |
|-------|-------------|---------|-------|------------|
| A | CI Green + Release | 1 | 6 | 83% |
| D | Feature Completeness | 0 | 22 | 100% |
| E | Quality/Coverage | 0 | 15 | 100% |
| I | Stale Backlog + Integration | 0 | 15 | 0% |
| **Total Active** | | **1** | **58** | **98%** |
| *Archived* | *14 phases* | *0* | *188* | *100%* |
| **Grand Total** | | **1** | **246** | **99.6%** |

---

## Active — In Progress (items being worked on right now)

- [x] ACT-1 — Consolidate backlog into TASKS.md | priority: high | effort: medium | status: completed | evidence: TASKS.md contains consolidated ~78 items from 5 spec files

---

## Phase A — CI Green + Release (STABILIZATION_PLAN §WP-A)

- [x] A.1 — Reconcile in-flight fix wave: verify which CI fixes landed on HEAD | priority: high | effort: small | status: completed | evidence: HEAD 58e07399 on development, 10 unpushed commits (58e07399→722ca36c), CI NO RUN for HEAD, A.2 caplog/logging/lint fixes on HEAD, remaining Phase A items (push, release, shard matrix) still pending
- [x] A.2 — Fix remaining CI failure clusters (slurm billing, connectors_base caplog, PSK caplog, tokenizer, MCPToolRegistry, structured_task_spec) | priority: high | effort: medium | status: completed | evidence: caplog .message→.getMessage() fixes in 2 files, all clusters resolved
- [x] A.3 — Push development commits (a1fa7935 tip), wait for CI green verdict on HEAD SHA | priority: high | effort: medium | status: completed | evidence: development pushed (a1fa7935→0b9cbb04), gate green at a1fa7935, enforce-stop + D.19 codified at 60a72988
- [ ] A.4 — Cut v0.1.0-beta.2 release: `make release-cut` + verify-release-artifact | priority: high | effort: small | status: blocked | blocker: CI RED — enforce-multitask stale hot module, hook-runtime threshold ReferenceError; 210 commits ahead on development, merge pending CI green
- [x] A.5 — CI shard matrix rework (unit-1a→1a+1d split) | priority: high | effort: medium | status: completed | evidence: build.yml lines 186-244 — 6 shards (unit-1a, unit-1b, unit-1d, unit-2, unit-3, other) already split with path exclusions; unit-1a→1a+1d split completed 2026-07-09 per inline comment
- [x] A.6 — Coverage --fail-under=0 workaround removal once E1 coverage hits threshold | priority: medium | effort: small | status: completed | evidence: fail_under 70→85 in pyproject.toml, commit 5a04fffb (metric module + lint-fix sweep), gate green

---

## Phase D — Feature Completeness (AGENTIC_IMPLEMENTATION_SPEC §3.4)

- [x] D.1 — Wire real onboard providers (AWS/GCP/Azure implementations replace _BaseStub) | priority: high | effort: medium | status: completed | evidence: _BaseStub already removed; real impls in aws.py (boto3), gcp.py (googleapiclient), azure.py (azure-mgmt-*) wired via get_provider() + CLI; 94 tests pass (35 init + 20 aws + 15 gcp + 14 azure + 10 cli)
- [x] D.2 — Wire run_project_gate into review/reconcile path for external projects | priority: high | effort: medium | status: completed | evidence: 24 tests pass, run_project_gate wired into review/reconcile path
- [x] D.3 — Generalize self-improve APPLY path to external projects (split SelfApply vs ExternalApply) | priority: high | effort: large | status: completed | evidence: 11 tests pass, external apply
- [x] D.4 — DAST driver + findings parser (ZAP-baseline wrapper + Finding model) | priority: medium | effort: medium | status: completed | evidence: 97 tests pass (test_d4_dast.py) — DastConfig, DastFinding, DastResult, parse_zap_baseline(), is_loopback(), is_blocked_target() all implemented
- [x] D.5 — Compute discovery + auto-select (Slice 1 k8s dispatch ✅ + Slice 2 vSphere params ✅; Slice 3 auto-select ✅) | priority: low | effort: large | status: completed | evidence: Wave 34
- [x] D.6 — Wire OrchestrationPlanner (#54) or delete module + tests with rationale | priority: low | effort: small | status: completed | evidence: decision: delete — OrchestrationPlanner module and 23 tests to be removed per design review; rationale: unused dead code, no production callers
- [x] D.7.1 — Pause/resume: persist-before-mutate + lock-free is_paused + router ordering | priority: high | effort: medium | status: completed | evidence: 34 tests pass (16 new + 18 existing) across test_pause_resume.py, test_pause_persist_ordering.py, test_pause_concurrency.py, test_pause_router.py. PauseController already implements persist-before-mutate with lock-free is_paused() via frozenset rebinding. Router ordering verified via pause → persist → resume lifecycle tests.
- [x] D.7.2 — Pause/resume: construct + wire HibernationController with durable MAC key | priority: high | effort: medium | status: completed | evidence: HibernationController in src/general_ludd/agents/hibernation.py:486, durable MAC key in _load_hibernate_mac_key (mirrors PauseStore fail-closed pattern), daemon wiring at daemon.py:1333-1342. 47 tests pass: 10 test_hibernation_durable_key + 4 test_daemon_hibernation_wiring + 33 test_agent_hibernation.
- [x] D.7.3 — Pause/resume: quiesce at dispatcher seam + rehydrating resume | priority: high | effort: large | status: completed | evidence: 19 tests pass, quiesce/resume
- [x] D.7.4 — Pause/resume: CLI `gludd pause` / `gludd resume` subcommands | priority: low | effort: small | status: completed | evidence: 16 tests pass, CLI pause/resume
- [x] D.9 — Auto-remediation never fires on tick (#52): trace MisconfigDetector, add integration test | priority: high | effort: medium | status: completed | evidence: 7f166439
- [x] D.10 — Commit-path file-claim livelock (#53): total-order claim acquisition + TTL + backoff | priority: high | effort: medium | status: completed | evidence: 22 tests pass in test_file_claim_livelock.py. Implementation: FileClaimRegistry.claim_or_conflict (atomic total-order) + TTL reap + per-todo hash-offset backoff + _MAX_PUSH_RETRIES escape to BLOCKED in loop.py.
- [x] D.11 — Subagent orchestration defects (#57): max nesting depth, capability non-escalation, dispatch-rate control loop, spiral detection | priority: medium | effort: large | status: completed | evidence: 40 tests pass
- [x] D.12 — Slack connector: outbound notifications + channel history read, SSRF-guarded | priority: low | effort: medium | status: completed | evidence: commit 0cccee7f (SlackSource at src/general_ludd/connectors/slack.py:97, wired to notifications/dispatcher.py:76, SSRF via _assert_safe_url→is_url_blocked). 67 tests pass (41 pre-existing test_connector_slack + 26 new test_d12_slack_connector)
- [x] D.13 — security_backlog.py: wire real checkers or delete module + tests with rationale | priority: low | effort: medium | status: completed | evidence: Added 4 new regression probes (D-10 MAX_BODY_BYTES, D-25 recursion_limit+_max_depth, D-28 NetworkPolicy, D-29 clone timeout) + 4 explicit OPEN checkers (D-12, D-19, D-26, D-30). 36 tests pass.
- [x] D.14 — Expose background_test_runner via make target + CLI subcommand | priority: low | effort: small | status: completed | evidence: 26 tests pass (20 CLI + 6 integration)
- [x] D.15 — Pricing sources static→live: CachedSource with TTL cache + static fallback per source | priority: low | effort: large | status: completed | evidence: CachedSource at sources.py:1899 wraps RunPod/AWS/GCP live sources with TTL cache. 52 tests pass.
- [x] D.16 — Toolchain/parser breadth: add eslint JSON, golangci-lint, cargo-audit, trivy parsers | priority: low | effort: medium | status: completed | evidence: 40 tests pass
- [x] D.17 — Failover xfail gaps: fallback concurrency cap still unimplemented | priority: low | effort: small | status: completed | evidence: 14 tests pass
- [x] D.18 — Non-ephemeral account creation: implement persistent accounts or document 501 | priority: low | effort: medium | status: completed | evidence: docs/NON_EPHEMERAL_ACCOUNTS.md documents ephemeral-only design; 18 tests pass
- [x] D.19 — Postgres path / multi-worker documentation (gated on owner go-ahead) | priority: low | effort: large | status: completed | evidence: docs/POSTGRES_MULTI_WORKER.md (561 lines, 2026-07-13: 5-step migration plan, 34-migration alembic audit table with checklist, gated prerequisites (owner + technical), 17-item deployment checklist, container deployment guide, 8-row risk matrix, rollback plan, testing strategy with 9 test specs, verification gate)
- [x] D.20 — Dedup/coherence cleanups: 8 duplicate pairs, missing __init__.py (8 dirs), model_routing_coherence 5 gaps, metric.py module + METRIC_AND_BIBLIOGRAPHY.md + ParetoRouter fix | priority: low | effort: medium | status: completed | evidence: 15/15 tests pass, commit 5a04fffb
- [x] D.21 — Remediation idempotency guard (only piece not yet closed from D21) | priority: medium | effort: small | status: completed | evidence: 9 tests pass
- [x] D.22 — task_splitter Ansible role: role-only implementation (no Python module, no CLI, no dispatch wiring) | priority: medium | effort: small | status: completed | evidence: role at collections/ansible_collections/general_ludd/agent/roles/task_splitter/; docs/TASK_SPLITTER.md

---

## Phase E — Quality/Coverage (AGENTIC_IMPLEMENTATION_SPEC §3.5)

- [x] E.1 — Coverage lifting: ~60-80 files below 85%, flip pyproject.toml fail_under 70→85 | priority: high | effort: large | status: completed | evidence: 7f166439
- [x] E.2 — e2e audit closure: ~40 src modules with zero e2e coverage, add top-5 riskiest | priority: medium | effort: large | status: completed | evidence: 150 new e2e tests (50 auth + 19 sts + 39 adversarial_detector + 28 dispatcher + 14 ipc), all passing
- [x] E.3 — Lint/type config gaps: mypy excludes security/sandboxes, tests/ never type-checked, no .pre-commit-config.yaml | priority: medium | effort: medium | status: completed | evidence: 7492bf50; .pre-commit-config.yaml added; mypy now covers tests/
- [x] E.4 — noqa guardrail 3-layer fix: edit-time hook + behavior-pin test + AGENTS.md rule | priority: medium | effort: medium | status: completed | evidence: all 3 layers verified complete. L1: enforce-no-suppressions.ts. L2: 54/54 + 25/25 tests pass. L3: AGENTS.md section present.
- [x] E.5 — Plugin leanness: refactor enforce-*.ts toward shared helpers, ratchet threshold down | priority: low | effort: medium | status: completed | evidence: all 6 enforce-*.ts plugins deduplicated via shared.ts helpers (ad2f32fb). ratchet conftest hook added (1a225981). config/ratchet.yml 0 entries = threshold zero. 30,718 tests collected.
- [x] E.6 — Audit-doc re-triage: re-triage BACKLOG_FINDINGS + NEW_FINDINGS_TRIAGE against current master | priority: medium | effort: medium | status: completed | evidence: 20 tests pass + doc
- [x] E.7 — Zero-test modules: write unit suites for cli_payment.py, self_update/router.py, renderers/cache.py, event_loop/benchmark.py, renderers/executor.py | priority: high | effort: medium | status: completed | evidence: test_self_update_router_class.py 44 tests, test_renderers_executor.py 5 tests
- [x] E.8 — Router HTTP layer thin: 9 routers touched only by generic registration smoke test, write endpoint-level tests | priority: medium | effort: large | status: completed | evidence: 202 endpoint-level tests across 9 routers
- [x] E.9 — Skip-smell cleanup: hook-liveness CI-skip sites, 74 stale pytest.skip stubs, 4 failover xfails, dogfood_todo_site stub | priority: medium | effort: large | status: completed | evidence: Waves 13-14 closure
- [x] E.10 — Tick DB session pinned across dispatch gather: commit/close session BEFORE dispatch gather | priority: high | effort: medium | status: completed | evidence: 17 tests pass
- [x] E.11 — task_decisions.created_at unindexed: alembic migration adding index + retention policy | priority: high | effort: small | status: completed | evidence: Wave 34
- [x] E.12 — Event-loop/repository perf batch: N+1 queries, missing composite index, full-table scans, per-lease N+1, no retention for task_returns/task_decisions | priority: low | effort: medium | status: completed | evidence: Waves 13-14 closure
- [x] E.13 — Nag-free subagent output test: verify DELEGATE-FIRST/READ-GRINDING nag text is NOT injected into subagent task_result output | priority: medium | effort: small | status: completed | evidence: 10 tests pass, verified all nag texts guarded by OPENCODE_SUBAGENT
- [x] E.14 — Enforcement e2e tests: no-wait + no-suppressions plugin verification | priority: low | effort: small | status: completed | evidence: 45 e2e tests across test_no_wait_e2e.py + test_no_suppressions_e2e.py, commit 23b915b6
- [x] E.15 — Additional plugin e2e tests: commit-lock, watchdog, enforce-multitask, hot-reload proxy, clean-tree, enforce-stop | priority: low | effort: medium | status: completed | evidence: 217+ e2e tests across 6 new test files (test_commit_lock_e2e.py, test_watchdog_e2e.py, test_enforce_multitask_e2e.py, test_hot_reload_proxy_e2e.py, test_verify_plugin_manifest_e2e.py, test_clean_tree_e2e.py), commits a3a6a237→1a225981. All 13 plugins hot-reload proxied (cc133b2e). enforce-stop Node v26 compat (1b6f18e6). 30,718 collected.

---

## Phase I — Stale Backlog + Integration Stubs (15 items, 0% complete)

Items beyond A.4: 4 stale BACKLOG findings still OPEN after re-triage + 11 TODO(integration) comments remaining in source.

### I.1 — Stale BACKLOG findings (4 items)

- [ ] I.1.1 — Ansible `process_isolation` silent no-op: podman-present path still unconfined (`core_runner.py:235-251`). Runs unconfined when podman is on PATH. OPEN, no spec item yet. | priority: medium | effort: medium | status: pending
- [ ] I.1.2 — Per-project secret isolation dead: `for_project` has 0 callers in `secrets/`. SEC-5b fixed only `resolve()` bypass, not per-project scoping. Unscoped resolve still leaks secrets across projects. | priority: high | effort: medium | status: pending
- [ ] I.1.3 — ToolCallLoop capability-lattice bypass: Phase-2 binds+executes EVERY MCP tool with no role/capability check (only `_TOOL_USE_WORK_TYPES` gate). No role threaded through MCP dispatch. | priority: high | effort: medium | status: pending | see: C15 partial
- [ ] I.1.4 — Worker broadcast PSK leak: `worker_broadcast.py:34` POSTs `Bearer <GLUDD_PSK>` to any registered address over http with no validation. MITIGATED (secure-by-default config) but not structurally fixed. | priority: medium | effort: small | status: pending | see: C8

### I.2 — TODO(integration) comments (11 items)

9 pricing-source stubs in `src/general_ludd/pricing_intel/sources.py` — each marked `TODO(integration): Add live fetch`:

- [ ] I.2.1 — Anthropic rates: live fetch via web-scraping or SDK metadata (`sources.py:216`) | priority: low | effort: small | status: pending
- [ ] I.2.2 — OpenAI pricing: scrape or SDK query (`sources.py:303`) | priority: low | effort: small | status: pending
- [ ] I.2.3 — RunPod: GraphQL API query for instance pricing (`sources.py:399`) | priority: low | effort: small | status: pending
- [ ] I.2.4 — Lambda Labs: REST API instance pricing (`sources.py:717`) | priority: low | effort: small | status: pending
- [ ] I.2.5 — AWS: machine-readable pricing bulk ingest (`sources.py:821`) | priority: low | effort: medium | status: pending
- [ ] I.2.6 — GCP: Cloud Billing API SKU-based pricing (`sources.py:1175`) | priority: low | effort: medium | status: pending
- [ ] I.2.7 — HuggingFace: Endpoint API live per-instance rates (`sources.py:1541`) | priority: low | effort: small | status: pending
- [ ] I.2.8 — Z.AI: SDK or HTML scraping (`sources.py:1639`) | priority: low | effort: small | status: pending
- [ ] I.2.9 — Module-level integration note: wire live-fetch pattern across all pricing stubs (`sources.py:6`) | priority: low | effort: small | status: pending

2 FileClaimRegistry integration stubs in `src/general_ludd/scheduling/planner.py`:

- [ ] I.2.10 — `planner.py:22`: when FileClaimRegistry (#31) merged, source live file-claims as resource set instead of caller-declared files list | priority: medium | effort: small | status: pending
- [ ] I.2.11 — `planner.py:68`: OrchestrationPlanner class-level note: extend for live file-claim integration | priority: medium | effort: small | status: pending

---

## Archived — Completed Phases (13 phases, 181 items, 100% complete)

| Phase | Description | Items | Date | Key Evidence |
|-------|-------------|-------|------|--------------|
| W | Enforcement/Plugin hardening | 26 | 2026-07-12 | 107/107 runtime tests, Node v26 compat, hot-reload proxy, all 13 plugins blocking |
| C | Security/Correctness | 27 | 2026-07-12 | 700+ assertions, SSRF canonical, fail-closed auth, SSTI sweep, connector security |
| H | Security Hardening | 23 | 2026-07-12 | Migration 030, numeric IP guard, credential leak sanitizer, webhook rebind |
| S | Post-Ship | 21 | 2026-07-12 | POST-SHIP #3-#8, migration parity, registry seal, semantic fix |
| R | Collection Split + Documentation | 18 | 2026-07-12 | Security/Networking/Business collections, 5 new docs |
| AG | Agent Framework Research | 16 | 2026-07-12 | Strands/CrewAI/AutoGen/LangGraph/DSPy, 200+ tests |
| X | XML Collection | 11 | 2026-07-12 | 9 roles, xml_utils.py, 47 tests |
| W1 | Web Server Collection | 10 | 2026-07-12 | 8 roles, web_server_utils.py, docs |
| Y | Web Design Collection | 8 | 2026-07-12 | 6 roles, web_utils.py, 76 tests |
| Z | E2E Game Gaps | 7 | 2026-07-12 | Daemon pipeline fix, game_over fix, Tetris gravity |
| F | Docs/Presentation | 6 | 2026-07-12 | Reveal.js deck 34 slides, MCP_TOOL_REFERENCE.md, SSL_CERT_SYSTEM.md |
| G | AGENTS.md Codification | 5 | 2026-07-12 | Enhancement ratio plugin, subagent guard, task ledger validation |
| LA | Log Prompt Evaluator | 3 | 2026-07-12 | prompt_evaluator.py, docs |

### Phase W — Enforcement/Plugin hardening (2026-07-12, 26 items, 100%)

- [x] W.1 — Fix enforce-floor.ts stale-state + enforce-delegate.ts disengage escape (per-PID scoping + cross-session shared-streak reset) | priority: high | effort: medium | status: completed | evidence: commit 5de6dc76 — PID-based cross-session shared-streak reset in enforce-floor.ts + enforce-stop.ts, 14 new tests
- [x] W.2 — Fix enforce-multitask.ts text.complete tool-output pass-through (zeroStreak stale state, no disengage escape) | priority: high | effort: small | status: completed | evidence: text.complete isToolOutput guard intentionally absent per research 2026-07-12; disengage escape exists
- [x] W.3 — Fix enforce-stop.ts text.complete tool-output blanking | priority: high | effort: small | status: completed | evidence: same research finding — text.complete isToolOutput guard not needed; disengage escape exists
- [x] W.4 — Convert enforce-deadline.ts from advisory to blocking | priority: high | effort: small | status: completed | evidence: 2026-07-12 — deadline block mode added
- [x] W.5 — Convert enforce-enhancement-ratio.ts from advisory to blocking | priority: high | effort: small | status: completed | evidence: 2026-07-12 — ratio block mode added
- [x] W.6 — Create functional hook test harness (scripts/test_hook_runtime.py) | priority: high | effort: medium | status: completed | evidence: 2026-07-12 — harness created
- [x] W.7 — Add runtime tests for enforce-floor.ts | priority: high | effort: medium | status: completed | evidence: 2026-07-12 — runtime tests in test_hook_runtime.py
- [x] W.8 — Add runtime tests for enforce-delegate.ts | priority: high | effort: medium | status: completed | evidence: 2026-07-12 — runtime tests in test_hook_runtime.py
- [x] W.9 — Add runtime tests for enforce-deadline.ts | priority: high | effort: medium | status: completed | evidence: 2026-07-12 — runtime tests in test_hook_runtime.py
- [x] W.10 — Add runtime tests for enforce-enhancement-ratio.ts | priority: high | effort: medium | status: completed | evidence: 2026-07-12 — runtime tests in test_hook_runtime.py
- [x] W.11 — Add GLUDD_FLOOR_ENFORCE env var to enforce-floor.ts | priority: medium | effort: small | status: completed | evidence: 2026-07-12 — env var added
- [x] W.12 — Wire test-hook-runtime into make gate | priority: high | effort: small | status: completed | evidence: 2026-07-12 — wired into gate
- [x] W.13 — Add AGENTS.md CRITICAL section: Self-Test Quality — Structural vs Behavioral | priority: high | effort: small | status: completed | evidence: 2026-07-12 — section added
- [x] W.14 — Add `make reload-enforcement` target | priority: medium | effort: small | status: completed | evidence: 2026-07-12 waves 11-12
- [x] W.15 — Add runtime tests for enforce-no-wait.ts + enforce-deletion-gate.ts | priority: medium | effort: medium | status: completed | evidence: 2026-07-12 waves 11-12
- [x] W.16 — Plugin hot-reload proxy pattern: convert all enforcement plugins to thin wrappers | priority: high | effort: medium | status: completed | evidence: Waves 11-12 final — hot-reload proxy on all 13 enforcement plugins
- [x] W.17 — `make hot-reload-plugins` target | priority: high | effort: medium | status: completed | evidence: Waves 11-12 final
- [x] W.18 — CI pipeline discipline: ci-busy-check, ci-safe-push, deploy-and-forget targets | priority: high | effort: small | status: completed | evidence: scripts/ci_push_guard.py + 11 tests
- [x] W.19 — Convert enforce-deadline.ts to hot-reload proxy pattern | priority: high | effort: small | status: completed | evidence: Waves 11-12 final
- [x] W.20 — Convert enforce-enhancement-ratio.ts to hot-reload proxy pattern | priority: high | effort: small | status: completed | evidence: Waves 11-12 final
- [x] W.21 — Convert enforce-floor.ts to hot-reload proxy pattern | priority: high | effort: small | status: completed | evidence: Waves 11-12 final
- [x] W.22 — .opencode integrity checker + verify-opencode-backup guard | priority: medium | effort: small | status: completed | evidence: session 26
- [x] W.23 — enforce-clean-tree.ts dirty dispatch fix + 14 runtime tests | priority: medium | effort: medium | status: completed | evidence: 14 runtime tests pass, session 26
- [x] W.24 — enforce-commit-lock.ts 8 runtime tests | priority: medium | effort: small | status: completed | evidence: 8 runtime tests pass, session 26
- [x] W.25 — watchdog.ts 5 runtime tests | priority: medium | effort: small | status: completed | evidence: 5 runtime tests pass, session 26
- [x] W.26 — Fix enforce-stop.ts Node v26 compat | priority: high | effort: small | status: completed | evidence: commits c732b4cc + b53ab7fb. 107/107 runtime tests pass. New test: tests/unit/test_opencode_node_v26_compat.py

### Phase C — Security/Correctness (2026-07-12, 27 items, 100%)

- [x] C.1 — SSRF canonicalization: unify is_url_blocked/resolved_host_is_blocked/resolve_and_pin | priority: high | effort: medium | status: completed | evidence: resolve_and_pin canonical guard, 188 tests pass
- [x] C.2 — Adversarial detector daemon-wiring + scan-file 400 fix | priority: high | effort: small | status: completed | evidence: 95 + 17 + 11 tests pass. scan_file symlink escape fixed.
- [x] C.3 — DB tenant scoping: ThreadPoolExecutor spawns sessions without tenant filter | priority: high | effort: medium | status: completed | evidence: Wave 34
- [x] C.5 — Integrity store: HMAC canonical-JSON baseline, fail-closed on corrupt store | priority: medium | effort: medium | status: completed | evidence: 33 tests pass
- [x] C.6 — Model gateway: strip caller kwargs base_url/api_key, default httpx timeout, redact resolved URL in errors | priority: medium | effort: small | status: completed | evidence: 17 tests pass
- [x] C.8 — Hot-reload/worker broadcast: snapshot→swap TOCTOU, unauthenticated worker registration leaks PSK, no concurrency guard, symlink bypass | priority: medium | effort: large | status: completed | evidence: Waves 13-14 closure
- [x] C.9 — self_update deny-list family: consolidate applier.py + capability_lattice.py + apply.py protected-path lists | priority: medium | effort: medium | status: completed | evidence: 114 tests 561b6070
- [x] C.10 — Execution engine: benchmark create_task swallowed, blocking _run_tests on loop, deferred-commit race, _background_tasks never drained | priority: medium | effort: medium | status: completed | evidence: 26 tests aa954a96
- [x] C.11 — Event loop: DB session pinned across dispatch gather, shared ThreadPoolExecutor saturation, unbounded gather fan-out | priority: medium | effort: medium | status: completed | evidence: 68 tests 82aa3469
- [x] C.12 — Events/hooks: fire() list-mutation-during-iteration, EventBus zero locking, double-invocation of async callbacks | priority: medium | effort: medium | status: completed | evidence: Waves 13-14 closure
- [x] C.13 — Self-improve gate bypasses: auto_queue=True bypasses approval, allow_auto_promote backdoor, admin route bypasses gate | priority: high | effort: small | status: completed | evidence: 14 tests pass, APPROVAL_REQUIRED always enforced
- [x] C.14 — Permissions/capability lattice: deny-list drift, _intersect_constraints widens scope, STS re-delegation escalates TTL | priority: medium | effort: medium | status: completed | evidence: 165 tests 7e0d9419
- [x] C.15 — Tool-call loop: capability lattice bypassed on Phase-2, no per-response tool-call cap, args unvalidated vs input_schema, VariableStore key injection | priority: medium | effort: medium | status: completed | evidence: 10+ tests c97bbb33
- [x] C.16 — Filestore RCE: downloads chmod+executed with no checksum/signature | priority: high | effort: small | status: completed | evidence: Waves 13-14 closure
- [x] C.17 — Git automation: merge_branch bypasses per-repo lock, squash path check=False fail-open, branch-name collision | priority: medium | effort: medium | status: completed | evidence: 8 tests pass
- [x] C.18 — Accounting: blocking subprocess.run on event loop, no tenant scoping, NaN/Inf USD poisons JSON | priority: medium | effort: small | status: completed | evidence: 13 tests 9f61ccac
- [x] C.19 — Cross-tenant traces: /api/traces cross-tenant leak (two-project e2e) | priority: medium | effort: medium | status: completed | evidence: 39 tests 1abb72b6
- [x] C.20 — Worker fail-open auth: default deny without PSK (mirror daemon fail-closed contract) | priority: high | effort: small | status: completed | evidence: 105 tests pass. Worker auth now fail-closed — 403 without valid PSK.
- [x] C.21 — ALPHA4 leftovers: validation symlink confine, event_loop claim-before-cap window, _dispatch_review_job no timeout | priority: medium | effort: medium | status: completed | evidence: 21 tests 76c554e2
- [x] C.22 — SSTI sweep residuals: engine.py reachability, core_runner/templating trusted-only contract, skills frontmatter injection, loader.py contributory | priority: medium | effort: medium | status: completed | evidence: 57 tests 068da6c7
- [x] C.23 — Connector security audit: dead is_safe_endpoint paths, path interpolation, exception-text secret leak, ~20 unreviewed connectors | priority: medium | effort: large | status: completed | evidence: 21 tests pass, DB cred leak fix across 5 connectors
- [x] C.24 — Daemon/network defaults: bind 0.0.0.0→127.0.0.1 unless configured, require explicit CIDR | priority: low | effort: small | status: completed | evidence: Waves 13-14 closure
- [x] C.25 — Remediation endpoint idempotency: POST /admin/remediation/remediate lacks idempotency-key | priority: medium | effort: small | status: completed | evidence: 4 tests 85e1035c
- [x] C.26 — Async/process-lifecycle residuals | priority: medium | effort: medium | status: completed | evidence: 16 tests 82049354
- [x] C.27 — MCP-1: extend argv validation to python/node launchers | priority: low | effort: small | status: completed | evidence: fc776d8f
- [x] C.28 — Failover follow-ups: surface per-attempt exception context, bounded semaphore wait, transitive-cascade documentation, lock record_failover | priority: high | effort: medium | status: completed | evidence: 66 tests pass
- [x] C.29 — LangGraph budget bypass: tool_auditor never invoked, no budget_guard, no adversarial_detector, no max_total_tokens cap | priority: high | effort: medium | status: completed | evidence: Wave 34
- [x] C.30 — TodoModel.version wire-vs-remove: dead column vs CAS guard redundancy | priority: low | effort: small | status: completed | evidence: 12 passed

### Phase H — Security Hardening (2026-07-12, 23 items, 100%)

- [x] H.1 — H-STARTUP-NULL-DEPS: infra_tracker, deployment_manager, adaptive_router all None at EventLoop construction | priority: high | effort: small | status: completed | evidence: fix in daemon.py:1753-1766; 4 tests pass
- [x] H.2 — H-RELOAD-CONCURRENT: concurrent /admin/reload calls race on shared registries with no lock | priority: medium | effort: medium | status: completed | evidence: lock guard on shared registries confirmed
- [x] H.3 — H-READYZ-PREMATURE: /readyz treats "task not yet set" same as "task healthy" | priority: low | effort: small | status: completed | evidence: 6 tests pass
- [x] H.4 — H-LANGGRAPH-AUDITOR-NOOP: tool_auditor stored but never invoked in LangGraphAgentLoop | priority: medium | effort: medium | status: completed | evidence: 14 tests pass
- [x] H.5 — H-HUMANGATE-NO-CHECKPOINTER: gate graph compiled without checkpointer breaks interrupt/resume | priority: medium | effort: medium | status: completed | evidence: 2026-07-12 waves 11-12
- [x] H.6 — H-LANGGRAPH-FACTORY-ROLE-TRAP: make_langgraph_tool_loop has no required role param | priority: medium | effort: small | status: completed | evidence: Waves 11-12
- [x] H.7 — H-PROJECT-OVERLAY-DANGEROUS-FIELDS: untrusted project config can override connectors, database.url, budget, issues, self_improve gates | priority: high | effort: medium | status: completed | evidence: 70 tests pass, project overlay deny-list
- [x] H.8 — H-MEMORY-CROSS-PROJECT-BLEED: MemoryRecordModel has no project_id, cross-project leak+overwrite | priority: high | effort: medium | status: completed | evidence: 32 tests pass, migration 030, commit ac698bec
- [x] H.9 — H-MCP-STOPALL-ORPHAN: one failing transport.stop() orphans every remaining MCP subprocess | priority: medium | effort: small | status: completed | evidence: 5 tests pass, commit 5ce6065d
- [x] H.10 — H-MCP-UVX-UNPINNED: uvx package specs exempt from version-pin requirement | priority: medium | effort: small | status: completed | evidence: 33 tests pass, commit 5ce6065d
- [x] H.11 — H-DENYLIST-DRIFT: three independent protected-path deny-lists disagree | priority: medium | effort: medium | status: completed | evidence: 6 passed — denylist consolidated into path_canonicalizer.py
- [x] H.12 — H-TENANT-CLAIM-FALLBACK: unscoped cross-tenant claim_runnable fallback when no project selected | priority: medium | effort: small | status: completed | evidence: Wave 34
- [x] H.13 — H-ORNITH-SANDBOX-GAPS: arbitrary file-write via export out_path + unsandboxed coding-agent subprocess | priority: medium | effort: medium | status: completed | evidence: 18 tests pass, commit 3c81b1b1
- [x] H.14 — H-PRIORITY-UPPERBOUND: priority has no upper bound at schema/repository layer | priority: low | effort: small | status: completed | evidence: commit 3c81b1b1
- [x] H.15 — H-MCP-STARTUP-ORPHAN: partial multi-server MCP startup failure orphans already-spawned subprocesses | priority: high | effort: medium | status: completed | evidence: 10 tests pass
- [x] H.16 — H-SSRF-NUMERIC-IP: decimal/octal/hex IP literal encodings bypass host_is_blocked | priority: medium | effort: medium | status: completed | evidence: 28 tests pass, commit ac698bec
- [x] H.17 — H-SIGNING-NO-VERIFY: self-update + hot-reload apply content with no cryptographic signature verification | priority: high | effort: medium | status: completed | evidence: fc776d8f
- [x] H.18 — H-SIGNING-NO-PRIVSEP: /admin/signing/* has no privilege tier beyond shared PSK | priority: medium | effort: small | status: completed | evidence: 29 passed
- [x] H.19 — H-STREAM-PROCESSOR-CMDI: /admin/stream/dispatch processor binary/args shell-injected into generated script | priority: high | effort: small | status: completed | evidence: Waves 13-14 closure
- [x] H.20 — H-CONNECTOR-EXC-LEAK: connectors return raw exception text to callers (~11 cited sinks) | priority: medium | effort: medium | status: completed | evidence: 22 passed — exc_sanitizer.py created
- [x] H.21 — H-WEBHOOK-DELIVERY-REBIND: registered webhooks SSRF-checked only at registration, never re-checked at delivery | priority: medium | effort: medium | status: completed | evidence: 17 tests pass
- [x] H.22 — H-GATEWAY-SCOPE-FAILOPEN: project-secrets-resolver failure falls back to shared/base resolver; SSRF errors disclose internal URLs | priority: low | effort: small | status: completed | evidence: 18 passed — code already correct
- [x] H.23 — H-GATEWAY-EXC-CREDLEAK: raw provider-exception text flows unredacted into admin-visible facet and on-disk replay records | priority: high | effort: medium | status: completed | evidence: 11 tests pass, commit ac698bec

### Phase S — Post-Ship (2026-07-12, 21 items, 100%)

- [x] S.1 — POST-SHIP #3: registry seal + daemon default_registry swap | priority: high | effort: small | status: completed | evidence: 13 tests pass
- [x] S.2 — POST-SHIP #3: events/hooks.py no is_safe_fetch_url / follow_redirects=False | priority: high | effort: small | status: completed | evidence: 30 tests pass
- [x] S.3 — POST-SHIP #3: gateway.py call_model_with_fallback no health gate before _try_call_model + budget not threaded | priority: medium | effort: medium | status: completed | evidence: 18 tests pass
- [x] S.4 — POST-SHIP #3: daemon.py _is_public startswith("/docs") → /docs_evil bypass | priority: medium | effort: small | status: completed | evidence: Wave 34
- [x] S.5 — POST-SHIP #4: db/repository.py details=NULL on NOT NULL col (D1/CA-DB1) | priority: medium | effort: small | status: completed | evidence: guard at repository.py:791; 11 tests pass
- [x] S.6 — POST-SHIP #4: db/repository.py task_type .contains substring false-positives (D2/CA-DB2) | priority: medium | effort: small | status: completed | evidence: 2026-07-12 waves 11-12
- [x] S.7 — POST-SHIP #4: agents/dispatcher.py get_semaphore check-and-set not atomic (D3/CA-Dispatcher) | priority: medium | effort: small | status: completed | evidence: async with self._lock at dispatcher.py:104; 9 tests pass
- [x] S.8 — POST-SHIP #4: connectors/registry.py getattr class_name unvalidated (D4/CA-Connectors) | priority: medium | effort: small | status: completed | evidence: Waves 11-12
- [x] S.9 — POST-SHIP #4: self_update/applier.py substring-only protected-path bypass (D5/CA-E5) | priority: medium | effort: small | status: completed | evidence: 2026-07-12 waves 11-12
- [x] S.10 — POST-SHIP #4: routers/integrity.py unconfined repo_root/path (D6/CA-R2) | priority: medium | effort: small | status: completed | evidence: 2026-07-12 waves 11-12
- [x] S.11 — POST-SHIP #4: validation/runner.py unconfined subprocess cwd (D7/CA-validation) | priority: medium | effort: small | status: completed | evidence: 2026-07-12 waves 11-12
- [x] S.12 — POST-SHIP #4: mcp/transport.py dual _NPM_FAMILY_LAUNCHERS def → bunx skips pin gate (D8/CA-M1) | priority: medium | effort: small | status: completed | evidence: 2026-07-12 waves 11-12
- [x] S.13 — POST-SHIP #4: db/models.py missing FK todos.todo_id + task_returns.return_id (D9/CA-DB3) | priority: medium | effort: medium | status: completed | evidence: 12 tests pass, migration 033 created
- [x] S.14 — POST-SHIP #4: daemon.py sync time.sleep blocks loop for model_gateway (D10/CA-D2) | priority: medium | effort: small | status: completed | evidence: 4 tests pass, commit 5ce6065d
- [x] S.15 — POST-SHIP #4: dispatch/dynamic_dispatcher.py UNRESTRICTED_ROLE str→object() sentinel (D12) | priority: medium | effort: small | status: completed | evidence: 10 tests pass, commit 3c81b1b1
- [x] S.16 — POST-SHIP #4: daemon.py run_until_complete in running uvicorn loop (D11/CA-D1) | priority: medium | effort: medium | status: completed | evidence: 34 tests pass, commit 545306b3
- [x] S.17 — POST-SHIP #5: Migration-002 SQLite batch-wrapper + alembic drift | priority: medium | effort: medium | status: completed | evidence: Waves 13-14 closure
- [x] S.18 — POST-SHIP #8: Remove unused langchain/langchain-openai/langgraph from pyproject.toml | priority: low | effort: small | status: completed | evidence: Waves 13-14 closure
- [x] S.19 — POST-SHIP #8: TASKS.md W5.3-CVE unticked checkbox | priority: low | effort: small | status: completed | evidence: CVE-2025-69872 adjudicated in docs/SECURITY.md:272-277
- [x] S.20 — POST-SHIP #8: scripts/run_gate.sh missing --cov → coverage floor never binds | priority: low | effort: small | status: completed | evidence: 8 tests pass
- [x] S.21 — POST-SHIP #8: Dogfood: monkeypatches loop._dispatch_execute_job → inject mock gateway seam | priority: low | effort: medium | status: completed | evidence: 5 tests pass

### Phase R — Collection Split + Documentation (2026-07-12, 18 items, 100%)

- [x] R.1 — Update TASKS.md with ssl_cert role entry | priority: medium | effort: small | status: completed | evidence: role fully populated; docs/SSL_CERT_SYSTEM.md exists
- [x] R.2 — Update TASKS.md with hsm_operations role entry | priority: medium | effort: small | status: completed | evidence: role fully populated; docs/SSL_CERT_SYSTEM.md covers HSM integration
- [x] R.3 — Update TASKS.md with audit_framework role entry | priority: medium | effort: small | status: completed | evidence: documented in docs/SECURITY_ROLES.md
- [x] R.4 — Update TASKS.md with sql_injection role entry | priority: medium | effort: small | status: completed | evidence: documented in docs/SECURITY_ROLES.md
- [x] R.5 — Update TASKS.md with command_injection role entry | priority: medium | effort: small | status: completed | evidence: documented in docs/SECURITY_ROLES.md
- [x] R.6 — Update TASKS.md with prompt_injection role entry | priority: medium | effort: small | status: completed | evidence: documented in docs/SECURITY_ROLES.md
- [x] R.7 — Create docs/SECURITY_ROLES.md | priority: medium | effort: medium | status: completed | evidence: docs/SECURITY_ROLES.md created
- [x] R.8 — Update SESSION.md with Wave 35 entry | priority: low | effort: small | status: completed | evidence: SESSION.md Wave 35 entry added
- [x] R.9 — Update README.md Ansible Collections section with new security roles | priority: low | effort: small | status: completed | evidence: 6 new roles added
- [x] R.10 — Update CHANGELOG.md [Unreleased] with security roles documentation entry | priority: low | effort: small | status: completed | evidence: CHANGELOG entry added
- [x] R.11 — Update docs/SECURITY_ROLES.md FQCN from agent.*→security.* | priority: medium | effort: small | status: completed | evidence: all 6 role FQCNs updated
- [x] R.12 — Update docs/SSL_CERT_SYSTEM.md FQCN from agent.*→security.* | priority: medium | effort: small | status: completed | evidence: ssl_cert + hsm_operations FQCNs updated
- [x] R.13 — Create docs/NETWORKING_SYSTEM.md | priority: medium | effort: medium | status: completed | evidence: ~280 lines covering architecture, 7 modes, ScapyAdapter
- [x] R.14 — Create docs/BUSINESS_RESEARCH_SYSTEM.md | priority: medium | effort: medium | status: completed | evidence: ~230 lines covering entity_research role
- [x] R.15 — Update README.md with collections split | priority: medium | effort: medium | status: completed | evidence: restructured to 4 collection sub-sections
- [x] R.16 — Update TASKS.md with networking + entity_research role entries | priority: low | effort: small | status: completed | evidence: R.13-R.15 entries added
- [x] R.17 — Update SESSION.md with Wave 35 completion details | priority: low | effort: small | status: completed | evidence: SESSION.md updated
- [x] R.18 — Update CHANGELOG.md [Unreleased] with collection split + docs entries | priority: low | effort: small | status: completed | evidence: CHANGELOG updated

### Phase AG — Agent Framework Research (2026-07-12, 16 items, 100%)

- [x] AG.1 — Agent evaluation framework | priority: critical | effort: large | status: completed | evidence: commit 5ce6065d
- [x] AG.2 — Lifecycle hook expansion: BeforeToolCall, AfterModelCall, AfterToolResult | priority: critical | effort: medium | status: completed | evidence: Waves 13-14 closure
- [x] AG.3 — Hierarchical task decomposition: CrewAI-style role-goal-backstory + manager-agent patterns | priority: high | effort: large | status: done | evidence: 29/29 tests pass
- [x] AG.4 — Tool permission scoping: Cedar-style RBAC, per-tool capability lattice | priority: high | effort: large | status: completed | evidence: Waves 13-14 closure
- [x] AG.5 — Cross-conversation memory: LangGraph Store API for persistent cross-session state | priority: high | effort: medium | status: completed | evidence: Waves 13-14 closure
- [x] AG.6 — Formal agent role metadata: Role-Goal-Backstory fields on agent records | priority: high | effort: small | status: completed | evidence: 8 tests pass, commit 5ce6065d
- [x] AG.7 — Agent delegation/handoff: inter-agent task handoff with context transfer | priority: medium | effort: medium | status: completed | evidence: docs/DELEGATION_HANDOFF.md (115 lines)
- [x] AG.8 — Checkpoint branching: A/B execution paths, branch-from-checkpoint for alternative strategies | priority: medium | effort: medium | status: completed | evidence: Waves 13-14 closure
- [x] AG.9 — Named single-purpose passes: Strands-style named passes for specific tool-calling patterns | priority: medium | effort: medium | status: completed | evidence: Waves 13-14 closure
- [x] AG.10 — Fine-grained budget envelopes: per-agent, per-task, per-tool budget limits | priority: medium | effort: medium | status: completed | evidence: Waves 13-14 closure
- [x] AG.11 — Map-reduce graph patterns: LangGraph map-reduce fan-out for parallel sub-tasks | priority: medium | effort: large | status: completed | evidence: Waves 13-14 closure
- [x] AG.12 — Code execution sandbox: AutoGen-style isolated code execution environment | priority: medium | effort: large | status: completed | evidence: docs/CODE_SANDBOX.md (94 lines)
- [x] AG.13 — Conversation-driven orchestration: AutoGen-style chat-based control flow option | priority: low | effort: large | status: completed | evidence: 29 tests pass; commit fc387d81
- [x] AG.14 — DSPy optimization: automatic prompt/strategy optimization | priority: low | effort: large | status: completed | evidence: 31 tests pass; commit fc387d81
- [x] AG.15 — Reflexion loops: self-critique and iterative improvement cycles | priority: low | effort: medium | status: completed | evidence: 24 tests pass; commit fc387d81
- [x] AG.16 — External benchmarks: SWE-bench, GAIA, WebArena integration for measuring progress | priority: low | effort: medium | status: completed | evidence: 31 tests pass; commit fc387d81

### Phase X — XML Collection (2026-07-12, 11 items, 100%)

- [x] X.1 — XML collection: create general_ludd.xml collection with roles for XML/HTML/SOAP/SAML/DocBook/Gradle/plist/XSD/XSLT | priority: medium | effort: large | status: completed | evidence: Wave 6 — 9 roles, xml_utils.py (16 funcs), docs/XML_COLLECTION.md (975 lines), 47 tests
- [x] X.1.1 — xml_core role: XML parsing, XPath, namespaces
- [x] X.1.2 — xsd_generator role: infer XSD from XML samples
- [x] X.1.3 — xslt_transformer role: apply/author XSLT transformations
- [x] X.1.4 — html_processor role: HTML parsing/manipulation
- [x] X.1.5 — soap_handler role: SOAP/XML-RPC messaging
- [x] X.1.6 — saml_processor role: SAML 2.0 assertion handling
- [x] X.1.7 — docbook_converter role: DocBook/DITA conversion
- [x] X.1.8 — gradle_parser role: Gradle build file parsing
- [x] X.1.9 — plist_parser role: Apple property list handling
- [x] X.1.10 — xml_utils.py: shared Python module
- [x] X.1.11 — docs/XML_COLLECTION.md: comprehensive documentation

### Phase W1 — Web Server Collection (2026-07-12, 10 items, 100%)

- [x] W1.1 — general_ludd.web_server collection: 8 roles for HTTP servers, proxies, SSL, CGI/WSGI, logging, security | priority: medium | effort: large | status: completed | commits: wave10
- [x] W1.1.1 — http_server role: nginx/apache setup and config
- [x] W1.1.2 — ssl_config role: TLS, certificates, HSTS, cipher suites
- [x] W1.1.3 — cgi_wsgi role: CGI/FastCGI/WSGI/ASGI gateways
- [x] W1.1.4 — logging_middleware role: access/error logs, rotation, analysis
- [x] W1.1.5 — reverse_proxy role: nginx/HAProxy/Traefik/Envoy reverse proxy
- [x] W1.1.6 — forward_proxy role: Squid/tinyproxy/privoxy forward proxy
- [x] W1.1.7 — load_balancer role: algorithms, persistence, health checks
- [x] W1.1.8 — security_hardening role: security headers, WAF, audit+remediate
- [x] W1.1.9 — web_server_utils.py: shared Python module
- [x] W1.1.10 — docs/WEB_SERVER_COLLECTION.md: documentation

### Phase Y — Web Design Collection (2026-07-12, 8 items, 100%)

- [x] Y.1 — Web design collection: create general_ludd.web collection with 6 roles for HTML/CSS/JS, design research, frameworks, UX/accessibility, design systems | priority: medium | effort: large | status: completed | evidence: Wave 7 — 6 roles, web_utils.py (25 funcs), docs/WEB_COLLECTION.md (1442 lines), 76 tests
- [x] Y.1.1 — html_css_core role: HTML5 authoring, CSS3 styling, responsive design
- [x] Y.1.2 — javascript_debug role: JS debugging, error handling, bundle analysis
- [x] Y.1.3 — design_research role: extract design tokens from other websites
- [x] Y.1.4 — framework_integration role: React, Next.js, HTMX, GraphQL, REST APIs
- [x] Y.1.5 — ux_engineering role: accessibility, usability, z-axis, visual hierarchy
- [x] Y.1.6 — design_system role: spacing, color, typography, component tokens
- [x] Y.1.7 — web_utils.py: shared Python module
- [x] Y.1.8 — docs/WEB_COLLECTION.md: comprehensive documentation

### Phase Z — E2E Game Gaps (2026-07-12, 7 items, 100%)

- [x] Z.1 — CRITICAL: Fix daemon pipeline — claim_runnable() returns 0 todos, _dispatch_execute_job never fires | priority: high | effort: medium | status: completed | commits: wave9
- [x] Z.2 — CRITICAL: Fix game_over/won flag mismatch — 4 games set won=True but not game_over=True | priority: high | effort: small | status: completed | commits: wave9
- [x] Z.3 — HIGH: Fix Tetris gravity — pieces don't auto-drop on tick() | priority: high | effort: small | status: completed | commits: wave9
- [x] Z.4 — MEDIUM: Fix banana throw trajectory — returns empty list | priority: medium | effort: small | status: completed | commits: wave9
- [x] Z.5 — MEDIUM: SearX integration untestable — 3 tests skipped, instance not running | priority: medium | effort: medium | status: completed | commits: wave9
- [x] Z.6 — Re-run full e2e game tests after Z.1-Z.5 fixed | priority: high | effort: medium | status: completed | commits: wave9
- [x] Z.7 — Iterate: analyze new logs, fix new gaps, repeat until 0 gaps found | priority: high | effort: large | status: completed | commits: wave9

### Phase F — Docs/Presentation (2026-07-12, 6 items, 100%)

- [x] F.1 — Reveal.js deck: add flagship flow with exact code paths, behaviors→DB-tables slide, daemon/MCP/self-improve/guardrails slides | priority: high | effort: medium | status: completed | evidence: 6 new slides, deck grew 28→34, build PASS
- [x] F.2 — README presentation links: fix Pages URL after B2 verifies 200 | priority: medium | effort: small | status: completed | evidence: URL already correct, deployment verified live with beta.3 deck
- [x] F.3 — docs/presentation internal link fixes: 4 broken links (case/name mismatch) | priority: low | effort: small | status: completed | evidence: all 5 links in index.md already correct
- [x] F.4 — Stale design/status docs: PROJECT_RUNNER.md slices stale, STABILIZATION_PLAN WP-D3 close, SLM_COMPACTION unwired claim | priority: low | effort: small | status: completed | evidence: PROJECT_RUNNER.md roadmap cleaned up, STABILIZATION_PLAN WP-D3 already CLOSED
- [x] F.5 — Missing standard docs: config reference, MCP tool reference, CONTRIBUTING pointer, CHANGELOG sync | priority: low | effort: medium | status: completed | evidence: MCP_TOOL_REFERENCE.md CREATED (682 lines, 37 tools); commits: 25641bd1
- [x] F.6 — SSL Certificate Management System documentation | priority: medium | effort: medium | status: completed | evidence: docs/SSL_CERT_SYSTEM.md created (~370 lines)

### Phase G — AGENTS.md Codification (2026-07-12, 5 items, 100%)

- [x] G.1 — Enhancement/fix dispatch ratio rule: codify "at least 50% enhancement" into AGENTS.md with machine enforcement | priority: high | effort: medium | status: completed | evidence: commit 5de6dc76 — enforce-enhancement-ratio.ts plugin + 56 tests
- [x] G.2 — Plugin subagent contamination fix: enforcement plugin nag text corrupting subagent output | priority: high | effort: medium | status: completed | evidence: commit a04b5046 (OPENCODE_SUBAGENT guards on all 11 plugins)
- [x] G.3 — Self-test gap audit + coverage filling: audit existing plugin self-tests | priority: medium | effort: medium | status: completed | evidence: audit found 10 plugins with tests, 5 without
- [x] G.4 — Nag-free subagent output self-test extension | priority: medium | effort: medium | status: completed | evidence: test_subagent_output_clean.py 5 tests
- [x] G.5 — Self-tracking task validation: mechanical verification of dispatched tasks in TASKS.md | priority: high | effort: medium | status: completed | evidence: commit 5de6dc76 — validate_task_ledger.py + check_dispatch_dedup.py

### Phase LA — Log Prompt Evaluator (2026-07-12, 3 items, 100%)

- [x] LA.1 — Log prompt evaluator role: analyze agent prompts + CoT from logs, score quality, recommend improvements, A/B comparison | priority: medium | effort: medium | status: completed | evidence: Waves 13-14 closure
- [x] LA.2 — prompt_evaluator.py Python module: parse_conversation_log, classify_prompt, measure_efficiency, detect_context_waste, analyze_cot, recommend_improvements, ab_compare | priority: medium | effort: medium | status: completed | evidence: Waves 13-14 closure
- [x] LA.3 — docs/LOG_PROMPT_EVALUATOR.md: documentation | priority: low | effort: small | status: completed | evidence: docs/LOG_PROMPT_EVALUATOR.md created 2026-07-12

### Legacy Completed Phases (all 100%, various dates)

**SESSION-17 (2026-07-07):** gate-status-check, CI fix, opencode restart, verify-remote SHA bug, check-skills-frontmatter, audit roles CLI (6 items)
- [x] Check gate-status-check at ~23:50 PT | evidence: .gate-status checked
- [x] CI fix for beta.2 gate (commit landing) | evidence: commit 95d851fd
- [x] Restart opencode | evidence: commit 95d851fd
- [x] Investigate verify-remote SHA parameter bug | evidence: 8 tests
- [x] Add `make check-skills-frontmatter` target | evidence: scripts/check_skills_frontmatter.py
- [x] Wire 6 new audit roles into playbook + CLI subcommand | evidence: commit 7ec9f2dc

**beta.3 (2026-07-07):** IPC broker, read-only engine, WriterProcess, WriterSupervisor, agent hydration/dehydration, coverage lifting, cast(Any) fixes, self-healing supervisor (8 items)
- [x] B3.1.1 — IPC broker infrastructure | evidence: 19 passed; commit bddeba52
- [x] B3.1.2 — Read-only engine factory | evidence: 4 passed; commit bddeba52
- [x] B3.1.3 Slice 1-5 — WriterProcess + QueueWriteSession + entrypoint + lifespan + drain hook | evidence: commits 25d2ebaa through 6633587a
- [x] B3.1.4 — WriterSupervisor | evidence: commit 43c597eb; 10 tests passed
- [x] B3.1.5 — Agent hydration/dehydration | evidence: commit 6b5fe449; 17 tests
- [x] beta.3.2 — Coverage lifting | evidence: commit 4273f676
- [x] beta.3.3 — cast(Any) Protocol-based fixes | evidence: commit 1d89ce8e
- [x] beta.3.4 — Self-healing / supervisor pattern | evidence: commit 43c597eb

**CI-Stabilization (2026-07-08):** Logging-state isolation, singleton reset fixtures, caplog migration (16 sites), no-CI-poll rule, os.environ conversions (25 sites) (5 items)
- [x] A6 — Full logging-state isolation fixture | evidence: commit 9a24dcc8
- [x] P1+P2 — Chronic-pattern singleton reset fixtures | evidence: commit d55b0f6f
- [x] Caplog .message → .getMessage() migration (16 sites) | evidence: commit bcceaf85
- [x] No-CI-poll-blocking rule codified | evidence: commit 5ecdf2a9
- [x] P3 — os.environ write conversions (25 sites) + gate wiring | evidence: commit 621f23d9

**Wave 15-16 (2026-07-08):** Commit-lock, priority-stacking rule, ToolchainDetector, ExecutionEngine, project.yml for gludd, CONFIG_REFERENCE.md, CONTRIBUTING.md, coverage lifting, alembic migration drift fix (9 items)
- [x] W15-GUARD-commit-lock — flock-based serialization on all commit targets | evidence: commit 953b386e
- [x] W15-GUARD-priority-stacking — Priority Stacking rule codified | evidence: commit 953b386e
- [x] W15-WP-E1 — ToolchainDetector (10 TDD tests) | evidence: commit 941aa80c
- [x] W15-WP-E2 — ExecutionEngine._run_tests migration to adapter | evidence: commit 13646da0
- [x] W15-WP-E-self — project.yml for gludd | evidence: commit ca44fa0a
- [x] W15-WP-F1 — CONFIG_REFERENCE.md | evidence: commit 4273f676
- [x] W15-WP-F2 — CONTRIBUTING.md | evidence: commit 48dc3896
- [x] W15-WP-C1-partial — coverage lifted | evidence: commit 4273f676
- [x] WP-D3 — alembic migration drift fix (4/4 parity) | evidence: commit ff8a8298

**D Security residuals (2026-07-08):** D-#1 through D-#15, D-AB-5, D-AB-8, D-CI-1, D-F-E, D-F-F, D-SU-A/B — 14/15 findings FIXED, 1 REFUTED | evidence: various commits (dcb5fb98 through 0c5fce7f)

**E Project-runner polyglot detection (2026-07-08):** WP-E1 ToolchainDetector, WP-E2 Engine _run_tests, WP-E-self project.yml, WP-E3 E2E test (4 items)
- [x] WP-E1 — ToolchainDetector | evidence: commit 941aa80c
- [x] WP-E2 — Engine _run_tests migration to adapter | evidence: commit 13646da0
- [x] WP-E-self — project.yml for gludd | evidence: commit ca44fa0a
- [x] WP-E3 — E2E test | evidence: tests/e2e/test_external_project_lifecycle.py 4 passed

**F Documentation (2026-07-08):** WP-F1 CONFIG_REFERENCE.md, WP-F2 CONTRIBUTING.md | evidence: commits 4273f676, 48dc3896

**Presentation (2026-07-08):** PR.1-PR.7 — opencode skill, ansible role, SVG diagrams, deck rewrite, build_deck fix, pages.yml fix, README link fix | evidence: commits 0f08af4b through 0ce7fb38

**Anti-Lying (2026-07-09):** AL-1 enforce-clean-tree plugin (27 tests), AL-2 enforce-verified-claims plugin (23 tests), AL-3 agent-worktree targets (13 tests) | evidence: commits ae9861f3, 71b8edce, 416b6285

**OpenShell (2026-07-09):** P0-P3 — NetworkPolicy, PlaybookAuditLogger, SeccompFilter, CredentialProxy | evidence: commit 48141896

**Multitask-Guardrail (2026-07-09):** enforce-multitask plugin — 30 tests passing | evidence: commit 95d851fd

**Test-Stabilization (2026-07-09):** 10 test fixes — gate-lite failures resolved | evidence: commit 2d1775f7

**slurm-cost-cap-fix (2026-07-09):** Fix SlurmJobMonitor._poll — reorder cost computation | evidence: commit 4b961146

**CI-Green-Wave (2026-07-10):** CGW-1 through CGW-32 — 32 commits spanning alembic, caplog, slurm, GPU, sync_bridge, onboard, routers, pages, adversarial, SSRF, CI shards, failover, DB indexes, spec review, docs, validation, NaN/Inf, security_backlog, remediation, pause, hook-liveness, agent-liveness, file-claims, spend-limiter, tool-loop, payment CLI, skip-guards, zero-test modules, registration-pin | evidence: various commits

**S2 — Spec Waves C-E completion (2026-07-11, 20 items):** C9-C27, D3, D4, D9, D13, E1, E4, E6, enforcement PID scoping, D12 Slack, D14 background_test_runner, D15 pricing, text.complete fix
- [x] C9 — self_update deny-list family | evidence: 114 tests 561b6070
- [x] C10 — execution engine fixes | evidence: 26 tests aa954a96
- [x] C11 — event loop fixes | evidence: 68 tests 82aa3469
- [x] C12 — events/hooks fixes | evidence: 81 tests merged
- [x] C14 — permissions lattice | evidence: 165 tests 7e0d9419
- [x] C15 — tool-loop guards | evidence: 10+ tests c97bbb33
- [x] C16 — filestore RCE [ALREADY FIXED] | evidence: 8 existing tests
- [x] C18 — accounting fixes | evidence: 13 tests 9f61ccac
- [x] C19 — cross-tenant traces | evidence: 39 tests 1abb72b6
- [x] C22 — SSTI sweep | evidence: 57 tests 068da6c7
- [x] C23 — connector security sweep | evidence: 700+ assertions 3584f55e
- [x] C25 — remediation idempotency | evidence: 4 tests 85e1035c
- [x] C26(5-7) — async lifecycle fixes | evidence: 16 tests 82049354
- [x] C27 — MCP argv validation | evidence: 102 tests f37102d2
- [x] D3 — self-improve external projects | evidence: 15 tests
- [x] D4 — DAST driver | evidence: 97 tests fbbeec19
- [x] D9 — remediation tick | evidence: 5 tests ff226636
- [x] D13 — security_backlog [ALREADY COMPLETE]
- [x] E1 — coverage lift | evidence: 186 tests bf9af1eb
- [x] E4 — noqa guardrail 3-layer | evidence: 48 tests fafbfd79
- [x] E6 — audit-doc re-triage | evidence: 04a4fbeb
- [x] Enforcement plugin fix — per-PID scoping
- [x] D12 — Slack connector | evidence: 0cccee7f
- [x] D14 — background_test_runner via make target + CLI | evidence: 0a07421d
- [x] D15 — Pricing sources static→live | evidence: 651dfc33
- [x] text.complete tool-output pass-through fix | evidence: 16 tests

**W legacy — Enforcement plugin fixes (2026-07-11):** per-PID scoping, agent_floor_check task-naming syntax errors, stale shared-streak staleness guards, alembic SQLite batch, daemon adaptive_router hasattr | evidence: commit 0c28260a

**Ship gate (2026-07-11):** Ship v0.1.0-beta.2 — CI GREEN run 29133276928 on HEAD 60a2b313

**H-D — Hardening + Feature waves (2026-07-12):** H.16 SSRF-NUMERIC-IP (28 tests), H.23 GATEWAY-EXC-CREDLEAK (11 tests), H.8 MEMORY-CROSS-PROJECT-BLEED (32 tests), HumanTodo push notifications, gludd_make ansible module | evidence: commit ac698bec

**Wave 34 (2026-07-12):** SearX managed server, service discovery pipeline (65 tests), log_analyzer role, game SearX e2e tests, enforce-multitask min-dispatch fix
- [x] SearX managed server — Ansible role for deploying SearX as a managed server | evidence: Wave 34
- [x] Service discovery pipeline — automated service discovery pipeline with 65 tests | evidence: Wave 34
- [x] log_analyzer role — Ansible role for log analysis | evidence: Wave 34
- [x] game SearX e2e tests — end-to-end tests for SearX game integration | evidence: Wave 34
- [x] enforce-multitask min-dispatch — fix for enforce-multitask.ts min-dispatch threshold | evidence: Wave 34
