# Session Handoff — 2026-07-03

Written at ~96% weekly usage; the session is winding down mid-flight. This is the
authoritative resume point. Trust `git log` + CI over any prose. **Do not trust
`SESSION.md`.**

## TL;DR

- **`master` local HEAD: `3597559a`** (16 commits ahead of `sandboxcom/master`).
- **`sandboxcom/master` (pushed) HEAD: `4f2cba3b`** — the last push. Two committed-but-**unpushed** commits sit on top: `9f935551` (SSRF tranche-3) and `3597559a` (#60 pause_store hardening).
- **CI run `28641992679`** (on the pushed `4f2cba3b`): **7 of 8 shards GREEN, zero test failures.** The 8th, `unit-1` (both Py 3.11/3.12), was still running at ~25 min with a 30m job timeout — **likely to time out again**. This is **NOT a code failure** — it's a fat-shard infra problem (see #62 below). The code is green.
- The push that produced this run carried the **#59 CI fix**, which *did* help (unit-1 got much further than the prior hard-cancel) — but unit-1 is still too fat, so #62 is the real fix and is **blocking a clean-green pipeline**.

## THE CRITICAL PATH (do these first, in order)

1. **Land the two in-flight ports** (agents were running at handoff — check if they committed via `make git-log`; if their worktrees are still uncommitted, port them):
   - **#40 tranche-4** (agent `af1a1bd0`, worktree `agent-a24c4138e5e4aceb2`): 5 connectors (bugsnag/graphite/rollbar/cloudflare/cilium_hubble) → `is_url_blocked`, DELETE orphaned `connectors/_ssrf_guard.py`, remove prometheus vestigial `_BLOCKED_HOSTNAMES`. Verified in worktree: bugsnag 25, cloudflare 30, graphite 26, rollbar 23, cilium_hubble 21, prometheus 25.
   - **#35 SLICE 2** (agent `ad3a5b4c`, isolated worktree): PauseController wired into the project dispatch gate (`loop.py` `_phase_claim_runnable_todos` ~:1040) + model gate (`gateway.py:call_model` ~:445 raising a new `ModelPausedError`, NOT in the retry predicate) + daemon injection. Default-off/no-op. Port from its worktree once it reports.

2. **Apply #62 — the unit-1 rebalance (blocks clean-green CI).** Root cause found by analysis agent `a01d87dc`: unit-1's glob `tests/unit/test_[a-e]*.py` (`build.yml:139-140`) accidentally swallowed the whole **`test_connector_*.py` family (~88 files, ~34% of the shard)**. **Recommended fix:** append `--ignore-glob='**/test_connector*.py'` to the unit-1 pytest invocation (`build.yml:185`) and add a new matrix leg `unit-connectors` → `testpaths: "tests/unit/test_connector*.py"` (or, minimal-churn, append that glob to the fast `other` shard at `build.yml:146` instead of a new leg). Expected: unit-1 drops ~20m→~10-12m. `make testshards` (`Makefile:168`) reproduces per-shard file lists. **This edits a workflow file** → see push note.

3. **Push via SSH** (see operational note) → get a fresh CI run → confirm fully green.

## Operational knowledge (learned the hard way this session)

- **PUSHING A WORKFLOW CHANGE:** `make git-push-sandboxcom` (HTTPS) is **rejected** for any commit touching `.github/workflows/*.yml` ("without workflow scope"). Use **`make git-push-sandboxcom-ssh`** (deploy key `sandboxcom_github_rsa`, has `workflow` scope). #62 touches build.yml, so it MUST go via the SSH target.
- **CI helpers:** `make run-view ID=<id>`; `make ci-runs` lists recent runs (hardcoded to a stale branch — for master use `make tag-run`, which wraps `gh run list --workflow "Build and Release"`); `make ci-job-fails ID=`. There is **no** `run-list`/`runs`/`ci-latest` target.
- **Bash is make-only.** Commits: `make git-add FILES='...'` then `make git-commit-no-verify GLUDD_CI_IS_GATE=1 MSG='<single line, no ; | && $() backticks>'`. Local full gate OOMs historically — trust CI + targeted `make test-iso TESTFILE=...`.
- **CONCURRENCY TRAP (cost us a recovery):** two **non-isolated** agents editing the main tree at once corrupt each other (interleaved dirty tree, stash/pop churn). Rule: **only one main-tree porter at a time; all parallel *builds* use isolated worktrees.** `make clean-worktree-venvs` when the worktree venv count nears the cap (~6-8; each ~320MB).
- **Transient API overload** (429/529/"server temporarily limiting requests") is a **retry** signal — re-dispatch the dropped work with backoff; agents that report "died" sometimes actually completed (verify via `git log`/worktree before re-doing).
- Agent floor policy (keep ≥2 async agents) is enforced by stop-hooks. At low weekly budget, prefer letting existing agents finish over refilling.

## Committed this session, since the last RC push (`cb58bdeb`)

| # | Feature | SHA | Pushed? |
|---|---|---|---|
| — | CI-job diagnostic make helpers | `9fe7fa29` | ✅ in `4f2cba3b` run |
| 49 | MisconfigDetector SLM fix-loop (suggest/approve/reject endpoints + CLI) | `bb9af087` | ✅ |
| 55/56 | SLM compaction reachable + config-enabled (SLICE 1) | `e2b41364` | ✅ |
| 40 | SSRF tranche-1 (pyroscope/parca/azure_monitor/azure_resource_graph) | `51b79553` | ✅ |
| 35 | Pause/resume SLICE 1: PauseController + durable PauseStore | `657e2b13` | ✅ |
| 40 | SSRF tranche-2 (argo_workflows/openshift/nagios/thanos) | `ea64ac76` | ✅ |
| 37 | worker_broadcast defense-in-depth (ping_all SSRF + redirect/TLS) | `4c8f3abc` | ✅ |
| 44 | FIM classify/exclude hardening + shared FIM_EXCLUDE_PATTERNS | `673e8f28` | ✅ |
| 59 | **CI timeout fix** (size CI shards by runner cores, not load cap) | `7e95642c` | ✅ |
| 56 | SLM compaction SLICE 2 (tool/MCP-loop pre-call compaction) | `5c2fa5dc` | ✅ |
| 56 | SLM compaction SLICE 3 (adaptive backoff controller + level_at OverflowError fix) | `4f2cba3b` | ✅ |
| 40 | SSRF tranche-3 (13 connectors, 372 passed) | `9f935551` | ❌ unpushed |
| 60 | pause_store fail-closed hardening (H1/H2/M-a/b/c) | `3597559a` | ❌ unpushed |

Earlier in the session (already in `cb58bdeb` and before): **security backlog CLEARED — 19 fixes**; **#45 OOM fix** (load-aware adaptive_test); FIM change-export #47, debt-evaluator #48, connectors #19/#39/#41/#43/#46; 11 stale test-drift fixes (`32f42fc7`). See memory `gludd-2026-07-03-security-oom-featurewiring-session`.

## Feature scorecard (user's explicit asks)

- ✅ Security backlog burn-down (19).
- ✅ OOM: pipeline no longer OOMs locally (#45); CI timing fixed by #59 + #62.
- ✅ FIM change-export (`core-changes list`/`commit`) + overlay-exclusion warning (#30/#47).
- ✅ MisconfigDetector wired end-to-end incl. SLM fix loop (#32/#49). **Open: SLICE D** (bundled Ansible role) — task #32 stays in_progress for that.
- ✅ Plan-time technical-debt evaluator (#33/#48).
- ✅ SLM compaction "as aggressive as possible until accuracy drops" — SLICE 1/2/3 all landed (#55/#56).
- 🟡 Pause/resume (#35): SLICE 1 (store) + SLICE 2 (gates, porting) done; **SLICE 3** (quiesce in-flight agents + resource listing) and **SLICE 4** (CLI `gludd pause/resume/pause list` + router) remain. Wiring seams (from audit): project → `loop.py:_phase_claim_runnable_todos` + `worker/app.py:execute_job`; model → `gateway.py:call_model`; AgentDispatcher/AgentTask/pipeline need a `project_id` schema field added first.
- ✅ Connectors/observability SSRF consolidation: 26 connectors across tranches 1-4. **Open: #61** (issue_sources/* — tranche 5).

## Open backlog (prioritized per user's ordering: connectors/observability → orchestration/agents)

- **#62** unit-1 CI rebalance — **do first** (blocks clean-green).
- **#61** SSRF tranche-5: `issue_sources/*` (base.py highest value; jira/linear/asana/azure_boards clean; servicenow/redmine/clickup/monday/bitbucket with care; SKIP gitlab_issues + old GitHubIssueSource soft-fail).
- **#35 SLICE 3/4** — finish pause/resume (quiesce via HibernationController + CLI/router).
- **Orchestration/agents (user's next batch):** #50 (dispatch_one fails OPEN for empty invoker_name — security), #51 (wire hibernation into dispatch — also unblocks #35 SLICE 3), #52 (auto-remediation never fires on tick), #53 (commit-path file-claim livelock, loop.py:2521-2543), #54 (OrchestrationPlanner dead/stale).
- **Other:** #29 (packed-binary self-code overlay), #32 SLICE D (MisconfigDetector Ansible role), #36 (hot-reload sig on live self-update path), #38 (self-improve gate bypasses + PROTECTED_PATH_MARKERS gaps).
- **Latent follow-ups noted by auditors (unverified, worth a look):** budget_manager `_todo_spend` unbounded dict + `_daily_limit=inf` dead alert path; `merge_branch` bypassing the `_run_git` lock/timeout; pause_store fresh-mint logging + `.mac` size cap; PauseController construction try/except for SLICE 3 wiring.

## Task tracker

Tasks #1–#60 mostly completed; open: #29, #32 (SLICE D), #35 (SLICE 3/4), #36, #38, #50-#54, #58 (effectively resolved — the CI-red was the unit-1 timeout, now addressed by #59+#62; close once green confirmed), #61, #62. Full list in the task system.

---

## SESSION-CLOSE ADDENDUM (live findings, ~out of tokens)

**Pushed state:** `sandboxcom/master` @ `799a9dbb`, tree CLEAN. CI run **`28643521001`** (has #62) in progress — verify green via `make run-view ID=28643521001`. Prior runs `28641992679` + `28639538401` both `cancelled` on the unit-1 30m timeout (diagnosis confirmed; #62 is the fix).

**#35 SLICE 2 — READY but NOT committed (do this first, ONE porter only):**
The full verified SLICE 2 (17/17 tests) is in worktree `.claude/worktrees/agent-ad3a5b4cb74c0f24b`. A porter (`a7b8e21c`) got mid-way and left a PARTIAL dirty tree (loop.py project-gate + gateway `ModelPausedError` def + ctor kwargs, but MISSING the gateway raise-site @call_model, the daemon wiring, and the 3 test files) — I RESET it (`make git-restore FILES='src/general_ludd/event_loop/loop.py src/general_ludd/models/gateway.py'`), tree is clean again. **To land:** dispatch ONE main-tree porter from that worktree; seams = project gate `loop.py:~1050 _phase_claim_runnable_todos` (paused ⇒ `claimed_todos=[]`), model gate `gateway.py:~445 call_model` raising `ModelPausedError` (defined ~gateway.py:65, NOT retryable), ctor kwargs `pause_controller` on both mirroring `spend_limiter`, daemon wiring injecting a shared PauseController into EventLoop + ModelGateway + `app.state._pause_controller`. NOTE: gateway budget is `budget_guard` (not `_spend_limiter`); pause controller is its OWN kwarg. **CONCURRENCY: never run 2 main-tree porters — a `make git-status` DIRTY check + single-owner is mandatory (this cost two recoveries this session).**

**pause_controller.py robustness follow-ups (audit `aa40fea9`, NOT yet fixed — file a ticket):**
1. `pause()` (lines 118-119): mutates RAM (`_index`) BEFORE `_persist()`; if `store.save()` raises, RAM says paused but disk doesn't → restart silently un-pauses. Fix: persist-first, or roll back RAM in an `except` around `_persist()`.
2. `resume()` (lines 130-134): same non-atomic mutate-then-persist → restart re-materializes a cleared pause.
3. `is_paused()` (lines 137-139): lock-free read of lock-mutated sets — benign under GIL, a data race under free-threaded 3.13t.
4. `_set_for()` (lines 89-90): any non-`"project"` kind silently buckets as a model (no validation) — robustness gap.

**Residual SSRF gaps (future tranche 6, from `a07c8d16` auditor — NOT in #40's completed scope):** `kubernetes.py:112`, `nomad.py:93`, `grafana_oncall.py:175` (soft-fail/DNS-resolving — different semantics, migrate with care). Plus the cilium_hubble DNS-rebind `_is_blocked_ip` loop omits `not ip.is_global` (misses CGNAT 100.64.0.0/10 incl. 100.100.100.200) — delegate that loop to `security.ssrf._ip_addr_is_blocked`.

**#61 issue_sources SSRF + #50 dispatch_one fail-open:** were dispatched (`a97e9218`, `ac4971a9`) during a server-overload window; may have died — verify via worktrees / re-dispatch. #50 target: `AgentDispatcher.dispatch_one` capability gate fails OPEN for empty/None `invoker_name` (must fail CLOSED). #61: migrate `issue_sources/*` (base.py/jira/linear/asana/azure_boards clean; servicenow/redmine/clickup/monday/bitbucket with care; SKIP gitlab_issues + old GitHubIssueSource soft-fail).

**#35 SLICE 3/4 (to complete pause/resume fully):** SLICE 3 = quiesce in-flight agents (reuse `HibernationController.dehydrate` — see #51) + resource listing (facts._spend_facet, leases, ProcessRegistry, connector registry) into `PauseRecord.resources/last_state`. SLICE 4 = router `routers/pause.py` (template `routers/spend.py`) with GET/POST + CLI `gludd pause project|model <id>` / `resume` / `pause list` (template `_cmd_project_*` in cli.py). Reads `app.state._pause_controller` (set by SLICE 2).

**Operational reminders proven this session:** workflow-file commits push ONLY via `make git-push-sandboxcom-ssh`; discard dirty files via `make git-restore FILES='...'`; list runs via `make tag-run` (no run-list target); the "agent died" signal is often PREMATURE — verify via `git log`/worktree before re-doing; transient overload (429/529) = retry w/ backoff, not stop.

**AUDIT BACKLOG (latent, unverified — from floor-holding auditors this session; file tickets, none are active crashes):**

_`models/gateway.py` (`aaf09242`):_
1. **Double-spend risk (strongest):** `_invoke_and_bill` (743-824) calls `record_spend(cost)` at 744, then UNGUARDED side-effects (health_tracker.record_success 754, _metrics_collector 756-765, token_tracker 774, _response_cache.set 815). If any raises, the paid-for `ModelResponse` is discarded and `call_model_with_fallback`'s broad `except Exception` (1301/1325) routes to a fallback → SECOND provider call + SECOND record_spend. Wrap post-billing side-effects so an already-billed response is always returned.
2. Docstring says AUTH_ERROR/CONTEXT_LENGTH "re-raise immediately" but they surface as `openai.APIStatusError` (in `_retryable_exc_types`) → outer `except` at 1069 sets `_exhausted` and walks the FULL fallback chain (extra cost on terminal errors). Outer handler keys off exception type, not kind.
3. `call_model` (434-504) has NO circuit-breaker gate — only the wrappers do; direct callers hammer unhealthy providers.
4. Broad `except Exception` in fallback walkers (1146-1148, 1301/1325) would swallow a future pause/cancel signal → needs an explicit carve-out like the `BudgetExceededError`/`SSRFRejectionError` re-raises.
5. Last-resort probe (898-901) deliberately hits `fallback_ids[0]` via health-gate-less `call_model` — re-hammers a just-judged-unhealthy model.
6. `if not _exhausted: return None` (1074-1076) returns None where signature promises `ModelResponse` (latent AttributeError).
7. `_try_call_model` (1239-1240) maps config `ValueError` (profile-not-found/no-registry) → None → silently fails over instead of surfacing misconfig.
(Verified OK: BudgetExceededError/SSRFRejectionError propagate consistently + don't trigger failover; empty-200 guard raises before billing; NaN/Inf budget handling sound.)

_`daemon.py` (`aec10b83`):_
1. Event-loop deps `_benchmark_recorder`/`_model_perf_repo`/`app.state.model_perf_repo` (1339-1345) are attached AFTER `create_task(event_loop.run_forever)` at 1317 — the exact anti-pattern the H3 fix (constructor-inject `spend_limiter`) was written to avoid. Safe only because no `await` occurs in 1317-1350; any future await → first ticks lose benchmark/perf recording. Pass via constructor or move above 1317.
2. `build_event_loop_mcp_dispatcher` (1204-1208) is NOT fail-soft (unlike the MCP client startup right above it at 1164-1197 which degrades to None) → a dispatcher build failure drops the WHOLE daemon into degraded mode instead of just disabling MCP dispatch.
(Verified OK: spend_limiter ordering/H3, deployment_health_router None-safety, file_claim_registry fail-soft, single shared pricing_catalog.)

_`event_loop/loop.py` claim/dispatch (`ab864a9c`) — relevant to #52/#53:_
1. **Claim+dispatch not atomic → double-dispatch on tick-commit failure:** `claim_runnable` writes ACTIVE+leases into the TICK session (committed only after all phases, ~562), but concurrent dispatch uses isolated sessions that commit task_returns IMMEDIATELY (~1283). If the final tick commit fails (rollback ~563-575), the ACTIVE claim+leases are undone but the committed task_returns survive → todos revert to QUEUED, get re-claimed/re-dispatched next tick, AND the orphaned task_returns flow through review → duplicated work. (`_pushed_work`/`_applied_decisions` guard reconcile idempotency, not claim/dispatch.)
2. Over-cap requeue best-effort-suppressed (1098-1127): `contextlib.suppress(Exception)` around the QUEUED transition + release_lease → on a version race the todo is dropped from dispatch yet stays ACTIVE; recovery only via lease expiry / 15-min `_reap_stuck_todos`.
3. Concurrent-batch `asyncio.wait_for` timeout (1214-1235) `continue`s without inspecting results → jobs that completed before timeout are uncounted (`todos_dispatched` under-reports) + their todos stay ACTIVE.
4. Budget gate estimates on PRE-cap claim count (1083-1090 uses len(claimed) before the PID cap trims at 1098) → can needlessly skip a tick that would fit.
5. Lease-acquire failure in claim phase (1064-1075) leaves an ACTIVE todo with no lease → up to 15 min ACTIVE before requeue.
(Residual low risk: parallel dispatch threads write gateway collaborators — budget_guard.record_spend, global token tracker, health/metrics sinks — without a gateway-held lock; verify their thread-safety. Note: this auditor saw the now-RESET partial-#35 loop.py; tree is clean again.)
