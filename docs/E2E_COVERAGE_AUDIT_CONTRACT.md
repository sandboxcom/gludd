# E2E Coverage Audit Contract

This document defines the evidence required before Gludd can claim that its
end-to-end (E2E) suite covers Python control-flow branches.  A test count or a
regular-expression scan of source files is not a coverage measurement.

## Thresholds and metric

`make audit-coverage` is the canonical audit target.  It instruments the E2E
run with `coverage.py` branch tracking, executes the configured shards, combines
their data, and emits a machine-readable aggregate report.

The report is acceptable only when all of these conditions hold:

* aggregate E2E branch coverage is at least **85%**;
* every measured source file is at least **75%** covered (the project target is
  85% wherever practical); and
* every shard completes successfully, with no collection, timeout, or worker
  error omitted from the report.

Branch coverage counts both outcomes of a conditional edge.  It is distinct
from statement/line coverage: executing the line containing an `if` does not
prove that both its true and false paths ran.  Tests must assert the behavior
of each path, rather than merely execute code for metric padding.

## Required evidence

An audit result is valid only when its JSON contains the following evidence:

* `e2e_branch_totals` and `e2e_branch_coverage` (covered/total and percentage);
* line coverage and the per-file threshold verdicts;
* one result for every discovered E2E shard, including pass/fail status and
  the tests assigned to that shard; and
* failure details for every failed shard, even when a shard exits before it can
  write a normal coverage JSON file.

The aggregate JSON and its source commit must be retained with the gate logs.
If a shard fails, the aggregate percentage is **not** a release result: fix or
quarantine the failure with an explicit task, rerun the complete audit, and
replace the stale report.

### Durable in-flight progress

While shards are running, the runner writes an atomic sidecar next to the
requested aggregate path: `<aggregate>.progress.json`.  The sidecar is the
auditable source for partial-run state and is intentionally not an aggregate
coverage result.  It contains:

* `schema_version`, `run_id`, `pid`, `started_at`, and `updated_at` so an
  operator can correlate the file with one process and one invocation;
* `current_index` and `total`, plus a stable relative `path` for every E2E
  file; each shard summary includes the resolved environment namespace used
  for resource isolation; and
* `counts.attempted`, `counts.passed`, `counts.failed`, and `counts.skipped`,
  with per-file states (`pending`, `running`, `passed`, `failed`,
  `timed_out`, or `skipped`). Completed or failed shards also include ordered
  per-test diagnostics from JUnit XML (`nodeid`, status, and bounded failure
  message/text), so a failing file can be triaged without rerunning it merely
  to discover which test ran first.

The sidecar has `complete: false` for every interrupted, timed-out, or failed
run.  Remaining files are marked `skipped` with a reason when a failure stops
the serial audit.  Only after the final `coverage json` command succeeds is
`status` set to `completed` and `complete` set to `true`; a sidecar must never
be treated as proof of a branch percentage.  Updates use a temporary file and
an atomic replace, so readers never observe truncated JSON.  This makes a
stale or interrupted audit distinguishable from a certified aggregate even if
the process is terminated before the normal failure report is written.

## Subprocess and shard handling

E2E tests launch subprocesses and may run in parallel.  Coverage data therefore
must use unique per-process files and be combined only after all workers exit.
Never infer coverage from a partial `.coverage` file or from a run that was
interrupted.  The audit runner records shard progress and keeps failed-shard
reports so a connector or lifecycle regression cannot silently disappear.

This requirement follows long-lived coverage.py guidance: subprocess
measurement needs parallel data files and an explicit combine step ([coverage.py
subprocess documentation](https://coverage.readthedocs.io/en/7.14.1/subprocess.html)).
The same failure mode is regularly reported by practitioners: a user forum
discussion warns that a high percentage can still be meaningless when tests do
not exercise both outcomes of a branch ([Reddit discussion on meaningful
coverage](https://www.reddit.com/r/ExperiencedDevs/comments/1cgucr6/is_anyone_having_a_hell_of_a_time_with_codecov/)).

## Review checklist

Before recording a percentage in `TASKS.md` or a release note:

1. Run `make audit-coverage` from a clean, namespaced workspace.
2. Confirm the discovered E2E file/function counts and that every shard has a
   terminal pass result.
3. Inspect `e2e_branch_totals`, `e2e_branch_coverage`, and each per-file verdict
   in the aggregate JSON.
4. Resolve every failed shard and every file below 75%; do not lower thresholds
   or edit tests only to make the number pass.
5. Record the report path, commit SHA, command, and gate output beside the task
   evidence.  A missing or stale aggregate report means the coverage claim is
   unverified.
