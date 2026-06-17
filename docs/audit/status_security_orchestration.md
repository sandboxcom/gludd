# Status Audit — Security / Orchestration Issues (#51, #55, #57, #58, #63, #64, #70)

Read-only verification performed against the **working tree** (uncommitted state) on
branch `master`, commit `5f7a453` (HEAD). Verdicts reflect what is actually on disk
and what the test suite does *right now* — not what HEAD alone would do.

## Critical cross-cutting finding: a half-applied `security.auth` refactor has the suite RED

`git diff --stat` shows `src/general_ludd/security/auth.py | 80 ---` (80 lines deleted,
zero added) alongside `security/sanitize.py | 149 +++`. An in-progress refactor moved /
renamed the shared auth primitives but left the call sites and tests pointing at symbols
that **no longer exist**:

- `src/general_ludd/worker/app.py:131` imports `check_bearer_token, load_auth_posture`
  from `general_ludd.security.auth` — **neither symbol is defined anywhere** (verified by
  repo-wide grep: the only hits are the import line + its use at app.py:133). `auth.py`
  now exports only `verify_psk`, `require_auth_env`, `is_path_within`, `is_safe_fetch_url`.
- `tests/security/test_router_auth_redteam.py:28` imports `is_path_within`,
  `is_safe_fetch_url`, `sanitize_skill_name` from `general_ludd.security.sanitize` — but
  `is_path_within`/`is_safe_fetch_url` live in `security.auth`, **not** `sanitize`
  (`sanitize.py` has `confine_path`/`validate_fetch_url`/`sanitize_skill_name`).
- `tests/security/test_daemon_auth_redteam.py:66` was updated to assert
  `"check_bearer_token" in _DAEMON_SRC` — the daemon does **not** use that helper (it
  inlines `hmac.compare_digest`), so the test fails.

This single inconsistency is the proximate cause of every #51 test failure below.

---

## Verdict table

| Issue | Verdict | Grounding (file:line + passing/failing test) |
|---|---|---|
| **#51** HTTP/worker/skills auth+scoping | **PARTIAL (working tree RED)** | Production logic present & correct; the centralizing refactor is half-applied so **worker + router auth tests do not pass**. See detail. |
| **#55** event-loop reconcile | **DONE** | `event_loop/loop.py:1042-1142` (F1/F2/F3/F6). `tests/unit/test_event_loop_reconcile_fixes.py` — **4/4 PASS** (run verified). |
| **#57** subagent orchestration defects | **OPEN** | No code/test for forbid-nesting/escalation, restore-control-loop, or stop-find>fix-spiral. The named defects are unimplemented. |
| **#58** self-modification guards | **DONE** | `security/capability_lattice.py` + `reload/hot_reloader.py:167` enforcement. `tests/security/test_self_modify_guards.py` — **12/12 PASS** (run verified). |
| **#70** wire safe_merge into HotReloader | **PARTIAL / MISDIRECTED** | `safe_merge` primitive DONE + 22 tests pass, but it is **NOT** wired into `reload/hot_reloader.py` (uses `os.replace`). It is wired into `pipeline/daemon_adapters.py` instead — a different call site than the issue names. |
| **#63** per-repo git serialization | **PARTIAL** | `git_automation/locking.py` complete (5/8 tests pass), but **NOT wired into `repo.py::_run_git`** as the issue requires. `tests/unit/test_git_locking.py` — **3/8 FAIL** (real `index.lock` races). |
| **#64** worktree-creation + branch-name race | **PARTIAL** | Input *hardening* DONE (`worktree/core.py`, 49 tests pass). **No race-coordination** for concurrent creation / branch-name collisions exists; `generate_branch_name` uses 1-s-resolution timestamps. |

---

## #51 — HTTP/worker/skills auth + scoping  → PARTIAL (working tree RED)

Production code for each sub-fix IS present and looks correct, but the suite is broken by
the auth refactor above.

| Sub-fix | Production code | Test status |
|---|---|---|
| (1) Method-aware public allowlist | `daemon.py:955-1006` — `_is_public(method, path)` gates `_PUBLIC_PATHS` on `_SAFE_METHODS` only; `POST /api/todos` falls through to the auth gate. Inline `hmac.compare_digest` (daemon.py:1002). **Correct.** | `test_router_auth_redteam.py::TestMethodAwarePublicAllowlist` — **ERROR (cannot collect)**: ImportError on `is_path_within` from `sanitize`. |
| (2) Public-todo cross-project | `routers/todos.py:67-77` — unknown `project_id` rejected 422 when `app.state._project_manager` has active projects; back-compat open when none. **Correct.** | `test_router_auth_redteam.py::TestCrossProjectTodoCreate` — **ERROR (cannot collect)**. |
| (3) Worker PSK parity | `worker/app.py:113-159` — fail-closed 503 branch, `check_bearer_token`/`load_auth_posture` usage. **BROKEN**: imports symbols that do not exist. | `tests/unit/test_worker_redteam.py` — **7/7 FAIL** (`ImportError: cannot import name 'check_bearer_token'`). |
| (4) Skill SSRF / path confinement | `security/auth.py:118` `is_safe_fetch_url`, `security/sanitize.py:52` `sanitize_skill_name`, `skills/fetcher.py:157`, `routers/skills.py:113`. **Correct.** | Guard unit-tests live inside `test_router_auth_redteam.py` — **ERROR (cannot collect)**. (Production confined; `test_skills_ssti_injection_redteam.py` exists and is not implicated.) |
| (5) Path confinement on complexity/suggest-model/integrity | `routers/models.py:57`, `routers/integrity.py:41` use `is_path_within`. **Correct.** | Same blocked test module — **ERROR (cannot collect)**. |

