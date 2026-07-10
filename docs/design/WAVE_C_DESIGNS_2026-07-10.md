# Wave C — Turnkey Implementation Designs (2026-07-10)

Status: **design-complete, not yet implemented.** Each section is a self-contained,
line-anchored spec any implementer (human or LLM) can execute against current
`master` (HEAD `1c6afab0`). Line numbers are current-tree at authoring time —
re-confirm with a Read before editing, they drift. Implement **after** the
`v0.1.0-beta.3` cut so the release SHA stays clean; land each section as a
single-writer batch per its file ownership, run its listed tests, then
`make gate-async` before claiming the finding closed.

Source ledger: `docs/AGENTIC_IMPLEMENTATION_SPEC.md` §3.3 + `docs/audit/BACKLOG_FINDINGS_2026-07-01.md`,
re-verified 2026-07-10. Severity per the compiled Wave C sweep.

---

## C-SEC-1 — Permissions: `denied` grants are inert (HIGH, live today)

**Files:** `src/general_ludd/security/permissions.py`, `security/sts.py`, `secrets/manager.py`

`intersection()` (permissions.py:459-489) builds `out_caps` only from
`a.capabilities × b.capabilities`; `denied_union` (~:482-489) is computed but
**never subtracted**. `StsIssuer.validate()` (sts.py:167-173) and
`_enforce_permission()` (secrets/manager.py:242-272) only call
`capability_for(...)` and never consult `.denied`. Net: an operator's
"allow read on build/* EXCEPT prod-signing-key" carve-out is silently ignored;
`prod-signing-key` stays readable. Reachable on every secret access — no new
caller needed.

**Fix:**
1. `intersection()` — after building `out_caps`, subtract each `denied_union`
   entry from any surviving same-`resource` capability: action-set difference +
   path/constraint containment; drop the capability if the remainder is empty.
2. `_enforce_permission()` and `StsIssuer.validate()` — add an explicit
   deny-check at the **top**, before the allow-check runs. Grep `capability_for`
   for any other call site that also needs the deny gate.
3. Matching semantics follow the `Capability` dataclass shape (actions list +
   constraints dict, e.g. `openbao_paths`): a deny matches when resource equals
   and the denied path-pattern set intersects the allow path-pattern set.

**Tests:** `tests/unit/test_permissions_denied_enforced.py` — the prod-signing-key
carve-out scenario (deny beats broad allow) at all three gates; an STS token
minted from a deny-bearing spec still refuses the denied path.

## C-SEC-1b — STS re-delegation TTL ceiling not clamped (MED, latent)

**File:** `security/sts.py:142-165`, `permissions.py:399-417`

`issue()` caps `expires_at` via `min(ttl, issuer_spec.max_sts_ttl_seconds)` but
stores `spec=subject_spec_request` **verbatim** — the stored spec's own
`max_sts_ttl_seconds` is never clamped. `is_subset()` never inspects it. Dormant
(no current 2-hop caller reuses `token.spec` as `issuer_spec`) but a landmine for
any future nested-delegation feature.

**Fix:** in `issue()`, store
`dataclasses.replace(subject_spec_request, max_sts_ttl_seconds=min(subject_spec_request.max_sts_ttl_seconds, issuer_spec.max_sts_ttl_seconds))`;
add a `max_sts_ttl_seconds` ceiling check to `is_subset()`.
**Test:** a re-delegated spec cannot raise its own TTL ceiling above the issuer's.

---

## C-RELOAD — Reload family: TOCTOU + symlink bypass + concurrency + debounce (HIGH/MED)

**Files:** `security/capability_lattice.py`, `reload/hot_reloader.py`. Land as one batch.

### M3/C14 — symlink & `..` bypass (`capability_lattice.py:205-236`)
`_normalise` only does backslash→slash; `is_protected_path` matches lexically, so
a symlink named `innocuous.py` pointing into `.claude/` evades every check. Two
call sites (`capability_lattice.py:252`, `self_update/apply.py:154`) so the fix
must live **inside** `is_protected_path`.

