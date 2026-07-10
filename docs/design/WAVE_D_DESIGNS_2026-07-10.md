# Wave D implementation-ready designs — 2026-07-10

Companion to `docs/AGENTIC_IMPLEMENTATION_SPEC.md` (Wave D items). Each section below was
produced by a read-only design pass against the live tree on 2026-07-10 with every cited
seam verified by reading the file. Re-pin line numbers with a Read at apply time.

---

## D2 — Wire `run_project_gate()` into the COMPLETE-transition path

**Gap:** `quality/project_gate.py:35-208` `run_project_gate()` is implemented, fail-closed,
zero production callers (grep-verified).

**Load-bearing fact:** both places a `decision=="complete"` becomes a `TodoStatus` transition
already funnel through `verify_completion()` (`review/completion_verifier.py:120-184`):
(1) in-process reviewer path `event_loop/loop.py:1129` → `review/decision_applier.py:35-38`
(calls verify_completion at :37 before the transition at :60-65); (2) reconcile path
`loop.py:3031` → verify_completion at `loop.py:3096-3098` before the CAS transition at :3136.

**Decision: wire INSIDE `verify_completion()`** — one change covers both write paths.
Reconcile-phase-only would miss path 1; reviewer pre-prompt is advisory-to-an-LLM (reopens the
exact hole Layer 2 exists to close).

Implementation:
1. `project_runner/profile.py`: new `ProjectQualityGate(BaseModel)` — `enabled: bool = True`,
   `checks: list[str] = []`, `required: list[str] | None = None` (None → required == checks),
   `timeout_s: int = 120` with a field_validator clamping 1–300. Add
   `quality_gate: ProjectQualityGate | None = None` to `ProjectProfile` (absent key → None → off).
2. `review/completion_verifier.py`: new module-level `_check_quality_gate(repo_root) -> str | None`
   (loads profile; None/disabled/empty checks → None = skip; else `run_project_gate(...,
   checks=cfg.checks, required=cfg.required or cfg.checks, timeout_s=cfg.timeout_s)`; on fail
   return a downgrade-reason string). Call it right before the final `return decision` (:184),
   inside the existing `if not root_unresolved:` guard; on failure return
   `decision.model_copy(update={"decision": "needs_more_work", "confidence": 0.0,
   "audit_notes": [...]})` — identical shape to the evidence_refs downgrade at :171-182.
3. No changes to decision_applier.py or loop.py — both inherit the check.
4. `docs/examples/project.yml`: document the opt-in `quality_gate:` block (after the DAST block).

Failure semantics: required-check fail → downgrade to needs_more_work (state change only;
`_decision_to_status` already maps it). `run_project_gate` never raises (fail-closed dict).
Timeout: per-check `timeout_s` clamped 1–300 (deliberately far below runner.py:46's 900s default —
verify_completion runs on every reviewed complete decision, up to 50/tick at loop.py:3034).
Degraded: no `quality_gate:` key OR empty checks → skip, never fail-closed (the reference
example's check keys are lint-py/test-py, not run_project_gate's DEFAULT_CHECKS lint/test — a
silent default-on would fail-closed the reference project).
Naming: disambiguate from gludd's own self-hosting gate (`schemas/quality_gate.py:59-67`,
`daemon_state["quality_gate"]` at daemon.py:1911) in docstrings.

Tests (all homes exist): tests/unit/test_project_runner.py (schema cases incl. timeout bounds),
tests/unit/test_completion_verifier.py (TestQualityGate: no project.yml → stays complete;
gate absent → complete; passing checks → complete; failing/undeclared required → downgraded with
confidence 0.0 + audit note; repo_root=None → load_project_profile never called; kwargs
forwarding via monkeypatched run_project_gate), tests/unit/test_decision_applier.py (failing gate
→ transition called with NEEDS_MORE_WORK), tests/unit/test_event_loop.py (reconcile-phase case
proving write path 2 is covered by the same insertion).

---

## D3 — Self-improve APPLY path: project-neutral validation + strategy split

**Key discovery:** `projects/manager.py:89` `_detect_self_project()` (+ `_normalize_repo_url` :24,
`_resolve_self_repo_url` :59) already implements target==self detection, covered by
tests/integration/test_self_improve_project_routing.py:64-105, with ZERO production callers.
`ProjectWeight` (:194) has no `is_self` field.

