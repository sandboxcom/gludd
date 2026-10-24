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

## Task handoff correction after independent rejection (2026-10-22 UTC)

Exact candidate62151e62d048c2f425cdc016f682da86ab9e15f6 receives independent
CHANGES REQUIRED against82db11b57b254c1ca1e035975b5cc86253e2fed5. Root fully reads
/tmp/gludd-v012-git-mutex-621-source-review.md and the separate exact correction
proposal. Preserve that immutable negative decision. Earlier261-case passes and
96.9line/89.5branch measure the original source/consumer scope; they never
override a later semantic failure. Normal621 hooks retain128731selected/
128755collected,24deselected/zeroerrors49.21s/native54.504s, zero new clones and six
retained checkpoints. Native receipt:
.gate-logs/observed/test-count/20261022T062646Z-83008-3cc4c8cc.json.

The acquired wrapper entered any task, while its worker registry still named
the acquiring task. A foreign consumer could therefore hold the context while
the original task re-entered the same native RLock, overlapping critical
sections. A completed acquisition task created by wait_for also kept its old
registry identity, so its parent's subsequent direct nested acquisition selected
another worker and timed out against its own held lock.

One independent fixture-only native run loads exact frozen621 and proves both:
2FAIL0.45s/native1.175s, retained at
/tmp/gludd-v012-git-mutex-621-repro.log and
.gate-logs/observed/git-mutex-review621-handoff/v012-git-mutex-review621-handoff.json.
Its actual child pins equal physical rootdir/confcutdir and project config;
the source postimage and all12 cases remain unchanged during diagnosis.

Root ports both intents additively into
tests/unit/test_git_automation_locking_handoff.py. Multiple-reservation and
destination-worker collision controls require unchanged registry identities,
reference counts and native lock depth after refusal; resolving the actual
reservation ambiguity then permits safe adoption. A completed-loop result also
retains the public synchronous context API. The five controls first reproduce
4FAIL/1PASS0.80s/native1.7s in
/tmp/gludd-v012-git-mutex-handoff-red311.{log,xml}. All12 predecessor cases and
their assertions remain untouched.

Before first entry, a live acquiring task may not hand an unopened wrapper to
another task. A completed acquiring task's sole result can be adopted by its
consuming task, or the consuming thread after its event loop closes. Adoption
requires one reservation, the exact existing worker binding and no destination
conflict, all checked under the existing registry guard. Only that identity
changes; the same native worker retains RLock/flock ownership. A refused entry
does not set the wrapper's entered flag or move/drop any reservation.

Direct nesting by the consuming task then reuses the worker. Awaiting a new
task's acquisition while already holding an outer context still creates a
distinct contender. Do not treat inherited context or a shared executor thread
as logical re-entry permission for independent tasks. Synchronous exit,
cancellation drainage, final reference shutdown, deadlines, metadata resolution,
stable inode and existing process fences remain. The fork dictionary/descriptor
controls do not certify teardown of a live inherited async wrapper; preserve
that unproved boundary rather than broadening the source claim.

The original12 plus new5 cases pass17 in1.30s/native2.1s, retained at
/tmp/gludd-v012-git-mutex-handoff-green311.{log,xml}. Seven complete owners then
pass266 on311 in15.42s/native17.6s under the unchanged one-file profile:
96.8% line/90.7% branch, both85aggregate/75each-file requirements passing.
Preserve native coverage-files/20261022T063717Z-94134-2804838a.json,
.gate-logs/coverage-git-mutex-handoff-311.json and terminal
.gate-logs/coverage-20261022T063735Z.json. The same266 cases also pass312 in14.90s/native16.1s at
/tmp/gludd-v012-git-mutex-handoff-312.{log,xml}; both separate strict scopes and
Ruff pass.

