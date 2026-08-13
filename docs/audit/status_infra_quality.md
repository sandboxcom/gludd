# Status Audit — Infra / Quality Issues (#35, #36, #37, #39, #40, #47, #62, #67, #68)

Read-only verification pass, 2026-06-16. Method: direct Read of the Makefile, configs,
CI workflow, source, and tests. No `make`/pytest was run (read-only, make-only repo), so
"green" claims below are about **presence + wiring of the gate**, not a freshly executed
suite. Tool note: Glob/Grep were unavailable in this session, so file *enumeration* of
`scripts/`/`tools/`/`alembic/`/`collections/` is partial — verdicts rely on files I opened
directly plus the Makefile/CI recipes, which are authoritative.

Evidence anchors:
- Makefile: `/Users/shawnwilson/gludd/Makefile`
- CI: `/Users/shawnwilson/gludd/.github/workflows/build.yml`
- pytest cfg: `/Users/shawnwilson/gludd/pyproject.toml`
- ansible-lint cfg: `/Users/shawnwilson/gludd/.ansible-lint`
- pre-commit: `/Users/shawnwilson/gludd/.pre-commit-config.yaml`

## Summary table

| # | Issue | Verdict | One-line basis |
|---|---|---|---|
| 35 | widen lint+typecheck to ALL tracked Python | **PARTIAL** | `lint-all`/`typecheck-all` exist but the **gate/CI still run only `ruff check src tests` + `mypy src`** — wider scope is not enforced |
| 36 | enforce YAML + ansible-lint (no toothless `\|\| true`) | **PARTIAL** | `yaml-lint` fails-closed, but it is **not in `gate`/`validate`/CI**; the in-gate `ansible-syntax` is syntax-only; `ansible-lint-playbooks` still ends `\|\| true` |
| 37 | molecule coverage real (no no-op / dir!=pass / TODO) | **DONE** | `tests/integration/test_molecule_coverage.py` partitions full module+role inventory; both `_NOT_YET_COVERED_*` sets empty; `molecule-test-all` runs every scenario and fails on any |
| 39 | CI gate timeout root-cause (1 xdist worker → >40min) | **DONE** | `GLUDD_XDIST` env override (`Makefile:11-15`) + CI sets `GLUDD_XDIST: auto`; per-test `timeout=180`; gate `timeout-minutes: 40` |
| 40 | concurrent-pytest collision → per-run basetemp | **DONE** | gate pins `--basetemp=/tmp/gludd-gate-basetemp`; `test-iso`/`test-xdist` use unique `$$`/`$ID` basetemp dirs |
| 47 | re-add test_gludd_reload + role_self_improve_propose molecule scenarios that PASS | **DONE (pass unverified)** | Both scenarios present with full molecule.yml + prepare/converge/verify asserting real behavior; gated by #37's checklist. "Pass" not re-run this session |
| 62 | standing integrate+reclaim loop (wt-reap/wt-sync-all) | **DONE** | `wt-reap`, `wt-sync-all`, `wt-sync`, `wt-apply`, `wt-import`, `wt-changed`, `wt-remove-many` all present in Makefile |
| 67 | recover #52 DB-race hardening (repository.py; test_db_redteam.py green) | **PARTIAL** | `db/repository.py` hardening is **present and thorough**; but **`test_db_redteam.py` does NOT exist** and DB tests only assert single-session version guards, not true 2-session races |
| 68 | wt-sync clobber-guard | **DONE (code)** / no test | Clobber guard present in `Makefile` `wt-sync` (REFUSED on locally-modified-vs-HEAD); **no automated test** asserts it |

---

## #35 — widen lint + typecheck to ALL tracked Python — PARTIAL

