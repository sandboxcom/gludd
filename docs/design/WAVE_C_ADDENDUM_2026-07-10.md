# Wave C Addendum — late-session verified findings (2026-07-10)

Extends `WAVE_C_DESIGNS_2026-07-10.md` with findings verified/designed in the
later part of the session. Each entry: verdict + exact locus + fix pointer.
Line numbers are current-tree; re-Read before editing. Same landing discipline
as the parent doc (single-writer per file, targeted tests, CI-green per batch).

## Verified ALREADY-FIXED (move to spec Already-Done; do NOT rework)
- **Integrity C5 H1 (store signing) + H2 (corrupt-store rebaseline)** — FIXED.
  `integrity/scanner.py` signs the baseline store with a sidecar HMAC (`.mac`) +
  monotonic-counter/high-water-mark anti-rollback; corrupt/truncated store raises
  `IntegrityStoreError` (no silent rebaseline); first-run vs deletion distinguished;
  key fail-closed via `GL_INTEGRITY_KEY`. Tests: `TestHashStoreSigned` (7),
  `TestHashStoreAntiRollback` (4), `TestHashStoreDeletedStore`.
- **Per-project secret isolation** — ENFORCED (the "0 callers / unscoped" premise is
  stale). Scoping is in `secrets/project_secrets.py` `ProjectSecretsManager`
  (`_scoped_path` prefixes `projects/<project_id>/`, rejects `/`/`..`), wired
  daemon→gateway→job via `daemon.build_secrets_resolver` `_LazyProjectSecrets.for_project`
  (daemon.py:322-397/1117), `ModelGateway._resolver_for_project` (gateway.py:729-764),
  threaded from tool_loop.py:391/407, job_invocation.py:155, worker/app.py:171,
  langgraph_agent.py:177. Tests: test_project_scoped_secret_wiring, test_secrets_redteam
  TestProjectSecretsScoping. **Residual (LOW, dead code):** `engine.py:637` sync
  `execute()` calls `call_model` without `project_id=job.project_id` — add it for
  consistency (execute() is dead per C-ENGINE).
- **Daemon bind / allowed_cidr** — FIXED. `cli.py:295` `--host` defaults `127.0.0.1`;
  non-loopback host auto-generates a PSK (cli.py:1154-1160); host/port validated
  (cli.py:3006-3049); `infra/compute.py:76` `allowed_cidr="127.0.0.1/32"`. Tests exist.

## STILL-OPEN — new designs

### C12 — events/EventBus: no locking + list-mutation-during-iteration + unbounded webhooks (MED)
**Files:** `events/bus.py`, `events/hooks.py`
- No lock anywhere. `EventBus.subscribe` `self._next_id += 1` (bus.py:24) + `HookSystem.register_callback` `self._next_cb_id += 1` (hooks.py:117) are non-atomic → duplicate ids under concurrency → `unsubscribe` removes the wrong/both. **Fix:** `threading.RLock` in each `__init__`; wrap subscribe/unsubscribe/register/unregister bodies; in `publish` wrap only the snapshot.
- `HookSystem.fire` (hooks.py:177) `hooks = self._hooks.get(event_name, [])` is NOT a snapshot; a callback that `register_callback`s the same event re-`sort()`s the list mid-iteration → silent skip/double-fire. **Fix:** `hooks = list(self._hooks.get(event_name, []))`.
- Webhook concurrency unbounded (`_pending_webhooks` plain set, fresh `httpx.AsyncClient()` per fire, hooks.py:249) → self-DoS. **Fix:** `_MAX_INFLIGHT_WEBHOOKS=50` drop-and-log cap + a shared client with `httpx.Limits`.
- **Tests:** `test_events_concurrency.py` — unique-ids-under-threads, reentrant-register-no-corruption, webhook-concurrency-bounded.

### Admin API rate-limiting + body-size cap (MED, OPEN) — [Finding B of bind/rate audit]
**Files:** new `rate_limit.py`, `daemon.py:2468` middleware, `config/user_config.py`
No rate limiter or body cap on the PSK-gated `/admin/*` `/api/*` surface (the
receiver's `_RateLimiter`+`MAX_BODY_BYTES` at `receiver/router.py:104-243` covers only
`/v1//ingest`). `/admin/self-improve/run` (unbounded LLM spend) + `/admin/security/scan-text`
(no `max_length`) are the concrete unbounded-cost/body endpoints. **Fix:** generalize
receiver's token-bucket into `general_ludd.rate_limit.RateLimiter(rate, burst)`; wire a
keyed rate check into `auth_and_stats_middleware` (429+Retry-After, exempt healthz) + a
separate ASGI body-size middleware (413), with per-path stricter overrides; config block
`admin_rate_limit` on UserConfig. Designs already in feature_package_wiring.md §9 +
observability_receiver.md §1.5.

### rg_search root unconfined + unbounded output (MED, OPEN)
**File:** `code_intelligence/rg_search.py`. `search(root=".")` passes `root` to argv with
zero validation (line 199/129); no output cap. **Fix:** `workspace_root` param;
`Path(root).resolve()` + `is_relative_to(workspace_root)` fail-closed; `MAX_MATCHES` cap
+ `truncated` flag; `--max-filesize` on argv. (Latent — no live MCP tool wraps it yet.)

### Runtime bundle signing self-referential (HIGH, OPEN)
**File:** `runtime/release.py:37-98`. `MANIFEST.json`+`CHECKSUMS.sha256` are co-located
and cross-check each other only — no external signature; tamper-then-rewrite-both passes
(`validate_release` returns valid). **Fix:** mirror `integrity/scanner.py`'s sidecar HMAC:
sign the serialized manifest with `GL_INTEGRITY_KEY` at build (`MANIFEST.json.mac`), verify
with `hmac.compare_digest` before trusting it in `_check_pip_bundle`, fail-closed on
missing/mismatch. Bind release `version` into the signed message for anti-downgrade.

