# CI Failure Ledger

## Purpose

Gludd must not report one green workflow while a sibling workflow for the same
commit is red, discard later failures after noticing the first one, or rerun an
unchanged failure as a substitute for repairing it. The CI failure ledger turns
those expectations into persistent, executable policy.

The operational ledger lives at `.gludd/ci-failure-ledger.json`. It is ignored
by Git, written atomically with mode `0600`, capped at 2 MiB, and bound to
immutable GitHub Actions run ID/attempt pairs and full 40-character commit SHAs.

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
  for an already-recorded terminal attempt is rejected as contradictory
  evidence. GitHub reuses the run ID for a rerun, so attempts after the first
  are stored as `<run-id>:<attempt>` rather than corrupting attempt 1 evidence.
- A later recurrence of the same family removes its stale repair receipt and
  reopens it.

The distinction between `repaired` and `resolved` prevents a local test from
being misreported as a successful deployment.

## Automatic integration

`make ci-view` now fetches one exact run and persists **all** failed jobs and
failed steps before it prints the ledger. It no longer treats successful API
access as a successful build.

`make pipeline-status` resolves the exact pushed SHA, selects the newest
exact-branch `push` or `workflow_dispatch` run for every workflow, and observes
every terminal run into the ledger before returning the all-workflow verdict.
This includes manually dispatched candidate-branch runs without relaxing the
required-workflow set. Observation and verdict collection both run even if one
fails; their exit codes are combined afterward. This removes the prior
memory-dependent step of noticing a sibling workflow and manually running
`ci-view` for it.

`make ci-rerun` first invokes `ci-view`, then blocks a rerun of the unchanged
failed SHA. The only exception is an explicit operational experiment with both
`CI_RERUN_ALLOW_UNCHANGED=1` and a non-empty `CI_RERUN_REASON`. This override
does not mark any failure repaired.

`make ci-recover-runner-acquisition` is the no-prompt recovery path for the
specific GitHub-hosted failure “The job was not acquired by Runner of type
hosted even after multiple attempts.” It first records the complete terminal
attempt, fetches the Check Run annotations for every non-successful job, rejects
any job that executed a failing step or lacks that exact annotation, and permits
one full same-SHA rerun only from attempt 1. Attempt 2 is a hard recovery limit;
it requires diagnosis instead of an automatic loop. A full rerun is deliberate:
dependent jobs and artifacts remain part of one coherent candidate proof.

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

## Diagnose a completed job while its workflow is active

GitHub exposes logs at the job lifecycle boundary, while `gh run view --log
--job` can withhold them until every sibling job in the workflow is terminal.
Use the run-bound diagnostic instead:

```console
make ci-job-failure-context \
  RUN=<immutable-run-id> \
  JOB=<completed-job-id> \
  PATTERN=SHARD-FAIL \
  BEFORE=8 \
  AFTER=12 \
  MAX_MATCHES=10 \
  CI_JOB_CONTEXT_VALIDATE_ONLY=0
```

The target first proves that `JOB` belongs to `RUN`, then uses GitHub's
authenticated workflow-job log endpoint. The temporary log lives under the
external Gludd resource namespace and is removed at exit. An API error, empty
log, missing literal pattern, invalid numeric bound, or run/job mismatch fails
closed. `BEFORE`, `AFTER`, and `MAX_MATCHES` bound output without silently
discarding later independent failure batches.

## Terminal reconciliation of an active-run diagnosis

Per-job access accelerates diagnosis, but it does not make an active workflow's
job inventory complete. Build/Release run `37381218767` demonstrated the
difference: three completed failed jobs exposed four structural assertions
while `test-shard (3.11, other)` was still running. The terminal workflow later
contained four failed jobs, and `other` contributed two parametrized
self-improvement assertions. The immutable terminal inventory was therefore
six assertions, not the interim four.

Gludd now describes an active-run extraction as provisional. A completeness
claim requires a terminal run summary, enumeration of every non-successful job,
and bounded context from each failed job. On candidate
`5142ba735f66fbf074a7ec02d80d0d2ced0b582c`, the two late `fake-local` and
`fake-azure` cases pass together 2/2; that is local repair evidence, not hosted
resolution. A replacement exact-SHA workflow must still resolve the families.

## Candidate-merge repair preflight

The combined v0.1.1 candidate demonstrated why repair evidence runs before a
push. Parent branches were independently green, but their merged source set
contained 222 files below the unchanged maintainability-index floor budget of
220, and one structural lock test still assumed that `_commit-lock-acquire` had
no prerequisite. Cohesive extraction restored the existing budget, while the
test now parses prerequisites and independently requires the active-gate
history guard. No threshold or production behavior was weakened.

Repair selectors are exact identities. `ci-failure-status` and the central push
guard therefore emit the complete 64-character `family_id`; the repair command
accepts that value directly. Short display-only prefixes are not actionable
repair input. Evidence variables remain structured one-assignment arguments;
when one family needs multiple node IDs, use a deterministic test target or a
bounded filename glob rather than embedding shell syntax.

This boundary applies the same practitioner lessons recorded below: duplicate
checks and partial reruns make branch labels ambiguous, so the candidate SHA,
failure family, evidence command, and hosted resolution must all be immutable
and directly reusable.

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
- Users have reported the exact runner-acquisition annotation on unchanged,
  simple workflows and observed that a later retry may succeed, identifying it
  as hosted infrastructure rather than a repository test failure
  ([GitHub Community #186208](https://github.com/orgs/community/discussions/186208),
  [#165291](https://github.com/orgs/community/discussions/165291), and
  [#166283](https://github.com/orgs/community/discussions/166283)).
- A 13-job user report describes randomly cancelled hosted jobs and GitHub staff
  confirmed a platform-side incident, so a cancelled job is never classified
  from conclusion alone
  ([GitHub Community #126539](https://github.com/orgs/community/discussions/126539)).
- GitHub documents that matrix jobs maximize parallelism by default and that
  `max-parallel` is an explicit throughput control. Gludd retains full shard
  parallelism and bounds only this proven infrastructure retry instead of
  permanently slowing every healthy run
  ([GitHub matrix documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/run-job-variations#defining-the-maximum-number-of-concurrent-jobs)).
- GitHub CLI users report that selecting a completed job with `gh run view
  --log --job` can still wait for the entire workflow
  ([cli/cli #4575](https://github.com/cli/cli/issues/4575)); related reports
  cover unavailable or inconsistent job-log retrieval
  ([#4712](https://github.com/cli/cli/issues/4712),
  [#11059](https://github.com/cli/cli/issues/11059), and
  [#11109](https://github.com/cli/cli/issues/11109)). GitHub's
  [workflow jobs REST API](https://docs.github.com/en/rest/actions/workflow-jobs)
  therefore supplies Gludd's per-job diagnostic boundary.

Consequently, Gludd records the first terminal evidence before any retry,
keys it by immutable run ID and exact SHA, and treats a retry as an exception
rather than a repair.

## Verification

`tests/unit/test_ci_failure_ledger.py` covers all-failure collection,
idempotence, terminal-attempt immutability, retry identity, bounded
runner-acquisition recovery, recurrence, repair ancestry, complete blocker
output, guarded reruns, atomic permissions, CLI wiring, and malformed state.
`config/coverage_ci_failure_ledger.ini` measures the implementation directly
under the repository's 85% aggregate and 75% per-file requirements.