**Fix:** add `import os` (line 25); resolve `norm = os.path.realpath(_normalise(path)).lower()`
inside a `try/except OSError` (fall back to lexical on symlink-loop) **before**
the segment/substring/stem checks. The existing segment check still fires because
`realpath` on a relative `.claude/...` keeps the `.claude` segment literal — all 8
existing `test_self_modify_guards.py:232-252` assertions keep passing (targets
don't exist on disk, so only the CWD prefix resolves).
*Adjacent residual (flag, don't fix here):* `is_collections_path` (209-212) and
`self_update/apply.py:132-142 _is_hard_denied` share the same lexical gap.

### H1/M1/M4 — one non-blocking reload mutex (`hot_reloader.py`)
`os.replace` (line 274) **is** atomic — never the bug. The race is the
read(213)→merge(224-267)→swap(274) straddle: a second writer can replace
`live_path` between the read and the swap, so the authenticity decision is
computed over stale bytes. Fix = a single instance-wide **non-reentrant,
non-blocking** `threading.Lock` around the whole window (rules out deadlock: a
caller either gets it immediately or is refused immediately).

**Fix:**
- imports: `import threading` (after line 10); add `Iterator` to the
  `collections.abc` import (line 12).
- add `class ReloadBusyError(Exception)` after line 36 (always caught at the
  public boundary, never escapes).
- `__init__`: add `min_reload_interval_s: float = 0.05` param; init
  `self._reload_lock = threading.Lock()`, `self._min_reload_interval_s`,
  `self._last_reload_at: dict[str,float] = {}`.
- add a `@contextlib.contextmanager _reload_slot(self, key)` that does
  `self._reload_lock.acquire(blocking=False)` (raise `ReloadBusyError` if held),
  then a per-`key` debounce check (raise if `elapsed < min_interval`), `yield`,
  stamp `_last_reload_at[key]` on success, `release()` in `finally`.
- wrap the bodies of `reload()` (92-116, key `f"scope:{scope.value}"`),
  `reload_code_module()` (170-326, key `f"module:{module_name}"` — leave
  `scope`/`details` at 168-169 outside), and `reload_changed_modules()` (365-436,
  key `"changed_modules"`) in `with self._reload_slot(key):` + an outer
  `except ReloadBusyError` → failed `ReloadResult`. `with` runs the `finally`
  release on every existing early-return path, so no per-branch edits.

**Tests:** `test_self_modify_guards.py` — symlink-escape-into-protected-dir,
`..`-traversal-through-symlink, `check_self_modification` refuses symlink escape.
`test_hot_reload_code.py` — concurrent reload serializes (H1), reload-storm is
debounced 1-of-20 (M4), re-entrant reload from a `health_check` callback is
refused not deadlocked (M4).

---

## C-SELFIMP — Self-improve code-tier `approval_id` bypass (HIGH)

**File:** `routers/self_improve.py:343-406`

The "legacy/code-tier" branch (381-406, any `kind` not in `_CONFIG_TIER_KINDS =
{"config","yaml"}` — i.e. `code`, `role`, or empty) runs
validate→apply→reload with **zero** `approval_id` gate.
`SelfImprovementWorkflow.apply_improvement` applies unconditionally on
`validation.success`, so the gate must live in the router (as it does for
config-tier).

**Fix:** add `_code_tier_apply` / `_enqueue_code_change` /
`_apply_approved_code_change` (insert before `def register`, ~line 306),
mirroring the config-tier trio exactly:
- no `approval_id` → enqueue an `APPROVAL_REQUIRED` self-improve todo capturing
  the spec (`plan_artifact = json.dumps({kind,title,description,worktree_path})`),
  return `{"status":"approval_required","approval_id":...}`, **run nothing**.
- with `approval_id` → require the todo be `work_type == self_improve` and status
  `QUEUED` (human-released), load the spec from `plan_artifact` (**never** the
  request body — anti-bait-and-switch), run validate/apply/reload via
  `asyncio.to_thread`, consume on success `QUEUED→ACTIVE→COMPLETE`.
- fail-closed 503 when no session factory (can't record approval).
Replace the 381-406 branch with `return await _code_tier_apply(app, kind, payload)`.
All needed imports already present.

**Tests (update 2 existing + add 5):**
- update `test_completion_audit_wiring.py::test_apply_endpoint_uses_workflow` and
  `test_self_improve_wiring.py::test_non_config_kind_skips_update_applier` to
  expect **503** (no-DB fail-closed) instead of the old 200/applied shape.
- add `TestSelfImproveApplyCodeTier`: no-approval→enqueued-not-applied;
  valid-approval→proceeds + replay-refused-409; unreleased-approval→409;
  no-DB→503; recorded-spec-used-not-body.

---

## C-GATEWAY — Provider timeout (MED) + SSRF alias leak (MED)

**File:** `models/gateway.py`

**M1 (no timeout):** provider construction passes no `timeout=` at
`get_chat_model` (ctor line 516) or `_invoke_and_bill` (ctor line 878) → SDK
default (openai/anthropic 600s) unbounded by the gateway. Only 3 wrapper families
exist: `ChatOpenAI` (accepts `httpx.Timeout`), `ChatAnthropic` (float),
`HuggingFaceEndpoint` (int). **No litellm in tree.**

**Fix:** `import httpx`; add 4 `DEFAULT_MODEL_*_TIMEOUT_S` constants (connect 10 /
read 120 / write 30 / pool 10) and 2 `ModelProfile` fields
`connect_timeout_s`/`request_timeout_s` with a `>0 & finite` validator; add
`_build_timeout_kwarg(profile)` returning the correctly-shaped kwarg per
`provider_class_hint` (httpx.Timeout for ChatOpenAI, float for ChatAnthropic,
`int(ceil(read))` for HuggingFaceEndpoint); at both ctor sites do
`init_kwargs.update(_build_timeout_kwarg(profile))` **before** the caller-kwargs
merge (line 876) so callers can still override.

**M3 (alias leak):** `SSRFRejectionError` at 833-836 (and the parity site 511-514)
embeds the **resolved** `{base_url!r}`, re-raised un-redacted. **Fix:** put only
`profile.api_base_alias` in the exception text; send the resolved URL to
`logger.debug`. (The caller-supplied-base_url message at 864-867 is not a leak.)

**Tests:** `test_model_gateway.py::TestGatewayTimeout` (capture-provider asserts
`httpx.Timeout` threaded, per-family shapes, profile override, zero-timeout
rejected); `test_gateway_base_url_ssrf.py` — blocked-alias error does NOT contain
the resolved host but DOES contain the alias name; resolved URL still at DEBUG.

---

## C-INTEGRITY — Non-canonical HMAC payload (MED, forgery/replay)

**File:** `integrity/scanner.py`

`sign_change` (603), `verify_signature` (622), `sign_change_openbao` (661),
`verify_openbao_signature` (700) build the HMAC message with raw `"|".join(...)`.
`file_path`/`path` are attacker-influenceable and may contain `"|"`, so
`["foo|bar","ct",...]` and `["foo","bar|ct",...]` collide to the same MAC → an
approval for a benign path is replayable against a boundary-shifted path. (The
store MAC is already safe — int-counter-prefixed.)

**Fix:** add `_canonical_sig_payload(fields) = json.dumps(fields,
ensure_ascii=True, separators=(",",":"))` + domain-separation scheme tags
`_SIG_SCHEME_CHANGE = "gl-integrity-approval-v2"` /
`_SIG_SCHEME_OPENBAO = "gl-integrity-openbao-approval-v2"` as the first signed
field. Rewrite all four functions to sign the ordered list
`[scheme, ...str()-coerced fields...]` via the helper (sign/verify lockstep;
`json` already imported). **Migration = fail-closed:** old signatures stop
verifying → `verify_*` already returns `False` → those approvals lapse and are
re-approved on next scan. No accept-both fallback (that keeps the vuln live).
Document "pending approvals must be re-approved once" in release notes.

**Test:** new `tests/unit/test_integrity_sig_field_injection.py` — pipe-collision
tuples produce **distinct** MACs (fails on old code); sign→verify round-trip still
passes; boundary-shifted forgery does not verify. Freeze `scanner_mod.time.strftime`
for the openbao case; reset `scanner_mod._INTEGRITY_KEY` around each test.

---

## C-ENGINE — ExecutionEngine async hygiene (HIGH verify + MED×3)

**File:** `execution/engine.py`

**Item 3 — sync `execute()` (600-755) is DEAD CODE.** Only construction site is
`daemon.py:1273`; the only consumer (`daemon.py:2683-2694`) reads attributes and
never calls `.execute()`. All 19 `.execute()` callers are tests.
**Recommendation: delete 600-755** (the async `execute_async` is a functional
superset) and migrate the 19 test call sites to `asyncio.run(engine.execute_async(job))`;
add `test_execution_engine_has_no_sync_execute_method` (`assert not hasattr(...,"execute")`)
as a resurrection guard.

**Item 15 + 16 (call site 551-561):** `_run_tests` runs blocking on the loop, and
the deferred commit races the test read of the same tree. **Fix:** make
`defer_commit` (431-453) return the `asyncio.Task` (+ a done-callback that logs
`task.exception()` at ERROR); in `execute_async`, capture the task, `await` it
(under `contextlib.suppress(Exception)`) **before** tests, then run
`test_exit_code, test_summary = await asyncio.to_thread(_run_tests, self.workspace_path)`.

**Item 17:** `_background_tasks` (300) never drained. **Fix:** add
`self._bg_tasks_lock = asyncio.Lock()`; add `_drain_background_tasks(cancel=True)`
(snapshot-under-lock → cancel → `gather(return_exceptions=True)`) + a `shutdown()`
that calls it — mirror `event_loop/loop.py:474-494`. **Wiring gap to flag:**
`daemon.py` has no shutdown hook, so wire `execution_engine.shutdown()` wherever
the daemon lifespan teardown lives, else item 17 is itself unreached.

**Tests:** no-sync-execute guard + migrated scenarios; `_run_tests` via to_thread
(spy) + loop-not-frozen; commit-before-test ordering (`events == ["commit","test"]`)
+ tests-run-even-if-commit-fails; shutdown drains / noop-when-empty / cancels-pending.

---

## C-GITAUTO — git_automation lock/timeout gap + tag dash-injection (MED/LOW)

**File:** `git_automation/repo.py`

**GA-1:** `init_repo` (221-237), `create_worktree` (465-493), `remove_worktree`
(526-544), `list_worktrees` (546-581), `create_release_tag` (760-770),
`create_checkpoint_tag` (772-783), `create_local_bare_mirror` (821-828), and
`push_to_remote` (785-819, has timeout but no lock) call raw `subprocess.run`,
bypassing `_run_git`'s `git_repo_lock(realpath(cwd))` serialization +
`_GIT_TIMEOUT_SECONDS`. **Fix:** route each through
`self._run_git(*args, _cwd=<same cwd used today>, check=<same>)` — `_run_git`
already supports `_cwd` and translates timeout→`CalledProcessError(124)`.
`create_local_bare_mirror` needs `os.path.abspath` on both args first (it has no
`cwd` today) then `_cwd=abs_repo`. `push_to_remote` keeps `check=False` and maps
`returncode==124` to its `PushResult(...,"timed out")` — verified compatible with
`test_push_to_remote_timeout.py` unmodified.

**GA-tag-dash:** `tag_release` (271-273) and `tag_checkpoint` (275-277) lack
`_reject_leading_dash` + `--`. **Fix:** add `_reject_leading_dash(tag, kind="tag name")`
and reorder to `self._run_git("tag","-a","-m",f"Release {tag}","--",tag)` /
`self._run_git("tag","--",tag)`.

**Tests (update 3 + add):** update `test_git_automation_coverage.py` tag/mirror
assertions for new arg shapes; add leading-dash-rejected (`mock_run.assert_not_called()`),
per-method "routes through `_run_git`" structural tests, a contention/serialization
test (no `index.lock` collision), and a timeout-carrying test for `create_worktree`.
**Known residual to track:** a `GitAutomation(worktree_path)` locks on the
worktree path, a different key from the main repo's — shared refs/objects not
serialized; close later by locking on `git rev-parse --git-common-dir`.

### GA-1 worktree-lock fix (adversarial-review correction, CONFIRMED, 2026-07-10)

Adversarial review closes the "known residual" above: **the worktree lock buys
zero protection today, not just a weaker key.**

`locking.py` `_git_dir` (120-131) uses `os.path.isdir(repo_path/.git)` — a
worktree's `.git` is a **file** (gitlink), so `isdir` is `False` → `_git_dir`
returns `None` → the cross-process `flock` is **skipped entirely** for every
worktree. `_normalize` (86-96) separately keys the in-process `RLock` on the
worktree's own `realpath`, so two worktrees get two distinct `RLock`s. Net:
`GitAutomation(worktree_path)` from `loop.py:3373` (`_try_commit_completed_work`,
H6 delivery) gets a private in-process lock and **zero** flock — concurrent
writers across the main repo and its worktrees are unserialized against shared
`refs`/objects. Also: `push_to_remote` (`repo.py:785-819`) has **zero** locking
of any kind (raw `subprocess.run`, not even the flock GA-1 above adds via
`_run_git`).

**Fix:**
1. Resolve the git **common dir** via `git rev-parse --git-common-dir` (git
   itself parses the gitlink for us; this reports the *main* repo's `.git` for
   both a plain repo and any of its worktrees). Cache the result keyed by
   `realpath(repo_path)`, and use that cached common-dir as the single lock key
   in **both** `_normalize` and `_git_dir` — this is what makes a worktree and
   its main repo serialize on the same lock.
2. **Guard:** `os.path.exists(repo_path/.git)` as a cheap pre-check before
   spawning the `git rev-parse` subprocess — preserves zero-subprocess cost for
   not-a-repo-yet paths, and keeps tests that probe nonexistent paths (e.g.
   `/tmp/repo`, `/repo`) from spawning git with a bad cwd.
3. **Only cache SUCCESS.** Never cache `None` — a repo that doesn't exist yet at
   probe time must not be poisoned forever; self-heal (re-probe) if the cached
   common-dir has since vanished.
4. **`push_to_remote`:** wrap its existing raw `subprocess.run` in `with
   git_repo_lock(repo_path):` directly. Do **NOT** route it through `_run_git` —
   on timeout `_run_git` **raises** `CalledProcessError(124)` rather than
   returning a result, so a `returncode == 124` check in `push_to_remote` would
   be dead code and the exception would propagate uncaught, breaking
   `test_push_to_remote_times_out_cleanly`. Keep `push_to_remote`'s own
   subprocess call and timeout handling as-is; only add the lock around it.

**Test:** `test_worktree_lock_blocks_while_main_repo_lock_held` (new, in
`tests/unit/test_git_locking.py`) — deterministic (two threads/processes,
one holds the main-repo lock, assert the worktree-path lock attempt blocks
until release). Fails today (worktree flock is skipped), passes after the fix.

---

## C-TOOLLOOP — per-response cap (MED) + arg schema validation (MED) + key injection (LOW/MED)

**Files:** `execution/tool_loop.py`, `dispatch/variable_store.py`

**Item 10:** the `for tc in tool_calls:` at 240-242 has no bound on calls bundled
into one response (`max_iterations` only bounds rounds). **Fix:** add
`MAX_TOOL_CALLS_PER_RESPONSE = 20` (mirror `routers/dispatch.py:26
MAX_CALLS_PER_REQUEST`, "D-16"); truncate to the first 20, and append an answering
`role:"tool"` "cap exceeded" message for each rejected `tool_call_id` (never
orphan an id), then `tool_calls = accepted`. Add a drift-guard test asserting the
two constants are equal.

**Item 11:** `tc_args` decoded at 244-255 is never validated against
`input_schema` (163-170) before `call_tool`. **Fix:** `from jsonschema import
Draft202012Validator` (hard dep, pyproject.toml:44); build `schema_by_name` after
170; add `_validate_tool_args(args, schema)` (empty schema = no-op for back-compat);
before the auditor gate (256), if a tool has a schema and args fail it, append a
`role:"tool"` validation-error message + `continue` (no unvalidated call reaches
`call_tool`).

**Item 22:** `variable_store.py:109` `safe_name = name.replace(".","_DOT_").replace("-","_DASH_")`
doesn't escape the literal `"last"`, which collides with the unconditional
`dispatch__last__*` sentinel keys (116-120). `result.name` is model-controlled
(`dynamic_dispatcher.py:290`). **Fix:** add `_RESERVED_DISPATCH_NAMES =
frozenset({"last"})` + `_safe_dispatch_name(name)` that appends
`_RESERVED_TOOLNAME` when the sanitized name is reserved; use it at line 109.

**Tests:** `test_tool_loop_guards.py` — over-cap truncated + rejected ids answered,
exactly-at-cap all run, constant-drift guard; args-match-execute,
missing-required-rejected-without-call, wrong-type-rejected, no-schema-skips,
multi-violation-all-reported. `test_variable_store_dispatch.py` — tool-named-`last`
escaped + still readable, doesn't leak into sentinel, substring `last_check`/`lastly`
unescaped, survives render cycle.

---

## C-SPD1 — SpendRepository flush phase (MED, P1)

**Files:** `event_loop/loop.py`, tests. (SpendLimiter watermark API **already
landed** — do not re-add it.)

`SpendRepository.add()` (repository.py:1773) has zero production callers, so
`daemon._restore_persisted_spend` always rehydrates an empty table → spend state
lost across restart. The **only** missing piece is the writer.
`SpendLimiter` already has `_seq`/`_last_flushed_seq`, `unflushed_records()`
(452-466), `mark_flushed(upto_seq)` (468-482), and self-seeding `restore()`
(438-444). Record tuple is `(seq, ts, cost_usd, project_id)` — seq FIRST.

**Fix:** add `_phase_flush_spend_ledger` to `event_loop/loop.py` (after
`_phase_check_service_credits`): interval-gated on
`config.get("spend_persist_interval_ticks", 60)` (`<=0` disables), reads
`unflushed_records()`, writes each via `SpendRepository.add(ts, cost_usd,
kind="token", project_id=...)`, commits, then `mark_flushed(max_seq)` **only after
commit** (self-healing; dedup honored by the watermark). Insert `"flush_spend_ledger"`
into `PHASE_ORDER` between `check_service_credits` and `remediate_blocked_tasks`
→ **length 17→18**. No daemon config wiring required (pure `.config.get` default).

**Phase-count test updates (all four):** `test_obj04_event_loop.py:12-31`
(add to expected list), `test_event_loop.py:533-534` (17→18),
`test_audit_gaps_e2e.py:52` (17→18), `test_event_loop_session_per_tick.py:44-46`
(16→17, since one phase raises).

**Tests:** watermark dedup after restore; phase persists records + no-duplicate on
second call + kill-switch + None-factory noop; e2e "spend survives simulated
daemon restart" (the test that would have caught this dead-code bug — a unit test
constructing `SpendRepository` directly cannot).

---

## C-BUDGET — reservation TOCTOU (MED) + rollover leak (MED) + projected-cost (MED) + restore-dedup (LOW)

**Files:** `controllers/budget.py`, `models/gateway.py`, `controllers/budget_manager.py`,
`budget_guard_check.py`, `models/job_invocation.py`, `execution/tool_loop.py`,
`review/reviewer.py`, `controllers/spend_limiter.py`

**F3 TOCTOU (item 7):** `RunBudgetGuard.record_spend` (budget.py:27-34) has one prod
caller — `gateway.py:989` inside `_invoke_and_bill` — after an unbounded network
round-trip that follows the read-only `check_all_limits`. Two concurrent callers
both pass the pre-check then both bill, blowing the cap. **Fix:** add
`reserve(estimated_cost)→token` / `commit(token, actual)` / `release(token)` to
`RunBudgetGuard` (folds outstanding reservations into the run-budget check under the
existing `_spend_lock`); in `_invoke_and_bill`, reserve before the provider call
(raise `BudgetExceededError` if denied), `try/finally` release on any early exit,
commit-or-fallback-to-record_spend on success. `check_all_limits` stays as the cheap
read-only advisory pre-check elsewhere.

**F6 rollover leak (item 8):** `_reset_daily_if_needed` (budget_manager.py:260-266)
zeroes `_daily_spend` but not `_daily_reservations` — a stale hold reconciles a
`amount - stale_reserved` delta against the fresh day's ledger. **Fix:** add
`self._daily_reservations.clear()` on rollover (leave `_todo_reservations` — clearing
that is a different bug).

**F1 projected-cost (item 9):** `budget_pre_check` defaults `projected_cost=0.0` at 3
sites (job_invocation.py:119, tool_loop.py:175, reviewer.py:219) → purely reactive.
**Fix:** extract `estimate_projected_cost(gateway, profile_id, guard)` into
`budget_guard_check.py` (mirror the already-fixed `engine._projected_cost`), thread a
real estimate into all 3 sites.

**F5 restore-dedup (item 21):** `spend_limiter.py restore()` (353-444) always extends
with no dedup — a replayed snapshot double-counts. **Fix:** add a lifetime
`self._restored_keys: set[(ts, cost, pid)]` keyed on the **raw pre-clamp** identity;
skip duplicates.

**Tests:** reserve→commit records actual; outstanding-reservation blocks second
reserve (the TOCTOU regression); concurrent-reserve-cannot-exceed-cap; commit-unknown-
token-noop; rollover clears reservations + doesn't corrupt new day; 3 sites thread
nonzero cost = engine's; restore-twice-no-double-count + survives-pruning + per-project.

## C-EVENTLOOP — executor saturation (MED) + unbounded gather (MED) + tick-pin (CLOSED)

**File:** `event_loop/loop.py` + new `concurrency/executors.py`

**Item 14 — VERIFIED CLOSED.** `_run_phases` (746-756) is strictly sequential; the
only fan-out phase already threads per-job `_session_override`/repo overrides
end-to-end (`_dispatch_execute_job_isolated` 1603-1645, honored at 1900/3026). No fix.

**Item 12 — dedicated bounded executors.** `asyncio.to_thread` always targets the
default pool; ~20 provider/git/test-run sites contend. **Fix:** new
`concurrency/executors.py` — singleton `provider_executor()` (16), `git_executor()`
(8), `testrun_executor()` (4, doubles as subprocess admission control), env-overridable,
plus `run_on(executor, fn, *a, **kw)`. Migrate the enumerated provider sites
(engine.py:492, tool_loop.py:385/399, langgraph_agent.py:172, loop.py:1144/2007/2155/3771,
worker/app.py:350, routers/models.py:579/722), git sites (loop.py:3385/3412/3420/3428,
issue_ingestor.py:110, daemon.py:1900, routers/maintenance.py:38-39), and test-run
sites (loop.py:1038/2647/3131, decision_applier.py:37, renderers/runner.py:239,
worker/app.py:493, mcp/builtins.py:197) to `run_on(<category>_executor(), ...)`.

**Item 13 — dispatch semaphore.** `_dispatch_jobs_via_scheduler` (1552-1601) gathers
over every batch todo unbounded. **Fix:**
`self._dispatch_semaphore = asyncio.Semaphore(config.get("scheduler_max_concurrent_dispatch", 8))`
in `__init__`; wrap the concurrent branch body in an `async with` `_bounded_dispatch`
coroutine. Gather/timeout/cancel unchanged.

**Tests:** executors singleton/distinct/sizes/env-override + saturated-testrun-doesn't-
starve-provider; semaphore bounds peak + configurable + transparent-when-not-bottleneck.

## C-TODOMODEL (D-07-b) + C-FILESTORE — blob caps + decompression-bomb + atomic write

**Files:** `db/models.py`, new `alembic/versions/027_...py`, `filestore/bootstrap.py`, `filestore/store.py`

**D-07-b:** `TodoModel` has 13 unbounded `Text` cols. **Fix:** add
`MAX_TODO_BLOB_LEN = MAX_JSON_BLOB_LEN * 4`; append 13 `_len_check(...)` to
`TodoModel.__table_args__` (11 at 64 KiB, `description`/`plan_artifact` at 256 KiB).
New migration `027` (down_revision "026", **confirmed unclaimed**) mirrors 026's
`batch_alter_table("todos", recreate="always")`. Generic parity test auto-covers;
add a positive-presence pin test. Update 026's stale "out of scope" docstring.

**Filestore:** (1) `_extract_executable_member` (bootstrap.py:300-330) unbounded
`.read()` = decompression-bomb. **Fix:** `_read_bounded_member` streams 1 MiB chunks,
raises `ValueError` past `_MAX_DOWNLOAD_BYTES`; narrow `except` to `tarfile.TarError`.
(2) `store.py:113-118 write_bytes` non-atomic. **Fix:** `tempfile.mkstemp` same-dir →
write+fsync → `os.replace` → unlink-on-failure.

**Tests:** oversized-member rejected + nothing-stored; atomic write no-partial/temp on
failure, preserves original on overwrite-failure.

## C-CONNECTORS — `_associate_by_window` poisoned-anchor (LOW) + C-WORKERAUTH (CLOSED)

**File:** `connectors/base.py`. `_sort_by_ts` itself is guarded — the residual is
`_associate_by_window` (378-398): line 380 filters only `is not None`, so a `ts=NaN/Inf`
reaches the anchor arithmetic (389) where `ts - anchor_ts > window_s` is always `False`
once anchor is non-finite → every later record silently merges into one poisoned group.
**Fix:** filter via `_is_missing_ts`, sort via `_sort_key_ts`; drop the unused
`import operator`. **Test:** NaN/Inf-ts dropped, two clusters form.

**C-WORKERAUTH — VERIFIED CLOSED.** `worker/app.py` `_psk_auth_middleware` is
fail-closed by default via `load_auth_posture` (`require_auth=True` with no PSK → 503),
backed by `test_auth_posture_default_secure.py` + `test_daemon_auth_redteam.py`. Only
nit: stale comment at 261-262 (cosmetic).

## C-LANGGRAPH + allow_auto_promote (MED dormant / LOW)

**Files:** `execution/langgraph_agent.py`, `event_loop/loop.py:2363-2369`,
`agents/capabilities.py:198-219`, `self_improve/gate.py`

**LangGraph budget bypass:** `get_chat_model` returns a raw provider client that never
bills; `LangGraphAgentLoop` takes no `budget_guard`; 3 construction sites don't forward
it. (Fixing it surfaces that `_resolve_chat_model` never called `get_chat_model` — the
path is non-functional today, why it's dormant behind `use_langgraph_tool_loop=False`.)
**Fix:** add `_BudgetGuardedChatModel` wrapper (pre-check + post-call `record_spend`,
delegates via `__getattr__`); thread `budget_guard` through `__init__`; make
`_resolve_chat_model` call `get_chat_model` then wrap; forward at loop.py:2363-2369
(parity with ToolCallLoop :2460) and `make_langgraph_tool_loop`.

**allow_auto_promote dead knob:** `gate.py:29,42` never passed by the sole prod
constructor. **Recommendation: DELETE** (auto_queue already covers "skip
APPROVAL_REQUIRED"; a second unwired approval-bypass knob is pure attack surface). Add
`test_constructor_rejects_allow_auto_promote_kwarg` resurrection guard + TASKS.md rationale.

**Tests:** resolve-calls-get_chat_model, denied-when-budget-exhausted,
records-spend-on-success, event-loop-forwards-guard; gate default-requires-approval,
auto_queue-bypasses, rejects-removed-kwarg.
