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

### Exact job ownership without a run inventory lookup

`ci-job-failure-context` resolves the supplied numeric `JOB` through
`GET /repos/sandboxcom/gludd/actions/jobs/{job_id}` and requires both the
response `id` and `run_id` to match `JOB` and `RUN` before downloading logs.
The maintained `gh api` command performs both authenticated GET requests with
the existing media type and API version. GitHub documents these identity fields
on the single-job endpoint and the separate job-log download endpoint.
[GitHub workflow-job REST API](https://docs.github.com/en/rest/actions/workflow-jobs?apiVersion=2026-03-10#get-a-job-for-a-workflow-run).

During v0.1.2 stabilization, the run-wide job lookup repeatedly returned HTTP
502 on its `per_page=100` fan-out while the exact job ID was already available.
Direct job ownership lookup removes that unnecessary list request. Metadata
errors retain the original CLI exit status; a missing or mismatched identity
fails before log retrieval. Log download errors, empty logs, missing literal
patterns, cleanup, all existing context arguments, and the 2,048-byte line and
32,768-byte total UTF-8 display limits retain their existing behavior.

Historical practitioner evidence supports treating job inventory as a distinct
retrieval dependency. In CLI issue #9341 (July 2024), a user could retrieve a
specific job while the run view omitted it; the maintainer linked the older
client behavior to pagination, and the reporter confirmed upgrading fixed that
case. That resolved pagination report is evidence for the dependency boundary;
it does not establish the cause of the current HTTP 502 response.
[GitHub CLI practitioner report #9341](https://github.com/cli/cli/issues/9341).

The fake-CLI regression fixture executes the native Make recipe and the same
context collector. It exercises exact job/run ownership, missing identities,
metadata and log errors, empty responses, context bounds, and owned-file
cleanup. Focused diagnostics remain separate from terminal workflow inventory
and exact-SHA release proof.

### Complete inventory through native pagination

`fetch_run` keeps GitHub CLI's run metadata fields while requesting `jobs`
separately with authenticated `gh api --paginate --slurp`. Each request uses
50 jobs per page and the latest metadata's explicit attempt endpoint, so every
page belongs to the same run attempt even if a rerun starts during retrieval.
The native CLI follows pagination links; Gludd adds no HTTP client or retry
loop. [GitHub CLI API manual](https://cli.github.com/manual/gh_api) and
[GitHub attempt jobs endpoint](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run-attempt).

The adapter preserves the existing run-view job and step keys, ordering, empty
conclusions, and timestamp representation. It verifies run/repository/SHA
ownership, explicit job attempts when supplied, unique job IDs, stable page
totals, and complete job counts. A failed final page rejects the entire
inventory, including otherwise valid partial output. Existing ledger and
pipeline-status consumers retain their field contract and fail-closed behavior.
[GitHub CLI field projection](https://github.com/cli/cli/blob/v2.80.0/pkg/cmd/run/shared/shared.go).

Practitioner report [CLI #9341](https://github.com/cli/cli/issues/9341) describes
missing run-view jobs resolved by upgrading to a CLI with pagination. GitHub
Community [#196998](https://github.com/orgs/community/discussions/196998)
reports workflow and job attempt counts disagreeing after a rerun. These
reports support complete pagination and explicit attempt checks; neither proves
the cause of Gludd's observed HTTP 502. The regression suite includes 123 jobs
over three pages, a final-page error, malformed payloads, ownership mismatches,
null values, and timestamp compatibility.

Canonical final selection reproduces 67 failures and six controls before the
native transport change. All 269 ledger/concurrency/status/CI-green consumer
cases then pass on Python 3.11 and 3.14 (2.94/4.33 seconds). The unchanged native
coverage profile passes 155 cases at 96.34% lines/93.52% branches and both 85/75
floors; audit: `.gate-logs/coverage-20261013T221146Z.json`. Strict typing/Ruff,
docstrings and unchanged 258-resource ownership pass. All 73 new cases reside in
the already admitted complete ledger test file; no early assertion or scope is
removed. The native duplicate-code guard then rejects a repeated API response
block. Jobs and existing annotations now share one bounded CLI JSON helper,
retaining both exact argument lists, existing error messages, and annotation-only
empty-output behavior. All 269 consumers pass again on both Python versions;
the unchanged profile passes 155 cases at 95.65% combined coverage, with both
line and branch floors green (`.gate-logs/coverage-20261013T221530Z.json`).
This is source acceptance, not live hosted inventory or release proof.

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

## Late terminal step details (2026-10-13)

The exact native observation for run 38477108604, attempt 1, initially records
19 terminal jobs with gate (3.12) failed steps reported only as job failure.
The next authenticated observation preserves every run and ordered-job
identity and outcome, but supplies the named admission failure. Rejecting this
narrow refinement as a rewritten terminal run leaves the snapshot in lookup
error and prevents the bounded heartbeat from terminating.

The existing locked ledger owner now stores optional terminal_details,
bounded to one entry per original run/attempt and the unchanged 2 MiB ledger
limit. The projection binds both the original receipt fingerprint and the
recomputed refined fingerprint. Original run receipts, failure occurrences,
repair evidence and family status remain unchanged. Only a failure job's
missing step names can be filled. Subsequent observations compare against
the accepted details; changed identity, outcome, job order or known step
names still fail closed. Legacy version-1 ledgers require no migration.

The [attempt-specific jobs API](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run-attempt)
provides job and step data but does not promise byte-identical terminal
responses. The 2022 [CLI inventory report](https://github.com/cli/cli/issues/6378)
and longstanding [workflow API discussion](https://github.com/orgs/community/discussions/24626)
describe related inconsistent or missing results; they are not proof of this
exact cause. The paired local observations establish the narrow correction.
No new client, watcher, retry, status writer or deployment action is added.

Three canonical history controls fail first on the original producer. The
38 additive controls preserve all existing assertions; 293 ledger, concurrency,
status and CI-verdict cases pass on both installed Python versions. The original
coverage profile passes 193 cases at 96.53% lines and 93.39% branches, with zero
exclusions and unchanged 85/75 floors; its audit is
.gate-logs/coverage-20261013T225503Z.json. Ruff, strict typing and docstrings pass.
The live native owner enriches the exact existing receipt, emits failure with
no lookup errors, and automatically stops nonzero with release_ready=false.
The real Python 3.12 timeout remains open; this repair supplies no release proof.
Rollback restores the owner and additive tests together; retain original
receipts and their validated optional details rather than deleting evidence.

## Observed candidate versus local checkout banner (2026-10-14)

A bounded owner tracking published `05f1a93d8c5e4e443e4367c282834e1bb7212258`
retains that correct snapshot identity after local development advances. Its
heartbeat banner nevertheless labels the newer local HEAD as the watched SHA.
Two new native controls reproduce this mismatch for a pinned and an automatic
remote-head observation. The banner now distinguishes the requested SHA
(or `remote-head` while the producer resolves it) from `local_sha`. Snapshot
identity, terminal-stop authority and all release-proof requirements are
unchanged; no extra query, writer or watcher is added.

All 84 related controls pass on Python 3.11 and the combined 368-case native
acceptance/watcher replay passes on Python 3.14. All 234 Make contracts and the
documented no-execution example pass; that plan output is not gate evidence.
The complete 420-case pipeline-owner replay passes in 52.2 observed seconds;
canonical two-owner coverage is 96.43% lines and 91.67% branches, with unchanged
85/75 floors (`.gate-logs/coverage-20261014T035819Z.json`). Its nine existing
typing/import exclusions are unchanged. This is source evidence only.
The current producer owns the exact snapshot, not the banner, as reinforced by
the immutable-source practitioner and API findings above. The already-running
owner keeps its compiled old banner until it exits; it is not restarted or
duplicated merely to pick up a display fix. Public proof remains tied to its
explicit watched SHA, and S98.4 remains open.

## Canonical source identity across child directories (2026-10-14)

The pipeline profile's wildcard run includes also measure same-named scripts
created outside the checkout by native Make fixtures. An initial relative-only
repair still measures child fixture copies and loses canonical child coverage
when their working directory changes. Passing test assertions therefore do not
establish valid source coverage.

The existing coverage.py profile now uses `source_dirs = scripts` and retains
the two canonical file paths in report includes. coverage.py binds source
directories before serializing subprocess configuration; relative run includes
do not receive that binding. Branch measurement, parallel fragments and
`patch = subprocess` remain enabled. There are no omit patterns, new exclusion
pragmas, warning suppressions, runner changes or reduced coverage floors.
[Official source-directory configuration](https://coverage.readthedocs.io/en/7.16.2/config.html#run-source-dirs)
and [subprocess measurement](https://coverage.readthedocs.io/en/7.14.1/subprocess.html).

The long-lived [coverage.py practitioner report #914](https://github.com/coveragepy/coveragepy/issues/914),
opened in January 2020, reproduces changed-directory subprocesses dropping
canonical tracing under relative includes. It motivates the real child-process
regression here; it is not a Gludd coverage proof or permission to omit code.

Five native controls fail first in the main checkout, then execute real
canonical owners and external namesakes in parent and changed-directory Python
children. They reject external measurement, require child-only canonical body
lines and preserve empty stderr. Thirty-three additional controls cover existing
lookup, exact identity, newest-run failure and local liveness branches without
claiming live hosted results. All eight original owner suites remain intact.

The complete main replay passes 420 cases in 52.2 observed seconds. Its JSON
contains exactly `scripts/pipeline_status.py` and `scripts/require_ci_green.py`:
aggregate 96.43% lines/91.67% branches; pipeline owner 95.56%/89.53%; CI owner
100%/100%. The native 85/75 audit passes with nine unchanged typing/import
exclusions (`.gate-logs/coverage-20261014T035819Z.json`). Ruff and strict typing
pass; unrelated entrypoint module-root layouts are typed separately to avoid
duplicate-module invocation errors. This is source evidence only, not a full
gate, exact-SHA hosted result or installed deployment acceptance.
All 38 additive source-boundary/failure controls also pass on Python 3.14 in
3.8 observed seconds (`.gate-logs/v012-pipeline-source-main-314.log`).

## Verification

`tests/unit/test_ci_failure_ledger.py` covers all-failure collection,
idempotence, terminal-attempt immutability, retry identity, bounded
runner-acquisition recovery, recurrence, repair ancestry, complete blocker
output, guarded reruns, atomic permissions, CLI wiring, and malformed state.
`config/coverage_ci_failure_ledger.ini` measures the implementation directly
under the repository's 85% aggregate and 75% per-file requirements.

## Remaining-batch diagnostics decision (2026-10-14)

Source inspection finds native collect-all support already present:
`make ci-shards-diagnostic` forwards `--collect-all-failures` to
`scripts/run_ci_shards_serial.py`. Its classifier continues codes 1, 2, 5 and 6,
and its existing loop retains every failed batch and returns nonzero. This is
broader than assertion-only continuation: pytest documents 2 as interruption,
5 as no collected tests and 6 as an exceeded warning limit; `-W error` can also
produce an ordinary failed-test result. An exit code alone cannot distinguish
an assertion from a warning exception.
[pytest exit codes](https://docs.pytest.org/en/stable/reference/exit-codes.html)
and [warning errors](https://docs.pytest.org/en/stable/how-to/capture-warnings.html#controlling-warnings).

The hosted command in `.github/workflows/build.yml` requires release policy.
`main()` explicitly rejects its combination with diagnostic collect-all before
execution; `test_release_policy_rejects_collect_all_diagnostics` and
`test_native_diagnostic_and_release_policy_remain_incompatible` pin that
contract. Keep this rejection. Existing exact-SHA admission reuses passing
receipts, while failure receipts are `reusable=false`; restarting with resume
would execute the failed batch again. It cannot satisfy a request to run only
the previously unstarted remainder.

Proposed decision: add an explicit option to the existing owner that switches
to diagnostics after its first code-1 batch failure and advances the same plan
iterator. The original failure remains recorded and the terminal result remains
nonzero even if every later batch passes. Later results are diagnostic evidence;
code 1 may include warning failures and must never be described as assertion-only.
Codes 2 through 6 and all existing cancellation, timeout, worker-death, owner,
resource, coverage-integrity and cleanup failures stop further execution.
The failed batch and earlier successful batches are each executed only once.
The already-finished hosted invocation cannot acquire this behavior retroactively.

Use the existing policy and receipt seams: parser construction in
`scripts/ci_shadow_receipt_runtime.py`, `run()` and `main()` in
`scripts/run_ci_shards_serial.py`, `_runtime_admit_exact_batch`, successful
`session.writer.publish`, `_publish_shadow_failure_receipt`, gate progress and
`_write_terminal_attestation`. After switching to diagnostics, disable both
passing-receipt admission and publication; retain non-reusable failure receipts
and identify the diagnostic scope in existing terminal evidence. No second
runner, result parser, watcher or proof cache is needed. Ordinary pytest already
continues after failed tests unless configured to stop with `-x` or `--maxfail`.
[pytest failure handling](https://docs.pytest.org/en/stable/how-to/failures.html).

Add failing-first controls at these exact existing integration paths:

- `tests/unit/test_ci_collect_all_stress.py`: pass/fail/pass/fail plan order,
  each batch launched once, every failure retained, final nonzero, real warning
  failures, collection interruption and a later safety stop; preserve the old
  diagnostic-mode assertions.
- `tests/unit/test_ci_named_shard_safety.py`,
  `tests/unit/test_ci_failure_receipts.py` and
  `tests/unit/test_ci_named_shard_files.py`: unchanged owned cleanup and deadline
  authority, no admission/publication after the first failure, retained immutable
  receipts and terminal failure even when the diagnostic remainder passes.
- `tests/unit/test_ci_shards_diagnostic_entrypoint.py` and
  `tests/unit/test_ci_hosted_environment_contract.py`: preserve the existing
  release rejection and prove explicit native workflow wiring, all eight shards,
  one worker, 16-file batches, 90-minute step and 120-minute job bounds.

The user report [pytest #1421](https://github.com/pytest-dev/pytest/issues/1421),
opened February 29, 2016, describes wanting collection/import errors to prevent
otherwise runnable tests from executing. It supports preserving that stop
boundary; it is not Gludd acceptance evidence. This decision is a proposal,
not a measured throughput improvement. Exact-SHA source review, admission,
complete canonical gate, hosted proof, ZDD and unchanged 85/75 coverage floors
remain required; diagnostic results cannot close S98.4 or authorize promotion.

### Native adoption and limits

The proposed boundary is now implemented by `--diagnose-after-failure` in the
same runner and selected explicitly by the existing hosted shard step. A first
code-1 failure latches the diagnostic scope across the remaining original plan;
later batches are not replayed or scheduled by another owner. The existing
terminal attestation stays failed and adds `diagnostic_only_batches` to its
batch-execution details. Later exact and legacy passing-receipt reuse and
publication are refused. Failed receipts retain their non-reusable contract.
General collect-all remains incompatible with release policy. No selector,
warning handling, coverage floor, worker count or execution deadline is weakened.

Twenty-two native controls fail before the option, with separate failing-first
workflow/admission controls and additive conformance of three exact roster pins.
All 677 related cases pass on Python 3.11 and 3.14, including real warning-fatal
children and Python 3.14's required release-policy refusal. The unchanged
two-owner coverage profile passes at 91.7% lines/89.2% branches and every 75% floor
(`.gate-logs/coverage-20261014T163047Z.json`). All existing early selectors remain;
the full stress file and two native failure families are added to admission.
The complete native roster passes 1690 cases plus fresh help in 62.8 observed
seconds under unchanged 90/60 limits. At the full 32-command inventory, the
existing repaired registry acceptance command passes its three cases first;
native retention then releases its superseded successful pointer without deleting
the receipt. Failed/live pointers and the inventory cap are preserved. This is
actual repaired-input acceptance, not an inventory reset or a status-only task.
Exact-successor hosted and complete release proof remain pending.

This control-plane change requires no serving restart or deployment mutation.
Active jobs retain their original loaded code; adoption cannot recover unstarted
batches from an already finished invocation. Rollback restores the prior source
and workflow through normal branch review, retaining immutable failure receipts.
Local tests and diagnostic results do not satisfy full-gate, installed-image or
deployment acceptance. No completed-backlog item or release task is closed here.

### Legacy resume publication follows owned cleanup

Legacy resume previously wrote a passing batch entry immediately after rc0 and
coverage capture, before checking owned temporary/workspace cleanup. Three
native controls fail first: even ordinary success is published too early, and
cleanup failure 74 or cancellation 130 leaves a passing entry. The same atomic
save is moved after successful owned cleanup; valid coverage, rc0, existing
candidate/runner binding and refusal of diagnostic pass records remain required.
No existing resume file, receipt or failure history is manually rewritten.

The [pytest safe-teardown guidance](https://docs.pytest.org/en/stable/how-to/fixtures.html#safe-teardowns)
explains that teardown failures can leave resources behind. The
[2022 practitioner report](https://github.com/pytest-dev/pytest/issues/9909)
shows a test-body pass alongside a teardown error, with follow-up reports in
2023/2024. That is lifecycle/reporting context, not independent proof of this
Gludd supervisor bug. Reuse existing pytest and native cleanup/atomic-save
owners; do not build another result parser, cache or cleanup framework.

All 680 related cases pass on Python 3.11/3.14. Unchanged two-owner coverage passes
91.7% lines/89.2% branches and every 75% floor
(`.gate-logs/coverage-20261014T170226Z.json`). Complete native admission passes
1693 cases plus fresh help in 60.9 observed seconds under unchanged 90/60 bounds.
New-family literal annotation is corrected after four registration failures;
all original selectors, resource limits and required full-gate phases remain.

This is control-plane ZDD: no serving process is restarted. Future invocations
use the reordered save, while active jobs keep their loaded code. Rollback uses
normal branch review and retains immutable evidence. Publication, clean full
gate, exact-successor hosted and installed deployment proof remain mandatory.

### Repair evidence binds the tested checkout

The repair CLI previously accepted any descendant SHA while executing evidence
in its current ROOT. A passing child could therefore be attributed to a different
revision or a dirty checkout. Fourteen boundary controls reproduce that gap.
The existing native `make worktree-state` owner now verifies exact HEAD, clean
state and qualified root before and after evidence. Query failure, missing or
malformed data, source mutation and 15-second query timeout refuse publication;
two timeout regressions fail before handling, with two I/O controls retained.
Validation-only remains non-executing and produces no receipt.

All 397 related cases pass on Python 3.11/3.14. The unchanged canonical owner
profile passes 96.6% lines/93.4% branches and both 75% floors
(`.gate-logs/coverage-20261014T173231Z.json`). Full native admission passes 1712
cases plus fresh help in 63.4 observed seconds under unchanged 90/60 limits.
Existing argument, history and locking assertions are preserved; distinct
revision concurrency is modeled with separate clean checkouts. The actual dirty
checkout is also refused before evidence, leaving the real failure open.

[SLSA provenance](https://slsa.dev/spec/v1.2/provenance) treats source revision
provenance as distinct from build output claims. Reuse the project's existing
worktree owner and ledger instead of adding an attestation format or claiming
SLSA compliance. A
[2022 Actions practitioner discussion](https://github.com/orgs/community/discussions/28474)
distinguishes a pull request's head revision from its temporary merge checkout;
that supports explicit identity binding, but is not Gludd acceptance evidence.

These checks cover observed execution boundaries, not an adversarial edit-and-
restore within the evidence interval. Immutable reviewed-head integration and
all existing release gates remain required. No serving process is restarted;
active callers keep their loaded code, and future invocations use the guard.
Rollback uses normal branch review while preserving receipt history. Local
repair remains `repaired`, never hosted `resolved` or deployment-ready.
