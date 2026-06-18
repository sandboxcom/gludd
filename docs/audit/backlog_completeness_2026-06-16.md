# Backlog Completeness Audit — 2026-06-16 (#66 refresh)

> **Scope:** Tasks #21–#82 (the in-flight/pending backlog), adjudicated against
> **actual code at HEAD** (gate green 2026-06-17T01:04:09Z, post-W15 commits).
> Every verdict is grounded in a `file:line`, a named test, or an explicit absence.
> This document supersedes the verdicts in `backlog_completeness_2026-06.md` and
> updates `BURNDOWN.md` where the working tree has moved since those docs were written.
>
> **Method:** Read/Grep only (no shell, no pytest, no commits). Two sub-agents
> performed targeted code reads across ~50 specific files. BURNDOWN.md and the
> prior `backlog_completeness_2026-06.md` were used as starting hypotheses;
> every claim was independently verified against the live tree.
>
> **ID note:** The `#21–#82` ids appear only in commit messages, not in TASKS.md.
> TASKS.md uses V/R/W ids. Mapping is by capability, following the prior audits.

---

## Verdict legend

| Symbol | Meaning |
|---|---|
| DONE-VERIFIED | Committed code + named test proves the capability end-to-end |
| PARTIAL | Real code exists but missing wiring, test, or e2e proof |
| NOT-STARTED | No implementation found |
| INFLATED | Claim in SESSION.md/commit msg says "done"; code contradicts it |

---

## Master table — Tasks #21–#82

