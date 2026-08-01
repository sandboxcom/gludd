# Incremental gate-failure triage

## Purpose

`make gate-refresh GATE_REFRESH_VALIDATE_ONLY=0` mirrors verbose pytest output
to `.gate-logs/gate-refresh-test.log`. Operators and agents do not need to wait
for the final short-test summary: `make triage-failures` can consume that file
repeatedly while it is growing and return only the failures first seen since
the preceding call.

```console
make triage-failures LOG=.gate-logs/gate-refresh-test.log TRIAGE_STATE=/tmp/gludd-gate-refresh-triage.json TRIAGE_FORMAT=json
```

The state path is explicit in automation so independent Gludd projects and
gate runs cannot share a cursor. If `TRIAGE_STATE` is empty, the script derives
a namespaced path from the resolved log path. With `LOG` empty, the target keeps
its original collect-only behavior:

```console
make triage-failures LOG= TRIAGE_STATE= TRIAGE_FORMAT=json
```

## Incremental contract

- The cursor records the log identity, byte offset, and a bounded prefix
  fingerprint. Replacement, truncation, corrupt state, or a changed inode
  rebuilds the snapshot rather than silently skipping failures.
- Controller status forms emitted during execution (`FAILED`/`ERROR` before or
  after a node ID) enter `delta.new` immediately. The later short summary may
  upgrade `runtime_failure` to a typed cause such as `AssertionError`; that
  change appears once in `delta.updated` without duplicating the node ID.
- A trailing partial line is left behind the cursor and re-read after its line
  terminator arrives, so a mid-write poll cannot freeze a truncated node ID or
  exception class into the snapshot.
- `files` groups failures by source test file. `root_causes` groups by a
  normalized exception class and intentionally excludes exception messages,
  keeping output bounded and avoiding propagation of payloads or credentials.
- JSON is one minified line. The underlying triager exits 1 while any untracked
  failure is present, 0 when the snapshot is empty or contains only ratcheted
  failures, and 2 when the requested log does not exist (`make` reports a
  non-zero recipe as a target failure).
- State replacement is atomic. The command starts no watcher and leaves no
  process behind; callers choose their own bounded polling cadence.

The useful fields are `counts`, `delta`, `files`, `root_causes`, and `cursor`.
`cursor.bytes_read=0` plus empty delta proves that a repeated call observed no
new complete output since the previous call.

## Upstream evidence and limitations

Pytest's official
[output documentation](https://docs.pytest.org/en/stable/how-to/output.html)
documents per-test verbose output and the `FAILED nodeid - cause` and
`ERROR nodeid - cause` short-summary forms. Its
[hook API](https://docs.pytest.org/en/stable/reference/reference.html#pytest.hookspec.pytest_report_teststatus)
also defines `PASSED`, `SKIPPED`, and `ERROR` as verbose words shown while tests
progress. Those are the stable controller-level signals this parser consumes.

The official pytest-xdist
[known-limitations page](https://pytest-xdist.readthedocs.io/en/stable/known-limitations.html#output-stdout-and-stderr-from-workers)
states that worker stdout/stderr cannot be transferred live. Incremental triage
therefore promises early controller statuses, not arbitrary worker application
logs.

Two long-lived user reports shaped the implementation:

- In [pytest-xdist issue #877](https://github.com/pytest-dev/pytest-xdist/issues/877),
  a user with more than 100,000 tests reported that unbounded distributed output
  broke CI log viewing. This is why the triage record is grouped and minified
  rather than replaying surrounding log context.
- In [pytest-xdist issue #868](https://github.com/pytest-dev/pytest-xdist/issues/868),
  an open report from January 2023 shows a live `FAILED` marker before teardown
  and the typed `FAILED nodeid - TimeoutError` summary later. The parser treats
  these as one evolving record: early detection first, root-cause enrichment
  when enough information arrives.

## Verification

Behavioral coverage is in `tests/unit/test_triage_failures.py`. It exercises
xdist and classic formats, de-duplication, summary upgrades, incremental append,
rotation/truncation, corrupt state recovery, ratchet classification, compact
JSON, and the retained collect-only path.
