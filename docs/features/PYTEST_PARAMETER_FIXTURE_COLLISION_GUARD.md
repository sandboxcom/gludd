# Pytest Parameter/Fixture Collision Guard

## Problem

Pytest treats a directly parametrized argument as a fixture override. That is
normally useful, but it is hazardous when an installed plugin already owns the
same fixture name. Gludd hit this with `pytest-base-url`: its session-scoped URL
verification fixture depends on the plugin's `base_url` fixture, while two test
modules independently used `base_url` as an ordinary function-scoped parameter.
The complete test shard therefore failed at setup with `ScopeMismatch`, even
though each test's application logic was valid.

The same repository scan found latent collisions with the plugin-provided
`base_url` and Playwright's `browser` fixture. Those parameters now use the
intent-revealing names `candidate_url` and `browser_engine`.

## Mechanical Prevention

`scripts/check_pytest_param_fixture_collisions.py` discovers installed pytest
plugins through the standard `pytest11` entry-point group. It reads their Python
source without importing plugin internals, follows literal `pytest_plugins`
declarations, and extracts fixtures declared through public `pytest.fixture`
decorators. An AST scan then compares those names with literal arguments to
`pytest.mark.parametrize` throughout `tests/`.

This keeps the rule generic as plugins change: fixture names come from the
installed plugin set rather than a Gludd-maintained `base_url` denylist. The
scanner supports comma-separated strings, tuple/list argument declarations,
pytest import aliases, fixture decorator aliases, and explicit fixture names.
Dynamic parameter declarations remain pytest's responsibility because a static
checker cannot resolve them safely.

## Community Evidence

- [pytest issue #11350](https://github.com/pytest-dev/pytest/issues/11350)
  reports that shadowed fixtures can hide dependency parameters and defer the
  resulting failure until setup.
- [pytest issue #13754](https://github.com/pytest-dev/pytest/issues/13754)
  documents a user encountering `ScopeMismatch` where parametrization and
  fixture scopes interact unexpectedly.
- [pytest issue #14248](https://github.com/pytest-dev/pytest/issues/14248)
  traces a related fixture-closure regression to direct parametrization
  shadowing fixture dependencies.
- The [pytest-base-url project documentation](https://github.com/pytest-dev/pytest-base-url)
  confirms that `base_url` is part of the plugin's public fixture interface,
  which is why it must not be reused as an unrelated direct parameter.

These reports span multiple pytest releases and show that parameter/fixture
shadowing is a persistent class of collection and setup failure, not a
repository-specific naming accident.

## Regression Proof

`tests/unit/test_pytest_param_fixture_collisions.py` proves both original
single-argument collision shapes against synthetic source, validates aliased
public fixture declarations, and scans the complete repository against the
fixtures exposed by the installed CI plugin set.


## Field-value traversal without field-pair generators

The retained ca91f33743014d6667646757f34616457a81b987 diagnostic profile records
4184 declaration traversals at2.459 seconds cumulative/1.254 self. Its process-wide
ast.iter_fields count includes other consumers. Cumulative scopes overlap, and
these values are not a predicted saving or original admission proof. The native
profile is preserved at /tmp/gludd-v012-main-hotpath-profile-ca91f33/report.txt;
the original90-second admission remains failed.

The existing declaration walker now consumes a private field-values iterator:
stdlib map(getattr, repeat(node), node._fields, repeat(None)). It reads ALL current
fields once and in order, including custom or programmatically modified native
fields. Missing attributes yield None, which the existing list consumer ignores;
AttributeError follows the same default behavior and other exceptions propagate.
The pending stack, list/wrapper handling, LIFO order, nearest owner, public source
entrypoints, fresh parse and exact GC restoration/tree release are unchanged.
No grammar pruning, schema table, authenticity flag, cache, global memoization,
new dependency or alternate runner is introduced.

Eight additive controls cover deterministic iterator reduction, lazy once/in-order
attribute access, missing/property AttributeError behavior, non-AttributeError
propagation, native/custom fields and fresh calls/tree release. The representative
import/function/plugin input emitted23 Python field-pair tuples before the change
and emits none afterward; its real findings remain. The original bounded-visit
test redirects only its instrumentation seam/type/patch target, preserving all
visited-set/cardinality/owner/order/finding assertions. GC tests and signatures
are unchanged.

The failing-first subset is5FAIL/3PASS1.39s. The complete owner passes57 cases on
canonical CPython3.11 (final5.37s) and existing private CPython3.12.15 (6.72s), with
fatal warnings and serial execution. The unchanged complete-file coverage config
passes57 cases/8.01s,96% combined coverage,97.7% lines/94.0% branches under the
original85aggregate/75per-file line/branch floors. A new test's Module constructor
was made explicit for strict typing; final whole-owner acceptance includes that
fixture correction, without a production change after the coverage receipt.

Commands, whole-owner roster, RED/green JUnits and actual native logs/status/trace/
coverage/audit are retained in /tmp/gludd-v012-ast-field-traversal-evidence.
These are focused compatibility and operation-count proofs. They establish no
whole-admission timing gain, full gate, hosted success, release closure or change
to90/60, three children/workers2-1-1,21phases2850, ZDD or cleanup/promotion policy.
