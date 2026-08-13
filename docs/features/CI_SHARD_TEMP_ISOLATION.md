# CI Shard Temporary-State Isolation

## Problem

The local parallel CI runner used one directory both as pytest's
`--basetemp` and as the parent of shard-owned `TMPDIR`, enforcement state,
and JUnit output. Pytest owns and resets its basetemp, so those longer-lived
control files could disappear while the shard process still needed them.

## Contract

Each shard has one namespaced workspace:

- `pytest/` is the only path passed to pytest as `--basetemp`.
- `tmp/` remains the child process `TMPDIR`.
- `state/` contains Gludd enforcement and coordination state.
- `junit.xml` remains at the workspace root for durable summary extraction.
- The runner removes the whole workspace only after the shard has terminated
  and its summary has been persisted.

The layout prevents pytest cleanup from recursively deleting its own process
environment or the control-plane files used to observe and terminate a shard.

## Practitioner evidence

Pytest issue [#5524](https://github.com/pytest-dev/pytest/issues/5524) reports a
concurrent CI basetemp reset race under xdist. Pytest issue
[#10679](https://github.com/pytest-dev/pytest/issues/10679) documents that xdist
adds worker-specific levels beneath basetemp and accesses the base temporary
factory directly. These long-lived reports support treating pytest's basetemp
as tool-owned scratch, separate from runner-owned state.

## ZDD, security, and resources

This is test-control-plane isolation and causes no application data-plane
downtime. Shard namespacing is retained, process-group cleanup remains bounded,
and no state is shared with another project. Separating pytest scratch also
makes JUnit evidence survive until the runner records it, while final cleanup
still reclaims the entire bounded workspace.

## Verification

Contract tests prove that `TMPDIR` is outside pytest's child basetemp, command
construction uses the child path, JUnit stays at the stable root, all Gludd
state variables remain namespaced, and cleanup retains its existing process
ownership.
