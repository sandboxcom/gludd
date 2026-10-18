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

## Deferred runtime capability probe (2026-10-18)

The first real private Python 3.12.15 execution on source 1329a87 failed
36 cleanup assertions and skipped the required no-idle-monitoring fallback
guard. The first child reported 1910 passes and one skip in 69.18 seconds;
the observer failed with rc2 after 71.97 seconds, rather than timing out.
The remaining two children never ran. The cleanup failures are a separate
RECORD snapshot repair.

The hook helper invoked its real harness while pytest collected modules.
The existing collection confinement correctly refused that subprocess; the
helper caught the refusal and cached an unavailable capability for runtime.
The unchanged exact Node `--version` import probe remains allowed. The actual
known-good harness probe now runs on first fixture setup and retains its
once-per-process result, original 15-second bound, unsupported/error reasons,
and save/restore behavior. No plugin or collection policy changes are needed.

The new regression observes actual public collection/setup hooks in an isolated
pytest child, rejects every collection subprocess except an exact version
probe, and selects the original fallback guard. It requires the supported
runtime case to execute without a skip. A real collection-phase RED recorded
the forbidden harness argv; a separate genuine RED observed the sticky skip.
Earlier fixture import/observation mistakes remain labelled intermediates.
After repair, the complete no-idle, shared-state-isolation and worktree-
confinement owners pass 86 tests on Python 3.11 in 6.91 seconds and on private
Python 3.12.15 in 8.33 seconds, with zero skips and fatal warnings.

This is fixture lifecycle conformance: production source and the native 85/75
coverage policy are unchanged, as are all 112 admission families, selectors,
workers and bounds. These focused results prove neither admission nor hosted
timing margin. No actual daemon or service is restarted.

### Related firsthand reports

In [pytest discussion 11312](https://github.com/pytest-dev/pytest/discussions/11312)
(2023), a user asks about import-time mocking and a maintainer describes the
fragility of that lifecycle and conftest loading. In
[discussion 9922](https://github.com/pytest-dev/pytest/discussions/9922) (2022),
a user reports imported module initialization causing unwanted collection
side effects. These are related lifecycle reports, not this Gludd failure or
an upstream bug diagnosis. The
[official deferred-resource example](https://docs.pytest.org/en/stable/example/parametrize.html#deferring-the-setup-of-parametrized-resources)
places expensive resources in setup rather than collection.
