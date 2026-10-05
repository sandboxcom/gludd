# Exact-SHA GHA Signal

`make ci-push-committed-head` is the release-candidate push path. After it
pushes and verifies the clean committed HEAD, it invokes
`make ci-trigger-committed-head`. The trigger target is an idempotent signal:
it returns a GitHub Actions run URL for the exact pushed SHA and does not
require a person to notice that a run is missing.

## Contract

The real target fails closed unless all of these are true:

1. The current worktree is clean.
2. `sandboxcom/<current branch>` exists.
3. The remote branch tip equals the full local `HEAD`.
4. GitHub CLI lookup and dispatch operations succeed.
5. A run whose full `headSha` equals local `HEAD` becomes visible.

Unrelated sibling worktrees are deliberately outside this push-and-signal
gate. The path evaluates only the current checkout and the selected remote ref,
so completed or dirty work in another isolated worktree cannot suppress a
needed exact-SHA CI signal.

It then follows this sequence:

1. Take a host-local lock keyed by repository, workflow, and full SHA.
2. Query `Build and Release` runs using the full commit SHA.
3. Give the push-triggered run a bounded discovery window.
4. If an active or successful exact-SHA run exists, return its URL without
   dispatching.
5. If only completed non-success runs exist, emit `GHA-SIGNAL-RETRY`, dispatch
   one replacement, and atomically refresh the durable marker.
6. Otherwise dispatch once, durably record that accepted request, and query
   until the exact-SHA run URL is visible.
7. If GitHub visibility is delayed beyond the confirmation window, fail with
   the accepted dispatch URL when available and refuse another dispatch.

The lock prevents concurrent release processes on this host from racing. The
durable marker under `/tmp/gludd-gha-signal-*.json` prevents a later retry from
dispatching the same SHA again while GitHub has not returned terminal evidence.
A completed `cancelled`, `failure`, or other non-success conclusion invalidates
that marker and permits exactly one replacement dispatch. An
external actor on another host can still create a run; the exact-SHA lookup
detects and reuses any active or successful run that GitHub exposes.

Successful output ends with a stable machine-readable line:

```text
GHA_RUN_URL=https://github.com/sandboxcom/gludd/actions/runs/<run-id>
```

`GHA-SIGNAL-EXISTING` means no dispatch was needed.
`GHA-SIGNAL-DISPATCHED` means this invocation dispatched and then confirmed
the run. `GHA-SIGNAL-RETRY` identifies the terminal run that authorized a
replacement. `GHA-SIGNAL-BLOCKED` is fail-closed and never counts as CI
evidence.

## Usage

The release-candidate push path signals automatically:

```text
make ci-push-committed-head
```

To signal an already-pushed current branch directly:

```text
make ci-trigger-committed-head REF=release/beta3-candidate
```

The historical `make ci-trigger` name is a compatibility alias to the same
exact-SHA implementation. It no longer performs an independent branch-only
`gh workflow run`, so operators and automation cannot accidentally bypass run
discovery, durable dispatch ownership, or full-SHA confirmation.

The documented behavioral smoke is deterministic and network-free:

```text
make ci-trigger-committed-head EXAMPLE=1 REF=release/beta3-candidate REMOTE=sandboxcom REPO=sandboxcom/gludd WORKFLOW='Build and Release' DISCOVERY_POLLS=1 CONFIRM_POLLS=1 POLL_INTERVAL=0
```

The safe example must print both `GHA-SIGNAL-EXISTING` and an example
`GHA_RUN_URL`.

## All-workflow terminal verdict

Signalling one workflow is not a release verdict. After a push, use the
fail-closed collector for the exact remote branch tip:

```text
make pipeline-status PIPELINE_STATUS_REPO=sandboxcom/gludd PIPELINE_STATUS_BRANCH=development PIPELINE_STATUS_REMOTE=sandboxcom PIPELINE_STATUS_SHA= PIPELINE_STATUS_VALIDATE_ONLY=0
```

The collector resolves `refs/heads/<branch>` when no SHA is supplied, queries
all runs for that full commit with `gh run list --commit`, and chooses the
newest run independently for every required workflow. A release verdict is
green only when both `Build and Release` and `Molecule Tests` have a terminal
`success` conclusion for that exact SHA. A missing workflow, an active run, a
failed run, an empty remote ref, or a GitHub API error is a non-green result.
It never substitutes the newest run on the branch and never reduces the result
set to the first workflow returned by GitHub.

`make verify-state` delegates its CI section to this same collector, and
`make require-ci-green` consumes the collector's pure evaluator and fetch
adapter as the release precondition. This keeps interactive status, automation,
and release evidence on one implementation and prevents a status path from
claiming green while another required workflow is red.

The regression suite covers multiple workflows for one SHA, superseded runs,
missing and pending workflows, API failures, remote-ref resolution, and the
network-free validation contract. Its focused coverage gate is:

```text
make coverage-files COVERAGE_TESTFILES='tests/unit/test_pipeline_status_exact_sha.py tests/unit/test_require_ci_green.py tests/unit/test_require_ci_green_detect_branch.py' COVERAGE_CONFIG=config/coverage_pipeline_status.ini COVERAGE_REPORT=.gate-logs/coverage-pipeline-status.json COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=1 OBSERVED_QUIET_SECS=60 OBSERVED_MAX_SECS=300 OBSERVED_RETAIN_RUNS=20
```

## Long-lived platform findings

- A long-running [GitHub Community discussion about pushes from Actions not
  triggering subsequent workflows](https://github.com/orgs/community/discussions/25702)
  documents the `GITHUB_TOKEN` recursion restriction and continued user
  reports years later. `workflow_dispatch` is an explicit exception. Therefore
  the release path must observe a run and explicitly signal when absent; a
  successful push alone is not run evidence.
- Users also report [duplicate runs when multiple events cover the same
  commit](https://github.com/orgs/community/discussions/46775). Therefore the
  signal queries by full SHA before dispatching and gives the ordinary push
  event time to appear.
- Users report that `gh run list --branch` can be misleading when several
  workflows run for the same branch, and the CLI has a long-lived request for
  a single view spanning workflow runs and their jobs
  ([cli/cli#6221](https://github.com/cli/cli/issues/6221)). Therefore Gludd does
  not interpret the first list row as the branch verdict: it collects the
  newest exact-SHA run for every required workflow and reports each result.
- The GitHub CLI documents that [`gh workflow run` creates a dispatch and
  returns the created run URL when available](https://cli.github.com/manual/gh_workflow_run).
  It also documents that [`gh run list` supports `--commit` and exposes
  `headSha` and `url` JSON fields](https://cli.github.com/manual/gh_run_list).
  The implementation still confirms `headSha` rather than trusting branch
  identity or an older run URL.
