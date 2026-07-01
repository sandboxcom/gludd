# CI Green Plan — 2026-07-01

A concrete roadmap to **stable** green CI. This is not a single-fix effort; the suite
is flaky in an order-dependent way, and the honest expectation is a multi-commit
campaign against cross-test pollution and environmental fragility.

## 1. State

Two recent CI runs tell the whole story:

| Run | SHA / branch | Failures | Shard | Nature |
|-----|--------------|----------|-------|--------|
| `28496503128` | master `ce22dfe6` | 8 tests | unit-1 + others | 7 × caplog empty-records + 1 × dist-readme |
| `28538259829` | master + a conftest fix | 24 tests | unit-3 | a **completely disjoint** set |

The critical observation: **zero overlap** between the two failure sets. The conftest
fix applied between the runs did *not* regress unit-3; rather, the original 8 failures
were genuinely fixed (confirmed not recurring), and the flakiness **relocated** to a
different shard and a different set of tests.

This is the classic signature of **order-dependent cross-test pollution**: global state
leaked by one test changes the outcome of an unrelated test, and which tests "fail"
depends on collection/execution order (which varies by shard, worker assignment, and
xdist scheduling). Fixing the symptom (the specific failing tests) just moves the
pollution somewhere else. The durable fix targets the *leaked state*, not the victims.

## 2. Root Causes

Three classes of defect are in play.

### (a) Cross-test global-state pollution (the dominant class)

Shared process-global mutable state that one test mutates and does not restore, so a
later test observes the mutation:

- **Logging state** — handlers, levels, and `propagate` flags mutated in-place. caplog
  captures nothing when propagation was disabled by an earlier test. *(Fixed
  conservatively via an ancestor-only conftest fixture that restores propagate on the
  relevant logger ancestry.)*
- **`process.registry._DEFAULT_REGISTRY` singleton** — a module-level registry mutated
  by tests that register agents/handlers and never reset. *(Reset fixture in progress.)*
- **`sys.modules[...]` raw assignment with generic names** — tests inject fake modules
  under generic keys and never pop them, so a later test importing the real name gets
  the stub (or vice versa).
- **`sys.path.insert(...)` without teardown** — path entries accumulate; later imports
  resolve to unexpected locations depending on order.

### (b) Environmental repo-state fragility

Tests whose pass/fail depends on the checkout's *shape* rather than on code under test:

- Tests depending on **submodule checkout** being present/absent.
- Tests depending on **`.devspark` scaffolding** existing.
- **Sibling-test side effects** — files/dirs created by one test that another test reads.

These pass locally and on some CI runs, fail on others, purely by environment.

### (c) Genuine test-vs-code drift (small but real)

- **`test_preflight_coverage`** patches `preflight.FileStore`, but `FileStore` is a
  *local import* inside the function — the patch targets the wrong symbol, so the real
  `FileStore` runs.
- **`test_pipeline_wiring`** asserts a budget rejection while `remaining=inf`, so the
  rejection never fires.

## 3. Remediation (ordered)

Ordered so that each step reduces the pollution surface before the next, ending with a
durable catch-all:

1. **Logging-state isolation** — *DONE.* Ancestor-only conftest fixture restores logger
   `propagate`/handler state.
2. **Process-registry reset** — *IN PROGRESS.* Autouse fixture snapshots and restores
   `process.registry._DEFAULT_REGISTRY` around each test.
3. **`sys.modules` / `sys.path` sandbox fixture** — autouse fixture that snapshots both
   `sys.modules` keys and `sys.path` at test entry and restores (pops added keys,
   removes added path entries) at exit.
4. **Environmental test self-containment** — make environment-dependent tests skip on
   missing state (the `_require_dist` pattern: `pytest.skip(...)` when the submodule /
   `.devspark` scaffolding is absent) instead of failing; or better, construct the
   required state in a fixture so the test is hermetic.
