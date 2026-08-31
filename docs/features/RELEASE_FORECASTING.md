# Evidence-Calibrated Release Forecasting

## Problem

A release estimate is not trustworthy when it adds fixed guesses, ignores
parallel lanes, or assumes the current failure is the last one. Gludd now uses
structured evidence from prior local and GitHub Actions (GHA) phases, current
fail-closed blockers, coverage and platform replay gaps, and artifact
dependencies.

The output remains part of the existing `release-readiness` JSON. It adds:

- an empirical P50 and P90;
- the exact dependency critical path;
- calibration sample counts and sources;
- current repairs ranked by expected risk reduction per repair minute;
- coverage and local-platform replay gaps;
- a bounded hosted canary ordered for minimum time to first failure.

The ranking is an engineering scheduling signal, not permission to bypass any
release gate. Every required test and all 28 artifact categories remain
mandatory.

## Upstream and user evidence

GitHub Community users report that per-job time is derived from the Actions jobs
API by comparing `started_at` and `completed_at`. Gludd therefore ingests
completed phase durations rather than estimating from the age of a run:
[GitHub Community discussion 59051](https://github.com/orgs/community/discussions/59051).

A separate GitHub Community report documents stale `started_at` and
`completed_at` values on rerun webhook events. A history producer must retain
the immutable run/attempt identity and reject incoherent timestamps instead of
merging attempts:
[GitHub Community discussion 161557](https://github.com/orgs/community/discussions/161557).

Users debugging nondeterministic Actions failures identify runtime, runner,
ordering, concurrency, and external-state differences as recurring causes.
Gludd records platform and Python version and reports a replay gap when a hosted
failure has no matching local replay:
[GitHub Community discussion 194567](https://github.com/orgs/community/discussions/194567).

Users also describe the time lost when a fast failure cannot be acted on until a
long workflow finishes. Gludd's canary plan moves high-value historical failure
nodes ahead of broad shards while retaining those shards:
[GitHub Community discussion 73156](https://github.com/orgs/community/discussions/73156).

This behavior follows pytest's established failed-first concept rather than
inventing a new test-ordering convention. Pytest documents `--failed-first`
and the fixture-setup tradeoff from reordering:
[pytest command-line reference](https://github.com/pytest-dev/pytest/blob/main/doc/en/reference/reference.rst).

## History schema

When `.gludd/release_forecast_history.json` exists in the invoking worktree,
`make release-readiness` loads it automatically. The CLI also accepts an
explicit `--history` path for isolated validation.

```json
{
  "schema_version": 1,
  "observations": [
    {
      "run_id": "gha-33345023078-unit-1b-attempt-1",
      "phase": "hosted_ci",
      "lane": "gha",
      "duration_minutes": 44.0,
      "succeeded": false,
      "failure_class": "unit-regression",
      "failing_node": "tests/unit/test_cloud.py::test_late_unit_1b",
      "node_order": 930,
      "total_nodes": 1000,
      "platform": "linux",
      "python_version": "3.11"
    }
  ]
}
```

Records are fail-closed:

- run ID, phase, lane, positive finite duration, and boolean outcome are required;
- lane must be `local`, `gha`, or `shared`;
- node order and total must appear together and form a valid range;
- successful observations cannot carry failure evidence;
- unknown phases and lane mismatches are rejected;
- only the most recent 500 records are loaded.

A producer must record one terminal attempt once. It must not turn an incomplete,
cancelled, or rerun attempt into a successful duration.

## Forecast method

For each phase:

1. P50 is the median of successful observed durations. Failed durations are
   used only when no successful duration exists.
2. P90 is the empirical nearest-rank 90th percentile. With fewer than three
   usable samples, the tail is at least 1.3 times P50.
3. Historical failure rate expands the tail but not the median.
4. Coverage gaps expand the local tail; missing local platform/Python replays
   expand the hosted tail.
5. Current repair work is a lower bound for its phase.

The release P50 and P90 are the longest paths through the stage dependency
graph. Local exact-SHA and hosted CI lanes remain parallel; neither is silently
added twice. The report preserves the previous
`local_dual_track+hosted_ci` compatibility label and also emits the concrete
execution path.

With no history, gaps, or blockers, the tracked stage baselines produce the
same 200-minute P50 and 260-minute P90 as the prior interface. This avoids fake
precision while history accumulates.

## Repair priority

For a current blocker, Gludd computes:

```text
expected risk reduction =
  remaining phase-to-release P90 * evidence-weighted recurrence
  + 5 minutes * transitively unlocked artifact nodes

priority value =
  expected risk reduction / estimated repair minutes
```

The recurrence starts at 0.5 for a known current blocker, then increases for a
matching historical failure class, coverage-gap files, and missing
platform/Python replays. It is capped at 1.0. The JSON includes the components
as human-readable reasons so an operator can audit why an item is early.

Artifact scoring is dependency-aware. Repairing a binary producer, for example,
can unlock checksum, smoke-attestation, and release-manifest work. The score
never marks any artifact complete.

Coverage-gap priority is supplemental to the hard coverage policy. A release
still requires at least 85% aggregate coverage and at least 75% in every
individual file.

## Hosted canary and time to first failure

Only prior failed GHA observations with an exact node, original order, platform,
and Python version enter the canary. Duplicate nodes are grouped. The planner
computes:

```text
expected minutes saved =
  (median historical minutes to failure - estimated isolated node minutes)
  * observed recurrence
```

Candidates are ordered by expected saved minutes, then original lateness, then
stable lexical identity. The default canary is bounded to five nodes and the
hard API maximum is 20. The emitted `canary_order` is the order a hosted
consumer runs before broad shards. Broad shards still run and remain release
evidence; canary success is never substituted for them.

The regression suite pins a historical unit-1b failure at node 930 as canary
number one ahead of an early unit-1a failure.

## ZDD rollout

The rollout is zero-downtime (ZDD):

1. The existing `estimate.p50_minutes`, `p90_minutes`, `critical_path`, and
   `stages` keys remain present.
2. New fields are additive. Older consumers can ignore them.
3. An absent history file preserves baseline behavior.
4. A canary runs before broad shards but does not remove, rename, or weaken a
   required check.
5. Forecast calculation is read-only and does not launch processes, mutate
   release state, or clean another worktree.

The feature uses bounded in-memory dictionaries and the Python standard library.
It opens at most one history file, retains at most 500 observations, and plans
at most 20 canary nodes. It starts no daemon, model server, container, or GHA
poller.

## Rollback

Rollback is data- and schema-safe:

- move aside or stop producing `.gludd/release_forecast_history.json` to return
  immediately to tracked baselines;
- configure a consumer to ignore the additive priority and canary fields;
- revert the library/script commit if the formula itself must be withdrawn.

No rollback deletes test evidence, cancels hosted runs, changes a tag, removes
an artifact, or edits the task ledger. Exact-SHA local and hosted gates remain
the release authority throughout rollout and rollback.
