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
bug in a comment. **Fix:** pass the true merge base (the worktree's fork point).

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

### S7. ApprovalGate is a dead HITL gate
`approval/gate.py:31-33` — the class is `return ApprovalResponse(request=…)`;
decision is always PENDING, nothing can APPROVE/DENY. G7 HITL has no decision
mechanism. **Fix:** back it with the human-todos resolve surface.

### S8. Pause/resume drops all conversation history
`pause_controller.py:185-195` leaves `snapshot.messages == []`; resumed agents
restart cold while the API returns `"resumed": true`. **Fix:** persist and
rehydrate messages; the `hibernation.py` docstring already promises this.

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

- **S14** Worker staleness lifecycle dead: `reload/worker_broadcast.py:109-128`
  `heartbeat()`/`cleanup_stale()` never called; `/admin/workers` reports stale
  as live; PSK broadcasts retry dead workers forever. Also `M2`: broadcast/ping
  iterate the live dict (mutation-during-iteration) — wrap in `list(...)`.
- **S15** Validation-job pipeline unimplemented both ends:
  `event_loop/loop.py:3198-3212` (no phase, no caller) + `worker/app.py:541-556`
  (honest 501). Remove or implement.
- **S16** Multi-worker write-queue bridge unconnected with three incompatible
  topic namespaces: `writer/bridge.py:167-211`, `daemon.py:1016,1035`,
  `loop.py:904-924`, `writer/_child.py:126-145`. No topic is both produced and
  consumed — a silent-drop trap. Reconcile topics before wiring.
- **S17** DAST orchestration unreachable (`project_runner/dast.py`, zero
  callers) and `_start_app:386-399` uses `subprocess.Popen(shell=True)` on a
  caller-supplied `start_command`, bypassing the shell-metachar hardening every
  other project_runner path enforces. Fix the shell=True regardless of wiring.
- **S18** StallWatchdog stall action publish-only, zero consumers
  (`daemon.py:2165-2184`) — wire a consumer (re-dispatch / human-todo / kill).
- **S19** `code_quality_score = 0.5` constant (`observability/recorder.py:25`)
  reaches live model-quality routing via loop.py:2804 → repository.py:995-1002.
  Pass real test results or exclude the constant from scoring.
- **S20** QualityGateChecker fail-open: `quality/gate.py:79`
  `g.get("passed", True)` (sibling preflight.py:379 is fail-closed).
- **S21** `ck_todos_priority_range` model CHECK (models.py:286) absent from the
  alembic chain — `test_alembic_create_all_parity.py:363` is RED today.
- **S22** AgentCapabilities defaults to bare `AgentRegistry()`
  (`agents/capabilities.py:82`) → zero tools; use `default_registry()`.
- **S23** HibernationController.parked() never called from the dispatch path
  (`dispatcher.py:428`) — memory-reclaim feature inert.
- **S24** Self-improve outputs discarded: `loop.py:4416-4433` logs suggestion
  count only; `self_improve_error_patterns` written, never read.
- **S25** AG.9 checkpoint compare half absent (`ag9_checkpoint/branching.py:120-137`)
  → `compare_branches()` only ever returns "pending".
- **S26** `/admin/code/suggest-model` (`routers/models.py:384-397`) swallows
  router crashes as `"insufficient_historical_data"` — a broken router looks
  like cold-start. Distinguish the two.

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
