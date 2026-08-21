# Concurrency Model Reference

gludd is a long-running daemon that dispatches many roles **in parallel**. Two
shared resources need explicit serialization / atomicity to stay correct under
that parallelism:

1. **A git working tree** shared by parallel roles → `git_repo_lock`.
2. **The rolling spend budget** charged by concurrent dispatches → `SpendLimiter`.

This document describes both, verified against the source.

---

## 1. Per-repo git serialization — `git_repo_lock`

**File:** `src/general_ludd/git_automation/locking.py`
**Wired into:** `src/general_ludd/git_automation/repo.py` `_run_git` (the single
choke point every `GitAutomation` git call flows through).

### Why it exists

Parallel roles call git against the **same** working tree concurrently. Plain
`subprocess.run(["git", ...])` has no serialization, so concurrent mutating git
invocations race on:

- `.git/index.lock` — git aborts with *"Another git process is running for this
  repository"*,
- `HEAD`,
- the commit graph — lost / interleaved commits.

`git_repo_lock(repo_path)` is the serialization choke point. It holds the
guarantee **both inside one daemon process and across several processes** that
share a repo on disk, using two layers.

### Layer (a): in-process re-entrant lock registry

- A module-level registry `dict[str, threading.RLock]` keyed by
  **`os.path.realpath(repo_path)`** (`_normalize`), so every thread / coroutine
  in one daemon that touches a given repo serializes on the **same** lock object
  — even when the repo is reached via different spellings (`./repo`,
  `/abs/repo`, a symlink).
- The lock is a `threading.RLock` (**re-entrant**), so a nested git call on the
  same repo from the same thread (e.g. `commit()` calling `_run_git` three
  times, or a helper that runs git while already holding the lock) does **not**
  self-deadlock.
- The registry dict itself is guarded by a separate short-lived
  `_registry_guard` lock, held only for the dict lookup — never while a per-repo
  lock is held — so it can never become a contention bottleneck.

### Layer (b): cross-process file lock

- An advisory `flock` (`fcntl.LOCK_EX | LOCK_NB`, polled) on
  **`<repo>/.git/gludd-git.lock`**. A second daemon / a stray external git
  wrapper going through this lock blocks until the holder releases.
- **Bounded acquire:** `_DEFAULT_ACQUIRE_TIMEOUT = 60.0s`. On timeout it raises
  `TimeoutError` so a stuck repo surfaces as a clean failure, never an unbounded
  hang.
- **Stable-inode crash recovery:** `_DEFAULT_STALE_AFTER = 300.0s` is a
  diagnostic threshold only. A stale mtime is reported once, but the lock file
  is never unlinked because existing descriptors would continue to lock the old
  inode while a new caller could lock its replacement. The kernel releases
  `flock` when the owning descriptors close, including abnormal process exit.
- **Re-entrancy across the fd boundary:** advisory `flock` is per
  open-file-description and is *not* re-entrant across separate `os.open` fds in
  one process. A per-repo depth counter (`_file_lock_depth`) — only ever touched
  while the per-repo RLock is held, so single-threaded — makes a nested entry
  (depth > 0) skip re-flocking instead of opening a second fd and deadlocking
  against the process's own first fd.
- **Process-scoped ownership:** the depth counter is accepted only for the PID
  and thread that acquired it. An `os.register_at_fork` child hook closes copied
  descriptors, clears inherited ownership, and rebuilds the in-process registry
  before the child can contend.
- **Scheduling:** the mutex guarantees exclusion, not FIFO acquisition order.
  Contending processes may enter in any scheduler-selected order.
- **POSIX-only:** on platforms without `fcntl` (Windows) it degrades gracefully
  to the in-process lock alone (the common in-daemon race is still serialized)
  rather than failing to import.

### Linked-worktree metadata and deployment boundary

In a linked worktree, `.git` is a text `gitdir:` pointer rather than a
directory. The pointed-to private Git directory can in turn contain a
`commondir` path relative to that directory. Gludd reads those two bounded
metadata files directly and places `gludd-git.lock` in the resolved common Git
directory. It does not launch `git rev-parse` while entering the lock: doing so
would mix lock discovery with the caller's actual Git subprocess seam and made
mocked or failed commands observable as extra application operations.

Evidence reviewed on 2026-08-21:

- Git's [repository-layout documentation](https://git-scm.com/docs/gitrepository-layout)
  defines both the `.git` gitfile and `commondir`; relative `commondir` values
  are resolved from `$GIT_DIR`.
- Git's [worktree documentation](https://git-scm.com/docs/git-worktree)
  confirms that linked worktrees use a private `$GIT_DIR` while
  `$GIT_COMMON_DIR` points to the main repository metadata shared by every
  worktree.