5. **Fix the 2 drift bugs** —
   - `test_preflight_coverage`: patch the correct import target (patch where the local
     import resolves `FileStore`, i.e. its source module, not `preflight.FileStore`).
   - `test_pipeline_wiring`: set a finite `remaining` budget so the rejection path
     actually executes.
6. **Broad snapshot/restore-all-global-state autouse fixture** — the durable fix. A
   single autouse fixture that snapshots the known global-state surfaces (logging,
   registries, `sys.modules`, `sys.path`, relevant env vars, and any other discovered
   singletons) and restores them after every test. This backstops future pollution so
   new tests can't reintroduce order-dependence.

## 4. Process (per fix)

1. Verify locally: `make ci-test TESTPATHS=... NPROC=1` (NPROC=1 to expose
   order-dependence deterministically), then `make kill-pytest` to clean up workers.
2. Push to an `integration/` branch (never straight to master).
3. `make ci-verdict` on the pushed SHA to read the real CI result.
4. Iterate.

Because CI is **~20–30 min/run and itself flaky**, expect several cycles per fix — a
single green run is not proof; require repeated green (or green across shards) before
trusting a fix. Re-run to distinguish a real fix from a lucky ordering.

## 5. Honest Note

This is a **large, multi-commit effort against a flaky suite**. Stable green is not a
single fix. The two disjoint failure sets prove that patching named failing tests only
relocates the pollution; only isolating the leaked global state (steps 3 and 6) makes
the suite order-independent and therefore durably green. Progress should be measured by
*reduction in cross-shard variance*, not by any one green run.

## Appendix — Remediation Proposals (ready-to-apply)

The concrete fixes drafted this session, consolidated as an actionable checklist. Each
item lists **status**, the **mechanism** (what to change and how), and the **beneficiary
tests** it unblocks. Apply top-to-bottom: each earlier item shrinks the pollution
surface the later ones must backstop.

### A1. Ancestor-only conftest logging fixture — **DONE** (committed)

- **Mechanism:** autouse conftest fixture
  (`_force_propagate_all_general_ludd_loggers`, `tests/conftest.py:125`) that runs as
  *setup* (reset-before pattern, **no `yield`/teardown**): it unconditionally calls
  `logging.disable(logging.NOTSET)` to undo any leftover global disable from a prior
  test, then sets `propagate = True` and `disabled = False` on the *ancestor loggers
  only* — `general_ludd`, `general_ludd.events`, `general_ludd.connectors`,
  `general_ludd.daemon` — rather than walking every logger in the manager dict.
  Conservative by design: touches only the ancestry tests are observed to mutate.
  Note: it restores `propagate`/`disabled` only — it does **not** snapshot or restore
  handler lists or numeric levels (A6 covers that).
- **Beneficiaries:** the 7 caplog empty-records failures (run `28496503128`, unit-1);
  any test asserting on `caplog.records` after a sibling disabled propagation.
- **Note:** this is the *interim* fix; A6 is its eventual durable replacement.

### A2. `process.registry._DEFAULT_REGISTRY` reset fixture — **DONE** (added to conftest)

- **Mechanism:** autouse fixture (`_reset_process_registry`, `tests/conftest.py:169`)
  that nulls the module-level singleton
  `general_ludd.process.registry._DEFAULT_REGISTRY = None` both before the `yield`
  (setup) and after (teardown), forcing lazy re-creation, so tests that register
  agents/handlers into the default registry cannot leak those registrations into a
  later test that assumes an empty/default registry. (It nulls rather than
  snapshot/restores — safe because the registry is lazily created on first use.)
- **Beneficiaries:** any test that registers into the default registry and any later
  test asserting on registry contents / default-agent membership.

### A3. `sys.path` / `sys.modules` sandbox autouse fixture — **PROPOSED**

