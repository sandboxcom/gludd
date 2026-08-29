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
- The GitHub CLI documents that [`gh workflow run` creates a dispatch and
  returns the created run URL when available](https://cli.github.com/manual/gh_workflow_run).
  It also documents that [`gh run list` supports `--commit` and exposes
  `headSha` and `url` JSON fields](https://cli.github.com/manual/gh_run_list).
  The implementation still confirms `headSha` rather than trusting branch
  identity or an older run URL.