Verified seams: `reload/self_improve.py:24` (ctor lacks repo_root), `:84-89` (hardcoded
`test_commands=["make test-unit"]`), `:125-172` (`reload_if_needed` always routes through
HotReloader — structurally self-only); `reload/hot_reloader.py:118/:328` operate on the running
process's sys.modules; `routers/self_improve.py:344-406` — config-tier branch already resolves
workspace_root from project_id (:346-359) but code-tier branch (:384-399) ignores project_id
AND runs with no approval_id gate (adjacent bypass to close in the same wave);
`self_improve/harness.py:37-53` already resolves repo_root (gap-analysis side); `:355-411`
`apply_self_improvement` has GitAutomation.commit_and_push + UNCONDITIONAL
`reloader.reload_changed_modules`, and is itself unwired from loop.py;
`event_loop/loop.py:2791-2911` `_apply_self_update_code` always uses set_code_target +
reload_if_needed with no project check; `loop.py:1045-1073` `_resolve_repo_root` is reusable.
CAUTION: gludd's own project.yml declares `test: make test` (full suite, OOMs locally) — naively
swapping seam :88 to profile-derived commands would upgrade self-validation to the OOM-risk
command. Preserve `make test-unit` for self (add a `self_check` command to project.yml).

Ordered plan:
1. `ProjectWeight.is_self: bool = False`; set in add_project/seed_from_config/
   rebuild_manager_from_db via `_detect_self_project`.
2. `SelfImprovementWorkflow.__init__` gains optional repo_root/workspace_root; code-tier router
   branch resolves workspace_root like the config-tier branch; `_apply_self_update_code` uses
   `self._resolve_repo_root(todo.project_id)`.
3. Validation strategy: is_self=False → `load_project_profile(workspace)` +
   `ProjectCommandRunner(workspace, profile).run("test")` (mirror execution/engine.py:194-226),
   adapt CheckResult → ValidationResult. is_self=True → keep `make test-unit`/`self_check`.
4. Apply strategy split: is_self=True → hot-reload path unchanged (do not touch that machinery);
   is_self=False → worktree strategy: verify via `quality.project_gate.run_project_gate(workspace,
   checks=("lint","test"))`, commit via harness's existing GitAutomation path, SKIP
   reload_changed_modules entirely, return a commit-result payload.
5. Rewire both call sites (loop.py:2791 and routers/self_improve.py:384) to resolve is_self first.
6. Config: `self_improve.worktree_apply_checks` (default ["lint","test"]); close the code-tier
   approval_id bypass.

Tests: reuse tests/fixtures/external_pyproject (exists, built for WP-E3); TestApplyStrategySelection
in test_self_improve_project_routing.py (is_self=True → hot-reload; False → worktree-commit,
reload_changed_modules assert_not_called); regression: gludd's own path unchanged.

---

## D4 — Slack outbound notifications (Slice 1)

**Placement: `events/slack_notifier.py`** (push/subscriber shape matches events/hooks.py, not the
connectors pull/Source contract; avoids a connectors→events dependency inversion).

Verified seams: events/hooks.py `_ensure_safe_webhook_url` (:24-37, canonical SSRF),
`_redact_payload` (:53-79, `_SECRET_PATTERNS` :43-50), `_fire_webhook` retry/tracked-task pattern
(:233-301); events/bus.py `subscribe` (:23-28) + async-callback scheduling via `_dispatch_coro`
(:57-60, :108-112 — an `async def` subscriber just works); events/types.py has NO
todo_completed/todo_failed/human_todo_created members (grep-verified) — add them + dataclasses
mirroring HookTriggeredEvent (:164-167); publish sites: loop.py after :3147 (`reconciled += 1`,
gated on new_status in COMPLETE/FAILED; `self._event_bus` already stored at :410) and
routers/human_todos.py after :144 (post-commit, before return);
secrets: `resolve(alias) -> str | None` shared by SecretsManager (:286-317) and EnvSecretsManager
(env.py:70-93); EnvSecretsManager is allow-list fail-closed — wiring must call
`allow_env(webhook_secret_alias)` (env.py:61-63, currently zero callers) so env-only deployments
resolve; daemon seam: construct after `app.state._secrets_resolver` assignment (daemon.py ~:1081),
`subsys["bus"]` from :1018 still in scope.

