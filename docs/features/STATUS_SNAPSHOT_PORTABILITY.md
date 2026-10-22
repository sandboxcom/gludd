# Status Snapshot Interpreter Portability

## Problem

The `status-snapshot` Make target invoked ambient `python3`, while the rest of
the project is pinned and synchronized through `uv`. On hosts where the system
interpreter predates Python 3.11, the script fails before it can update
`SESSION.md` because `datetime.UTC` is unavailable.

## Contract

The target now runs the tracked script with `$(UV) run python`, so it selects
the same project interpreter and dependency environment as the other Python
quality targets. `STATUS_SNAPSHOT_VALIDATE_ONLY=1` passes
`--validate-only`; that mode builds and validates the gate block but never
rewrites `SESSION.md`. This gives the Make target contract a safe behavioral
example.

## Preserve and validate the native handoff region (2026-10-21 UTC)

Published6161 Build39533219987 unit-3b job119007635395 fails the original
test_session_md_has_markers assertion after handoff compaction removes the
native region. Local exact failure is retained with two obsolete cleanup checks
in /tmp/gludd-v012-remaining-hosted-red.log/XML,3FAIL/2PASS. Preserve the original
marker test. Restore only its structural scaffold, then run the existing
make status-snapshot STATUS_SNAPSHOT_VALIDATE_ONLY=0 owner. It writes the actual
preserved19-line failed gate; it never manufactures candidate PASS or changes
.gate-status/current pipeline JSON. Keep historical profile/source provenance
and current release obligations outside the generated region.

The old validate-only mode builds a block without inspecting SESSION at all.
It reports PASS for missing files, missing/reversed/duplicate markers, while
repeated native writes accumulate another status header. Ten new controls fail
before repair10/1.02s/native1.9s in
/tmp/gludd-v012-session-boundaries-red.log/XML. A shared render_session now
requires an existing handoff with exactly one ordered standalone marker pair. Validation
renders without writing; normal mode writes that same validated region.
Only its immediately preceding native header is replaced, retaining surrounding
handoff text and the actual FAIL. Three repeated-write cases preserve identical
bytes and one header for absent, undated and dated prior headers. Existing tests,
missing-input refusals and actual status producer ownership remain.

The complete snapshot owner participates in the172-case two-runtime source
checkpoint and579-case five-file coverage checkpoint documented in
[the admission feature](INTEGRATION_ADMISSION.md#remaining-hosted-failures-and-native-readiness-repairs-2026-10-21-utc).
The earlier snapshot whole-file checkpoint measures98.4line/95.0branch under unchanged85/75 BOTH.
Native strict checking exposes15 older test-owner errors; complete fixture/
return annotations and explicit list/string validation preserve the original
README assertions and diagnostic. No suppression, changed threshold or assertion
removal. Receiving whole-owner proof and exact-SHA CI remain required.

Source review also finds inline marker prefix/suffix text accepted by validation;
rendering could then consume surrounding prose. Four added cases retain
4FAIL/7PASS1.06s/native2.0s before requiring standalone marker lines. The entire
snapshot owner then passes32/1.00s/native1.9s in
/tmp/gludd-v012-session-inline-green.log/XML; its RED is
/tmp/gludd-v012-session-inline-red.log/XML. The same shared rendering function
refuses before either validation success or writing. Existing standalone markers
at file start, dated/undated headers and surrounding handoff assertions remain.

During future compaction, preserve the marker scaffold and invoke this native
producer; do not paste or edit observations. Run its read-only documented example
afterward. The mandatory guide names the original marker owner and complete
persistence owner, alongside the required Last Updated and Total tests: labels.
No new handoff authority, scanner, cache, daemon or manual-default file is added.

## Hex-letter SESSION hash boundary (S98.4,2026-10-22 UTC)

The first clean runtime-family repair at
ad4f92abc025cd416d0c72d822270336901ba00a fails2/2377PASS78.29s,
native2/81.9s, before either later child. Its original90/60/2-1-1 bounds remain:
.gate-logs/observed/runtime-repair-ad4f92/v012-runtime-repair-ad4f92-312.json.
The persistence owner mistakes the branch suffix20261022 for its first Git
hash. SESSION already names the actual published eb772 commit after that branch.
Reordering the handoff would hide the parser defect.

Conform the test parser to the existing AGENTS evidence policy: a bounded7-40
lowercase hexadecimal token must contain at least one a-f letter. Numeric dates
and counts are not evidence tokens. Preserve all nine original cases and the
first actual token's native git cat-file commit verification; an invalid first
hex token still fails even when a valid historical commit follows. Eight added
token cases cover numeric/date, first/last-letter and length boundaries; two
delegate controls exercise that original assertion. Explicit return annotations
allow the complete changed owner to pass strict typing without suppressions.

Canonical tests-first receiving is5FAIL/14PASS0.80s/native2/about1.9s:
.gate-logs/observed/session-hash-boundary-red/v012-session-hash-boundary-red311.json.
The complete persistence, snapshot and fixture-collision union then passes116
on each runtime:3115.27s/native0/about6.2s and3126.34s/native0/7.4s.
Receipts are .gate-logs/observed/session-hash-boundary/
v012-session-hash-boundary311.json and v012-session-hash-boundary312.json;
XML/raw remain /tmp/gludd-v012-session-hash-boundary{311,312}.{xml,log}.
Ruff and a separate strict complete-owner check pass. No production Python
source changes, coverage exclusions or fabricated coverage measurement are added.

The same failed runtime run independently exposes an inherited xdist worker
label on a serial grandchild; that producer repair is separate and remains
pending. The failed attempt invalidates older current repair authority through
ci-failure-repair. Only a clean successor's actual original-budget repair-owned
execution can restore it. Collection/source review, full gate, hosted exact-SHA
CI and deployment remain distinct pending proofs; all seven tasks stay unchecked.
This local validation change mutates no serving process or data.

## Practitioner evidence

Astral uv issue
[#13507](https://github.com/astral-sh/uv/issues/13507) is a long-lived user
report about the mismatch between simple `python script.py` invocations and
uv-managed Python installations. The report reinforces that a project target
must explicitly enter its managed interpreter instead of assuming the ambient
`python3` is equivalent.

## ZDD, security, and resources

This is a local control-plane operation and causes no application downtime.
Validation mode is read-only, launches one bounded process, performs no network
access, and writes no temporary helper scripts. Normal mode retains its existing
single-file responsibility for the tracked session snapshot.

## Verification

The tests pin the uv command wiring and prove validation mode leaves
`SESSION.md` byte-for-byte unchanged. The target registry documents its only
variable and provides the non-mutating behavioral example.
