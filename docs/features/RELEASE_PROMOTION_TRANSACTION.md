# Exact-SHA Release Promotion Transaction

`release-promote` stops at the first failed evidence or Git operation. Its
continued shell enables `set -eu`, so a rejected cleanliness guard, dual-track
result, or readiness receipt cannot be overwritten by a later success or a
validation marker. The existing release policy and 85% aggregate / 75% per-file
coverage requirements remain unchanged.

## Candidate and master identity

The target captures the development SHA, canonical master SHA, and development
local-attestation path before any branch selection. It requires the invoking
checkout to match the captured development tip, both checkouts to be clean,
master to be an ancestor, and the exact candidate's dual-track and readiness
evidence to pass.

Only real mode then selects `master` in the canonical main checkout. It verifies
the branch and captured master SHA before `merge --ff-only` of the captured
development SHA. Afterward, it verifies the canonical branch is still `master`
and its HEAD is exactly the verified candidate before invoking `release-cut`.
A successful checkout or merge that leaves the wrong identity therefore cannot
publish. A concurrent master advance during evidence collection also fails
before the merge.

Publication retains `RELEASE_CANDIDATE_SHA`, `RELEASE_CI_BRANCH=development`, the
captured local attestation, and the absolute reviewed-receipt path. Selecting
master never replaces the already verified source evidence with another
branch's namespace.

## Reviewed candidate integration

Seal one immutable source ref at clean development tip `S`, and independently
review that source against the recovered integration base `B`. Construct a real
no-fast-forward integration commit `I` on an isolated branch from `B`, with
ordered parents `[B, S]` and exactly the source tree. Existing `git-branch`,
`agent-worktree-base`, and named-ref `git-merge` targets own that operation.
Do not call a normal commit a cherry-pick or invent merge parents for a receipt.

`development-fast-forward` then advances only canonical main-checkout development
from exact `S` to exact `I`, without adding another merge. It retains the inactive
gate and main-checkout guards, requires a clean development checkout, checks both
expected full SHAs and ancestry before any ref write, merges only the captured
SHA with Git's `--ff-only`, and verifies branch, HEAD, development, source ref,
and cleanliness afterward. Divergence or identity drift fails closed. It creates
no tag, remote push, release, daemon, or deployment, and never changes master.

Safe validation accepts resolvable expectations and writes no refs:

```text
make development-fast-forward SOURCE=development EXPECTED_BEFORE_SHA=HEAD EXPECTED_AFTER_SHA=development APPLY=0
```

Real application requires explicit immutable 40-character SHA expectations:

```text
make development-fast-forward SOURCE=sealed-integration-ref EXPECTED_BEFORE_SHA=full-source-sha EXPECTED_AFTER_SHA=full-integration-sha APPLY=1
```

Run the combined focused acceptance and fresh canonical full gate at clean `I`
in the main checkout. The reviewed-head receipt records actual source `S`, actual
parents `[B, S]`, and focused/gate tip `I`; hosted evidence separately proves `I`.
The existing receipt builder checks real refs and parents against the external
review. Its `passed` fields must describe executed checks, never anticipated work.

Temporary real-Git recipe tests prove successful exact fast-forward without an
extra commit, nonmutating validation, rejected malformed or mismatched pins,
dirty/untracked/staged trees, wrong branch, divergence, failed ownership guards,
and post-merge hook identity drift. Only existing external guards are replaced;
the target recipe and Git operations run unchanged against test-owned repositories.

## Stable release publication

`release-cut` applies full readiness admission (`RELEASE_READINESS_VALIDATE_ONLY=0`)
to both `v0.1.1` and `v0.1.2`. A valid reviewed receipt cannot bypass an open or
absent required release milestone. Its first continued shell also enables
`set -eu`: any rejected readiness evidence stops before the dual-track child or
any push, tag, release lookup, or artifact verification. Both readiness bypass
switches are explicitly zero. Exact candidate, branch, attestation, and
dual-track controls retain their existing ownership.

`RELEASE_READINESS_CI_BRANCH` defaults to empty and is forwarded independently
as `--ci-branch`; empty lets readiness use the actual checkout's CI namespace.
Promotion supplies `development`, and the cut carries its captured
`RELEASE_CI_BRANCH` into that field. The actual checkout remains truthfully
identified as master after promotion. CI results must still prove the exact
candidate SHA in the explicit source namespace; changing a branch label cannot
make evidence for another source or SHA admissible.

For an explicit source, use the normal readiness target with every argument:

