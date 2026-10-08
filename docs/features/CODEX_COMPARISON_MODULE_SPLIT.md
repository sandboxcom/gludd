# Codex comparison module split

## Outcome

The historical `general_ludd.self_improve.codex_comparison` import remains the
stable compatibility surface while implementation ownership is split by concern:

- `codex_protocol.py` owns immutable protocol identities, proposal contracts,
  strict codecs, and snapshot materialization.
- `codex_evaluation.py` owns manifest aggregation, Codex scoring, planner feedback,
  and bounded retry diagnosis.
- `codex_gateway.py` owns model-visible prompt handling, structured decoding, and
  the retained local-model gateway.
- `codex_comparison.py` re-exports the established API and forwards attribute
  mutations to the implementation owner. Existing tests and plugins can therefore
  keep patching the compatibility module without knowing the new layout.

Every source module is below the 2,500-line ceiling. The split is zero-downtime:
callers retain the same import path, names, signatures, schemas, protocol digests,
and local-runtime seams. Rollback is one atomic branch revert because there is no
data migration or persisted-format change.

## Long-lived user reports considered

The split deliberately addresses recurring Python import and test-seam problems,
not just file size:

- A 2022 Python.org thread reports that circular imports in a large codebase caused
  subtle runtime failures and continuing maintenance cost until the cycles were
  removed. That experience supports the dependency graph used here: evaluation and
  gateway depend on protocol contracts, while the facade wires narrow callbacks
  only after all owners are imported. See [Add forward referencing of symbol
  imports](https://discuss.python.org/t/add-forward-referencing-of-symbol-imports-similar-to-existing-module-forward-references-to-break-import-recursion-with-from-x-import-y-style-references/19804).
- A 2022 pytest user discussion notes that values copied with `from module import
  VALUE` may need patching in every consumer after import. The compatibility module
  therefore forwards patched attributes to every implementation module and has a
  regression test for the default model-factory seam. See [setting environment
  variables which are accessed at import time](https://github.com/pytest-dev/pytest/discussions/10027).
- A 2026 Python.org help thread explains that the redundant `from owner import name
  as name` form tells type checkers an indirect import is an intentional public
  re-export. The facade uses that form for established names which are intentionally
  public but historically absent from `__all__`. See [Fix all indirect imports?](https://discuss.python.org/t/fix-all-indirect-imports/106685).

These reports are long-lived because the underlying risks are structural: Python
caches imported objects, tests patch the reference actually used by code, and type
checkers cannot infer whether an indirect import is contractual. The facade and its
focused tests make those expectations explicit.

## Verification contract

Focused verification covers the original comparison and span protocol suites, the
runtime protocol shape tests, the facade ownership contract, patch forwarding,
strict lint/type checks, and line-plus-branch coverage. Coverage must remain at
least 85% across the owned files and at least 75% for each owned implementation
file. No Makefile, configuration, enforcement plugin, or shared workflow changes
are part of this feature.
