# Git Repository Mutex Process Contract

Status: beta4 release contract

## Contract

`git_repo_lock(repo_path)` provides mutual exclusion for repository mutations.
All worktrees belonging to one repository resolve to the same Git common
directory and therefore the same `.git/gludd-git.lock` inode. Different
repositories remain independently namespaced and can make progress in parallel.

The implementation composes two mature standard-library/platform primitives:

- a per-common-directory `threading.RLock` serializes threads, supports
  same-thread re-entry, and provides a bounded acquire operation;
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

Every contended acquisition uses one monotonic deadline across both layers.
Time spent waiting for the in-process `RLock` is deducted from the time left for
`flock`, so the public timeout is a total acquisition budget rather than an
unbounded thread wait followed by a fresh process wait. Exceeding either layer
raises `TimeoutError` with the bounded duration and contested identity.
`stale_after` remains an API-compatible diagnostic threshold; it never
authorizes lock deletion.

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

### Linked-worktree path spelling

Git may persist a physical common-directory path even when the caller entered a
linked worktree through a lexical alias. On macOS, the common example is a
caller under `/tmp` whose Git metadata points through `/private/tmp`. Gludd now
finds the nearest caller ancestor whose physical path contains the metadata
target and rebuilds only that descendant suffix through the caller's spelling.
The returned path is therefore stable for logs, assertions, and downstream
joins, while `_normalize` still resolves aliases before selecting the mutex.

The transformation is read-only and bounded by the number of path ancestors.
It creates no directory, process, descriptor, cache, or cleanup obligation. If
no matching ancestor exists, Gludd returns the validated absolute metadata path
unchanged. Rollback is a source revert; no on-disk repair or lock-file deletion
is required, so rolling workers remain compatible throughout a ZDD deployment.

## Security, resources, and observability

- Newly created lock files use mode `0600`; ownership metadata remains in
  process memory rather than exposing PIDs or commands on disk.
- Acquisition polling is bounded and sleeps between attempts. Async callers own
  namespaced single-thread executors as described below; synchronous callers
  create no helper thread. No daemon, socket, semaphore or untracked script is added.
- Child-process regressions join and close every process and pipe. A crashed
  child deliberately bypasses Python cleanup, proving kernel-level release.
- Thread contention uses `RLock.acquire(timeout=...)`; it creates no polling
  thread and cannot wait indefinitely before reaching the process-lock layer.
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

## Async lifecycle staging (2026-10-22 UTC)

Root stages the existing feature source41fd1b1d854a7f7b678239429f8783c2a2942d57
on release-candidate/v012-git-mutex-20261022, based on development
82db11b57b254c1ca1e035975b5cc86253e2fed5, under S98.4. Its parent
a5b0d62273f2c7a89120f4f273cb3c1da86deffa is already an ancestor of that base.
This is bounded integration of implemented release work while final capacity
and publication proof remain blocked. It adds no provider or runtime feature.

The original twelve adversarial cases run first against the receiving base:
5FAIL/7PASS3.27s/native4.221s. They expose event-loop blocking during cancellation,
foreign-thread nested re-entry, abandoned ownership, reusable wrappers and the
absent async fork registry. Preserve
/tmp/gludd-v012-git-mutex-stage-red311.{log,xml} and native JSON under
.gate-logs/observed/git-mutex-stage-red311. These are actual receiving failures,
separate from historical source-branch reports.

An attempted git-merge-nc with the source-identical new test staged refuses that
file, emits index.lock/File exists and then no merge to abort, and leaves a clean
checkout. Six existing owned checkpoint entries remain unchanged. No competing
PID or autostash cause is established. Do not retry that input, delete a lock or
restore an unrelated checkpoint. The test is recoverable from its immutable
source; Root restores it through git-show-file-to and applies only the original
source hunks through literal Make edits. Current bounded common-directory and
path-spelling helpers remain intact. The native helper safety repair has a
separate confined proposal owner and must retain index, worktree and refs.

Each active task/common-directory pair owns one namespaced gludd-git-lock
single-thread executor. Nested acquisitions reuse that worker and reference
count; unrelated tasks retain distinct ownership and unrelated repositories
may overlap. The last reservation shuts the executor down. Fork reset discards
the copied worker registry and its guard alongside existing descriptor/owner
state. This is ephemeral lock ownership, not a proof or query cache.

