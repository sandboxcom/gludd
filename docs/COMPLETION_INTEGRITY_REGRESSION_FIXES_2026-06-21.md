# Completion-Integrity Regression Fixes (2026-06-21)

A post-remediation code review (agent ad2ae61a) of the committed completion-integrity
work on `test/coverage-recovered` found the build green (11744 tests, 0 collection
errors) but caught one HIGH + several MED regressions — the same "green tests / inert
feature" failure mode the remediation set out to eliminate. Fixes below were applied to
the MAIN checkout working tree (`/Users/shawnwilson/gludd`), **NOT committed, NOT pushed**
— they extend the commit-map (ae5b9d2e) which predates them. None are in the alpha.3 ship
(ship = `fe85b9e9` on integration/alpha3-rc = C2 + worker-flake only).

## HIGH — tool-call loop dead in production — agent ad0aa459 ✓ FIXED
CONFIRMED dead-in-prod: `ModelResponse` (gateway.py:103-108) had no `tool_calls` field;
`_call_with_tools`→`gateway.call_model` returns a ModelResponse, so tool_loop.py:109
`getattr(response,"tool_calls",None)` was ALWAYS None → `if tool_calls:` never fired →
no MCP tool ever dispatched. Provider AIMessage.tool_calls survived only inside
`raw_response`, which tool_loop never inspects. The dispatch path had ZERO test coverage
(`bind_tools` in no test) — that's why it went unnoticed.
FIX (gateway.py): added `tool_calls: list[dict[str,Any]] | None = None` to ModelResponse;
new `_extract_tool_calls(raw_response)` normalizer (langchain-flat / nested-OpenAI / SDK
objects → `{"id","type":"function","function":{"name","arguments":<json-str>}}`, with an
`isinstance(list/tuple)` guard so MagicMock stubs don't raise); `_invoke_and_bill` extracts
tool_calls before the empty-200 guard (now `if not str(content).strip() and not tool_calls`),
populates ModelResponse.tool_calls, and does NOT cache tool-call turns (incomplete — must
not replay). Added `TestToolCallsReachToolLoopProductionPath` (3 tests, real provider-shaped
raw response + real bind_tools + real ToolCallLoop dispatching to MCP — no preset mock).
Results: 3/3 new pass, 18/18 test_model_gateway pass, 11875 collected clean, lint clean.

## ⚠ TWO FACTS FOR THE COMMIT (found by ad0aa459)
1. **commit-map (ae5b9d2e) is INCOMPLETE.** It used the stale session-start `git status`
   (which showed only M Makefile/.claude/settings.json/uv.lock) and MISSED uncommitted
   src/ changes — gateway.py had ~54 lines of pre-existing uncommitted completion-integrity
   work (bind_tools/F6) BEFORE ad0aa459's ~110, and timeout_detector.py has ~+60 uncommitted.
   The MED/HIGH fixes added more (loop.py, reviewer.py, daemon.py + tests). **At commit time
   run a FRESH `make git-status` + `make git-diff` to capture the true set — do NOT trust the
   ae5b9d2e file list.**
2. **Pre-existing FAILING test on test/coverage-recovered:**
   `tests/unit/test_gateway_circuit_breaker.py::TestNoDoubleCount::test_breaker_does_not_trip_below_threshold`
   fails at the bare `chat_model.invoke()` (gateway.py:401) BEFORE any fix region — driven by
   the untouched `_overload_max_retries=10` cap vs the test's `max_retries=1` assumption.
   Independent of all fixes here. OPEN — must be reconciled before this branch is gate-green
   (either fix the retry-cap/test mismatch or quarantine with rationale).

## MED — FIXED (report-only, uncommitted)

### loop.py blocking review on async loop — agent a773c8d3 ✓
`event_loop/loop.py:528` `_review_in_process` (async) called the sync blocking
`self._reviewer.review_return(...)` directly → froze the event loop every review.
FIX: `decision = await asyncio.to_thread(self._reviewer.review_return, task_return,
candidate_todos=[], artifacts=[])` (matches sibling paths ~497/505). `asyncio` already
imported. Test added to `tests/unit/test_event_loop.py` (asserts to_thread used). 17 passed.

### reviewer JSON fence extractor not string-aware — agent ab2e8c1c ✓
`review/reviewer.py:181` `_extract_json_from_output` brace-scanner miscounted on `}`
inside string values + failed on trailing prose → valid reviews clobbered to "failed".
FIX: replaced manual scanner with `json.JSONDecoder().raw_decode(candidate, start)`
(handles quoted braces + ignores trailing content); fence regex loosened to `\{.*?` so
raw_decode owns closing logic; slice `candidate[start:_end]` (_end is absolute index).
4 tests added to `tests/unit/test_return_review.py`. 8 passed.

### daemon router/tracker wiring order (CA-T7/CA-T8) — agent a6b09338 ✓
`daemon.py` lifespan built `AdaptiveRouter` via `_get_or_create_extended_subsystems`
BEFORE `app.state._health_tracker` was assigned → router got None → health-filter never
fired (inert). FIX: pre-create `ModelHealthTracker()` + assign to app.state before the
subsystems build; pass `health_tracker=` into AdaptiveRouter; later block reuses the same
instance so router + ModelGateway share one live tracker. 2 tests added to
`tests/unit/test_daemon.py`. 11747 collected, healthcheck PASS.
NOTE: a6b09338 also edited a stale worktree copy (agent-a7ccb192/.../daemon.py) — that's
throwaway; the main-checkout edit is authoritative.

## MED — ALSO FIXED (second wave)

### gateway.py circuit-breaker hole + max_retries-ignored bug — agent ab9c8d2b ✓
HOLE (gateway.py ~591-610, `call_model_with_retry`): unhealthy primary + empty fallback_ids
fell through to the tenacity retry loop, hammering the unhealthy primary. FIX: unconditional
`raise RuntimeError(...)` (fail-fast) at the end of the unhealthy-primary block (matches the
all-fallbacks-exhausted error at ~712).
PLUS a REAL PRODUCTION BUG found via the failing test: `TimeoutRetryPolicy.decide()` used
`_overload_max_retries` (always 10) for OVERLOAD kinds (PROVIDER_ERROR/RATE_LIMITED → 503/429),
IGNORING the caller's `max_retries`. So a 503 retried up to 10× regardless of config. FIX: hard
cap in `_is_retryable` (~644): `if _attempt_counter[0] > max_retries: return False` — makes
max_retries binding for ALL kinds (prod default 3 now caps overload at 3, was uncapped 10).
The test (max_retries=1 → 2 invocations) was correct; the gateway was wrong. 4/4 + 18/18 pass.

### daemon `_quantization_tracker` never set (CA-T9) — agent a3fdb223 ✓
`AdaptiveRouter._quantization_map` (dict[model_id,(precision,confidence)], consumed in
scoring/router.py:199-208 `_apply_quantization_penalty`) was always `{}` because
`app.state._quantization_tracker` was never assigned before the router build (the
`/admin/quantization/detect` endpoint populates it at runtime). FIX: pre-assign a live
`QuantizationTracker()` (models/quantization.py:342) to app.state before the router build,
mirroring CA-T7. +2 tests, healthcheck pass.

### execution/engine.py wrong call_model signature — agent a4c9c27e ✓
`engine.py` (~289 execute_async, ~417 execute) called `call_model(system_prompt=,user_prompt=)`
→ TypeError swallowed into "Model call failed". FIX: `call_model(profile_id, messages=[...])`
(mirrors job_invocation.py:101 / tool_loop.py:176). VERIFIED `ExecutionEngine` is genuinely
UNUSED in production (zero non-test imports; `ToolCallLoop` is independent, NOT a subclass) —
DELETION CANDIDATE, conservatively kept. 91 engine tests pass.

### tests/unit/test_daemon_coverage_lift.py vacuous patches — agent ab23142c ✓
(see above — 3 tests repointed origin→daemon-bound names, 49 pass.)

## Still OPEN — deferred (post-ship, lower priority)
- **daemon.py:1381,1396 `run_until_complete` in live handlers**: latent RuntimeError if the
  dispatcher path is wired (handlers built but EventLoop currently has no dispatcher). Overlaps
  audit-HIGH CA-D1 (task #6, deliberately deferred — own change + test).
- **Delete `ExecutionEngine`** (confirmed dead code) as a deliberate follow-up — requires
  migrating/removing ~7 test files; not urgent.

## ⚠⚠ COMMIT BLOCKER + WORKTREE-DUALITY (2026-06-21, found by a5f0ad31)
Delegated agents' make-only Bash runs inside the git worktree `.claude/worktrees/agent-a7ccb192`
(branch worktree-agent-...), which has DIVERGED from the main checkout (test/coverage-recovered).
Absolute-path Read/Edit hit MAIN; relative paths + `make test` hit the WORKTREE. So fixes/tests
split across trees and agents' "tests pass" can mean "pass in the worktree," not MAIN.
- CONFIRMED SPLIT: the rules-engine runtime fix (acd27dc20) — its TESTS landed in MAIN
  (test_event_loop.py: test_evaluate_rules_runs_after_claim_in_phase_order,
  test_rules_fire_on_live_claimed_todos) but the SOURCE edit did NOT. MAIN loop.py STILL has
  PHASE_ORDER evaluate_rules(idx4) BEFORE claim_runnable_todos(idx6), and _phase_evaluate_rules
  (~line 643) STILL reads self.config.get("todos", []) not _tick_state["claimed_todos"]. → those
  tests FAIL against MAIN. COMMIT BLOCKER.
- CONFIRMED COHERENT IN MAIN (a5f0ad31 direct read): daemon.py (health@664, quant@676, MCP
  dispatcher build@514/call@827, trackers before subsystem build, router reads live@1137),
  gateway.py (tool_calls@121 + _extract_tool_calls@124 populate@412/488; circuit-breaker@594 +
  max_retries cap@550/575/651). loop.py: blocking-review-to_thread@528 + silent-skip@916 PRESENT;
  rules-fix ABSENT (the split above).
- MAIN static gates PASS: test-count 11753/0-err, ruff clean, mypy 0/402, healthcheck OK.

## ✅ TOPOLOGY RESOLVED (a37973da) — commit recipe
- TRUE checkout `/Users/shawnwilson/gludd` is on branch **`fix/self-update-sec`** (per .git/HEAD),
  NOT test/coverage-recovered (harness env label is stale). Completion-integrity work is in ITS
  working tree.
- `make` runs in worktree `agent-a7ccb192` (HEAD fe85b9e, ship-derived). **`make git-*` CANNOT
  commit the true checkout** (it stages the worktree's index; `make git-log` ignores REF=). To
  commit the true checkout, add a target using `git -C /Users/shawnwilson/gludd ...` (or cd there).
- FIX PRESENCE in true checkout: **14/17 PRESENT** (gateway tool_calls/breaker/max_retries,
  reviewer raw_decode, engine sig, loop to_thread+silent-skip+tracing, daemon trackers+MCP
  dispatcher, comparison key-fix, test_model_gateway/return_review/observability/event_loop
  tool_calls+fence+key+to_thread/rules/tracing tests, test_daemon tracker tests,
  test_daemon_mcp_dispatch integration test, both docs). **3 MISSING from true checkout:**
  1. **loop.py 4c rules-source** (PHASE_ORDER still has evaluate_rules@idx4 BEFORE
     claim_runnable_todos@idx6; `_phase_evaluate_rules` L640-656 reads `self.config.get("todos")`
     not `_tick_state["claimed_todos"]`). The CORRECT impl is in the worktree
     `.claude/worktrees/agent-a7ccb192.../src/general_ludd/event_loop/loop.py` (acd27dc20's edit) —
     port it (move evaluate_rules after claim_runnable_todos in PHASE_ORDER + read claimed_todos).
     The matching tests ARE already in the true checkout's test_event_loop.py → they FAIL until
     this source lands. **COMMIT BLOCKER until ported.**
  2. breaker/max_retries UNIT tests in test_model_gateway.py (prod code present; integration ok).
  3. mcp-dispatch UNIT test in test_daemon.py (prod present; integration test covers it).
- VERDICT DOC: `docs/COMPLETION_INTEGRITY_VERDICT_2026-06-21.md` (a4337d3): ~37 functional / 7 inert.
- AT COMMIT TIME: confirm intended branch (fix/self-update-sec vs test/coverage-recovered), port 4c,
  add a `git -C` commit target, run affected tests against the TRUE checkout, then commit per map.

CORRECTED COMMIT PLAN (MAIN-THREAD DRIVEN — do NOT delegate to worktree agents):
1. After a4cdb14 (tracing, last loop.py editor) finishes, on the MAIN THREAD apply the rules
   SOURCE fix to MAIN loop.py: move "evaluate_rules" in PHASE_ORDER to AFTER
   "claim_runnable_todos"; change _phase_evaluate_rules to iterate _tick_state["claimed_todos"]
   (additive to config["todos"]) building {"todo": <dict>} contexts keyed by real todo_id.
2. Run the affected tests AGAINST MAIN via main-thread make (cwd=MAIN): test_event_loop.py,
   test_daemon.py, test_model_gateway.py, test_observability.py, test_return_review.py — to catch
   ANY other tree-split, not just rules.
3. Reconcile every failure (apply missing source edits to MAIN), re-run, THEN commit per the map.
4. Verify a4cdb14's tracing edit + the model-comp fix actually landed in MAIN too.

## SECOND-WAVE AUDITS (broader "are features real?" sweep, 2026-06-21)

### LIVE-VERIFIED ✓ — tool-calling works against real z.ai (agent a6a46d1445)
With the tool_calls fix, glm-4.6 @ api.z.ai/api/coding/paas/v4 was driven end-to-end:
enumeration ✓, file-write ✓ (file appeared on disk with exact content), git tool-call ✓ —
the model→tool_calls→ToolCallLoop→effect chain executes. (Was dead before the fix.) glm-4.6
DOES support OpenAI tool-calling. Test: scripts/verify_zai_toolcall_live.py + make target
test-zai-toolcall-live (uncommitted).

### HIGH — daemon MCP dispatch INERT (agent a30dc5ac found; a612df15 fixing)
The ToolCallLoop path works, but the DAEMON's EventLoop path drops model-emitted MCP tool
calls: daemon.py (~L713-739) builds MCPClient + passes mcp_client= to EventLoop but NEVER
passes dispatcher= and never binds an mcp_handler to mcp_client.call_tool. loop.py (~L986-995)
then hits `_dispatcher is None` → "no dispatcher wired — skipping dispatch" → call DROPPED.
Even with a dispatcher it would fail-closed (capability_denied role None — overlaps the
security-P1 bare-AgentRegistry() finding). FIX IN FLIGHT (a612df15): wire DynamicDispatcher +
mcp_handler→call_tool + a permitted role + integration test. ALSO: mcp/transport.py bunx
dual-def bug rejects bunx launchers by default (audit-HIGH D8, see POST_SHIP doc).

### Test-quality fully closed (agents ab23142c + a873ff26) ✓
All vacuous test_daemon_coverage_lift.py patches (lines 85/97/109) repointed origin→daemon-bound
names + assertions tightened (isinstance EnvSecretsManager + MockMgr.called). 49 pass, non-vacuous.

### HIGH/feature-gap — model-driven self-improvement INERT (agent a6473a542) — answers user Q
"does using a model for gludd self-improvement work?" → NO, INERT (not fixed — substantial,
deliberate wiring needed):
1. daemon `_phase_self_improve` (loop.py:1325, interval 10) runs but uses NO model — static
   gap-analysis (SelfImprovementHarness: file walks + coverage.xml); its todos are gated
   `APPROVAL_REQUIRED` (auto_queue:false) → never claimed/dispatched.
2. self_update pipeline (UpdateRequestRouter keyword→plan no-model; UpdateApplier = real
   fail-closed apply w/ PROTECTED_PATH_MARKERS) is ORPHANED — zero production callers,
   test-only; scripts/gludd_update.py emits a todo spec but never calls apply().
3. /admin/self-improve/apply → SelfImprovementWorkflow.apply_improvement writes NOTHING
   (in-memory) → ReloadManager.execute_reload = no_op; real HotReloader only reached if
   set_code_target is armed, which nothing calls.
4. Tests mock the model + disk-write — no e2e generate→write→validate→rollback coverage.
FIX (substantial, post-ship): wire generation→UpdateApplier.apply with a real SafeWriter;
make apply_improvement actually write + arm set_code_target; operator-choice auto_queue:true.
Files: event_loop/loop.py, daemon.py, self_improve/harness.py+gate.py, self_update/applier.py+
router.py, routers/self_improve.py, reload/{self_improve,manager,hot_reloader}.py.

### connectors + dynamic_dispatcher audit (a88791258): connectors FUNCTIONAL (wired via
wire_observability @daemon.py:1432); dispatch WIRED-but-INERT — daemon.py:785 bare
AgentRegistry() (empty) + EventLoop built without dispatcher (loop.py:160 accepts, daemon
omits) → loop.py:987-994 warn-and-skip. Same cluster as the MCP-dispatch HIGH (a612df15 fixing).

### rules engine PARTIAL (af854c2b) → fix in flight (acd27dc20)
Rules load + evaluate + override model/prompt profile correctly, BUT `_phase_evaluate_rules`
(loop.py ~640) iterates `config["todos"]` not the live `_tick_state["claimed_todos"]` → rules
never fire at normal runtime; and ROUTE/PAUSE_QUEUE/REDUCE_BUCKETS actions are silently dropped
(only SET_MODEL_PROFILE/SET_PROMPT_PROFILE handled). Fix (acd27dc20): evaluate live claimed
todos. (Qualifies task#7 "rules engine fixed" — engine works, runtime wiring didn't.)

### budget enforcement PARTIAL (aca7e3596)
Genuine pre-call blocks exist (SpendLimiter try_charge, RunBudgetGuard.check_all_limits gating
dispatch phases loop.py:695-702/456-463, BudgetManager gating _gateway_executor daemon.py:851-866)
but ALL are OPT-IN (only active when operator sets spend_window_usd>0 / finite budget.*); a
DEFAULT run has NO active spend cap. The gateway's own run_budget_usd ceiling is STRUCTURALLY
INERT: gateway.call_model defaults estimated_cost=0.0/budget_remaining=inf and no caller threads
real values, AND ModelGateway is built without budget_guard= (daemon.py:625-638) so record_spend
never runs either. restore()/persistence works. FIX (overlaps security-P1 "budget not threaded",
see POST_SHIP doc): wire budget_guard into ModelGateway + thread estimated_cost/budget_remaining
from callers; consider a sane default cap. NOT yet fixed (ceiling-full; deferred to the
gateway/security-P1 work).

### daemon MCP/agent dispatch — FIXED (a612df15) ✓
Wired `build_event_loop_mcp_dispatcher`: a DynamicDispatcher(role="event_loop", mcp_handler=
make_mcp_handler→mcp_client.call_tool, skill_handler) passed as dispatcher= to the EventLoop.
Used role="event_loop" (lattice already grants {role,mcp,skill}) → sidesteps the empty-registry/
capability_denied trap WITHOUT needing default_registry (so security-P1 item untouched). Added
`_sync_bridge` (dispatcher.dispatch is sync but call_tool is async + runs inside the live loop).
4 integration tests (fail-before/pass-after), 33 pass, lint/mypy/healthcheck clean. role-kind
handler left async-unbridged (follow-up). ⚠ STILL OPEN: the WORKER path (worker/app.py:99-107)
also never wires a dispatcher (a86e5ac3) — worker-executed tool calls still dropped; needs the
same wiring there.

### task-specific model routing — PARTIAL (a741221d, user Q "do model weights work?")
The weighting ALGORITHM is FUNCTIONAL + prod-wired + PROVEN (scripts/verify_routing_live.py:
SECURITY_FIX picks high-quality model, DOCUMENTATION picks cheap one — selection diverges purely
from weights_for(task_type); router invoked daemon→event_loop→gateway→provider). BUT dormant by
default: (a) NO model-profile YAMLs ship (profiles load only from a runtime config dir; clean boot
= 1 "default" model), and (b) empty BenchmarkRepository → route() returns fallback=
"insufficient_historical_data" and the loop uses the todo's static model until ≥2 enabled profiles
+ benchmark history exist. So correct + live-proven but latent out-of-the-box.

### workflow/pipeline — PARTIAL (a86e5ac3, user Q "did workflows work?")
The "pipeline" = EventLoop tick-phase orchestration (PHASE_ORDER claim→dispatch→review→reconcile,
31 real ansible playbooks). Linear path (model→edit→test→git→review) FUNCTIONAL + live-proven
(tests/e2e/test_pipeline_live_zai.py, real glm-4.6, skip-guarded on key only). Tool-call branch
INERT — same dispatcher-None gap (loop.py:987-993); the live e2e test does NOT cover the
tool-dispatch branch so its green doesn't certify it. (Daemon path now fixed by a612df15; worker
path still open.)

### benchmark recording — FUNCTIONAL ✓ (a8fd2d6e)
daemon.py:887-891 injects a live AutoBenchmarkRecorder+BenchmarkRepository into
event_loop._benchmark_recorder post-construction; loop.py:1015 guard fires in prod;
record_result persists to DB; AdaptiveRouter consumes it (closes the write→read routing loop).
Only inert bit: job_invocation's unused recorder param (redundant — loop-level covers it).

### runtime/ package — INERT dead-code, but NOT on ship path (a6e1249c)
container.py/pip_bundle.py issue real subprocess builds, release.py does real validation, but
the whole package has NO live caller except the dev-only `make release-validate` (Makefile:1806,
itself untested); profile.py/validator.py have zero callers. Build/install tests are mock-only.
⚠ KEY: the alpha.3 "Build and Release" job builds via `uv run pyinstaller gludd.spec` + manual
tar/sha256 (build.yml:91-246) and NEVER imports general_ludd.runtime — so this inertness does
NOT affect the ship. It's an unused packaging island (delete-or-wire as a deliberate decision),
not a broken release feature. Low priority.

### coverage-critic (ab80816b): ~19 working + ~12 fixed + ~7 second-wave verified / ~8 still
inert-or-unverified.

### G3 ansible runner — FUNCTIONAL ✓ (abb4d1e) — the critical-path leg works
Real CoreAnsibleRunner (drives ansible-core PlaybookExecutor.run() at core_runner.py:521, NOT the
pip ansible-runner pkg), instantiated at runner.py:67 + daemon.py:655, wired into EventLoop
(daemon.py:833-865, http_client=None → in-process live path) AND the worker (app.py:35-39,195-229).
Real side effects (mkdir, artifact JSON, commands); 30 playbooks syntax-validated. So the flagship
chain's FINAL leg (model→tool→PLAYBOOK EXEC→review) executes for real. Residual = test-coverage:
no real-playbook e2e in the default `make test` gate (only out-of-gate molecule noop runs one).

### observability — both WIRED-but-inert (a126ff8b)
TRACING: ExecutionTrace/spans/RecentTracesBuffer/OTelBridge all real + endpoints reachable, but
NO ExecutionTrace is ever built in the dispatch path (loop.py:191 _active_traces stays empty); the
loop uses a record_job_benchmark shortcut that bypasses trace→record_from_trace→buffer, so
/api/traces always returns count 0. Fix-assessment in flight (ab42c32c... a tracing agent).
MODEL-COMPARISON: live admin endpoint over a real SQL query BUT (a) bypassed by the daemon's own
_metrics_facet (re-implements ranking inline), and (b) a confirmed KEY-MISMATCH BUG — comparison.py:
54,56 read avg_code_quality/avg_token_efficiency but repo emits avg_quality/avg_efficiency →
those two fields always 0; tests' mocks ENCODE the bug. Quick fix dispatched.

### runtime/ = inert dead-code, NOT on ship path (a6e1249c) — see above. benchmark recording
FUNCTIONAL (a8fd2d6e). In flight: static-gate validation of all accumulated fixes (a5f0ad31),
W8.2-W15 ansible roles (ab42c32c), model-comparison key-fix + tracing-wiring assessment.

## STATUS: all ad2ae61a review findings remediated (1 HIGH + all MEDs); tool-calling
live-verified; daemon MCP-dispatch HIGH fix in flight; test-quality closed. Deferred:
run_until_complete (CA-D1/task#6), ExecutionEngine deletion. All report-only, feeding the
authoritative a975e5d6 commit-map. Use a FRESH git-status at commit time.

## Commit guidance
These edits append to commit-map group "(a) AI-feature remediation fixes" (or a new
"completion-integrity regression fixes" commit). Files touched: `event_loop/loop.py`,
`review/reviewer.py`, `daemon.py`, + tests `test_event_loop.py`, `test_return_review.py`,
`test_daemon.py` (+ tool-call HIGH files once ad0aa459 lands). Re-run `make test-count`
before committing; gate via CI (full local gate OOMs).
