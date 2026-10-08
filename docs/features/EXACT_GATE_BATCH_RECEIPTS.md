# Exact-Gate Batch Receipts

## Delivered phase

S83.179 implements the first, shadow-write phase of the
[exact-gate rerun acceleration decision](../research/EXACT_GATE_RERUN_ACCELERATION_DECISION_2026-10-07.md).
The canonical serial runner still executes every selected batch. It does not
restore coverage or skip a test. The later
[phase-two shadow auditor](EXACT_GATE_SHADOW_REPLAY_AUDIT.md) can inspect prior
receipts only after fresh execution and cleanup; it does not change this
writer's admission boundary. The
[shadow progress follow-up](EXACT_GATE_SHADOW_PROGRESS.md) renders bounded
estimate-only progress from the canonical plan and completed receipts. The
[non-reusable failure follow-up](EXACT_GATE_FAILURE_RECEIPTS.md) records only
sanitized diagnostics after executed failed batches and can never authorize a
skip. The [legacy-log import follow-up](EXACT_GATE_LEGACY_FAILURE_IMPORT.md)
can summarize one confined completed historical log without writing to the
receipt store or making its diagnostics reusable. The worker count remains one
and `MAX_FILES_PER_BATCH` remains 16. The
[receipt-authentication follow-up](EXACT_GATE_RECEIPT_AUTHENTICATION.md) adds
integrity classifications to these artifacts but keeps admission disabled.

After an executed batch returns zero, the runner preserves its Coverage.py
database, reduces its JUnit XML to content-free terminal outcome digests, and
removes both the compact pytest temporary root and batch workspace. Only then
may `scripts/ci_batch_receipts.py` atomically publish a candidate receipt.
Statement-only coverage, missing outcomes, cleanup failures, identity drift,
unsafe paths, corruption, and storage uncertainty produce a visible
`BATCH-RECEIPT-SHADOW status=refused` marker. A nonzero execution can produce a
separate sanitized failure receipt only after successful cleanup; that receipt
is explicitly non-reusable. A shadow refusal or failure receipt never becomes a
pass or suppresses execution.

`--no-shadow-batch-receipts` is the immediate rollback switch. It leaves the
existing runner, coverage aggregation, terminal attestation, and hosted lane
unchanged and writes no receipt. Dirty or non-exact worktrees also disable the
writer automatically.

## Content-addressed evidence

Receipts live outside the checkout under the existing project-namespaced
resource root:

```text
ci-shards/batch-receipts/v1/<40-character-git-sha>/<action-sha256>/
├── complete.json
├── coverage.data
├── manifest.json
├── outcomes.json
└── attestation.json (optional during unsigned-legacy migration)
```

The action directory is the canonical-JSON SHA-256 of the complete identity.
That identity binds the clean full Git SHA and repository-state identifier;
ordered test files; batch, shard, complete-plan, collection-plan, maximum-file,
and execution-policy digests; runner and attestation implementations; pytest
arguments and deadlines; cleanup policy; Python executable and installed
distribution fingerprint; UV, lock, and dependency-profile evidence; pytest
entry-point inventory and core plugin versions; Coverage.py branch settings;
platform and ABI; a non-secret environment allowlist; and the explicit empty
external-input declaration for this first phase.

The outcome artifact stores counts plus hashes of testcase identities and
terminal outcomes. It deliberately discards JUnit properties, stdout, stderr,
tracebacks, and testcase text, so a parameter or plugin cannot smuggle captured
credentials into the cache. Environment values outside `CI`, `GITHUB_ACTIONS`,
`PYTHONHASHSEED`, and `TZ` are never serialized; only the sorted-name-set digest
and a restricted-input-present bit are retained. No pickle or executable cache
format is accepted. Coverage remains Coverage.py's SQLite format and is opened
through `CoverageData` in branch-aware, read-only validation before and after
publication. Testcase names have a 65,536-character ceiling so legitimate
pytest-generated parameter IDs remain admissible; class and file identities
retain their 4,096-character limits, and the complete JUnit artifact retains
its 64 MiB limit.

The writer creates private `0700` directories and `0600` files, refuses direct
cache/generation symlinks, foreign owners, unsafe modes, special files, unknown
layouts, unsupported schemas, and digest or identity mismatches. It fsyncs each
artifact, writes the completion marker last, fsyncs the staging directory, and
renames the complete directory atomically. Immediate self-validation reads only
the receipt just staged; it is not cache admission and can never skip work.

## Resource and ZDD boundary

The cache admits at most two exact-SHA generation directories and at most 2 GiB
including the incoming conservative manifest allowance. The disjoint failure
namespace additionally caps each generation at 256 diagnostic receipts. When a
pass receipt for a third exact SHA arrives, the writer may retire only the
uniquely oldest complete inactive generation. It validates every pass and
failure receipt before considering retirement, keeps the newest existing
generation as the current rollback generation, and never selects the incoming
candidate. Equal modification times, empty generations, incomplete staging
directories, unknown children, malformed receipts, symlinks, special files,
unsafe ownership or modes, a busy mutation lock, and a changing tree all refuse
rollover without deletion. Failure-only generations remain eligible only when
every diagnostic is complete and valid; failure publication itself does not
evict a generation.

