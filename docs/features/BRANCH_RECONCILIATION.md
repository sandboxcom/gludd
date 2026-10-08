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

That exception never applies to reviewed heads whose content must enter a
candidate. Content heads follow [Reviewed Head Integration](REVIEWED_HEAD_INTEGRATION.md):
apply one source at a time for conflict/provenance attribution, then run one
bulk focused-validation phase and one exact final gate. Mixing content heads
into this ancestry-only octopus transaction is a contract violation.

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
inventory. The exhaustive summary does not trust cursor completion alone: before
emitting `terminal: true`, it re-resolves the target and performs one bounded,
explicitly sorted terminal scan. The exact ordered `(ref, head)` observations
must match the classified rows. A branch created before the active cursor,
deleted after an earlier page, or moved to a new head therefore fails closed as
changed evidence instead of producing a stale release decision.

Pagination follows Git's documented
[`for-each-ref --start-after`](https://git-scm.com/docs/git-for-each-ref.html)
lexicographic boundary. The cursor itself is excluded, so every returned ref is
strictly greater. A truncated page emits its final returned ref as `next_cursor`;
the terminal page emits `null`. Concatenating pages over a stable ref set yields
each local branch exactly once, with no duplicate or skipped boundary ref. Git's
documentation notes that refs can change between invocations. Page-by-page API
consumers must account for that limitation; the exhaustive summary instead
verifies the terminal ref/head snapshot and fails closed when its observations
became stale.

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
RECONCILE_QUIET_PROGRESS=0 RECONCILE_HEAD_SEMANTICS=0` starts at the empty cursor
and consumes each bounded page until the terminal page. It preserves every
observed branch name and canonical ref internally while grouping shared tips by
classification and commit ID. The default bounded payload omits expanded groups
and reports page count, total branches, deduplicated heads, `terminal: true`, and
`truncated: false`.
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
make branch-reconciliation-summary RECONCILE_TARGET=development \
  RECONCILE_LIMIT=100 RECONCILE_DETAILS=1 RECONCILE_CURRENT_ONLY=1 \
  RECONCILE_QUIET_PROGRESS=1 RECONCILE_HEAD_SEMANTICS=0
```

Quiet mode does not redirect stderr, catch exceptions, or rewrite exit codes.
Argument, bound, cursor, Git, and classification failures retain their structured
nonzero result. Operators should therefore keep the default for interactive
reconciliation and opt into quiet mode only at a JSON consumption boundary.

## Deterministic sequential merge queue

CLI consumers can add `--all-pages --merge-queue` to request an opt-in,
machine-readable handoff for later sequential integration. The mode first builds
the exhaustive, terminally verified summary. It then emits exactly one queue
entry for each `unique` head, in deterministic canonical-ref and full-object-ID
order. Multiple branch names at the same unique head share one entry: `source_ref`
is the first canonical ref, `refs` retains every alias, and `expected_tip` retains
the exact object ID that was classified. Ancestor and patch-equivalent heads do
not enter the merge queue; they remain fully accounted for under `collapsed`,
grouped by classification and exact head.

The payload uses schema v2 and mode `sequential-merge-queue`. Its `target`
object retains the validated input, canonical ref, and exact expected head. The
`counts` object proves that every observed branch and deduplicated head is either
queued or collapsed, while `terminal: true` and `truncated: false` distinguish a
complete handoff from a page. Queue expansion is capped at 256 unique heads in
addition to the existing 10,000-ref and 500-commit comparison bounds. Exceeding
any bound fails closed; it never emits a partial queue.

Immediately before emission, the producer re-resolves the target and performs a
new bounded, sorted local-head scan. The target must still equal
`target.head`, and every queued or collapsed canonical ref must still equal its
recorded `expected_tip`. A created, deleted, renamed, or moved ref, or any target
movement, returns structured JSON error output and a nonzero status. This second
check narrows the gap between the terminal inventory snapshot and queue handoff;
it is not a repository lock.

A sequential merge consumer starts only after verifying the recorded target and
every queued source identity. It integrates one entry at a time, records the new
target after each successful merge, and rejects any target movement not caused by
its own preceding step. It also rechecks `expected_tip` immediately before each
source is used. On interruption, conflict, or external ref movement, the consumer
stops without skipping ahead and regenerates the read-only queue from current
refs. The inventory deliberately performs none of those merges itself.

### Native Git conflict preflight

Every queued head includes a `preflight` generated from the exact recorded target
and source object IDs with `git merge-tree --write-tree --name-only --no-messages
-z`. Git's modern
[`merge-tree` contract](https://git-scm.com/docs/git-merge-tree) uses the same
three-way content, rename, directory/file, and recursive merge-base machinery as
a real merge without reading or writing the index or worktree. Gludd does not
reimplement that algorithm, inspect conflict markers, or infer cleanliness from
the path list. Exit status 0 means clean, status 1 means conflicted, and every
other status—including a host Git without modern `--write-tree` support—fails
closed without copying Git output into the diagnostic.

The entry binds `expected_target`, `expected_source`, Git's resulting tree object
ID, and a `clean` or `conflicted` status. For conflicts it records the documented
NUL-delimited conflicted-file section, never the unstable human messages. At most
10,000 conflict paths are accepted, the first 100 are displayed, displayed paths
are capped at 240 characters, and controls or overlong values are visibly
redacted. The complete observed count, truncation flag, and redaction count remain
explicit. Standard output and error are each capped at 262,144 characters and
each process retains the existing ten-second timeout. Duplicate, absolute,
traversing, unterminated, oversized, or otherwise malformed evidence fails closed.

This is a preflight against the receipt's original base target, not a prediction
of the synthetic target after preceding queue items are integrated. The explicit
target identity prevents overstatement. A sequential consumer still verifies the
next exact source and current target immediately before a real merge; after a
conflict or unrelated target movement it stops and regenerates the queue instead
of treating historical preflight evidence as current.

### Resumable reconciliation receipts

Every merge-queue payload includes a version-2 `receipt`. Its canonical SHA-256
digest covers the original target SHA, latest target checkpoint SHA, ordered
novel queue entries and their conflict preflights, collapsed ancestor and
patch-equivalent groups, and the zero-based integration cursor. Queue entries
retain all alias refs and exact tips, while collapsed groups preserve the branches
intentionally excluded from integration. The receipt therefore carries the
complete no-branch-left-behind accounting across an interrupted sequential run.

The CLI flag `--replay-receipt` reads exactly one JSON receipt from bounded
standard input. It remains compatible with the existing required target, limit,
and empty-cursor arguments: the target must equal the receipt target, the limit
must remain within 1 through 100, and other inventory modes are rejected. Input
is capped at 16,777,216 characters. Invalid JSON, schema drift, noncanonical
ordering, bound violations, and digest mismatch all return the same content-free
`invalid reconciliation receipt` error; received JSON, refs, and object IDs are
never copied into diagnostics.

Version 1 receipts predate conflict evidence and are deliberately rejected rather
than replayed with an unproven field. Regenerate them through the read-only queue
command. The outer queue remains schema v2 and the new entry member is additive
for JSON consumers that already ignore unknown keys.

Replay re-resolves the symbolic target, compares the complete bounded local-ref
snapshot with every queued and collapsed `(ref, expected_tip)` pair, and checks
target ancestry from the recorded checkpoint. Target movement is accepted only
when it makes an ordered prefix beginning at the receipt cursor reachable. A
later head becoming reachable before the next head fails as out of order, and a
target move with no newly integrated queue head also fails closed. A successful
result marks the complete integrated prefix, separately identifies heads newly
recognized by this replay, emits one `next` item or `null`, and returns a renewed
receipt with the updated cursor and checkpoint. It never performs the merge.

SHA-256 makes accidental or unreviewed receipt mutation evident; it is not a
secret-key signature. Automation must retain the receipt as a trusted release
artifact and must not accept a digest recomputed by an untrusted party. On a
movement or integrity failure, operators discard the stale handoff and generate
a new exhaustive queue after repository activity settles.

## Opt-in semantic head summaries

The authoritative S83.125 implementation landed in commit
`b9a9c7ea2d5714c7c076f83f020fa942e7a07abb` (`feat: add bounded reconciliation
head semantics`). Later queue and receipt work layers on that source commit; a
reconciliation should preserve its ancestry instead of recreating the feature or
applying a patch-equivalent duplicate.

Semantic review is explicit and terminal. Set `RECONCILE_DETAILS=1` and
`RECONCILE_HEAD_SEMANTICS=1` on `branch-reconciliation-summary` to add a
`head_summaries` array keyed by the already deduplicated full commit ID. The CLI
equivalent is `--all-pages --head-semantics`; page and counts-only modes reject
the flag. Each entry reports the head commit subject, the first-parent changed
paths for that commit, the complete bounded path count, and explicit subject/list
truncation and path-redaction signals. Current-only mode enriches only its emitted
unique heads, making it the narrowest release-review form.

Semantic review and sequential integration are intentionally separate snapshots.
`--head-semantics` cannot be combined with `--merge-queue` or `--replay-receipt`.
Operators inspect one stable exhaustive snapshot, then generate a fresh verified
queue and receipt from the stable refs they intend to integrate. This keeps
subjects and paths out of the resumable receipt while its exact target, source
tips, collapsed equivalents, ordering, and cursor remain content-addressed.

The default remains schema v2 with exactly the prior keys and Git command set:
when the flag is absent there is no `head_summaries` member and no semantic Git
inspection. Opt-in evidence uses mature `git show --no-patch --format=%s` and
NUL-delimited `git diff-tree --name-only -z --first-parent` formats. The latter
avoids newline/path quoting ambiguity and makes merge-head comparison explicit.
Only validated full object IDs are passed as revisions.

Semantic inspection covers every deduplicated head in the already bounded
snapshot, sharing its 10,000-local-ref ceiling. Each head is limited to 100
returned paths, 200 subject characters, 240 characters per displayed path,
262,144 output characters per Git invocation, and the existing ten-second command
timeout. A repository above the shared head/ref ceiling or an oversized/malformed
Git record fails closed before claiming complete evidence. A longer path list
remains useful but explicit:
`changed_path_count` retains the observed total while `changed_paths_truncated`
marks the first-100 display. Paths stay repository-relative; absolute paths,
empty/traversal components, and malformed NUL framing are rejected. Control
characters and overlong displayed paths are replaced or suffix-truncated, with
`path_redactions` recording how many returned names changed. No checkout path,
home directory, worktree root, commit body, patch content, or untracked helper is
exposed.

The exhaustive path retains the 10,000-ref ceiling, rejects duplicate refs or a
non-advancing cursor, and revalidates the target identity on every page and at
the terminal boundary. A target change, a terminal local-ref/head mismatch, or
conflicting evidence for one shared head fails closed. The final verification is
not a repository lock and does not mutate refs; it proves that every observation
still matches the terminal state. If concurrent work changes a ref, the operator
restarts the read-only command after that work settles.

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

The GitLab CLI practitioner request
[#1089](https://gitlab.com/gitlab-org/cli/-/issues/1089), opened in 2022, calls
out pagination oddities when the underlying collection changes during an
operation. Cursor ordering prevents boundary duplication, but it cannot reveal a
new ref inserted before an already-consumed cursor or a previously observed ref
that moved. That long-lived operational concern is why the exhaustive path
compares its ordered observations with a final ref/head snapshot before making a
terminal claim. The Git project's 2024 performance report
[#401](https://gitlab.com/gitlab-org/git/-/issues/401) demonstrates that sorted
`for-each-ref` work scales with repository ref count even when `--count` is
small. Gludd consequently permits exactly one additional sorted verification
scan, retains the 10,000-ref ceiling and ten-second timeout, and never retries in
an unbounded loop.

GitLab maintainers also recorded the operational risk in
[#62793](https://gitlab.com/gitlab-org/gitlab-foss/-/work_items/62793): a merge
request can have passed tests against one target SHA while the target branch has
moved, so that evidence does not establish success against its current head.
That practitioner report motivates preserving both sides of each reconciliation
decision as exact object identities. The queue therefore records the target head
and every source tip, rechecks them before emission, and gives a later sequential
consumer enough evidence to reject stale work rather than merging by a mutable
branch name alone.

The GitLab practitioner report
[#229156](https://gitlab.com/gitlab-org/gitlab/-/issues/229156) documents a
merge-train interruption that lost the prior successful state and forced a
source-branch change before work could be queued again. Its expected behavior is
to remember success for the exact current source revision so the train can be
resumed. Gludd's receipt applies that durable lesson locally: it preserves the
exact source tips and completed cursor, but reuses them only after target and
source identities are proven unchanged or advanced by the next ordered merge.

GitLab's long-lived conflict-service report
[#28424](https://gitlab.com/gitlab-org/gitlab/-/issues/28424), opened in 2019,
records `ListConflictFiles` exceeding a 55-second deadline for a moderately sized
repository with an older merge request, more than 200 changed files, and a very
large diff. A later GitLab report
[#383730](https://gitlab.com/gitlab-org/gitlab/-/issues/383730) records conflict
inspection for a roughly 2,000-commit merge request consuming enough memory to be
OOM-killed. Those practitioner failures support per-head time and output limits,
bounded displayed paths, serial preflights, and fail-closed behavior instead of
an unbounded conflict service.

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

Because it changes neither refs, the index, the worktree, nor runtime service
state, deployment continuity is preserved. Default progress messages and
structured counts expose bounded work and truncation. Quiet mode removes only
deterministic narration and does not change the Git command set, scan bounds,
result schema, or resource namespace.
Semantic mode is also read-only and foreground-only: at most two fixed list-form
Git inspections run per bounded head, with progress on stderr and no files, locks,
daemons, services, ports, background workers, ref changes, or deploy interruption.
Queue generation and receipt replay have the same foreground-only boundary: they
verify identities and emit JSON but never merge, delete, rewrite, or push a ref.
Native preflight does not read or write the index or worktree and never creates a
commit or ref. Git may materialize the documented result tree as an unreachable
object; no reference exposes it, and normal Git object maintenance owns its
lifecycle. No custom cleanup runs during deployment or rollback.
An exhaustive run adds one terminal target resolution and one explicitly sorted
local-head scan, emits `verify=terminal-ref-snapshot` progress by default, and
shares the existing 10,000-ref, output, and timeout bounds. This preserves ZDD
while keeping CPU, memory, subprocess, and JSON growth bounded. During rollout,
old and new callers can run concurrently because the schema and Make interface do
not change; only stale terminal evidence becomes a structured nonzero failure.

Rollback is layered and requires no coordinated downtime. Stop requesting the
opt-in semantic, queue, or replay flags first; default schema-v2 inventory callers
remain unchanged. If code rollback is required, revert receipt and queue support
before reverting the authoritative semantic source commit, so no surviving mode
depends on removed types or validation paths. A version-2 receipt can be discarded
and regenerated after rollback; it is never applied to repository state. The older
textual inventory and one-branch patch comparison targets remain independently
available throughout. No service restart, data migration, ref repair, or downtime
is needed.

Merge-queue mode adds only foreground, read-only Git inspection and bounded JSON
serialization. It creates no checkout, index, lock, merge, commit, ref update,
push, daemon, port, or service transition; only Git's unreferenced result-tree
objects described above may be materialized. Generation and rollback therefore
have no runtime outage window. Existing callers remain unchanged because the flag
is opt-in. Rollback is a normal revert of the queue builder, its focused tests,
and this documentation; previously emitted payloads can simply be discarded and
regenerated with the older exhaustive summary. There is no repository repair,
data migration, service restart, or destructive cleanup step.

Receipt replay has the same ZDD boundary. It reads bounded stdin, resolves refs,
performs one bounded sorted scan and at most 256 ancestry probes, and writes one
JSON result; it creates no file, lock, checkout, index entry, commit, merge, ref
update, deletion, push, worker, daemon, port, or service transition. Rollback is
a normal revert of receipt creation/replay, its focused tests, and these sections.
Schema-v2 queue consumers can ignore additive members, while version-1 receipts
must be regenerated rather than replayed. That requires no repository or runtime
cleanup.

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
