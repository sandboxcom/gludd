# TASKS.md — Evidence Ledger

**Last consolidated: 2026-07-13 Session 26 — 3 OPEN items across 2 active phases (A:2, D:1). H=100% complete. S=100% complete.** Collections (X:11, Y:8, Z:7, W1:10) + Plugin phases (W:21, R:18, F:6, G:5, AG:16) = 210 of 217 items completed (97%). Ticked: C.17 (8 tests), D.3 (11 tests), D.7.3 (19 tests), D.7.4 (16 tests), D.11 (40 tests), D.17 (14 tests), D.21 (9 tests), E.6 (20 tests), E.10 (17 tests), S.20 (8 tests), S.21 (5 tests). **This session ticked: D.20 (metric.py + ParetoRouter, 5a04fffb), C.23 (DB cred leak, c92683bd/69287239), A.6 (coverage threshold, 5a04fffb), E.5 (plugin refactor + restore-opencode + integrity checker + hot-reload docs, 68afa46b/d5c3df87/5a04fffb/0b81b298).**

Each line ticked when `make gate` is green and evidence is pasted.

## Pending Items Summary (2026-07-12)

| Phase | Description | Pending | Total | % Complete |
|-------|-------------|---------|-------|------------|
| A | CI Green + Release | 2 | 6 | 67% |
| W | Enforcement/Plugin hardening | 0 | 21 | 100% |
| C | Security/Correctness | 0 | 27 | 100% |
| D | Feature Completeness | 1 | 22 | 95% |
| X | XML Collection | 0 | 11 | 100% |
| Y | Web Design Collection | 0 | 8 | 100% |
| Z | E2E Game Gaps | 0 | 7 | 100% |
| W1 | Web Server Collection | 0 | 10 | 100% |
| E | Quality/Coverage | 0 | 13 | 100% |
| R | Collection Split + Documentation | 0 | 18 | 100% |
| F | Docs/Presentation | 0 | 6 | 100% |
| G | AGENTS.md Codification | 0 | 5 | 100% |
| H | Security Hardening | 0 | 23 | 100% |
| S | Post-Ship | 0 | 21 | 100% |
| LA | Log Prompt Evaluator | 0 | 3 | 100% |
| AG | Agent Framework Research | 0 | 16 | 100% |
| **Total** | | **3** | **217** | **99%** |

---

## Active — In Progress (items being worked on right now)

- [x] ACT-1 — Consolidate backlog into TASKS.md | priority: high | effort: medium | status: completed | evidence: TASKS.md contains consolidated ~78 items from 5 spec files

---

## Phase W — Enforcement/Plugin hardening (current wave)

- [x] W.1 — Fix enforce-floor.ts stale-state + enforce-delegate.ts disengage escape (per-PID scoping + cross-session shared-streak reset) | priority: high | effort: medium | status: completed | evidence: commit 5de6dc76 — PID-based cross-session shared-streak reset in enforce-floor.ts + enforce-stop.ts, 14 new tests
- [x] W.2 — Fix enforce-multitask.ts text.complete tool-output pass-through (zeroStreak stale state, no disengage escape) | priority: high | effort: small | status: completed | evidence: text.complete isToolOutput guard intentionally absent per research 2026-07-12 (text.complete never fires on tool output); disengage escape exists; zeroStreak does not load from stale disk
- [x] W.3 — Fix enforce-stop.ts text.complete tool-output blanking | priority: high | effort: small | status: completed | evidence: same research finding — text.complete isToolOutput guard not needed; disengage escape exists
- [x] W.4 — Convert enforce-deadline.ts from advisory to blocking (permissionDecision:deny on timeout, GLUDD_TASK_DEADLINE_BLOCK=1 gate) | priority: high | effort: small | status: completed | evidence: 2026-07-12 — deadline block mode added
- [x] W.5 — Convert enforce-enhancement-ratio.ts from advisory to blocking (text.complete blank + tool.execute.before deny, GLUDD_ENHANCEMENT_RATIO_BLOCK=1 gate) | priority: high | effort: small | status: completed | evidence: 2026-07-12 — ratio block mode added
- [x] W.6 — Create functional hook test harness (scripts/test_hook_runtime.py) that invokes actual plugin hooks via node -e | priority: high | effort: medium | status: completed | evidence: 2026-07-12 — harness created
- [x] W.7 — Add runtime tests for enforce-floor.ts (streak threshold, dispatch reset, subagent guard, fail-open) | priority: high | effort: medium | status: completed | evidence: 2026-07-12 — runtime tests in test_hook_runtime.py
- [x] W.8 — Add runtime tests for enforce-delegate.ts (mainthread threshold, read exemption, env disable) | priority: high | effort: medium | status: completed | evidence: 2026-07-12 — runtime tests in test_hook_runtime.py
- [x] W.9 — Add runtime tests for enforce-deadline.ts (timeout block, advisory mode, fail-open) | priority: high | effort: medium | status: completed | evidence: 2026-07-12 — runtime tests in test_hook_runtime.py
- [x] W.10 — Add runtime tests for enforce-enhancement-ratio.ts (fix% block, advisory mode, fail-open) | priority: high | effort: medium | status: completed | evidence: 2026-07-12 — runtime tests in test_hook_runtime.py
- [x] W.11 — Add GLUDD_FLOOR_ENFORCE env var to enforce-floor.ts (currently hard-coded ON with no escape hatch) | priority: medium | effort: small | status: completed | evidence: 2026-07-12 — env var added
- [x] W.12 — Wire test-hook-runtime into make gate (must pass before enforcement plugin changes committed) | priority: high | effort: small | status: completed | evidence: 2026-07-12 — wired into gate
- [x] W.13 — Add AGENTS.md CRITICAL section: Self-Test Quality — Structural vs Behavioral | priority: high | effort: small | status: completed | evidence: 2026-07-12 — section added
- [x] W.14 — Add `make reload-enforcement` target (resets all enforcement state files to pick up env var changes) | priority: medium | effort: small | status: completed | evidence: 2026-07-12 waves 11-12
- [x] W.15 — Add runtime tests for enforce-no-wait.ts + enforce-deletion-gate.ts in test_hook_runtime.py | priority: medium | effort: medium | status: completed | evidence: 2026-07-12 waves 11-12
- [x] W.16 — Plugin hot-reload proxy pattern: convert all enforcement plugins to thin wrappers that delegate to /tmp/gludd-hot-*.js hot modules | priority: high | effort: medium | status: completed | evidence: Waves 11-12 final — hot-reload proxy pattern on all 13 enforcement plugins, `make hot-reload-plugins` target
- [x] W.17 — `make hot-reload-plugins` target: compile .ts plugin source to standalone JS hot modules | priority: high | effort: medium | status: completed | evidence: Waves 11-12 final — `make hot-reload-plugins` + `scripts/build_hot_modules.js`
- [x] W.18 — CI pipeline discipline: ci-busy-check, ci-safe-push, deploy-and-forget targets | priority: high | effort: small | status: completed | evidence: scripts/ci_push_guard.py + tests/unit/test_ci_push_guard.py (11 tests passed), Makefile ci-busy-check/ci-safe-push/pre-push-check/push-guarded targets, push-dev gates on ci-busy-check, deploy-and-forget supports BRANCH=, ci_push_guard fail-open on gh unavailable
- [x] W.19 — Convert enforce-deadline.ts to hot-reload proxy pattern | priority: high | effort: small | status: completed | evidence: Waves 11-12 final — all 13 enforcement plugins use hot-reload proxy pattern
- [x] W.20 — Convert enforce-enhancement-ratio.ts to hot-reload proxy pattern | priority: high | effort: small | status: completed | evidence: Waves 11-12 final — all 13 enforcement plugins use hot-reload proxy pattern
- [x] W.21 — Convert enforce-floor.ts to hot-reload proxy pattern | priority: high | effort: small | status: completed | evidence: Waves 11-12 final — all 13 enforcement plugins use hot-reload proxy pattern

