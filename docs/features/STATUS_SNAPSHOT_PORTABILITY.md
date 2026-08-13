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
single-file atomic responsibility for the tracked session snapshot.

## Verification

The tests pin the uv command wiring and prove validation mode leaves
`SESSION.md` byte-for-byte unchanged. The target registry documents its only
variable and provides the non-mutating behavioral example.
