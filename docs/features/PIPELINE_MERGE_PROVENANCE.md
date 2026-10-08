# Pipeline Merge Provenance

Issue 77 S4 closes a data-loss path in pipeline admission. A worktree result is
now merged against the exact commit from which that worktree forked. The live
repository is never substituted for the merge base.

## Failure being closed

The former adapter could call a three-way merge with the current repository as
both `base` and `ours`. In that shape, only `theirs` appears to have changed, so
a concurrent repository edit can be overwritten without producing a conflict.
The conflict branch therefore provided no protection against the event it was
intended to detect.

Every non-empty admission now requires `CompletedUnit.base_sha` to be a full
40- or 64-hex object ID. An absent, symbolic, invalid, or unreadable fork point
fails closed. The repository remains unchanged and the worktree remains
available for diagnosis or retry.

## Admission protocol

The adapter holds the repository mutation lock and processes changed paths in
the supplied order:

1. Reject an unsafe, duplicate, non-string, or excessive changed-path list.
2. Read bounded regular-file snapshots for the live repository (`ours`) and
   agent worktree (`theirs`) without following a final symlink.
3. Use `git ls-tree -z --full-tree` with a literal path to distinguish a file
   absent at the fork point from a Git failure.
4. Ask `git cat-file -s` for the blob size before materializing it with
   `git cat-file blob`; a Git error is never interpreted as a new file.
5. Run `git merge-file --stdout ours base theirs`. Exit zero admits the result,
   exit 1 through 127 is a content conflict, and every other result is a tool
   failure. Conflict output is never installed.
6. Defer repository writes until all paths pass. Apply each result through a
   same-directory temporary file and atomic replacement. If a later write
   fails, restore already replaced files from their bounded snapshots.
7. Reclaim the worktree only after the complete admission and write phase is
   green.

New-file admission also uses provenance. If the path was absent at the fork
point, it is accepted only when the repository still lacks the path or both
sides independently produced identical bytes. Different concurrent additions
conflict. A repository deletion combined with a worktree modification also
conflicts; an unchanged worktree copy preserves the repository deletion.

## Resource and diagnostic bounds

The merge lane is intentionally serial. At most one merge subprocess runs at a
time, with a 15-second timeout per Git invocation. Admission has these hard
ceilings:

| Resource | Ceiling | Failure behavior |
| --- | ---: | --- |
| Changed paths | 256 | Refuse the unit before reading content |
| Any base, ours, theirs, or result file | 8 MiB | Refuse without writing |
| Total materialized input and result bytes | 64 MiB | Refuse without writing |
| Logged Git diagnostic | 4 KiB | Truncate before logging |
| Merge input temporary files | Three, reused sequentially | Remove on every context exit |

Subprocesses never use a shell. Outcome details contain fixed reason classes and
relative paths, not raw Git diagnostics. The original worktree is the durable
recovery artifact for every refusal.

## Zero-downtime delivery and rollback

This change adds no database migration, service port, background process, or
shared on-disk format. It is safe for rolling delivery because each invocation
is self-contained under the existing repository lock. Older in-flight units
that lack fork-point provenance are refused rather than interpreted under the
old unsafe rule; operators can recreate them from a recorded fork point or
resolve them manually while their worktrees remain intact.

Rollback is a code revert. No data downgrade is needed. A refusal performs no
repository write, and a write-phase error attempts immediate restoration before
returning red. Operators must not delete a refused worktree until its changes
are reconciled. The pipeline gate remains the post-admission verification layer;
merge admission does not weaken or bypass it.

The bounded file count, bytes, subprocess duration, diagnostics, and temporary
file lifecycle prevent one unit from monopolizing memory, disk, logs, or the
integration lane. Processing sequentially also avoids a burst of Git children
and keeps multiple namespaced projects from competing for unbounded resources.

## Evidence and tests

The focused tests exercise the original missing-base failure first, real
fork-point conflicts, disjoint edits that retain both sides, independent new
files, Git tool errors and timeouts, worktree preservation, temporary cleanup,
and every declared resource ceiling. Targeted branch coverage uses
`config/coverage_pipeline_merge_provenance.ini` with an 85 percent aggregate
floor and a 75 percent per-file floor.

## Upstream and practitioner evidence

The [official `git merge-file` manual][merge-file] defines the three inputs as
current, base, and other; documents `--stdout`; and specifies zero for a clean
merge, a conflict count up to 127, and a negative error result. The official
[`git ls-tree`][ls-tree] and [`git cat-file`][cat-file] manuals provide the
plumbing used to prove path absence and inspect a blob before reading it.

A [2018 practitioner question about recovering a conflicting file's common
ancestor][forum-2018] records the operational need for all three versions and
explains that Git's conflict index keeps base, ours, and theirs separately. A
[2022 practitioner report about true single-file three-way merges][forum-2022]
shows the concrete loss mode: selecting only ours or theirs discards a
non-conflicting edit from the other side. These long-lived reports reinforce the
design rule that an explicit common ancestor is required; a two-copy overwrite
is not a merge.

Sources were reviewed on 2026-10-08.

[cat-file]: https://git-scm.com/docs/git-cat-file
[forum-2018]: https://stackoverflow.com/questions/50464726/how-to-get-the-common-ancestor-of-a-conflicting-file-with-git
[forum-2022]: https://stackoverflow.com/questions/71533905/git-merge-a-single-file-and-a-true-3-way-merge-not-just-a-checkout-of-ours-v
[ls-tree]: https://git-scm.com/docs/git-ls-tree
[merge-file]: https://git-scm.com/docs/git-merge-file