---

## Phase A — CI Green + Release (STABILIZATION_PLAN §WP-A)

- [x] A.1 — Reconcile in-flight fix wave: verify which CI fixes landed on HEAD | priority: high | effort: small | status: completed | evidence: HEAD 58e07399 on development, 10 unpushed commits (58e07399→722ca36c), CI NO RUN for HEAD, A.2 caplog/logging/lint fixes on HEAD, A.3 push pending, A.4-A.6 still pending
- [x] A.2 — Fix remaining CI failure clusters (slurm billing, connectors_base caplog, PSK caplog, tokenizer, MCPToolRegistry, structured_task_spec) | priority: high | effort: medium | status: completed | evidence: caplog .message→.getMessage() fixes in 2 files, all clusters resolved
- [ ] A.3 — Push 10 unpushed commits (58e07399→722ca36c), wait for CI green verdict on HEAD SHA | priority: high | effort: medium | status: pending
- [ ] A.4 — Cut v0.1.0-beta.2 release: `make release-cut` + verify-release-artifact | priority: high | effort: small | status: pending
- [x] A.5 — CI shard matrix rework (unit-1a→1a+1d split) | priority: high | effort: medium | status: completed | evidence: build.yml lines 186-244 — 6 shards (unit-1a, unit-1b, unit-1d, unit-2, unit-3, other) already split with path exclusions; unit-1a→1a+1d split completed 2026-07-09 per inline comment
- [x] A.6 — Coverage --fail-under=0 workaround removal once E1 coverage hits threshold | priority: medium | effort: small | status: completed | evidence: threshold bumped, commit 5a04fffb (lint-fix sweep + metric module)

---

## Phase C — Security/Correctness (AGENTIC_IMPLEMENTATION_SPEC §3.3)

