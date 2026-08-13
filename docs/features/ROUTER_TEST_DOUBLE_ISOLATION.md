# Router Test Double Isolation

## Status

Implemented for the `0.1.0-beta.4` release train. Model-router test doubles
now have function-scoped teardown and cannot alter later test modules.

## Problem

The endpoint suite assigned replacement subsystem factories directly onto the
imported `general_ludd.routers.models` module. Python caches that module in
`sys.modules`, so the replacements survived the test that installed them.
When the deep router suite ran afterward, its downloaded-model endpoint called
the stale registry factory and returned an empty list. The same test passed in
isolation, making the result dependent on file order.

## Isolation contract

- The shared helper requires pytest's function-scoped `monkeypatch` fixture.
- Both router subsystem factories are replaced with `monkeypatch.setattr`.
- Every direct helper consumer declares the fixture explicitly.
- Pytest restores the original module attributes during fixture teardown.
- Authentication, capability checks, route behavior, and production code are
  unchanged.
- The authoritative regression runs the endpoint file before the deep file,
  matching the order that exposed the leak.

## Practitioner evidence

The long-running
[pytest issue 4576](https://github.com/pytest-dev/pytest/issues/4576)
records practitioner experience with `monkeypatch`, `mock.patch`, fixture
scope, and patches leaking into other fixtures when lifecycle is broader than
intended. The discussion recommends using fixture dependencies to control the
lifecycle of inserted test doubles.

A later
[pytest discussion 13353](https://github.com/pytest-dev/pytest/discussions/13353)
documents another order-dependent failure caused by global imported-module
state surviving into a later module. That matches this incident: changing a
cached module without teardown made a later test observe the wrong dependency.

## Zero-downtime, security, and resource boundary

This is a test-harness correction only. It changes no runtime import, HTTP
route, provider configuration, cache, database, port, migration, or deployment
state. It keeps the real authentication and authorization middleware in the
endpoint tests and removes process-global mutation instead of adding a
suppression or test-order workaround.

Function-scoped teardown reduces retained mocks and makes serial, shuffled, and
parallel test execution equivalent. It introduces no process, persistent file,
or cross-project temporary path.

## Verification

The exact two-file order first reproduced one failure in 255 tests while each
file passed alone. After the repair, the same ordered selection must pass all
255 tests under `-W error`. The complete model-router coverage family must run
without deselection, keep `src/general_ludd/routers/models.py` above the
75-percent file floor, and remain subject to the 85-percent aggregate release
gate.
