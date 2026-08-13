# Stub & Dead-Wiring Closure — spec for v0.1.0-beta.2

Status: DRAFT (2026-07-14). Source: three parallel verified stub sweeps
(core runtime; integration/connectors/web; agents/guardrails/observability),
each finding confirmed by reading the code, callers, and tests — grep-only
hits and self-disclosed 501s were discarded. Companion to
NEXT_RELEASE_BETA2_SPEC.md (which sequences these into waves).

## The dominant failure shape

Very few classic `TODO`/`pass` stubs remain. The real risk is **fully-written
machinery whose production wiring is missing or neutered, that reports success
while doing nothing** — and whose test fixtures leave the relevant field at its
default, so the suite stays green over the gap. Every CRITICAL/HIGH below is
this shape. The fix pattern is the same: wire the producer/consumer, then add a
test that populates the real value (not the default) end-to-end.

## CRITICAL — silently breaks a shipped feature today

### S1. Subagent dispatch degrades to a no-op executor without warning
Deep-audit CONFIRMED on the exact path: `daemon.py:2007` → `2019` (**no `else:`
branch**) → `2118` passes `executor=None` → `dispatcher.py:65` → `_noop_executor`
→ `status="completed", output=""`, with **not one WARNING or ERROR line
anywhere on that path**. Trigger conditions: `config/model_profiles/` missing,
empty, or all-unparseable → `load_model_profiles` (daemon.py:598-616) returns
`[]` → `model_gateway` stays `None`. Note the repo's own `config/` is **not on
the discovery path** (`$GLUDD_CONFIG_DIR` → `~/.config/general-ludd` →
`/etc/general-ludd`), so running the daemon from the repo without
`GLUDD_CONFIG_DIR` set is itself a triggering condition.

Three findings make this worse than "add a warning":
- **No consumer in `src/` reads `AgentTaskResult.status`.** Even when the
  dispatcher *does* fail correctly, the signal has no receiver — every guard
  rejection (dispatcher.py:247/261/278/294) returns rather than raises. Logging
  alone cannot reach a caller that never looks → **fail-closed, not log-loud.**
- **A models-less daemon reports `/healthz: healthy` and `/readyz: ready`
  (HTTP 200, unauthenticated)** while incapable of a single model call.
- **The hot-reload escape hatch doesn't work**: `routers/reload.py:193-214`
  `POST /admin/config/reload` republishes `startup_config["model_profiles"]`
  but **never rebuilds `ModelGateway`** — a gateway that booted `None` stays
  `None`, so an operator who fixes the YAML cannot recover without a restart.

**Fix:** `_unconfigured_executor` raising `ExecutorNotConfiguredError` at
`dispatcher.py:43-65`; `else:` branch setting a `_model_unconfigured` flag at
`daemon.py:2019`; `/readyz` must report NOT ready when that flag is set; reload
must rebuild the gateway or explicitly report "restart required". Blast radius
is narrow: `dispatch_many` has **zero real call sites** (the hibernation.py:544
hit is a docstring) — all live traffic is `dispatch_one`. **Rewrite the
bug-enshrining test `test_dispatcher_falls_back_to_noop`.**

### S2. Review dispatch strands task returns forever (3 code paths, not 1)
Deep-audit CONFIRMED and **broader than first stated** — a 501 alone does NOT
fix it.

- `worker/app.py:537-539`: `/jobs/return-review` fabricates `{"status":"ack"}`
  and runs nothing.
- `db/repository.py:736`: `claim_unreviewed` selects `status == "created"`
  only. Once a row flips to `claimed_for_review` (repository.py:751/768)
  **nothing ever re-claims it**. `TaskReturnStatus.REVIEWED` is never assigned
  anywhere in `src/` — only in test fixtures. Second casualty: self-improve's
  `_collect_training_data_from_returns` (loop.py:4222-4300) queries
  `status == "reviewed"` and so **always gets zero rows**.