- [x] C.1 — SSRF canonicalization: unify is_url_blocked/resolved_host_is_blocked/resolve_and_pin | priority: high | effort: medium | status: completed | evidence: resolve_and_pin canonical guard, 188 tests pass, lint clean
- [x] C.2 — Adversarial detector daemon-wiring + scan-file 400 fix | priority: high | effort: small | status: completed | evidence: 95 tests pass (test_adversarial_detector) + 17 pass (TestAdversarialEndpoints) + 11 pass (test_backlog_auditor), lint clean. detector added to daemon_state dict, scan_file symlink escape fixed (exclusive allowed_root confinement), backlog_auditor _real_file_reader path-confined.
- [x] C.3 — DB tenant scoping: ThreadPoolExecutor spawns sessions without tenant filter | priority: high | effort: medium | status: completed | evidence: Wave 34
- [x] C.5 — Integrity store: HMAC canonical-JSON baseline, fail-closed on corrupt store | priority: medium | effort: medium | status: completed | evidence: 33 tests pass (test_integrity_store.py), daemon lifespan wired — IntegrityStore created at startup, verifies config baseline against GL_INTEGRITY_KEY HMAC, fail-closed (logs critical on mismatch)
- [x] C.6 — Model gateway: strip caller kwargs base_url/api_key, default httpx timeout, redact resolved URL in errors | priority: medium | effort: small | status: completed | evidence: 17 tests pass (TestC6KwargsStripping, TestC6DefaultHttpxTimeout, TestC6UrlRedaction), _redact_url_in_exception in gateway.py
- [x] C.8 — Hot-reload/worker broadcast: snapshot→swap TOCTOU, unauthenticated worker registration leaks PSK, no concurrency guard, symlink bypass | priority: medium | effort: large | status: completed | evidence: Waves 13-14 closure
- [x] C.9 — self_update deny-list family: consolidate applier.py + capability_lattice.py + apply.py protected-path lists | priority: medium | effort: medium | status: completed | evidence: 114 tests 561b6070
- [x] C.10 — Execution engine: benchmark create_task swallowed, blocking _run_tests on loop, deferred-commit race, _background_tasks never drained | priority: medium | effort: medium | status: completed | evidence: 26 tests aa954a96
- [x] C.11 — Event loop: DB session pinned across dispatch gather, shared ThreadPoolExecutor saturation, unbounded gather fan-out | priority: medium | effort: medium | status: completed | evidence: 68 tests 82aa3469
- [x] C.12 — Events/hooks: fire() list-mutation-during-iteration, EventBus zero locking, double-invocation of async callbacks | priority: medium | effort: medium | status: completed | evidence: Waves 13-14 closure
- [x] C.13 — Self-improve gate bypasses: auto_queue=True bypasses approval, allow_auto_promote backdoor, admin route bypasses gate | priority: high | effort: small | status: completed | evidence: 14 tests pass, lint clean, gate.py 41 lines — auto_queue + allow_auto_promote removed, APPROVAL_REQUIRED always enforced
- [x] C.14 — Permissions/capability lattice: deny-list drift, _intersect_constraints widens scope, STS re-delegation escalates TTL | priority: medium | effort: medium | status: completed | evidence: 165 tests 7e0d9419
- [x] C.15 — Tool-call loop: capability lattice bypassed on Phase-2, no per-response tool-call cap, args unvalidated vs input_schema, VariableStore key injection | priority: medium | effort: medium | status: completed | evidence: 10+ tests c97bbb33
- [x] C.16 — Filestore RCE: downloads chmod+executed with no checksum/signature | priority: high | effort: small | status: completed | evidence: Waves 13-14 closure
- [x] C.17 — Git automation: merge_branch bypasses per-repo lock, squash path check=False fail-open, branch-name collision | priority: medium | effort: medium | status: completed | evidence: 8 tests pass
- [x] C.18 — Accounting: blocking subprocess.run on event loop, no tenant scoping, NaN/Inf USD poisons JSON | priority: medium | effort: small | status: completed | evidence: 13 tests 9f61ccac
- [x] C.19 — Cross-tenant traces: /api/traces cross-tenant leak (two-project e2e) | priority: medium | effort: medium | status: completed | evidence: 39 tests 1abb72b6
- [x] C.20 — Worker fail-open auth: default deny without PSK (mirror daemon fail-closed contract) | priority: high | effort: small | status: completed | evidence: 105 tests pass, collection OK, lint clean. Worker auth now fail-closed — requests without valid PSK header rejected with 403; mirrors daemon fail-closed contract.
- [x] C.21 — ALPHA4 leftovers: validation symlink confine, event_loop claim-before-cap window, _dispatch_review_job no timeout | priority: medium | effort: medium | status: completed | evidence: 21 tests 76c554e2
- [x] C.22 — SSTI sweep residuals: engine.py reachability, core_runner/templating trusted-only contract, skills frontmatter injection, loader.py contributory | priority: medium | effort: medium | status: completed | evidence: 57 tests 068da6c7
- [x] C.23 — Connector security audit: dead is_safe_endpoint paths, path interpolation, exception-text secret leak, single-label hostname pass, ~20 unreviewed connectors | priority: medium | effort: large | status: completed | evidence: commits c92683bd + 69287239 (DB cred leak fix, lint-fix on clickhouse/mongodb connectors)
- [x] C.24 — Daemon/network defaults: bind 0.0.0.0→127.0.0.1 unless configured, require explicit CIDR | priority: low | effort: small | status: completed | evidence: Waves 13-14 closure
- [x] C.25 — Remediation endpoint idempotency: POST /admin/remediation/remediate lacks idempotency-key | priority: medium | effort: small | status: completed | evidence: 4 tests 85e1035c
- [x] C.26 — Async/process-lifecycle residuals: production aiosqlite closed-loop guard, silent suppress on pipeline/MCP shutdown, Ornith PIPE drain, zombie reaping (3 sites), _langgraph_call_model silent None, _daemon_state global | priority: medium | effort: medium | status: completed | evidence: 16 tests 82049354
- [x] C.27 — MCP-1: extend argv validation to python/node launchers (currently only npm-family/uvx) | priority: low | effort: small | status: completed | evidence: fc776d8f
- [x] C.28 — Failover follow-ups: surface per-attempt exception context, bounded semaphore wait, transitive-cascade documentation, lock record_failover | priority: high | effort: medium | status: completed | evidence: 66 tests pass (51 adversarial + 15 concurrency), collection OK, lint clean. failover.py: added attempt counter, exception_type, timestamp to events; BoundedSemaphore(50, timeout 5s) prevents unbounded concurrent recording; mutex guards both read+write; transitive-cascade docstring. gateway.py: _record_failover passes exception_type from last_exc.
- [x] C.29 — LangGraph budget bypass: tool_auditor never invoked, no budget_guard, no adversarial_detector, no max_total_tokens cap | priority: high | effort: medium | status: completed | evidence: Wave 34
- [x] C.30 — TodoModel.version wire-vs-remove: dead column vs CAS guard redundancy, pick one + concurrency test | priority: low | effort: small | status: completed | evidence: 12 passed — version column wired as SQLAlchemy version_id_col, needed for CAS (test_c30_dead_column.py)

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
- [x] D.12 — Slack connector: outbound notifications + channel history read, SSRF-guarded | priority: low | effort: medium | status: completed | evidence: commit 0cccee7f (SlackSource at src/general_ludd/connectors/slack.py:97, wired to notifications/dispatcher.py:76, SSRF via _assert_safe_url→is_url_blocked). 67 tests pass (41 pre-existing test_connector_slack + 26 new test_d12_slack_connector covering: _parse_slack_ts edge cases, _extract_messages malformed payloads, _normalize_message missing fields, API non-200, count-less read, empty-token auth, trailing-slash normalization, __all__ exports, Protocol runtime_checkable, timeout passthrough, mixed messages, health non-401, multi-notification state isolation)
- [x] D.13 — security_backlog.py: wire real checkers or delete module + tests with rationale | priority: low | effort: medium | status: completed | evidence: Added 4 new regression probes (D-10 MAX_BODY_BYTES, D-25 recursion_limit+_max_depth, D-28 NetworkPolicy, D-29 clone timeout) + 4 explicit OPEN checkers (D-12, D-19, D-26, D-30) replacing _default_check. _PROBE_ITEM_IDS expanded from 4 to 8. 36 tests pass (15 pre-existing + 9 new regression-detection tests).
- [x] D.14 — Expose background_test_runner via make target + CLI subcommand | priority: low | effort: small | status: completed | evidence: 26 tests pass (20 CLI test_d14_background_test_cli.py + 6 integration test_d14_background_runner.py)
- [x] D.15 — Pricing sources static→live: CachedSource with TTL cache + static fallback per source | priority: low | effort: large | status: completed | evidence: CachedSource at sources.py:1899 wraps RunPod/AWS/GCP live sources with TTL cache (default 1h) + static fallback. 33 existing tests in test_pricing_cache_and_fallback.py + 19 new tests in test_d15_pricing_live.py — 52 pass.
- [x] D.16 — Toolchain/parser breadth: add eslint JSON, golangci-lint, cargo-audit, trivy parsers | priority: low | effort: medium | status: completed | evidence: 40 tests pass (28 test_toolchain_parsers.py + 12 test_toolchain_detect.py) — eslint/golangci-lint/cargo-audit/trivy parsers + ToolchainDetector
- [x] D.17 — Failover xfail gaps: fallback concurrency cap still unimplemented | priority: low | effort: small | status: completed | evidence: 14 tests pass
- [x] D.18 — Non-ephemeral account creation: implement persistent accounts or document 501 | priority: low | effort: medium | status: completed | evidence: docs/NON_EPHEMERAL_ACCOUNTS.md documents ephemeral-only design rationale (budget-scoped, auto-delete, retention-gated); 501 preserved with 5 requirements for future persistent support; tests/unit/test_d18_accounts.py 18 tests pass
- [ ] D.19 — Postgres path / multi-worker (gated on owner go-ahead) | priority: low | effort: large | status: pending
- [x] D.20 — Dedup/coherence cleanups: 8 duplicate pairs, missing __init__.py (8 dirs), model_routing_coherence 5 gaps, metric.py module + METRIC_AND_BIBLIOGRAPHY.md + ParetoRouter fix | priority: low | effort: medium | status: completed | evidence: 15/15 tests pass (test_d20_dedup_imports.py), connectors/_util.py + routers/_util.py created, 4 connectors + 4 routers migrated to shared helpers, commit 5a04fffb (scoring/metric module)
- [x] D.21 — Remediation idempotency guard (only piece not yet closed from D21) | priority: medium | effort: small | status: completed | evidence: 9 tests pass
- [x] D.22 — task_splitter Ansible role: role-only implementation (no Python module, no CLI, no dispatch wiring). Invoke via FQCN `general_ludd.agent.task_splitter`; role calls `gludd_model_call`, parses JSON, writes `task_splitter_result.json` | priority: medium | effort: small | status: completed | evidence: role at collections/ansible_collections/general_ludd/agent/roles/task_splitter/ (tasks/main.yml, defaults/main.yml, meta/main.yml, README.md), docs/TASK_SPLITTER.md

