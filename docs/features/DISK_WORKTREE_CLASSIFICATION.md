# Registered Worktree Disk Classification

## Status

S83.106 defines the disk guard boundary between active Git worktrees and
generated `/tmp/gludd-*` scratch. The guard is read-only: it classifies and
reports usage but never deletes a worktree, cache, virtual environment, or
orphan.

## Problem

`/tmp/gludd-worktrees` is a namespace, not one disposable scratch tree. A direct
child may be a registered worktree, an abandoned directory, or a branch-family
container such as `feature/` whose registered worktrees and orphan siblings are
nested below it. Treating every child as active hides orphan growth. Treating the
whole namespace as scratch charges several gigabytes of legitimate, concurrently
active development state against the 100 MB generated-scratch budget.

## Contract

The checker obtains the authoritative registry with
`git worktree list --porcelain -z`. Git documents porcelain output as stable
across versions and recommends the NUL form so unusual path characters remain
unambiguous:
[git-worktree documentation](https://git-scm.com/docs/git-worktree.html#_list_output_format).

Classification is exact and fail closed:

- A worktree is active only when Git lists it, the record is not `prunable`, the
  path exists as a directory, and its canonical path is a strict descendant of
  the canonical `/tmp/gludd-worktrees` root.
- An unregistered ancestor of a registered worktree is a namespace container,
  not an orphan root. The checker recursively partitions that container: an
  exact registered descendant receives the active exemption, while every
  unregistered sibling file or subtree counts in full.
- Source and `.venv` content in an active registered worktree are development
  state and do not count against the generated-scratch budget.
- `.pytest_cache`, `.mypy_cache`, and `.ruff_cache` in an active worktree remain
  generated artifacts and do count.
- Every byte under an unregistered child is orphan scratch and counts, including
  a stale `.venv`. Every other `/tmp/gludd-*` path also counts.
- A failed Git command, malformed registry record, relative path, missing path,
  prunable registration, symlink escape, recursive namespace link, or path
  outside the namespace cannot earn an exemption. Registry and disk-inspection
  failures make the guard fail.
- Generated scratch above 100 MB or repository-volume use above 90% makes
  `check-disk` fail. The separate cleanup-oriented `disk-check` retains its own
  95% policy.

The deterministic Make contract is
`make check-disk CHECK_DISK_VALIDATE_ONLY=1`; normal enforcement is
`make check-disk CHECK_DISK_VALIDATE_ONLY=0`.

The checker and cleanup targets are lightweight host diagnostics. They are in
Make's no-uv goal set and invoke their standard-library entry points with the
system Python, so classification or cleanup inspection never bootstraps a
project `.venv`. Validate-only modes explicitly delegate to `test-files`; that
test execution is the only intentional dependency-environment boundary.

`make check-disk-classification` provides the audit proof without applying the
threshold verdict. It emits at most 40 JSON-lines records ranked by counted
bytes, followed by a summary with total and omitted entry counts. Registered
records expose both observed bytes and counted cache bytes, so a large active
`.venv` is visibly excluded rather than silently disappearing from the report.
Normal failure output names and sizes the three largest counted roots.

The companion cleanup contract is
`make tmp-gludd-clean-ci-shards-now TMP_GLUDD_CLEAN_VALIDATE_ONLY=1` for
deterministic validation and the same target with `0` for an authorized live
cleanup. It includes inactive `gludd-gate-unit-*` directories. Immediately
before deleting each stale directory, the script takes one bounded `/bin/ps`
snapshot and refuses that root when an active command line references its exact
path. Process-inspection failure also refuses deletion and makes the target
nonzero. Matching files are reported but never deleted.

Classifier-proven orphan worktrees have a separate validate-first contract:
`make tmp-gludd-clean-orphan-worktrees-now
TMP_GLUDD_ORPHAN_CLEAN_VALIDATE_ONLY=1`. Mode `0` is reserved for an explicit
post-merge cleanup; it is not part of feature validation. Immediately before
each deletion, the cleanup refreshes both Git registration and classification.
It refuses a candidate that equals, contains, or is contained by any registered
path, falls outside the canonical namespace, changes classification, or has an
active process. Thus a container such as `feature/` remains intact around an
active nested worktree while a classifier-proven unregistered sibling can be
removed independently.

## Practitioner Evidence

A long-running Stack Overflow discussion documents the operational distinction
between a directory and Git's retained linked-worktree registration. Users who
delete a worktree directory can remain blocked until `git worktree prune`
reconciles the registry, which is why directory-name heuristics are not an
authoritative lifecycle signal:
[How to delete a worktree branch after its directory was removed?](https://stackoverflow.com/questions/44109234/how-to-delete-a-git-working-tree-branch-when-its-working-directory-has-been-removed/75757016)
(opened in 2017 and still updated in 2026).

This contract deliberately does not run `git worktree prune`: reconciliation is
a state-changing operator decision, while a pre-commit safety check must remain
read-only and must not destroy recoverable work.

## Security and Resource Boundaries

The subprocess uses a fixed argument vector with no shell. NUL-delimited records
avoid whitespace and newline parsing ambiguity. Canonical descendant checks
prevent prefix collisions such as `gludd-worktrees-old` and prevent a symlink
from exempting an unrelated tree. Ancestor containers are traversed only to
partition exact registered descendants from genuine unregistered children; the
container itself receives no blanket exemption. Registry uncertainty increases
enforcement rather than broadening trust.

Enumeration is bounded by a ten-second timeout and one Git process. Cleanup
process inspection is likewise bounded to one fixed-argument `/bin/ps` command
per deletion candidate, with no shell or optional Python dependency. Orphan
cleanup also refreshes the bounded Git registry and classifier serially for each
candidate to close registration races. Exact path boundaries prevent a
candidate such as `gate-unit-3-a` from matching `gate-unit-3-a-other`. Files are
walked and roots are removed serially, so the guard does not multiply I/O load.
The namespace supports both
`/tmp/gludd-worktrees/<branch>` and nested
`/tmp/gludd-worktrees/<family>/<branch>` identities so concurrent workstreams
remain isolated; paths outside that namespace never receive the worktree
exemption.

## Zero-Downtime Delivery and Rollback

Promotion is additive and requires no daemon restart, migration, or service
outage:

1. Run the deterministic validation contract and focused line/branch coverage.
2. Run the normal guard to observe the host's real orphan-scratch and disk state.
3. Merge through development, then promote development to master only after the
   repository gate and CI are green.

Rollback reverts the checker, cleanup modes, Make contracts, tests, and this
specification as one small change. The checker never deletes data or mutates
Git's registry. Cleanup removes only explicitly matched inactive generated
scratch or refreshed classifier-proven orphan roots. Removed output is not
restored by code rollback, so orphan cleanup remains validation-only until the
change has merged. During rollback the 100 MB scratch and 90% disk limits must
stay enabled; operators must not bypass either threshold to mask a
classification regression.