### Ansible process_isolation missing-binary = daemon CRASH, not fail-open (HIGH, OPEN)
**File:** `ansible/core_runner.py`. ansible-runner's own preflight does `sys.exit(1)` when
the isolation executable is absent — a `SystemExit` (BaseException) that slips past every
`except Exception` (core_runner.py:647, runner.py:210, loop.py:754) → propagates uncaught
through `_run_phases`→`tick`→`run_forever`, killing the daemon. **Fix:** preflight
`shutil.which(iso.executable)` and return a failed `AnsibleResult` (fail-closed) when
missing unless an explicit `allow_unconfined_fallback` opt-in; widen the catch to
`(Exception, SystemExit)` as defense-in-depth. Tests: missing-binary refuses run + never
runs unconfined; SystemExit caught not propagated.

### TaskReturnRepository.get_by_id unscoped + TodoRepository.update status-mass-assign (MED/LOW, OPEN)
**File:** `db/repository.py`. `TaskReturnRepository.get_by_id` (621) has no `project_id`
param (unlike every sibling on the class) → a future by-id route leaks another tenant's
return. `TodoRepository.update` `_IMMUTABLE_UPDATE_FIELDS` already blocks `project_id` but
NOT `status` (deliberately mutable) — latent state-machine bypass (no live prod caller
sets status via update). **Fix:** add optional `project_id` to `get_by_id` (default None
for back-compat); add `status` to `_IMMUTABLE_UPDATE_FIELDS` (status only via `transition()`)
+ migrate ~5 test seed sites to `transition()`. Tests: get_by_id scopes; update rejects
status/project_id.

### NEEDS_MORE_WORK dead-end status — failed tasks stall (MED, OPEN)
**Files:** `review/decision_applier.py`, `db/repository.py:424-506`, `event_loop/loop.py`.
A failed task set to `NEEDS_MORE_WORK` is never re-selected (`claim_runnable` only takes
QUEUED) and never auto-promoted → it stalls until the coarse `RemediationDispatcher` spawns
a brand-new todo; the specific test/diagnostic output is never fed back to retry the
original. **Fix:** a bounded auto-retry phase that re-promotes NEEDS_MORE_WORK→QUEUED with
the failure diagnostics attached to the todo context, capped at N attempts, then escalates
to a human-notification (ties to P-2b). Add an attempt-counter field. (Design agent
dispatched — see task output.)