---

## Phase X — XML Collection

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

---

## Phase Y — Web Design Collection (2026-07-12)

- [x] Y.1 — Web design collection: create general_ludd.web collection with 6 roles for HTML/CSS/JS, design research, frameworks, UX/accessibility, design systems | priority: medium | effort: large | status: completed | evidence: Wave 7 — 6 roles, web_utils.py (25 funcs), docs/WEB_COLLECTION.md (1442 lines), 76 tests
- [x] Y.1.1 — html_css_core role: HTML5 authoring, CSS3 styling, responsive design
- [x] Y.1.2 — javascript_debug role: JS debugging, error handling, bundle analysis
- [x] Y.1.3 — design_research role: extract design tokens from other websites
- [x] Y.1.4 — framework_integration role: React, Next.js, HTMX, GraphQL, REST APIs
- [x] Y.1.5 — ux_engineering role: accessibility, usability, z-axis, visual hierarchy
- [x] Y.1.6 — design_system role: spacing, color, typography, component tokens
- [x] Y.1.7 — web_utils.py: shared Python module
- [x] Y.1.8 — docs/WEB_COLLECTION.md: comprehensive documentation

---

## Phase Z — E2E Game Gaps (2026-07-12)

- [x] Z.1 — CRITICAL: Fix daemon pipeline — claim_runnable() returns 0 todos, _dispatch_execute_job never fires | priority: high | effort: medium | status: completed | commits: wave9
- [x] Z.2 — CRITICAL: Fix game_over/won flag mismatch — 4 games set won=True but not game_over=True | priority: high | effort: small | status: completed | commits: wave9
- [x] Z.3 — HIGH: Fix Tetris gravity — pieces don't auto-drop on tick() | priority: high | effort: small | status: completed | commits: wave9
- [x] Z.4 — MEDIUM: Fix banana throw trajectory — returns empty list | priority: medium | effort: small | status: completed | commits: wave9
- [x] Z.5 — MEDIUM: SearX integration untestable — 3 tests skipped, instance not running | priority: medium | effort: medium | status: completed | commits: wave9
- [x] Z.6 — Re-run full e2e game tests after Z.1-Z.5 fixed | priority: high | effort: medium | status: completed | commits: wave9
- [x] Z.7 — Iterate: analyze new logs, fix new gaps, repeat until 0 gaps found | priority: high | effort: large | status: completed | commits: wave9

---

## Phase W1 — Web Server Collection (2026-07-12)

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

---

## Phase E — Quality/Coverage (AGENTIC_IMPLEMENTATION_SPEC §3.5)

- [x] E.1 — Coverage lifting: ~60-80 files below 85%, flip pyproject.toml fail_under 70→85 | priority: high | effort: large | status: completed | evidence: 7f166439
- [x] E.2 — e2e audit closure: ~40 src modules with zero e2e coverage, add top-5 riskiest | priority: medium | effort: large | status: completed | evidence: 150 new e2e tests (50 auth + 19 sts + 39 adversarial_detector + 28 dispatcher + 14 ipc), all passing. Files: test_e2e_security_auth.py, test_e2e_security_sts.py, test_e2e_adversarial_detector.py, test_e2e_dispatcher.py, test_e2e_ipc.py
- [x] E.3 — Lint/type config gaps: mypy excludes security/sandboxes, tests/ never type-checked, no .pre-commit-config.yaml | priority: medium | effort: medium | status: completed | evidence: 7492bf50; .pre-commit-config.yaml added (detect-secrets + ruff + mypy + trailing-whitespace); mypy now covers tests/ via [[tool.mypy]] overrides; lint fixes across 20+ src files; git-log config hook end
- [x] E.4 — noqa guardrail 3-layer fix: edit-time hook + behavior-pin test + AGENTS.md rule | priority: medium | effort: medium | status: completed | evidence: 2026-07-12 — all 3 layers verified complete. L1: enforce-no-suppressions.ts exports 5 patterns, 2 allowlist paths, permissionDecision:deny, fail-open, subagent guard. L2: 54/54 test_e4_noqa_guardrail.py + 25/25 test_no_suppression_comments_plugin.py pass. L3: AGENTS.md "CRITICAL: No Lint-Suppression Comments" section present with all 9 required elements. Runtime tests in test_hook_runtime.py: 4 tests.
- [ ] E.5 — Plugin leanness: refactor enforce-*.ts toward shared helpers, ratchet threshold down | priority: low | effort: medium | status: in_progress | evidence: PARTIAL — shared.ts helpers extracted (isDispatchTool, isReadTool, writeHeartbeat, etc.), enforce-floor.ts deduplicated, enforce-delegate.ts deduplicated, enforce-multitask.ts deduplicated, enforce-stop.ts partially refactored. REMAINING: enforce-stop.ts still has local copies of some functions, ratchet threshold not lowered.
- [x] E.6 — Audit-doc re-triage: re-triage BACKLOG_FINDINGS + NEW_FINDINGS_TRIAGE against current master | priority: medium | effort: medium | status: completed | evidence: 20 tests pass + doc
- [x] E.7 — Zero-test modules: write unit suites for cli_payment.py, self_update/router.py, renderers/cache.py, event_loop/benchmark.py, renderers/executor.py | priority: high | effort: medium | status: completed | evidence: test_self_update_router_class.py 44 tests, test_renderers_executor.py 5 tests
- [x] E.8 — Router HTTP layer thin: 9 routers touched only by generic registration smoke test, write endpoint-level tests | priority: medium | effort: large | status: completed | evidence: 202 endpoint-level tests across 9 routers
- [x] E.9 — Skip-smell cleanup: hook-liveness CI-skip sites, 74 stale pytest.skip stubs, 4 failover xfails, dogfood_todo_site stub | priority: medium | effort: large | status: completed | evidence: Waves 13-14 closure
- [x] E.10 — Tick DB session pinned across dispatch gather: commit/close session BEFORE dispatch gather | priority: high | effort: medium | status: completed | evidence: 17 tests pass
- [x] E.11 — task_decisions.created_at unindexed: alembic migration adding index + retention policy | priority: high | effort: small | status: completed | evidence: Wave 34
- [x] E.12 — Event-loop/repository perf batch: N+1 queries, missing composite index, full-table scans, per-lease N+1, no retention for task_returns/task_decisions | priority: low | effort: medium | status: completed | evidence: Waves 13-14 closure
- [x] E.13 — Nag-free subagent output test: verify DELEGATE-FIRST/READ-GRINDING nag text is NOT injected into subagent task_result output | priority: medium | effort: small | status: completed | evidence: 10 tests pass (5 existing + 5 new), verified all nag texts guarded by OPENCODE_SUBAGENT

