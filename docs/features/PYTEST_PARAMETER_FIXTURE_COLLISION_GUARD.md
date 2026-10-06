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
