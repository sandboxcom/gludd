# Git Execution Architecture — Audit & Consolidation Plan

Status: design / implementation-ready
Date: 2026-06-16
Scope: where git runs in gludd (Python vs Ansible vs Makefile), which path
each operation *should* use, and how to close the cross-process lock gap so
role-git and daemon-git can never collide.

Every claim below is grounded in a file that was read; paths + line numbers
are cited inline.

---

## 0. TL;DR

1. **Two live git execution paths exist** and they do **not** share a lock:
   - **Python** — `GitAutomation._run_git` (`src/general_ludd/git_automation/repo.py:173`)
     takes the `git_repo_lock` (in-process RLock + cross-process flock on
     `<repo>/.git/gludd-git.lock`). This is the *only* path that locks.
   - **Ansible** — `playbooks/git_automate_change.yml`, `git_repo_init.yml`,
     `git_manage_worktree.yml` run git via `ansible.builtin.command: git ...`.
     They take **no lock at all**.
2. **The lock gap is real.** #63's flock serializes only the Python path. The
   Ansible playbooks, plus several *other* direct-subprocess Python callsites
   (`execution/engine.py`, `worktree/core.py`, `code_intelligence/git_intel.py`,
   `git_automation/pr_delivery.py`, `GitAutomation.clone/create_worktree/...`),
   all bypass the flock. Concurrent role-git + daemon-git on the same working
   tree can collide on `.git/index.lock` — exactly the race #63 was created to
   kill.
3. **Recommendation:** make the Ansible playbooks the *native, user-facing*
   path for project git (init, stage, commit, push, worktree) — they already
   run through the `git` queue — and keep a *narrow* Python path only for
   daemon-internal, tick-bounded operations (clone-on-materialize, commit/push
   of agent output inside a tick, worktree reclaim). Both paths MUST serialize
   through the **same** `<repo>/.git/gludd-git.lock` flock. The fix is a
   `gludd_git` role/wrapper that acquires that flock before any `git` command.

---

## 1. Current Map — where git runs today

### 1a. Python path (subprocess git)

| Module / callsite | git ops | Takes `git_repo_lock`? | Evidence |
|---|---|---|---|
| `git_automation/repo.py` — `GitAutomation._run_git` | init/branch/commit/tag/push/rev-parse/is_repo | **YES** (in-proc RLock + flock) | `repo.py:173-205`, `with git_repo_lock(self.repo_path)` at `:179` |
| `git_automation/repo.py` — `init_repo` | `git init`, `git config` | **NO** — raw `subprocess.run`, does not go through `_run_git` | `repo.py:211-222` |
| `git_automation/repo.py` — `clone` | `git clone` | **NO** — raw `subprocess.run` | `repo.py:293-299` |
| `git_automation/repo.py` — `create_worktree` / `remove_worktree` / `list_worktrees` | `git worktree add/remove/list` | **NO** — raw `subprocess.run` | `repo.py:324`, `:376`, `:388` |
| `git_automation/repo.py` — `merge_branch` / `create_release_tag` / `create_checkpoint_tag` / `push_to_remote` / `create_local_bare_mirror` | checkout/merge/tag/push/clone --bare | **NO** — raw `subprocess.run` | `repo.py:431`, `:448`, `:460`, `:472`, `:485`, `:502`, `:516` |
| `execution/engine.py` — `_is_git_repo` / `_git_create_branch` / `_git_commit` / `_git_current_branch` | rev-parse / checkout -b / add -A + commit + rev-parse | **NO** — its own helpers, raw `subprocess.run`; **duplicates** GitAutomation | `engine.py:109-157` |
| `worktree/core.py` — `_get_last_activity` | `git log -1` | **NO** (read-only) | `core.py:362` |
| `worktree/core.py` — `_reclaim_worktree_dir` | `git worktree remove --force` / `worktree prune` | **NO** (mutating) | `core.py:460-471` |
| `code_intelligence/git_intel.py` — `_run_git` | `git log` / blame / diff (read-only intel) | **NO** (read-only) | `git_intel.py:80-114` |
| `git_automation/pr_delivery.py` | `git push`, `gh pr create` | **NO** (mutating push) | `pr_delivery.py:81`, `:106`, `:123` |
| `projects/manager.py` — `_materialize_workspace` | `GitAutomation().clone(...)` | inherits clone's NO-lock | `manager.py:219-220` |
| `routers/integrity.py` — `admin_selftest` | runs `molecule` (NOT git) — out of scope | n/a | `integrity.py:138` |

