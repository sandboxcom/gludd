# Exact-Gate Shadow Replay Audit

## Delivered phase

The S83.179 phase-two follow-up adds a report-only eligibility audit on top of the pass-only receipts
from [S83.179](EXACT_GATE_BATCH_RECEIPTS.md). It is deliberately not receipt
admission: the serial runner still starts Pytest for every selected batch,
retains one worker and the 16-file batch limit, captures fresh branch coverage,
normalizes fresh JUnit outcomes, and completes owned cleanup before asking what
the prior receipt *would* have allowed. Every marker contains `skips=0`.
The later [shadow progress follow-up](EXACT_GATE_SHADOW_PROGRESS.md) consumes
only this bounded snapshot plus newly completed receipts and remains equally
unable to skip work or claim a terminal result. The
[failure-receipt follow-up](EXACT_GATE_FAILURE_RECEIPTS.md) adds a sanitized
diagnostic namespace whose entries are always audit refusals, never candidates.
The [authentication follow-up](EXACT_GATE_RECEIPT_AUTHENTICATION.md) further
requires a `verified` pass envelope for the future-eligible label and reports
all other trust states as bounded refusals.

At runner setup, `scripts/ci_batch_replay_audit.py` takes a bounded snapshot of
the prior receipt generation for the clean, full candidate SHA. Receipts written
later by the current run cannot become their own comparison control. After a
batch executes, the auditor reports one of:

- `status=candidate` only when the prior identity, node set, terminal outcomes,
  and semantic branch coverage exactly match the fresh execution;
- `status=miss` when no prior receipt exists for that canonical batch; or
- `status=refused` for drift, ambiguity, dirty input, corruption, unsafe paths,
  invalid current evidence, or resource-bound uncertainty.

The result type hard-codes `skip_authorized=False`, and the runner ignores it
for control flow. A defensive runner check converts any incompatible auditor
that requests a skip into `reason=skip-authorization-forbidden`. This phase does
not copy a coverage fragment, suppress Pytest, alter resume state, compose an
attestation, or claim a warm-run speedup.

A strictly valid prior failure receipt returns
`reason=prior-failure-non-reusable`, including when a pass receipt for the same
batch also exists. Malformed failure evidence, an unsafe failure namespace, or
ambiguous failure coordinates refuse the complete snapshot as corrupt. Failure
elapsed time is not exposed as pass-receipt timing evidence.

## Exact and fail-closed comparison

The auditor reuses the receipt writer's strict canonical JSON, SHA-256,
ownership/mode, content-address, duplicate-key, outcome, and Coverage.py
validators. It accepts only the current `v1/<full-git-sha>` generation and
refuses an unsafe cache root, a symlink, a special or foreign path, an unknown
generation name, more than two generations, an invalid completion marker, or
any corrupt candidate. It never deserializes pickle and never stores logs,
captured output, environment values outside the existing allowlist, or secret
values.

The current expected identity is compared with a fresh post-execution identity.
Source/candidate, plan/command, dependency/plugin, environment, platform,
Coverage.py configuration, and declared-input drift receive separate bounded
reasons; changes in multiple families are treated as ambiguous. Dirty,
non-exact, failed Git observations and restricted environment inputs refuse the
audit. A malformed identity cannot degrade into a cold-miss claim.

For an exact action digest, the auditor validates the receipt again after its
initial index so a later mutation is refused. It compares the normalized node
ID digest before terminal outcomes. Coverage equivalence is computed through
Coverage.py from sorted measured filenames, file-tracer identities, and sorted
branch arcs, rather than comparing SQLite bytes. The semantic walk is capped at
2,000,000 file-plus-arc items and detects database mutation across the read.

## Bounds, rollback, and ZDD

The snapshot admits at most 512 pass and failure receipt manifests together and
16 MiB of manifest index data; each manifest is at most 256 KiB. These read
bounds sit inside the writer's existing two-generation/2 GiB storage cap and
the per-generation 256-failure ceiling. Exceeding a bound refuses the whole
snapshot rather than selecting a partial candidate. Audit output is one
content-free line per executed batch and never includes receipt contents or
paths.

