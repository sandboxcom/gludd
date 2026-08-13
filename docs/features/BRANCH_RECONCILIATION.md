# Branch Reconciliation

## Contract

Release reconciliation must be driven by repository-tracked, reviewable tooling.
`make branches-unmerged-development` lists every local branch whose tip is not
reachable from the explicit `development` base. It uses ref names without
worktree marker decoration and sorts them deterministically.

An empty inventory prints a stable success message. The target is read-only: it
does not check out, merge, delete, or rewrite a ref. Every candidate still goes
through semantic review, focused verification, and a transactional merge-forward
target.

A set of semantically superseded candidates uses
`development-merge-forward-batch`. Its dry run resolves every ref, rejects
`master`, removes duplicate commit IDs, and reports the exact parent count. Apply
mode is allowed only on a clean `development` checkout. It creates one
ancestry-only octopus merge with Git's `ours` strategy, runs collection once, and
aborts the entire merge if collection or commit fails. This preserves current
production content while making every reviewed historical tip reachable from the
release graph without dozens of redundant collection runs.

## Zero-downtime and observability

The inventory never changes a running service or repository state, so it cannot
cause deployment downtime. Its complete stdout is the observable handoff: a
branch is either named as a reconciliation candidate or the target explicitly
reports that every local branch is already reachable from development.

## Practitioner evidence

The official [git-branch manual](https://git-scm.com/docs/git-branch.html)
defines `--no-merged <commit>` in reachability terms and describes the result
as the candidate set for integration. The original
[2008 Git mailing-list patch](https://www.spinics.net/lists/git/msg64057.html)
documents the long-lived practitioner use case: integration work across many
branches needs a direct list of merge candidates and a visible progress view.
That matches this project's release-forward workflow and is why the base is
spelled out as `development` instead of depending on whichever branch happens
to be checked out. A long-lived practitioner discussion on
[Stack Overflow #2692583](https://stackoverflow.com/questions/2692583/how-to-do-octopus-merge-with-git)
documents the same many-parent integration need and the important limitation that
an octopus merge is appropriate only when the parents do not require substantive
conflict resolution. The batch target therefore supports ancestry-only reviewed
supersession, never content reconciliation.

## Makefile integrity

Reconciliation tooling must remain discoverable through `make help`. The deep
Makefile contract therefore treats public, directly invokable targets as
entries that need help text even when they are not prerequisites of another
target. Its lightweight prerequisite parser also follows the
[GNU make comment rule](https://www.gnu.org/software/make/manual/html_node/Makefile-Contents.html):
an unescaped `#` starts a comment and the remainder of that rule line is not a
prerequisite list. This prevents descriptive `##` annotations from becoming
fictional dependencies in release evidence. Single-line `.PHONY` declarations end
on that same line, and dotted target names remain valid public names; the
contract parser preserves both rules so it cannot hide the immediately
following target or misclassify documented model targets.
