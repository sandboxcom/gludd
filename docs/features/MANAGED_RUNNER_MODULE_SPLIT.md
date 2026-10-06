# Managed runner module split

## Scope

`managed_runner.py` remains the compatibility facade and orchestration owner. The
split introduces two one-way dependencies:

- `managed_runner_contracts.py` owns immutable task, prompt, proposal, and result
  values plus their strict serialization validators.
- `managed_runner_boundaries.py` owns dependency-injection protocols, durable
  outcome adapters, local proposal adapters, and per-run state containers.
- `managed_runner.py` retains approval, admission, privacy enforcement,
  candidate execution/evaluation, publication, retry, and rollback behavior.

The facade re-exports the existing classes by object identity. Patch-sensitive
selection-policy constants and orchestration helpers remain facade globals, so
existing callers and tests continue to patch the reference actually used by the
runner. The extracted modules do not import the facade, preventing a new import
cycle.

## Long-lived upstream and user reports

Research performed 2026-10-06 found three recurring compatibility traps:

1. The mypy community has discussed explicit re-exports since 2021. The
   maintainers and downstream library authors converged on either `__all__` or
   the explicit `from module import Name as Name` form when a symbol is part of
   the compatibility surface. That informed the facade's explicit imports and
   the identity regression tests.
   [mypy issue #10198](https://github.com/python/mypy/issues/10198)
2. A pytest user discussion from 2022 documents that values copied with
   `from module import VALUE` at import time can leave several independently
   patchable references. This is why model-policy and retry-policy globals used
   by orchestration were not moved behind a second module-level copy.
   [pytest discussion #10027](https://github.com/pytest-dev/pytest/discussions/10027)
3. A pytest issue opened in 2019 recommends explicit dependency injection over
   patching hard-coded import paths. The extracted boundary protocols follow
   that advice while retaining the original constructor signatures and runtime
   protocol checks.
   [pytest issue #4576](https://github.com/pytest-dev/pytest/issues/4576)

These are long-lived design concerns rather than version-specific defects. The
split therefore treats import identity and injection seams as tested behavior,
not incidental implementation details.

## ZDD and verification

The change is import-only at the public boundary and does not add a migration,
daemon restart, schema change, process, port, or mutable runtime state. Existing
workers can continue importing the facade during a rolling deployment while new
workers load the extracted modules.

Focused verification covers facade identity, malformed contract inputs,
admission, execution, evaluation, publication, rollback, model lifecycle,
privacy, worker dispatch, and live candidate routing. The owned-file coverage
audit requires branch coverage and reports at least 85% aggregate and at least
75% for every owned source file.