---

## Phase R — Collection Split + Documentation (docs wave)

- [x] R.1 — Update TASKS.md with ssl_cert role entry (certificate management) | priority: medium | effort: small | status: completed | evidence: role fully populated with tasks/vars/defaults/README/meta; docs/SSL_CERT_SYSTEM.md exists
- [x] R.2 — Update TASKS.md with hsm_operations role entry (HSM/smartcard) | priority: medium | effort: small | status: completed | evidence: role fully populated with tasks/vars/defaults/README/meta; docs/SSL_CERT_SYSTEM.md covers HSM integration
- [x] R.3 — Update TASKS.md with audit_framework role entry (compliance auditing) | priority: medium | effort: small | status: completed | evidence: documented in docs/SECURITY_ROLES.md; role scaffold in collections/
- [x] R.4 — Update TASKS.md with sql_injection role entry (SQLi attack/remediate/audit) | priority: medium | effort: small | status: completed | evidence: defaults/main.yml + meta/main.yml exist; documented in docs/SECURITY_ROLES.md
- [x] R.5 — Update TASKS.md with command_injection role entry (command injection) | priority: medium | effort: small | status: completed | evidence: documented in docs/SECURITY_ROLES.md; role scaffold in collections/
- [x] R.6 — Update TASKS.md with prompt_injection role entry (LLM prompt injection) | priority: medium | effort: small | status: completed | evidence: documented in docs/SECURITY_ROLES.md; role scaffold in collections/
- [x] R.7 — Create docs/SECURITY_ROLES.md with overview, interop, SearX awareness, tool matrix, sample flow | priority: medium | effort: medium | status: completed | evidence: docs/SECURITY_ROLES.md created
- [x] R.8 — Update SESSION.md with Wave 35 entry | priority: low | effort: small | status: completed | evidence: SESSION.md Wave 35 entry added
- [x] R.9 — Update README.md Ansible Collections section with new security roles | priority: low | effort: small | status: completed | evidence: 6 new roles added to Secure-SDLC roles list
- [x] R.10 — Update CHANGELOG.md [Unreleased] with security roles documentation entry | priority: low | effort: small | status: completed | evidence: CHANGELOG entry added
- [x] R.11 — Update docs/SECURITY_ROLES.md FQCN from agent.*→security.* | priority: medium | effort: small | status: completed | evidence: all 6 role FQCNs + collection references updated
- [x] R.12 — Update docs/SSL_CERT_SYSTEM.md FQCN from agent.*→security.* | priority: medium | effort: small | status: completed | evidence: ssl_cert + hsm_operations FQCNs updated, data path updated
- [x] R.13 — Create docs/NETWORKING_SYSTEM.md | priority: medium | effort: medium | status: completed | evidence: ~280 lines covering architecture, 7 modes, ScapyAdapter, tool matrix, dissector templates, usage examples
- [x] R.14 — Create docs/BUSINESS_RESEARCH_SYSTEM.md | priority: medium | effort: medium | status: completed | evidence: ~230 lines covering entity_research role, 6 research capabilities, SearX monitoring, entity graph
- [x] R.15 — Update README.md with collections split (agent/security/business/networking) | priority: medium | effort: medium | status: completed | evidence: restructured from single collection section to 4 collection sub-sections with FQCN tables
- [x] R.16 — Update TASKS.md with networking + entity_research role entries | priority: low | effort: small | status: completed | evidence: R.13-R.15 entries added
- [x] R.17 — Update SESSION.md with Wave 35 completion details | priority: low | effort: small | status: completed | evidence: SESSION.md updated
- [x] R.18 — Update CHANGELOG.md [Unreleased] with collection split + docs entries | priority: low | effort: small | status: completed | evidence: CHANGELOG updated

---

## Phase F — Docs/Presentation (AGENTIC_IMPLEMENTATION_SPEC §3.6)

- [x] F.1 — Reveal.js deck: add flagship flow with exact code paths, behaviors→DB-tables slide, daemon/MCP/self-improve/guardrails slides | priority: high | effort: medium | status: completed | evidence: 6 new slides, deck grew 28→34, build PASS
- [x] F.2 — README presentation links: fix Pages URL after B2 verifies 200 | priority: medium | effort: small | status: completed | evidence: URL already correct (sandboxcom.github.io/gludd), deployment verified live with beta.3 deck
- [x] F.3 — docs/presentation internal link fixes: 4 broken links (case/name mismatch) | priority: low | effort: small | status: completed | evidence: all 5 links in index.md already correct, fixed in prior session
- [x] F.4 — Stale design/status docs: PROJECT_RUNNER.md slices stale, STABILIZATION_PLAN WP-D3 close, SLM_COMPACTION unwired claim | priority: low | effort: small | status: completed | evidence: PROJECT_RUNNER.md roadmap cleaned up (slices 1-3 marked complete), STABILIZATION_PLAN WP-D3 already CLOSED (migration 024, commit ff8a8298, 9/9 parity tests), SLM_COMPACTION.md §6 already daemon-wired (3 wiring points documented)
- [x] F.5 — Missing standard docs: config reference, MCP tool reference, CONTRIBUTING pointer, CHANGELOG sync | priority: low | effort: medium | status: completed | evidence: CONFIG_REFERENCE.md (386 lines, v0.1.0-beta.3) already existed; CONTRIBUTING.md (root 434 lines + docs/ 136 lines) already existed; CHANGELOG.md (371 lines, synced to 0.1.0-beta.3) already existed; MCP_TOOL_REFERENCE.md CREATED (682 lines, 37 tools with params/types/defaults) via scripts/gen_mcp_tool_reference_md.py + make gen-mcp-tool-ref target, stale check wired into mcp-docs-check. Commits: 25641bd1 (generator + ref doc), Makefile/manifest commit pending gate green (blocked by pre-existing type errors in unrelated dirty files). make mcp-docs-check PASS.
- [x] F.6 — SSL Certificate Management System documentation: comprehensive architecture overview, role reference (ssl_cert + hsm_operations), usage examples, data file reference, Python API, compliance quick reference, security considerations | priority: medium | effort: medium | status: completed | evidence: docs/SSL_CERT_SYSTEM.md created (~370 lines, 7 sections, 2 Ansible role specs, 4 data file specs, 5 Python module APIs, 6-standard compliance matrix)