Adjacent evidence: `tests/security/test_daemon_auth_redteam.py` — **13/14 PASS, 1 FAIL**
(`test_source_uses_hmac_compare_digest` wants `check_bearer_token` in the daemon source).

**Remaining work for #51:** finish the refactor — add `check_bearer_token` and
`load_auth_posture` to `security/auth.py` (and a `load_auth_posture` returning the
`.psk/.require_auth/.no_auth` posture the worker reads); fix the
`test_router_auth_redteam.py` import to pull `is_path_within`/`is_safe_fetch_url` from
`security.auth`; route the daemon's PSK check through `check_bearer_token`. Then re-run
`tests/unit/test_worker_redteam.py`, `tests/security/test_router_auth_redteam.py`,
`tests/security/test_daemon_auth_redteam.py` green. No DONE until those pass.

---

## #55 — event-loop reconcile  → DONE

`event_loop/loop.py`:
- **F1** idempotent re-apply — `_applied_decisions` ledger keyed by `_decision_id`
  (loop.py:194, 1029-1040, 1057-1094).
- **F2** completed-work exactly-once push — `_pushed_work` ledger
  (loop.py:195, 1117-1142, `_attempt_completed_push`).
- **F3** commit-swallow split-brain — `_try_commit_completed_work` re-raises (loop.py:1153-1178),
  `_attempt_completed_push` returns failure + leaves work id unmarked for retry; surfaced
  via `push_failures` metric.
- **F6** version race — guarded `transition()` CAS raises `ConcurrencyError`, caught and
  skipped without marking applied (loop.py:1084-1092).

Evidence (run verified): `tests/unit/test_event_loop_reconcile_fixes.py`
`test_f1_decision_reapply_is_idempotent`, `test_f2_completed_work_pushed_exactly_once`,
`test_f3_push_failure_is_surfaced_not_swallowed`,
`test_f6_stale_reconcile_loses_version_race` — **4/4 PASS**.

---

## #57 — subagent orchestration defects  → OPEN

The issue names four concrete defects: *forbid nesting*, *forbid escalation*, *restore the
control loop*, *stop the find→fix spiral*. None of these are implemented:

- `agents/dispatcher.py` (`AgentDispatcher`) is a concurrency-limited fan-out executor:
  per-agent semaphore + active-count. `AgentTask` carries `parent_task_id` but there is
  **no depth/nesting guard, no escalation gate, no control loop, no spiral break**.
- Repo-wide grep for `nesting`, `escalat`, `spiral`, `find_then_fix`, `control_loop`,
  `max_depth` → **no matches** in `src/` or `tests/`.
- `dispatch/dynamic_dispatcher.py` + `security/capability_lattice.py` provide a fail-closed
  capability gate (this is the #58 deliverable, and it does forbid *collection* escalation
  for under-privileged roles), and `execution/tool_loop.py` has a bounded
  `MAX_TOOL_ITERATIONS=10`. These are adjacent but are **not** the #57 defects.

No passing test cites #57 behavior. **Remaining work:** implement + test the four named
guards (e.g. dispatch-depth ceiling using `parent_task_id`, an escalation/privilege check
on subagent spawn, a control-loop owner that drives find→fix to a bounded conclusion, and a
spiral detector). Currently nothing to verify.

---

## #58 — self-modification guards  → DONE

- `security/capability_lattice.py` — default-DENY per-role lattice (`capabilities_for`,
  `role_may_dispatch`, `check_dispatch`), protected-path deny-list
  (`PROTECTED_FILE_STEMS`/`PROTECTED_PATH_SUBSTRINGS`, `is_protected_path`), and
  `check_self_modification(path, role)` gating collections writes.
- Wired at the live site: `reload/hot_reloader.py:26-30` imports the guards and
  `hot_reloader.py:167-176` calls `check_self_modification(str(live_path), role)` **before
  any byte is written** (protected → `ProtectedPathError`; collections without capability →
  `CapabilityError`).
- Dispatch site: `dispatch/dynamic_dispatcher.py:183` consults `role_may_dispatch` and
  fail-closes before invoking a handler.

