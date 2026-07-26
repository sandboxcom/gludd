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

