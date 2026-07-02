# CI Green Plan — 2026-07-01 (RESOLVED — updated 2026-07-02)

Status: **GREEN.** Branch tip `005ddd03`. All 8 `test-shard`s + `gate` + `linux` +
`macos` PASS on `31da8746` (measured). `molecule` / `container` / `windows` / `termux`
are `continue-on-error` (non-blocking, informational).

Two blockers were found and fixed, in order: (1) a root-logger level leak that produced
the caplog empty-records failures, and (2) a latent `coverage`-job plumbing bug that
only surfaced once the shards started passing. Both are corrected below. The earlier
"order-dependent propagate-pollution" thesis in this doc was **wrong** and has been
replaced with the measured root cause.

**Fix-commit chain:** `3c6d8183 → 285c1c39 → 440aedd8 → e8c231e6 → cfff7d8c →
7be51c28 → 57bcb682 → 31da8746 → 005ddd03`.

**Push constraint (workflow files):** changes to `.github/workflows/build.yml` require
the **SSH deploy key** — the OAuth HTTPS token lacks the `workflow` scope, so pushing
build.yml edits over HTTPS is rejected.

## 1. State

The suite is green. The final measured state:

| Item | SHA | Result |
|------|-----|--------|
| 8 × `test-shard` + `gate` + `linux` + `macos` | `31da8746` | **PASS** (measured) |
| `coverage` job (combine plumbing) | `005ddd03` | **PASS** (build.yml guard) |
| `molecule` / `container` / `windows` / `termux` | — | `continue-on-error` (non-blocking) |

## 2. Root Causes (measured — corrected)

Two blockers, resolved in order.

### (a) Root-logger LEVEL leak — the actual cause of the caplog-empty failures

The caplog empty-records failures were **not** caused by leaf `propagate=False` (there
are **zero** `propagate=False` loggers in the tree). The real leak was a **root-logger
level** mutation plus a stray global `logging.disable`:

- `tests/unit/test_daemon.py::test_log_level_endpoint_changes_level` drove the **root
  logger to DEBUG** via the `/admin/log-level` endpoint, then hardcoded it back to
  `WARNING` **without restoring the original level**. A later caplog-sensitive test
  (running under a shard/order where it followed this test) then saw records swallowed
  by the leaked root level.
- A stray process-global `logging.disable(...)` left set by an earlier test compounded
  it: caplog captures nothing while a global disable is in effect.

**Fix (measured):**
- `test_daemon` now **saves and restores the root-logger level** around the endpoint
  test (commit `7be51c28`).
- The conftest resets the global disable with `logging.disable(logging.NOTSET)` on every
  test.
- The per-subtree `propagate` / ancestor-level resets in conftest (appendix A1) are
  **largely defensive** — they backstop future mutation, but were **not** the operative
  fix, since no leaf disabled propagation.

### (b) Latent `coverage`-job plumbing bug — the second blocker

The CI `coverage` job `needs` `test-shard`, so it **never ran** while the shards were
red. Once the shards started passing, the `coverage` job ran for the first time and
**failed** on `coverage combine` over an **empty glob** — a latent plumbing bug that had
simply been masked by the shards never succeeding.

**Fix (commit `005ddd03`, in `build.yml`):**
- **Guard the `coverage combine`** step so it tolerates an empty / partial set of
  coverage artifacts instead of erroring.
- Each `test-shard` now runs `coverage combine` **before** the `cp` of its artifact, so
  the aggregation job receives well-formed inputs.

Because build.yml is a workflow file, this fix had to be pushed with the **SSH deploy
key** (HTTPS OAuth token lacks `workflow` scope).

### (c) Environmental repo-state fragility (pre-existing, backstopped)

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

The two blockers above (2a root-logger level leak, 2b coverage-combine plumbing) are the
operative fixes that turned CI green. The steps below are the broader defensive
backstops applied alongside them to keep the suite order-independent:

1. **Logging-state isolation** — *DONE (defensive).* `test_daemon` root-level
   save/restore (`7be51c28`) + conftest `logging.disable(NOTSET)` reset were the real
   fix; the ancestor-only conftest fixture is a backstop, not the operative cause.
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

## 5. Honest Note (resolved)

The original thesis in this doc — "order-dependent propagate-pollution" — was **wrong**.
There are zero `propagate=False` loggers in the tree. The measured root cause of the
caplog-empty failures was a **root-logger level leak** in
`test_daemon::test_log_level_endpoint_changes_level` (drove root to DEBUG, hardcoded
back to WARNING without restore) plus a stray global `logging.disable`. The durable fix
was the `test_daemon` root-level save/restore (`7be51c28`) and the conftest
`logging.disable(NOTSET)` reset — the per-subtree resets are defensive only.

A **second** blocker surfaced only after the shards went green: the `coverage` job
(`needs: test-shard`) had never run, so its `coverage combine`-over-empty-glob bug was
latent. Fixed in `build.yml` (`005ddd03`): guard the combine, and have each shard run
`coverage combine` before `cp`. Because build.yml is a workflow file, that push needed
the SSH deploy key (HTTPS OAuth token lacks `workflow` scope).

Final state: green on branch tip `005ddd03`; all 8 shards + gate + linux + macos PASS on
`31da8746`; molecule/container/windows/termux are `continue-on-error`.

## Appendix — Remediation Proposals (ready-to-apply)

The concrete fixes drafted this session, consolidated as an actionable checklist. Each
item lists **status**, the **mechanism** (what to change and how), and the **beneficiary
tests** it unblocks. Apply top-to-bottom: each earlier item shrinks the pollution
surface the later ones must backstop.

### A1. Ancestor-only conftest logging fixture — **DONE, but DEFENSIVE** (committed)

> **Correction:** this fixture was *not* the operative fix for the 7 caplog empty-records
> failures. The real cause was a **root-logger level leak** in
> `test_daemon::test_log_level_endpoint_changes_level`, fixed by a root-level
> save/restore (`7be51c28`); the conftest `logging.disable(logging.NOTSET)` reset handles
> the stray global disable. The propagate/ancestor resets below are a backstop only —
> there are zero `propagate=False` loggers in the tree.


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
