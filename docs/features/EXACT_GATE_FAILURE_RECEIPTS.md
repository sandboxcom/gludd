# Exact-Gate Non-Reusable Failure Receipts

## Delivered shadow boundary

The S83.179 failure-observability follow-up records a bounded diagnostic receipt
after an executed batch returns nonzero. These receipts are explicitly
**NON-REUSABLE**: they cannot authorize a skip, restore coverage, become a pass,
or contribute timing samples to an ETA. The serial runner still executes every
selected batch with one worker and at most 16 files per fresh process.
The [receipt-authentication follow-up](EXACT_GATE_RECEIPT_AUTHENTICATION.md)
can verify diagnostic integrity, but a verified failure remains non-reusable.
The [legacy-log importer](EXACT_GATE_LEGACY_FAILURE_IMPORT.md) can reduce one
completed historical log to the same non-reuse posture and aggregate counts,
but writes no receipt-store artifact and never promotes legacy evidence.

Publication is allowed only after the runner has reobserved the exact expected
action identity, completed owned temporary-root and workspace cleanup, and
passed its existing disk-reserve check. A missing or unreadable JUnit artifact
does not invite unsafe parsing: the receipt records bounded `unavailable`
metadata. Incomplete cleanup, identity drift, a zero return code, malformed
metadata, an unsafe path, corruption, or storage uncertainty refuses the write
and leaves the executed failure authoritative.

`--no-shadow-failure-receipts` is the narrow rollback switch. It leaves pass
receipt writes, replay reports, progress, Pytest, Coverage.py, cleanup, and the
terminal result unchanged. `--no-shadow-batch-receipts` disables the entire
shadow receipt session. Both modes preserve `skips=0`.

## Content-addressed and sanitized evidence

Failure receipts share the project-owned receipt root but occupy a disjoint
namespace:

```text
ci-shards/batch-receipts/v1/<40-character-git-sha>/failures/<failure-sha256>/
├── attestation.json (optional during unsigned-legacy migration)
├── complete.json
├── failure.json
└── manifest.json
```

The content address binds the exact action digest, full candidate SHA, shard,
batch ordinal, canonical test-file digest, collection-manifest digest, stable
failure class, elapsed time, bounded failing-node metadata, and a SHA-256 of the
owned run identifier. It never stores the run identifier itself. Stable classes
cover test failure, interruption, Pytest internal or usage errors, no tests
collected, and an otherwise nonzero runner result.

Elapsed time is finite, nonnegative, rounded to milliseconds, and capped at 24
hours. At most 32 failed/error nodes are retained as SHA-256 identities plus the
two-value outcome class. The receipt also records the total failure count, a
digest across all failed/error identities, and a truncation bit. It does not
retain test names, filenames, arbitrary paths, parameters, XML text, captured
output, tracebacks, exception bodies, environment values, secret values, or
test payloads. JSON is schema-allowlisted and canonical; pickle and executable
cache formats are not accepted.

Directories are private `0700` and files are `0600`. The writer refuses
symlinks, foreign ownership, permissive modes, special files, duplicate JSON
keys, unknown artifacts, invalid content addresses, digest mismatches, and
malformed completion markers. It writes artifacts into a private staging
directory, fsyncs them, writes completion last, validates the staged receipt,
atomically renames it, and validates the published result. A partial staging
tree is removed on failure; existing corrupt evidence is refused rather than
overwritten.

## Replay, progress, and terminal semantics

The shadow replay auditor strictly validates failure receipts while indexing a
candidate generation. Any exact or related prior failure is classified
`prior-failure-non-reusable`, with `eligible=false` and
`skip_authorized=false`. A failure receipt vetoes eligibility even if a pass
receipt for the same coordinate also exists; corruption or ambiguity refuses
the whole snapshot. This prevents stale or contradictory evidence from being
reported as a safe candidate while retaining the report-only `skips=0` model.