`--no-shadow-replay-audit` is the narrow rollback: it keeps phase-one pass-only
writes while disabling every prior-receipt read. The existing
`--no-shadow-batch-receipts` disables both writing and auditing. Neither mode
changes application traffic, databases, listeners, release assets, worker
count, batch size, or the paired hosted lane. Cache removal remains a separate
owned operation; this reader never prunes or repairs evidence.

Disabling replay auditing does not require disabling the progress renderer. In
that mode progress performs no historical read and can use only receipts
completed by the current run. `--no-shadow-gate-progress` disables the renderer
without changing either the writer or this audit.

Shadow-only remains required because one synthetic exact match proves the
validator, not the safety of replacing thousands of real executions. Promotion
still needs repeated clean-run parity, canonical node-plan reconciliation,
fresh aggregate Coverage.py combination, exact admission attestation, forced
cold controls, and measured resource/latency evidence. Until those independent
gates exist, an eligibility report cannot become a pass.

## Mature-tool and practitioner evidence

The implementation keeps mature owners in place: Pytest executes, Coverage.py
reads branch data, canonical JSON supplies portable manifests, and the existing
serial runner owns process supervision and cleanup. The small custom layer is
limited to Gludd's exact-action and resource policy. Long-lived upstream reports
reinforce why a cache hit cannot be inferred from a digest alone:

- [Pants issue 19579](https://github.com/pantsbuild/pants/issues/19579) reports
  cached results surviving a `pants.toml` change. Configuration therefore stays
  in Gludd's action identity and any difference is a refusal.
- [Pants issue 17958](https://github.com/pantsbuild/pants/issues/17958) reports a
  Docker image pull even when a test result was cached. Cache eligibility and
  dependency acquisition are distinct lifecycle concerns, so this phase makes
  no resource-saving claim and performs normal execution first.
- Pytest [issue 11107](https://github.com/pytest-dev/pytest/issues/11107) and
  [issue 6399](https://github.com/pytest-dev/pytest/issues/6399) document
  root-directory and relative-node-ID ambiguity. The auditor compares node-set
  digests produced by the same canonical runner instead of reconstructing IDs
  from filenames.
- [Coverage.py issue 1840](https://github.com/nedbat/coveragepy/issues/1840)
  describes fragile cross-environment path remapping. The audit therefore
  requires an exact coverage configuration/platform identity and compares
  Coverage.py semantics only within that identity.
- [Pants issue 5755](https://github.com/pantsbuild/pants/issues/5755) records an
  LMDB map-size exhaustion failure. Gludd retains explicit candidate, manifest,
  generation, and byte ceilings and refuses partial indexes.

These reports favor a cautious Pants shadow pilot for a future hermetic slice;
they do not justify replacing Gludd's current release execution with an
unobserved cache admission.

## Verification ledger

The first focused test failed at collection because the replay-audit module did
not exist. Runner integration tests then failed on the absent auditor session
field and unknown rollback option. A separate failing-first case proved that a
malformed plan identity initially degraded to a miss; the shared identity
validator now refuses it as invalid.

The repaired phase-two suite passes 39/39. The current integrated report covers
`scripts/ci_batch_replay_audit.py` at 85% branch-aware coverage, meeting the 85%
aggregate and 75% per-file floors. It pins exact candidates without skip
authorization; candidate, dependency, command, environment, node-ID, outcome,
and semantic-coverage drift; dirty/restricted inputs; corruption and duplicate
keys; ambiguous receipts; unsafe/symlinked/unbounded trees; bounded cold misses;
post-index mutation; cleanup ordering; and the independent rollback. Phase-one
regression remains 64/64 green. Full exact-head gate and commit evidence remain
deferred while the canonical gate owns the repository.
