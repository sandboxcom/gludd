# Idle Worktree Bytecode Cleanup

## Scope and authority

The existing `automatic_disk_cleanup` owner now reclaims ignored, untracked
`.pyc`/`.pyo` leaves directly below `__pycache__` in eligible peer worktrees.
It does not walk directories, remove cache parents, invoke recursive deletion,
change source/index/refs, or add another cleanup owner. Legacy bytecode outside
`__pycache__` remains untouched.

The existing dry-run entrypoint is:

```console
make disk-cleanup-preflight DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY=0 DISK_CLEANUP_PREFLIGHT_DRY_RUN=1 DISK_CLEANUP_RECEIPT_GRACE_SECONDS=1800
```

Real cleanup requires the normal owning workflow and review. Focused fixtures
do not establish actual reclaimed host capacity or passing disk admission.

## Selection and safeguards

Native Git selects candidates using `ls-files --others --ignored
--exclude-standard -z` and the literal pathspec
`:(top,glob)**/__pycache__/*.py[co]`. Native exclusions protect `.git`, `.gludd`,
`.gate-logs`, `ci-attempt-logs`, `.venv`, `node_modules`, `preserved-evidence`,
`release-evidence`, and `gludd-release-evidence` at every depth. Indexed bytecode
fixtures, source, nonbytecode files, symlinks and external targets are preserved.
[Git selection/NUL semantics](https://git-scm.com/docs/git-ls-files) and
[pathspec semantics](https://git-scm.com/docs/gitglossary#def_pathspec).

The owner still protects Main, the invoker, locked/detached/prunable peers and
live processes. Bytecode cannot use the fresh-active-workstream disposable-cache
exception: it requires refreshed logical inactivity or an unchanged positive
completion proof and lease. Original idle PID checks remain, with ownership and
PID revalidation for each bounded deletion batch.

Inventory is bound to the authoritative repository's common Git directory, the
registered peer's gitdir/backpointer/top-level and its real index. Inherited Git
routing/configuration overrides are refused. Route, index, ignore and config
identities are rechecked before mutation; a same-HEAD foreign index cannot make
a locally tracked fixture disposable. Git rechecks each batch's exact ignored
untracked selection. Full metadata checks occur at batch boundaries; each leaf
checks global metadata and only its own ancestor ignore rules.

Each candidate must be a same-owner regular non-symlink leaf with one link.
Directory-relative opens descend with `O_DIRECTORY | O_NOFOLLOW`; final parent
and leaf identities are checked before `unlink(..., dir_fd=...)`. Unsupported
platforms, parent swaps, inspection failures and uncertain ownership fail closed.
[Python descriptor-relative filesystem operations](https://docs.python.org/3.11/library/os.html#os.unlink).

## Bounds and observability

A pass shares a 100,000-candidate admission cap, 64 MiB Git-output budget and
120-second deadline. Deletion batches contain at most 128 leaves. The existing
Git helper has opt-in bounded stdout/stderr streaming and remaining-time limits;
it terminates and reaps its direct child on either bound. Default Git calls
retain their original behavior and 30-second timeout. Both output streams count
toward the shared budget even when a command fails or exceeds a bound.

The existing recovery target keeps its pinned system Python 3.9 and needs no
virtual environment. The imported index record retains frozen slots through a
3.9-compatible declaration; a harmless native Make dry-run fixture exercises
that bootstrap without discovering or deleting host files.

Capacity exhaustion defers remaining work and blocks materialization fallback
for that peer; it does not claim all bytecode is gone. Completed earlier peers
and partial batches retain their actual removed-file/byte counters. Safety
errors remain errors. Existing eight cleanup passes, four materialization
attempts, 20-detail reporting and the hard disk threshold are unchanged.
Dry-run reports eligible file/byte counts without unlinking. Progress is batched,
never a line per leaf. Two native 1,200-file/300-directory fixtures exercise
dry-run and apply behavior and guard against whole-worktree per-leaf scans;
their timings are fixture observations, not a production throughput claim.

## Collection policy and practitioner context

Normal shared `test-count`/`collect-check` and hosted/canonical gates keep their
bytecode warming. Blanket suppression can make later fresh pytest processes
compile again; the tradeoff is not measured for Gludd. This cleanup therefore
changes no interpreter flags, assertion rewriting or global pytest policy.
[Python bytecode startup policy](https://docs.python.org/3.11/using/cmdline.html#envvar-PYTHONDONTWRITEBYTECODE)
and [pytest assertion-cache contract](https://docs.pytest.org/en/stable/how-to/assert.html#assertion-rewriting-caches-files-on-disk).

The [2015 bytecode-cleanup discussion](https://stackoverflow.com/questions/28991015/remove-pycache-folders-and-pyc-files-from-python-project)
contains cautions about incorrect matches, whitespace and removing virtual
environment content. It supports narrow selection and preservation here, not
adoption of broad `find`, `xargs`, or recursive-removal recipes.

## ZDD, verification and rollback

This is control-plane ZDD: no serving process or active worktree is restarted.
Tests use isolated native Git repositories and temporary bytecode only.
`coverage_automatic_disk_cleanup.ini` includes the owner, leaf helper and extended
Git helper under unchanged 85% aggregate/75% per-file floors. Source review,
authorized native dry-run/apply, actual disk recheck and normal publication are
separate evidence. Full gate, hosted CI and release proof remain independent.

Rollback restores source through normal branch review. Deleted generated
bytecode is regenerable by the normal interpreter; code rollback cannot restore
its exact bytes. Source-of-record, evidence, index, refs, original cleanup
authority and disk thresholds must remain protected throughout rollback.