**Only two modules import `git_repo_lock`:** `git_automation/repo.py:14` and
`pipeline/daemon_adapters.py:30` (which wraps a merge in
`with git_repo_lock(repo_path)` at `daemon_adapters.py:147`). Confirmed by
`grep git_repo_lock src/`. Every other git callsite above is **outside** the
lock.

> Note: the `locking.py` module docstring itself already documents this debt —
> "Other modules that shell out to git directly … notably `worktree/core.py`,
> `execution/engine.py`, `pr_delivery.py`, and `git_intel.py` — own their own
> call sites and SHOULD adopt `git_repo_lock`" (`locking.py:30-34`). This audit
> confirms that adoption has **not** happened.

### 1b. Ansible path (the product's native execution model)

Three git playbooks, all run via `ansible.builtin.command: git ...` (the git
*module* can't init/stage/commit/worktree — see the `noqa` comments), dispatched
through the **`git` queue** (`schemas/queue.py:108-116`,
`allowed_playbooks=[git_repo_init.yml, git_manage_worktree.yml, git_automate_change.yml]`):

| Playbook | git ops | Takes the flock? | Evidence |
|---|---|---|---|
| `playbooks/git_repo_init.yml` | `git init`, `git config user.email/name` | **NO** | `git_repo_init.yml:23,32,40` |
| `playbooks/git_automate_change.yml` | `git add -A`, `git commit -m` | **NO** | `git_automate_change.yml:26,34` |
| `playbooks/git_manage_worktree.yml` | `git worktree list --porcelain` (read-only) | **NO** | `git_manage_worktree.yml:24` |
| `playbooks/gitsign_configure.yml` | `git config --local ...` (signing config, not mutating tree) | **NO** | `gitsign_configure.yml:47-50` |

None of these acquire `<repo>/.git/gludd-git.lock`. They are pure
`ansible.builtin.command` invocations of the `git` binary against `chdir: repo_path`.

### 1c. Orchestrator Makefile git targets (out of scope — analogy only)

The repo's own `make git-*` targets (`AGENTS.md:376-392`: `git-add`,
`git-commit`, `git-merge`, `feature-start/done`) are the *harness's* git, not the
*product's*. They are the human/agent dev workflow for this repo and never touch
a managed project's working tree, so they are out of scope for the product's git
serialization. They are listed only because they are the analogue of what the
Ansible role-git path should become for managed projects: a single,
policy-checked front door.

---

## 2. Who calls the Python path — "specific needs" or could-be-Ansible?

Three daemon callers invoke the Python `GitAutomation`/subprocess path:

1. **`event_loop/loop.py:1153 _try_commit_completed_work`** — commits + pushes a
   completed todo's worktree (`GitAutomation(worktree).commit(...)` then
   `.push(...)`, `loop.py:1159-1169`). It is **tick-bounded and in-process**: it
   runs *inside* the async event loop and is explicitly offloaded to
   `asyncio.to_thread` precisely because git is blocking and would stall the
   loop (`loop.py:1161-1169`). This is a **genuine Python-path need**: the daemon
   must commit agent output synchronously within a tick, observe the result, and
   gate the next state transition on it. Going out to a playbook here adds an
   Ansible-runner round-trip inside the hot loop.
   - **BUT** the lock matters most here: this is the daemon writing to the *same*
     working tree a user-triggered `git_automate_change.yml` could be committing.

2. **`event_loop/loop.py:1180 _maybe_open_pr` → `pr_delivery.PRDelivery`** —
   pushes the branch and runs `gh pr create` (`loop.py:1190-1202`). Daemon-internal
   delivery; reasonable to stay in Python (it shells `gh`, not just git), but its
   `git push` must take the flock.

3. **`projects/manager.py:_materialize_workspace`** — clones `repo_url` into the
   project workspace (`manager.py:219-220`). This is **first-checkout
   materialization**, reachable unauthenticated (it does SSRF/RCE URL vetting at
   `manager.py:195-199`). It is a **genuine Python-path need**: it must run
   synchronously when a project is created/restored, with the security vetting in
   `reject_unsafe_repo_url` (`repo.py:122-166`) applied before any clone. A
   clone into an *empty* target dir does not contend for that repo's
   `index.lock`, so the lock gap is lower-risk here — but `init_repo` and any
   post-clone op are not.

4. **`execution/engine.py` git helpers** — these are invoked by the execution
   engine to branch/commit an agent's run. They **duplicate** GitAutomation
   (`engine.py:120-146`) and should not exist as a separate path at all (see §4).

**Verdict:** callers (1)–(3) are legitimate daemon-internal, tick-bounded /
synchronous-materialization needs that justify a Python path. Caller (4) is
accidental duplication. None of them is a *user-facing project git* operation —
that is exactly what the Ansible `git` queue is for.