## Cross-cutting design decision (folds three Wave P items into one)
**Unify the P-4 edit-repair loop and P-5 LSP-diagnostics loop into ONE in-engine
fix-loop** in `execution/engine.py` (execute_async 455-598 / execute 600-755): a single
`while attempt < _MAX_FIX_ATTEMPTS` wrapping call_model→apply-edits(collect failures)→
run-tests(P-1)+LSP-diagnostics(P-5), re-prompting with combined feedback (failing hunk +
current file + test/LSP errors) as a new user turn, budget-rechecked per attempt,
partial-success-aware (don't resend applied edits), final exit_code=1 if unresolved. P-4,
P-5, and test-retry must NOT each build a competing loop.

## C-SEC-1 CORRECTION (adversarial review — the parent design was INCOMPLETE)
The WAVE_C_DESIGNS §C-SEC-1 fix (subtract denied in intersection + deny-check at
_enforce_permission/StsIssuer.validate) closes the direct-spec case but NOT the
STS-delegation path it was motivated by. Required additions:
- **Hole 1 (critical):** `StsIssuer.issue()` (sts.py:142-165) stores `subject_spec_request`
  verbatim and `is_subset()` (permissions.py:400-417) never inspects `.denied`, so the
  minted token's `.denied` is whatever the CALLER supplied — the issuer's carve-out never
  enters. Two live callers: `POST /admin/sts/issue` (routers/security.py:298-315, parses
  caller YAML, denied defaults []) and the escalation endpoints (security.py:522/602)
  which `intersection(augmented, human_spec)` — `agent_spec`/issuer_spec is NEVER an
  intersection operand, so an agent/issuer-only deny is invisible. **FIX must also:** merge
  `issuer_spec.denied` (and the human spec's) into the minted spec in `issue()` before
  storing; add sts.py:issue + is_subset + security.py:522/602 to the fix file list.
- **Hole 2:** `StsIssuer.validate()` has ZERO prod callers (only tests); `daemon.py:415-441`
  uses `STSRegistry` which is never assigned (`state._sts_registry` unset) → that scoping
  path always falls through. Fixing validate() is future-proofing; the LIVE exposure is
  Hole 1 (minting) + `_enforce_permission` against directly-configured specs.
- **Hole 3:** empty `denied.actions` is legal + unvalidated → pin the semantics: **empty
  denied.actions == deny ALL actions** (mirror the "empty openbao_paths allow == deny-all"
  precedent). validate() won't catch omission, so encode it in the deny-match logic.
- **Hole 4:** glob "subtraction" in intersection() is NOT generally expressible (literal
  string-set diff leaves `build/*` unchanged when denying one literal key). So step-1
  subtraction is BEST-EFFORT/non-authoritative; the runtime fnmatch deny-checks
  (_enforce_permission / validate) are AUTHORITATIVE. Document this explicitly.
- **Hole 5 (C-SEC-1b ordering contradiction):** a hard `is_subset` reject on
  `requested.max_sts_ttl_seconds > issuer` makes the companion clamp-on-store dead AND
  regresses any sub-3600 issuer to PermissionDeniedError on legitimate requests. **Resolve:
  drop the is_subset hard-reject; keep ONLY the silent `dataclasses.replace` clamp-on-store**
  (max_sts_ttl_seconds is a re-delegation CEILING, not a TTL ask).
- Adjacent (flag, not C-SEC-1): `linux_selinux.py:52` renders denied as `dontaudit`
  (audit-suppression, NOT enforcement) + `typeattribute unconfined_t` → SELinux deny
  rendering is inert. Separate triage item.

## Test drafts ready (full content in task outputs, drop-in)
- `test_permissions_denied_enforced.py` (C-SEC-1, all 3 gates + TTL clamp)
- `test_self_modify_guards.py` + `test_hot_reload_code.py` additions (C-RELOAD)
- `test_integrity_sig_field_injection.py` (C-INTEGRITY)
- `test_model_gateway.py::TestGatewayTimeout` + `test_gateway_base_url_ssrf.py` leak tests (C-GATEWAY)
- `test_budget_guard.py`/`test_budget_guards.py`/`test_spend_limiter.py` reserve/rollover/dedup (C-BUDGET)
- `test_event_loop_dispatch_semaphore.py` (C-EVENTLOOP item 13) + C-SPD1 flush + 4 phase-count updates

## Adversarial review corrections — round 2 (2026-07-10)

### C-GATEWAY — timeout switch has no safe default
**Design being corrected:** C-GATEWAY's `_build_timeout_kwarg` (per-family timeout
plumbing, motivated by the M1 no-timeout bug).
**Hole:** as specced, `_build_timeout_kwarg` is a 3-way string match on
`provider_class_hint` (`ChatOpenAI` / `ChatAnthropic` / `HuggingFaceEndpoint`). But
`ProviderRegistry.from_profiles()` (`models/provider_registry.py:71-101`) lets any
`ModelProfile` register an arbitrary `provider_class_hint` — so any other family
(`ChatGoogleGenerativeAI`, `ChatMistralAI`, `ChatBedrock`, native `ChatVertexAI`, etc.)
falls through with ZERO timeout applied, reproducing the exact M1 bug the design was
meant to close.
**Required design change:** add an explicit fallback branch that attempts a bare-float
`timeout=` kwarg on the unrecognized family, plus a `logger.warning` on that path so
silent no-timeout is at least observable; add a test iterating every `provider_class`
in `PROVIDER_PRESETS` asserting none fall through unhandled. Note `langchain-anthropic`
and `langchain-huggingface` are NOT project deps (`pyproject.toml:47-52` only lists
`langchain-openai`) — those two timeout branches need the packages mocked in tests, not
imported for real.

### C-GATEWAY ↔ C-BUDGET — try/finally scope gap
**Design being corrected:** C-BUDGET's reserve()/release() wrapping of
`_invoke_and_bill`.
**Hole:** the existing try/except in `_invoke_and_bill` (`gateway.py:912-938`) wraps
ONLY `chat_model.invoke()`. It does NOT wrap provider construction
(`gateway.py:824-878`, including the SSRF raises at 833-836) nor the empty-200 guard
raise (`gateway.py:958-961`, which fires AFTER a successful invoke but BEFORE
`record_spend` at 988-989). C-BUDGET's spec is under-specified on which lines the new
try/finally spans, so a naive implementation either reserves budget it never releases
on a construction failure, or fails to release on the empty-200 path.
**Required design change:** C-BUDGET must widen the guarded region to also cover the
empty-200 raise (958-961), releasing (not committing — no real cost is known there) on
that path. `reserve()` must be placed AFTER provider construction (at ~911), never
before line 824, so construction failures (incl. the SSRF raises) fail closed without
ever taking a reservation. Sequencing: land C-GATEWAY first (diff confined to 824-878),
then implement C-BUDGET against a fresh Read of the post-C-GATEWAY file — do not design
C-BUDGET's line ranges against the pre-C-GATEWAY file.

### C-TOOLLOOP — per-response cap burns iteration budget
**Design being corrected:** the per-response tool-call cap for code/bug_fix/refactor/
feature/test work types.
**Hole:** those work types get only `CODE_MAX_ITERATIONS=5` (`execution/tool_loop.py:30`).
A legitimate large honest bundle (e.g. 25 file reads) capped to the default per-response
limit forces the model to re-issue the tail across additional iterations — up to ~40% of
a 5-iteration budget can be spent just recovering the capped remainder, risking a
spurious `ToolLoopExhausted` on otherwise-honest work.
**Required design change:** either add compensating iteration headroom when a
cap-rejection occurred in a prior round, or an explicit non-goal statement accepting the
risk — and either way, a test asserting "capped-then-retried consumes iteration budget"
so the tradeoff is pinned down, not silently rediscovered later.

### C-TOOLLOOP — compaction id-invariant ordering must be explicit
**Design being corrected:** the cap-rejection message injection relative to
`_compact_history`'s verbatim-prefix boundary.
**Hole:** `_compact_history` (`tool_loop.py:340-374`) treats everything from
`open_round_start` (captured at line 241) onward as verbatim/uncompactable. If the
cap-rejection messages are appended BEFORE `open_round_start` is captured, they fall
into the compactable prefix and lose their `tool_call_id` — breaking the id invariant
compaction depends on.
**Required design change:** state as a hard requirement (assert or comment at the
capture site) that cap-rejection messages must be appended AFTER `open_round_start` is
captured, never before.

### C-TOOLLOOP — jsonschema validation is over-strict vs gludd's own builtin
**Design being corrected:** the Draft202012Validator gate in front of `call_tool`.
**Hole:** `WEB_RETRIEVE_TOOL.input_schema` declares `timeout_seconds: integer`
(`mcp/builtins.py:88-93`), but the handler (`mcp/builtins.py:209-213`) tolerates a
stringified `"30"` via `int(timeout_seconds)` + try/except. A strict
Draft202012Validator gate placed in front of `call_tool` would reject the string before
the handler ever runs, narrowing behavior that gludd's own builtin currently accepts.
**Required design change:** add a type-coercion pass before validation, or explicitly
document this as an intentional behavior change (stringified numerics no longer
accepted) so it isn't discovered as a regression later. Confirmed non-issues, no design
change needed: `jsonschema>=4.21` IS a hard dep (`pyproject.toml:44`); the empty-dict
schema skip is safe since `MCPTool.input_schema` defaults to `{}` and is never `None`.

### C-ENGINE async-execute migration corrections
**Design being corrected:** C-ENGINE's migration of the 19 test call sites from
`execute()` to `execute_async()`/`asyncio.run(...)`, and its shutdown-hook claim.

1. **asyncio.run() migration hazard — 3 of 19 sites are already inside a running loop.**
   `asyncio.run()` raises `RuntimeError: asyncio.run() cannot be called from a running
   event loop` when called from within one. Three call sites are already inside a
   running loop and must instead use plain `await engine.execute_async(job)`:
   - `tests/integration/test_full_pipeline_e2e.py:144` (inside `async def
     patched_dispatch`)
   - `tests/e2e/test_daemon_game_building.py:759` (inside a `@pytest.mark.asyncio`
     async test)
   - `tests/e2e/test_daemon_game_building.py:985` (inside a `@pytest.mark.asyncio`
     async test)
   The other 16 sites are plain `def test_...` functions and are fine wrapped in
   `asyncio.run(engine.execute_async(job))`. **Fix:** case-split the 19-site migration
   list into these 3 (await-only, no asyncio.run) vs. the other 16 (asyncio.run).

2. **`test_pg6_deferred_commit.py` spy defeats the await (false-confidence guard).**
   `_spy_defer` (`tests/.../test_pg6_deferred_commit.py:195-201`) has no return
   statement. Item 16 requires `defer_commit` to RETURN the `asyncio.Task` so
   `execute_async` can `await task`. Under this spy, `task=None`, so `await None`
   raises `TypeError` — which item 16's `contextlib.suppress` then swallows. The test
   stays green, but the new await-before-return ordering is never actually exercised.
   **Fix:** the spy must `return original_defer(...)`'s task (not swallow/discard it),
   so the await path is genuinely covered.

3. **Items 3 and 16 must land as ONE batch — explicit sequencing requirement.**
   `tests/unit/test_execution_git_delivery.py::test_commit_message_includes_todo_info`
   (lines 123-141) calls `execute()` then immediately reads git log expecting the
   commit to have already landed. Once migrated (item 3) to
   `asyncio.run(execute_async(...))`, this only passes if item 16's
   await-commit-before-return change is *already in place* — otherwise
   `asyncio.run()` cancels the pending background commit task at loop teardown and
   the git-log read races an unlanded commit. **Requirement: item 16 (await-commit
   ordering fix) must land in the same commit/batch as item 3's test migration, not
   as separate follow-on PRs** — landing 3 without 16 first (or in the same batch)
   reintroduces a flaky/failing test.

4. **`_drain_background_tasks` shutdown wiring is a trivial, precisely-locatable
   one-liner — NOT a missing capability.** Correction to the design's claim that "the
   daemon has no shutdown hook": `daemon.py` `_lifespan`'s `finally` block
   (`daemon.py:2050-2159`) already drains ~12 other components via the same
   suppress-and-await idiom, immediately adjacent to `engine.dispose()`
   (`daemon.py:2141-2142`). The `ExecutionEngine` instance (`execution_engine`,
   constructed at `daemon.py:883`, referenced `daemon.py:1274-1281`) is already in
   scope at that point in `_lifespan` but is simply not drained there. **Fix is a
   one-line insertion** immediately alongside the existing drain calls:
   ```python
   if execution_engine is not None:
       with contextlib.suppress(Exception):
           await execution_engine.shutdown()
   ```
   The design doc must be corrected to say "one-line wiring gap next to the existing
   drain idiom," not "no shutdown hook exists."

**Reconciliation with the cross-cutting fix-loop decision (this doc, lines ~94-101):**
C-ENGINE items 15/16 and the unified P-4/P-5 in-engine fix-loop both rewrite the same
`execution/engine.py` region (~486-561). These are NOT independent and must not be
designed/landed against stale line numbers of each other. **Recommend C-ENGINE lands
first** (its target, `execute()`, is dead code per the Already-Fixed note above, so its
diff is lower-risk and unblocks the async-execute migration sooner); the fix-loop
unification must then be designed against a fresh Read of the post-C-ENGINE
`engine.py`, the same discipline already required for C-GATEWAY→C-BUDGET above.

### C-SELFIMP code-tier approval-gate holes (3 confirmed)

**Hole 1 — Ungated hot-rotation via `queue=="self_update"`.**
`self_update/router.py:94-137` (`admin_self_update_enqueue`) calls `classify()` +
`to_todo_spec()` + `repo.create()` but NEVER calls `apply_plan()`. `to_todo_spec()`
never sets `status`, so `TodoModel.status` defaults to `BACKLOG` (`db/models.py:192`).
Generic `BACKLOG→QUEUED→ACTIVE` promotion reaches `_dispatch_execute_job`
(`loop.py:1889-1891`) which routes `queue=="self_update"` straight to
`_apply_self_update_code` (`loop.py:2826-2947`), which fabricates
`ApplyResult(applied=True, validation_passed=True)` and hot-swaps via
`reload_if_needed` with ZERO approval check (only checks `module:`/`candidate:` tags,
which `to_todo_spec` doesn't even write). The `approval_token` gate
(`self_update/apply.py:278-291`, `apply_plan`) is only invoked by the read-only `/plan`
endpoint. Also `SelfImproveApprovalManager._release` (`self_improve/approval.py:210-215`)
hard-checks `work_type==SELF_IMPROVE_WORK_TYPE` but self-update todos are stamped
`work_type=INFRA` (`priority.py:99`) so they can't even be approved through the
existing surface.
**FIX:** (1) `router.py:94-137` call `apply_plan` before `repo.create`; REFUSED→no
todo; non-APPLIED (CODE tier always lands AWAITING_APPROVAL/VALIDATION_FAILED)→set
`spec["status"]=APPROVAL_REQUIRED`; APPLIED (config/scaffold)→unchanged BACKLOG. (2)
`to_todo_spec`: add optional `status` param. (3) `approval.py:210-215` widen the
work-type gate to also accept `queue=="self_update"`. (4) Defense-in-depth:
`_apply_self_update_code` parse a `tier:` tag, require an explicit `"approved"` marker
tag when `tier=="code"`, fail-closed to FAILED otherwise; stamp `"approved"` tag at
release time.
**Tests must accompany the fix:**
`test_enqueue_code_tier_request_creates_approval_required_todo`.

**Hole 2 — Check-then-act TOCTOU double-submission.**
`routers/self_improve.py:203-306` (`_apply_approved_config_change`) reads
`todo.status==QUEUED` (228), runs expensive `applier.apply()` (288), only THEN
CAS-transitions `QUEUED→ACTIVE` (293). Two concurrent calls both read QUEUED, both
call `applier.apply()` (both write disk), loser's transition raises an uncaught
`ConcurrencyError` → 500 AFTER the duplicate write. Same act-before-claim pattern in
`loop.py:2826-2947` (`_apply_self_update_code`): `reload_if_needed` (2925) runs before
the version-guarded CAS (2944); loser's `ConcurrencyError` is silently swallowed
(2989-2993), masking a double hot-swap.
**FIX:** move the CAS claim `QUEUED→ACTIVE` BEFORE `applier.apply()`; catch
`ConcurrencyError`→`HTTPException(409)`; always land a terminal COMPLETE/FAILED (also
fixes a failed apply stranding QUEUED forever). For `loop.py`: claim exclusivity via a
version-guarded `todo_repo.update` (bump version via the `assigned_agent` field, a
mutable field) BEFORE `reload_if_needed`; `ConcurrencyError`→skip the duplicate
hot-rotation.
**Tests must accompany the fix:**
`test_concurrent_apply_approved_config_change_second_caller_gets_409`.

**Hole 3 — project_id/workspace_root bait-and-switch.**
`routers/self_improve.py:344-379` (`admin_self_improve_apply`) resolves
`workspace_root` from the REQUEST BODY's `project_id` on every call incl. the
approved-apply call, passing it into `AtomicSafeWriter(workspace_root=...)`. Change
content is locked at enqueue but the confinement ROOT is recomputed from whatever
`project_id` the apply-call body carries — an approval enqueued against project A can
be replayed with `project_id=B` to land confined to B's workspace.
**FIX:** stamp `project_id` into the locked spec at enqueue (`_enqueue_config_change`);
in `_apply_approved_config_change` derive `workspace_root` FROM
`spec.get("project_id")` (the immutable stored spec), not the live request body; fall
back to `Path.cwd()` when the spec has no `project_id`.
**Tests must accompany the fix:**
`test_apply_approved_ignores_bait_and_switch_project_id`.

### C-RELOAD concurrency-hole correction (mutex premise refuted + real fix)
Findings verified against the tree:
- There is NO shared/singleton HotReloader. Every caller builds a fresh instance:
  routers/reload.py:87-97 (per-request), reload/self_improve.py:26
  (SelfImprovementWorkflow.__init__), and SelfImprovementWorkflow itself is built fresh at
  routers/self_improve.py:386 AND event_loop/loop.py:2901. So a per-instance `self._lock`
  would protect NOTHING.
- Worse: reload/hot_reloader.py has NO lock at all today (grep confirms locks exist
  elsewhere but none in reload/).
- Concurrent reload IS reachable (not theoretical): POST /admin/reload and POST
  /admin/self-improve/apply are both async FastAPI handlers offloading to
  asyncio.to_thread; two simultaneous requests (or an event-loop self_update reload
  racing an HTTP apply) run read→merge→os.replace→importlib.reload in parallel OS
  threads, each with its own `original_bytes` snapshot. The issue-#70 3-way anti-clobber
  merge does NOT help — it only compares against the caller's own snapshot, blind to a
  second in-flight writer.
- The realpath-based protected-path false-negative (from the earlier review) is REFUTED
  against the current tree: capability_lattice.py is_protected_path/is_collections_path
  operate on lexical `_normalise` only (backslash→slash), NO .resolve()/realpath — so a
  protected-looking path is still caught regardless of symlink target. hot_reloader.py:
  183-204 passes the un-resolved `module.__file__`. No realpath false-negative exists
  here.

CORRECTED FIX:
1. Lock scope: NOT a per-instance lock. Use a MODULE-LEVEL lock table keyed by resolved
   live-file path (dict[str, threading.Lock] behind its own guard lock, at
   general_ludd.reload.hot_reloader module scope), acquired BEFORE
   `original_bytes = live_path.read_bytes()` (hot_reloader.py:213) and held through the
   health-gate decision + any rollback (~line 322). This serializes concurrent swaps to
   the SAME file across independent instances/threads without over-serializing unrelated
   modules. Optionally add an fcntl.flock sidecar lock if reload can ever be triggered
   cross-process.
2. Protected-path check: keep the current lexical check (it already avoids the realpath
   false-negative) but ADD a defensive resolved-form check too, mirroring
   self_update/applier.py::_first_protected which checks BOTH the lexical path AND
   Path(path).resolve().as_posix().lower() and fails closed if .resolve() raises. That
   gives both-direction coverage (protected symlink target AND ../ traversal into a
   protected tree) by reusing an already-hardened sibling pattern.

### MCP tool-surface findings (web_retrieve SSRF is HIGH, live)
- **Finding 1 (HIGH, live bug):** web_retrieve (mcp/builtins.py:203-230) →
  retrieval/web.py fetch_web_page calls urllib urlopen (~line 90) with NO SSRF guard: no
  scheme check, no host_is_blocked, no loopback/RFC-1918/link-local/169.254.169.254
  blocking. Only an OPT-IN domain allowlist (GLUDD_WEB_FETCH_ALLOWED_DOMAINS, off by
  default). It is the ONE live egress path in the repo missing the canonical guard that
  every other path (security/auth.py, skills/fetcher.py, connectors/*, issue_sources/*,
  gateway, git repo) enforces. FIX: wire is_safe_fetch_url/host_is_blocked into
  fetch_web_page before urlopen. (NOTE: a separate worktree agent is implementing+testing
  this fix now — this doc entry records the finding and its acceptance test:
  http://169.254.169.254/, 127.0.0.1, 10.0.0.5 rejected; public https allowed.)
- **Finding 2 (MED-HIGH):** the only gate before any MCP call is coarse
  check_dispatch(role,"mcp") (tool_loop.py:148-149) — all-or-nothing; any role with "mcp"
  may call EVERY tool identically incl run_project_check (shell exec, though
  subprocess-hardened) and external stdio servers (fs write, git, slack, db). No per-tool
  danger tiering. The ToolCallAuditor is purely a quality/efficiency filter, zero
  security semantics. Recommend per-tool capability tiers.
- **Finding 3 (MED):** external MCP server tool `description` and `input_schema` pass
  through UNSANITIZED (transport.py:440-451 → client.py:83-84 → verbatim to model at
  tool_loop.py:163-170); only `name` is regex-validated. A supply-chain-compromised npm
  MCP package could embed prompt-injection in its tool description. Mitigated (not
  eliminated) by operator-only server registration (daemon.py:268-282; untrusted target
  repo's .gludd overlay merges only into UserConfig, not mcp_servers) + version pinning
  (catalog.py._harden_registry_entry). Recommend sanitizing/length-capping external
  descriptions.
- **Finding 4 (LOW-MED):** no local jsonschema validation of tool-call args against
  input_schema for external server tools — args forwarded verbatim to subprocess
  (transport.py:463-469). Ties into C-TOOLLOOP's jsonschema-validation item; note the
  over-strict-coercion caveat already recorded there.

### C-BUDGET reinforcement: the gateway budget gate is structurally DEAD (4 confirmed HIGH)
A concurrency audit CONFIRMED these against the current tree — recorded here as the
concrete failure surface C-BUDGET must close. Cross-reference the existing C-BUDGET
entry above (F3 reserve/commit TOCTOU, and the C-GATEWAY↔C-BUDGET try/finally-scope
correction): these findings are the evidence for why that entry is HIGH, not
theoretical.

1. **Gateway budget gate is a no-op in practice (HIGH).** `ModelGateway._invoke_and_bill`
   (`models/gateway.py:766-989`) calls `check_budget(profile_id, estimated_cost,
   budget_remaining)` at ~679-690, then `chat_model.invoke()` at 913 (no lock), then
   `record_spend(cost)` at 988-989 — a check-then-act with NO reservation. Worse:
   `budget_remaining` defaults to `float("inf")` (`gateway.py:648`) and `estimated_cost`
   defaults to `0.0`, and a grep of every call site (`daemon.py:1898-1906`,
   `job_invocation.py:148`, `execution/engine.py:637`, `self_improve/harness.py:78`,
   `review/reviewer.py:226`, etc.) found ZERO that pass `budget_remaining=`. So
   `check_budget` always passes everywhere — the gateway's own gate is structurally
   dead.
2. **RunBudgetGuard on the main dispatch path is never queried, only accumulated
   (HIGH).** `daemon.py:1206-1216` builds a `RunBudgetGuard` and wires it into
   `ModelGateway(budget_guard=...)`, but on the primary `_gateway_executor` path
   (`daemon.py:1872-1919`) `call_model_with_retry` is invoked with no
   `estimated_cost`/`budget_remaining`, and nothing calls
   `RunBudgetGuard.check_run_budget()`/`check_all_limits()` to GATE a call —
   `record_spend` only accumulates. So the operator-configured `run_budget_usd`/
   `per_call_budget_usd` caps (`bc.daily_limit`/`bc.per_task_limit`) are decorative on
   the main path.
3. **Admission gates are check-then-act with no lock spanning to commit (HIGH).**
   `routers/models.py:498` `check_all_limits(estimated_cost=0.0)` and
   `event_loop/loop.py:981-983` `check_all_limits(...)` read a snapshot, decide, return
   — no reservation, no lock held across the subsequent call_model + record_spend. N
   concurrent requests all read the same pre-commit total, all pass, all bill after —
   overshoot bounded only by in-flight concurrency × per-call cost.
4. **Ad-hoc fallback ModelGateway has no budget_guard (MEDIUM, narrow branch).**
   `routers/models.py:526-538` constructs a fallback `ModelGateway` with no
   `budget_guard=` when `app.state._model_gateway` is unset — its spend never reaches
   the app-level `RunBudgetGuard` whose `check_all_limits` gate (line 498) is
   nonetheless consulted, so that gate can never reflect this path's real spend.

**POSITIVE (already correct — the fix pattern already exists in-repo):**
`BudgetManager` (`controllers/budget_manager.py`) DOES implement a race-free
reserve-then-reconcile (`check_todo_budget`/`check_daily_budget_reserved` hold
projected cost under `_spend_lock` BEFORE the call at 79-111/152-195;
`record_spend`/`release_reservation` reconcile after) and `_gateway_executor` uses it
correctly (reserve 1876/1885, reconcile 1907-1911, release 1917-1918). The codebase
already solved this TOCTOU class once — **C-BUDGET's job is to apply that same
reserve/commit pattern to ModelGateway's own gate and make the RunBudgetGuard
admission checks actually gate** (thread real `estimated_cost`/`budget_remaining`,
hold the reservation across the call). Also NOT bugs (audit confirmed correct):
cache-hit and empty-200 paths correctly skip `record_spend` (empty-200 raises BEFORE
`record_spend` at `gateway.py:948-961`); fallback-chain hops single-bill correctly.

## Round-3 corrections (2026-07-10 deep review)

### 1. Runtime bundle-signing: DROP the HWM, use version-binding + downstream verify
**Design being corrected:** the "Runtime bundle signing self-referential" entry above
(this doc, lines ~56-62), specifically its proposed high-water-mark anti-rollback.
**Hole:** the HWM protects NOTHING. `ReleaseArtifactValidator.validate_release` runs
ONLY at build time (`release_orchestrator.py:49`, invoked via `make release-validate`)
— it is absent from `dist`/`gate`/CI/release-cut, and the bundle is NEVER re-validated
at install/deploy time (`Dockerfile:133`'s entrypoint and `install.sh` do no signature
check at all). Since "the verifying host" is in practice always "the build host," an
HWM stored on/checked by the verifier is an HWM checked by the builder — the same
party that would rewrite it on a rollback. It guards against nothing an attacker
couldn't also rewrite.
**CORRECTED DESIGN** (matches this doc's own OPEN entry, tightened): sign the
canonical manifest with the EXISTING `GL_INTEGRITY_KEY` (do not introduce a new
`GL_RELEASE_SIGNING_KEY` — no provisioning precedent for it), sidecar
`MANIFEST.json.mac`, and BIND the release `version` (and ideally artifact digests)
into the signed message itself. This makes anti-downgrade STATELESS — the bound
version travels with the bundle and is checked by comparing against the
currently-installed version at verify time, with no persistent HWM state needed on
either host. The real security value requires MOVING verification to install/deploy
time on the CONSUMING host: add a fail-closed verify step to the Docker entrypoint /
`install.sh` / daemon-startup path, mirroring the pinned-checksum pattern already
implemented in `filestore/bootstrap.py:118-134`. Also flag: a fail-closed
`GL_*_KEY` requirement breaks the currently-manual `make release-validate` workflow
with no key-provisioning precedent — CI today only provisions `GITHUB_TOKEN` +
`DEEPSEEK_API_KEY`, nothing signing-related — so the design must specify key
provenance per environment (dev/CI/release/install) before this can land.

### 2. Per-project secret isolation: the WORKER path is UNSCOPED — "ENFORCED" claim overstated
**Design being corrected:** the "Per-project secret isolation" entry in the
Already-Fixed section above (this doc, lines ~15-24), which asserts the scoping is
fully wired daemon→gateway→job.
**Hole:** that's true for the daemon path but false for the worker path.
`worker/app.py::build_gateway_from_config` never constructs a project-scoped
resolver at all — its default is a bare `EnvSecretsManager()` (`worker/app.py:106`)
or a bare `SecretsManager(config, permission_spec)` (`worker/app.py:112-115`), neither
of which is project-aware. `for_project` exists ONLY on `daemon._LazyProjectSecrets`
(`daemon.py:394`) — it is never constructed or called anywhere in `worker/`. This gap
is currently masked by a green test: `test_project_scoped_secret_wiring.py:131-142`
asserts a non-project-aware resolver returns the unscoped shared value, i.e. the test
encodes the bug as expected behavior rather than catching it.
**FIX:** promote/share the `LazyProjectSecretsResolver` construction so the worker
factory can build one too (not just the daemon), and wrap it in
`build_gateway_from_config`'s default path; drop the `projects_active` startup gate
that currently short-circuits scoping; do not trust a wire-supplied `project_id`
without authenticating it first (this depends on C20's auth work landing first);
integrate `projects/*` config with the existing `openbao_paths` capability gate so
worker-side secret access is scoped the same way daemon-side access already is.

### 3. C-CONNECTOR resolve_and_pin: SNI alone is insufficient — also set Host header + pinned-IP URL
**Design being corrected:** the connector `resolve_and_pin` design (guarded_get /
DNS-rebinding defense for `nomad.py` and `cilium_hubble.py`).
**Verified:** httpx's `extensions={"sni_hostname": host}` DOES set TLS SNI
independently of the URL host (`httpcore` `_sync/connection.py:107,151-152`) — cert
validation correctly still checks against the original hostname. BUT the TCP dial
target is taken from the URL's host, not from the SNI extension. So pinning is only
real if the URL itself is built with the pinned IP as its host. And once the URL host
is the raw IP, httpx will auto-derive the `Host` header from that IP/netloc unless
told otherwise — which both leaks the pinned IP to the origin and breaks
name-based virtual hosting.
**Required design change:** `resolve_and_pin`'s consumer must set all THREE of the
following together, not just SNI:
1. URL host = the pinned IP (this is what actually gets dialed).
2. `headers={"Host": original_host}` set explicitly (else httpx derives it from the
   IP netloc).
3. `extensions={"sni_hostname": original_host}` (for correct TLS SNI + cert
   validation).
Also note: `cilium_hubble.py` has NO existing default transport today (unlike
`nomad.py`, which does) — wiring `guarded_get` into `cilium_hubble.py` is a NET-NEW
default transport there, not a drop-in swap of an existing one, and should be scoped/
tested accordingly.

### 4. Logging design (LOGGING_CONFIGURATION.md) corrections
Pointer corrections to the logging design, by priority:
- **P0 — `disable_existing_loggers` must be explicit `False`.** `build_dict_config`
  MUST set `disable_existing_loggers: False` in the dictConfig payload. The stdlib
  default is `True`, which would silently disable all ~180 already-imported module
  loggers on the first `install()` call — directly contradicting the design's own
  "no-op by default" claim.
- **P1 — bind `RunContextFilter`'s contextvar at the per-todo coroutine, not the
  outer dispatch phase.** Bind at `_dispatch_execute_job_isolated` /
  `_dispatch_execute_job` (`event_loop/loop.py`), NOT at the outer
  `_phase_dispatch_execute_jobs`. Dispatch runs multiple todos concurrently via
  `asyncio.gather` (`loop.py:1552-1567`); binding at the outer phase would smear one
  todo's run-context onto log lines from a different concurrently-running todo.
- **P1 — the per-task FileHandler cache needs a hard LRU cap.** Unbounded per-task
  file handles is the steady-state failure mode for a long-lived daemon (handle
  exhaustion over the process lifetime), not an edge case to defer — the cap must be
  in the initial design, not a follow-up.
- **P2 — validate pluggable handlers before dictConfig; close discarded handlers on
  hot-reload.** Before passing a pluggable handler class into dictConfig, validate it
  is actually a `logging.Handler` subclass (fail closed / skip + warn otherwise); when
  a hot-reload replaces a previously-configured handler, explicitly close the
  discarded one rather than leaking it.

## Round-4 findings (2026-07-10 night audits)

### H-HUMANTODO-OWNERSHIP (HIGH) — human-in-the-loop facility has NO ownership/tenant check
**Files:** `db/repository.py:2004-2157`, `db/models.py:842-892`, `routers/human_todos.py:187-342`,
`routers/security.py:183-218`, `renderers/runner.py:227-229`.
`HumanTodoRepository.get/mark_done/dismiss/mark_in_progress/supersede/add_tag/remove_tag`
(db/repository.py:2004-2157) take only `human_todo_id` — no agent_id/session_id/project_id
param, no caller comparison. `HumanTodoModel` (db/models.py:842-892) has NO `project_id`
column at all (unlike `TodoModel`/`RemediationActionModel`). The router endpoints
(routers/human_todos.py:187-342) never check the row's agent_id/session_id vs caller.
Every dispatched agent holds the same admin PSK (renderers/runner.py:227-229 injects
`GLUDD_AUTH_PSK` into the agent subprocess env), so ANY agent can read/resolve/dismiss ANOTHER
agent's human-todo. WORST: routers/security.py:183-218 `_sync_escalation_from_human_todo`
(invoked from the generic PATCH handler human_todos.py:280-310) flips a linked
permission-escalation to approved/denied when ANY human-todo carrying the
`escalation:<id>` tag transitions to done — with NO check that the resolver is a real
human or belongs to that escalation → a PSK-holding agent can PATCH the
escalation-linked human-todo with a self-supplied `human_resolver` and record a
permission escalation as human-approved WITHOUT real review. Precedent for the missing
guard: `AgentMessageModel` DOES have a project-ownership guard
(`tests/unit/test_agent_message_ack_project_scope.py`). **FIX:** add a `project_id`
column to `HumanTodoModel` (migration) + thread ownership (agent_id/session_id/project_id)
into every get/mutate and reject on mismatch (mirror `AgentMessage.ack`); the
escalation-sync must verify the resolver is a human + the todo belongs to the
escalation's requester. **TEST:** agent A cannot resolve agent B's human-todo;
escalation cannot be self-approved via the human-todo PATCH.

### H-HUMANTODO-FLOOD (MED) — no rate limit / creation cap / sanitization on human-todos
**Files:** `routers/human_todos.py:72-145`, `cli_human_todos.py:118-142`,
`routers/security.py:130-137`.
No rate limit / per-agent creation cap on POST /api/human-todos (human_todos.py:92-145);
body has no max length (`CreateHumanTodoRequest.body` `Field(min_length=1)` only, :72);
tags list has no max count/length on create. An agent floods the operator's queue /
storage. Content is not sanitized before CLI display (cli_human_todos.py:118-142 prints
raw title/body/tags — ANSI/control-seq terminal-injection). Escalation notification
f-string mixes trusted instructions with attacker-controlled `esc_row['reason']`
(routers/security.py:130-137) — a fake "Resolve via..." line could social-engineer the
operator. **FIX:** per-agent/per-category creation cap + body/tags length caps + strip
control chars on display + delimit/escape untrusted segments in notifications. **NOTE:**
the new TIME_TIMERS_SCOPED_NOTIFICATIONS feature sits on this store — it MUST add the
project_id column + ownership + flood cap as part of its own design (recurring timers
amplify the flood gap).

### H-SKILL-SHADOW (MED) — project skills shadow global skills BY NAME (prompt-injection, not RCE)
**Files:** `daemon.py:2251-2252`, `skills/loader.py:11-52`, `daemon_wiring.py:98-104`,
`skill.skill.py`.
A target repo's `.gludd/skills/*.md` shadows a global skill BY NAME (daemon.py:2251-2252
"last write wins", registers project skills AFTER global). Text-only (no exec —
skills/loader.py:11-52 does `yaml.safe_load` + strict pydantic; `make_skill_handler`
daemon_wiring.py:98-104 returns `skill.body` as a string, never executed), so it's
prompt-injection/policy-override, NOT RCE. But dormant fields `Skill.tools`/
`Skill.model_profile` (skill.skill.py) — if ever wired into a permission grant, the
shadowing becomes privilege-escalation. **FIX:** for the `general_ludd`/reserved skill
namespace, refuse project-tier shadowing of global/operator skills (or mark provenance +
never let a project skill grant tools); flag the dormant tools/model_profile fields as
future-wiring risk.

### H-GATEWAY-EXC-SCRUB (LOW, defense-in-depth) — unredacted str(exc) sinks near header-bearing clients
**Files:** `gateway.py:931/1504` → `failover.py:44`, `job_invocation.py:160`,
`quantization.py:154`, `openrouter_discovery.py:52`.
~5 unredacted `str(exc)` sinks near header-bearing clients. SAFE today (httpx/openai
`__str__` embeds URL+status+body, NOT the Authorization header), but add a header-scrub
as defense-in-depth in case an SDK's exception shape changes. Keys themselves are
alias-resolved + project-scoped + caller-override-rejected + the one DEBUG line is
`***REDACTED***` (gateway.py:904-909) — CONFIRMED no live leak.