- A long-running practitioner thread on
  [multiple Git working directories](https://stackoverflow.com/questions/6270193/how-can-i-have-multiple-working-directories-with-git)
  dates to 2011 and documents the shared common-directory model as native
  worktree support evolved. A 2025 practitioner question on
  [locating the common `.git` directory](https://stackoverflow.com/questions/79872739/get-path-to-git-directory-including-from-a-worktree)
  independently describes reading `commondir` and resolving it relative to the
  private Git directory.

Zero-downtime and resource contract:

- Old and new Gludd processes converge on the same common-directory lock inode,
  so a rolling deployment needs no repository migration or daemon outage.
- Each metadata file is capped at 4 KiB and read once; discovery launches no
  child process, opens no persistent descriptor, and writes no worktree-root
  artifact.
- Missing, malformed, oversized, or non-UTF-8 metadata fails back to the
  existing in-process lock without inventing a cross-process lock path.
- Rollback is code-only: restore the previous resolver. No schema, lock file, or
  repository metadata must be rewritten, and the kernel continues to own
  release of any already-open `flock` descriptor.

### Acquisition order & API

`git_repo_lock` acquires the **in-process RLock first**, then the file lock, so
within one process only one thread at a time ever contends for the (more
expensive, timeout-bearing) file lock. A re-entrant acquisition by an
already-holding thread passes straight through both layers cheaply.

```python
with git_repo_lock(repo_path):
    subprocess.run(["git", "commit", ...], cwd=repo_path)
```

- Synchronous context manager (`git_repo_lock`) is the primary API, because
  `_run_git` is synchronous `subprocess.run`.
- `async_git_repo_lock` acquires the same (potentially blocking) lock **off the
  event loop** via `run_in_executor`, then returns an already-entered context
  manager, so an async caller never blocks the loop while waiting on a contended
  repo.

If there is no `.git` directory yet (`_git_dir` returns `None`), the
cross-process lock is skipped and the in-process lock alone serializes the
common in-daemon race.

### Call sites that still need to adopt it

`_run_git` in `repo.py` is wired. The module is deliberately import-light so
other modules that shell out to git **directly** against a shared repo can adopt
`git_repo_lock` around their mutating invocations too. Per the module docstring,
those outstanding call sites are:

- `src/general_ludd/worktree/core.py`
- `src/general_ludd/execution/engine.py`
- `src/general_ludd/git_automation/pr_delivery.py`
- `src/general_ludd/code_intelligence/git_intel.py` (its git calls are
  read-only history queries, lowest-risk, but listed for completeness)

Until those adopt the lock, a mutating git call made directly from one of them
(bypassing `_run_git`) is not serialized against the others.

---

## 2. Atomic spend charging — `SpendLimiter`

**File:** `src/general_ludd/controllers/spend_limiter.py`

A **soft, rolling-window** spend cap checked *before* a model/infra call. It is
never used to hard-abort an in-flight call: a dispatch whose projected cost fits
the remaining budget proceeds; once remaining `<= 0`, any positive projected
cost defers the dispatch.

State: in-memory list of `(timestamp, cost_usd)` records; entries older than
`window_seconds` are pruned lazily on every `window_spend()`. The `clock` is an
injectable zero-arg callable (`time.monotonic` by default; a fake clock in
tests).

### Atomic `try_charge`

`try_charge(cost_usd, *, kind, ...)` runs the **check-and-record in a single
critical section** under a re-entrant `threading.RLock`, returning `True` iff
the charge was accepted and recorded. This closes three bypasses:

- **Inert limiter:** every accepted charge is `record()`ed against the window in
  the same critical section as the check, so rolling spend actually grows and
  the cap can trip.
- **Concurrent overshoot:** because check-and-record is atomic under the lock,
  two concurrent charges can never both observe the same headroom and both
  commit — their combined spend can never exceed the cap. (The lock is
  *re-entrant* because `try_charge` calls `would_exceed`/`window_spend`/`record`,
  which each also take the lock.)
- **Silent fail-open on unknown cost:** when `cost_usd is None` and a cap is
  configured (`cap_configured` — i.e. `limit_usd > 0`), the charge is
  **refused** (no record made; caller must defer). Only when no cap is
  configured is an unknown cost allowed through.

Supporting predicates: `would_exceed(projected)` →
`window_spend + projected > limit`; `remaining()` → `max(0, limit - spent)`
(never negative). A projected cost of `0.0` never triggers deferral even at the
cap.

### Restart-rehydrate

In-memory window state would reset to zero on a daemon restart, letting the cap
be evaded by restarting. To prevent that:

- `snapshot()` returns a serializable copy of the in-window
  `(timestamp, cost_usd)` records.
- Persist it across a restart and pass it back to `restore(records)`, which
  re-extends the limiter's records so accumulated spend **survives** the
  restart.
- Restored records outside the current window are pruned lazily on the next
  `window_spend()`, so rehydrating stale records after a long downtime is safe.

### Integration status

The enforcement primitive (`try_charge`, `snapshot`/`restore`) is complete and
unit-tested. The module carries a `TODO(integration)` to wire
`would_exceed()`/`record()` (or `try_charge`) into the dispatch path before
every model/infra call so each dispatch checks the rolling budget prior to
executing.

---

## Summary

| Concern | Primitive | Mechanism | Bound / fail-mode |
| --- | --- | --- | --- |
| Parallel roles racing on one git tree | `git_repo_lock` (`git_automation/locking.py`) | PID/thread-scoped RLock re-entry + cross-process `flock` on one stable `.git/gludd-git.lock` inode | 60s acquire timeout → `TimeoutError`; crash release by kernel close; no FIFO guarantee; POSIX-only with in-process fallback |
| Concurrent dispatches racing past the budget | `SpendLimiter.try_charge` (`controllers/spend_limiter.py`) | atomic check-and-record under a re-entrant lock | unknown cost under a cap → refused (fail closed) |
| Budget surviving a restart | `SpendLimiter.snapshot` / `restore` | persist + rehydrate `(ts, cost)` records | stale records pruned lazily on next window read |
