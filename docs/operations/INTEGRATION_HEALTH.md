# Integration health failure accounting

## Contract

`make integration-health` streams pytest output unchanged, emits the existing
progress markers and periodic JSON snapshots, and preserves its exit contract:
0 for a clean run, 1 for test failures, and 2 for runner errors or timeout.

Failure accounting treats the pytest node ID as the identity of a failure. It
accepts both xdist controller lines such as
`[gw0] [ 50%] FAILED tests/integration/test_api.py::test_case` and final summary
lines such as `FAILED tests/integration/test_api.py::test_case - AssertionError`.
Repeated sightings merge into one record, with the final reason enriching the
live record. The source file is the node ID segment before the first `::`, so
the JSON and terminal summary report exact failed tests and distinct files.

## Upstream evidence

Pytest's [output documentation](https://docs.pytest.org/en/stable/how-to/output.html)
defines the short-summary `FAILED nodeid - reason` form. Long-lived user reports
also show why the parser must consume line-oriented status records instead of
searching traceback prose:

- [pytest-xdist issue #868](https://github.com/pytest-dev/pytest-xdist/issues/868)
  shows a distributed live failure and the later typed short-summary record for
  the same test. Integration health therefore deduplicates by node ID and lets
  the later summary enrich the reason.
- [pytest issue #12713](https://github.com/pytest-dev/pytest/issues/12713)
  documents version-dependent changes to reason verbosity in the short summary.
  The reason is useful metadata, but is not used as the failure identity.
- [pytest issue #13308](https://github.com/pytest-dev/pytest/issues/13308)
  includes six consecutive `FAILED` node IDs followed by a `6 failed` total.
  The focused regression fixture mirrors that shape and requires six test
  records while grouping their distinct source files separately.

## Verification

`tests/unit/test_check_integration_health.py` covers six-entry xdist summary
accounting, file grouping, class and parametrized node IDs, live/summary
deduplication, and reason enrichment without launching integration tests.

## Orphaned collection cleanup

`make reap-orphan-pytest` is dry-run by default. With `APPLY=1`, it selects only
old, orphaned pytest trees proven to belong to this checkout, walks descendants
leaf-first across process-group boundaries, sends `SIGTERM`, waits for the
bounded grace interval, escalates survivors to `SIGKILL`, and then verifies that
no selected PID remains. A nonzero survivor count is an error; cleanup must not
claim success while a collection worker still holds the shared lock.

This escalation is grounded in long-lived practitioner reports rather than a
project-specific assumption. [pytest-timeout issue #159](https://github.com/pytest-dev/pytest-timeout/issues/159)
documents pytest termination leaving child processes orphaned, and the
[2018 subprocess cleanup report](https://stackoverflow.com/questions/52476265/killing-shell-true-process-results-in-resourcewarning-subprocess-is-still-runni)
shows that sending a kill signal alone is not proof that the process lifecycle
has finished. The Gludd reaper therefore couples bounded escalation with an
explicit liveness check and keeps its existing project-ownership guards.

`tests/unit/test_orphan_pytest_reaper.py` pins selection safety, leaf-first
termination, TERM-resistant escalation, and post-kill survivor verification.
