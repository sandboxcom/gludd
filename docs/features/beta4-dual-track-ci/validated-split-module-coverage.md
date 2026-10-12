# Validated split-module coverage ownership appendix

[Back to the anchored feature section](../BETA4_DUAL_TRACK_CI.md#validated-split-module-coverage-ownership-2026-10-06)

The development gate exposed eight false `UNTESTED` reports after the CLI,
daemon, and event-loop facades were split: the CLI parser, daemon lifecycle
ports, and six event-loop lifecycle/dispatch mixins. Their tests deliberately
exercise behavior through the stable compatibility facades or computed module
names, so a literal-import-only AST index cannot see the real ownership edge.
`config/coverage_gap_test_mappings.json` now records those indirect edges.

This mapping is not a coverage-gap allowlist. For each entry, the checker proves
that the target component and facade both exist, the facade directly imports
the component, the mapped test exists and has test functions, and that test
statically imports the facade. Missing files or either broken import edge fail
the audit as mapping errors; unrelated passing tests cannot conceal an untested
module. The baseline retains only the pre-existing reviewed gaps.

Long-lived practitioner reports support explicit but verified ownership where
static inference is incomplete. [pytest-testmon issue #12][testmon-explicit-deps],
opened in 2015 and reviewed 2026-10-06, requests merging explicit file
dependencies with measured coverage for inputs automatic analysis cannot see.
The [pytest discussion on imported test functions][pytest-imported-tests],
reviewed 2026-10-06, notes that definition ownership cannot reliably be inferred
from an imported name alone. Gludd therefore requires both source and test import
edges instead of accepting a filename, prose mention, or unrestricted mapping.

ZDD is unchanged: this checker only reads Python and JSON files and never starts
or mutates a runtime service. Rollback removes the mapping, checker validation,
tests, and this note together; the coverage gate then fails closed on all eight
components again.

Exact-run artifact inspection is now a first-class bounded operation through
`make ci-artifact-context`. It resolves only the requested run-and-artifact
resource namespace, accepts one safe basename, and rejects symlinks, duplicate
matches, traversal, and root escapes. Explicit context limits bound output;
validate-only mode performs no network or checkout write. Run 33345023078
exposed the resource and disk diagnostics, while its authenticated job log
supplied the traceback absent from the downloaded artifact.

This inspection path starts no daemon beyond its foreground checker and leaves
no service state to clean up. Rollback is the isolated target, contract,
checker, test, and docs. The bounded design follows
[pytest-timeout issue 60][pytest-timeout-issue-60]: timeout failures need
durable, scoped reporting instead of an unbounded or hidden scan.

[Continue to exact-SHA promotion and release operations](exact-sha-promotion.md)

[pytest-imported-tests]: https://github.com/pytest-dev/pytest/discussions/11366
[pytest-timeout-issue-60]: https://github.com/pytest-dev/pytest-timeout/issues/60
[testmon-explicit-deps]: https://github.com/tarpas/pytest-testmon/issues/12

## Coverage-index reuse after the release-candidate timeout (2026-10-12)

Hosted run `38189779986` timed out in Python 3.11 `unit-1b` at
`test_repository_split_component_mappings_are_exact_and_live`. Spot-checking
three mappings still built the full repository index and therefore retained the
expensive work. The regression again verifies all 29 exact mappings and their
live facade tests against that full index.

The checker now derives source re-exports and compact dependency edges from the
same parse and traversal. Each distinct facade's reachability is computed once
per index using Python's standard-library [`functools.cache`][python-cache].
Test counting, normal imports, attribute re-exports, and file-loader discovery
share one module traversal; tests without a file-loader call avoid inspecting
every function body for loader wrappers. Each source is parsed once per index,
and each selected test module is traversed once. These work bounds are verified
by regressions, including a cyclic relative-import graph and a file-loader
wrapper, rather than machine-sensitive elapsed-time assertions.

The reuse ends at each audit invocation. Rewriting a source edge or test import
before the next index build is observed and fails closed; malformed mapping
documents, paths outside the test tree, missing tests, and unrelated facades
remain errors. Source ASTs are discarded after their compact summary is built,
and test traversal data is retained only for the current file. No process-global
or on-disk cache can silently credit stale coverage.

On the local Python 3.11.14 environment, the original node took **30.53 seconds**;
the implementation repair, with every live mapping restored, took **13.42
seconds** (56 percent less time). The focused coverage run passed **41 tests in
34.91 seconds**, with **96.0 percent line coverage** and **90.8 percent branch
coverage** for the checker. The 85 percent aggregate and 75 percent per-file
gates passed, and the per-test timeout remains 180 seconds. These are local
measurements; a green hosted run is still required for release admission.

The long-lived upstream reports were reviewed again on 2026-10-12:

- [CPython issue #123373][cpython-ast-walk], opened 2024-08-27, explains that
  `ast.walk()` traverses the descendant tree using a queue. Sharing its result
  removes repeated traversal while preserving the same static nodes.
- [pytest-testmon issue #12][testmon-explicit-deps], opened 2015-03-31, discusses
  combining explicit file dependencies with observed dependency evidence.
  Gludd continues validating each declared edge rather than treating a mapping
  as a coverage exemption.
- [pytest-timeout issue #60][pytest-timeout-issue-60], opened 2020-03-31 and
  referencing a 2016 pytest mailing-list discussion, reports that a CI timeout
  can prevent graceful cleanup and a final report. Gludd retains its existing
  timeout, namespaced temporary roots, and observed progress while reducing the
  index work.

ZDD remains candidate rejection: this read-only checker neither restarts nor
changes a serving runtime. Rollback is the isolated implementation, regression,
and documentation commit. The audit starts no daemon, model, or network client;
the serial test target owns and removes its temporary test directory.

[python-cache]: https://docs.python.org/3.11/library/functools.html#functools.cache
[cpython-ast-walk]: https://github.com/python/cpython/issues/123373