The pass and failure writers share the repository's maintained cross-platform
`filelock` implementation, acquired nonblockingly through an owner-only lock
file scoped to the private version directory, while they inspect and create
lifecycle paths. The native implementation refuses symlink/reparse-point
targets and preserves exclusion across POSIX and Windows. Retirement is also
entry-bounded at 16,384 paths. Before mutation, the pass writer subtracts only
the validated retirement candidate from the byte projection; if the new
receipt would still exceed 2 GiB, it leaves both generations untouched. It then
atomically renames the selected generation below a private quarantine
directory, rechecks the no-symlink tree identity, creates the incoming
generation while the quarantine blocks competing writers, and deletes only the
unchanged quarantined tree. A rename, recheck, candidate handoff, or deletion
failure refuses publication and restores the old generation when its complete
snapshot remains intact. A leftover quarantine is ambiguous layout and fails
closed on later writes rather than being silently repaired.

Before publication can delete a generation, callers can invoke
`ShadowBatchReceiptWriter.preview_retirement()` with the same complete request.
The result is a frozen, content-free `RetirementPreview` containing only a
decision (`retain`, `retire`, or `fail-closed`), a bounded reason, and a
retirement count of zero or one. It never returns a cache path, Git SHA, receipt
identity, environment value, or evidence payload. Preview and publication call
the same bounded rollover planner, so the two-generation and 2 GiB decisions
cannot drift into separate policies.

Preview does not create the cache, version directory, lock file, quarantine, or
candidate generation. When the lock file already exists, it uses `filelock`'s
nonblocking descriptor primitive to share the writer's native exclusion without
opening the lock through its mutating path API. It also compares bounded
before/after cache snapshots. Contention, a newly appearing lock, changed
evidence, unsafe lock identity, an ambiguous generation, or an entry-limit
failure therefore produces `fail-closed` without modifying the tree. Repeating
a preview against unchanged evidence returns the same decision.

This lifecycle does not start a daemon, open a listener, add a worker, change a
database, or alter a serving process. Existing action paths are never
overwritten, and a corrupt existing action path is refused rather than repaired
implicitly. Receipt reads still cannot admit a test result: the canonical
runner executes every selected batch, preserves fresh coverage, and reports
`skips=0`.

That makes rollout zero-downtime by construction: the feature writes optional
external validation evidence after successful execution and cleanup. Removing
or disabling the writer cannot change application traffic, the test plan, or
the current release result. Receipt admission, warm-run reconciliation,
and release-attestation consumption require later independently reviewed
phases. This continuation adds only safe retirement of complete inactive
generations so bounded shadow writes can continue. The other S83.179 follow-ups
add only a bounded post-execution eligibility report, estimate-only progress
summary, and sanitized non-reusable failure diagnostics, all with `skips=0`.

## Long-lived practitioner reports that shaped the boundary

The research decision evaluated mature tools before this application-specific
adapter was written. The user/maintainer issue history matters because each
report describes a failure mode that a release cache must not hide:

- [pytest-testmon issue 12](https://github.com/tarpas/pytest-testmon/issues/12),
  open since 2015, requests explicit non-Python file dependencies. Its longevity
  confirms that executed-code dependency discovery cannot represent Gludd's
  templates, policies, lockfiles, generated inputs, or external services.
- [pytest-xdist issue 220](https://github.com/pytest-dev/pytest-xdist/issues/220),
  open since 2017, requests a controller-side timeout, while
  [issue 61](https://github.com/pytest-dev/pytest-xdist/issues/61) records a 2016
  pre-test worker hang. Gludd therefore keeps its existing outer supervision and
  does not increase workers to obtain rerun speed.
- [pytest-split issue 100](https://github.com/jerry-git/pytest-split/issues/100)
  reported duplicated and omitted tests across groups in 2024. Stored durations
  can help scheduling, but they are not result evidence and still require exact
  node-set reconciliation.
- [pytest issue 6881](https://github.com/pytest-dev/pytest/issues/6881) tracked
  long automatically generated parameter IDs from 2020 through 2026, with
  explicit IDs as the longstanding workaround. Receipt normalization therefore
  accommodates bounded long pytest names instead of treating every name above
  4,096 characters as hostile, while still refusing names above 65,536.
- [Pants issue 10379](https://github.com/pantsbuild/pants/issues/10379) describes
  the operational need to force a cold rerun, and
  [issue 11622](https://github.com/pantsbuild/pants/issues/11622) shows how an
  undeclared data file disappears from a hermetic snapshot. Pants remains the
  preferred long-term pilot only after Gludd has declared those inputs.

These reports are not used to justify custom test execution. Pytest still owns
execution, Coverage.py still owns branch data and combination, and the serial
runner still owns process and cleanup boundaries. The new code is limited to
Gludd's exact-action receipt and resource policy, for which those tools do not
provide an immediately compatible release adapter.

## Verification ledger

The failing-first focused test initially reported the missing receipt module,
then separately reported the absent JUnit command boundary and runner session.
Later failing-first cases pinned special permission bits, empty branch-data,
duplicate JSON keys, ambiguous terminal outcomes, a second disk-reserve check
immediately before receipt writes, UV toolchain drift, and internally inexact
source identity. The bounded-rollover continuation first failed five focused
cases because every third exact SHA still returned `generation-limit`. The
repaired focused suite is 104/104 green and covers oldest-generation retirement,
current/candidate preservation, active and malformed evidence, equal-age
ambiguity, advisory-lock contention, pre-quarantine and post-rename races,
failure-only generations, symlink confinement, byte-budget proof, and rollback
after rename or deletion faults. It also covers deterministic content-free
preview decisions, absent-cache retention, read-only descriptor contention,
and before/after tree identity. The six-file receipt regression slice is
249/249 green. Its branch-aware report records 89% for
`scripts/ci_batch_receipts.py`; aggregate and branch coverage exceed 85%, and
the measured file exceeds 75%. Scoped Ruff, strict mypy, and Markdown lint are
green, and the feature-branch implementation is committed. Exact-head full-gate
evidence remains pending integration; no warm-hit or release-speed claim
belongs to this phase.