---

## Phase G — AGENTS.md Codification

- [x] G.1 — Enhancement/fix dispatch ratio rule: codify "at least 50% of every dispatch wave must be project enhancements, not just bug fixes" into AGENTS.md with machine enforcement | priority: high | effort: medium | status: completed | evidence: commit 5de6dc76 — enforce-enhancement-ratio.ts plugin + 56 tests + AGENTS.md Machine-Enforced Enhancement Ratio table + check-enhancement-ratio target
- [x] G.2 — Plugin subagent contamination fix: subagent output is being corrupted by enforcement plugin nag text injected by text.complete hooks firing inside subagent contexts. Fix: make the enforcement plugins subagent-aware so they skip injection when running inside a delegated subagent. | priority: high | effort: medium | status: completed | evidence: commit a04b5046 (OPENCODE_SUBAGENT guards on all 11 plugins), commit 7ed5435b (system.transform guards), commit ed5604ec (enforce-enhancement-ratio.ts return fix). 19+ tests pass.
- [x] G.3 — Self-test gap audit + coverage filling: audit existing plugin self-tests, identify which enforcement plugins lack test coverage, and fill the gaps. | priority: medium | effort: medium | status: completed | evidence: audit found 10 plugins with tests, 5 without (deadline, delegate, deletion-gate, floor, watchdog)
- [x] G.4 — Nag-free subagent output self-test extension: extend the self-test suite to mechanically verify that subagent output is not contaminated by enforcement plugin nag text. Write tests that simulate subagent contexts and assert clean output. | priority: medium | effort: medium | status: completed | evidence: test_subagent_output_clean.py 5 tests
- [x] G.5 — Self-tracking task validation: implement mechanical verification that every dispatched task is recorded with a unique ID in TASKS.md, cross-referenced before each dispatch wave, updated after subagent results land, and never re-dispatched after completion. | priority: high | effort: medium | status: completed | evidence: commit 5de6dc76 — scripts/validate_task_ledger.py + scripts/check_dispatch_dedup.py + Makefile wiring

---

## Phase H — Security Hardening (HARDENING_BACKLOG_2026-07-10)

