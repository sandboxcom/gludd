# Exact-Gate Legacy Failure-Log Import

## Delivered diagnostic boundary

The S83.179 legacy-import follow-up reads one explicitly named, completed gate
log below one explicitly named allowed root. It produces only a bounded JSON
diagnostic: aggregate batch/phase totals and sanitized failure receipts marked
`NON-REUSABLE`. It never writes to the source, enters the pass-receipt store,
restores coverage, changes the canonical plan, or authorizes a test skip.
`skip_authorized=false` and `skips=0` are fixed output invariants.

The motivating `gate-20261007204256-a198e742.log` incident contained eight
planned shard families and five executed failure markers: two in one shard
class, two in a second, and one in a third. The import produced five distinct
content-addressed diagnostic receipts and aggregate totals while withholding
the run identifier, shard labels, batch coordinates, source path, test names,
node IDs, captured output, and arbitrary log lines. A read-only acceptance test
against that exact completed source passed and proved its bytes and stat fields
were unchanged.

This is a migration and incident-triage bridge, not receipt admission. Fresh,
authenticated shadow writers remain the only source of current receipt
envelopes, and even their verified pass receipts are still report-only. Legacy
diagnostics cannot be promoted, signed after the fact, used as ETA samples, or
made future-eligible by another component.

## Strict parsing and refusal rules

`scripts/ci_legacy_gate_log_import.py` reuses the existing confined-log path
helper, canonical JSON/SHA-256 primitives, and stable return-code classes. Its
regular expressions are fixed, anchored, and length bounded. The importer
accepts only evidence with all of these relationships intact:

- exactly one bounded run identity, one serial summary, a failed terminal
  marker, and a matching final watcher-completion marker;
- one primary numeric phase result for every batch coordinate and an exact
  union with the pass/failure markers;
- matching return codes, failure count, and distinct-shard total; and
- strict UTF-8, a terminating newline, a stable source device/inode/mode/size/
  modification tuple across the read, and no duplicate or ambiguous evidence.

It fails closed on an absent or non-regular source; symlinks, traversal, or a
path outside the allowed root; source mutation or I/O failure; malformed,
truncated, mixed-run, mixed-terminal, duplicate, incomplete, or contradictory
markers; and any configured or observed bound violation. Unknown ordinary log
lines are never interpreted or copied, so a traceback, secret, path, or test
payload cannot become structured output merely by resembling human text.

The hard ceilings are 64 MiB per source, 250,000 lines, 1 MiB per line, 64
failure receipts, and 32 KiB of rendered JSON. Configuration may only narrow
those ceilings. Output contains the source byte/line counts and SHA-256,
aggregate batch/phase counts, the planned-shard count, and fixed-schema failure
receipts. Each failure receipt contains only a digested batch identity, stable
failure class, schema/kind, non-reuse policy, skip refusal, and its own canonical
digest. No pickle, XML payload, exception body, environment value, credential,
raw identifier, or arbitrary filesystem path is accepted into output.

## Resource, privacy, ZDD, and rollback

Import is synchronous and read-only. It creates no worker, daemon, listener,
database, gate lease, subprocess, temporary copy, cache generation, or cleanup
job; it does not widen the one-worker/16-file execution boundary. All input,
parsed evidence, receipt count, and output are capped before a report is
accepted. The source is opened only for a bounded read and is revalidated after
that read. Generated JSON may be discarded without touching the source or the
live receipt namespace.

Zero-downtime rollback is therefore to stop invoking the standalone importer
and discard its diagnostic output. No application process, release artifact,
canonical gate result, or receipt store needs migration or restoration. If a
future migration needs to retain imported reports, it must introduce a separate
owned namespace and retention decision; this slice deliberately writes none.

## Mature-tool and practitioner evidence

The implementation does not replace Pytest, Coverage.py, or the serial runner,
and it does not invent a general log parser. It reuses the project's confined
log-path and canonical-digest owners, then recognizes only Gludd's small fixed
marker grammar. Long-lived practitioner reports explain the conservative
diagnostic-only ruling:

- GitHub CLI [issue 4575](https://github.com/cli/cli/issues/4575) describes
  completed workflow logs being unavailable through an expected retrieval
  path. A retained log is useful migration evidence, but availability and
  lifecycle do not make it a current execution receipt.
- Pytest [issue 6399](https://github.com/pytest-dev/pytest/issues/6399) and
  [issue 11107](https://github.com/pytest-dev/pytest/issues/11107) document
  root-directory and relative-node-ID ambiguity. The importer therefore never
  reconstructs reusable nodes from displayed paths or test names.
- Coverage.py [issue 1840](https://github.com/nedbat/coveragepy/issues/1840)
  records cross-environment path-remapping failures. Legacy text cannot restore
  semantic branch data or establish current environment identity.
- Pants [issue 115](https://github.com/pantsbuild/pants/issues/115) discusses
  cache poisoning and invalidation. Imported failure observations are kept
  disjoint from pass evidence and are unconditionally non-reusable.
- Pants [issue 5755](https://github.com/pantsbuild/pants/issues/5755) reports
  cache map-size exhaustion. Fixed byte, line, failure, and output ceilings
  reject oversized evidence rather than partially importing it.

These reports favor a bounded forensic summary, not a new source of release
truth. Receipt admission still requires fresh exact identity, authenticated
envelopes, current node/outcome and branch-coverage reconciliation, cleanup,
terminal attestation, cold controls, and a separate reviewed decision.

## Verification ledger

The failing-first focused run stopped at collection because the importer module
did not exist. The first branch-aware coverage run then exposed missing
adversarial refusal cases at 79%; new tests pinned every configuration ceiling,
marker ambiguity and cross-check, unavailable/empty/oversized/invalid-encoding
sources, and the sanitized CLI boundary. The repaired suite passes 36/36, and
Coverage.py reports 96% branch-aware coverage for
`scripts/ci_legacy_gate_log_import.py`, above the 85% aggregate and 75% per-file
floors. A separate read-only test against the motivating completed log passes
1/1 with five receipts, eight planned shards, the expected source digest, and
unchanged source metadata. Scoped Ruff, strict mypy, Markdown lint, task
integrity, and task-ledger results are recorded with the S83.179 task evidence;
full-gate, commit, merge, push, and any reuse claim remain out of scope.