```text
make release-readiness TAG=v0.1.2 RELEASE_READINESS_VALIDATE_ONLY=0 RELEASE_COMPLETED_STAGES= RELEASE_OBSERVATIONS= REVIEWED_HEAD_INTEGRATION_RECEIPT=path/to/receipt.json RELEASE_CANDIDATE_SHA=full-sha RELEASE_READINESS_CI_BRANCH=development
```

## Prepared candidate versus publication

`RELEASE_PROMOTE_PUBLISH` defaults to `1`, preserving live publication through
`release-cut`. Only explicit `0` or `1` values are accepted; empty or unknown
values fail before topology or evidence operations.

In real mode, `RELEASE_PROMOTE_PUBLISH=0` still requires every existing exact-SHA
local and hosted result, cleanliness guard, full milestone and receipt check,
canonical master selection, exact fast-forward, and final branch/SHA verification.
Only after those checks does it emit `RELEASE-PROMOTE-PREPARED` with the verified
candidate SHA and `publish=0`. It exits without calling `release-cut`, pushing,
creating a tag, or publishing artifacts. This supports the requested GitHub-flow
integration and release preparation independently of a later publication request;
it does not grant admission to an incomplete candidate.

`RELEASE_PROMOTE_VALIDATE_ONLY=1` remains read-only with either publication
selection: it runs validation and emits `RELEASE-PROMOTE-VALIDATED`, without
selecting or advancing master. Preparation-only real mode is explicitly different
because it advances canonical local master after the complete proof.

A failed publication step propagates a nonzero result. If failure occurs after
the local fast-forward, local master may remain at the verified candidate; the
target does not rewrite shared history or automatically reverse published refs.
Operators retain the failure evidence and revalidate the exact candidate before
retrying the existing publication workflow.

## Harmless behavioral verification

`tests/unit/test_release_promote_target.py` executes the actual extracted recipes
with temporary Git and Make evidence owners. Only the canonical checkout path is
mapped into the temporary fixture; no real ref, remote, tag, or release is used.
The receipt owner is a test double for existing readiness validation, not a
replacement receipt format. Tests cover each evidence failure in both modes,
namespace and Git errors, wrong master identities, exact fast-forward ordering,
captured evidence routing, publication errors, and absent or mismatched receipts
for both stable tags. The fake readiness owner distinguishes receipt-only
validation from full admission: valid receipts with open or absent milestones,
wrong source namespaces, and mismatched CI SHAs must all stop publication.
Additional tests prove preparation-only mode performs every evidence check and
exact master promotion, rejects failed proof before any ref write, emits its
marker only after final verification, and never invokes publication owners.

The literal `release-promote` behavioral example from
`config/make_target_contract.json` is replayed through those isolated owners and
must report validation success with zero ref or publication mutations.

Run the focused contract with:

```text
make test-files TESTFILES=tests/unit/test_release_promote_target.py PYTEST_ARGS='-q -n 1 -W error --tb=short'
```

## Zero-downtime and resource bounds

This is an administrative release-control change: verification starts no
application listener or daemon and changes no database schema. Active release
traffic continues serving throughout candidate checks and local Git promotion;
the existing deployment workflow owns later canary, drain, and rollback behavior.

The focused suite uses one worker, temporary paths beneath its namespaced Gludd
test root, small JSON evidence files, and a ten-second timeout for each isolated
recipe. It starts no background watcher or extra CI matrix. Real publication
retains the existing bounded exact-tag workflow wait and artifact owners.

## Practitioner and upstream evidence

Reviewed 2026-10-12. The long-lived 2012 practitioner discussion
[Terminate makefile command when subcommand throws?][shell-failure] records the
same failure pattern: a shell command list can return the last command's success
after an earlier child fails. [GNU Make's one-shell guidance][gnu-one-shell]
documents this risk and the need to make shell failures visible to Make.

[Git's merge documentation][git-merge] states that fast-forward-only updates the
current branch and rejects divergent histories; an already integrated commit
can otherwise yield an up-to-date success. Checking cleanliness alone therefore
cannot prove that canonical master was promoted.

The existing release-page rationale cites [GitHub Community #26603][tag-filter],
where practitioners reported release-event tag filters failing to constrain
execution. Both stable tags consequently pass explicit reviewed-head policy
before publication instead of relying on workflow triggers for admission.

[shell-failure]: https://stackoverflow.com/questions/10559064/terminate-makefile-command-when-subcommand-throws
[gnu-one-shell]: https://www.gnu.org/software/make/manual/html_node/One-Shell.html
[git-merge]: https://git-scm.com/docs/git-merge
[tag-filter]: https://github.com/orgs/community/discussions/26603