Design: `SlackNotifier(webhook_url, channel_map, rate_limiter, retry_count=1, timeout_seconds=10)`
with `async def handle_event(event)`: per-EventType sliding-window rate limit (new private
`_SlidingWindowLimiter` — no rate-limiter exists in the codebase, grep-verified) → template per
EventType consuming only `_redact_payload(event.payload)` → `_ensure_safe_webhook_url` re-check at
send → async httpx POST with follow_redirects=False + clamped retry (mirror hooks.py). handle_event
awaits the POST directly (EventBus already tracks the coroutine).
Config: `NotificationsConfig.slack: SlackNotificationConfig` — `enabled: bool = False`,
`webhook_secret_alias: str = "slack_webhook_url"`, `channel_map: dict[str, str] = {}`,
`rate_limit_per_minute: int = 20`; `UserConfig.notifications` field. Unresolvable alias → warn +
don't subscribe (fail-soft). Out of scope: two-way Slack.

Tests: fake-transport webhook capture per event type (shape, no raw payload); redaction through
the Slack template; SSRF-blocked URL raises before POST; rate limit drops N+1 per-EventType;
default-off → no subscriptions; allow_env fallback with EnvSecretsManager.

---

## D5 — Compute resource discovery + auto-select (Slice 1: local/k8s/vsphere)

Verified: terraform.py:630-633 hardcodes DC0/Cluster0/datastore0/"VM Network" (pyvmomi only
find_spec-checked at :614, never used); :158-171 k8s missing from the dispatch dict → falls to
_generate_generic; providers.py:225-235 get_cheapest_for_gpu has ZERO callers (grep-verified);
routers/compute.py:139-152 hard-requires provider (422); UtilizationTracker API
(infra/utilization.py:69): register_endpoint/list_endpoints/route_task(min by utilization)/
find_underutilized/find_idle_gpus — ComputeEndpoint has NO cost field (the gap the ranking fills);
wired via daemon.py:2186-2187 + :2256-2266. House k8s style: REST over injectable httpx transport
with the canonical SSRF policy (connectors/kubernetes.py) — prefer that over the kubernetes pip
client. ComputeProvider enum has NO LOCAL member → local discovery is informational-only
(deployable=False) in Slice 1.

Design: new `infra/discovery.py` — `DiscoveredResource` dataclass (provider, kind, cpu_cores,
mem_gb, gpu_type, gpu_count, labels, cost_estimate_usd_hr, deployable, discovered_at) +
`ResourceProbe` Protocol + `LocalProbe` (os.cpu_count, zero deps), `KubernetesProbe` (GET
/api/v1/nodes over injectable transport; no api_server config → [] with zero network calls;
SSRF-check first), `VSphereProbe` (lazy pyvmomi inventory walk; absent/fail → [] and terraform.py
falls back to current literals) + `discover_all(probes, timeout_s=5.0)` with per-probe failure
isolation. routers/compute.py: provider optional when `compute.discovery.enabled` (default
false); `_auto_select_provider` ranks deployable discovered fits by (cost, utilization tiebreak),
falls back to `ProviderRegistry.get_cheapest_for_gpu` (WIRE it — first real caller; its KeyError
for unsupported GPU must surface as a client error, not a 500). Config:
`ComputeDiscoveryConfig(enabled=False, probe_timeout_s=5.0, probes=[local,kubernetes,vmware])`
under `UserConfig.compute` (env `GLUDD_COMPUTE__DISCOVERY__ENABLED`). Disabled default keeps
today's behavior byte-identical.

Tests: tests/infra/test_discovery.py (probe fakes incl. SSRF-blocked api_server → zero transport
calls; per-probe isolation), tests/routers/test_compute_autoselect.py (disabled → unchanged 422;
enabled+fit → provider passed to DeploymentManager; no fit → cheapest-cloud fallback; unsupported
GPU → client error), user-config default/env tests.

---

## D6 — Auto-remediation tick (#52) + file-claim TTL/jitter/ordering (#53)

