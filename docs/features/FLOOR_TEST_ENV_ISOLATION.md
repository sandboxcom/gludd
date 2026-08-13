# Floor Test Environment Isolation

## Problem

The floor-controller tests wrote directly to process-global `os.environ` and
implemented their own save/restore helper. The repository's environment-write
gate rejected three writes. Handwritten cleanup also makes teardown depend on
every control-flow path remaining correct.

## Contract

- Tests use pytest's `monkeypatch.setenv` and `monkeypatch.delenv`.
- Every mutation is reverted automatically at function-scope teardown.
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

## ZDD, security, and resources

This is test-only conformance. It causes no service restart or data-plane
downtime. Function-scoped teardown prevents one test's environment policy from
changing a later test's authorization or resource ceiling. It adds no workers,
files, or persistent runtime state.

## Verification

The repository environment-write checker must report zero violations, and the
complete floor-controller test file must pass with warnings treated as errors.