**What's enforced today (the gate):**
- `Makefile` `lint:` → `ruff check src tests` (`Makefile:187-188`)
- `Makefile` `typecheck:` → `mypy src` (`Makefile:193-194`)
- `gate` phase 1 runs `ruff check src tests` (`Makefile:235`); phase 2 runs `mypy src` (`Makefile:242`)
- CI `gate` job runs `make lint typecheck ...` (`build.yml:51`) — i.e. the same `src tests` / `src` scope.

**What exists but is NOT wired in:**
- `lint-all:` → `ruff check src tests collections scripts alembic tools molecule` (`Makefile:860-861`)
- `typecheck-all:` → `mypy src scripts tools` (`Makefile:862-863`)

**Untracked-by-gate Python confirmed present (opened directly):**
- `/Users/shawnwilson/gludd/scripts/plan_work.py` (and ~12 other `scripts/*.py` referenced by Makefile targets)
- `/Users/shawnwilson/gludd/alembic/env.py`
- `/Users/shawnwilson/gludd/collections/ansible_collections/general_ludd/agent/plugins/modules/gludd_ping.py` (collection modules)

**Verdict basis:** the *capability* to lint/type the whole tree exists (`lint-all`/`typecheck-all`),
but the binding gate (local `gate`/`validate` and CI) still only covers `src`+`tests`. So scripts,
alembic migrations, collection modules, tools, and molecule plays are **not** lint/type-gated.
**Remaining work:** make `gate`/`validate`/CI call `lint-all`/`typecheck-all` (or fold those
dirs into the `lint`/`typecheck` targets), and burn down whatever errors that surfaces; note
`typecheck-all` only adds `scripts tools` — `mypy` still ignores `collections`, `alembic`, `molecule`.

## #36 — enforce YAML + ansible-lint (no toothless `|| true`) — PARTIAL

**Real, fail-closed:**
- `.ansible-lint` exists with `profile: production` and a justified 2-rule skip_list
  (`var-naming[no-role-prefix]`, `yaml[line-length]`) — correctness rules stay on
  (`/Users/shawnwilson/gludd/.ansible-lint:1-32`).
- `Makefile` `yaml-lint:` → `ansible-lint playbooks collections/.../roles` with **no `|| true`**
  (`Makefile:865-866`) — this one fails closed.
- pre-commit has `check-yaml` (`.pre-commit-config.yaml:7-8`).

**Still toothless / not enforced:**
- `ansible-lint-playbooks:` still ends in `|| true` (`Makefile:346-347`) — the exact anti-pattern the issue targets, still present (a second, separate target).
- `yaml-lint` is **NOT** part of `gate` (5 phases: lint, typecheck, collect, test, smoke — `Makefile:227-274`), **NOT** in `validate` (`Makefile:1397-1404`), and **NOT** in the CI `gate` step (`build.yml:48-54`). So the fail-closed ansible-lint never runs in the binding gate.
- The gate's ansible coverage is `ansible-syntax` (syntax-check only, via `validate`), not lint.

**Verdict basis:** the fail-closed target was built, but the issue's intent ("enforce") is unmet
because nothing in the gate/CI invokes `yaml-lint`, and a `|| true` ansible-lint target still exists.
**Remaining work:** add `yaml-lint` to `gate`/CI; delete or fix `ansible-lint-playbooks`'s `|| true`.

## #37 — molecule coverage real (no no-op CLI / dir≠pass / TODO burndown) — DONE

Evidence: `/Users/shawnwilson/gludd/tests/integration/test_molecule_coverage.py`.
- Inventory partition gate: `TestModuleCoverageChecklist.test_inventory_partition_is_exact`
  (`:147-168`) and `TestRoleCoverageChecklist.test_inventory_partition_is_exact` (`:178-191`)
  assert that (covered ∪ not-yet-covered) exactly equals the real module/role inventory read
  from `collections/.../plugins/modules/gludd_*.py` and `.../roles/`. Adding a scenario without
  ticking the checklist → fail; deleting a covered scenario → fail.
