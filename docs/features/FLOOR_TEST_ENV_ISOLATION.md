# Floor Test Environment and State Isolation

## Problem

The floor-controller tests wrote directly to process-global `os.environ` and
implemented their own save/restore helper. The repository's environment-write
gate rejected three writes. Handwritten cleanup also makes teardown depend on
every control-flow path remaining correct.

The runtime hook harness also let each Node invocation fall back to the live
`/tmp/gludd-tool-streak.json`. Parallel pytest workers and an active OpenCode
session could therefore change the floor streak between a test's three calls,
making a deny assertion depend on unrelated process order.

## Contract

- Tests use pytest's `monkeypatch.setenv` and `monkeypatch.delenv`.
- Every mutation is reverted automatically at function-scope teardown.
- `_run_ts` gives every Node invocation its own `GLUDD_STREAK_FILE`, namespaced
  by worker PID and a monotonic invocation counter under the harness state root.
- Harness-owned streak files are removed in `_run_ts` teardown; an explicit
  per-test override remains authoritative.
- Default, explicit, and environment-derived floor precedence assertions stay
  unchanged.
- Production floor selection behavior is not modified.

## Practitioner evidence

In pytest issue 4576, practitioners describe fixture dependencies as the
mechanism that controls monkeypatch lifecycle and removes inserted test doubles:
<https://github.com/pytest-dev/pytest/issues/4576>.

Pytest discussion 12983 documents the broader persistence hazard of reused
Python process state across repeated test execution:
<https://github.com/pytest-dev/pytest/discussions/12983>.

Pytest issue 11790 records practitioners seeing concurrent invocations collide
even when a temporary path was described as unique; the reported remedy is to
uniqueify the per-run path, matching this harness's PID-plus-counter boundary:
<https://github.com/pytest-dev/pytest/issues/11790>.

## ZDD, security, and resources

This is test-only conformance. It causes no service restart or data-plane
downtime. Function-scoped teardown prevents one test's environment policy from
changing a later test's authorization or resource ceiling. Per-invocation
streak files prevent workers from changing one another's authorization state
and are deleted before `_run_ts` returns. It adds no workers or persistent
runtime state.

## Verification

The namespace regression, the complete hook-runtime suite, and the serialized
gate must pass. The repository environment-write checker must report zero
violations, and the complete floor-controller test file must pass with warnings
treated as errors.
