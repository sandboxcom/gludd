# CI Failure Ledger

## Purpose

Gludd must not report one green workflow while a sibling workflow for the same
commit is red, discard later failures after noticing the first one, or rerun an
unchanged failure as a substitute for repairing it. The CI failure ledger turns
those expectations into persistent, executable policy.

The operational ledger lives at `.gludd/ci-failure-ledger.json`. It is ignored
by Git, written atomically with mode `0600`, capped at 2 MiB, and bound to
immutable GitHub Actions run IDs and full 40-character commit SHAs.

## Enforced state machine

Each failed or cancelled job is a separate failure family. Its identity is a
SHA-256 digest of branch, workflow, job, conclusion, and every failed step.

```text
terminal failure -> open -> repaired -> resolved
                         \-> open     (failure recurs)
```

- `open` means the failure was observed but no successful local repair evidence
  has been recorded. Every open family blocks the next push.
- `repaired` means a newer descendant commit passed an exact `make` evidence
  target. A repaired family permits a descendant push, but it is not described
  as hosted-green.
- `resolved` means a later hosted run at a different exact SHA completed the
  matching workflow/job successfully.
- Observing the same terminal run again is an idempotent no-op. Different data
  for an already-recorded terminal run is rejected as contradictory evidence.
- A later recurrence of the same family removes its stale repair receipt and
  reopens it.

The distinction between `repaired` and `resolved` prevents a local test from
being misreported as a successful deployment.

## Automatic integration

`make ci-view` now fetches one exact run and persists **all** failed jobs and
failed steps before it prints the ledger. It no longer treats successful API
access as a successful build.

`make pipeline-status` resolves the exact pushed SHA, selects the newest run
for every workflow, and observes every terminal run into the ledger before
returning the all-workflow verdict. Observation and verdict collection both
run even if one fails; their exit codes are combined afterward. This removes
the prior memory-dependent step of noticing a sibling workflow and manually
running `ci-view` for it.

`make ci-rerun` first invokes `ci-view`, then blocks a rerun of the unchanged
failed SHA. The only exception is an explicit operational experiment with both
`CI_RERUN_ALLOW_UNCHANGED=1` and a non-empty `CI_RERUN_REASON`. This override
does not mark any failure repaired.

Every repository push route already converges on `_push-rate-guard`.
`ci-failure-push-guard` is now a prerequisite of that central guard, so direct,
batch, development, release, and force-push wrappers cannot bypass recorded
failure ownership. No ledger is an explicit `INACTIVE` state, never a claim
that CI is green.

## Repair workflow

Use a narrowly scoped evidence target after landing the repair locally:

```console
make ci-failure-repair \
  CI_FAILURE_LEDGER=.gludd/ci-failure-ledger.json \
  CI_FAILURE_FAMILIES='<family hashes, or leave empty>' \
  CI_REPAIR_ALL_OPEN=1 \
  CI_REPAIR_SHA=<full repair commit SHA> \
  CI_REPAIR_EVIDENCE_TARGET=test-files \
  CI_REPAIR_EVIDENCE_VARS='TESTFILES=tests/unit/test_ci_failure_ledger.py PYTEST_ARGS=-q' \
  CI_FAILURE_VALIDATE_ONLY=0
```

The ledger invokes the evidence as an argument-vector `make` command, streams
its output, and writes receipts only after exit zero. Shell snippets and
lowercase/unstructured variable names are rejected. The repair SHA must differ
from and descend from the most recent failed SHA for every selected family.

Useful read-only commands are:

```console
make ci-failure-status \
  CI_FAILURE_LEDGER=.gludd/ci-failure-ledger.json \
  CI_FAILURE_VALIDATE_ONLY=0

make ci-failure-push-guard \
  CI_FAILURE_LEDGER=.gludd/ci-failure-ledger.json \
  CI_FAILURE_BRANCH=development \
  CI_FAILURE_HEAD=<full SHA> \
  CI_FAILURE_VALIDATE_ONLY=0
```

Both guards enumerate every blocker rather than returning after the first.

## Practitioner findings

This design addresses failure modes reported by GitHub Actions users:

- A rerun uses the original workflow definition rather than a later workflow
  fix, so retrying the old run cannot validate the new commit
  ([GitHub Community #27083](https://github.com/orgs/community/discussions/27083)).
- Users report that “re-run failed jobs” can omit dependent jobs in matrixed
  reusable workflows, making a partial retry incomplete evidence
  ([GitHub Community #52505](https://github.com/orgs/community/discussions/52505)).
- Full reruns can replace artifacts from the original attempt, destroying the
  diagnostics needed to explain the first failure
  ([GitHub Community #17854](https://github.com/orgs/community/discussions/17854)).
- Duplicate Actions for the same commit are common enough that users employ a
  separate Check Runs lookup to suppress them
  ([GitHub Community #27031](https://github.com/orgs/community/discussions/27031)).
- Branch/ref names are insufficient when exact PR head identity matters
  ([GitHub Community #25191](https://github.com/orgs/community/discussions/25191)).

Consequently, Gludd records the first terminal evidence before any retry,
keys it by immutable run ID and exact SHA, and treats a retry as an exception
rather than a repair.

## Verification

`tests/unit/test_ci_failure_ledger.py` covers all-failure collection,
idempotence, terminal-run immutability, recurrence, repair ancestry, complete
blocker output, guarded reruns, atomic permissions, CLI wiring, and malformed
state. `config/coverage_ci_failure_ledger.ini` measures the implementation
directly under the repository's 85% aggregate and 75% per-file requirements.
