# BURNDOWN.md — master completeness ledger (gludd)

> **Authoritative burn-down**, read-only, uncommitted. Built 2026-06-16 by
> cross-reading every status doc under `docs/audit/` + `docs/design/feature_gap_backlog.md`
> + `TASKS.md` + the git log, then **re-verifying the load-bearing wiring claims
> against current code** (the working tree has moved past several of the source
> docs). Method: Read/Grep only; no pytest/gate run (fixers are running tests
> concurrently). Every verdict is grounded in a `file:line`, a named test, or a
> commit. Where two source docs disagreed, the disagreement was reconciled by
> reading the code and the **stale doc is flagged** in the row.

---

## 0. How to read this / scope notes

- **Two disjoint id schemes.** `TASKS.md` tracks `V/R/W` ids. The `#12–#82`
  numbers appear **only in commit messages** (e.g. `#27 event-loop-wiring`,
  `#49 spend`, `#42 saturation`), never in `TASKS.md`. They are mapped here by
  *capability*, not by a matching tick. (Confirmed: `backlog_completeness_2026-06.md` §0.)
- **Verdict legend:** **DONE** = committed + a real passing test asserts the
  capability *and* it is reachable in production. **PARTIAL** = real, tested code
  exists but the end-to-end capability is not live (the dominant failure class —
  "built-but-unwired"). **OPEN** = not started / design-only.
- **% complete** is the fraction of the task's end-to-end capability that is real
  and reachable. A fully-tested-but-unwired module sits at ~60–75% (library done,
  wiring + e2e proof remaining).

---

## SUMMARY

| Section | Count | Meaning |
|---|---|---|
| **A — 100% DONE** (committed + tested + reachable) | **~46 items** | The V/R/W spine, guardrails, security red-team fixes, Ansible role/molecule layer. |
| **B — PARTIAL** (built-but-unwired / missing piece) | **~14 items** | The bulk of remaining value. Needs the **daemon-integration wave**. |
| **C — OPEN** (not started / design-only) | **~13 items** | The `feature_gap_backlog.md` G1–G13 next-wave roadmap. |

### The single biggest lever → the DAEMON-INTEGRATION WAVE

The largest, highest-quality block of remaining value is **fully-built, fully-unit-tested
modules that no production call path reaches.** One focused wave that *wires the
built modules into the running daemon + adds an e2e proof per module* moves the
most items from PARTIAL→DONE at once:

