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

## Collection helper import boundaries

Collection serialization and stale-owner inspection support package imports and
direct script entry points. Static typing resolves their dependency-free helpers
through the existing `MYPYPATH=src:scripts` base; package execution binds the
`scripts.*` helpers, while direct execution binds the adjacent bare modules. A
missing helper remains an import failure, never an exception-based fallback to
another module identity. The reaper's typing branch uses that same canonical
identity; its runtime package/direct branches remain unchanged.

Long-lived user reports support checking identity rather than branch spelling:
[mypy #13202](https://github.com/python/mypy/issues/13202), opened July 2022,
reproduces duplicate source discovery even with explicit package bases;
[mypy #19500](https://github.com/python/mypy/issues/19500), opened July 2025,
reproduces identity confusion around a package CLI and working directory.
These are analogous failure modes, not proof of the exact Gludd defect.

Acceptance runs both native entry points in fresh interpreters from unrelated
directories, verifies bound helper identity and distinct project resources, and
preserves injected missing-helper failures. Canonical RED has one failure with
27 controls; all 37 collection/reaper cases then pass on Python 3.11. The expanded
116-case native coverage replay measures all three existing production owners
at 90% combined coverage and passes unchanged aggregate/per-file line-and-branch
floors. No lease, process ownership, cleanup authority or runtime service changes.
Rollback reverts typing and acceptance together. Full-gate, exact-SHA hosted and
deployment proofs remain pending.

## Git-hook collection environment

A real linked-worktree pre-push reproduction identified `GIT_DIR` as the leaked
canonical-checkout reference: the hook exported its private Git directory under
Main, and pytest's unchanged confinement guard correctly rejected a plugin's
subprocess environment. Standalone collection had no such hook-local input.

Both public collection entry points now query `git rev-parse --local-env-vars`
and check its status before starting the observer. The recipe requires a
nonempty inventory of at most 64 valid `GIT_*` shell names and 8,192 characters,
then unsets exactly those names inside its own shell. It evaluates no values,
does not clear the parent hook, and does not change cwd. The existing lock owner
rediscovers the same shared Git directory from the active worktree's `.git`.
Discovery, identity and confinement failures still refuse collection.

Full `tests/` inventory, fresh collection, warning policy, selected toolchain,
observer heartbeats/tails, exit codes and canonical lease remain unchanged.
Ordinary Git config files, `GIT_EDITOR`, `GIT_OPTIONAL_LOCKS` and unrelated caller
inputs survive. Temporary staged-index context remains intact in the parent
and all commit/content/index-drift guards; only full-worktree collection removes
the native-listed override. No runner, proof cache or receipt admission is added.

This is not a blanket `GIT_*` or Makeflags sanitizer. Residual numbered
`GIT_CONFIG_KEY_n`/`GIT_CONFIG_VALUE_n` values and explicit Make CLI Git overrides
can still carry canonical paths; unchanged confinement rejects those contexts.
Do not pass repository-routing variables as collection Make assignments. If
transient command-scope configuration was required for Git identity, collection
fails closed rather than substituting another lease or bypassing Git safety.

[Git's hook contract](https://git-scm.com/docs/githooks#_description) recommends
child-local clearing for other-worktree operations, using the maintained
[name-only native inventory](https://git-scm.com/docs/git-rev-parse#Documentation/git-rev-parse.txt---local-env-vars).
The long-lived [pre-commit issue #1849](https://github.com/pre-commit/pre-commit/issues/1849),
opened in March 2021, records child Git operations contaminating the original
index with inherited `GIT_INDEX_FILE`; it supports preserving parent ownership,
not globally clearing hook state.

Native local-bare-remote tests exercise the real pre-push hook, shared Make
macro, observer, lease and pytest with an isolated three-node inventory and the
existing confinement guard. They assert key names only, exact parent/index
preservation, both targets, repeated fresh collection and failure propagation.
Before its first setup mutation, the fixture separately discovers native local
names with a checked read-only, minimal-environment probe and removes them only
from its copied setup environment. Owned-sentinel regressions preserve exact
HEAD/index/config/ref bytes under inherited `GIT_DIR` and `GIT_INDEX_FILE`; the
subsequent real pre-push still introduces its genuine `GIT_DIR`.
The parameterized hook regression is promoted into the existing mandatory
runtime owner under unchanged 90/60-second budgets, alongside mutation controls.
Delivery remains zero-downtime; rollback reverts the recipe and its admission
binding together. Focused fixtures do not replace full inventory, full gate,
normal guarded publication or exact-SHA hosted evidence.
