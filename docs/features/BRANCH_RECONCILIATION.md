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

## Bounded classification contract

`make branch-reconciliation-inventory` requires explicit
`RECONCILE_TARGET=<ref>` and `RECONCILE_LIMIT=<n>` values. It emits one JSON
document on standard output and progress on standard error, keeping the result
machine-readable without hiding operator-visible work.

Every returned local branch has one classification and lifecycle:

- `ancestor` / `historical` means its tip is reachable from the target.
- `patch-equivalent` / `historical` means it is not an ancestor, but every
  bounded `git cherry` record is `-` and its patches already exist upstream.
- `unique` / `current` means at least one `+` record exists, no patch record
  exists, or the bounded scan cannot prove equivalence.

The branch limit is restricted to 1 through 100. Sorted `git for-each-ref`
enumeration reads at most two extra rows to report truncation, and each branch
gets a 500-commit comparison bound. Counts describe only the returned set;
`truncated: true` prevents callers from mistaking partial output for a complete
inventory.

Target resolution happens before enumeration and fails closed. Empty,
whitespace-containing, option-shaped, invalid, and non-symbolic refs produce a
structured JSON error and a nonzero exit. Malformed or failed Git evidence does
the same.

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

A [2009 Git mailing-list report](https://www.spinics.net/lists/git/msg110170.html)
shows practitioners using `git cherry` minus records to recognize commits whose
patches already exist under different commit IDs. A
[2014 Git mailing-list discussion](https://www.spinics.net/lists/git/msg234631.html)
recommends combining `for-each-ref` with merge and patch-identity primitives for
scriptable branch reporting. Those long-lived reports motivate separating
topological ancestry from patch equivalence instead of treating every
non-ancestor branch as unique work.

## Security, resources, ZDD, and rollback

The classifier invokes Git with fixed list-form arguments, accepts only a
validated symbolic target, validates every object ID and status record, applies
command timeouts, and bounds branch and commit work. It is strictly read-only:
there is no checkout, merge, delete, ref update, or push path.

Because it changes neither repository nor runtime state, deployment continuity is
preserved. Progress messages and structured counts expose bounded work and
truncation. Rollback is a normal revert of the script, target, contract, tests,
task entry, and this document; the older textual inventory and one-branch patch
comparison targets remain independently available throughout.

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
