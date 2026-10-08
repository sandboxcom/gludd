# Exact-Gate Shadow Progress and ETA

## Delivered phase

The S83.179 observability follow-up adds a bounded machine-readable progress
snapshot to the existing pass-only receipt writer and report-only replay audit.
The serial runner emits one `GATE-PROGRESS-SHADOW` JSON object after each batch
that reached execution, Coverage.py preservation, outcome normalization, and
owned cleanup. It does not add a worker, change the 16-file batch limit, admit a
receipt, restore coverage, or skip a test. Every snapshot states `skips: 0`.

This is deliberately not a terminal gate verdict. `gate_result` remains
`unknown`, `overall_green` remains `null`, and `terminal_phases_complete`
remains `false`, including after every planned batch has executed and passed.
Coverage combination, the per-file audit, isolated phases, cleanup, repository
stability, and terminal attestation still own the final result.

The [receipt-authentication follow-up](EXACT_GATE_RECEIPT_AUTHENTICATION.md)
accepts timing samples only from `verified` pass envelopes. Its fixed
authentication status distinguishes unsigned legacy, unknown, tampered,
revoked, expired, and malformed evidence without exposing receipt content.

## Fixed progress model

`scripts/ci_gate_progress.py` receives the canonical batch plan already built by
the serial runner. It records two independent dimensions for each completed
batch:

- execution is `passed` or `failed`; and
- the prior-receipt audit is an exact `receipt_candidate`, `missing`, or
  `ineligible` because auditing was disabled or evidence was refused.

The [failure-receipt follow-up](EXACT_GATE_FAILURE_RECEIPTS.md) adds a fourth
receipt status, `failure`, only when a sanitized non-reusable diagnostic was
published for an executed failed batch. It never changes execution status or
becomes a prior candidate.

The JSON `progress` object reports `executed`, `passed`, `failed`, `remaining`,
`total`, `receipt_candidates`, `failure_receipts`, `missing`, and `ineligible`.
These counts cannot authorize control flow. A failure receipt is excluded from
the ineligible and timing counts because it is a separate diagnostic result.
The runner reports progress only after its receipt audit and optional
publication, then continues through the same failure, coverage, cleanup, and
terminal paths as before.

The ETA is explicitly labeled `eta_kind: "estimate"`. It is derived only from
validated start and completion timestamps in completed shadow receipts for the
same exact candidate and canonical plan. A known remaining coordinate uses its
own duration; an unknown remaining coordinate uses the median of retained valid
durations. With no trusted duration, `eta_seconds_estimate` is `null`. Live
process heuristics, failure-receipt elapsed time, filenames, node text, log
volume, and unexecuted results are never treated as timing evidence.

## Fail-closed evidence and privacy

The tracker reuses strict canonical JSON, SHA-256 action identity, private-path,
ownership/mode, completion, artifact-digest, outcome, and branch-aware
Coverage.py validation from the receipt layer. Candidate SHA, test-file digest,
collection-manifest digest, shard, and batch ordinal must match the known plan.
A malformed receipt, mixed candidate, foreign plan, ambiguous action for one
coordinate, unsafe path, or evidence count beyond the bound rejects the entire
timing basis. It never calculates an ETA from the remaining subset.

Output contains aggregate counts, the full candidate SHA, a bounded evidence
status, and timing metadata. It contains no receipt path, test filename, node
ID, stdout, stderr, environment value, credential, or serialized exception.
The tracker reads JSON and Coverage.py's existing SQLite format; it neither
accepts nor writes pickle or another executable cache format.

## Resource, observability, ZDD, and rollback

The canonical plan and receipt snapshot are each capped at 512 batches. At most
64 duration samples are retained, each accepted duration is between zero and 24
hours, and canonical output is capped at 4,096 bytes. These bounds remain inside
the existing two-generation/2 GiB receipt store. The tracker starts no daemon,
listener, process, thread, database, or background cleanup job.

One JSON line per completed execution makes progress observable without an
unbounded log. Evidence rejection is visible as a bounded `evidence_status` and
a null ETA. When replay auditing is disabled, the tracker performs no historical
receipt read and may learn only from receipts completed by the current run.

`--no-shadow-gate-progress` is the independent rollback switch: receipt writes
and replay reports remain enabled while progress snapshots stop. Disabling the
writer disables progress because there is no owned receipt session. Either
rollback leaves the test plan, worker count, coverage path, terminal result,
application traffic, serving processes, and release assets unchanged. Removing
cached data remains a separate owned operation; the progress reader never
prunes or repairs it.

## Mature-tool and practitioner evidence

Pytest remains the executor, Coverage.py remains the branch-data owner, and the
serial runner remains the supervisor. The custom code is restricted to Gludd's
content-addressed progress policy. Long-lived upstream issue discussions explain
why this phase stays an estimate and shadow-only:

- [Pants issue 17958](https://github.com/pantsbuild/pants/issues/17958) reports a
  Docker image pull even when the test result was cached. Dependency acquisition
  can dominate elapsed time independently of a batch result, so a receipt ETA
  cannot promise completion or resource savings.
- [pytest-xdist issue 220](https://github.com/pytest-dev/pytest-xdist/issues/220)
  has tracked controller-side timeout needs since 2017. A duration median cannot
  replace the runner's heartbeat, no-progress deadline, or process cleanup.
- [pytest-split issue 100](https://github.com/jerry-git/pytest-split/issues/100)
  reported duplicated and omitted tests across duration-based groups. Gludd uses
  durations only for display and retains its canonical plan unchanged.
- [Pants issue 5755](https://github.com/pantsbuild/pants/issues/5755) records a
  cache map-size exhaustion failure. Fixed plan, sample, output, generation, and
  byte ceilings therefore fail closed instead of silently sampling a partial
  cache.

These practitioner reports support observability, not admission. Replacing even
one execution still requires cold controls, exact plan reconciliation, fresh
aggregate coverage, terminal attestation composition, and a separately reviewed
promotion decision.

## Verification ledger

The failing-first focused test initially failed collection because
`scripts.ci_gate_progress` did not exist. Additional tests pin estimate-only
semantics, independent execution and receipt classifications, malformed and
mixed-candidate refusal, ambiguity, plan/sample/output bounds, privacy, exact
runner emission, and the independent rollback. A later failing-first case proved
that a newly published receipt initially could be associated with the wrong
executed coordinate; the tracker now rejects that mismatch. The repaired focused
progress suite passes 34/34, and the replay-audit suite including
post-publication ordering passes 39/39. Branch-aware Coverage.py reports 92% for
`scripts/ci_gate_progress.py`, above both the 85% aggregate and 75% per-file
floors. Scoped Ruff and strict mypy, Markdown lint, task integrity, and ledger
validation are green. Full-gate, commit, merge, and push evidence remain outside
this shadow-only slice while the canonical gate owns the repository.