---

## 3. The lock gap (#63) — what bypasses the flock

`locking.py` provides a **two-layer** lock (`locking.py:9-39`, `:233-280`):
- (a) in-process re-entrant `threading.RLock` keyed by `realpath(repo_path)`
  (`locking.py:99-105`), and
- (b) a cross-process advisory `flock` on **`<repo>/.git/gludd-git.lock`**
  (`_LOCK_FILENAME = "gludd-git.lock"`, `locking.py:56`; opened/flocked in
  `_file_lock`, `locking.py:195-227`), with a 60s acquire timeout and a 300s
  stale-break (`locking.py:59,64`).

Layer (b) — the flock — **is the only mechanism that can serialize across
processes**, i.e. between the daemon process and an Ansible-runner subprocess.
It is the designated bridge between the Python path and the Ansible path.

**What currently takes the flock:** only code that flows through
`GitAutomation._run_git` (`repo.py:179`) or `pipeline/daemon_adapters.py:147`.

**What currently BYPASSES the flock (and can therefore collide):**

- **All three Ansible git playbooks** (`git_automate_change.yml` `git add -A`
  + `git commit`, `git_repo_init.yml` `git init`, `git_manage_worktree.yml`) —
  they run `ansible.builtin.command: git ...` and never open
  `<repo>/.git/gludd-git.lock`. **A user-triggered `git_automate_change.yml`
  committing project repo R while the daemon's `_try_commit_completed_work`
  (`loop.py:1166`) commits a worktree of R races on `.git/index.lock`.** This is
  the precise failure mode #63 names ("Another git process is running for this
  repository", `locking.py:4-7`).
- `execution/engine.py` `_git_commit`/`_git_create_branch` (`engine.py:120-146`).
- `worktree/core.py` `_reclaim_worktree_dir` `git worktree remove/prune`
  (`core.py:460-471`).
- `git_automation/pr_delivery.py` `git push` (`pr_delivery.py:81`).
- `GitAutomation`'s own non-`_run_git` methods: `init_repo`, `clone`,
  `create_worktree`, `remove_worktree`, `merge_branch`, the tag helpers,
  `push_to_remote`, `create_local_bare_mirror` (all raw `subprocess.run`,
  §1a). The lock was wired into `_run_git` but **not** the worktree/merge/clone
  methods on the same class.

So today: **roles run git via Ansible and take neither the in-process RLock
(different process) nor the cross-process flock (they never open the lock file).
The bridge exists but the Ansible side is not plugged into it.**

---

## 4. Recommendation — which path each operation should use