Normal49bb1326bb8adf818d476ca3ade4751487735834 hooks retain128736selected/
128760collected,24deselected/zero errors56.55s/native63.545s, zero new clones and
six checkpoints. Receipt:
.gate-logs/observed/test-count/20261022T064554Z-890-82da95ff.json.
Independent APPROVE SOURCE ONLY against rejected621 is fully read at
/tmp/gludd-v012-git-mutex-49-source-review.md. It accepts the handoff scope and
all five additive controls, preserving the earlier rejection and unproved fork
boundary. Four complete operating owners pass124 in6.92s/native7.8s; Markdown,
registration, native SESSION validation and the unchanged line ceiling pass.
This receiving documentation binds that actual review/collection without
rerunning accepted source work. The two semantic controls now have additive
early routes155/156 after all154 predecessors; the
[admission record](INTEGRATION_ADMISSION.md#git-task-handoff-admission-and-confined-duration-fixtures-2026-10-22-utc)
owns their failing-first routing and remaining receiving proof. Original2/1/1,
90/60,21/2850 and required full gate remain.

Exact41b8ea255b23dc309f8d0114ff56ea3174ba2ee6 normal hooks retain128738selected/
128762collected,24deselected0errors51.42s/native57.111s, zero new clones/six checkpoints.
Independent APPROVE SOURCE ONLY against49bb is fully read at
/tmp/gludd-v012-git-handoff-admission-41-source-review.md. Source remains frozen.
This ownership correction is separate from the now-receiving
[native merge-helper repair](GIT_MERGE_NO_COMMIT.md). Its failed approaches,
checkpoint/ignored-file controls and unknown index.lock cause remain distinct.
Neither source change clears capacity97percent/7.8GiB,
original60907, full canonical gate/global coverage, exact-SHA all-shard hosted/
deployment/reviewed-head or guarded promotion proof. All seven tasks stay open.

## Immutable commit comparison for source review

The S98.4 merge reviewer needed to compare immutable merge
7e8669178a979c4f8584a9f0243ae58e1a03be1f with source
713af3ac0869c17f7792e4f4348855efc6f7baed. The existing git-diff-full
reads the working tree against HEAD and converts a native Git failure to
"No diff"; it cannot answer that pinned comparison. Its historical behavior
and every other Git target remain unchanged.

The additive git-compare-commits target accepts only explicit GIT_COMPARE_BASE
and GIT_COMPARE_HEAD, each a literal full40hex object ID. An early native Make
block captures raw public values into overridden private exports and unexports
the public inputs before startup. Pure Make hex stripping/counting rejects
foreign bytes, missing/short inputs and multiple goals before subprocesses.
The existing Python recipe verifies each direct object type with native
cat-file; blobs, trees and annotated tag objects refuse instead of peeling to a
commit. It then runs:

~~~text
git --no-pager --no-optional-locks diff --no-ext-diff --no-textconv --no-renames --no-color --exit-code BASE HEAD --
~~~

The terminal -- excludes caller file scope. No arbitrary options, output path,
new helper, observer or cache is introduced. Stdout is patch only; stderr keeps
Git diagnostics and outcome-specific AGENT_NEXT with the actual pinned pair.
Native0 means identical trees, even at different commit IDs; native1 means
differences and is Make2/Error1. Native errors remain errors, including their
actual status. Invalid syntax or wrong object type is policy refusal2.

The safe registered example compares
4604eeccc90ce9e89e575a8641ba1213a15f9925 with itself. The
[mandatory Make guide](../../.agents/policy/key-make-targets.md#compare-two-immutable-commit-trees-for-source-review)
documents the explicit invocation and statuses. Tests use real private Git
repositories, dirty staged and working bytes, equal-tree distinct commits,
different commits, missing/noncommit objects, native failure, disabled external
diff/textconv and active same-origin Make/backtick witnesses. CLI, environment
and -e retain normal precedence. Index bytes/metadata, refs/HEAD and working
bytes must remain unchanged; unguarded witnesses must actually execute the
same payload before guarded no-evaluation assertions are accepted.

This interface is diagnostic source-review tooling. It supplies no semantic
approval, ancestry result, collection, canonical full gate, exact-SHA hosted
CI, deployment, capacity clearance or release completion. Existing coverage,
ZDD, promotion and final-proof guards remain. Local validation remains distinct from those authorities.

### Native local validation (2026-10-24 UTC)

The isolated source base is 31de23bbd8260528dcaba64cac7da1904829c29d on
agent-v012-worktree-create; Main was not edited. Before adding the target, all
nine real private-Git result cases failed against the missing interface:
same SHA, different SHA/equal tree and differing trees, across CLI/env/-e.
The retained RED is 9 FAIL in 5.62s, native6.674s/status2 at
.gate-logs/observed/git-compare-red/git-compare-red311-31de23b.json
(00:28:45.449038 through00:28:52.123499 UTC). The later additive implementation
then passed the complete new owner: 48 PASS in23.56s/native24.901s/status0,
.gate-logs/observed/git-compare-focus/git-compare-focus311-31de23b.json
(00:29:18.761256 through00:29:43.662109 UTC), CPython3.11.14, fatal warnings,
n0, explicit ID= and original300/60 watched bounds. Both exact sessions drained.

The 12 active same-origin witnesses cover Make and backtick payloads, both
inputs and CLI/env/-e. They first prove the same unguarded payload executes,
remove only that owned marker, then prove guarded refusal before startup/Git.
The complete native positives also preserve dirty index bytes/mtime/mode/inode,
HEAD, refs and working bytes and retain native1/128/37 versus policy2.

The changed target's actual documented equal-SHA example returned native0 with
empty stdout and outcome=identical/pinned-pair/no-gate guidance.
check-make-target-contract passed293 targets. Test-source strict typing and the
native environment-write guard passed. An initial Ruff121-character decorator
failure was corrected only by line wrapping; Ruff then passed.

The first peer Markdown check refused because its existing owner requires the
peer-local .venv/bin/rumdl, which is absent. No environment was installed.
The same native lint-markdown owner was run from Main with the two absolute
peer Markdown paths and the absolute unchanged peer config; the existing locked
binary reported both files clean. This read changed no Main source/index.
The first line-limit check reported the contract JSON at2504lines. Compacting
only the adjacent pre-existing git-show-full record from seven lines to one
preserved its exact data and restored the unchanged <2500 policy;
check-file-line-limits passed10871tracked files, then the293-target contract
check passed again. No threshold, assertion or Git target behavior was weakened.

### Complete receiving and diagnostic use (2026-10-24 UTC)

Root staged the exact six-path change based on
31de23bbd8260528dcaba64cac7da1904829c29d and completed the native content
preflight in 7.5s: all three hooks passed, staged line policy passed, zero new
clones. Its retained native receipt is
.gate-logs/observed/git-compare-staged-preflight/git-compare-staged-preflight311-31de23b.json;
the exact staged observation was 1792802113. Source/tests/configuration stayed
frozen through both complete receiving executions.

The complete seven-owner union was:

~~~text
tests/unit/test_git_compare_commits.py
tests/unit/test_make_target_contract.py
tests/unit/test_makefile_layout.py
tests/unit/test_make_readonly_version.py
tests/unit/test_make_help_audit.py
tests/unit/test_make_help_throughput.py
tests/unit/test_status_snapshot.py
~~~

Native collection actually selected 190 cases on each runtime. Both executions
passed all 190, with no skips or warnings, n0, explicit ID=, fatal warnings,
native trace and the unchanged 300/60 watched bounds:

- CPython 3.11.14: 190 PASS in 51.29s / native 52.619s, terminal 0,
  .gate-logs/observed/git-compare-receiving/git-compare-receiving311-31de23b.json;
  UTC 00:35:54.811481 through 00:36:47.430171.
- CPython 3.12.15: 190 PASS in 55.72s / native 56.902s, terminal 0,
  .gate-logs/observed/git-compare-receiving/git-compare-receiving312-31de23b.json;
  UTC 00:37:02.430419 through 00:37:59.332977.

The exact native sessions 72699 and 25113 drained before the final receipt-only
prose. Final staged-source observation 1792802290 retained the same full 31de HEAD,
six staged paths and zero unstaged/untracked entries. Root owns staging this
final feature prose and normal guarded commit/collection. Receipt prose alone
does not justify replaying the already accepted unchanged source union.

The target also enabled actual independent diagnostics while other work
continued. Root compared source 713af3ac0869c17f7792e4f4348855efc6f7baed
with merge 7e8669178a979c4f8584a9f0243ae58e1a03be1f: the native diff
contained only five metadata paths, and the separate Main source-only review
completed. The Hosted owner's retained eb-to-fd8 diagnostic independently
confirmed workflow-byte changes rather than inferring them from its history
query. Those short diagnostic labels are not valid target inputs; the target
still requires the actual full40hex pair. Both actual uses returned native
DIFFERENT 1 / GNU Make 2, the expected patch-bearing result to review, not a failed
proof to rerun unchanged.

These diagnostics supply tree-content evidence only. Main's source-only review
does not approve this comparator's future commit or establish a full gate,
hosted CI, deployment or release result. This complete receiving covers the isolated
six-path change based on 31de; receiving an overlapping merge still needs
its own actual evidence. Independent source review, normal guarded collection
and any final exact-SHA proofs retain their original owners and limits.

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
