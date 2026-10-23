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

## Native Make transaction receiving (2026-10-23 UTC)

S98.4 receives this source in the isolated agent-v012-commit-transaction
worktree from4604eeccc90ce9e89e575a8641ba1213a15f9925. The former
_commit-lock-acquire prerequisite closed its flock descriptor before later
staging/check/commit recipes. Its global temporary path also did not share
the established repository mutex. Two actual Make callers reproduce the
unheld common-directory lock during collection in both one checkout and
linked worktrees:2FAIL/1PASS4.91s/native5.9s at
.gate-logs/observed/commit-transaction-red/commit-transaction-native-red311-4604.json.
Only the old global lock path is confined to fixture scratch; the native
recipes, real Git operations and assertion intent remain.

The existing git_repo_lock now spans one native transaction. Twelve public
commit entrypoints retain their names and original bodies in private rules,
including both commit-and-ship composites' original lint/staging work.
The existing stream_command.py is the real OS observer entrypoint with literal
--label commit-transaction. A lazy fixed-source runpy dispatch loads this
checkout's locking.py and supplies this actual observer/identity function.
Source inspection rejected a locking.py process merely importing the observer:
the existing process/protection inventory requires the native source role and
literal label. This record preserves that decision; the
[phase audit](GHA_PIPELINE_PROMOTION_AUDIT.md) separates source acceptance from
remaining release proof. Status/tail remain independent of the lazy commit import. No new runner, mutex, daemon, scanner or proof cache
is introduced.

Private entry requires the actual inherited regular-file descriptor to match
the common-directory inode and pass native nonblocking exclusive flock.
An environment marker or fstat match alone grants no authority. Missing,
closed, foreign, same-inode independent and native-integer-overflow descriptors
refuse before staging. Validation uses the existing selected UV/runtime,
including an actual UV/no-sync control that creates no environment; an ambient
internal interpreter marker cannot select /usr/bin/true to skip validation.
The existing observer passes only owned lock/native jobserver descriptors,
retains ordinary caller kwargs, and drains the native process group before
ordinary signal/timeout return. Installed GNU Make3.81 parent sibling and
-j2 controls retain actual concurrency and warnings; private ordering is
conditional on an actual private child goal.

All public/private transaction rules and the lock/gate guard are explicitly
phony. Same-named root files therefore cannot silently turn an action into
an up-to-date result, and the fixture retains their original bytes.
GNU Make inspection captures only this invocation's native options before
command assignments. Public -q/-t/-n use native phony inspection, emit planned
source_checkpoint=false guidance and execute no staging, collection, commit,
target touch or observer update. Actual native statuses remain query1 and
touch/dry-run0, including with a prior current receipt. An initial helper
query1 produced Make2; a first-word-only predicate missed literal -q after
--no-print-directory, and indirect recursive prefixes behave differently under
touch. Those native failures and the bounded flag witness are retained.
Do not infer a checkpoint or proof from any inspection result.

The original complete five-owner focused cohort passes237/85.03s/native86.7s
before the later phony/inspection repair. The final inspection cohort passes
20/12.31s/native13.6s at
.gate-logs/observed/commit-transaction-make-modes/commit-transaction-native-inspection311-4604.json.
It retains Make's statuses, absent/unchanged current pointers, source/index
bytes, all twelve dry-run routes and actual jobserver success. Existing guard,
single fresh collection, exact scope/tree, drift/failure and mutation assertions
remain; only native private-rule selectors and owned adapters conform.
The native controls substitute harmless adapters only at the existing quality/
collection boundaries. They certify the transaction boundary, not a full gate.

Final ordinary receiving of the frozen production changes from4604 passes
1658 tests on EACH installed Python3.11.14 and3.12.15, with fatal warnings:
357 complete mutex tests,527 complete observer tests and774 remaining consumers.
The deduplicated39-owner union is partitioned into8/12/19 complete owners;
every partition retains its original300 total/60 quiet seconds. Exact receipts
under .gate-logs/observed are
commit-transaction-mutex-receiving/commit-transaction-mutex-receiving{311,312}-4604.json,
commit-transaction-observer-receiving/commit-transaction-observer-receiving{311,312}-4604.json
and commit-transaction-consumer-receiving/commit-transaction-consumer-receiving{311,312}-4604.json.
Native elapsed seconds are87.129/85.751,65.199/79.551 and165.882/192.020,
respectively. Identical commands ran sequentially across runtimes.

Whole unchanged Python311 coverage profiles retain85aggregate/75each LINE
AND BRANCH: mutex357 gives locking96.1% line/88.2% branch at
.gate-logs/coverage-20261023T200304Z.json; observer673 gives aggregate91.8/86.0
at .gate-logs/coverage-20261023T201706Z.json. Individual observer files are
active_work_status85.0/82.1, stream_command91.5/84.7, summarize98.8/97.1 and
trace_plugin96.7/87.5. These actual Python311 measurements grant no Python312
coverage claim; Python312 has the complete ordinary receiving above.

Preserve both original39-owner timeouts: instrumented coverage timed out124
at300.561s and ordinary receiving timed out124 at300.542s. The latter retains
the actual logical-Makefile AST failure in the new fixture. Its isolated RED
failed1 in18.56s; the fixture-only compose_makefile correction then passed the
complete native/layout owners165 in83.18s/native85.3s. Production coverage
bytes, original assertions and canonical profile were unchanged. Before broad
receiving for Make-reading tests, run the existing
tests/unit/test_makefile_layout.py::test_tests_read_the_logical_makefile_instead_of_only_the_entrypoint
through test-files with explicit inputs. Use compose_makefile even for an
unsplit fixture. This cheap existing check localizes the observed defect;
neither the partition nor trace spans prove hosted speed improvement.

Normal per-commit collection, independent source review, Main receiving,
canonical full gate and exact-SHA hosted/deployment proof remain separate.
A required followup must release the common Git mutation mutex before any
hosted wait: public push composites still hold it through their original
publication calls in this packet. Obtain an actual blocked-wait/linked-writer
RED, preserve PUSH audits, condition/order/native statuses and GNU Make
n/q/t no-mutation controls, then receive that change with the hosted owner.
Current receipts cannot certify that later source.

Successful native signal/drain controls do not certify denied OS inspection.
A terminal zero means the required children returned zero; inspect actual
commit output/worktree-state before claiming a new commit. Bare independent
git-add remains a single-coordinator mutation boundary and tree revalidation
still refuses uncooperative index drift. No foreign rollback or force unlock
is authorized.

Stop dispatching Git writers and drain old commit processes before activating
new harness source across worktrees. Keep application generations serving.
Rollback also drains active transactions before a source revert, preserving
stable mutex inodes, index/worktree bytes, refs, checkpoints and failed receipts.
This is no service, schema, account, deployment or publication migration.
The [GNU recipe shell contract](https://www.gnu.org/software/make/manual/html_node/Execution.html)
and [.ONESHELL limitations](https://www.gnu.org/software/make/manual/html_node/One-Shell.html),
retrieved through the bounded phase research owner, explain why a prerequisite
or global one-shell setting cannot provide a whole transaction lifetime.
The first-hand inode/fork reports below retain the long-lived operational
context; native tests establish this project's actual boundary.
All seven release items remain open until the canonical complete gate and
required exact-SHA hosted/deployment/promotion proofs.

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
