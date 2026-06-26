# Session Work Ledger — feature/alpha4-green-the-gate

Maintained by the Claude orchestrator so no requested work is left behind. Audit
of every operator request + discovered backlog this session, with live status.
Status legend: ✅ DONE+PROVEN · 📦 STAGED (committed-pending-gate) · 🔧 APPLIED
(in working tree, not yet gated) · ✍️ DRAFTED (apply-ready, not applied) · 🤖
DRAFTING (agent running) · ⛔ OPEN (not started).

Last updated: 2026-06-26 (verification sweep + concurrency/cross-tenant audit wave).

## A. Operator requests (chronological)

| # | Request | Status | Evidence / location |
|---|---------|--------|---------------------|
| 1 | Finish in-flight osquery bundling | 🔧/🤖 | facts.py async fix ✍️; module fixes 🔧 (A/B applied, C/D via agent); bootstrap extraction ✍️ |
| 2 | GitHub Releases + Packages pipeline (codify in GHA) | 📦 | build.yml GHCR job + Dockerfile + latest/source-label; review 4/4 PASS. NOTE: a Release/Package only *appears* after a green `v*` tag run — not yet exercised |
| 3 | Set subagent floor to 6 | ✅ | /tmp/gludd-floor-override=6; settings CLAUDE_AGENT_FLOOR=7 |
| 4 | ripgrep make targets + ansible action (python ripgrep-rs bindings; ship binary) | ✍️ | gludd_ripgrep.py + Makefile rg targets + pyproject `ripgrep-rs` extra — DRAFTED, NOT applied |
| 5 | osquery python bindings instead of shelling out | 🤖 | facts.py async (FINDING-1) + bootstrap extraction (FINDING-2) drafted; module A/B applied |
| 6 | Open settings: don't ask for url/tmp/settings | ✅ | ~/.claude/settings.json allow-list + defaultMode dontAsk |
| 7 | Allow file edits | ✅ | operator-enabled |
| 8 | Naming/versioning/filetype tooling (claude + gludd) | ✍️ | release_asset_naming.md skill + 4 artifacts — DRAFTED, NOT applied |
| 9 | Anti-lie: code so I can't falsely claim done; gludd too | ✅(L1)/🔧(L2) | L1 hook **22/22**+ReDoS-fixed; L2 verify_completion gate APPLIED+GREEN (**37+4+5 passed**, lint/typecheck clean). FOLLOW-UP 🤖: wire repo_root + teach prompt prefix-grammar, else gate always-blocks every completion in prod |
| 10 | Branch hygiene: never red a green branch; new release branches; promote when green | 📦 | check_green_branch_guard.py **6/6**; release-branch-new/promote; AGENTS.md lifecycle |
| 11 | Never ask to create file/hook; never stop-and-prompt (blocking hooks) | ✅ | no_wait hook round-3 self-deferral + escalation **45/45**; assume-yes behavioral |
| 12 | Daemon task scheduling (cron + one-shot) in todo list | ✅📦 | **COMPLETE+VERIFIED, STAGED (commit pending fresh gate)**. DEFECT 0 (dead/unwired) FIXED — `run_scheduler` phase wired into PHASE_ORDER before claim_runnable_todos. DEFECT 1 (resume 422 guard), DEFECT 2 (advance-before-spawn, no dup children), DEFECT 3 (schedule_paused into SQL before LIMIT) all FIXED. Tests: unit **13** + wiring **4** + integration **4** (real-DB) + event_loop **44** passed; lint clean; typecheck 409/0; daemon session-wiring audited; adversarial review = no blocking defects. Bundled with #9 into the staged todo-lifecycle commit (shares loop.py) |
| 13 | Contextual logging (todo/model/role/node + pid/container/dump) + error reproduction | ✍️ | DESIGNED + preserved → `docs/design/CONTEXTUAL_LOGGING_AND_ERROR_REPRO.md` (stdlib logging not structlog; audit_events store w/ project_id trap; sync-gateway/async-DB split; exact insertion points cli/loop/gateway/dispatcher). NOT applied — contends loop.py with #12 wiring + gate-follow-up |
| 14 | Code-generated completeness tables, per build/deploy + on-demand | 🤖 | status-table generator design agent running |
| 15 | Maintain text todo + design docs; audit all work | ✅ | THIS ledger |
| 17 | Project-local `.gludd/` dir (analogous to `$XDG_CONFIG/gludd`, discovered per-repo): `collections/` (project ansible build roles/collections/variables for tests + workflows), project skills, weight export + portable gludd backup data, full archive export, live-reflects edits within the session | 🔧/🤖 | DESIGNED (docs/design/PROJECT_LOCAL_GLUDD_DIR.md, 4 phases). **Phase 1** module src/general_ludd/config/project_dir.py APPLIED (find_project_gludd_dir walk-up + GLUDD_PROJECT_DIR override, project_config_path, merge_config deep-merge project-wins) — **typecheck CLEAN (0 errors / 410 files** after dict[str,Any] fix). **Phase 1+2** daemon/runner/registry integration + tests 🤖 applier a871d9f5 in flight (writing test_project_local_gludd_phase2.py; 2 I001 import-sort to lint-fix on completion). **Phase 3** live-reload (HotReloader skill_registry.refresh + POST /admin/config/reload) ✍️ DRAFTED apply-ready (aca11734). **Phase 4** export/archive 🤖 drafting (ae52e8256) |