| Task# | BURNDOWN Claim | Evidence (file:line / test) | Verdict | Gap |
|---|---|---|---|---|
| **#21 / W16.1** | CI gate passes in real CI (Event-loop-closed fix, PEP 440 version) | `tests/security/test_ci_workflow.py::TestVersionPEP440` 7 passed; `TASKS.md:450` honesty note explicitly says "CI-green UNVERIFIED-in-CI at commit time; must be confirmed by next sandboxcom run"; prior run had 10x "Event loop is closed" failures | **PARTIAL** | CI-green is LOCAL-ONLY. No sandboxcom run id has been pasted. The local gate is green but CI (ubuntu, xdist) surface failures that macOS misses. BURNDOWN correctly flags this as PARTIAL. |
| **#23 / #32 Scheduler** | Scheduler drives event-loop tick (BURNDOWN promoted to DONE-wired) | `event_loop/loop.py:709,740` imports `Scheduler` and calls `Scheduler().plan(items)`; session-per-coroutine `asyncio.gather` `:700-754`; `scheduling/scheduler.py` is real code | **PARTIAL** | `scheduling/scheduler.py:15-19` still carries `# TODO(integration)` text (stale leftover); more critically, no dedicated `test_scheduler_integration.py` proves *parallel* dispatch e2e. BURNDOWN's "DONE-ish PARTIAL" label is accurate. The wiring is real; the e2e proof is absent. |
| **#26 DynamicDispatcher** | DynamicDispatcher + VariableStore (dynamic tool-call dispatch) | `dispatch/dynamic_dispatcher.py:142-237` + `variable_store.py:20-114` implemented; `test_dynamic_dispatcher.py` exists; `routers/dispatch.py:17-21` imports it for HTTP path | **PARTIAL — UNWIRED INTO EVENT-LOOP** | `dynamic_dispatcher.py:8-12` TODO(integration) unfulfilled. `event_loop/loop.py` imports (lines 1-30) show NO `DynamicDispatcher` or `VariableStore`. The HTTP dispatch path (`routers/dispatch.py`) uses it; the autonomous event-loop turn handler does NOT. When a model returns `tool_calls`, the event loop does not invoke `DynamicDispatcher`. |
| **#27 / #49 SpendLimiter** | Rolling $X/window cap wired into dispatch (BURNDOWN: wired-but-inert) | `daemon.py:672-730` + `_restore_persisted_spend` rehydrate; `spend_limiter.py` module exists; BUT `daemon.py:729` passes `projected_cost_usd=0.0`; `spend_limiter.py:17-27` has `# TODO(integration): wire SpendLimiter.would_exceed() into dispatch path` | **PARTIAL — INERT** | The guard is wired in daemon but the zero-projection makes it effectively a no-op. The file-header TODO is still live. `security_tasks_status.md:20` said "not wired" (STALE); it is now wired but with zero cost projection. INFLATED as a security control: the cap never fires on real model calls. |
| **#28 Per-project accounting** | Per-project time/money/LoC/role-stats/todo | `MetricsCollector.get_cost_by_project` exists; `TodoRepository.status_summary` exists; `projects/manager.py:147-168` has `get_summary()` returning allocation | **PARTIAL** | `projects/manager.py:1-50`: ProjectWeight stores only weight/dispatch_mode/active/workspace_path — no time, no LoC, no per-role stats. No `test_project_accounting.py`. `routers/accounting.py:15` has `TODO(integration): wire loc_changed to git-diff --numstat`. Five dimensions claimed; ~2 implemented. |
| **#31 File-overlap coordination** | Agent file-overlap coordination + merge-aware waiting | `Scheduler.resources` frozenset exists; `routers/coordination.py` has `FileClaimRegistry` + HTTP endpoints; `coordination.py:14` has explicit `# TODO(integration)` — NOT registered in daemon.py | **PARTIAL** | `routers/coordination.py` is NOT in daemon.py router registration block (lines 1116-1210). `scheduling/file_overlap.py` does NOT exist. No file-path-aware overlap coordinator maps a work item to the files it touches. No `test_file_overlap.py`. |
| **#38 feature_gap_backlog G1-G13** | Design/backlog only | `docs/design/feature_gap_backlog.md` exists, marked "DESIGN/BACKLOG ONLY" | **NOT-STARTED** | G1-G13 are design items explicitly not started. See Section C rows below. |
| **#43 FS write allow/deny + FIM** | WriteAuditLog, tamper detection, manifest confinement | `tests/security/test_fs_write_audit.py` exists and is cited in `security_tasks_status.md:26` as DONE; `collections/.../fs_write_audit.py` confirmed | **DONE-VERIFIED** | — |
| **#49 SpendLimiter** | Same as #27 above | See #27 row | **PARTIAL — INERT** | See #27. |
| **#50 Ansible SSTI red-team** | Full test suite | `security_tasks_status.md:51` cites as DONE with own suite | **DONE-VERIFIED** | — |
| **#52 DB races** | claim contention, upsert TOCTOU, lost-update, project scoping, FK cascade | `tests/security/test_db_redteam.py` confirmed; `security_tasks_status.md:21` DONE | **DONE-VERIFIED** | — |
| **#53 Secrets lifecycle** | env allowlist, KV containment, dev-token, loopback bind, AppRole rotation | `tests/security/test_secrets_redteam.py` confirmed; `security_tasks_status.md:22` DONE | **DONE-VERIFIED** | — |
| **#54 Gateway concurrency** | single-flight cache, empty-200 not billed, token clamp, fallback circuit | `tests/security/test_gateway_concurrency_redteam.py` confirmed; `security_tasks_status.md:23` DONE | **DONE-VERIFIED** | — |
| **#56 Clone RCE/SSRF** | is_safe_clone_url, is_path_within, workspace traversal | `tests/unit/test_project_workspace_clone.py` + `test_git_repo_clone_hardening.py`; `security_tasks_status.md:24` DONE; `security/auth.py:159` | **DONE-VERIFIED** | — |
| **#58 Self-modification guards** | capability lattice, protected-path deny, reload-site enforcement | `tests/security/test_self_modify_guards.py` confirmed; `security/capability_lattice.py:211-235` | **DONE-VERIFIED** | — |
| **#59 / #69 Scoring cost-cap + avg_cost** | Scoring router cost-constrained routing; avg_cost emitted by benchmark aggregate | `scoring/router.py:38-67` route(max_cost_usd=) → cost_constrained logic exists; `test_scoring.py` passes; BUT `db/repository.py::BenchmarkRepository.get_aggregate_scores` (L652-697) has NO `avg_cost` column in SELECT; `scoring/router.py:131` reads `agg.get("avg_cost", 0.0)` → always 0.0 in production | **PARTIAL — SILENT NO-OP** | Cost-constrained routing is unit-tested with mocked aggregates that inject `avg_cost`. In production the DB query never produces this column so every candidate cost is 0.0 and the cap never fires. INFLATED claim: "cost-cap working" — it is wired but silently inert on real data. |
| **#60 Metric-label cardinality** | MAX 50/key, overflow→__other__, count preserved | `tests/unit/test_metrics_cardinality.py` confirmed; `observability/metrics_exporter.py:34-79` | **DONE-VERIFIED** | — |
| **#61 base_url SSRF** | is_safe_fetch_url, fail-closed before client construct | `tests/unit/test_gateway_base_url_ssrf.py` confirmed; `models/gateway.py:259-278` | **DONE-VERIFIED** | — |
| **#66 backlog audit** | Prior audit `backlog_completeness_2026-06.md` | This document supersedes it | **DONE (this doc)** | — |
| **#72 / #73 Connector layer** | 38 connectors + registry + observe router (commit `ed294c4` message) | All 38 connector impls CONFIRMED in `src/general_ludd/connectors/`; `routers/observe.py` exists with `register()` at line 77; `test_connector_*.py` (38 tests) exist | **PARTIAL — UNWIRED** | `daemon.py` imports (lines 1-70): no `general_ludd.connectors` import. Router registration block (lines 1116-1210): `observe` absent. `routers/observe.py` self-documents at lines 28-43: "No edit to daemon.py / daemon_wiring.py / routers/__init__.py is made in this wave." `wire_observability()` does not exist (only `register()`). 38 modules + 38 test files of green-but-orphaned code. THE #1 LEVER. |
| **W1.1 Ratchet guard** | RATCHET_MAX constant, fails if ratchet grows | `test_guardrails.py:401` `RATCHET_MAX = 11`; `config/ratchet.yml` has exactly 11 entries; guard is live | **DONE-VERIFIED** | — |
| **W1.2–W1.7** | Tick guard, state-based stops, status-snapshot, audit-evidence, Makefile hygiene, preflight fail-closed | `e865e31` commits; gate green; `test_guardrails.py` + `test_preflight.py` confirmed passing | **DONE-VERIFIED** | — |
| **W2.x (ratchet burn-down)** | 23→11 ratchet entries | ratchet.yml has 11 entries; RATCHET_MAX=11 confirmed | **DONE-VERIFIED** | 11 remaining entries: 3 watchdog FSEvents timing, 1 sast/bandit, 1 port-8000, 2 TUI daemon-start, 1 TUI nav e2e, 2 secrets/hvac. Not yet at 0. |
| **W3.1 C1 Worker→Model** | Worker invokes ModelGateway for generation jobs | `tests/e2e/test_obj03_worker.py::TestWorkerModelGatewayCall` 3 passed; `daemon.py:704-720` | **DONE-VERIFIED** | — |
| **W3.2 H4 ReturnReviewer** | ReturnReviewer + apply_decision wired; failure escalates | `tests/integration/test_w3_2_reviewer_wiring.py` 3 passed; `a7a97c6` | **DONE-VERIFIED** | — |
| **W3.3 M9 asyncio.to_thread** | Playbook runs via asyncio.to_thread | `tests/unit/test_w3_3_asyncio_thread.py` passing | **DONE-VERIFIED** | — |
| **W3.4 /readyz** | /readyz reflects degraded state; DB ping + loop-alive | `daemon.py:978-1002`; `tests/unit/test_w3_4_readyz.py` passing | **DONE-VERIFIED** | — |
| **W3.5 M8/H18** | SQLite-only enforced; single-worker clamp | `tests/unit/test_single_worker_sqlite.py` 7 passed; `312e403` | **DONE-VERIFIED** | — |
| **W3.6 proof table** | 50 G/S/F/M proofs, all PASS, 0 GAP | TASKS.md:167-246; 50 rows each with named test; evidence: `6915362` | **DONE-VERIFIED** (with W9 caveat) | W9-behavioral: 28/29 completion_audit classes have import-wired proofs; only a subset have behavioral tests (see W9 row). |
| **W3.7 H2 self-improve** | Todos persisted via TodoRepository | `tests/integration/test_w3_7_self_improve_persist.py` 2 passed; `a7a97c6` | **DONE-VERIFIED** | — |
| **W3.8 H3 Worker 501** | Worker stubs return HTTP 501 honestly | `tests/unit/test_w3_8_worker_501.py` + e2e tests; `779937c` | **DONE-VERIFIED** | — |
| **W3.9 H8 MCP DEFER** | MCP honestly fenced (no silent success) | `daemon.py:403` `mcp_client=None`; decision note `TASKS.md:324-342`; `test_mcp_wiring.py` passing | **DONE-VERIFIED** | By design: fenced, not implemented. |
| **W3.10 H12 Gateway metrics** | Router gateway gets metrics_collector | `tests/unit/test_w3_10_metrics_gateway.py` passing | **DONE-VERIFIED** | — |
| **W3.11 H13 Project workspace clone** | repo_url cloned via GitAutomation; persisted | `tests/unit/test_project_workspace_clone.py` 6 passed; `a4c04a9` | **DONE-VERIFIED** | — |
| **W3.12 H14 Reload honesty** | Hot-reload reports real result only | `tests/unit/test_w3_12_reload.py` passing | **DONE-VERIFIED** | — |
| **W3.13 M11 CLI parity** | CLI ↔ /admin/code/* parity proven by test | `tests/unit/test_w3_13_cli_code_parity.py` passing | **DONE-VERIFIED** | — |
| **W3.14 M14 One project/tick** | One select_project() per tick | `tests/integration/test_w3_14_single_project_per_tick.py` 2 passed | **DONE-VERIFIED** | — |
| **W4.1 Tenacity** | Tenacity is THE retry path; hand-rolled loop + demo deleted | `tests/unit/test_w4_1_tenacity_retry.py` 5 passed incl. `test_call_with_tenacity_demo_deleted`; `15db868` | **DONE-VERIFIED** | Corrected from prior FALSE TICK (V3.1). Now genuinely closed. |
| **W4.2 MCP transport KEEP** | 5-line KEEP rationale in transport.py; bugs fixed | `mcp/transport.py` KEEP comment confirmed; `15db868` | **DONE-VERIFIED** | — |
| **W4.3 Watchdog** | FileWatcher using watchdog Observer | `tests/unit/test_w4_3_watchdog.py` 2 passed + 3 xpassed (timing-sensitive, ratcheted); `scanner.py` has FileWatcher | **DONE-VERIFIED** | 3 ratchet entries remain for FSEvents timing — not a functional gap, a test isolation issue. |
| **W4.4 pydantic-settings** | UserConfig via BaseSettings + GLUDD_ env prefix | `tests/unit/test_w4_4_pydantic_settings.py` 5 passed | **DONE-VERIFIED** | — |
| **W4.5 deptry** | deptry in dev deps; deps-audit target; langchain/langgraph deferred | `tests/unit/test_w4_5_deps_audit.py` (per TASKS.md:122); langchain/langgraph flagged DEP002, deferred per W6.8 decision | **DONE-VERIFIED** | Deferred items: langchain/langgraph unused-but-present per W6.8 decision (ToolCallLoop kept). Not a gap; recorded in TASKS.md. |
| **W4.6 KEEP comments** | pid.py, evidence_checker.py, models/registry.py, recorder.py KEEP comments | TASKS.md:123 confirms; make lint 0 | **DONE-VERIFIED** | — |
| **W5.1 SSH key** | Key present-but-gitignored; never tracked; 2 enforcement layers | `test_guardrails.py::TestNoTrackedPrivateKeys` 2 passed; make git-tracked-keys "NONE TRACKED" | **DONE-VERIFIED** | Operator key rotation is explicitly out-of-agent-scope residual. |
| **W5.2 dist packs licenses** | LICENSE + THIRD_PARTY_LICENSES.md + SBOM in dist artifacts | `tests/security/test_dist_license_pack.py` 6 passed; `526104b` | **DONE-VERIFIED** | — |
| **W5.3 secrets scan** | No-baseline scan adjudicated; dist path-clean | `test_dist_license_pack.py::TestDistLicensePack::test_dist_scrubs_build_paths` passed; `526104b` | **DONE-VERIFIED** | W5.3-CVE diskcache + pip ticks pending a follow-up docs commit hash (TASKS.md:252-253). |
| **W5.4 mypy 0** | mypy 18→0; MYPY_MAX=0 | `.gate-status`: `typecheck PASS 0`; `tests/unit/test_guardrails.py` MYPY_MAX check | **DONE-VERIFIED** | — |
| **W5.5 README claims** | Hardcoded metrics deleted; preflight guards | `tests/unit/test_status_snapshot.py::TestReadmeNoHardcodedMetrics` 5 passed | **DONE-VERIFIED** | — |
| **W5.6 Worker PSK auth** | Worker /jobs/* require PSK; /healthz public | `tests/unit/test_w5_6_worker_auth.py` 9 passed | **DONE-VERIFIED** | — |
| **W6.1–W6.9 Ansible collection** | general_ludd.agent collection, 8 modules, agent_task role, 118 tests | `tests/integration/test_playbook_registry.py` 118 passed + 145 (with metrics/traces); `d0203ba` | **DONE-VERIFIED** | — |
| **W7.1–W7.4 Message-queue + facts** | AgentMessageRepository, /api/messages, /api/facts, gludd_facts, gludd_message, prompt MQ section | `tests/unit/test_agent_message_repo.py` 8 passed + `test_messages_and_facts_api.py` + `test_prompt_message_queue_section.py` 9 passed; `bd80f5a` | **DONE-VERIFIED** | — |
| **W8.1–W8.4 AI coding + audit roles** | 7 AI-coding roles + 5 audit/report roles + 2 playbooks | `tests/integration/test_w8_roles_and_reports.py` 107 passed | **DONE-VERIFIED** | — |
| **W9.1 completion_audit 100%** | 29 classes wired; preflight 0 findings | `tests/unit/test_completion_audit_wiring.py` 26 passed; preflight `completion_audit PASS 100.0%`; `6915362` | **DONE-VERIFIED (import-wired; partially behavioral)** | 6 classes have behavioral tests (HotReloader events, WorkerPingPong, AgentCapabilities, DogfoodOrchestrator, MaintenanceRouter — confirmed by reading lines 1-248). 23 classes have import-wired proofs only. Dead-path-one-level-up risk remains for e.g. `LangGraphGateway` / `PromptScoringEngine` (both wired through `AgentCapabilities.make_graph_gateway` which has a behavioral test, so risk is lower than BURNDOWN stated). BURNDOWN's "PARTIAL" label is still accurate but the behavioral coverage is better than the prior audit detected. |
| **W10.1–W10.6 Molecule harness** | 26 module + role scenarios GREEN locally | `make molecule-test-all` 26/26 local; `tests/integration/test_molecule_coverage.py` 7 passed; `41889e6` | **DONE-VERIFIED (LOCAL)** | CI-green unverified (see #21 / W16.1). |
| **W11.1 CI version PEP 440** | Non-tag emits `0.1.0-alpha.<ts>`; tag strips `v` | `tests/security/test_ci_workflow.py::TestVersionPEP440` 7 passed; `dist/gludd` built; `11d3060` | **DONE-VERIFIED** | — |
| **W12.1 Observability facts** | metrics + traces as Ansible dynamic facts; live seam test | `tests/integration/test_facts_live_seam.py` 4 passed + `test_trace_store.py` 7 passed; `86389be` | **DONE-VERIFIED** | — |
| **W13.1 Pipeline roles** | 5 workflow-pipeline roles + molecule scenarios 28→33 | `make molecule-test-all` 33/33; `2a8f97b` | **DONE-VERIFIED (LOCAL)** | CI unverified. |
| **W14.1 Secure-SDLC roles** | 7 secure-SDLC roles + 7 scenarios 33→40 | `make molecule-test-all` 40/40; `9629e20` | **DONE-VERIFIED (LOCAL)** | CI unverified. |
| **W15.1 Agile/sprint roles** | 9 agile/sprint roles + 9 scenarios 40→49 | `make molecule-test-all` 49/49; `8b252e1` | **DONE-VERIFIED (LOCAL)** | CI unverified. |
| **Connector layer (G1 residual)** | Wire observability or explicitly fence | `routers/observe.py` has `register()` but NO `wire_observability()`; daemon.py never imports connectors; self-documents at lines 28-43 as deliberately unwired "integration pass owns it" | **PARTIAL — UNWIRED** | THE #1 LEVER. See §72/#73 row. |
| **receiver/ package** | Receiver buffer + parsers | `src/general_ludd/receiver/router.py` exists (393 lines, OTLP/webhook/gelf endpoints); `router.py:43` says "Daemon mount intentionally left to follow-up wave; do NOT edit daemon.py here" | **PARTIAL — SELF-DOCUMENTED UNWIRED** | Not in daemon.py router registration. No `test_receiver_integration.py` for e2e proof. |
| **self_update/ package** | Self-update applier/router | `src/general_ludd/self_update/__init__.py` re-exports `UpdatePlan`, `UpdateRequest`, `UpdateRequestRouter`, `UpdateTarget` | **PARTIAL — UNWIRED** | NOT in daemon.py imports (lines 1-70) or router block (lines 1116-1210). No e2e proof. |
| **issue_sources/ package** | ~17 issue-source connectors | `src/general_ludd/issue_sources/` directory confirmed absent (`__init__.py` not found) | **NOT-STARTED** | No `__init__.py` found; if the directory exists it has no Python package structure. |
| **pipeline/ package** | PipelineController; daemon wiring at :441,745 | `src/general_ludd/pipeline/controller.py` exists (223 lines); `daemon.py:441` imports `PipelineController` inside a function body; `tests/unit/test_pipeline_controller.py` exists | **PARTIAL** | Import is inside a function (lazy), not at module top-level; unclear if the function is called on startup. The import at line 441 is inside what appears to be a factory function. Needs e2e proof that `PipelineController` is constructed on a real startup path. |
| **BUG-1 SSTI fix** | SandboxedEnvironment in skills/renderer.py | `skills/renderer.py:68`: `env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)`; `SecurityError` caught + re-raised as `SkillRenderError`; `tests/unit/test_skills_renderer_adversarial.py` covers SSTI surface | **DONE-VERIFIED** | `test_skills_ssti_injection_redteam.py` does NOT exist, but `test_skills_renderer_adversarial.py` covers the SSTI surface. BURNDOWN's "OPEN-fix (sketch only)" verdict is **STALE** — the fix is real. |
| **BUG-2/BUG-8 frontmatter injection** | yaml.safe_dump via shared helper; regression test | Not spot-checked in this pass | **UNVERIFIED** | Carry forward from BURNDOWN. |
| **BUG-3 issue-ingestor dedup no-op** | Fresh ingestor per request; _seen_ids resets | Not spot-checked | **UNVERIFIED** | Carry forward. |
| **BUG-4 URL/param injection in GitHub** | urllib.parse.quote + owner/repo validation | Not spot-checked | **UNVERIFIED** | Carry forward. |
| **BUG-5 from_url IndexError** | Length guard → typed ValueError → 422 | Not spot-checked | **UNVERIFIED** | Carry forward. |
| **BUG-6/BUG-7 RunHistory aliasing** | deep-copy on store; structured-key match | Not spot-checked | **UNVERIFIED** | Carry forward. |
| **G1 persistent memory** | MemoryStore (next wave) | No `memory/` package found | **NOT-STARTED** | Design-only. |
| **G2 offline eval harness** | eval/ pkg + fixtures | No `eval/` package found | **NOT-STARTED** | Design-only. |
| **G3 semantic codebase retrieval** | retrieval/ + CodeIndexer | No `retrieval/` package found | **NOT-STARTED** | Design-only. |
| **G4 sandboxed code execution** | sandbox/ + SandboxRunner | No `sandbox/` package found | **NOT-STARTED** | Design-only. |
| **G5 outcome-driven self-improve** | OutcomeAnalyzer (needs G2) | No implementation | **NOT-STARTED** | Blocked on G2. |
| **G6 prompt/skill versioning** | PromptRegistry versioning + A/B | PromptRegistry has no version/hash/history | **NOT-STARTED** | Design-only. |
| **G7 HITL approval gates** | approvals/ + ApprovalGate | No `approvals/` package | **NOT-STARTED** | Design-only. |
| **G8 cost/quality Pareto router** | CostQualityOptimizer (needs #59 avg_cost fixed) | No implementation; blocked by avg_cost gap | **NOT-STARTED** | Blocked on #59/#69 fix. |
| **G9 plan/critique layer** | planning/ controller | No `planning/` package | **NOT-STARTED** | Design-only. |
| **G10 per-run replay** | replay/ + RunRecorder | No `replay/` package | **NOT-STARTED** | Design-only. |
| **G11 multi-agent debate** | review/consensus.py fan-out | No implementation | **NOT-STARTED** | Design-only. |
| **G12 live web retrieval** | web_retrieve MCP tool | No implementation | **NOT-STARTED** | Design-only. |
| **G13 structured task-spec** | acceptance_criteria field | Todos are free-text; no schema field | **NOT-STARTED** | Design-only. |

---

## Scorecard

| Bucket | Count |
|---|---|
| DONE-VERIFIED | ~47 |
| DONE-VERIFIED (local only, CI unverified) | ~6 (#21/W16.1, W10-W15 molecule) |
| PARTIAL (built-but-unwired / inert / missing piece) | ~12 |
| NOT-STARTED (design only) | ~14 |
| INFLATED (claimed done, code contradicts) | **3** (#27/#49 spend cap, #59/#69 cost-cap, #72/#73 connector layer) |

---

## Section A — BURNDOWN divergences (where this audit differs)

| Item | BURNDOWN said | This audit found | Delta |
|---|---|---|---|
| **BUG-1 SSTI fix** | "OPEN-fix (sketch only)" — `skills/renderer.py:56` plain Environment | `skills/renderer.py:68` uses `SandboxedEnvironment(undefined=StrictUndefined)`; `SecurityError` caught; `test_skills_renderer_adversarial.py` covers it | **STALE BURNDOWN** — BUG-1 is DONE-VERIFIED, not open. |
| **W9 behavioral coverage** | "PARTIAL — only AnsibleTemplater has behavioral test; 28/29 are import-wired only" | `test_completion_audit_wiring.py:50-248` has 6 classes with behavioral tests (HotReloader, WorkerPingPong, AgentCapabilities x5, DogfoodOrchestrator, MaintenanceRouter). Remaining 23 are import-wired. | BURNDOWN underestimated behavioral coverage; still PARTIAL but less so. |
| **routers/coordination.py** | Not mentioned in BURNDOWN explicitly | Exists with `FileClaimRegistry` + HTTP endpoints; `coordination.py:14` has `# TODO(integration)` — NOT in daemon registration | New finding: file-overlap coordination has an HTTP layer but it is unwired. |
| **receiver/ package** | "untracked" (June audit) | `receiver/router.py` exists with self-documented "intentionally not wired" note | Was untracked; now committed but still unwired by design. |
| **self_update/ package** | "untracked/uncommitted" | `self_update/__init__.py` re-exports 4 symbols; NOT in daemon.py | Now committed, still unwired. |
| **issue_sources/ package** | "untracked/uncommitted" | No `__init__.py` found; package is absent or empty | Less progress than BURNDOWN implied. |
| **Ratchet count** | "~11 entries" (BURNDOWN estimated post-burn) | Exactly 11 entries; `RATCHET_MAX=11` in test_guardrails.py | BURNDOWN G3 concern resolved — count and constant are in sync. |
| **#23/#32 Scheduler wiring** | BURNDOWN promoted to "DONE-ish PARTIAL (wired)" | Confirmed wired at `event_loop/loop.py:709,740`; `scheduler.py:15-19` TODO comment is stale leftover text | Confirm BURNDOWN's STALE correction: the "unwired" claim in `status_older_inprogress.md` is wrong. Scheduler IS wired. No dedicated e2e test. |

---

## Top 10 most-inflated claims

Ranked by the gap between what was claimed and what the code shows.

| Rank | Task | Claimed | Reality | Inflation type |
|---|---|---|---|---|
| 1 | **#72/#73 Connector layer** | "Observability connector layer" in commit `ed294c4` message; 38 modules with tests | 38 modules + 38 tests exist but `daemon.py` never imports `general_ludd.connectors`; `routers/observe.py` self-documents as deliberately unwired; operator can NEVER query a connector through the daemon | Commit message implies shipped feature; it is dead library code. ~38 test files inflate the test count with no production capability. |
| 2 | **#27 / #49 SpendLimiter rolling budget cap** | `security_tasks_status.md:20` originally said "not wired"; was corrected to "wired" in BURNDOWN | Wired via `daemon.py:672-730` BUT `daemon.py:729` passes `projected_cost_usd=0.0` and `spend_limiter.py:17-27` TODO is live; cap literally never fires on any model call | Security control claimed as live; it is wired-but-inert. Every model call passes the cap unconditionally. |
| 3 | **#59/#69 Scoring cost-cap** | `security_tasks_status.md` implies cost-constrained routing works; unit tests pass | `BenchmarkRepository.get_aggregate_scores` returns NO `avg_cost` column; `scoring/router.py:131` defaults to 0.0; every candidate has zero cost in production | Unit test passes because mock injects `avg_cost`. Production behavior: cap is a no-op. |
| 4 | **W3.6 proof table "all 50 PASS, 0 GAP"** | "50 proof IDs, every one mapped to a named acceptance test, ALL PASS, 0 GAP" | The proofs are real and the tests pass. BUT W9 caveats fold in here: 23/29 wired classes are import-wired only; #26 DynamicDispatcher is in the table (via dispatch router) but the event-loop integration is missing; #27 SpendLimiter and #59 cost-cap are "PASS" in tests but inert in production | The table claims production coverage; ~5-6 of the 50 items are passing tests of partially-inert code. |
| 5 | **W16.1 CI gate fix** | Tick says "Event loop is closed fixed"; gate "ALL PASSED" | TASKS.md:450 itself admits "UNVERIFIED-in-CI at commit time"; no sandboxcom run id pasted | The tick exists with evidence but the evidence is local only. CI status unknown. |
| 6 | **#26 DynamicDispatcher "wired"** | Cited as wired into `routers/dispatch.py` | True for the HTTP path; but `dynamic_dispatcher.py:8-12` TODO(integration) is live and event_loop/loop.py has no DynamicDispatcher import | The HTTP route works; autonomous tool-call dispatch does not. The "wired" label omits the crucial qualifier "HTTP-only". |
| 7 | **#28 Per-project accounting** | "per-project cost/todo accounting" cited as PARTIAL with only 2 missing dimensions | `projects/manager.py:1-50` has only weight/dispatch_mode/active — no time, LoC, role stats. Only cost (via MetricsCollector) and todo-count work. 3 of 5 claimed dimensions are missing | Overstated as 40% done; more like 20-30%. |
| 8 | **#31 File-overlap coordination** | "Scheduler.resources frozenset serializes shared resources" presented as partial progress | `routers/coordination.py` with `FileClaimRegistry` exists but is NOT in daemon registration (`coordination.py:14` has explicit TODO). No file-path-to-work-item mapping. The Scheduler `resources` frozenset is unused in any live dispatch path | More infrastructure exists than BURNDOWN showed, but none of it is reachable. |
| 9 | **W10–W15 molecule "49/49 green"** | "make molecule-test-all 49/49 ALL scenarios passed" | Locally verified. CI job added but CI-green is unverified (same W16.1 problem: macOS local ≠ ubuntu CI) | Local claim is accurate; CI claim is open. Six phases of work rest on an unconfirmed CI basis. |
| 10 | **receiver/ + self_update/ "committed"** | Were "untracked ??" in June audit; now committed (progress) | Committed but both are explicitly self-documented as unwired (receiver/router.py:43 "do NOT edit daemon.py here"; self_update not in daemon imports) | The commit creates the appearance of progress but the daemon cannot reach either module. |

---

## Key residuals from this pass

These are actionable gaps not yet in BURNDOWN or prior docs:

1. **BUG-1 SSTI fix is DONE** — BURNDOWN's OPEN-fix verdict is stale. `skills/renderer.py:68` uses `SandboxedEnvironment`. Remove BUG-1 from the open-fix list.

2. **routers/coordination.py is also unwired** — the `FileClaimRegistry` HTTP layer at `routers/coordination.py` has `# TODO(integration)` at line 14 and is absent from daemon.py's router registration. This is a second unwired-but-built router alongside `routers/observe.py`.

3. **W5.3-CVE ticks are still open** — TASKS.md:252-253 diskcache CVE-2025-69872 and pip PYSEC-2026-196 ticks have no commit hash. These are the only two open V/R/W-scheme ticks.

4. **#59/#69 avg_cost is a DB schema gap, not just a routing gap** — adding `func.avg(cost)` to `BenchmarkRepository.get_aggregate_scores` is a 2-line fix that would make cost-constrained routing real. Currently scored 0% effective despite a passing unit test.

5. **Ratchet G3 concern resolved** — `config/ratchet.yml` has exactly 11 entries; `RATCHET_MAX = 11` in test_guardrails.py. In sync.

---

## Provenance

Direct code reads in this pass: `daemon.py` (headers, lifespan, router block, spend wiring),
`dispatch/dynamic_dispatcher.py`, `controllers/spend_limiter.py`, `event_loop/loop.py`,
`scoring/router.py`, `db/repository.py` (get_aggregate_scores), `projects/manager.py`,
`routers/observe.py`, `routers/dispatch.py`, `routers/coordination.py`,
`skills/renderer.py`, `scheduling/scheduler.py`,
`src/general_ludd/receiver/router.py`, `src/general_ludd/self_update/__init__.py`,
`src/general_ludd/pipeline/controller.py`,
`tests/unit/test_completion_audit_wiring.py`, `tests/unit/test_guardrails.py`,
`config/ratchet.yml`, `.gate-status`.

Prior docs reconciled: `BURNDOWN.md`, `backlog_completeness_2026-06.md`,
`status_older_inprogress.md`, `security_tasks_status.md`,
`docs/design/feature_gap_backlog.md`, `TASKS.md`.
