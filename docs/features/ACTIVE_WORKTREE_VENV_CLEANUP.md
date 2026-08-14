# Active Worktree Virtual-Environment Cleanup

## Status

S83.128 replaces blanket worktree `.venv` deletion with a registered,
process-aware reclamation boundary. The invoking worktree is unconditionally
preserved. Only a different, currently registered worktree with no visible
process may have its direct `.venv` removed.

## Problem

The former `clean-worktree-venvs` recipe recursively found every `.venv` below
the two agent-worktree namespaces and passed all matches to `rm -rf`. When the
target ran from a linked worktree, that set included the interpreter and
dependencies backing the invoking checkout. The recipe also had no evidence
that another registered worktree was idle.

Deleting an environment while tools still use it is not a harmless cache
operation. It can interrupt the current command, strand background language or
test processes, and leave a partially removed environment that cannot be
reused.

## Contract

`make clean-worktree-venvs CLEAN_WORKTREE_VENVS_VALIDATE_ONLY=1` runs the
deterministic safety regression without deleting a live environment. Explicit
mode `0` runs the tracked standard-library cleaner.

The cleaner applies these rules in order:

1. Resolve the invoking path before considering any candidate. If that identity
   is unavailable, remove nothing.
2. Enumerate only Git-registered worktrees inside the two approved Gludd
   namespaces. Unregistered directories are owned by the separate orphan
   classifier and are never inferred to be disposable here.
3. Preserve the registered worktree that contains the invoking path, even when
   no process snapshot happens to mention it.
4. Require a direct, real `.venv` directory. Refuse links and non-directories.
5. Refresh Git registration immediately before deletion. A missing, moved,
   malformed, prunable, or out-of-namespace registration is a refusal.
6. Take a bounded process snapshot after the registration refresh. Preserve a
   peer when its canonical path appears in any visible command line. Process
   inspection failure is also a refusal.
7. Report every eligible, removed, skipped, or errored path and return nonzero
   for safety-evidence failures.

The authoritative registry remains `git worktree list --porcelain -z`. Git
documents this format as stable across versions and recommends NUL termination
for paths containing unusual characters:
[Git worktree list output](https://git-scm.com/docs/git-worktree.html#_list_output_format).

## Practitioner Evidence

The long-running uv issue
[#13986](https://github.com/astral-sh/uv/issues/13986), opened in June 2025,
reports that background editor processes can keep files in `.venv` open while a
removal attempt leaves the project environment unusable. The still-open uv issue
[#15603](https://github.com/astral-sh/uv/issues/15603), opened in August 2025,
records the sharper active-environment failure mode: an operation unexpectedly
removes and recreates the environment currently selected by the user.

Those reports support two independent safeguards here. Process evidence protects
other active worktrees, while invocation identity protects the current worktree
even when process enumeration has a transient blind spot.

## Security, Resources, and Observability

- Candidate authority comes from Git's canonical NUL-porcelain registry, not
  directory names, globs, branch strings, or caller-controlled shell expansion.
- The cleaner accepts only strict descendants of approved worktree namespaces
  and only the direct `.venv` child of a registered root. Symlink escapes fail
  closed.
- Registry and process inspection use fixed argument vectors with bounded
  subprocess timeouts inherited from the existing disk-safety modules.
- Work is bounded by the number of registered worktrees. Each candidate gets one
  registry refresh and one process snapshot immediately before removal.
- Output names every decision and ends with eligible, removed, skipped, and
  error counters. There is no silent `2>/dev/null || true` success path.
- The cleaner is invoked with the system Python and the Make goal is on the
  no-uv path, so cleanup does not create another project environment.

## Zero-Downtime Delivery and Rollback

This change has no service, schema, or persistent-data migration. Roll it out by
merging the tracked cleaner and Make recipe before authorizing mode `0`. Existing
worktrees continue running throughout; active and invoking environments are
preserved, and an inactive peer recreates its environment through the normal
locked uv workflow on its next dependency-bearing command.

Rollback is a code-only revert of the cleaner, Make contract, tests, and this
document. Do not restore blanket deletion. Until a corrected cleanup is
available, keep validation mode enabled and reclaim a reviewed inactive
worktree through its normal lifecycle tooling. A removed inactive `.venv`
contains no source-of-record data and is recreated from `pyproject.toml` and
`uv.lock`.
