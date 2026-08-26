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
`RECONCILE_TARGET=<ref>`, `RECONCILE_LIMIT=<n>`, and `RECONCILE_AFTER=<cursor>`
values. An explicitly empty cursor requests the first page; subsequent cursors
are canonical `refs/heads/...` identities copied from `next_cursor`. The target
emits one schema-v2 JSON document on standard output and progress on standard
error, keeping the result machine-readable without hiding operator-visible work.

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

Pagination follows Git's documented
[`for-each-ref --start-after`](https://git-scm.com/docs/git-for-each-ref.html)
lexicographic boundary. The cursor itself is excluded, so every returned ref is
strictly greater. A truncated page emits its final returned ref as `next_cursor`;
the terminal page emits `null`. Concatenating pages over a stable ref set yields
each local branch exactly once, with no duplicate or skipped boundary ref. Git's
documentation notes that refs can change between invocations, so callers that
permit concurrent ref mutation must restart their inventory rather than claim a
cross-mutation snapshot.

On older Git releases that reject `--start-after`, the same contract falls back
to `for-each-ref --sort=refname` over local heads with a hard 10,000-ref scan
ceiling. A repository above that ceiling fails closed instead of silently
skipping a late cursor. The fallback therefore preserves deterministic paging on
the host's mature Git feature set while keeping traversal bounded.

The 2026-08-26 beta4 reconciliation found a second backend boundary after more
than 400 local refs: the page ending at `refs/heads/fix/dogfood...` succeeded,
but the next `--start-after` response included a lexicographically earlier
hyphenated ref. The inventory now treats any backwards result as an unreliable
cursor implementation and reuses the same bounded, explicitly sorted local-head
scan. It still rejects malformed or unordered evidence and still refuses a
repository above the 10,000-ref ceiling; no unbounded compatibility retry exists.

## Exhaustive deduplicated summary

`make branch-reconciliation-summary RECONCILE_TARGET=development
RECONCILE_LIMIT=100 RECONCILE_DETAILS=0 RECONCILE_CURRENT_ONLY=0
RECONCILE_QUIET_PROGRESS=0` starts at the empty cursor and consumes each bounded
page until the terminal page. It preserves every observed branch name and
canonical ref internally while grouping shared tips by classification and commit
ID. The default bounded payload omits expanded groups and reports page count,
total branches, deduplicated heads, `terminal: true`, and `truncated: false`.
Setting `RECONCILE_DETAILS=1` exposes the grouped refs when a focused
reconciliation needs them. Adding `RECONCILE_CURRENT_ONLY=1` then limits those
groups to unique current heads and reports explicit selected-head and
selected-branch totals while retaining the complete scan counts. The Make
contract rejects current-only count mode, so a release review cannot silently
request detail and discard it. All modes remain tracked Make workflows, so
release work never depends on an external helper script.

Progress remains observable by default. The CLI writes a bounded start, page,
and per-ref marker to standard error while keeping the terminal JSON document on
standard output. A machine consumer that captures both streams can explicitly
set `RECONCILE_QUIET_PROGRESS=1`; the Make target passes the tracked
`--quiet-progress` CLI flag and suppresses only those progress markers. For
example, the following invocation emits one current-only JSON document with an
empty successful stderr stream:

```console
make branch-reconciliation-summary RECONCILE_TARGET=development RECONCILE_LIMIT=100 RECONCILE_DETAILS=1 RECONCILE_CURRENT_ONLY=1 RECONCILE_QUIET_PROGRESS=1
```

Quiet mode does not redirect stderr, catch exceptions, or rewrite exit codes.
Argument, bound, cursor, Git, and classification failures retain their structured
nonzero result. Operators should therefore keep the default for interactive
reconciliation and opt into quiet mode only at a JSON consumption boundary.

## Opt-in semantic head summaries

Semantic review is explicit and terminal. Set `RECONCILE_DETAILS=1` and
`RECONCILE_HEAD_SEMANTICS=1` on `branch-reconciliation-summary` to add a
`head_summaries` array keyed by the already deduplicated full commit ID. The CLI
equivalent is `--all-pages --head-semantics`; page and counts-only modes reject
the flag. Each entry reports the head commit subject, the first-parent changed
paths for that commit, the complete bounded path count, and explicit subject/list
truncation and path-redaction signals. Current-only mode enriches only its emitted
unique heads, making it the narrowest release-review form.

The default remains schema v2 with exactly the prior keys and Git command set:
when the flag is absent there is no `head_summaries` member and no semantic Git
inspection. Opt-in evidence uses mature `git show --no-patch --format=%s` and
NUL-delimited `git diff-tree --name-only -z --first-parent` formats. The latter
avoids newline/path quoting ambiguity and makes merge-head comparison explicit.
Only validated full object IDs are passed as revisions.

Semantic inspection is bounded to 256 deduplicated heads, 100 returned paths per
head, 200 subject characters, 240 characters per displayed path, 262,144 output
characters per Git invocation, and the existing ten-second command timeout. More
than 256 selected heads or an oversized/malformed Git record fails closed before
claiming complete evidence. A longer path list remains useful but explicit:
`changed_path_count` retains the observed total while `changed_paths_truncated`
marks the first-100 display. Paths stay repository-relative; absolute paths,
empty/traversal components, and malformed NUL framing are rejected. Control
characters and overlong displayed paths are replaced or suffix-truncated, with
`path_redactions` recording how many returned names changed. No checkout path,
home directory, worktree root, commit body, patch content, or untracked helper is
exposed.

The exhaustive path retains the 10,000-ref ceiling, rejects duplicate refs or a
non-advancing cursor, and revalidates the target identity on every page. A target
change or conflicting evidence for one shared head fails closed. As with Git's
cursor primitive, the guarantee applies to a stable local ref set; concurrent ref
creation requires restarting the command rather than treating its output as an
atomic repository snapshot.

Target resolution happens before enumeration and fails closed. Empty,
whitespace-containing, option-shaped, invalid, and non-symbolic refs produce a
structured JSON error and a nonzero exit. Malformed or failed Git evidence does
the same. Nonempty cursors must be canonical local refs and pass Git's mature
`check-ref-format` validation before enumeration; deleted but well-formed cursor
refs remain valid lexicographic boundaries.

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

The official
[`git-for-each-ref` manual](https://git-scm.com/docs/git-for-each-ref/2.52.0.html),
reviewed 2026-08-26, defines `--start-after` as a lexicographic boundary and also
states that it cannot be combined with sorting or a ref pattern. GitLab's
long-lived practitioner request
[#584](https://gitlab.com/gitlab-org/git/-/issues/584), reviewed 2026-08-26,
requests pattern support precisely because callers otherwise receive every ref
namespace. Those constraints justify validating every returned cursor page and
falling back to one bounded `refs/heads` sort when the backend violates its own
ordering contract, instead of accepting incomplete release evidence.

The still-open GitHub CLI practitioner request
[#8536](https://github.com/cli/cli/issues/8536), opened in January 2024, records
that a long-running exhaustive operation without meaningful progress is hard to
distinguish from a stall. That durable report supports retaining progress as the
human-facing default. For the explicit machine-facing exception, the mature
[git-sizer CLI](https://github.com/github/git-sizer) provides the directly
analogous design: JSON results on stdout, progress on stderr, and a
`--no-progress` override. Gludd keeps the same separation while naming its flag
`--quiet-progress` so the suppressed class is unambiguous and real errors remain
outside the suppression boundary.

The long-lived GitHub CLI issue
[#6642](https://github.com/cli/cli/issues/6642), opened in 2022, records that
remote file-list queries can be expensive and points practitioners to local
`git log` when commit messages and touched paths are the evidence they need. The
2023 GitHub CLI issue
[#7815](https://github.com/cli/cli/issues/7815) records generated release notes
failing after unbounded commit text exceeded a 125,000-character service limit.
Together with the older Git mailing-list reports above, these practitioner cases
support local mature Git formats, an opt-in surface, explicit truncation, and hard
output bounds instead of an always-on or remotely expanded payload.

## Security, resources, ZDD, and rollback

The classifier invokes Git with fixed list-form arguments, accepts only a
validated symbolic target and canonical local-ref cursor, validates every object
ID and status record, applies command timeouts, and bounds branch, page, and
commit work. Cursor pages use Git's default refname ordering because
`--start-after` deliberately cannot be combined with a custom sort or ref
pattern; parsing stops safely at the next ref namespace. It is strictly
read-only: there is no checkout, merge, delete, ref update, or push path.

Because it changes neither repository nor runtime state, deployment continuity
is preserved. Default progress messages and structured counts expose bounded
work and truncation. Quiet mode removes only deterministic narration and does not
change the Git command set, scan bounds, result schema, or resource namespace.
Semantic mode is also read-only and foreground-only: at most two fixed list-form
Git inspections run per bounded head, with progress on stderr and no files, locks,
daemons, services, ports, background workers, ref changes, or deploy interruption.
This preserves ZDD while keeping CPU, memory, subprocess, and JSON growth bounded.
Rollback is a normal revert of the CLI flag, Make variable, contract, tests, task
entry, and this document; because the default schema never changed, callers need
no coordinated migration. The older textual inventory and one-branch patch
comparison targets remain independently available throughout. No service restart,
data migration, cleanup, or downtime is needed.

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