Evidence (run verified): `tests/security/test_self_modify_guards.py` — **12/12 PASS**,
covering collections allow+deny at the reload write-site, the protected-path deny-list
(5 parametrized guard files + ordinary-file allow), and dispatch allow/deny/unrestricted.

---

## #70 — wire safe_merge into gludd HotReloader  → PARTIAL / MISDIRECTED

- The primitive `integration/safe_merge.py` is complete (`safe_merge`, `safe_merge_file`,
  `detect_overlap`; refuses to write on conflict). `tests/unit/test_safe_merge.py` —
  **22/22 PASS** (run verified).
- **The issue's literal ask is not met:** `reload/hot_reloader.py` does **not** import or
  call `safe_merge`/`safe_merge_file`. It rotates a module with a whole-file
  `os.replace(candidate over live_path)` (`hot_reloader.py:191-197`) — exactly the
  blind-copy clobber pattern `safe_merge` exists to prevent.
- Where it *is* wired: `pipeline/daemon_adapters.py:31,161` (`make_merge_fn` →
  `safe_merge`) feeding `pipeline/lanes.py`. This is an untracked parallel "pipeline"
  integration path, not the HotReloader.

**Remaining work:** either wire `safe_merge_file` into `HotReloader.reload_code_module`'s
swap (3-way against the original bytes so a concurrent base edit is not clobbered, refuse
on conflict) **or** re-scope #70 to acknowledge the integration moved to
`pipeline/daemon_adapters.py` and prove the HotReloader call-site is intentionally out of
scope. No HotReloader-wiring test exists.

---

## #63 — per-repo git serialization  → PARTIAL

- `git_automation/locking.py` is complete and correct: in-process re-entrant `RLock`
  registry keyed by `realpath` + cross-process advisory `flock` on
  `<repo>/.git/gludd-git.lock` with timeout + stale-break + re-entrancy depth counter.
  The lock primitive's own tests pass.
- **NOT wired into `repo.py::_run_git`.** `git_automation/repo.py:172-199` runs
  `subprocess.run(["git", ...])` with **no `git_repo_lock`** around it. Repo-wide grep
  confirms `git_repo_lock` is used **only** in `pipeline/daemon_adapters.py:147`, never in
  `repo.py`. The #63 test docstring explicitly requires "its wiring into
  `GitAutomation._run_git`."

Evidence (run verified): `tests/unit/test_git_locking.py` — **5/8 PASS, 3/8 FAIL**:
- PASS: `test_different_repos_do_not_block_each_other`,
  `test_file_lock_breaks_stale_lock_after_timeout`,
  `test_acquire_times_out_when_held_by_another_process`,
  `test_reentrant_same_repo_same_thread_no_self_deadlock`,
  `test_nested_run_git_same_repo_does_not_deadlock`.
- FAIL (the actual product fix): `test_two_threads_mutating_same_repo_no_lock_error_no_lost_commit`
  (`CalledProcessError 128`), `test_index_lock_never_observed_under_contention`
  (real `fatal: Unable to create '.../index.lock'` + `cannot lock ref 'HEAD'`),
  `test_run_git_holds_lock_for_duration` (`_run_git did not hold the per-repo lock`).

**Remaining work:** wrap the `subprocess.run` in `GitAutomation._run_git` with
`git_repo_lock(self.repo_path)` (re-entrant, so `commit()`'s three nested `_run_git` calls
are safe). Re-run `test_git_locking.py` to green. No DONE until the 3 wiring tests pass.

---

## #64 — worktree-creation + branch-name race coordination  → PARTIAL

- Input **hardening** is DONE: `worktree/core.py` `validate_branch_name`,
  `confine_worktree_path`, and `build_worktree_*_argv` reject leading-dash / ref
  metacharacters / traversal and emit `--`-separated list-form argv.
  `tests/unit/test_worktree_core_hardening.py` + `tests/security/test_git_worktree_redteam.py`
  — **49/49 PASS** (run verified). `repo.py::create_worktree` also rejects dash/escape.
- **Race coordination is ABSENT.** There is no locking or uniqueness arbitration around
  concurrent `git worktree add -b <branch>` calls. `GitAutomation.generate_branch_name`
  (repo.py:522-525) derives the name from a `%Y%m%d%H%M%S` timestamp — **1-second
  resolution**, so two todos dispatched in the same second collide on the branch name. No
  test exercises concurrent worktree creation or a branch-name collision.

**Remaining work:** add coordination (e.g. take `git_repo_lock` around worktree creation
once #63 lands, and make branch names collision-proof — monotonic counter / uuid suffix /
retry-on-exists) and a concurrency test proving N parallel creations yield N distinct
branches/worktrees with no `index.lock`/ref races. No DONE until such a test passes.

---

## Method note

All pass/fail claims above were produced by running the cited tests via
`make test-unit TESTFILE=...` (Python 3.11.14, pytest 9.0.3) in this session. Source line
references were read directly. `make grep` was used for call-site enumeration. No files
were modified; this document is the only artifact and is uncommitted.
