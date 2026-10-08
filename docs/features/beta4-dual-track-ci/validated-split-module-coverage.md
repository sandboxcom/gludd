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