1. **Connector/observe layer (38 connectors + `routers/observe.py` + `connectors/registry.py`)** —
   the single biggest item. `routers/observe.py:28-43` literally documents the
   **2-line hookup** that was deliberately deferred ("do NOT apply this wave —
   integration pass owns it"). Call `wire_observability(app, _daemon_state, _connector_config)`
   in `daemon.py` + add a `connectors:` config section + an e2e "configured source
   is queryable through the daemon" test. **~38 modules + ~38 test files of
   green-but-orphaned code** flip to a real operator capability.
2. **`DynamicDispatcher` + `VariableStore`** (`dispatch/`) — wired into a *router*
   (`routers/dispatch.py`) but **NOT into the event-loop turn handler**; the
   `# TODO(integration)` at `dispatch/dynamic_dispatcher.py:8` is unfulfilled.
   Wire it so a model's `tool_calls` drive a `VariableStore` that re-renders the
   next prompt.
3. **`SpendLimiter` real cost projection** — now wired into the dispatch path
   (`daemon.py:672-730`, `daemon_wiring.make_spend_guarded_executor`) but the
   guard passes `projected_cost_usd=0.0` (`daemon.py:729`), so it is *wired but
   conservatively inert*. Feed a real per-call token-cost projection.
4. **Per-class behavioral proof for W9** — 28/29 "wired" classes rest on
   *reference*-wiring, not behavior (only `AnsibleTemplater` got a behavioral
   test). Same dead-path-one-level-up risk as the connectors.

Everything in Section B below is gated behind this wave. Do it once, prove each
module e2e, and the PARTIAL column collapses.

### Reconciliations made this pass (where source docs were STALE)

- **#23/#32 Scheduler "not wired"** (`status_older_inprogress.md:20,25`) → **NOW WIRED.**
  `event_loop/loop.py:709,740` imports `Scheduler` and calls `Scheduler().plan(items)`
  in `_phase_dispatch_execute_jobs`, dispatching concurrency-safe batches via
  session-per-coroutine `asyncio.gather` (`loop.py:700-754`). The `# TODO(integration)`
  comment at `scheduler.py:15` is now stale leftover text. **Promoted to DONE-ish PARTIAL.**
- **#49/#27 SpendLimiter "not wired"** (`security_tasks_status.md:20`, `status_older:22`) →
  **NOW WIRED into dispatch + restart-rehydrated.** `daemon.py:672-702` constructs
  it, `_restore_persisted_spend` rehydrates from the DB across restart (`daemon.py:697`),
  and `make_spend_guarded_executor` gates the gateway executor (`daemon.py:726`).
  Residual: `projected_cost_usd=0.0` makes the gate inert until a real projection
  is supplied. **Promoted PARTIAL→ (wired) PARTIAL with a smaller gap.**
- **Connector layer** (`backlog_completeness_2026-06.md` G1) → **STILL UNWIRED**
  (verified: `daemon.py` does not import `general_ludd.connectors`; `routers/observe.py`
  exists but `wire_observability()` is never called; not in `routers/__init__.register_all`).
  This finding stands and is the #1 lever.

---

## SECTION A — 100% DONE (committed + tested + reachable)

| id | title | % | verdict | evidence (file:line / test / commit) | what's left to 100% |
|---|---|---|---|---|---|
| V0.1–V0.4 | honest green gate (42 failures fixed, strict-xfail ratchet, 0-tolerance) | 100 | DONE | TASKS.md:9-12; `make gate "ALL PASSED"` 237123f | — |
| R0.1–R0.7 | restore build: collects 0-err, lint 0, daemon wiring real, re-baseline | 100 | DONE | TASKS.md:26-32; 9ed21e0/53811f8/7797660 | — |
| R1.1–R1.10 | guardrails: truth targets, gated commit, completion-claim check, evidence ledger | 100 | DONE | TASKS.md:36-45; `.opencode/plugin/enforce-make.ts`; 03552d1/6fc53f1 | — |
| R2.1–R2.6, R3.5 | missed work M1/M6/M10/M12/M13 + every G/S/F/M re-proven; validate green | 100 | DONE | TASKS.md:49-56; named unit tests pass; 7797660 | — |
| W1.1–W1.7 | ratchet-growth guard, TASKS tick guard, state-based stop checks, MYPY_MAX var, preflight fail-closed on unknown criteria | 100 | DONE | TASKS.md (W1 phase); e865e31; `backlog_completeness:105-106` GROUNDED | — |
| W2.x | worker full-pipeline ratchet, lease acquire+reclaim, deploy registry, compute secrets resolver, runtime path fix, git-sha | 100 | DONE | TASKS.md:72-82; named e2e/unit tests; eb84b0c/26cf62b/779937c | — |
| W3.1 (C1) | worker invokes ModelGateway for generation jobs | 100 | DONE | `test_obj03_worker.py::TestWorkerModelGatewayCall` 3 passed; daemon.py:704-720; b4de809 | — |
| W3.2 (H4) | ReturnReviewer + apply_decision wired into review phase; failure escalates | 100 | DONE | `test_w3_2_reviewer_wiring.py` 3 passed; a7a97c6 | — |
| W3.3/W3.4 | asyncio.to_thread playbook runs; `/readyz` (DB ping + loop-alive → 503 degraded) | 100 | DONE | daemon.py:978-1002; `test_w3_3/_w3_4` pass; 779937c | — |
| W3.5 (M8/H18) | SQLite-only enforced (non-SQLite refused) + single-worker clamp | 100 | DONE | `test_single_worker_sqlite.py` 7 passed; 312e403 | — |
| W3.7 (H2) | self-improvement todos persisted via TodoRepository | 100 | DONE | `test_w3_7_self_improve_persist.py` 2 passed; a7a97c6 | — |
| W3.8 | worker stubs honest 501 (validate/policy/reload) | 100 | DONE | `test_w3_8_worker_501.py` + e2e; 779937c | — |
| W3.9 | MCP DEFER decision recorded; code honestly fences (no silent success) | 100 | DONE (as decision) | TASKS.md:324-342; `test_mcp_wiring.py`; daemon.py mcp_client=None | (config→client source intentionally deferred) |
| W3.10/W3.11/W3.12/W3.13/W3.14 | gateway metrics; project workspace clone+persist; reload honesty; CLI parity; one select_project/tick | 100 | DONE | named tests pass; a4c04a9/779937c/a7a97c6 | — |
| W4.1 | tenacity is THE retry path; demo deleted (was a FALSE TICK, now real) | 100 | DONE | `test_w4_1_tenacity_retry.py` 5 passed; `backlog_completeness:113` GROUNDED; 15db868 | — |
| W4.2–W4.6 | MCP transport KEEP rationale; watchdog FileWatcher; pydantic-settings UserConfig; deptry audit; KEEP comments | 100 | DONE | TASKS.md:119-123; 15db868 | — |
| W5.1 | SSH key present-but-gitignored (never tracked, never in history); 2 enforcement layers | 100 | DONE | `TestNoTrackedPrivateKeys` 2 passed; `backlog_completeness:138` GROUNDED; 526104b | (operator key *rotation* is out-of-agent-scope residual) |
| W5.2/W5.3 | dist packs LICENSE+THIRD_PARTY+SBOM, path-scrubbed; fresh secrets scan | 100 | DONE | `test_dist_license_pack.py` 6 passed; 526104b | (2 CVEs adjudicated non-blocking; ticks pending in TASKS.md:252-253) |
| W5.4 | mypy 18→0; MYPY_MAX=0 single var | 100 | DONE | `.gate-status` typecheck PASS 0; 526104b | — |
| W5.5 | README hardcoded metrics deleted → `make gate` pointer; preflight guards re-intro | 100 | DONE | `TestReadmeNoHardcodedMetrics` 5 passed; 526104b | — |
| W5.6 | worker `/jobs/*` require PSK auth (401 before 501); /healthz public | 100 | DONE | `test_w5_6_worker_auth.py` 9 passed; 526104b | — |
| W6.1–W6.9 | Ansible collection skeleton + modules (ping/worktree/git/db/skill/mcp_tool/agent_run) + agent_task role + 118-test registry | 100 | DONE | `test_playbook_registry.py` (118); d0203ba | — |
| W7.1–W7.4 | message-queue persistence+API; facts aggregation API; gludd_facts/gludd_message modules; prompt MQ section | 100 | DONE | `test_agent_message_repo.py`+`test_messages_and_facts_api.py`+`test_prompt_message_queue_section.py`; bd80f5a | — |
| W8.1–W8.4 | 7 AI-coding task roles + 5 audit/report roles + 2 playbooks + 107-test suite | 100 | DONE | `test_w8_roles_and_reports.py` 107 passed; 2eec9e1 | — |
| W9.1 | completion_audit 83%→100% (29 classes wired); preflight 0 findings | 90 | DONE (import-wired) | `test_completion_audit_wiring.py` 26 passed; 6915362/5a232c3 | **behavioral** proof for 28/29 classes (only AnsibleTemplater has one) — see B-row W9-behavioral |
| W10.1–W10.6 | molecule mock-daemon harness + 26 scenarios (modules+roles); coverage gate | 100 (local) | DONE | `test_molecule_coverage.py` 7 passed; `make molecule-test-all` 26/26; 41889e6 | (CI-green is separate — see W16) |
| W12.1 | observability facts: metrics + traces as Ansible dynamic facts; live seam test | 100 | DONE | `test_facts_live_seam.py` 4 + `test_trace_store.py` 7; 86389be | — |
| W13.1/W14.1/W15.1 | 5 pipeline roles + 7 secure-SDLC roles + 9 agile/sprint roles + molecule (→49 scenarios) | 100 (local) | DONE | `make molecule-test-all` 49/49; 2a8f97b/9629e20/8b252e1 | (CI-green separate) |
| W3.6 | per-item proof table: 50 G/S/F/M proofs, all PASS, 0 GAP | 95 | DONE | TASKS.md:167-246; named tests re-run green; 6915362 | minor: W9 behavioral caveat folds in here too |
| #52 | DB races (claim contention, upsert TOCTOU, lost-update, project scoping, FK cascade) | 100 | DONE | `test_db_redteam.py` (TestClaimContention/UpsertTOCTOU/LostUpdate/ProjectScoping/FKCascade); `security_tasks_status:21` DONE | — |
| #53 | secrets lifecycle (env allowlist, KV containment, dev-token, loopback bind, AppRole rotation) | 100 | DONE | `test_secrets_redteam.py` (multiple classes); `security_tasks_status:22` | — |
| #54 | gateway concurrency (single-flight cache, empty-200 not billed, token clamp, fallback circuit) | 100 | DONE | `test_gateway_concurrency_redteam.py`; `security_tasks_status:23` | — |
| #56 | clone RCE/SSRF + workspace traversal (is_safe_clone_url, is_path_within) | 100 | DONE | `test_project_workspace_clone.py` + `test_git_repo_clone_hardening.py`; `security_tasks_status:24` | — |
| #58 | self-modification guards (capability lattice, protected-path deny, reload-site enforcement) | 100 | DONE | `test_self_modify_guards.py`; `security_tasks_status:25` | — |
| #43 | fs write allow/deny + FIM-on-write (WriteAuditLog, tamper detection, manifest confinement) | 100 | DONE | `test_fs_write_audit.py`; `security_tasks_status:26` | — |
| #60 | metric-label cardinality bound (MAX 50/key, overflow→__other__, count preserved) | 100 | DONE | `test_metrics_cardinality.py`; `security_tasks_status:27` | — |
| #61 | base_url SSRF (is_safe_fetch_url, fail-closed before client construct) | 100 | DONE | `test_gateway_base_url_ssrf.py`; `security_tasks_status:28` | — |
| A-1/A-2/A-3, S-1, F-1 | PSK security: hmac.compare_digest, no-PSK-in-logs, GLUDD_REQUIRE_AUTH opt-in, env allowlist, sanitize-path | 100 | DONE | daemon.py:856-945; `backlog_completeness:108` GROUNDED; 81fcc33 | — |
| #50 | Ansible SSTI red-team suite | 100 | DONE | per `security_tasks_status:51` (already-fixed w/ own suite) | — |
| W11.1 | CI version PEP 440 fix (non-tag `0.1.0-alpha.<ts>`, tag strips `v`) | 100 | DONE | `test_ci_workflow.py::TestVersionPEP440` 7 passed; `build-executable` produced dist/gludd; 11d3060 | — |
| #23/#32 | Scheduler drives event-loop tick (concurrency-safe parallel batches) | 90 | DONE (wired) | `event_loop/loop.py:709,740` `Scheduler().plan(items)`; session-per-coroutine gather :700-754 | **STALE in `status_older`** (said unwired). Residual: no dedicated `test_scheduler_integration.py` proving *parallel* dispatch e2e; `scheduler.py:15` TODO comment is dead text. |

---

## SECTION B — PARTIAL (built-but-unwired / missing piece) — the bulk; needs the daemon-integration wave

| id | title | % | verdict | evidence (file:line / test) | what's left to 100% |
|---|---|---|---|---|---|
| **Connector layer** (#72/#73; "observability connector layer" in ed294c4 msg) | 38 connectors + registry + observe router | 60 | **PARTIAL — UNWIRED** | `connectors/registry.py`, `connectors/base.py`, 38 `connectors/*.py` + 38 `test_connector_*.py` (all green, mocked transports, SSRF-guarded); `routers/observe.py` (PSK-gated, SSRF-safe surface) | **THE #1 LEVER.** daemon.py does NOT import `general_ludd.connectors`; `wire_observability()` is never called; not in `routers/__init__.register_all`. Add a `connectors:` config section + call the documented 2-line hookup (`routers/observe.py:36-37`) + an e2e proof a configured source is queryable. Until then: do NOT tick "connector layer delivered" (`backlog_completeness` G1). |
| W9-behavioral | 28/29 completion_audit classes proven by *reference*, not behavior | 70 | PARTIAL | `test_completion_audit_wiring.py` (import-wired only); only `AnsibleTemplater` has a behavioral test (TASKS.md:312) | Add behavioral tests proving `AgentCapabilities.make_graph_gateway` / `make_tool_loop` / `failover` (+ the other 25) are invoked on a **real request path**, not only the audit-wiring test. Dead-path-one-level-up risk (`backlog_completeness:127`). |
| #49/#27 | SpendLimiter rolling $X/window cap | 80 | PARTIAL (wired-but-inert) | limiter logic DONE (`spend_limiter.py:109` try_charge, fail-closed, RLock; `test_spend_limiter_enforcement.py` 50-thread barrier); **NOW wired** daemon.py:672-730 + restart rehydrate :697 | Guard passes `projected_cost_usd=0.0` (daemon.py:729) → the cap never engages until a caller supplies a **real per-call token-cost projection**. Wire `token_cost_usd(model, est_in, est_out)` into the guard. `spend_limiter.py:17` TODO is partly stale (wiring done; projection isn't). **STALE-er than `security_tasks_status:20` claims** (it said "not wired at all"). |
| #26 | DynamicDispatcher + VariableStore (dynamic tool-call dispatch) | 65 | PARTIAL — UNWIRED into loop | fully implemented `dispatch/dynamic_dispatcher.py:142-237` + `variable_store.py:20-114`; tested `test_dynamic_dispatcher.py`; wired into `routers/dispatch.py` only | `# TODO(integration)` `dynamic_dispatcher.py:8` unfulfilled: NOT called from the event-loop turn handler. When a model returns `tool_calls`, dispatch via `DynamicDispatcher`, write results to a `VariableStore`, re-render next prompt; add integration test. |
| #59/#69 | scoring cost-cap + avg_cost | 70 | PARTIAL | router logic DONE `scoring/router.py:38-67` `route(max_cost_usd=)` → cost_constrained; `test_scoring.py::test_route_cost_constraint` | `avg_cost` is **never emitted by the production aggregate query** (`BenchmarkRepository.get_aggregate_scores` has no avg_cost column/key) → every candidate cost is 0.0, cap is a silent no-op on real data. Add `func.avg(cost)` + a `cost` column to `BenchmarkResultModel`. (`security_tasks_status:29`) |
| #28 | per-project accounting (time/money/LoC/role-stats/todo) | 40 | PARTIAL | per-project cost via `MetricsCollector.get_cost_by_project` (facts.metrics); per-project todo via `TodoRepository.status_summary` | `ProjectManager` tracks only weight/dispatch_mode/workspace — **no time, no LoC, no per-role stats**. Add a per-project accounting aggregate (wall-time + USD + LoC delta + per-role counts + todo throughput) + a test asserting all 5 dimensions. (`status_older:23`) Note `routers/accounting.py:15` has a `TODO(integration): wire loc_changed to git-diff --numstat`. |
| #31 | agent file-overlap coordination + merge-aware waiting | 35 | PARTIAL | abstract `Scheduler.resources` frozenset serializes shared resources (now wired, see A); Makefile `wt-sync/wt-apply/wt-reap` + `docs/ORCHESTRATION.md` | No **file-path-aware** overlap coordinator: nothing maps a work item to the file paths it touches, serializes intersecting items, and blocks a dependent until the overlapping branch merges. No `test_file_overlap.py`. (`status_older:24`) |
| #21/W16.1 | GitHub Action passes in REAL CI | 50 | PARTIAL — UNVERIFIED-in-CI | workflow complete `.github/workflows/build.yml:31-71`; "Event loop is closed" fix shipped (4704299) | TASKS.md:450 itself admits the fix **could not be reproduced locally** and "CI-green is UNVERIFIED-in-CI; must be confirmed by the next sandboxcom run." Paste a real green `sandboxcom/gludd` run id (admin-gated logs). Prior `308793c` proves CI surfaces failures the local gate misses. |
| W10–W15 CI | 49 molecule scenarios green in CI | 70 | PARTIAL (local-only) | green locally (`make molecule-test-all`); CI molecule job added | Same as #21 — local-green ≠ CI-green. Confirm with a sandboxcom run id. |
| observe/ package | `src/general_ludd/observe/` + `routers/observe.py` facade | 60 | PARTIAL — UNWIRED | router + facade built, PSK+SSRF-safe; untracked | Folds into the connector lever: wire `wire_observability()` in daemon + config + e2e. Currently untracked (`??`) and unreachable. |
| orchestration/ package | `src/general_ludd/orchestration/` (untracked) | ~50 | PARTIAL — verify | new untracked package (`git status ??`); not covered by any source doc; not confirmed wired into daemon.py | NEXT-SESSION: read the package, determine if it duplicates `scheduling/Scheduler` (already wired) or adds new capability; wire or fence it; it is uncommitted. |
| pipeline/ package | `src/general_ludd/pipeline/` controller | 80 | PARTIAL (wired, uncommitted) | controller imported + used `daemon.py:441,745` per wiring probe; has tests (`test_pipeline_*.py`) | Appears wired but is **untracked/uncommitted** + not in any V/R/W tick. Commit + add a TASKS.md ledger row with evidence; confirm e2e. |
| receiver/ + issue_sources/ + self_update/ | untracked new packages (receiver buffer/parsers, ~17 issue-source connectors, self-update applier/router) | 50 | PARTIAL — verify+wire | `git status ??` lists them + many `test_issue_source_*.py`, `test_receiver_*.py`, `test_self_update_*.py`; `scripts/gludd_update.py`, `docs/MODEL_DEPLOYMENT_TUNING.md`, `docs/privileges/` also untracked | Large body of green-but-uncommitted, likely-unwired work (the connector-layer pattern repeated). NEXT-SESSION: per package confirm daemon wiring + an e2e proof, then commit or fence. Do NOT tick as delivered until reachable. |

### Bug-audit items (from `bugs_80_findings.md`) — fixes sketched, NOT applied → PARTIAL/OPEN as fixes

| id | title | % | verdict | evidence | what's left |
|---|---|---|---|---|---|
| BUG-1 | skill body rendered through **non-sandboxed Jinja2** (SSTI/RCE) | 0 (sketch only) | OPEN-fix | `skills/renderer.py:56` plain `Environment`; reachable via remote SKILL.md → `engine.py:48` | Switch to `SandboxedEnvironment`, catch `SecurityError`→`SkillRenderError`; add `test_renderer_sandbox.py`. **NOTE: `test_skills_ssti_injection_redteam.py` + `test_skills_renderer_adversarial.py` are untracked `??`** — a fix may be in flight; verify. |
| BUG-2/BUG-8 | frontmatter injection on install (raw name/description into YAML; logic duplicated 2 sites) | 0 (sketch) | OPEN-fix | `fetcher.py:129` + `routers/skills.py:110` | yaml.safe_dump via one shared `build_installed_skill_md` helper; regression test. |
| BUG-3 | issue-ingestor dedup is a no-op in production (new instance per request) | 0 (sketch) | OPEN-fix | `routers/maintenance.py:55` fresh ingestor each call; `_seen_ids` resets | Cache ingestor on app.state keyed by (owner,repo,label); integration test poll-twice→count 0,0. (`test_issue_ingestor_adversarial.py` untracked — verify.) |
| BUG-4 | URL/param injection in GitHub issues request (no quote) | 0 (sketch) | OPEN-fix | `issue_ingestor.py:70-73` | `urllib.parse.quote` + owner/repo charset validation; capture-URL test. |
| BUG-5 | `from_url` IndexError on short URLs → 500 | 0 (sketch) | OPEN-fix | `fetcher.py:47-49` | length guard → typed ValueError → 422. |
| BUG-6/BUG-7 | RunHistory aliasing leak + naive substring todo-id match | 0 (sketch) | OPEN-fix | `run_history.py:29-32,50` | deep-copy on store; structured-key match `job_id.split(":",1)[0]==todo_id`. (`test_run_history_adversarial.py` untracked — verify.) |

---

## SECTION C — OPEN (not started / design-only)

Source: `docs/design/feature_gap_backlog.md` (#38) — explicitly "DESIGN/BACKLOG
ONLY, do not treat any row as done". These are absences in the daemon import map.

| id | title | % | verdict | evidence | what's left to 100% |
|---|---|---|---|---|---|
| G1 | persistent agent memory / context store | 0 | OPEN | no `memory/` package (`feature_gap:58-75`) | new `memory/` pkg + `MemoryStore` + migration + `gludd_memory` module + prompt injection. HIGH/L. |
| G2 | offline eval / regression harness | 0 | OPEN | only live benchmark telemetry exists (`feature_gap:77-93`) | `eval/` pkg + fixtures + `make eval` + baselines/gating. HIGH/L. (prereq for G5/G6/G8) |
| G3 | semantic codebase retrieval (RAG context) | 0 | OPEN | tree-sitter is structural-only, not wired to dispatch (`feature_gap:95-111`) | `retrieval/` pkg + `CodeIndexer` + `ContextRetriever` wired into dispatch. HIGH/XL. |
| G4 | sandboxed code execution (container/gVisor jail) | 0 | OPEN | only process-isolation config exists (`feature_gap:113-130`) | `sandbox/` pkg + `SandboxRunner` + egress allowlist + caps. HIGH/L. (overlaps BUG-1 risk) |
| G5 | outcome-driven self-improvement loop | 0 | OPEN | harness is static-only (`feature_gap:132-148`) | `OutcomeAnalyzer` over benchmark+reconcile history, gated by G2. HIGH/M (needs G2). |
| G6 | prompt/skill versioning + A/B + rollback | 0 | OPEN | PromptRegistry stores no version/hash/history (`feature_gap:152-166`) | versioning + `experiments/` controller + CLI promote/rollback. MED/M. |
| G7 | generalized human-in-the-loop approval gates | 0 | OPEN | HITL scoped to file-integrity only (`feature_gap:168-183`) | `approvals/` pkg + `ApprovalGate` wired at merge/release/major-bump/budget-breach. MED/M. |
| G8 | cost/quality Pareto routing optimizer | 0 | OPEN | router is role/quality/latency + static fallback (`feature_gap:185-200`) | `CostQualityOptimizer` over benchmark history; needs #59/#69 avg_cost fixed first. MED/M. |
| G9 | plan/critique/decomposition layer (planner→executor→critic) | 0 | OPEN | planner role referenced but no decomposition controller (`feature_gap:202-215`) | `planning/` controller + parent/child todo linkage + langgraph. MED/L. |
| G10 | per-run replay / deterministic trace bundle | 0 | OPEN | traces are aggregate, not replay bundles (`feature_gap:217-230`) | `replay/` `RunRecorder` + `gludd replay show/rerun`. MED/M. |
| G11 | multi-agent debate / consensus review | 0 | OPEN | single-reviewer pipeline (`feature_gap:234-244`) | `review/consensus.py` fan-out + judge. LOW/M. |
| G12 | live web/docs retrieval tool for agents | 0 | OPEN | no first-party web-retrieve tool (`feature_gap:246-256`) | `web_retrieve` MCP tool/role + allowlist + cache. LOW/M. |
| G13 | structured task-spec / acceptance-criteria schema | 0 | OPEN | todos are free-text; reconcile uses gate signals (`feature_gap:258-269`) | optional `acceptance_criteria` field + reconcile evaluation. LOW/S. |

---

## Housekeeping flags for the next working session (not burn-down items)

- **G2/G3 (working-tree hygiene):** `nested/`, `proj-ok/` are test-scratch dirs at
  repo root; `.opencode/plugin/enforce-floor.ts`, `.claude/` are untracked. Gitignore
  or clean — must never be committed. (`backlog_completeness` G2)
- **RATCHET_MAX vs live count:** confirm `RATCHET_MAX` in `tests/unit/test_guardrails.py`
  equals the live `config/ratchet.yml` count (~11) so the W1.1 growth guard isn't
  silently slack. (`backlog_completeness` G3)
- **Two TASKS.md ticks still open:** W5.3-CVE diskcache + pip (TASKS.md:252-253) —
  adjudicated non-blocking, awaiting a follow-up docs commit hash.
- **`routers/coordination.py` + `routers/dispatch.py`:** registered in daemon
  (1198/1208) but NOT in `routers/__init__.register_all` — centralize or document.
- **`daemon.py:1204` + `daemon_wiring.py:182`:** `TODO(integration): wire to
  collection loader — no loader exists` (features/collection-handler gap).

## Provenance — docs reconciled this pass

`docs/audit/status_older_inprogress.md` (#21/#23/#26-28/#31-32),
`docs/audit/security_tasks_status.md` (#43/#49/#52-54/#56/#58-61/#69),
`docs/audit/backlog_completeness_2026-06.md` (#66, connector G1),
`docs/audit/bugs_80_findings.md` (BUG-1..8), `docs/design/feature_gap_backlog.md`
(#38, G1-G13), `TASKS.md` (V/R/W ledger), `make git-log`. Direct code re-verification:
`daemon.py:670-730`, `event_loop/loop.py:700-754`, `controllers/spend_limiter.py:1-40`,
`routers/observe.py:1-50`, `make git-status` (untracked-package inventory).
Two source-doc claims were found STALE and corrected here: Scheduler wiring
(#23/#32) and SpendLimiter wiring (#49/#27) are now LIVE in code.