Cancellation shields the native acquire future, asynchronously waits for its
bounded completion despite repeated cancellation, then releases on the same
worker before propagating cancellation. Native enter errors propagate and
retire the reservation. The acquired wrapper is single-use, release is
idempotent, body exceptions retain their identity, and abandoned wrappers
attempt release. The public synchronous-context API remains: acquire with
await, then enter with with. Synchronous exit still waits for worker cleanup;
this change does not promise a fully asynchronous body/exit API.

All twelve original adversarial cases then pass1.31s/native2.3s. The unchanged
one-file coverage profile executes six complete owners:261PASS17.45s/native19.6s,
96.9% line/89.5% branch, both original85aggregate/75each-file floors passing.
Preserve .gate-logs/observed/coverage-files/20261022T062215Z-80404-bb07ced7.json,
.gate-logs/coverage-git-mutex-stage-311.json and terminal
.gate-logs/coverage-20261022T062235Z.json. Existing assertions and the twelve
source cases remain intact. The same261 complete cases pass on312 in14.11s/
native15.2s, retained at /tmp/gludd-v012-git-mutex-stage-312.{log,xml} and native
JSON under .gate-logs/observed/git-mutex-stage-312. Both separate strict scopes
and Ruff pass. Operating checks, normal collection and independent exact-source
review still need their actual receipts. Promote a cheap regression into the
existing early owner with the helper repair, preserving all154 earlier families,
three2/1/1 children,90total/60quiet,21phases/2850 and every final proof requirement.

[CPython issue107505](https://github.com/python/cpython/issues/107505), opened
July2023 for Python3.11/macOS M1 and currently closed as not planned, reports
that cancelling an executor await leaves its thread running. The official
[Future cancellation contract](https://docs.python.org/3.11/library/concurrent.futures.html#concurrent.futures.Future.cancel)
says a running call cannot be cancelled; shutdown also waits for running work.
These primary sources support using the existing stdlib executor and owning
completion explicitly, rather than inventing thread cancellation. The native
receiving tests establish Gludd's defect and repair; the report is no timing,
FIFO, crash-recovery or release proof.

The stable common-directory inode,0600 lock mode, total acquisition timeout,
POSIX process fence and non-POSIX fallback remain unchanged. Deploy through the
existing immutable generation and drain in-flight Git operations before
retiring old workers; never unlink the mutex or discard active ownership.
Rollback reverts this source integration after draining its acquired contexts,
preserving lock files and all failed receipts. No service, socket, schema or
database migration is introduced. Capacity97percent/6.7GiB, original60907,
canonical full gate/global85-75 BOTH, exact-SHA all-shard hosted/deployment,
reviewed-head and guarded promotion proof remain pending; all seven release
tasks remain unchecked. Candidate staging supplies none of those proofs.

## Verification

- `tests/unit/test_git_automation_locking.py` proves PID/thread ownership, fork
  reset, spawn timeout, abnormal-exit recovery, stable-inode behavior, and
  worktree serialization. It also proves that same-process contention consumes
  the same bounded acquisition budget as process contention.
- `tests/unit/test_git_automation_locking_deep.py`,
  `tests/unit/test_git_automation_deep.py`, and `tests/unit/test_mutex_deep.py`
  verify re-entry, bounded contention, multiple waiters, and no overlap.
- Focused production coverage must be at least 85% aggregate and at least 75%
  line and branch coverage for `locking.py`; warnings are errors. The beta4
  focused result is 169 passed with 89.0% line, 85.3% branch, and 86.36%
  combined coverage for the production module.
- The 2026-08-21 alias regression is platform-independent: a synthetic linked
  worktree enters through a directory symlink while its metadata records the
  physical path. The focused contract result is 99 passed with warnings as
  errors; `locking.py` retains 89% branch-aware coverage.

## Practitioner evidence

- [GitLab Runner issue #31003](https://gitlab.com/gitlab-org/gitlab-runner/-/issues/31003),
  reviewed 2026-08-21, reproduces the same long-lived macOS alias failure:
  `/tmp` and `/private/tmp` name one location, but relativizing across the two
  spellings produces a nonexistent path. Gludd preserves the caller spelling
  at its return boundary and canonicalizes only the internal lock identity.
- Git's official [worktree documentation](https://github.com/git/git/blob/master/Documentation/git-worktree.adoc),
  reviewed 2026-08-21, defines the private `.git` pointer and shared common Git
  directory used by linked worktrees. The implementation reads those bounded
  metadata files and does not infer that the checkout-local `.git` path is the
  shared lock root.

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
- The [Python `threading.RLock` documentation](https://docs.python.org/3/library/threading.html#threading.RLock.acquire)
  defines the timed, re-entrant acquire primitive used to bound thread
  contention without replacing the mature standard-library lock.
