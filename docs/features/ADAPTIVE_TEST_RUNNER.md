# Adaptive Test Runner Contract

Status: beta4 release contract

## Contract

`scripts/adaptive_test.py` may retry a failed pytest run only when the process
has an OOM-shaped exit code (`-9` or `137`) or emits a complete, known xdist
worker-crash diagnostic line. A successful exit is never reclassified, and
ordinary assertion or documentation prose containing crash words is not an OOM
signal.

When the caller does not provide `--basetemp`, the runner creates a short,
process-namespaced path. POSIX uses `/tmp/gludd-at-<pid>-<token>` so nested
`popen-gwN`, test-name, and AF_UNIX socket suffixes retain path headroom.
Non-POSIX platforms retain the system temporary root. An explicit caller
basetemp always wins.

## ZDD and isolation

The runner halves workers only after strong crash evidence and otherwise
returns the original failure immediately. This avoids replaying deterministic
test failures and preserves the first useful result. Unique basetemps prevent
one concurrent pytest process from deleting or sharing another run's working
tree; the PID and random token permit multiple Gludd projects to test in
parallel without collision.

## Observability and compatibility

Every attempt prints its worker count and complete pytest command, while the
streaming runner persists namespaced heartbeat progress. Exit codes remain
unchanged. Xdist diagnostic matching is line-anchored and case-insensitive,
covering current worker identifiers without accepting arbitrary prose.

`make test-failures` is an inventory operation, not a test execution alias. It
prints an immediate progress line, reads pytest's `cache/lastfailed` JSON without
changing it, sorts the node IDs, and emits at most the explicit limit (50 by
default, hard-capped at 200). A missing cache is an observable empty result;
malformed, oversized, or schema-invalid cache data fails closed. The reporter
starts no pytest or xdist worker, so it cannot leave a test process tree behind.

## Verification

`tests/unit/test_adaptive_test.py` pins signal-code classification, exact
xdist diagnostics, prose false positives, no retry after ordinary failures,
unique paths, caller overrides, and the Darwin AF_UNIX length boundary.

## Practitioner evidence

- [pytest #5524](https://github.com/pytest-dev/pytest/issues/5524) records a
  long-lived concurrent `basetemp` race observed under xdist, supporting
  explicit per-run temporary-root isolation.
- [pytest-xdist #868](https://github.com/pytest-dev/pytest-xdist/issues/868)
  documents distributed worker shutdown and teardown effects that can resemble
  crashes, supporting strict diagnostic classification instead of substring
  matching.
- On 2026-08-20, pytest's upstream
  [`cacheprovider.py`](https://github.com/pytest-dev/pytest/blob/main/src/_pytest/cacheprovider.py)
  documented `cache/lastfailed` as the source of truth and warned that `--lf`
  defaults to the full suite when no failures are cached. Reading that cache
  directly avoids an accidental 106,000-test execution.
- On 2026-08-20, the long-running pytest-xdist
  [hang report #60](https://github.com/pytest-dev/pytest-xdist/issues/60)
  remained evidence that parallel pytest shutdown can strand execution. The
  inventory command therefore creates no xdist workers and has no owned child
  service requiring compensating cleanup.
