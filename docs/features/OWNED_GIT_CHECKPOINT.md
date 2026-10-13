# Owned Git checkpoint admission

Source acceptance only; not a release proof or completed milestone.

Use the existing native Git stash, not a second history store. The native
`git-owned-checkpoint-push` and `git-owned-checkpoint-restore` Make targets
accept explicit `FILES`, a unique `GIT_CHECKPOINT_LABEL`, an exact restore
`GIT_CHECKPOINT_SHA`, and `GIT_CHECKPOINT_VALIDATE_ONLY=0|1` (default plan).
File names are literal, repository-relative, whitespace-free regular-file
paths. Explicit files mean the caller owns each entire file, not selected hunks.
Directories, wildcards, pathspec magic, symlinks, submodules, traversal,
duplicates, redirected Git state and detached HEAD are refused.

Execution uses native `stash push --include-untracked --message ... -- files`
and `stash apply --index <exact SHA>`. Ownership, scope and canonical worktree
identity are in the ordinary native stash message. Restore requires the exact
top stash and matching label, identity and file scope. The stash object remains
after successful restore and native failure; there is no automatic pop, drop,
clear, reset or checkout. The saved SHA is printed automatically by its owner.
No generated JSON, alternate index, daemon, backup manifest or proof cache is
created. A plan prints `native_ownership_verified=0` and no saved-SHA receipt.

Make rejects dollar-bearing checkpoint inputs at parse time before automatic
command-line exports can evaluate them. Files are forwarded through its native
value function; other admitted inputs contain no Make expression. Four real
temporary-repository regressions reproduce the prior evaluation. Unsupported
inputs remain visible refusals and cannot execute a shell function first.

Unrelated unstaged work remains untouched. Foreign staged paths are refused
before push because Git's stash index tree can capture them even with a narrow
pathspec. Restore refuses overlapping local work and any currently staged work.
Native merge conflicts retain both the stash version and current committed
version; the owner never resets or discards conflict evidence. Single-writer
coordination remains required: checks plus native Git operations are not a
global transaction against arbitrary simultaneous Git writers.

Commit and pre-commit stash guards distinguish verified retained checkpoints
from unknown hook stashes using the same native owner. Verification reads every
stash's worktree identity, literal scope, parent/index topology and captured
regular-file modes; a matching label alone is insufficient. Unknown, foreign or
malformed entries remain blocking. Redirected Git state fails closed even when
the visible stash count is zero. The inspection prints exact checkpoint SHAs
and never applies, pops, drops or deletes saved work. Known checkpoints no longer
produce instructions to pop an unrelated stash.
An unknown-stash refusal receipt uses Git's worktree-local path, preventing
this guard's regression tests or another checkout from clobbering shared state.

The installed Git rejects scoped already-staged deletions. One stdin-pathspec
experiment also created a stash before returning an error. The owner therefore
refuses staged deletions explicitly **before mutation**, rather than rewriting
the index, using a broad stash, or claiming universal support. Unstaged tracked
deletions are proven to round-trip. This unsupported boundary does not affect
the root's reported current WIP (no staged changes or tracked deletions).

## Research and native counterexamples

[Git's official stash documentation](https://git-scm.com/docs/git-stash/2.43.0)
defines pathspec rollback, untracked inclusion, index restoration and retained
`apply` semantics. These existing primitives are sufficient for supported
owned files; custom Git object/index manipulation is unnecessary.

The long-lived
[2022 scoped-stash forum report](https://stackoverflow.com/questions/73492077/git-stash-show-after-stashing-only-one-file)
explains why other staged paths can remain in a stash's index and working tree
commits. A native temporary-Git negative control reproduces this, so the owner
rejects foreign staged paths instead of assuming pathspec isolation covers the
index. The historical
[2018 untracked scoped-stash data-loss discussion](https://stackoverflow.com/questions/49437625/git-stash-push-path-deletes-untracked-files)
also shows why successful native untracked round-trip acceptance is required;
historical Git bugs are not dismissed on the basis of command syntax alone.

## Focused evidence

The artifact's initial missing-owner implementation produced 17 failures and two passes,
including the native foreign-index control. A plan-routing test failed before its repair, and
the staged-deletion pre-mutation refusal test also failed before its repair.
The final portable adoption suite has 36 native tests: Python 3.14 passed in
9.36 seconds; the existing Python 3.11 environment passed in 8.11 seconds.
Both runs use `-W error` and one worker, with no dependency/environment allocation.
Ruff and Mypy passed. Both documented plan behaviors executed without mutation.

The observed coverage replay passed 36 tests in 10.76 seconds and measured one
source file: 126/133 lines (94.74%), 60/68 branches (88.24%), combined 92.54%.
The existing coverage auditor excludes artifact paths outside the repository;
these percentages are explicitly taken from its native coverage JSON, not the
auditor's zero-file artifact summary. On adoption, use the provided repository
coverage include and rerun the normal aggregate/per-file owner. The artifact
contains no evidence of full gate, exact-SHA CI, deployment acceptance or merge.

Root's repository replay adds four Make-input execution cases and retains
every native Git assertion: all 40 pass in both Python 3.14 and Python 3.11.
Repository coverage passes the actual 85/75 audit at 93% combined coverage.
The real nonroot invocation also exposed a relative-owner diagnostic gap;
the Make fragment now anchors its script path before the native cwd check.