### #52 — remediation is 100% HTTP-triggered today
Verified: routers/remediation.py:180-245 builds BlockerDetector+RemediationDispatcher per request;
PHASE_ORDER (loop.py:88-105) has no remediation phase (pinned by
tests/e2e/test_obj04_event_loop.py:30); **dead config wiring** — daemon.py:138 sets
`"remediation_config": None` in startup_config which is never merged into daemon_state
(rooted at daemon.py:837), so routers' `daemon_state.get("remediation_config")` always falls back
to defaults — operators cannot configure thresholds; fix in the same change.
Dispatcher actions are DB-side-effect-only and fully audited via RemediationActionRepository
(repository.py:2195-2219).

Design: new `_phase_remediate_blocked_tasks` between check_service_credits and self_improve
(so self_improve's `_collect_recurring_failures` at loop.py:3730-3757 sees post-remediation
state). Interval gate idiom from `_phase_check_compute_utilization` (loop.py:3446-3448):
`remediation_check_interval_ticks` default 30, `<=0` = kill switch (mirrors self_improve at
loop.py:3630). Reuse the tick's `self._active_session`/`self._todo_repo`. Per-tick action cap
`remediation_max_actions_per_tick` default 5. Idempotency: new
`RemediationActionRepository.exists_recent(blocked_todo_id, since)` — skip todos already acted on
within the cooldown (`retry_delay_hours`). Default ON argued: self_improve (riskier) defaults ON;
RemediationConfig defaults are inert on healthy projects (24h/4h/3-requeue); every action audited;
actions are conservative (new todo / retry / human-todo, never code self-modification).
Config wiring: user_config.py RemediationSettings + daemon.py EventLoop config dict (same spot as
compute_idle_check_interval_ticks, daemon.py:1652-1658) + fix the dead daemon_state key.
Tests: PHASE_ORDER pin updates (test_event_loop.py:391-396 + test_obj04:30), new
test_remediation_phase_wiring.py (interval skip, kill switch, action cap, idempotency),
integration end-to-end (blocked todo → exactly one audited action over N ticks),
/admin/remediation/config reflects operator config.

### #53 — precise livelock + fix
Partial fix already landed (loop.py:3200-3309: exponential backoff `2**min(retry_count,6)` +
escape-to-BLOCKED after `_MAX_PUSH_RETRIES = 5` at :3984). Remaining defects:
1. FileClaimRegistry (coordination/file_claims.py:18-139) has NO timestamps; claims via
   `POST /api/coordination/claim` (routers/coordination.py:82-90) from a crashed external worker
   NEVER expire (registry is in-memory) → every future todo touching an overlapping path burns
   5 retries then BLOCKED, forever, until daemon restart — file-path poisoning.
2. Backoff window is a pure function of retry_count checked against `self._total_ticks % window`
   — no jitter → two todos at the same retry_count wake on the identical tick and re-collide
   deterministically.
3. Two live overlapping claims → both defer (no tie-break) — safe but not live.

Fix: (a) claim TTL — `_worker_claimed_at` timestamps, `ttl_seconds` (default 900), overlaps()/
all_claims() treat stale entries as absent; re-claim refreshes (heartbeat semantics);
`reap_stale(now)` + a periodic sweep phase so /api/coordination/claims doesn't show ghosts.
(b) jittered backoff — `offset = sha256(tid) % window`, check `(total_ticks + offset) % window`.
(c) deterministic tie-break — on live conflict, lexicographically-smallest todo_id (or oldest
claim) proceeds; registry exposes ordering metadata.
Tests: test_file_claims_ttl.py (fake clock: stale ignored, refresh, reap); extend
test_event_loop_file_claims.py (backdated dead-worker claim → push succeeds; existing live-claim
retry-escape tests still pass); jitter (two todo_ids differ mod window); ordering (smaller id
proceeds); integration: claim → simulated crash → tick succeeds after TTL, defers before.

---

## D7 — #57 subagent chain guards (full design)

**Verdicts (all four claimed defects confirmed UNPROTECTED, file:line verified):**
1. No max nesting depth — AgentTask.parent_task_id (agents/types.py:48) is write-only; nothing
   walks it; neither production constructor (daemon_wiring.py:148, pipeline/daemon_adapters.py:81)
   even sets it.
