# Cross-worktree Collection Lease

## Problem

Repository-wide pytest collection was serialized only per checkout. The lease
namespace was derived from the current worktree path, so the main checkout and
each linked worktree opened different lock files even though they walked the
same repository test inventory. Two agents could therefore collect the full
suite concurrently, duplicate a large memory and process load, and race on
shared repository metadata.

The upstream practitioner record shows why concurrent pytest processes need an
explicit owner boundary. In
[pytest issue #5456](https://github.com/pytest-dev/pytest/issues/5456), opened
in 2019, concurrent invocations raced on pytest temporary-directory lock state
and produced an internal `FileNotFoundError`. The exact Gludd failure differs,
but the durable lesson is the same: separate pytest processes cannot assume
that shared collection-adjacent state is isolated.

## Contract

The existing `fcntl.flock` lease remains the serialization mechanism. Before
opening it, `git rev-parse --git-common-dir` resolves the one Git directory
shared by the main checkout and every linked worktree. A path-safe label and a
SHA-256 digest of that resolved directory form the resource namespace. Separate
clones retain separate namespaces, while all worktrees of one clone select the
same `collection.lock` inode.

Git documents that linked worktrees have private Git directories but share
`$GIT_COMMON_DIR`, and recommends Git commands instead of assumptions about
repository layout:
[Git worktree documentation](https://git-scm.com/docs/git-worktree). Python's
[`fcntl.flock` documentation](https://docs.python.org/3/library/fcntl.html)
provides the maintained exclusive, non-blocking lock primitive used here.

Identity discovery is bounded to five seconds and fails closed. The collection
command does not start when Git cannot prove the common directory. Lock waits
retain their existing bounded resource-specific deadlines. Waiting, acquisition,
release, elapsed time, resource name, path, and owner PID are emitted directly
to the observable command stream.

The PID and acquisition timestamp in the file are evidence, not authority.
Kernel ownership of the open descriptor is authoritative: process exit releases
the lease, and the next successful owner overwrites any stale record without
unlinking or replacing the lock inode. Explicit `GLUDD_COLLECTION_LOCK`
configuration remains an operator-owned escape hatch.

`make active-work-status` exposes the same parser and non-mutating lock probe
under `resource_observability.collection_lease`. It reports only the bounded
state, sanitized path, validated owner PID and acquisition timestamp, record
status, and a capped waiter PID list. `waiter_count` retains the total observed
count while `waiter_overflow_count` says how many entries were omitted. Raw lock
contents and process command lines are never copied into this object. Adding the
object does not rename or remove existing JSON fields, and collection wrappers
are not counted as admitted workers, so existing namespaced resource and worker
counts retain their meaning.

Both repository-wide entry points, `make collect-check` and `make test-count`,
enter the same canonical lease before pytest starts. The observable wrapper
remains outside the lease so a queued invocation continues to emit bounded
heartbeats while it waits; only the memory-intensive collection child is
serialized.

## Zero-downtime delivery and rollback

This guardrail is zero-downtime: it adds no service, daemon, listener, migration,
or application-state transition. Existing collectors finish under the path they
opened; new collectors use the repository-common path on their next invocation.
The change never terminates a collector and does not increase worker counts.

Rollback reverts the resolver, regressions, and this document together after
any active collection finishes. It restores worktree-local serialization and
therefore the known concurrent-collection risk; no persisted data or service
state needs migration.
