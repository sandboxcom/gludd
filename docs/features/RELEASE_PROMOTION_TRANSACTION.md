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

## Stable release publication

`release-cut` applies reviewed-receipt readiness to both `v0.1.1` and `v0.1.2`.
Its first continued shell also enables `set -eu`: a rejected receipt stops before
the dual-track child or any push, tag, release lookup, or artifact verification.
Both readiness bypass switches are explicitly zero. Exact candidate, branch,
attestation, and dual-track controls retain their existing ownership.

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
for both stable tags.

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