- **The trap:** `loop.py:1189-1200` (HTTP branch) never inspects
  `resp.status_code` — it passes *any* response to `_persist_review_response`,
  which only cares whether `decision` is present. A 501 body has no `decision`
  either, so converting the worker to 501 leaves the claim stranded exactly as
  today, while tests (which assert only the worker's status code) go green.
- **Third instance, previously unflagged:** the in-process runner branch
  (`loop.py:1140-1188`) *discards the playbook return value entirely* — only
  the `except TimeoutError` branch releases the claim.
- **And the playbook itself is a stub:** `playbooks/return_review.yml`
  hardcodes `decision: "complete"` with no model-gateway call. So "wire the
  worker to actually run it" would **rubber-stamp every return as complete** —
  strictly worse than discarding. 501 is the correct choice.

**Fix (3 files):** (1) `worker/app.py:537-539` → honest 501 matching the
sibling handlers at 541-586; (2) `loop.py:1189-1223` → check
`resp.status_code >= 400` and take the existing timeout path (release claim to
`created`, flush, todo → BLOCKED); factor the release-claim logic (already
duplicated 3×) into `_release_review_claim(tr, reason)`; (3) `loop.py:1140-1188`
→ capture the runner result and release the claim when no real decision comes
back. **Tests that enshrine the bug and must be inverted:**
`test_worker.py:197-205`, `tests/e2e/test_obj03_worker.py:67-78`, and
`test_event_loop.py:143-168` — whose comment literally reads *"Happy path must
not touch/release the claim"*. Add
`test_dispatch_review_job_http_501_releases_claim`.

### S3. Pipeline (#77) gate is hardcoded `return True`
`daemon.py:763-767`. The production `gate_fn` unconditionally returns True;
`GateLane` logs "GREEN — committed snapshot" and counts a green gate for
validation that never ran, and merged files are written to the live repo
*before* the gate with no revert path. No config path can inject a real gate.
**Fix:** wire the real project gate as `gate_fn`, or keep the pipeline feature
flag off and mark it experimental in docs until S3-S5 are closed.

### S4. Pipeline anti-clobber merge is mathematically unreachable
`pipeline/daemon_adapters.py:184`. `safe_merge(repo_text, repo_text, wt_text)`
passes repo content as both base and ours → theirs always wins cleanly →
`result.conflict` can never be True → the "REFUSING clobber" branch is dead →
concurrent repo edits are silently overwritten (the exact data-loss the
function exists to prevent). `test_pipeline_daemon_adapters.py:58` blesses the
bug in a comment.

**Fix:** `safe_merge(base_text, repo_text, wt_text)`, where `base_text` is the
worktree's **fork point** content, read via a new `git show <base_sha>:<relpath>`
helper. Deep-audit findings that size the work:
- **No fork-point value exists anywhere in the repo to reuse.** A repo-wide
  search for `base_sha`/`base_commit`/`fork_point`/`parent_sha` finds one
  unrelated SWE-bench dataset field. `WorktreeInfo.commit`
  (`git_automation/types.py`) is the worktree's *current HEAD at scan time*
  (from `git worktree list --porcelain`), **not** a recorded base — it cannot
  be reused. `CompletedUnit.base_sha` is genuinely net-new plumbing.
- **The dispatch types are too thin to carry it.** `AgentTask`
  (`agents/types.py:42-53`) and `AgentTaskResult` (`dispatcher.py:33-40`) carry
  **neither a worktree path nor any SHA**. The S5 producer cannot populate an
  existing carrier — this needs `worktree_path` + `base_sha` on
  `AgentTaskResult` (or an out-of-band hold between `create_worktree()` and
  `report_completed()`). *Same shape as S8's missing `messages` field: the
  dispatch types consistently cannot carry state across the dispatch boundary.*
- **Production worktree creation never goes through Python.** Neither
  `GitAutomation.create_worktree()` nor `build_worktree_add_argv()` has a single
  caller in `src/` (all callers are tests) — the real path is the Makefile
  `agent-worktree` target shelling `git worktree add` directly
  (`test_agent_worktree_targets.py:63-66`), which records no base SHA. So the
  producer cannot "just call the existing worktree function"; the Python
  worktree API is dead code, and whichever path the producer adopts is the one
  that must be taught to record `base_sha`.

This confirms the fix order **S4 → S3 → producer**, but S4's field addition must
be designed with the `AgentTaskResult` gap in view so the two land coherently.

### S5. Pipeline lanes have no production input
Deep-audit CONFIRMED: `report_completed` has exactly one hit in `src/` — its own
definition (`pipeline/controller.py:101`); **`CompletedUnit(` is constructed
ZERO times in `src/`** (all 33 constructions are in tests). `daemon.py` only
ever calls `.start()`/`.stop()` (2128-2136, 2293-2301). Root cause of the
emptiness: **nothing in the pipeline dispatch path creates a worktree** —
`make_dispatch_fn` routes to `_gateway_executor`, which returns a model-response
*string*, not a worktree to merge.

**This is the only reason S3/S4 have not already destroyed data.** The lanes
idle forever, so the fake-green gate and the unreachable clobber-protection are
armed but never fired.

**Decision: keep `pipeline.enabled` default-OFF and mark the feature
EXPERIMENTAL in docs.** Fix order is **S4 → S3 → then** build the missing
worktree-creating producer and feed. Do not wire the feed first.

**Tests that pin the broken shapes** (must change when `CompletedUnit` gains
`base_sha` and `merged_awaiting_gate` carries units instead of bare ids):
`test_pipeline_state.py:56,60`; `test_pipeline_state_structural.py:63,69,143,148,153`;
`test_pipeline_controller.py:93,98,99,174,175`; and
`tests/integration/test_pipeline_controller_e2e.py` (14 `report_completed`
sites) — note this "e2e" test drives a flow **no production code performs**, so
it validates nothing about the real daemon path.

### S6. Resume path resets recursion depth to 0 (guard bypass)
`routers/pause.py:152-160`, `agents/dispatcher.py:523-532`,
`controllers/pause_controller.py:185-195`. `AgentTask` is reconstructed from a
hydrated snapshot without `depth`, and the write side never persists it, so
every resumed subagent restarts at depth 0 and resumed trees nest unboundedly
past the `dispatcher.py:126-132` guard. **Fix:** persist + rehydrate `depth`.

## HIGH — dead gates and unwired flagship capability

### S7. ApprovalGate is a dead HITL gate — and is never called at all
Deep-audit CONFIRMED, broader than drafted. `approval/gate.py:31-33` returns
`ApprovalResponse(request=request)` with `decision` defaulting to PENDING — no
path can ever produce APPROVED/DENIED. But the sharper fact: **`request_approval`
has zero production call sites** (every caller is a test). daemon.py:1355-1356
instantiates the gate onto `app.state._approval_gate`, and `routers/approval.py`
exposes exactly one endpoint — `GET /admin/approval/status`, which reports
`{"wired": …, "gate_type": …}`. G7 HITL is scaffolding that is instantiated,
parked, introspected for "is it wired", and never invoked.

**The decision surface it needs already exists and works**: `routers/human_todos.py`.
`PATCH /api/human-todos/{id}` (219-332) resolves to done/dismissed, requires
`human_resolver`+`human_resolution`, unblocks the linked parent agent todo
(`BLOCKED_ON_HUMAN` → `QUEUED`/`CANCELLED`, 273-299), and already syncs
permission-escalation rows via `security.py:_sync_escalation_from_human_todo`.
**Fix:** make ApprovalGate a thin adapter over human-todos (`request_approval`
creates a `HumanTodoModel` with `category="permission_escalation"`;
`check_decision` maps done→APPROVED, dismissed→DENIED); blocking/resume then
comes for free from the existing wiring. Then **wire real callers** —
`routers/security.py` escalation creation and
`routers/self_improve.py:_ConfigTierCapabilityChecker` each roll their own ad
hoc gating today. Note there are currently **three unrelated approve/deny
mechanisms** (ApprovalGate, human_todos, and security.py's in-memory
`_escalation_store`); consolidate on human-todos.

### S8. Pause/resume drops conversation history — and the router bypasses the correct path
Deep-audit CONFIRMED, bundle with S6 (same code path, same commit).
`pause_controller.py:185-195` builds the snapshot with **neither `messages=` nor
`depth=`**, so both silently take their zero defaults. `AgentTask`
(`agents/types.py:42-54`) has **no messages field at all** — so even a populated
snapshot would have nowhere to land on resume.

The damning detail: `AgentDispatcher.resume_project` (dispatcher.py:506-534)
**already threads `depth=snap.depth` correctly**, and
`PauseController.resume_rehydrate` (pause_controller.py:203-242) calls it — but
that path is **dead in production**. `routers/pause.py:123-179` bypasses it
entirely with its own inline rehydration loop that drops `depth`. So the fix for
S6 is partly "stop bypassing the code that already works."

Why messages are unreachable at quiesce: real history lives inside the live
`ModelGateway`/`ToolCallLoop` call, seeded per-dispatch from `task.prompt` alone
(daemon.py:2093), and `_active_tasks` only ever holds the *dispatch-time*
`AgentTask`. `hibernation.py:messages_from_dicts()` (164-197) is the documented
bridge for exactly this — and is **called only in tests**. Same for
`HibernationController.parked()` (533-561): zero production callers.
`pause_controller` also reaches past the policy layer straight into
`hibernation._store.dehydrate_async(snap)`, bypassing the
min_depth/min_context_messages dehydration policy.

**Fix:** add `messages` to `AgentTask`; seed `gateway.call_model` from
`task.messages` when present (daemon.py:2093); populate `depth=`/`messages=` in
`quiesce_project`; delete the inline loop in `routers/pause.py` and call
`resume_rehydrate`. **Open trace needed:** exactly where in-flight message state
is reachable from `AgentDispatcher._active_tasks` at the quiesce boundary — the
dispatcher must keep `AgentTask.messages` updated as the tool loop progresses.

### S9. EvidenceChecker marks any claim supported given any non-empty sources
`review/evidence_checker.py:45-47, 64-70` pools every `file:line` fragment from
all tool outputs into every claim → one unrelated path validates all claims.
Dead gate for unsupported-claim auditing. **Fix:** per-claim source matching.

### S10. Cost/performance model routing built but never invoked on dispatch
`models/performance_router.py`, `daemon.py:1984, 2057`,
`gateway.py:1849`. `ModelPerformanceRouter.select_model()` and
`select_cost_effective_profile()` have zero src callers; `_gateway_executor`
hardcodes `profile_id="default"` for every dispatched task. (The EventLoop todo
path *does* route correctly — only subagent dispatch is unwired.) **Fix:** call
the router from `_gateway_executor`; matches the known "router cheapest-
equivalent" backlog item.

### S11. Estimation self-correcting loop is a permanent no-op
`review/reviewer.py:150-157` hardcodes zero actuals; `record_estimate()`
(estimation_tracker.py:155) has zero prod callers → `record_completion()`
always early-returns ACCURATE. SUSPECT flagging never fires. **Fix:** thread
real actuals; call record_estimate on dispatch.

### S12. web_search router bypasses the hardened retriever
`routers/web_search.py:42-98` claims "backed by WebRetriever" but hand-rolls
`urllib.request.urlopen` against hardcoded DuckDuckGo HTML with a silent
`except Exception: return []` — never touching `WebRetriever`/`is_url_blocked`.
**Fix:** route through the SSRF-guarded retriever (the doc claim is the intended
behavior).

### S13. Operator permission-override silently widens the ceiling on parse error
`routers/security.py:75-79`. `_get_human_spec` wraps `parse_file` in
`except Exception: pass` → a YAML typo in a *narrower* operator override falls
back to the built-in default which includes `net:egress:any` / `allowed_hosts:
["*"]`, inverting the module's own "a typo cannot silently widen access"
principle. **Fix:** log + fail closed. (`_human_spec` also has no setter
anywhere — always falls through to the default factory; confirm that's
intended.)

## MEDIUM — inert lifecycle / fake-green / fail-open

- **S14** Worker staleness lifecycle dead — CONFIRMED:
  `reload/worker_broadcast.py:109,123` `heartbeat()`/`cleanup_stale()` have zero
  prod callers (the broadcaster is only constructed at `daemon.py:2419`).
  `GET /admin/workers` (`routers/reload.py:343-356`) returns `list_workers()`
  raw — `last_seen` frozen at registration, no staleness filter — and broadcasts
  never unregister on failure, so a dead worker is retried **and re-sent the
  PSK** forever. **M2 sub-claim REFUTED (already fixed):** all three broadcast
  paths iterate `self._snapshot_workers()` (`worker_broadcast.py:115-117`,
  `list(...)` under lock), not the live dict — proven by
  `tests/unit/test_hot_reload_toc.py:31-115`.
- **S15** Validation-job pipeline unimplemented both ends:
  `event_loop/loop.py:3198-3212` (no phase, no caller) + `worker/app.py:541-556`
  (honest 501). Remove or implement.
- **S16 → PROMOTE TO HIGH.** Not a topic-naming gap — **three independent
  structural breaks** make `GLUDD_WRITER_MODE=subprocess` non-functional in
  every dimension:
  1. **The queue physically cannot reach the child.** `ipc/queue.py:61-192`
     `WriteQueue` is a **pure in-process** `asyncio.Lock` + `deque` — no IPC at
     all — while the writer child is a real OS process spawned via
     `subprocess.Popen` (`writer/process.py:165-171`). Envelopes are also
     silently discarded at shutdown (`daemon.py:2332-2335` `.clear()`), never
     drained.
  2. **Config-shape bug keeps the child permanently inert.** `_child.py:main()`
     expects a nested `{"database": {...}}` (per its own test fixture
     `test_writer_child.py:67-71`), but `daemon.py:1017` passes the **flat** DB
     dict (`dict(db_config)`, from `daemon.py:979`). So `config.get("database")`
     is `None`, `has_db_url` is False, and the child **always** takes the Slice-1
     stub branch — writes a nonce, then `time.sleep(3600)` forever. It never
     builds a write engine or ticks, regardless of real DB config.
  3. **The file-spool alternative is never configured.** `inbound_spool_path`
     (`_child.py:186`) is set **only** in `test_writer_child.py:69` — never in
     prod — so `_drain_spool` never runs.
  Plus the original finding: no topic is both produced and consumed
  (`enqueue_or_commit` emits `todo.create`, `loop.py:904-924` handles only
  `todo.upsert`, `_child.py:126-145` only `execute_sql`). **Why HIGH:** HTTP
  workers in subprocess mode run on a genuinely read-only engine
  (`db/session.py:102-130` enforces `PRAGMA query_only=ON`), so an operator who
  flips this flag believing the multi-worker docs gets **every write endpoint
  failing** while the writer subprocess does nothing. Fix order: config shape →
  adopt the JSONL spool the child already implements → reconcile topics → add an
  integration test with a **real** (non-mocked) child (today's tests mock
  `WriterProcess`, so this has never been exercised end-to-end).
- **S17** DAST `shell=True` — CONFIRMED, and the unreachability is *wider*:
  `project_runner/dast.py:391-393` `subprocess.Popen(start_command, shell=True)`
  where `start_command` is a free-form pydantic `str` (`dast.py:70`) with no
  metachar validation. The **entire DAST module** is unreachable — no router,
  CLI, or daemon surface wires it. Note the internal inconsistency: dast.py's own
  scanner-launch path (`:326`, `:340`) correctly uses the hardened
  `ProjectProfile.resolve_argv` (`profile.py:79-106`: `shlex.split` + `_SHELL_META_RE`
  + `allowed_exec`, fail-closed) — only `_start_app` skips it. Keep MEDIUM (not
  attacker-reachable today) but fix now: it becomes HIGH the moment anyone adds a
  DAST entry point.
- **S18** StallWatchdog stall action publish-only, zero consumers
  (`daemon.py:2165-2184`) — wire a consumer (re-dispatch / human-todo / kill).
- **S19** `code_quality_score = 0.5` — CONFIRMED and **WIDER: two independent
  dead paths, not one.** (a) `observability/recorder.py:25` hardcodes 0.5 unless
  `test_results` arrives with `total > 0`; the **sole** prod caller
  (`loop.py:2804`) passes none, so 0.5 always fires. (b) **Second path:**
  `event_loop/benchmark.py:31` `record_job_benchmark` hardcodes 0.5 and has **no
  `test_results` parameter at all** — called from `loop.py:2745-2755` and
  `engine.py:604-614`/`786-796`. Both feed `composite_score`
  (`repository.py:995-1005`, weighted 30%) which drives **live model selection**
  (`scoring/router.py:752` argmax, `:778` leaderboard). Quality-aware routing is
  therefore defeated for all live traffic. **The real fix is cheap:**
  `engine.py:597` already computes real `test_exit_code`/`test_summary` from
  `_run_tests()` — it just isn't threaded into the scoring call.
- **S20** QualityGateChecker fail-open: CONFIRMED — `quality/gate.py:79`
  `all(g.get("passed", True) ...)` treats a gate dict **missing** the `passed`
  key as PASSED; the sibling `preflight.py:379` uses `.get("passed", False)`
  (fail-closed). Severity is low *today* only because `enforce()` has zero
  production callers — but that makes it a landmine: the completion-gate fix
  (beta.2 Wave 2) wires `enforce()` into production for the first time, so
  **flip the default to `False` in the same change**. Verified safe: every
  existing `enforce([...])` call in `test_quality_gate.py` / `test_quality.py`
  supplies `passed` explicitly, so the flip breaks **no existing test**.
  Add a regression test: `enforce([{"gate": "x"}])["all_passed"] is False`.
- **S21** `ck_todos_priority_range` model CHECK (models.py:286) absent from the
  alembic chain — `test_alembic_create_all_parity.py:363` is RED today.
- **S22 → PROMOTE TO HIGH. Live on every generation job today.**
  `agents/capabilities.py:82` `self._registry = agent_registry or AgentRegistry()`
  — a bare `AgentRegistry()` (`registry.py:16-21`) starts **empty**; only
  `default_registry()` populates real agents. The **single** production
  construction site, `models/job_invocation.py:135` (`invoke_model_for_generation`),
  does **not** pass `agent_registry=` — and that function is called from
  `worker/app.py:148` off the live job-dispatch loop (`worker/app.py:363`). So
  **every real generation job** builds AgentCapabilities with **zero agent-dispatch
  tools**, silently (`or AgentRegistry()` neither raises nor warns). The only
  correct usage in the tree is a *test* (`test_completion_integrity_high.py:460`).
  This is a **distinct, still-open bug** — not the previously-fixed daemon
  `default_registry()` regression. Fix: pass `agent_registry=default_registry()`
  at `job_invocation.py:135`; regression test asserting
  `list_agent_tools()` is non-empty on the real generation path.
- **S23** HibernationController.parked() never called from the dispatch path
  (`dispatcher.py:428`) — memory-reclaim feature inert.
- **S24** Self-improve outputs discarded: `loop.py:4416-4433` logs suggestion
  count only; `self_improve_error_patterns` written, never read.
- **S25** AG.9 checkpoint compare half absent (`ag9_checkpoint/branching.py:120-137`)
  → `compare_branches()` only ever returns "pending".
- **S26** `/admin/code/suggest-model` (`routers/models.py:384-397`) swallows
  router crashes as `"insufficient_historical_data"` — a broken router looks
  like cold-start. Distinguish the two.
- **S27 — tenant contextvar is WRITE-ONLY (security-relevant; peer-verified
  2026-07-14).** `db/tenant.py:28` defines `get_tenant()`, and `db/__init__.py`
  re-exports it — but **nothing in `src/` ever calls it**. The event loop
  (`loop.py:737`, `:785`) only calls `set_tenant`/`reset_tenant`; the value is
  written and never read. The only `get_tenant()` call sites in the repo are in
  `tests/unit/test_db_tenant_scoping.py` (~20 hits), which exercise contextvar
  get/set/reset semantics **in isolation, never wired into a query**. Also
  confirmed: the `event.listens_for` hooks at `db/session.py:47`/`:106` are
  sqlite-PRAGMA-only, **not** tenant filters. So the contextvar-based tenant
  scoping is **not enforcing anything** — it is the same "plumbing exists,
  nothing consumes it" shape as S1/S2/P-3, but on a **tenant-isolation
  boundary**. This is the mechanism that would be expected to prevent
  cross-tenant reads, and the tests give false assurance that it works.
  **Fix:** either wire `get_tenant()` into the query path (a SQLAlchemy
  `with_loader_criteria` / session event that injects `project_id`/tenant
  filtering), or delete the contextvar and its tests and rely solely on the
  explicit per-repository scoping — **but do not leave a dead isolation control
  that tests claim is live.**

  **The real mechanism is ALSO unscoped by default (peer-verified, and this is
  the sharper finding).** The actual tenant filter is a separate pattern:
  repositories take an explicit `project_id` ctor arg / `.scoped(session,
  project_id)`, **defaulting to `None` = unscoped** unless a caller passes it.
  And the very module that sets the dead contextvar does **not scope its own
  repos**: `loop.py:745-748` and `:1817-1818` construct `TodoRepository(session)`,
  `VariableNamespaceRepository(job_session)`, `TaskReturnRepository(job_session)`
  with **no project_id**. So neither mechanism is enforcing isolation on the main
  tick/dispatch path. Concrete instance: `routers/accounting.py:163-167` opens a
  session and calls `todo_repo.list_all()` / `role_repo.list_all()` **unscoped**,
  pulling **every project's rows**, then buckets by `t.project_id` in a Python
  dict afterward (`:169`, `:191`) — correct output, but a full-tenant table scan
  on every request, with isolation enforced only by an in-memory dict lookup
  rather than by the query. Fix must make scoping the **default** (fail-closed),
  not an opt-in argument callers can forget.

- **S28 — model-call logging is a silent no-op in production (peer-verified).**
  `ModelPerformanceRepository.record_call_sync` (`db/repository.py:2410-2463`) is
  called from `worker/app.py:400` on the live path. It does
  `asyncio.run(self.record_call(..., session=None))` (`:2455`); `record_call`
  resolves `eff_session = session or self._resolve_session()` (`:2389`); and
  `_resolve_session()` (`:2684-2697`) — **despite a docstring claiming it lazily
  creates a session from `session_factory`** — does no such thing: it just
  **raises `RuntimeError`** when `session_factory is not None and session is
  None`. Since `daemon.py:1972-1974` constructs the repo with
  `session_factory=session_factory` and no session, **every production
  `record_call_sync()` raises**, and the exception is **swallowed** by the bare
  `except` at `:2456-2463`. Net: model-performance telemetry is never recorded —
  which also starves the scoring/routing data this repo makes decisions on (cf.
  S19's `code_quality_score = 0.5`). **Fix:** make `_resolve_session()` actually
  build a session from the factory (or pass one in), and stop swallowing the
  exception — a telemetry write that always fails must be loud.

## LOW / descope-or-delete (dead code enshrined by tests)

`ReloadManager.rollback()` fake status flip (reload/manager.py:96-116);
`security/ssh_key_rotation.py:65-76` fake `# stub-` key material;
`SandboxEnforcer._isolate_network()` pass-only (enforcer.py:229-242);
`system/monitor.py` load gate enforced nowhere; `process/registry.py:329-346`
`reap()` never called (unbounded growth); ReflexionLoop, OutcomeObserver,
GrindingDetector class-half, MetricsExporter.gauge_set, classifier `llm_route`
scaffold, `ssl_agent/agent_flow.py:109-118` canned "model call",
ag2 lifecycle hooks fail-open via world-writable `/tmp/gludd-subagent-{pid}.json`.
Each gets a real ticket in beta.3 planning or is deleted with its
stub-asserting tests (the tests are the reason these rot unnoticed).

The socket-timeout fallback previously used by `_isolate_network()` was removed:
it changed Python's process-wide default rather than creating a network boundary.
Long-lived user reports confirm that `socket.setdefaulttimeout()` persists until
explicitly changed and affects subsequently created sockets, including sockets
returned by `accept()`:
[default timeout persists](https://stackoverflow.com/questions/45498383/setting-default-timeout-with-socket-setdefaulttimeout)
and
[accepted sockets inherit the global default](https://stackoverflow.com/questions/16215309/why-so-rcvtimeo-is-inherited-from-listening-socket-to-accepted-socket).
The hard deny boundary remains the responsibility of the configured container or
VM backend; this hook must not leak transport policy into unrelated callers.

## Verified clean (checked, cleared — do not re-audit)

Worker PSK auth (fail-closed), `/jobs/execute`, dispatch caps, saturation
controller, file-claim/stuck-todo reapers, writer supervisor health loop, D11
dispatcher guards, `execution/engine.py`, dogfood orchestrator, replay recorder
(wired at daemon.py:1745), `routers/account.py` (honest 501), self_update
fail-closed validation, mcp/skills layer, dehydrate/hydrate round-trip, the
security primitives (ssrf/sanitize/auth/permissions/sts/path_canonicalizer),
the daemon `default_registry()` regression (old memory note refuted), and the
alembic 001-missing-tables item (fixed by migrations 002+024).

## Closure protocol

Group S1/S2/S10 (dispatch integrity) as one batch — they share
`daemon.py`/`dispatcher.py`. S3/S4/S5 as the pipeline batch (do not ship the
lane feed until S3/S4 land). S6-S9 authz/gate batch. Each fix: failing test
first (populate the real value, not the default), then wire, then a
`| evidence:` line in TASKS.md. For every "delete" candidate, remove the
stub-asserting test in the same commit so the deletion can't silently regress.