Guiding principle (the user's ask): **git is the product's user-facing behavior
and should run through an Ansible git role/collection — the native execution
model — UNLESS there is a specific in-process, tick-bounded, or
security-gated need that justifies staying in Python.**

### 4a. Belongs in Ansible (role-driven, user-facing project git)

Run these through the `git` queue / a `gludd.git` role. They are user-visible
project-repo operations, already modeled as playbooks, and benefit from the
artifact/observability conventions the playbooks already write
(`git_automate_change.yml:39-49`):

- **Repo init + identity config** — `git_repo_init.yml` (already a playbook).
- **Stage + commit a user-requested change** — `git_automate_change.yml`
  (already a playbook).
- **Branch create / checkout / merge / tag / push** for user-facing project
  work — currently only in Python (`repo.py` `create_branch`, `merge_branch`,
  `push_to_remote`, tag helpers). **Promote these to Ansible tasks** in the git
  role; they are not tick-bound.
- **Worktree list / add / remove** as a user/operator action —
  `git_manage_worktree.yml` (list exists; add/remove should be added as actions).
- **gitsign/cosign signing config** — `gitsign_configure.yml` (already a
  playbook; signing is inherently a per-repo config concern, fits the role).

### 4b. Stays in Python (daemon-internal, specific needs)

Keep a *narrow* Python surface for operations that must run synchronously inside
the daemon and be observed before the next state transition:

- **Workspace materialization / clone** — `manager.py:_materialize_workspace`
  → `GitAutomation.clone`. Must run synchronously on project create/restore with
  the `reject_unsafe_repo_url` SSRF/RCE vetting (`repo.py:122-166`) applied
  *before* clone. Keep in Python; just make it take the flock.
- **Commit + push of completed agent output inside a tick** —
  `loop.py:_try_commit_completed_work` (`loop.py:1153-1178`). Tick-bounded,
  offloaded via `asyncio.to_thread`, result gates the todo state machine. Keep in
  Python; must take the flock.
- **Worktree reclaim** — `worktree/core.py:_reclaim_worktree_dir`. Runs inside
  the daemon's stale-scan loop, best-effort, must be fast and non-blocking. Keep
  in Python; must take the flock.
- **PR delivery** — `pr_delivery.py` (it shells `gh`, not just git). Keep in
  Python; its `git push` must take the flock.
- **Read-only intelligence** — `git_intel.py` (log/blame/diff) and
  `worktree/core.py:_get_last_activity`. Read-only; a flock is *defense-in-depth*
  but lower priority (no `index.lock` write). Acceptable to leave, or take a
  shared/read variant.

### 4c. Rationale

- The product's contract is "Ansible playbooks are the tool-call boundary"
  (`AGENTS.md:418`). User-facing project git is a tool-call; it belongs on the
  Ansible side where it is queued, artifact-logged, and Molecule-testable.
- Daemon-internal git is *not* a user tool-call — it is the event loop's own
  bookkeeping. It needs in-process, synchronous, timeout-bounded execution and
  result observation inside a single tick (`loop.py:1161-1169` documents exactly
  why blocking git is offloaded but kept in-process). Pushing it through an
  Ansible-runner round-trip per tick would add latency and an extra failure
  surface inside the hot loop.
- The dividing line is therefore **"is this a user/operator action on the project
  repo" (Ansible) vs "is this the daemon committing/observing its own output
  inside a tick" (Python)** — not an arbitrary split.

---

## 5. THE FIX — one flock for both paths

Both paths must serialize on the **same** lock file the Python path already uses:
`<repo>/.git/gludd-git.lock` (`locking.py:56`,
`_LOCK_FILENAME = "gludd-git.lock"`). The flock is advisory but works
cross-process as long as **every** writer opens that exact path and `flock`s it.

### 5a. Ansible side — a `gludd_git` wrapper that takes the flock

Two implementation options; **Option A is recommended.**

**Option A — a `gludd_git` action/role that wraps git under the flock.**
Add a small Ansible role `roles/gludd_git/` (or a `library/gludd_git.py` module)
whose single job is: given `repo_path` + an argv, **open
`<repo_path>/.git/gludd-git.lock`, `fcntl.flock(LOCK_EX)` with the same 60s
timeout + 300s stale-break semantics as `locking.py:181-227`, then run git, then
release.** Re-implement the exact stale/timeout policy from `locking.py` so the
two sides agree on staleness. The three git playbooks then call
`gludd_git: { repo_path: ..., argv: ["add","-A"] }` instead of
`ansible.builtin.command: git ...`.

This is the cleanest: the lock contract lives in one tested module, the playbooks
become declarative, and a role is the product's native unit.

**Option B — a pre/post flock task pair in each playbook.**
Add a "Acquire gludd git lock" task (a `gludd_flock` module that opens + flocks
and holds the fd via a fact) before the git tasks and a "Release" task after.
Brittle (fd lifetime across tasks, failure cleanup) — use only if a custom
module/role is not acceptable.

> Whichever option: the flock acquire/stale/timeout constants must be **shared
> with `locking.py`**, not re-hardcoded, so the Python and Ansible sides can
> never disagree on what "stale" means. Expose `_LOCK_FILENAME`,
> `_DEFAULT_ACQUIRE_TIMEOUT`, `_DEFAULT_STALE_AFTER` (`locking.py:56-64`) as
> public constants and import them in the `gludd_git` module.

### 5b. Python side — close the remaining bypasses

Wrap every mutating git callsite from §1a/§3 that is *not* already under the lock
in `git_repo_lock(repo_path)` (sync) or `async_git_repo_lock` (`locking.py:283`)
for async callers:

- `GitAutomation.init_repo`, `clone`, `create_worktree`, `remove_worktree`,
  `merge_branch`, tag helpers, `push_to_remote`, `create_local_bare_mirror`
  (`repo.py`) — wrap each `subprocess.run` in `git_repo_lock(target/repo_path)`.
  (Read-only `list_worktrees` is optional.)
- `worktree/core.py:_reclaim_worktree_dir` — wrap the remove/prune loop
  (`core.py:460-471`) in `git_repo_lock(safe_path)`.
- `pr_delivery.py` `git push` (`pr_delivery.py:81`) — wrap in
  `git_repo_lock(repo_path)`.
- `execution/engine.py` git helpers — **delete and route to GitAutomation**
  (see §6), inheriting its lock rather than adding a fourth lock-aware copy.

### 5c. Cross-process correctness note

The flock in `locking.py` is keyed per open-file-description and is re-entrant
*within one process* via a depth counter (`locking.py:83`, `:185-193`). Across
processes (daemon vs Ansible-runner) it is a normal advisory exclusive lock:
first to `flock(LOCK_EX)` wins, the other blocks up to the timeout, then the
stale-break protects against a crashed holder (`locking.py:134-157`). This is
exactly the cross-process guarantee #63 designed for — the only missing piece is
that the Ansible side never calls it. §5a plugs that in.

---

## 6. Consolidation plan — remove duplicated git logic

There are currently **four** independent git-shelling implementations. Collapse
to **one Python core + one Ansible role**, both behind the single flock.

**Step 1 — delete `execution/engine.py`'s git helpers (duplication).**
`_is_git_repo`, `_git_create_branch`, `_git_commit`, `_git_current_branch`
(`engine.py:109-157`) duplicate `GitAutomation.is_repo` / `create_branch` /
`commit` / a current-branch read. Replace call sites with `GitAutomation`
methods. This removes a whole un-locked path and centralizes the lock.
(TDD: write a failing test asserting the engine commits through GitAutomation —
e.g. that a monkeypatched `GitAutomation.commit` is invoked — before deleting.)

**Step 2 — make `GitAutomation` uniformly lock-aware.**
Today only `_run_git` locks; the worktree/clone/merge/tag/push methods bypass it
(§1a). Route them through a locked helper (or wrap each in `git_repo_lock`) so
*every* `GitAutomation` method is serialized. Then `GitAutomation` is the single
locked Python git core.

**Step 3 — build the `gludd_git` Ansible role/module (§5a)** and convert
`git_repo_init.yml`, `git_automate_change.yml`, `git_manage_worktree.yml`,
`gitsign_configure.yml` to use it. Share lock constants with `locking.py`.

**Step 4 — promote non-tick user-facing git from Python to the role.**
Branch/merge/tag/push of user-facing project work (currently `repo.py`
`create_branch`/`merge_branch`/`push_to_remote`/tag helpers) move to `gludd_git`
role tasks. Keep the Python methods only where a daemon-internal caller in §4b
needs them in-process; otherwise have the Python method dispatch the playbook.

**Step 5 — wrap remaining Python bypasses (§5b)** — `worktree/core.py`,
`pr_delivery.py`, `manager.py` clone.

**Step 6 — guardrail.** Add a test (mirroring `tests/unit/test_guardrails.py`
style) that fails if a *new* `subprocess.run(["git", ...])` (or
`ansible.builtin.command: git ...`) is introduced outside the two sanctioned
choke points (`GitAutomation` / the `gludd_git` role). This prevents the lock gap
from silently reopening — the same class of regression `locking.py:30-34` warned
about but had no enforcement for.

**Net result:** exactly two git front doors — `GitAutomation` (Python,
daemon-internal, locked) and the `gludd_git` role (Ansible, user-facing, locked)
— both serializing on `<repo>/.git/gludd-git.lock`, so a role-git commit and a
daemon-git commit on the same working tree can never collide on `.git/index.lock`.

---

## 7. Evidence index (files read for this audit)

- `src/general_ludd/git_automation/repo.py` (GitAutomation, `_run_git`, all
  subprocess git, `reject_unsafe_repo_url`)
- `src/general_ludd/git_automation/locking.py` (two-layer lock, `gludd-git.lock`,
  flock + stale/timeout, the "other modules SHOULD adopt" debt note)
- `src/general_ludd/event_loop/loop.py:1153-1206` (`_try_commit_completed_work`,
  `_maybe_open_pr`)
- `src/general_ludd/projects/manager.py:175-229` (`_materialize_workspace`)
- `src/general_ludd/execution/engine.py:109-157` (duplicate git helpers)
- `src/general_ludd/worktree/core.py:355-471` (`_get_last_activity`,
  `_reclaim_worktree_dir`)
- `src/general_ludd/code_intelligence/git_intel.py:70-129` (read-only intel)
- `src/general_ludd/git_automation/pr_delivery.py:81-123` (git push)
- `src/general_ludd/pipeline/daemon_adapters.py:30,147` (locked merge)
- `src/general_ludd/schemas/queue.py:107-116` (git queue allowed playbooks)
- `playbooks/git_automate_change.yml`, `git_repo_init.yml`,
  `git_manage_worktree.yml`, `gitsign_configure.yml`
- `AGENTS.md:376-392,418` (Makefile git targets; "playbooks are the tool-call
  boundary")
- grep `git_repo_lock src/` (only `repo.py` + `daemon_adapters.py` import it)