Progress records an executed failed batch as `failed=1` and
`failure_receipts=1` only when the diagnostic receipt was safely recorded. It
does not count that receipt as pass, missing, or replay-eligible, and failure
elapsed time is not an ETA sample. `gate_result` remains `unknown`,
`overall_green` remains `null`, and `terminal_phases_complete` remains `false`.
Coverage combination, isolated phases, cleanup, repository stability, and the
terminal attestation still own the final result.

## Resource bounds, ZDD, and retention

Failure evidence shares the existing two-generation and 2 GiB total cache
ceiling. Each generation admits at most 256 failure receipts. The replay index
still caps all pass and failure candidates together at 512 receipts and 16 MiB
of manifests, so adding diagnostics cannot widen its memory or traversal bound.
When any ceiling is reached, publication or indexing fails closed and normal
execution/result handling continues.

The existing generation lifecycle applies to both namespaces: an active
generation is never implicitly pruned, no receipt is overwritten, and removal
remains a separately owned validate/apply operation when no gate lease is
active. This slice starts no daemon, listener, database, worker, thread, or
background cleanup process; changes no application traffic or release asset;
and does not increase batch size or concurrency. Disabling the writer or
removing an inactive versioned cache therefore preserves zero downtime.

## Mature-tool and practitioner evidence

Pytest remains the executor, Coverage.py remains the branch-data owner, and the
serial runner remains the process supervisor. The custom layer only expresses
Gludd's exact diagnostic, privacy, and resource policy. Long-lived upstream
reports explain why these observations remain shadow-only:

- Pytest [issue 11107](https://github.com/pytest-dev/pytest/issues/11107) and
  [issue 6399](https://github.com/pytest-dev/pytest/issues/6399) document
  root-directory and relative-node-ID ambiguity. Failure identities are
  therefore hashed from the runner's actual JUnit observations, never rebuilt
  from a displayed filename or emitted as arbitrary paths.
- [pytest-xdist issue 220](https://github.com/pytest-dev/pytest-xdist/issues/220)
  has tracked controller-side timeout needs since 2017. A prior failure record
  cannot replace fresh supervision, heartbeat, cancellation, or cleanup.
- [Pants issue 17958](https://github.com/pantsbuild/pants/issues/17958) reports
  dependency work occurring even when a test result was cached. Failure timing
  is therefore diagnostic only and excluded from pass-receipt ETA samples.
- [Pants issue 5755](https://github.com/pantsbuild/pants/issues/5755) records a
  cache map-size exhaustion failure. Fixed node, receipt, index, generation, and
  byte ceilings refuse new evidence instead of retaining an unbounded corpus.
- [pytest-split issue 100](https://github.com/jerry-git/pytest-split/issues/100)
  reported duplicated and omitted tests across duration groups. Neither failure
  counts nor timings may alter Gludd's canonical plan.

These reports favor bounded observability, not failure reuse. Admission still
requires separate cold controls, exact node-plan reconciliation, fresh
aggregate branch coverage, terminal attestation composition, and an explicit
reviewed promotion decision.

## Verification ledger

The failing-first focused test initially failed collection because the failure
receipt API did not exist. A later red case proved that a valid pass receipt
could initially hide a coexisting failure receipt; the auditor now gives the
failure a hard non-reusable veto. Tests pin sanitation and truncation, stable
classes, unavailable metadata, identity and cleanup refusal, strict corruption
validation, unsafe layouts, atomic cleanup, shared retention ceilings, replay
refusal, nonterminal progress, cleanup ordering, and independent rollback.

The repaired failure suite passes 41/41. The eight-file serial focused slice
passes 343/343. Branch-aware Coverage.py reports 88% aggregate across
`ci_batch_receipts.py` at 88%, `ci_batch_replay_audit.py` at 85%,
`ci_gate_progress.py` at 92%, and `run_ci_shards_serial.py` at 88%; no measured
file is below the 75% floor. Full-gate, commit, merge, and push evidence remain
outside this shadow-only slice while the canonical gate owns the repository.