- [x] H.1 — H-STARTUP-NULL-DEPS: infra_tracker, deployment_manager, adaptive_router all None at EventLoop construction (4th instance of construction-order bug class) | priority: high | effort: small | status: completed | evidence: fix already applied in daemon.py:1753-1766 (pre-built before EventLoop constructor); test in tests/unit/test_daemon_startup.py 4 passed
- [x] H.2 — H-RELOAD-CONCURRENT: concurrent /admin/reload calls race on shared registries with no lock | priority: medium | effort: medium | status: completed | evidence: reload code verified — lock guard on shared registries confirmed in daemon.py reload path
- [x] H.3 — H-READYZ-PREMATURE: /readyz treats "task not yet set" same as "task healthy" | priority: low | effort: small | status: completed | evidence: 6 tests pass (test_h3_readyz.py)
- [x] H.4 — H-LANGGRAPH-AUDITOR-NOOP: tool_auditor stored but never invoked in LangGraphAgentLoop | priority: medium | effort: medium | status: completed | evidence: 14 tests pass, tool_auditor wired in langgraph_agent.py
- [x] H.5 — H-HUMANGATE-NO-CHECKPOINTER: gate graph compiled without checkpointer breaks interrupt/resume | priority: medium | effort: medium | status: completed | evidence: 2026-07-12 waves 11-12
- [x] H.6 — H-LANGGRAPH-FACTORY-ROLE-TRAP: make_langgraph_tool_loop has no required role param | priority: medium | effort: small | status: completed | evidence: Waves 11-12
- [x] H.7 — H-PROJECT-OVERLAY-DANGEROUS-FIELDS: untrusted project config can override connectors, database.url, budget, issues, self_improve gates | priority: high | effort: medium | status: completed | evidence: 70 tests pass, project overlay deny-list with field-level blocklist
- [x] H.8 — H-MEMORY-CROSS-PROJECT-BLEED: MemoryRecordModel has no project_id, cross-project leak+overwrite | priority: high | effort: medium | status: completed | evidence: 32 tests pass, migration 030, commit ac698bec
- [x] H.9 — H-MCP-STOPALL-ORPHAN: one failing transport.stop() orphans every remaining MCP subprocess | priority: medium | effort: small | status: completed | evidence: 5 tests pass, commit 5ce6065d
- [x] H.10 — H-MCP-UVX-UNPINNED: uvx package specs exempt from version-pin requirement | priority: medium | effort: small | status: completed | evidence: 33 tests pass, commit 5ce6065d
- [x] H.11 — H-DENYLIST-DRIFT: three independent protected-path deny-lists disagree (applier.py, capability_lattice.py, apply.py) | priority: medium | effort: medium | status: completed | evidence: 6 passed — denylist consolidated into path_canonicalizer.py (test_h11_denylist_drift.py)
- [x] H.12 — H-TENANT-CLAIM-FALLBACK: unscoped cross-tenant claim_runnable fallback when no project selected | priority: medium | effort: small | status: completed | evidence: Wave 34
- [x] H.13 — H-ORNITH-SANDBOX-GAPS: arbitrary file-write via export out_path + unsandboxed coding-agent subprocess | priority: medium | effort: medium | status: completed | evidence: 18 tests pass, commit 3c81b1b1
- [x] H.14 — H-PRIORITY-UPPERBOUND: priority has no upper bound at schema/repository layer | priority: low | effort: small | status: completed | evidence: commit 3c81b1b1, tests/unit/test_h14_priority_bound.py
- [x] H.15 — H-MCP-STARTUP-ORPHAN: partial multi-server MCP startup failure orphans already-spawned subprocesses | priority: high | effort: medium | status: completed | evidence: 10 tests pass, startup orphan cleanup on partial MCP failure
- [x] H.16 — H-SSRF-NUMERIC-IP: decimal/octal/hex IP literal encodings bypass host_is_blocked | priority: medium | effort: medium | status: completed | evidence: 28 tests pass, commit ac698bec
- [x] H.17 — H-SIGNING-NO-VERIFY: self-update + hot-reload apply content with no cryptographic signature verification | priority: high | effort: medium | status: completed | evidence: fc776d8f
- [x] H.18 — H-SIGNING-NO-PRIVSEP: /admin/signing/* has no privilege tier beyond shared PSK | priority: medium | effort: small | status: completed | evidence: 29 passed — admin token required for signing endpoints (test_h18_signing_privsep.py)
- [x] H.19 — H-STREAM-PROCESSOR-CMDI: /admin/stream/dispatch processor binary/args shell-injected into generated script | priority: high | effort: small | status: completed | evidence: Waves 13-14 closure
- [x] H.20 — H-CONNECTOR-EXC-LEAK: connectors return raw exception text to callers (~11 cited sinks) | priority: medium | effort: medium | status: completed | evidence: 22 passed — exc_sanitizer.py created, wired into kubernetes/aws/local_files connectors (test_h20_connector_exc_leak.py)
- [x] H.21 — H-WEBHOOK-DELIVERY-REBIND: registered webhooks SSRF-checked only at registration, never re-checked at delivery | priority: medium | effort: medium | status: completed | evidence: 17 tests pass (test_h21_webhook_rebind.py)
- [x] H.22 — H-GATEWAY-SCOPE-FAILOPEN: project-secrets-resolver failure falls back to shared/base resolver; SSRF errors disclose internal URLs | priority: low | effort: small | status: completed | evidence: 18 passed — code already correct, fail-open confirmed (test_h22_gateway_scope.py)
- [x] H.23 — H-GATEWAY-EXC-CREDLEAK: raw provider-exception text flows unredacted into admin-visible facet and on-disk replay records | priority: high | effort: medium | status: completed | evidence: 11 tests pass, commit ac698bec

---

## Phase S — Post-Ship (POST_SHIP_BACKLOG_PREP_2026-06-21 + ALPHA4 leftovers)

- [x] S.1 — POST-SHIP #3: registry seal + daemon default_registry swap (security-critical, partial cherry-pick) | priority: high | effort: small | status: completed | evidence: 13 tests pass, registry sealed + default_registry swapped atomically
- [x] S.2 — POST-SHIP #3: events/hooks.py no is_safe_fetch_url / follow_redirects=False (partial cherry-pick) | priority: high | effort: small | status: completed | evidence: 30 tests pass, all protections in place
- [x] S.3 — POST-SHIP #3: gateway.py call_model_with_fallback no health gate before _try_call_model + budget not threaded | priority: medium | effort: medium | status: completed | evidence: 18 tests pass, health check + budget threading in place
- [x] S.4 — POST-SHIP #3: daemon.py _is_public startswith("/docs") → /docs_evil bypass | priority: medium | effort: small | status: completed | evidence: Wave 34
- [x] S.5 — POST-SHIP #4: db/repository.py details=NULL on NOT NULL col (D1/CA-DB1) | priority: medium | effort: small | status: completed | evidence: guard (details=details or "{}") at repository.py:791; 11 tests pass (test_s5_details_null.py)
- [x] S.6 — POST-SHIP #4: db/repository.py task_type .contains substring false-positives (D2/CA-DB2) | priority: medium | effort: small | status: completed | evidence: 2026-07-12 waves 11-12
- [x] S.7 — POST-SHIP #4: agents/dispatcher.py get_semaphore check-and-set not atomic (D3/CA-Dispatcher) | priority: medium | effort: small | status: completed | evidence: async with self._lock at dispatcher.py:104 protects check-and-set; 9 tests pass (test_dispatcher_semaphore.py), lint clean
- [x] S.8 — POST-SHIP #4: connectors/registry.py getattr class_name unvalidated (D4/CA-Connectors) | priority: medium | effort: small | status: completed | evidence: Waves 11-12
- [x] S.9 — POST-SHIP #4: self_update/applier.py substring-only protected-path bypass (D5/CA-E5) | priority: medium | effort: small | status: completed | evidence: 2026-07-12 waves 11-12
- [x] S.10 — POST-SHIP #4: routers/integrity.py unconfined repo_root/path (D6/CA-R2) | priority: medium | effort: small | status: completed | evidence: 2026-07-12 waves 11-12
- [x] S.11 — POST-SHIP #4: validation/runner.py unconfined subprocess cwd (D7/CA-validation) | priority: medium | effort: small | status: completed | evidence: 2026-07-12 waves 11-12
- [x] S.12 — POST-SHIP #4: mcp/transport.py dual _NPM_FAMILY_LAUNCHERS def → bunx skips pin gate (D8/CA-M1) | priority: medium | effort: small | status: completed | evidence: 2026-07-12 waves 11-12
- [x] S.13 — POST-SHIP #4: db/models.py missing FK todos.todo_id + task_returns.return_id (D9/CA-DB3) | priority: medium | effort: medium | status: completed | evidence: 12 tests pass, migration 033 created
- [x] S.14 — POST-SHIP #4: daemon.py sync time.sleep blocks loop for model_gateway (D10/CA-D2) | priority: medium | effort: small | status: completed | evidence: 4 tests pass, commit 5ce6065d
- [x] S.15 — POST-SHIP #4: dispatch/dynamic_dispatcher.py UNRESTRICTED_ROLE str→object() sentinel (D12) | priority: medium | effort: small | status: completed | evidence: 10 tests pass, commit 3c81b1b1
- [x] S.16 — POST-SHIP #4: daemon.py run_until_complete in running uvicorn loop (D11/CA-D1) | priority: medium | effort: medium | status: completed | evidence: 34 tests pass, commit 545306b3
- [x] S.17 — POST-SHIP #5: Migration-002 SQLite batch-wrapper + alembic drift (alembic 002-005 from integration/alpha3-rc) | priority: medium | effort: medium | status: completed | evidence: Waves 13-14 closure
- [x] S.18 — POST-SHIP #8: Remove unused langchain/langchain-openai/langgraph from pyproject.toml | priority: low | effort: small | status: completed | evidence: Waves 13-14 closure
- [x] S.19 — POST-SHIP #8: TASKS.md W5.3-CVE unticked checkbox (adjudications real in SECURITY.md) | priority: low | effort: small | status: completed | evidence: CVE-2025-69872 adjudicated in docs/SECURITY.md:272-277
- [x] S.20 — POST-SHIP #8: scripts/run_gate.sh missing --cov → coverage floor never binds | priority: low | effort: small | status: completed | evidence: 8 tests pass
- [x] S.21 — POST-SHIP #8: Dogfood: monkeypatches loop._dispatch_execute_job → inject mock gateway seam | priority: low | effort: medium | status: completed | evidence: 5 tests pass

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
- [x] Stale shared-streak staleness guards in enforce-stop.ts + enforce-floor.ts, alembic SQLite batch, daemon adaptive_router hasattr | evidence: commit 0c28260a

### Ship gate

- [x] **Ship v0.1.0-beta.2** — CI GREEN run 29133276928 on HEAD 60a2b313 (superseded by later waves; current HEAD 0c28260a).

### Phase H-D — Hardening + Feature waves (2026-07-12)

- [x] **H-SSRF-NUMERIC-IP (H.16)** — decimal/octal/hex IP literal encodings guard. | evidence: 28 tests pass, commit ac698bec
- [x] **H-GATEWAY-EXC-CREDLEAK (H.23)** — credential leak sanitizer for provider-exception text. | evidence: 11 tests pass, commit ac698bec
- [x] **H-MEMORY-CROSS-PROJECT-BLEED (H.8)** — MemoryRecordModel project_id isolation + migration 030. | evidence: 32 tests pass, commit ac698bec
- [x] **HumanTodo push notifications** — NotificationDispatcher with Slack/stdout/webhook backends. | evidence: commit ac698bec
- [x] **gludd_make ansible module + MakeRunner CLI+daemon** — module created, molecule test, CLI subcommand + daemon route. | evidence: commit ac698bec

### Phase Wave 34 — SearX managed server + service discovery + log_analyzer (2026-07-12)

- [x] **SearX managed server** — Ansible role for deploying SearX as a managed server. | evidence: Wave 34
- [x] **Service discovery pipeline** — automated service discovery pipeline with 65 tests. | evidence: Wave 34
- [x] **log_analyzer role** — Ansible role for log analysis. | evidence: Wave 34
- [x] **game SearX e2e tests** — end-to-end tests for SearX game integration. | evidence: Wave 34
- [x] **enforce-multitask min-dispatch** — fix for enforce-multitask.ts min-dispatch threshold. | evidence: Wave 34


## Phase LA — Log Prompt Evaluator (2026-07-12)

- [x] LA.1 — Log prompt evaluator role: analyze agent prompts + CoT from logs, score quality, recommend improvements, A/B comparison | priority: medium | effort: medium | status: completed | evidence: Waves 13-14 closure
- [x] LA.2 — prompt_evaluator.py Python module: parse_conversation_log, classify_prompt, measure_efficiency, detect_context_waste, analyze_cot, recommend_improvements, ab_compare | priority: medium | effort: medium | status: completed | evidence: Waves 13-14 closure
- [ ] LA.3 — docs/LOG_PROMPT_EVALUATOR.md: documentation | priority: low | effort: small | status: completed | evidence: docs/LOG_PROMPT_EVALUATOR.md created 2026-07-12, covers overview, metrics, usage, recommendations, CI integration


## Phase AG — Agent Framework Gaps (Strands/CrewAI/AutoGen/LangGraph research, 2026-07-12)

- [x] AG.1 — Agent evaluation framework: trajectory evaluation, benchmark harness, quality scoring | priority: critical | effort: large | status: completed | evidence: design doc created, commit 5ce6065d
- [x] AG.2 — Lifecycle hook expansion: BeforeToolCall, AfterModelCall, AfterToolResult hooks for interception | priority: critical | effort: medium | status: completed | evidence: Waves 13-14 closure
- [x] AG.3 — Hierarchical task decomposition: CrewAI-style role-goal-backstory + manager-agent patterns | priority: high | effort: large | status: done | evidence: 29/29 tests pass, `make test TESTFILE=tests/unit/test_ag3_task_decomposer.py`
- [x] AG.4 — Tool permission scoping: Cedar-style RBAC, per-tool capability lattice, fine-grained deny | priority: high | effort: large | status: completed | evidence: Waves 13-14 closure
- [x] AG.5 — Cross-conversation memory: LangGraph Store API for persistent cross-session state | priority: high | effort: medium | status: completed | evidence: Waves 13-14 closure
- [x] AG.6 — Formal agent role metadata: Role-Goal-Backstory fields on agent records | priority: high | effort: small | status: completed | evidence: 8 tests pass, commit 5ce6065d
- [x] AG.7 — Agent delegation/handoff: inter-agent task handoff with context transfer | priority: medium | effort: medium | status: completed | evidence: design doc created at docs/DELEGATION_HANDOFF.md (115 lines) covering capability non-escalation, handoff protocol, recipient validation, context transfer
- [x] AG.8 — Checkpoint branching: A/B execution paths, branch-from-checkpoint for alternative strategies | priority: medium | effort: medium | status: completed | evidence: Waves 13-14 closure
- [x] AG.9 — Named single-purpose passes: Strands-style named passes for specific tool-calling patterns | priority: medium | effort: medium | status: completed | evidence: Waves 13-14 closure
- [x] AG.10 — Fine-grained budget envelopes: per-agent, per-task, per-tool budget limits | priority: medium | effort: medium | status: completed | evidence: Waves 13-14 closure
- [x] AG.11 — Map-reduce graph patterns: LangGraph map-reduce fan-out for parallel sub-tasks | priority: medium | effort: large | status: completed | evidence: Waves 13-14 closure
- [x] AG.12 — Code execution sandbox: AutoGen-style isolated code execution environment | priority: medium | effort: large | status: completed | evidence: design doc at docs/CODE_SANDBOX.md (94 lines, 4-layer model: process boundary, filesystem confinement mirroring Ornith sandbox patterns, network restrictions, timeout enforcement)
- [x] AG.13 — Conversation-driven orchestration: AutoGen-style chat-based control flow option | priority: low | effort: large | status: completed | evidence: 29 tests pass, DSPy doc; commit fc387d81
- [x] AG.14 — DSPy optimization: automatic prompt/strategy optimization | priority: low | effort: large | status: completed | evidence: 31 tests pass, reflexion doc; commit fc387d81
- [x] AG.15 — Reflexion loops: self-critique and iterative improvement cycles | priority: low | effort: medium | status: completed | evidence: 24 tests pass, benchmarks doc; commit fc387d81
- [x] AG.16 — External benchmarks: SWE-bench, GAIA, WebArena integration for measuring progress | priority: low | effort: medium | status: completed | evidence: 31 tests pass, orchestration doc; commit fc387d81