- The two TODO/burndown sets are both **empty**: `_NOT_YET_COVERED_MODULES: set[str] = set()`
  (`:58`) and `_NOT_YET_COVERED_ROLES: set[str] = set()` (`:79`) — i.e. burndown complete and
  guarded against regression.
- "dir ≠ pass" honesty: module scenarios must ship a `prepare.yml` that launches the mock daemon
  (`test_module_scenarios_start_the_mock_daemon`, `:135-143`); exemplars must have molecule.yml +
  converge.yml + verify.yml (`:124-133`).
- "no no-op CLI": `Makefile` `molecule-test-all` (`:372-386`) loops EVERY dir under
  `molecule/playbooks/`, runs each via `molecule test`, and **`exit 1` if any FAILED**. CI runs it
  (`build.yml:56-71`, `make molecule-test-all`).
- The legacy helper `src/general_ludd/quality/molecule_coverage.py` (`MoleculeCoverageChecker`) is a
  separate compute-coverage utility, not the gate; the test file above is the binding gate.

**Caveat:** I did not execute molecule this session, so "scenarios green" is asserted-by-CI, not
re-verified here.

## #39 — CI gate timeout root-cause (single xdist worker → >40min) — DONE

Evidence:
- Root cause + fix in `Makefile:11-15`: comment states a 4-vCPU runner's `cpu//4 == 1` made the
  gate sit ~38min near the 40min wall; `_XDIST_WORKERS` now honors a `GLUDD_XDIST` env override:
  `print(v if v else max(1, (os.cpu_count() or 1)//4))`; `_XD = -n $(_XDIST_WORKERS) --dist loadgroup`.
- CI sets it: `build.yml:54` `GLUDD_XDIST: auto` in the `gate` job env, so CI uses pytest-xdist
  auto worker count instead of 1.
- Belt-and-suspenders: per-test `timeout = 180` / `timeout_method = "signal"` (`pyproject.toml:123-124`)
  caps any single hung test; `gate` job `timeout-minutes: 40` (`build.yml:34`).