2. Escalation — WORSE than claimed: the sole production role-dispatch site hardcodes
   `invoker_name="build"` (daemon_wiring.py:153; build has allowed_subagents=["*"] at
   registry.py:86-87), so every child presents as the maximally-privileged agent. Not exploitable
   yet (only trusted top-level callers), but the capability lattice already grants "role" dispatch
   to self_improve_agent/self_research_agent/coder (capability_lattice.py:109-149) — the moment a
   subagent's tool loop is wired to make_role_handler, a read-only explore agent could dispatch
   general (edit+bash).
3. No A→B→A cycle detection — each hop is a fresh task_id with spoofed invoker; loops end only at
   DEFAULT_DISPATCH_TIMEOUT=1800s (dispatcher.py:25) or budget exhaustion.
4. No concurrency budgets — dispatch_many schedules ALL futures immediately (dispatcher.py:277);
   only throttle is the per-TARGET semaphore (:88-93); _active_count (:60,74-76) is telemetry only.
   FloorController bounds todo claims, not agent tasks; ToolCallLoop caps one agent's loop, not the
   spawn chain.

**Guards:**
- W-D1 chain context (foundation): agents/chain.py ContextVar[AgentTask|None]; dispatch_one
  sets before `await self._executor(task)` (:212), resets in the finally (:265); make_role_handler
  (daemon_wiring.py:143) reads it — parent present → real invoker_name, parent_task_id,
  depth=parent.depth+1, visited_agents=parent.visited|{parent.agent_name}; absent → today's
  behavior exactly (zero regression).
- W-D2 depth cap: AgentTask.depth (validate >=0); max_chain_depth=3
  (GLUDD_AGENT_MAX_CHAIN_DEPTH); fail-closed check before the can_invoke gate (:147).
- W-D3 escalation: falls out of W-D1 (can_invoke sees the true immediate parent every hop);
  regression tests: build→explore allowed, ambient-explore→general denied.
- W-D4 cycles: AgentTask.visited_agents frozenset; reject re-entry (covers A→A too); depth cap is
  the backstop for non-repeating chains.
- W-D5 budgets: per-invoker semaphore (5, GLUDD_AGENT_MAX_CONCURRENT_PER_INVOKER; acquire invoker
  then agent, fixed order = no deadlock), global active ceiling (20, GLUDD_AGENT_MAX_GLOBAL_ACTIVE)
  → status="blocked" mirroring the pause pattern (:130-145), dispatch_many batch cap (100,
  GLUDD_AGENT_MAX_DISPATCH_BATCH) before any future is created, reject max_concurrent<1.

**Tests:** new tests/unit/test_agent_chain_guards.py (house style of
test_can_invoke_daemon_activation.py): depth cap at/below threshold + default-0; ambient-invoker
propagation both ways; A→B→A and A→A rejection; per-invoker isolation (Event-gated executors);
global ceiling → blocked; oversized batch rejected pre-future; PLUS the previously-untested
dispatch_many timeout-cancellation path (:283-296, _result_from_future :313-326) and the
max_concurrent=0 boundary. The 6 existing dispatcher/wiring test files must stay green unmodified.

## D7 supplement — #57 dispatcher coverage gaps (from the guard-gap audit)

To fold into the #57 work item's test plan: dispatch_many timeout-cancellation path
(dispatcher.py:283-296 + _result_from_future :313-326) untested (all tests use the 1800s default);
`max_concurrent=0` creates a never-acquirable semaphore (:91-92) — untested boundary;
gather-results raw-exception branch (:297-310) and MCP-binding bare except (:183-184) untested;
registry `_agents` unlocked dict with no concurrency tests, duplicate registration silently
overwrites (registry.py:27), fnmatch patterns beyond "*"/exact untested; agents/types.py has zero
validation (empty task_id, max_concurrent=0, negative max_steps construct silently) — add minimal
validation when adding the depth/visited_agents fields. Tool-loop budget/safety branches
(per_iteration_timeout :193-206, max_total_tokens :211-223, budget_guard denial :174-186,
adversarial block :225-238, auditor recovery :245-280) untested — separate hardening item.