- **Mechanism:** autouse fixture that snapshots `sys.path` and the `sys.modules` key set
  at entry. At exit: **restore `sys.path` verbatim** (assign the saved list back), and
  **evict only test-injected `sys.modules` keys** — do not blanket-pop everything.
  Eviction is scoped to an allowlist so real/lazily-imported production modules are never
  torn down:
  - prefix match: `live_pkg_`, `livepkg`, `rbpkg`, `smg_`
  - exact-name match: `capability_policy`, `fs_write_policy` (extend as new stub names
    are discovered).
  Keys not matching the allowlist are left alone even if newly added.
- **Beneficiaries:** `test_hot_reload_*`, `test_self_modify_guards`,
  `test_reload_redteam` (all inject fake modules under generic keys / mutate `sys.path`).

### A4. Test-side drift fixes — **PROPOSED**

- **`test_preflight_coverage`:** change the mock patch target to
  `general_ludd.filestore.store.FileStore` (patch where the local import *resolves*, i.e.
  the source module), not `general_ludd.quality.preflight.FileStore` (which the
  function-local import at `preflight.py:143-144` never binds into the module namespace).
  Mechanism: `mock.patch("general_ludd.filestore.store.FileStore", ...)`. *(Verified: the
  `FileStore` class is defined at `src/general_ludd/filestore/store.py:12`, and
  `check_filestore()` imports it function-locally, so patching the `preflight` namespace
  is a no-op.)*
- **`test_pipeline_wiring`:** construct the `ModelProfile` with a finite
  `run_budget_usd=1000.0` (`ModelProfile.run_budget_usd`, `gateway.py:90`, default
  `200.0`, validator enforces finite non-negative) so the **per-profile** budget guard
  (`gateway.py:378`, `effective_cost > profile.run_budget_usd`) can fire — this rejection
  is independent of the caller's `budget_remaining` (which defaults to `float("inf")` at
  `call_model`, `gateway.py:439`, the "no limit" sentinel that the remaining-budget check
  never rejects against).

### A5. Environmental skip-guards — **PROPOSED**

- **`test_submodule_management`** (`tests/unit/test_submodule_management.py`): skip when
  submodules are uninitialized. Detection: a leading `-` in `git submodule status` output
  marks an uninitialized submodule; `pytest.skip(...)` in that case instead of failing.
  The skip pattern *already exists* in exactly one test
  (`test_submodules_are_at_specific_tags`, ~line 222) — the fix is to **extend it** to
  the `TestGitmodulesFile` class (`test_gitmodules_exists` etc.), which currently
  `assert GITMODULES.exists()` and hard-fails when `.gitmodules` is absent.
- **`test_sdd_integration`** (`tests/unit/test_sdd_integration.py`): a
  `_require_devspark()` helper (lines 8-21) `pytest.skip(...)`s when `.devspark` is
  absent/unpopulated and is *already called by ~7 tests*. The fix is to **extend the
  guard** to the ~15 tests that currently bypass it (`test_deepspec_skill_installed`, the
  twelve `test_make_sdd_*_target` tests, `TestDevSparkConstitution`,
  `TestDeepSpecIntegration`), which fail hard when the scaffolding is absent.
- **Rationale:** these are repo-shape dependencies, not code-under-test; skipping keeps
  them honest across checkouts and shards without weakening real coverage. Both guards
  are partially present today; the work is extending them to the bypassing tests.

### A6. Durable snapshot/restore-all-logging-state fixture — **PROPOSED**

- **Mechanism:** the eventual replacement for A1. Instead of an ancestor allowlist,
  snapshot the *entire* logging state (root config, `logging.disable` level, and every
  logger's `level`/`propagate`/`handlers` and `disabled` flag from the manager dict) at
  test entry and restore it wholesale at exit. Backstops any logging mutation, including
  loggers outside the `general_ludd` ancestry, so new tests can't reintroduce
  caplog-empty order-dependence.
- **Beneficiaries:** all logging-sensitive tests; supersedes A1 once validated as
  non-regressive across shards.
