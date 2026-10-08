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

Each individual page closes the smaller classification race as well. After
classification and before JSON emission, one mature `git show-ref --verify --`
invocation re-reads the resolved target plus every returned local branch by
exact canonical ref. Its full `(ref, head)` mapping must equal the observations
used for classification. A missing ref, moved head, command failure, duplicate
row, unexpected ref, or malformed object ID fails closed; no partly stale page
is emitted. The operation is bounded to the target plus at most 100 returned
branches and adds one foreground subprocess per page. It is a verification
point rather than a repository lock: callers still restart after later ref
changes, while the exhaustive mode retains its final whole-inventory scan.

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

### Bounded current-head merge-queue planning

The inventory CLI also accepts the separate opt-in combination `--all-pages
--merge-queue-plan`. This mode consumes the same exhaustive reachability and
patch-equivalence classification, then plans only deduplicated `unique` / `current`
heads. Ancestor and patch-equivalent heads remain visible in the observed counts
but cannot consume a planning slot. The existing page, exhaustive-summary,
sequential-queue, and receipt schemas are unchanged when the flag is absent.

For each exact candidate object ID, the planner asks Git for the NUL-delimited
three-dot branch delta (`target...head`). Paths are therefore selected from the
merge base to the candidate, not merely from its tip commit. Candidates are
sorted by canonical ref and assigned by deterministic first fit to the earliest
group in which every pair is disjoint. An exact changed-path overlap creates a
`changed-path` collision. In addition, any two candidates that both touch the
single-writer surface create a `shared-infrastructure` collision even when the
individual files differ. That surface is intentionally narrow and reviewable:
`Makefile`, `opencode.json`, `AGENTS.md`, `.claude/settings.json`, direct
`config/*.yml` files, and direct `.github/workflows/*.yml` files.

The JSON result exposes exact tips and alias refs in `candidates`, deterministic
`groups`, typed pairwise `collisions`, complete pair counts, and explicit display
truncation. Collision-free means path-disjoint under this policy; it is a safe
review or validation scheduling hint, not proof of semantic compatibility and
not permission to merge out of order. A consumer still creates and tests the
actual cumulative merge result before integration.

Planning is capped at 256 heads and 10,000 paths per head. It displays at most
100 paths per candidate, 1,000 collision records, and 20 paths per collision;
each Git stream is capped at 262,144 characters and the complete JSON document at
1,048,576 characters. Candidate path or Git-output overflow fails closed because
unknown overlap cannot be called safe. Collision display overflow is explicit:
grouping still considers every bounded pair while `collision_pairs` retains the
complete count and `collisions_truncated` becomes true. Immediately before
emission, the target and complete local-ref snapshot are revalidated just as they
are for the sequential queue.

The planner never checks out or updates a ref, index, or worktree. Its inspection
operations are fixed-argument `git diff --name-only --no-ext-diff --no-renames
--no-textconv -z` and bounded `git merge-tree` probes over validated full object
IDs. `merge-tree --write-tree` may create an unreachable result-tree object, but
it installs no name under `refs/` and touches neither the index nor the worktree.
Planning is therefore ref/worktree read-only and preserves zero-downtime behavior.

#### Per-group merge rehearsal and admission

Each collision-free group now carries an additive `rehearsal` member. A canonical
SHA-256 digest of the exact target identity, group order, source tips, and alias
refs names a `merge-rehearsal-sha256:*` candidate without creating a Git ref.
The preflight repeats the exact `rev-parse --verify --quiet --end-of-options`
argv and expected object ID for the target and every source alias. It also emits
the fixed three-dot `git diff` argv, full path count, and SHA-256 of each sorted
NUL-delimited path manifest. A consumer must reproduce those counts and digests;
mutable branch names or a merely successful command are not equivalent evidence.
Every group records the number of checked pairs and requires
`shared_path_conflict_count: 0` under the same changed-path and
shared-infrastructure policy used to form the group.

