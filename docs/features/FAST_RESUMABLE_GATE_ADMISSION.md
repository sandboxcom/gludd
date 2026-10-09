# Fast, Resumable Gate Admission

## Outcome

The local full gate now fails before lint/integration/test when any preflight
fails, reuses only authenticated pass receipts from the exact current Git SHA,
and runs each cold batch through a pytest-xdist `loadfile` queue capped at two
workers. Hosted CI still invokes the serial runner without receipt admission or
the local two-worker override, so it remains the cold paired control.

The existing `scripts/run_ci_shards_serial.py` remains the only execution and
receipt owner. This slice does not add a second runner, an affected-test
selector, cross-commit reuse, or unbounded process fan-out.

## Admission and failure rules

- A receipt hit requires the same full candidate SHA, repository-state digest,
  canonical plan, 32-file-or-smaller batch bound, worker count/distribution,
  runner/toolchain/plugin/coverage identities, verified HMAC, passing normalized
  outcomes, valid branch-aware Coverage.py data, and completed owned cleanup.
- Missing, stale, unsigned, revoked, corrupted, ambiguous, or mismatched evidence
  is a cold miss. Coverage is copied atomically and rehashed before use.
- A failed batch stops later batches and shards immediately. Aggregate coverage
  is not run over a partial plan, but earlier independently passing receipts
  remain eligible for an exact-SHA retry.
- `--collect-all-failures` is an explicit diagnostic-only escape hatch. It
  continues only after classified ordinary Pytest outcomes whose coverage and
  cleanup evidence remain valid; worker death, cancellation, resource,
  coverage-integrity, and cleanup failures still stop immediately. The CLI
  rejects this mode together with `--require-release-policy`, so default local,
  hosted, and release-evidence execution remains fail-fast.
- SIGINT, SIGTERM, and gate-owner death produce one authenticated terminal failed
  status, mark the test phase failed, and release both gate locks. Failed status
  authentication never makes that status eligible for positive gate admission.
- Every terminal runner summary reports planned, executed, resumed, not-started,
  receipt-derived time saved, max files, worker count, and reconciliation.

## Bounded cold-run policy

`GATE_MAX_FILES_PER_BATCH=32` halves the prior process-start boundary while
remaining capped. `GATE_BATCH_WORKERS=2` uses xdist `loadfile`, keeping every
test file within one isolated worker process and disabling worker restarts.
The runner rejects worker values outside `1..2`; it still executes batches and
named shards serially, and the existing heavy-operation semaphore remains the
outer resource owner.

The acceptance SLA is a cold exact-head local gate at or below 90 minutes and a
same-SHA retry at or below 30 minutes. The pre-change control observed 250
16-file batches; after five batches its ETA was 5,468 seconds (about 91 minutes),
and an ordinary failure in batch 10 still launched batches 11 through 18 before
manual cancellation. Regression tests now pin the two-worker ceiling,
fail-fast launch count, and explicit saved-batch/time measurements. Production
wall-time promotion still requires retained medians, not a best run.

Coverage ownership is unchanged: every attempt recombines actual coverage
fragments and enforces at least 85% aggregate and 75% per production file.

## ZDD and rollback

This changes validation processes and project-namespaced evidence only. It
starts no serving process, listener, database migration, or release workload,
so old and new application instances require no traffic handoff. The gate lock
prevents overlapping local gates from mixing evidence.

Rollback is immediate and bounded:

```text
make gate GATE_EXACT_SHA_RESUME=0 GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1
```

This forces cold execution with the former batch and worker bounds; it does not
delete receipts or mutate a Git ref. Hosted CI already exercises the cold
one-worker path. Cache removal remains a separate owned operation performed
only with no active gate lease.

## Long-lived user evidence and follow-up

The two-slot cap retains Gludd's controller timeout and TERM-to-KILL ownership
because the pytest-xdist controller-timeout request
[#220](https://github.com/pytest-dev/pytest-xdist/issues/220) has remained open
since 2017. The 2016 xdist hang report
[#61](https://github.com/pytest-dev/pytest-xdist/issues/61) demonstrates that
even a two-worker run can stall before tests execute, so heartbeat, no-progress,
owner-death, and restart-disabled behavior remain mandatory.

Pants is a separate long-term follow-up for a hermetic, transitive-input,
content-addressed unit-test slice. It must use Pants' mature dependency/CAS
model; Gludd will not invent a custom cross-commit dependency walker. Pants user
reports about undeclared snapshot inputs
[#11622](https://github.com/pantsbuild/pants/issues/11622) and the need to force
cache bypass [#10379](https://github.com/pantsbuild/pants/issues/10379) are why
that work requires explicit inputs, a cold escape hatch, shadow node/coverage
parity, and a new promotion decision. Until then, receipts never cross a commit.
