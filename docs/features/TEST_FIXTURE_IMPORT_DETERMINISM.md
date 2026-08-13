# Deterministic Mock Imports in Test Fixtures

## Problem

Some test fixtures used `__import__("unittest.mock")` and then accessed
`.patch`. Python's low-level import function intentionally returns the
top-level package for a dotted name when no `fromlist` is supplied. The
fixtures therefore received `unittest`, not `unittest.mock`, and every test
using those fixtures failed during setup.

A related pattern used `__import__("unittest").mock`. It happened to work only
when another import had already attached the `mock` submodule to the package,
making results depend on collection and execution order.

## Contract

Tests that use standard-library mocks import them explicitly:

- use `from unittest.mock import patch` for direct patch contexts;
- use `from unittest import mock` when a suite needs several mock helpers; and
- never rely on a prior test importing a submodule as a package side effect.

Patch targets remain fully qualified application symbols, so each fixture keeps
the same isolation boundary. This change does not replace real application
behavior with mocks; it only makes existing dependency seams deterministic.

## Security and observability

Explicit imports eliminate a dynamic string-import path from these fixtures and
make static analysis see the dependency. A failure now occurs in the test body or
the named patch target rather than as a setup-wide `AttributeError` on an
unrelated package object. CI should report setup errors separately from assertion
failures so one fixture cannot masquerade as many product regressions.

## Zero-downtime adoption

This is test-only infrastructure: no runtime artifact, database, API, or
deployment schema changes. Rollout consists of running the formerly failing
suites under strict warnings and then the repository collection gate. Rollback
is a source-only test revert and cannot affect a deployed service.

## Practitioner evidence

A Stack Overflow report open since 2008 documents the same surprising dotted-name
behavior: without `fromlist`, `__import__` returns the top-level package rather
than the requested nested module. The durable recommendation is to use ordinary
imports or `importlib.import_module`:
[Python's `__import__` doesn't work as expected](https://stackoverflow.com/questions/211100/pythons-import-doesnt-work-as-expected).

## Verification

The outcome-observer, Git-history-router, and hardware model-fit suites pass
77 tests under strict warnings. A repository search finds no remaining
`__import__("unittest.mock")` or `__import__("unittest").mock` pattern, and
Ruff passes all three touched files.