The temporary-candidate recipe is an ordered chain of captured argv templates.
For each source, `git merge-tree --write-tree --name-only --no-messages -z`
computes a tree against the prior captured candidate, and
[`git commit-tree`](https://git-scm.com/docs/git-commit-tree) gives that tree the
prior candidate and exact source as parents. Fixed author/committer identity,
the documented raw `@0 +0000` timestamps, and a digest-bound message make the
object recipe repeatable. `${result_tree_N}` means the validated first NUL record
from a clean merge-tree result; `${candidate_commit_N}` means the validated
commit-tree object ID. These tokens are structured capture references, never
shell text to evaluate. The inventory only emits this cumulative recipe: it does
not execute those recipe steps, `commit-tree`, `merge`, `update-ref`, or
`worktree`. Its separate pairwise `merge-tree` prediction is described below.

Rehearsal expansion fails closed above 256 groups, 256 heads in a group, 10,000
alias branches in a group, or 10,000 distinct changed paths in a group. At most
100 shared-infrastructure paths are displayed, with truncation and redaction
explicit, and the existing 1,048,576-character JSON ceiling still applies to the
complete plan. These independent bounds prevent an otherwise valid inventory
from expanding into an unbounded command document.

`admission.commands` contains only checks, in order: resolve the isolated
candidate's `HEAD`, prove the target and every source are ancestors of the
captured final candidate, and run `make gate` on that exact candidate. The
matching `evidence_requirements` prescribe the exact admission sequence: retain
the terminal snapshot; reproduce every ref object and path-manifest digest;
require a `predicted-clean` native result and zero shared-path conflicts; execute
and record every clean tree/commit capture in recipe order; prove candidate
identity and complete ancestry; retain the exact-candidate gate result; then let
an authorized single writer recheck every ref immediately before integration.
The list deliberately contains no merge, push, ref-update, or worktree-creation
command. Any changed ref, manifest, prediction, tree status, or candidate identity
invalidates the whole rehearsal; regenerate it instead of repairing or partially
admitting it.

##### Bounded Git-native group prediction

Before emission, each group runs the mature
[`git merge-tree`](https://git-scm.com/docs/git-merge-tree) plumbing command as
`git merge-tree --write-tree --name-only --no-messages -z LEFT RIGHT`. Inputs are
immutable object IDs. The deterministic matrix covers the target against every
source and every source/source pair; an injected runner keeps unit tests hermetic.
Compatibility is capability-based: a host must support that modern `--write-tree`
form and its documented NUL protocol. Unsupported syntax or any return code other
than clean `0` and conflicted `1` produces `status: blocked` with
`blocked_reason: unsupported-git`.

The predictor reports only `predicted-clean`, `conflicted`, or `blocked`; it never
emits a `mergeable` claim. Pairwise cleanliness is not proof that the ordered,
cumulative synthetic candidate will merge cleanly. Admission still requires the
emitted recipe to succeed step by step and the exact candidate to pass the gate.
Conflicted checks retain bounded path evidence, while any unsupported command,
timeout, nonempty stderr, malformed or ambiguous NUL output, invalid tree object,
unsafe or duplicate path, or bound overflow blocks the entire prediction and
discards partial check details.

At most 64 pair checks run serially. A group requiring more is blocked before the
first probe. Each probe has a ten-second timeout; stdout and stderr are accepted
only up to 262,144 characters each; at most 10,000 conflict paths are scanned and
100 displayed. The payload records those ceilings and the derived 640-second
worst-case command-runtime budget. Git stderr is never copied into JSON. If the
terminal target/local-ref verification cannot reproduce the original snapshot,
the top-level plan becomes nonterminal and unsuccessful, every group's partial
checks are erased, and each prediction becomes `blocked: freshness-drift`.

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

### Digest-bound no-branch-left-behind snapshots

Every terminal merge-queue plan now includes a `snapshot_basis`. Unlike the
candidate list, this basis retains every branch in the original exhaustive
inventory, including ancestor and patch-equivalent branches that needed no new
integration. Each ref is sorted and bound to its exact tip, initial
classification/lifecycle, and one of `planned-ready`, `planned-blocked`, or
`already-satisfied`. A canonical SHA-256 digest covers the complete basis and
target identity. This additive manifest prevents a later status pass from
silently forgetting an alias or an already-satisfied branch.

`--reconciliation-snapshot` reads one bounded JSON object from standard input and
does not invoke Git. The object must have exactly `plan`, `plan_digest`,
`fresh_inventory`, `freshness_digest`, and `retired`. `plan` is the earlier
terminal merge-queue plan; `plan_digest` must equal its sealed basis digest.
`fresh_inventory` is a new terminal, untruncated `exhaustive-summary`, while
`freshness_digest` is SHA-256 over its canonical JSON (`sort_keys`, compact
separators, ASCII escaping, and no nonfinite numbers). The CLI retains its normal
required target, bounded limit, and empty cursor; every other inventory mode is
mutually exclusive. A plan emitted before `snapshot_basis` cannot prove complete
accounting and is rejected; regenerate it with the read-only planner.

The result contains separately sorted `branches` and deduplicated `heads` arrays.
Head status uses the most conservative status among its aliases.

| Status | Exact meaning |
|---|---|
| `pending` | The same ref/tip remains unique/current and its planned group had a clean native prediction. |
| `merged` | The same tip is now ancestor or patch-equivalent to the fresh target. |
| `stale` | The planned ref still exists but points at another object ID. |
| `blocked` | The exact tip remains current without clean plan admission, or prior satisfied evidence unexpectedly regressed. |
| `explicitly-retired` | The ref is absent and its exact planned ref/tip has fresh equivalence evidence or a verified operator approval. |

An absent ref without retirement, an unknown fresh ref, an unknown or duplicate
retirement or approval, a stale retirement head, a duplicate plan/fresh ref,
inconsistent counts, target-identity drift, schema drift, or any digest mismatch
returns only `invalid reconciliation snapshot`.

Every retirement has exactly `approval`, `expected_tip`, and `ref`. An `approval`
of `null` is accepted only when another ref already sealed in the plan remains in
the digest-verified fresh inventory at the exact expected tip and is now
`ancestor` or `patch-equivalent`. The output records `fresh-ancestor` or
`fresh-patch-equivalent` as `retirement_basis`; path disjointness, a missing ref,
or an unverified caller assertion is never equivalence evidence.

Otherwise `approval` must be an exact Ed25519-signed envelope:

```json
{
  "algorithm": "ed25519",
  "body": {
    "expires_at": "2026-10-08T23:00:00Z",
    "expected_tip": "<full object ID>",
    "issued_at": "2026-10-07T23:00:00Z",
    "key_id": "release-reviewer-2026",
    "plan_digest": "<lowercase SHA-256>",
    "reason_code": "superseded",
    "ref": "refs/heads/<planned branch>",
    "reviewer_identity_digest": "<lowercase SHA-256>",
    "version": 2
  },
  "signature": "<lowercase 64-byte Ed25519 signature as 128 hex characters>"
}
```

Sign the domain-separated UTF-8 message
`gludd.reconciliation-retirement-approval/v1\0` followed by the canonical body
JSON. The implementation reuses
`general_ludd.self_update.signing.verify_signature`, backed by the project's
locked `cryptography` Ed25519 dependency; this feature does not implement a new
signature scheme. Invoke the snapshot with both
`--retirement-keyring <read-only-json>` and
`--retirement-verification-time <canonical-rfc3339-utc>`. The keyring is exact,
versioned, and sorted:

```json
{
  "keys": [{
    "key_id": "release-reviewer-2026",
    "public_key": "<lowercase 32-byte Ed25519 public key as 64 hex characters>",
    "reviewer_identity_digest": "<lowercase SHA-256>",
    "status": "active"
  }],
  "schema_version": 1
}
```

The selected key must be known, active, and bound to the same reviewer digest as
the signed body. The plan digest, ref, and head must match the sealed plan and
outer retirement exactly. Both times are real, second-precision canonical RFC
3339 UTC values and must satisfy `issued_at <= verification_time < expires_at`;
the issue time must precede expiry. Reason text and extra keys are rejected: only
`abandoned`, `out-of-scope`, or `superseded` is accepted, avoiding conversion of
an operator note into policy. Tampering, an unknown or revoked signer, expiry,
cross-plan/head replay, duplicate signed envelopes, and verifier errors all fail
closed. A ref that still exists—even at a different head—cannot be retired.

Successful output uses `operator-approval` as its basis and exposes the approval
digest, key ID, issue/expiry times, and fixed reason code. It never repeats the
reviewer identity digest. The top-level result records the exact keyring digest
and verification time, and `snapshot_digest` binds them with every branch and
count. Signature-free ancestry/patch-equivalence retirement does not need a
keyring; supplying unused trust/time evidence is rejected to keep the handoff
unambiguous.

The output repeats both input digests, reports complete per-status counts, and
seals target, branch, head, retirement basis, approval evidence, trust root, and
count evidence into `snapshot_digest`. `complete` is true only when every branch
is merged or explicitly retired. SHA-256 still supplies document integrity;
Ed25519 authenticates the bounded operator assertion. The reviewer digest is
pseudonymous rather than anonymous and may be guessable from a small identity
set. Keep identity mappings and private keys in the access-controlled approval
workflow; this reader accepts only public keys and never logs or outputs the
reviewer digest or key material.

Input, output, plan entries, fresh entries, retirements, and approvals are capped:
at most 10,000 refs/heads or retirement records and 16,777,216 canonical JSON
characters are accepted. At most 256 signed approvals and 128 keyring entries are
verified; the keyring read is capped at 65,536 characters, and signatures have an
exact 128-hex encoding. Approval fields have fixed schemas and digest, object ID,
ref, reason, signer, and timestamp validators. The operation is a deterministic
in-memory comparison with no Git subprocess, ref, index, worktree, object, daemon,
port, or service mutation. On any rejected or stale result, regenerate a fresh
exhaustive inventory or whole plan and obtain a new exact approval if needed; do
not edit or re-sign partial evidence. Rollback discards snapshots and approval
handoffs and reverts the additive basis/flags, leaving pagination, summaries,
queues, receipts, refs, worktrees, and deployments unchanged.

Unsigned version-1 SHA-256 approval envelopes are intentionally rejected. To
migrate, regenerate the plan/fresh inventory, issue a version-2 signature for the
exact current plan/ref/head and bounded validity window, and provide the matching
active public-key record. Existing fresh ancestry or patch-equivalence retirement
remains signature-free and needs no migration. This makes rollout additive and
ZDD-safe: deploy readers and trust configuration before emitting signed approvals;
rollback stops emitting the optional snapshot without touching repository state.

This design applies two long-lived practitioner lessons already recorded below.
GitLab [#229156](https://gitlab.com/gitlab-org/gitlab/-/issues/229156) documents
an interrupted merge train losing prior success state, while GitLab CLI
[#1089](https://gitlab.com/gitlab-org/cli/-/issues/1089) documents collection
changes producing pagination surprises. Exact retained identities plus explicit
verified retirement distinguish deliberate removal from either lost state or an
unstable inventory instead of treating absence as success. GitHub practitioners
also report in [#120203](https://github.com/orgs/community/discussions/120203)
that a queue-generated head can obscure the original source branch. Binding a
signature to the original ref, exact tip, plan digest, signer, and expiry prevents
disappearance, substitution, or stale approval replay from being accepted as
operator intent; current keyring revocation provides an explicit fail-closed stop.

## Fresh remote-tracking reconciliation

`--remote-tracking` extends the local inventory to one explicitly selected,
locally available `refs/remotes/<remote>/` namespace. It requires
`--remote-name` and `--remote-verification-time`, consumes one bounded freshness
envelope from standard input, and performs no network operation. The fetch
coordinator must capture the exact sorted remote-tracking refs immediately after
its successful fetch and retain this envelope as a trusted handoff:

```json
{
  "algorithm": "sha256",
  "body": {
    "expires_at": "2026-10-08T01:00:00Z",
    "fetched_at": "2026-10-08T00:00:00Z",
    "refs": [{
      "head": "<full object ID>",
      "ref": "refs/remotes/origin/feature/example"
    }],
    "remote": "origin",
    "version": 1
  },
  "digest": "<SHA-256 of the canonical body>"
}
```

The body digest, selected remote, and complete ref/tip set must match the current
local remote-tracking namespace exactly. Times are canonical second-precision UTC
and must satisfy `fetched_at <= verification_time < expires_at`; mismatched,
future, or expired evidence fails closed. SHA-256 makes handoff drift evident but
does not authenticate who fetched it. Keep the envelope in the trusted fetch
workflow, and use a validity window shorter than the repository's acceptable
remote-staleness budget. A locally current remote-tracking ref still is not proof
of current server state; this tool intentionally never claims otherwise.

The implementation uses only two sorted, NUL-field-delimited
`git for-each-ref` scans: one for `refs/heads/` and one for the exact remote
namespace. It rejects unsafe remote names, malformed or duplicate rows, unsafe
branch components, mismatched namespaces, configured-upstream ambiguity, and all
symbolic refs (including `<remote>/HEAD`). Each branch-name identity receives one
deterministic state and advisory recommendation:

| State | Evidence | Recommendation |
|---|---|---|
| `local-only` | A local ref exists, no same-name remote ref exists, and the local ref is not configured to track that missing selected-remote ref. | `retain` |
| `remote-only` | A remote-tracking ref exists with no same-name local ref. | `review` |
| `equal` | Local and remote-tracking refs exist at the same exact object ID. | `retain` |
| `diverged` | Both refs exist at different tips; this includes ahead/behind cases because the mode makes no graph-integration claim. | `review` |
| `deleted-upstream` | The local ref's exact configured upstream is the selected same-name remote ref, and that ref is absent from fresh evidence. | `delete-candidate` |

`delete-candidate` is evidence for human review, never deletion authority. The
mode emits only `retain`, `review`, or `delete-candidate`; it never fetches,
pushes, prunes, updates, or deletes a ref and never touches an index or worktree.
Its Git runner retains the global ten-second timeout. Local and remote scans are
independently capped at 10,000 refs and 2,097,152 characters; the canonical
freshness/output document is capped at 4,194,304 characters and bounded stdin at
16,777,216. The selected remote is at most 64 conservative ASCII characters.

Branch names and object IDs can reveal private work, so output belongs in the
same access-controlled release evidence store as the local inventory. Remote URLs,
credentials, reflogs, commit messages, and file paths are never read or emitted.
Rollout is opt-in and ZDD-safe: deploy the reader, then have the existing fetch
workflow start producing freshness envelopes. Rollback stops passing the three
remote flags and discards the advisory output; no repository or service state
needs restoration.

This boundary reflects a long-lived practitioner report on Stack Overflow
[#7726949](https://stackoverflow.com/questions/7726949/remove-tracking-branches-no-longer-on-remote): remote-tracking refs can linger
after upstream deletion until an explicit prune-capable fetch. It also retains
the GitHub Community [#120203](https://github.com/orgs/community/discussions/120203)
lesson that generated integration refs can obscure the original source branch.
Fresh exact identities plus a non-mutating recommendation distinguish a genuinely
remote-only branch from a stale local observation without turning absence into an
automatic deletion.

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

The inventory never changes a running service, ref, index, or worktree, so it
cannot cause deployment downtime. Native prediction may leave unreachable Git
tree objects for normal object maintenance. Its complete stdout is the observable
handoff: a branch is either named as a reconciliation candidate or the target
explicitly reports that every local branch is already reachable from development.

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
that moved. That long-lived operational concern is why every page verifies its
classified refs with Git's exact, read-only
[`show-ref --verify`](https://git-scm.com/docs/git-show-ref) primitive and why the
exhaustive path additionally compares all ordered observations with a final
ref/head snapshot before making a terminal claim. The Git project's 2024 performance report
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

The long-running GitHub merge-queue feedback thread
[#46757](https://github.com/orgs/community/discussions/46757), opened in 2023,
contains a practitioner report that nominally unrelated dependency updates still
overlap shared `go.mod` and `go.sum` files, ejecting later work from the queue.
That durable example motivates both exact path-overlap evidence and the stricter
single-writer treatment for repository-wide configuration surfaces; a label such
as "unrelated" is not sufficient evidence for co-scheduling.

GitLab's long-lived parallel merge-train proposal
[#11222](https://gitlab.com/gitlab-org/gitlab/-/issues/11222) describes optimistic
parallel validation, but still requires ordered integration and invalidates later
work when an earlier item fails or the target moves. The planner follows that
operational boundary: groups reduce review and validation contention, while exact
tip revalidation and the existing sequential queue remain authoritative for
integration. The official [`git diff` manual](https://git-scm.com/docs/git-diff)
defines the three-dot comparison used to measure each candidate from its merge
base.

A GitLab operator report opened in 2019,
[#33925](https://gitlab.com/gitlab-org/gitlab/-/issues/33925), records merge-train
admission regularly failing because the previous temporary ref no longer
existed. GitHub practitioners later reported in
[#120203](https://github.com/orgs/community/discussions/120203) that the
queue-created `gh-readonly-*` head can appear current while obscuring the
original source branch whose freshness actually matters. Together these durable
failures motivate a candidate identified by immutable object IDs rather than a
temporary ref, plus explicit freshness commands for the original target and
source refs before either rehearsal or admission.

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
bounded displayed paths, the 64-check group ceiling, serial preflights, and
fail-closed behavior instead of an unbounded conflict service. They also explain
why ambiguous or partial native evidence is discarded rather than promoted to a
clean claim.

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
pattern; parsing stops safely at the next ref namespace. It is ref/worktree
read-only: there is no checkout, merge, delete, ref update, or push path.

Because it changes neither refs, the index, the worktree, nor runtime service
state, deployment continuity is preserved. Default progress messages and
structured counts expose bounded work and truncation. Quiet mode removes only
deterministic narration and does not change the Git command set, scan bounds,
result schema, or resource namespace.
Semantic mode is also read-only and foreground-only: at most two fixed list-form
Git inspections run per bounded head, with progress on stderr and no files, locks,
daemons, services, ports, background workers, ref changes, or deploy interruption.
Page classification adds one exact `show-ref` verification over at most 101 refs;
the `--` option boundary prevents any validated ref from becoming an option, and
the parser accepts only the expected canonical refs and full object IDs. Default
progress exposes `verify=page-ref-snapshot` before that bounded check.
Queue generation and receipt replay have the same foreground-only boundary: they
verify identities and emit JSON but never merge, delete, rewrite, or push a ref.
Planning adds bounded path reads, pair comparisons, and native pairwise probes; it
does not execute the cumulative candidate recipe or treat either path disjointness
or pairwise cleanliness as a tested cumulative merge. Native preflight and group
prediction do not read or write the index or worktree and never create a commit
or ref. Git may materialize documented result trees as unreachable objects; no
reference exposes them, and normal Git object maintenance owns their lifecycle.
No custom cleanup runs during deployment or rollback.
An exhaustive run adds one terminal target resolution and one explicitly sorted
local-head scan, emits `verify=terminal-ref-snapshot` progress by default, and
shares the existing 10,000-ref, output, and timeout bounds. This preserves ZDD
while keeping CPU, memory, subprocess, and JSON growth bounded. During rollout,
old and new callers can run concurrently because the schema and Make interface do
not change; stale page or terminal evidence becomes a structured nonzero failure.

Rollback is layered and requires no coordinated downtime. Stop requesting the
opt-in semantic, planning, queue, or replay flags first; default schema-v2
inventory callers remain unchanged. If code rollback is required, revert
planning, receipt, and queue support before reverting page/terminal verification
or the authoritative semantic source commit, so no surviving mode depends on
removed types or validation paths. A version-2 receipt can be discarded and
regenerated after rollback; it is never applied to repository state. The older
textual inventory and one-branch patch comparison targets remain independently
available throughout. No service restart, data migration, ref repair, or
downtime is needed.

Merge-queue mode adds only foreground, read-only Git inspection and bounded JSON
serialization. It creates no checkout, index, lock, merge, commit, ref update,
push, daemon, port, or service transition; only Git's unreferenced result-tree
objects described above may be materialized. Generation and rollback therefore
have no runtime outage window. Existing callers remain unchanged because the flag
is opt-in. Rollback is a normal revert of the queue builder, its focused tests,
and this documentation; previously emitted payloads can simply be discarded and
regenerated with the older exhaustive summary. There is no repository repair,
data migration, service restart, or destructive cleanup step.

During inventory generation, the per-group predictor executes only bounded
pairwise `merge-tree` probes; the cumulative recipe remains JSON-only. If an
operator later executes that opt-in recipe, Git may write unreachable tree and
commit objects, but no name is installed under `refs/` and no index or worktree
is touched. Normal Git object maintenance owns those unreachable objects.
Rollback discards the plan or reverts the additive `rehearsal` builder and schema;
no ref, checkout, service, data migration, or cleanup coordination is required,
and the existing paginated, exhaustive, sequential-queue, and receipt modes
remain available throughout.

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