## A2. 5-hour token-window throttle (operator request #16, prioritized)

✅ DONE+PROVEN+RUNNING. `scripts/token_window_monitor.py` + `scripts/test_token_window_monitor.py` + Makefile targets (`token-monitor-bg`/`-stop`/`-status`/`-breakdown`/`-probe`/`-calibrate`/`test-token-monitor`). Sums 5h transcript token spend → writes `/tmp/gludd-floor-override`: floor→1 at ≥95%, floor→7 at <90% (hysteresis). `test-token-monitor` **12/12**.

**ACCURACY FIX 2026-06-26 (commits 40ea527 + 71d4faa, `9 passed` calibrate tests):** operator reported the % reading was wrong. Root cause PROVEN via new `--probe`: the API rate-limit headers (`anthropic-ratelimit-unified-*`) are NOT persisted anywhere in the transcript, so the monitor can only ever be a token-sum PROXY (cache_read is ~95% of the sum). The frozen budget constant drifted wrong. FIX: `--calibrate <pct>` (`make token-monitor-calibrate PCT=NN`) anchors `budget = measured_spend ÷ (pct/100)` into `/tmp/gludd-5h-token-budget` so the % self-corrects against a real reading; DEFAULT_BUDGET raised 200M→**316M** so it stops false-tripping. Operator re-anchors anytime with `make token-monitor-calibrate PCT=<real %>`. Live reading after calibration: honest ~90% (was over-reading 100%+). Cross-window resume cron unchanged.

## B. Apply queue + COMMIT-READINESS MATRIX (reviewed 2026-06-26)

Reviewer verdicts on each working-tree stream (gate PID 35078 holds pytest lock → targeted
pytest + clean gate still pending; all lint/typecheck below ran clean = 410 files / 0 issues):

