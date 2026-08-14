# Git Repository Mutex Process Contract

Status: beta4 release contract

## Contract

`git_repo_lock(repo_path)` provides mutual exclusion for repository mutations.
All worktrees belonging to one repository resolve to the same Git common
directory and therefore the same `.git/gludd-git.lock` inode. Different
repositories remain independently namespaced and can make progress in parallel.

The implementation composes two mature standard-library/platform primitives:

- a per-common-directory `threading.RLock` serializes threads and supports
  same-thread re-entry;
- POSIX `fcntl.flock` serializes independent processes on one stable inode.

The contract is mutual exclusion, not FIFO scheduling. Operating-system process
scheduling may allow later contenders to acquire before earlier contenders.
Tests prove that enter/exit pairs never overlap; they do not infer fairness from
process start order.

## Process ownership and crash behavior

Re-entrant file-lock state belongs to the `(PID, thread identifier)` pair that
acquired it. A child must not treat a copied parent depth counter as ownership.
On POSIX, an `os.register_at_fork` child hook closes inherited lock descriptors,
clears copied depth/owner state, and rebuilds the in-process lock registry. A
PID check at acquisition is a second fail-closed boundary for runtimes that do
not invoke Python's fork hook.

The lock file is never unlinked because `flock` protects an open file
description, not a pathname. A stale mtime emits one diagnostic while a waiter
continues to use the existing inode. The kernel releases ownership when all
descriptors close, including abnormal owner exit. This makes a crash recoverable
without creating a second inode that another process could lock concurrently.

Every contended acquisition uses a monotonic deadline. Exceeding it raises
`TimeoutError` with the bounded duration and lock path. `stale_after` remains an
API-compatible diagnostic threshold; it never authorizes lock deletion.

## Compatibility and limitations

The public synchronous and asynchronous context-manager APIs and their default
timeouts are unchanged. Existing POSIX processes contend on the same filename.
Platforms without `fcntl` retain the existing in-process-only fallback; callers
must not claim cross-process exclusion there. Spawned workers use importable
module-level targets and begin with clean ownership state. Forked workers also
begin clean because inherited descriptors and Python state are discarded.

No FIFO queue or third-party locking service is added. A queue would create a
new persistence, cleanup, and recovery protocol without improving the repository
mutation requirement.

## Security, resources, and observability

- Newly created lock files use mode `0600`; ownership metadata remains in
  process memory rather than exposing PIDs or commands on disk.
- Acquisition polling is bounded and sleeps between attempts. No helper daemon,
  background thread, socket, semaphore, or untracked script is created.
- Child-process regressions join and close every process and pipe. A crashed
  child deliberately bypasses Python cleanup, proving kernel-level release.
- A timeout is a fail-closed operational error. A stale timestamp is logged once
  per acquisition attempt but never weakens exclusion.
- Repository common-directory resolution keeps worktrees serialized while
  allowing unrelated projects to run concurrently.

## Zero-downtime delivery and rollback

The change has no schema, wire-format, command-line, or database migration. It
is compatible with a rolling deployment because both versions use the same
filename and `flock` operation. During the mixed-version window, drain old
workers before any Git critical section can exceed the historical 300-second
stale threshold; this prevents an old waiter from unlinking the shared inode.
Promote after the spawn, fork, timeout, crash, and multi-waiter regressions pass.

Rollback is a source revert with no filesystem cleanup: the stable lock file is
safe to retain permanently. Drain new workers before restoring the old code for
the same mixed-version reason. If promotion fails, stop dispatching new Git
mutations, allow the bounded holder to exit, roll back, and resume; no request or
repository state is transferred between lock implementations.

## Verification

- `tests/unit/test_git_automation_locking.py` proves PID/thread ownership, fork
  reset, spawn timeout, abnormal-exit recovery, stable-inode behavior, and
  worktree serialization.
- `tests/unit/test_git_automation_locking_deep.py`,
  `tests/unit/test_git_automation_deep.py`, and `tests/unit/test_mutex_deep.py`
  verify re-entry, bounded contention, multiple waiters, and no overlap.
- Focused production coverage must be at least 85% aggregate and at least 75%
  line and branch coverage for `locking.py`; warnings are errors. The beta4
  focused result is 169 passed with 89.0% line, 85.3% branch, and 86.36%
  combined coverage for the production module.

## Practitioner evidence

- A long-lived [Stack Overflow report about deleting a locked file](https://stackoverflow.com/questions/17708885/flock-removing-locked-file-without-race-condition)
  demonstrates the old-inode/new-inode split-brain race. This contract retains
  one stable directory entry.
- A long-lived [Stack Overflow report about `flock`, `fork`, and a dead
  parent](https://stackoverflow.com/questions/9106997/deadlock-with-flock-fork-and-terminating-parent-process)
  documents inherited open-file descriptions keeping a lock alive. The child
  hook closes inherited descriptors before any new acquisition.
- A practitioner question about [FIFO ordering with `flock`](https://stackoverflow.com/questions/55716702/bash-fifo-queue-with-flock)
  records that POSIX supplies no fairness guarantee. The executable contract
  checks mutual exclusion rather than scheduler order.
- The [Linux `flock(2)` manual](https://man7.org/linux/man-pages/man2/flock.2.html)
  specifies open-file-description ownership, fork inheritance, independent
  opens, and last-close release.
- The [Python multiprocessing documentation](https://docs.python.org/3/library/multiprocessing.html)
  requires importable, picklable targets under spawn and recommends PID-scoped
  invalidation for fork-sensitive cached state.
- The [Python `os.register_at_fork` documentation](https://docs.python.org/3/library/os.html#os.register_at_fork)
  defines the supported child hook used to discard inherited ownership.