**Remaining work:** none for the root cause. (Optional: confirm `auto` on a 4-vCPU runner actually
yields >1 workers — `auto` = #logical CPUs, which fixes the `//4 == 1` problem by design.)

## #40 — concurrent-pytest collision → per-run basetemp isolation — DONE

The beta.3 release gate exposed the final unisolated path: release integration
and E2E phases still used pytest's default
`<temproot>/pytest-of-<user>/pytest-N` namespace.  A concurrent cleanup removed
the live E2E `pytest-3/popen-gw0` directory at 93%, causing fixture setup
`FileNotFoundError` despite the tests themselves being healthy.

Current evidence:

- `gate`, `gate-lite`, `gate-release-phases`, `test-integration`, `test-e2e`,
  `test-iso`, `test-xdist`, and the adaptive CI shard runner all create
  invocation-unique `gludd-*` basetemps.
- `gate-release-phases` owns one `mktemp` root with separate `integration` and
  `e2e` children, and its EXIT/INT/TERM traps remove only that owned root.
- `scripts/clean_tmp.py` never removes a shared `pytest-of-*` root or live
  `pytest-N` child. It reclaims only pytest's atomically renamed `garbage-*`
  children; `disk-guard.sh` delegates to that scoped cleaner instead of using
  broad `rm -rf /tmp/pytest-of-*` globs.
- `tests/unit/test_release_gate_execution.py` overlaps two release gates behind
  a deterministic barrier, proves distinct namespaces, and proves owner-only
  cleanup. `tests/unit/test_clean_tmp.py` proves cleanup preserves a simulated
  live `pytest-3/popen-gw0` tree while removing sibling garbage.

Long-lived upstream/user reports support treating the default temp root as
shared, not owned:

- pytest's documentation says concurrent invocations require a unique base
  directory, keeps only the last three default roots, and warns that an explicit
  `--basetemp` is cleared blindly:
  <https://docs.pytest.org/en/stable/how-to/tmp_path.html>
- pytest issue #5524 records a real CI/xdist race while concurrently creating a
  basetemp: <https://github.com/pytest-dev/pytest/issues/5524>
- pytest issue #11789 and linked #11790 document the easily missed retention
  behavior and lack of default support for concurrent invocations:
  <https://github.com/pytest-dev/pytest/issues/11789>
- A user running concurrent MPI pytest processes reported `--basetemp`
  collisions when more than one process tried to own the same path:
  <https://stackoverflow.com/questions/79064328/how-to-fix-fileexistserror-when-using-the-basetemp-flag-with-pytest>

**Remaining work:** none for this failure class; unique ownership and concurrent
regressions now cover every release pytest entrypoint.

## #47 — re-add test_gludd_reload + role_self_improve_propose molecule scenarios that PASS — DONE (pass unverified)

Both scenarios are present with real verify logic (not stubs):
- `test_gludd_reload`:
  - `/Users/shawnwilson/gludd/molecule/playbooks/test_gludd_reload/molecule.yml` — sets
    `ANSIBLE_COLLECTIONS_PATH`, `GLUDD_MOCK_PORT=8840`, an import-clean `PYTHONPATH=/tmp/gludd-reload-8840`
    (the comment notes the previously-reverted scenario failed precisely for lacking this), and a
    custom test_sequence skipping idempotence (hot-swap is non-idempotent).
  - `.../default/verify.yml` asserts the **healthy** candidate was promoted (`success`, not
    `rolled_back`) and the **degraded** candidate was rolled back fail-closed, plus a promotion
    artifact on disk.
- `role_self_improve_propose`:
  - `/Users/shawnwilson/gludd/molecule/playbooks/role_self_improve_propose/molecule.yml` —
    `GLUDD_MOCK_PORT=8841`, idempotence skipped (creates a real git worktree).
  - `.../default/verify.yml` asserts a real candidate worktree dir was created and the proposal JSON
    fields (target_module = lowest-coverage file, status=proposed, signals present).
- Both are tied into #37's coverage gate: `test_molecule_coverage.py` comments explicitly cite
  `gludd_reload` (re-added under #47, `:56-57`,`:73`) and `self_improve_propose` (`:78`,`:117`) as
  now-covered, and the empty `_NOT_YET_COVERED_*` sets force them to remain present.

**Verdict basis:** re-added with substantive verify playbooks and guarded against re-removal.
**Caveat:** "PASS" is asserted by CI (`molecule-test-all`), not re-executed in this read-only pass.

## #62 — standing integrate+reclaim loop (wt-reap / wt-sync-all) — DONE

Evidence (all `Makefile`):
- `wt-reap:` (`:771-782`) — "Drain the WHOLE integrate+reclaim lane (#62) in one command": for every
  agent worktree it `wt-sync`s (clobber-guarded) then `git worktree remove --force`, honoring a
  `KEEP=` token list so live agents aren't destroyed. The recipe comment names #62 explicitly.
- `wt-sync-all:` (`:735-741`) — bulk wt-sync of a `SRCS='...'` list, tolerant of missing worktrees.
- Supporting lane: `wt-sync` (`:714-730`), `wt-apply` 3-way (`:748-756`), `wt-import` (`:705-708`),
  `wt-changed` (`:785-787`), `wt-remove-many` (`:760-763`), `wt-prune-safe` (`:800-806`),
  `wt-prune-force-merged` (`:818-830`).
**Remaining work:** none for the targets' existence. "Standing loop" is operator-invoked (`make wt-reap`),
not a daemon/cron — acceptable per the issue's "loop now exists?" framing.

## #67 — recover #52 DB-race hardening (repository.py; test_db_redteam.py green) — PARTIAL

**Hardening present and thorough** — `/Users/shawnwilson/gludd/src/general_ludd/db/repository.py`:
- `_is_locked_error()` (`:61-70`) treats SQLite `SQLITE_BUSY` ("database is locked") as a lost race.
- `TodoRepository.update` (`:90-131`) — guarded conditional UPDATE keyed on `id AND version`;
  rowcount≠1 → `ConcurrencyError`.
- `TodoRepository.claim_runnable` (`:158-229`) — optimistic `WHERE id AND status='queued' AND version`
  claim; `OperationalError`→lost-race skip; rowcount≠1 → refresh+skip (no double-claim).
- `TodoRepository.transition` (`:278-324`) — guard on version AND status.
- `TaskReturnRepository.claim_unreviewed` (`:397-444`) — status-only guard + SQLITE_BUSY handling.
- TOCTOU upserts via `on_conflict_do_update`/`do_nothing`: `VariableNamespaceRepository.set_var`
  (`:544-596`), `PromptProfileRepository.upsert` (`:707-730`), `FeatureRepository.upsert` (`:987-1022`).
- `ProjectRepository.deactivate` (`:809-829`), `AgentMessageRepository.ack` (`:887-910`) — guarded UPDATEs.

**The named proof is MISSING / weaker than claimed:**
- `test_db_redteam.py` **does not exist** anywhere under `tests/` (searched; NOT FOUND).
- The actual DB tests in `/Users/shawnwilson/gludd/tests/unit/test_db_models.py` only exercise the
  guards from a **single session**: `test_update_todo_version_mismatch_raises` (`:338-342`) and
  `test_transition_version_mismatch_raises` (`:369-373`) pass `expected_version=999`; `claim_runnable`
  tests (`:375-388`) only check queued-vs-backlog selection. **None spins up two concurrent
  sessions/claimers racing the same row** — so the race-hardening code paths
  (`_is_locked_error`, rowcount-0 lost-race skip) are not directly proven by a red-team test.

**Verdict basis:** code is recovered and looks correct, but the issue's explicit acceptance
(`test_db_redteam.py` green) is unmet — there is no red-team/concurrency test, and existing tests
don't drive a real race. **Remaining work:** add a true concurrent test (two `claim_runnable`
callers on overlapping candidates → exactly-once; concurrent `transition`/`update` at same version
→ one `ConcurrencyError`; ideally a `SQLITE_BUSY` path) and name it as the proof.

## #68 — wt-sync clobber-guard — DONE (code) / no test

Evidence: `/Users/shawnwilson/gludd/Makefile` `wt-sync` (`:714-730`). Before copying a file from a
worktree onto main, it checks: dst exists AND tracked-in-main AND locally-modified-vs-HEAD AND
content differs (`cmp -s`) → prints `⛔ REFUSED (CLOBBER GUARD): ... use make wt-apply` and `continue`s
(skips the copy) (`:721-726`). The trailer line confirms "clobber-guard active: locally-modified files
are refused, use wt-apply" (`:730`). `wt-reap`/`wt-sync-all` inherit the guard since they call `wt-sync`.
The complementary safe path (`wt-apply`, 3-way merge) exists at `:748-756`.

**Verdict basis:** guard is present and correct in the recipe. **Remaining work:** no automated test
asserts the guard (it's shell logic in a Makefile target). Optional hardening: a test that stages a
locally-modified file and asserts `wt-sync` refuses it.

---

## Cross-cutting note on "green"

This was a static audit. `make gate` was not executed (read-only, make-only repo). Per project
memory the gate was last verified green at a prior HEAD with the ratchet (`config/ratchet.yml`,
11 entries; `RATCHET_MAX = 11` in `tests/unit/test_guardrails.py`) absorbing known failures.
The DONE verdicts above attest the **gate/tooling is wired**; they do not re-attest a fresh green run.
The two PARTIALs that matter for shipping are **#35** (wide lint/type not gate-enforced) and
**#36** (fail-closed ansible-lint not in the gate) — both are "built but not wired into the binding
gate" — plus **#67** (hardening present, named red-team test absent).