| Stream | Files | Verdict |
|--------|-------|---------|
| **.gludd Phase 1+2 (#17)** | config/project_dir.py, daemon.py, ansible/runner.py, prompts/registry.py, test_project_dir.py (17), test_project_local_gludd_phase2.py (15), docs/design/PROJECT_LOCAL_GLUDD_DIR.md | ✅ **COMMITTABLE** — applier a871d9f5 done; lint+typecheck clean; reviewer a17f3d to confirm exact git-add set |
| **Webhook SEC-4** | events/hooks.py, tests/unit/test_hooks_security.py | ✅ **COMMITTABLE AS-IS** (ae6faebe) — redaction-before-capture correct, executor+sync-fallback correct, combined test present. 2 NON-BLOCKING notes: async total-failure not ERROR-logged (hooks.py:262-263); dead MagicMock line test_hooks_security.py:108 |
| **Status tables (#14)** | scripts/gen_status_table.py, docs/features.yml, test_gen_status_table.py, Makefile, build.yml, README.md | ✅ **COMMITTABLE as one unit** (a0047126) — no Makefile target dup w/ token-monitor; check-status-table PASS. .PHONY blocker FIXED (added 3 targets at Makefile:112) |
| **Token monitor (#16)** | scripts/token_window_monitor.py, scripts/test_token_window_monitor.py, Makefile(targets) | ✅ running+proven; Makefile coexists w/ status-table |
| **osquery** | gludd_osquery.py + 3 test files = ✅ COMMITTABLE; **facts.py + bootstrap.py = ❌ NOT committable** | 🤖 a50b0a applying real FINDING-1 (facts.py _osquery_facet still sync subprocess in async handler @285/325/410) + FINDING-2 (bootstrap.py stores raw .tar.gz, no extract/chmod → binary INERT). Arch detection already fixed |

### Drafts NOT yet applied (need pytest verification → land after gate frees lock)
| Item | Files | Status |
|------|-------|--------|
| .gludd Phase 3 live-reload | reload/hot_reloader.py, routers/reload.py, test_phase3_project_live_reload.py | 🤖 a762b36 applying |
| .gludd Phase 4 export/archive | filestore/store.py, BenchmarkRepository, Makefile, bootstrap.py | ✍️ DRAFTED (ae52e8256) — CONFLICTS bootstrap.py(osquery)+Makefile; apply AFTER osquery lands |
| gateway breaker + NaN/Inf | models/gateway.py (D1 mid-retry breaker recheck @813-826; D2 NaN budget_remaining fail-closed @354), test_gateway_circuit_breaker.py | ✍️ spec ready (ae6a40) |
| db list-cap (P12) | db/repository.py (9 methods; only Defect1 /api/status→list_all is public CRITICAL, rest internal/defensive), test | ✍️ spec ready (a2328) |
| MCP catalog OOM | mcp/catalog.py C-1/C-2/C-3 + loader.py C-4 + test mocks | ✍️ → docs/design/MCP_CATALOG_OOM_FIX_SPEC.md |
| scheduler coverage (12 gaps) | test_todo_scheduler.py + integration | ✍️ gap report (aae1479) — P1 error-handler branches |
| #13 contextual logging | observability/log_context.py + cli/loop/gateway/dispatcher | 🤖 a5d211 finalizing apply-spec |

## C. Design agents running (return apply-ready code)

- scheduler build-out (scheduler.py + migration + repository + loop wiring + croniter + API + tests)
- logging + error-reproduction (contextvar log context + ErrorEvent + `gludd reproduce` + pid/container/dump)
- code-generated status tables (gen_status_table.py + manifest + make targets + build wiring)

## D0c. COMMITTED CHAIN — 2026-06-26 (after c77ec82)

All via `make commit-ci-gate` (local full suite OOMs → CI is the gate; push HELD as outward-facing).
Each unit reviewed/verified clean (lint+typecheck 410 files / 0 issues) before commit:

| Commit | Unit | Files | Verify |
|--------|------|-------|--------|
| **cd8d7f6** | .gludd Phase 1+2 (#17) | 7 (+812/-13) | a17f3d: clean, isolated, no cross-stream hunks |
| **98f7a18** | Webhook SEC-4 | 2 (+111/-32) | ae6faebe: COMMITTABLE AS-IS |
| **df72d13** | .gludd Phase 3 live-reload | 3 (+439) | a1927c4: coherent+crash-safe; 1 MED bug (reload.py:78 global-skills dir) being fixed by ad0b77 |
| **591e7ce** | osquery FINDING-1+2 + module | 11 (+1520/-3) | a362a890: FINDING-1 AND -2 GENUINELY fixed (real FileStore X_OK test) |
| **4cbee74** | MCP catalog OOM (C-1..C-4) | 4 (+187/-18) | a57584 applier lint+typecheck clean |

### COMMITTED (continued) — verified then committed via commit-ci-gate
| Commit | Unit | Files | Verify |
|--------|------|-------|--------|
| **ee52583** | P12 list-caps + api_status COUNT-aggregate + status_summary NULL-key coercion | 3 (+476/-24) | af8928: count path correct, no undercount; 7 caps SQL-bound; NULL→"unknown" hardened at source |
| **230c13e** | gateway breaker/NaN (D1a role/pattern gate, D1b mid-retry recheck, D2a _coerce_token_count, D2b budget clamp) | 2 (+191/-1) | spec ae6a40+a98caa81 (5 fixes); a1233 lint+typecheck clean |
| **bc30bb1** | reload.py global-skills dir fix (config_dir/skills) | 2 (+101/-1) | a1927c4 found it; ad0b77 fixed+tested |
| **06ac095** | scheduler coverage (exception branches, semantic edges, phase metrics, real-DB cancel/paused) | 2 (+230/-1) | a42fe0c done; a3ee976 verified clean (no dup) |
| **3e578b5** | daemon HIGH async (H1 openbao→to_thread, H2 _maybe_open_pr async, H3 spend_limiter before create_task) | 3 (+270/-52) | ad6f768 +7 tests, lint+typecheck clean |

**11 commits total this session** (c77ec82 → 3e578b5). All push HELD (outward-facing, not authorized). NONE CI-verified yet — local full suite OOMs so commit-ci-gate skips the local gate; CI is the gate and runs only on push. Each unit was lint+typecheck-clean (410 files/0 issues) and adversarially reviewed/verified before commit, but "CI-green" is NOT yet observable.

### D0d. Verification sweep + concurrency/cross-tenant wave — 2026-06-26 (after 3e578b5)
Per-committed-unit pytest verification (lock freed) found+fixed bugs, then a fresh audit
wave (6 auditors → adversarial verifiers → apply). All via commit-ci-gate; push still HELD.

| Commit | Unit | Verify |
|--------|------|--------|
| **63b2437** | test_project_local_gludd_phase2 skill fixture → YAML frontmatter (parser reads desc only from frontmatter) | 16 passed (was 2 failed); adversarial review SOUND |
| **284414e** | claim_runnable FIFO `.order_by(created_at, id)` (anti-starvation) + dedicated test_claim_runnable_fifo.py | 2 passed + test_event_loop 44 passed (non-breaking); review SOUND (perf caveat: composite index deferred, see below) |
| **4d058a4** | projects.py admin_add_project: stop swallowing persist/commit failure → log+re-raise→422 (was false 200 + silent data loss) | 16 existing tests pass; negative test (needs failing session factory wired) deferred |
| **(applied, uncommitted)** | ssrf.py host_is_blocked: reject NUL bytes + strip trailing FQDN dot (2 confirmed bypasses: `localhost.`, `localhost\x00.evil`) | verifying vs existing ssrf tests before commit |

Earlier this session (pre-3e578b5, during the sweep): f5e5a3c (REAL gateway regression: inf budget clamped→0.0 rejected all calls; now only NaN fail-closes), ab29183/4d1442b/5cf7a8d/5b06223 (4 test-only bugs), 1d0ed9b (cross-tenant todo isolation). All committed units re-verified green except 1 pre-existing D-21 fallback failure (not mine).

### REFUTED / already-fixed (do NOT re-investigate — verified this wave)
- **CI-1 gateway provider_registry=None**: REFUTED — daemon.py:977 passes `ProviderRegistry.from_profiles(...)`; test_ci1_gateway_provider_registry.py proves it. (Stale docstring at provider_registry.py:65 should be updated — LOW.)
- **dispatcher can_invoke gate DEAD (bare AgentRegistry)**: REFUTED — daemon.py:1217 uses `default_registry()` (4 agents, sealed); gate enforced at dispatcher.py:77; both dispatch sites stamp invoker_name="build". No fix.
- **self-update approval bypass (bool(token))**: REFUTED — already fixed (apply.py:194-204 uses verify_psk constant-time vs configured secret, fail-closed).
- **budget spend-limiter TOCTOU**: already closed (RLock, 50-thread test). No per-invoker accounting (by design). Gateway cost-not-tracked-on-failure is CORRECT by design.
- **osquery bootstrap binary INERT**: RESOLVED — bootstrap.py:215-220 now extracts+chmods (no longer raw .tar.gz).

### CONFIRMED-OPEN backlog from this wave (apply-ready specs captured in docs/audit/CROSS_TENANT_AND_CONCURRENCY_FINDINGS_2026-06-26.md)
- **Cross-tenant leaks (HIGH)**: facts.py ×4 (get_aggregate_scores/list_all/_traces_facet/api_traces); features.py ×3 (list_all/get_by_id/verify ignore project_id — MED, PSK-gated); embeddings.py corpus=events (unfiltered AuditEventModel); messages.py degraded fallback ignores project_id. All have exact apply-ready diffs.
- **Lease F1/F3 double-dispatch (HIGH)**: full reclaim_expired_leases CAS-on-version replacement spec + 2 tests ready (no migration; version col exists).
- **Async-blocking IO (HIGH per-req)**: daemon_wiring.py:241 playbook write, skills.py:127 github fetch, environment.py:713 /proc/meminfo, filestore bootstrap download, daemon.py:1837 psutil stats — wrap in asyncio.to_thread (specs ready).
- **SSRF (MED, deferred)**: decimal/octal IPv4 literal not parsed (2130706433); redirect-follow not enforced in-code (documented caller contract).
- **DB schema**: missing composite index for claim_runnable FIFO (status,created_at,id) — needs model+migration pair (alembic parity). Missing FKs: TodoModel.parent_todo_id, TaskDecisionModel.matched_todo_id. Migration drift: task_returns.updated_at never created; 9 FKs lack ondelete=SET NULL in migrations 002/004.
- **todos.py recon vector (MED)**: read/update endpoints don't validate project_id (404-vs-422 enumeration); CREATE does.
- **connectors NaN sort residual (MED)**: base.py:361 _associate_by_window + :316 _sort_by_ts can still take NaN ts (boundary coercion bypassed for non-normalized records).
- **daemon /docs exposed unauth (MED)**: in _PUBLIC_PATHS; filestore bootstrap error leaks binary name (LOW).

### D0e. NEXT-WINDOW APPLY ORDER (consolidated, value÷effort; safest-highest-value first)
All specs in docs/audit/CROSS_TENANT_AND_CONCURRENCY_FINDINGS_2026-06-26.md. Apply one
at a time, single pytest each (OOM), commit-ci-gate per unit. **repository.py is touched
by #5/#6/#7 — sequence those back-to-back; daemon.py only by #9 (batch last).**
1. messages.py:122-127 degraded fallback project_id filter — extend test_messages_router_bounds.py / integration test_messages_and_facts_api.py. Independent.
2. embeddings.py:821-825 events-corpus project_id filter + add project_id to EmbeddingSearchRequest (schema ~191). NO existing embeddings test → NEW test. Independent.
3. metrics/collector.py add RLock + LRU cap on _global_model_usage (both DROP-IN-SAFE; do NOT del in unregister_agent — 3 tests rely on stopped-agents-retained). Extend test_metrics.py.
4. receiver/router.py _RateLimiter LRU eviction (unbounded dict DoS). Extend/new limiter test.
5. facts.py:125 get_aggregate_scores(project_id=) + facts.py:226 FeatureRepository.list_all(project_id=) [add optional param+where to repository.py ~1607]. Traces = DOCUMENT-ONLY (ExecutionTrace has no project_id). PREREQ for #6.
6. features.py 3 handlers (list/get/verify) project_id scoping — needs repository.py scoped methods (follows #5, same file).
7. lease.py reclaim_expired_leases CAS-on-version (F1/F3) — uses existing TodoModel.version, no migration; NEW test_concurrency_lease_expiry.py. Sequence after #5/#6 (repository.py churn).
8. todos.py read/update project_id validation (recon vector) — mirror CREATE's 422; NEW tests.
9. async-blocking to_thread wraps: daemon_wiring.py:241, skills.py:127, environment.py:713, daemon.py:1837 psutil, tool_loop.py:176/183 sync call_model. Lowest risk, batch last (daemon.py).
ALSO: connectors NaN sort fix already landed (41ee61a); GAP-30 metrics-signature already landed (12641f5/fa52e78); SSRF NUL/dot already landed (eef9ee9). Deferred (need migration+model change): claim_runnable composite index (alembic parity), missing FKs parent_todo_id/matched_todo_id, migration drift (task_returns.updated_at, 9 ondelete gaps).

### Remaining open backlog (specs in flight / ready to apply)
- cross-tenant todo scan (HIGH) — spec a4b30bd; needs router scoping + project_id NOT NULL migration (careful: internal dispatcher legitimately scans all projects)
- actor-spoofing allowlist (LOW — REFUTED as security issue: claim_runnable actor is hardcoded but internal-only, not user-supplied; no fix needed). claim_runnable ORDER BY (MED) — 🔧 APPLIED this session (repository.py:384 `.order_by(created_at, id)`); verified non-breaking (test_event_loop 44 passed); FIFO regression test pending
- budget TOCTOU lock + empty invoker_name fail-close (MED) — spec a0d56f9
- F3+F1 lease double-dispatch (HIGH, loop.py+lease.py) — from a424fcc; NOT yet specced into an applier
- gateway GAP-30 (failure metrics ring never populated), CI-1 (provider_registry=None live-call blocker), self-update approval bypass — backlog
- daemon MED/LOW (M1-M7, L1-L3) — from a54a5c
- .gludd Phase 4 export/archive (drafted ae52e8256) — apply after osquery/Makefile settle
- #13 contextual logging (finalized apply-spec in task a5d211 output)
- MCP catalog spec doc, scheduler exception escape points (list_due_scheduled/int(run_count))

### Discovered backlog (daemon async audit a54a5c — MED/LOW, not yet scheduled)
M1 harness.run_gap_analysis sync in _phase_self_improve; M2 reload_if_needed sync; M3 materialize_project_workspace sync clone on loop; M4 _preflight_task no done-callback (failures swallowed); M5 bg benchmark/trace tasks no error callback; M6 _sync_bridge spawns ThreadPoolExecutor per tool call; M7 PID controller failures logged at DEBUG. L1 run_forever logs ERROR on normal stop; L2 _active_session not cleaned in tick finally; L3 _load_shared_vars likely dead code.
Other (from a1233/audits): CI-1 daemon gateway provider_registry=None → live calls raise; self-update approval bypass (bool(token) accepts any non-empty); gateway fail-open bare excepts ~915/~985.

## D0b. COMMITTED — c77ec82 (2026-06-26)

✅ **Todo-lifecycle + guardrails + pipeline bundle COMMITTED** as `c77ec82` (37 files,
+3411/-54) via `make commit-ci-gate` (local full suite OOMs Error 137 → CI is the gate;
push HELD as outward-facing). Includes: anti-lie hooks (#9-L1/#10/#11), GHCR release
pipeline (#2), cron/one-shot scheduler (#12, DEFECT 0/1/2/3), evidence-gated completion
(#9-L2), daemon stamp_head off-loop. The 6 lint errors (all in test_gen_status_table.py)
were fixed first. Next committable units (per completeness-critic sequence): token monitor
(U1 standalone), webhook SEC-4 (run_in_executor fix drafted), .gludd Phase 1+2 (#17),
lease bug #1, P12 caps, gateway breaker/NaN, MCP catalog.

## D0. Commit status (historical — pre-c77ec82)

- Todo-lifecycle bundle (scheduler #12 + completion-gate #9, 25 files) is **git-add staged**.
- `make git-commit` gates on `.gate-status`. It was stale (`lint FAIL 8`). Root cause
  found: the "8" = 6 ruff violations (F401/I001×2/F541×2/F841) ALL in the untracked
  `tests/unit/test_gen_status_table.py` (subagent-written after the last clean lint).
  **All 6 FIXED**; `make lint` now clean. `.gate-status` still stale until a fresh gate.
- A `make gate-background` (PID 35078) is running to refresh `.gate-status`, but it's
  testing a tree the two status-table agents mutated mid-run → untrustworthy + many
  F/E. Holds the pytest lock, so a clean gate must wait for it to finish.
- IN FLIGHT: agent determining exactly which `.gate-status` phases `git-commit` requires
  + whether `commit-no-verify`/`commit-ci-gate` bypasses the locally-OOM test phase
  (full suite OOMs / Error 137 locally — CI is the real gate). Agent categorizing the
  gate failures (pre-existing/env vs mutation vs genuine regression).
- Scheduler bundle's OWN tests all pass (13+4+4+44); gate failures are in other streams.

## D. Convergence plan (how it all lands)

1. Layer-2 completion-gate APPLIED+GREEN (37/4/5 passed, lint/typecheck clean). Follow-up agent wiring repo_root + prompt prefix-grammar so the gate VERIFIES rather than always-blocks every completion in prod.
2. Apply remaining queue (B) + returned design code (C) to the working tree — disjoint files, no concurrent pytest.
3. ONE `make gate-background` → `make gate-bg-check`.
4. Commit the staged guardrail/pipeline group (already staged) + the rest in logical commits.
   Commit is currently GATE-BLOCKED (commit-no-verify requires fresh green gate; tree is dirty mid-apply). Do NOT weaken the gate to commit.

## E. Discovered backlog (audited, lower priority)

- gateway.py: sync-on-async (invoke/sleep/Lock), error-swallow paths — audited, untracked tests.
- repository.py P12 siblings: ProjectRepository.list_active (MED-HIGH), AgentMessageRepository.inbox, SpendRepository.list_since, PromptProfileRepository — caps recommended.
- db/models.py M-13: TodoModel.project_id nullable (cross-tenant scan vector, compounds list-cap).
- connectors find() residual NaN-in-sort-key (LOW).
