# SPEC — Tenant (Project) Isolation

**Status:** DRAFT (turnkey). Verify-first; every claim re-checked against the tree
on 2026-07-14. Verdicts: CONFIRMED / REFUTED / PARTIALLY-CONFIRMED / UNVERIFIED.

**Through-line:** gludd has **two independent tenant-isolation mechanisms, and
NEITHER enforces anything at the query layer.**
1. A `contextvar`-based tenant (`db/tenant.py`) that is **written but never read**.
2. An explicit `project_id` repository argument that **defaults to `None` =
   fully unscoped**, which the very module that sets the dead contextvar forgets to
   pass to its own repositories.

The tests give **false assurance**: they hand-write the `WHERE project_id = ?`
clause inside the test body, so they would pass identically if `db/tenant.py` were
deleted. The result is at least one concrete leak-shaped read path
(`routers/accounting.py`) that does a full-table scan across all projects and
enforces isolation with an in-memory Python dict instead of the query.

> Cautionary example (record in the spec): this cluster was marked **DONE (C.3)** in
> a prior ledger and **the claim was FALSE** — the ledger has since been corrected.
> "Done" without a test that proves a cross-tenant read is *impossible* is not done.

---

## Verify-first summary table

| # | Claim | Verdict | Anchor |
|---|-------|---------|--------|
| 1 | `get_tenant()` has zero prod call sites (write-only contextvar) | **CONFIRMED** | `db/tenant.py:28`; readers only in `tests/` |
| 2 | `set_tenant`/`reset_tenant` called from loop.py; value never read | **CONFIRMED** | `event_loop/loop.py:737`, `:785` |
| 3 | `db/session.py` hooks are PRAGMA-only; no ORM tenant hook exists | **CONFIRMED** | `db/session.py:47-57/106-110`; no `with_loader_criteria`/`do_orm_execute` anywhere |
| 4 | Tests exercise contextvar in isolation, not through a query | **PARTIALLY-CONFIRMED** | `tests/unit/test_db_tenant_scoping.py` |
| 5 | Real scoping = explicit `project_id` ctor arg, default `None` = unscoped | **CONFIRMED** | `db/repository.py:194-218/259-265` |
| 6 | loop.py builds repos with NO `project_id` | **CONFIRMED** | `event_loop/loop.py:745-748/764-767/1817-1818` |
| 7 | `routers/accounting.py` reads ALL projects, buckets in a Python dict | **CONFIRMED (docstring stale)** | `routers/accounting.py:163-167/169/191` |
| 8 | ~15 unscoped methods; `TaskReturnRepository.get_by_id` unscoped | **CONFIRMED (exact count UNVERIFIED)** | `db/repository.py:641-644`; no `TaskReturnRepository.scoped` |
| 9 | `MemoryRecordModel` lacks a `project_id` column | **REFUTED — it now HAS one** | `db/models.py:774-780`; filtered at `repository.py:2822/2906` |
| 10 | TASKS.md C.3 marked DONE | **UNVERIFIED (not found in TASKS.md)** | grep found no C.3 entry in TASKS.md |

---

## Verified findings (detail)

### 1–2. The contextvar is written and never read (CONFIRMED)
- `db/tenant.py:28 def get_tenant() -> str | None` — repo-wide, the only readers of
  `get_tenant` are `tests/unit/test_db_tenant_scoping.py` (19 call sites) plus its
  re-export in `db/__init__.py:36,93`. **Zero production readers.**
- `event_loop/loop.py:737 _tenant_token = _set_tenant(self._tick_project_id)` and
  `loop.py:785 _reset_tenant(_tenant_token)` (in a `finally:`). The contextvar is
  set per tick and reset, but nothing ever calls `get_tenant()` to consume it. It is
  dead state.

### 3. No ORM-level tenant hook (CONFIRMED)
- `db/session.py:47-57` (`_set_sqlite_pragmas`: journal_mode/busy_timeout/
  synchronous/foreign_keys/temp_store/mmap_size/cache_size) and `:106-110`
  (`_set_query_only`: `PRAGMA query_only=ON`) are pure SQLite PRAGMA connect hooks.
- Repo-wide grep for `with_loader_criteria` → **no matches**; `do_orm_execute` →
  **no matches**. There is **no** global ORM filter that could consume the
  contextvar. So even if `get_tenant()` were read, nothing wires it to queries.

### 4. Tests give false assurance (PARTIALLY-CONFIRMED)
`tests/unit/test_db_tenant_scoping.py` has two halves:
- `TestTenantContextPropagation` / `TestContextvarInThread` (~`:60-135`) assert only
  `get/set/reset` contextvar semantics — no DB at all (exactly as claimed).
- `TestTenantScopingWithDB` (~`:138-286`) DOES run real queries on a two-project
  seed — **but** every filter clause `.where(TodoModel.project_id == current)`
  (e.g. `:155/:185/:232/:265`) is **hand-written inside the test**, using
  `current = get_tenant()`; it never calls `TodoRepository` or any production
  filtering code. Since no prod code reads `get_tenant()` (finding 1), these tests
  prove nothing about production behavior and would pass with `db/tenant.py` deleted.
  Substance of the claim holds.

### 5. Explicit scoping defaults to unscoped (CONFIRMED)
`db/repository.py:194-196`:
```python
def __init__(self, session: AsyncSession, project_id: str | None = None) -> None:
    self._session = session
    self._project_id = project_id
```
`scoped(cls, session, project_id)` at `:198-208`; `_resolve_pid` at `:214-218`
returns the explicit arg or `self._project_id`; `get_by_id` at `:259-265` only adds
`.where(TodoModel.project_id == _pid)` **`if _pid is not None`.** So `None` (the
default) = **no filter = every project's rows**. Same shape on `FeatureRepository`
(`scoped` at `:1587`).

### 6. loop.py forgets to scope its own repos (CONFIRMED)
`event_loop/loop.py:745-748`:
```python
self._todo_repo = TodoRepository(session)
self._task_return_repo = TaskReturnRepository(session)
self._audit_repo = AuditEventRepository(session)
self._variable_repo = VariableNamespaceRepository(session)
```
Repeated at `:764-767`; and `:1817-1818` builds
`VariableNamespaceRepository(job_session)` / `TaskReturnRepository(job_session)`.
None pass `project_id` — all fully unscoped — **in the same module that sets the
dead `set_tenant` contextvar.** This is the core irony: the tenant *is* known
(`self._tick_project_id`) but is pushed only into the unread contextvar, not into
the repositories that run the queries.

### 7. Concrete leak-shaped read (CONFIRMED; docstring stale)
`routers/accounting.py:163-167`:
```python
async with factory() as session:
    todo_repo = TodoRepository(session)
    role_repo = RoleRunRepository(session)
    all_todos = await todo_repo.list_all()     # UNSCOPED — every project
    all_roles = await role_repo.list_all()     # UNSCOPED — every project
```
then buckets by `pid = t.project_id or ""` (`:169`) and `r.project_id or ""`
(`:191`) in a Python dict. Output is correct, but isolation is enforced by an
in-memory dict over a **full-tenant table scan**, not by the query. The module
docstring (`accounting.py:10-11`) claims these calls are "project-filtered" — that
comment is **stale/false**; the calls take no args.

### 8. Open unscoped methods (CONFIRMED; precise count UNVERIFIED)
- `TaskReturnRepository.get_by_id` (`db/repository.py:641-644`) has **no
  `project_id` parameter at all**: `select(TaskReturnModel).where(return_id == ...)`.
- `TaskReturnRepository` has **no `.scoped()` classmethod** (only `TodoRepository`
  and `FeatureRepository` do). A caller can never lock a `TaskReturnRepository`
  instance to a tenant; scoping exists only as optional per-call `project_id=` kwargs
  on `work_summary`/`history_summary`/`claim_unreviewed` (`:646/:676/:725`) — easy to
  omit, and loop.py + accounting.py do omit it.
- `FeatureRepository.get_by_name` (`:1696-1699`) also has no `project_id` param.
- The pattern is widespread (loop.py ×3, accounting.py ×2, plus facts/self_improve
  call sites), but an exact integer count would require reading the full ~2900-line
  `repository.py` class-by-class — **mark "~15" UNVERIFIED for precision; the pattern
  is CONFIRMED.**

### 9. MemoryRecordModel `project_id` (REFUTED — the prior "CRITICAL bleed" is fixed)
`db/models.py:774-780` now defines:
```python
project_id: Mapped[str | None] = mapped_column(
    String(32), ForeignKey("projects.project_id", ondelete="SET NULL"),
    nullable=True, index=True,
)
```
and it is used for filtering: `repository.py:2822`
`.where(MemoryRecordModel.project_id == project_id)` and `:2906`
(`list_by_namespace`). The prior audit's "CRITICAL cross-project memory bleed" is
**no longer accurate** — docs asserting it are stale. (Unrelated: a *different*
still-open D26 finding about `MemoryRecordModel` VACUUM scheduling exists in
`security/security_backlog.py:522-528` — do not conflate.)

### 10. TASKS.md C.3 (UNVERIFIED)
Grep of TASKS.md for `tenant`/`C.3`/`scoping`/`ThreadPoolExecutor`/`db/tenant.py`
returned **no hits inside TASKS.md** (only `src/`/`tests/` comments reference
"C.3"). Could not locate the DONE entry as worded. Record the *lesson* (a false DONE
was corrected) as a cautionary note regardless of the exact ledger line.

---

## DESIGN — make scoping the DEFAULT (fail-closed)

Two viable directions. **Recommend Option B** (typed required `project_id`) as the
primary, with Option A as a defense-in-depth backstop if adopted later.

### Option A — session-level `with_loader_criteria` driven by `get_tenant()`
Register a `do_orm_execute` event on the async session that, for every mapped class
carrying a `project_id` column, injects
`with_loader_criteria(Model, Model.project_id == get_tenant(), include_aliases=True)`
when `get_tenant()` is not `None`. This finally makes the contextvar *load-bearing*.

- **Pros:** one hook covers all repos; callers can't forget; loop.py's existing
  `set_tenant` at `:737` immediately starts filtering.
- **Cons:** implicit/global; a missing `set_tenant` = silent unscoped (fail-OPEN)
  unless you add a strict mode that *raises* when a project-scoped model is queried
  with no tenant set; cross-project admin reads (accounting) need an explicit
  `escape` context manager. sqlite + async event wiring needs care.
- **Sketch:**
```python
@event.listens_for(Session, "do_orm_execute")
def _tenant_filter(state):
    if state.is_select and not state.execution_options.get("skip_tenant"):
        tid = get_tenant()
        if tid is not None:
            state.statement = state.statement.options(
                with_loader_criteria(HasProjectId,
                    lambda cls: cls.project_id == tid, include_aliases=True))
        elif STRICT:  # fail-closed
            raise TenantScopeError("query issued with no tenant set")
```

### Option B (RECOMMENDED) — delete the contextvar; make ctor `project_id` required
Remove `db/tenant.py`'s `set/get/reset` from the loop path and change every
project-scoped repository constructor to **require** a typed `project_id: str`
(no default), plus an explicit `AdminRepository` / `.unscoped(session)` factory for
the handful of legitimate cross-project reads (accounting/facts).

- **Pros:** explicit and typed — "forgot to scope" becomes a **compile-time / mypy
  error**, not a silent leak. No global magic. Matches the existing `.scoped()`
  intent, just makes it mandatory.
- **Cons:** touches every construction site (loop.py ×5, routers, worker); the few
  genuine cross-project reads must opt in via `.unscoped()`.
- **Before** (`db/repository.py:194`):
```python
def __init__(self, session: AsyncSession, project_id: str | None = None) -> None:
```
  **After:**
```python
def __init__(self, session: AsyncSession, *, project_id: str) -> None:   # required
    ...
@classmethod
def unscoped(cls, session: AsyncSession) -> "TodoRepository":
    """EXPLICIT cross-project access. Every use must be justified in review."""
    obj = cls.__new__(cls); obj._session = session; obj._project_id = None
    return obj
```
- **loop.py After** (`:745`):
```python
self._todo_repo = TodoRepository(session, project_id=self._tick_project_id)
```
- **accounting.py After** (`:163`) — make the cross-project intent explicit:
```python
todo_repo = TodoRepository.unscoped(session)   # accounting spans all projects
```
- Add `TaskReturnRepository.scoped` + make its `get_by_id` take `project_id`
  (`repository.py:641`), closing finding 8's sharpest edge.

### Migration implications
- **No schema migration needed for the core fix** — every scoped model already has a
  `project_id` column (including `MemoryRecordModel`, finding 9). This is a
  code-level enforcement change, not a data migration.
- Backfill audit: confirm existing rows have non-null `project_id` where the new
  required-scope path will filter on it; rows with `NULL project_id` become invisible
  to scoped reads (intended) but must be reachable via `.unscoped()` admin paths.
- Option A additionally needs the `HasProjectId` mixin/registry and a strict-mode
  flag; Option B needs a mypy pass to catch all now-required kwargs.

---

## Failing tests to write (RED today — prove a cross-tenant read is IMPOSSIBLE)

These are the tests whose absence let the false DONE stand. Each must go through
**production repository code**, never a hand-written WHERE clause:

1. `tests/unit/test_tenant_isolation.py::test_todo_repo_default_cannot_read_other_project`
   — seed projects A and B; build `TodoRepository` the way loop.py does; call
   `list_all()`/`get_by_id()`; assert **zero** project-B rows returned. **Fails
   today** (returns both).
2. `::test_task_return_get_by_id_scoped` — `TaskReturnRepository` for project A must
   not return a project-B `return_id`. **Fails today** (`get_by_id` has no
   `project_id`).
3. `::test_accounting_uses_scoped_queries_not_full_scan` — spy the emitted SQL (or
   repo calls) for `/accounting`; assert it does NOT call `list_all()` unscoped, or
   assert an explicit `.unscoped()` justification path. **Fails today.**
4. `::test_variable_namespace_repo_scoped_in_loop` — the loop's
   `VariableNamespaceRepository` (`loop.py:747/1817`) must not read another
   project's variables. **Fails today.**
5. `::test_mypy_rejects_unscoped_construction` (Option B) — a `# type: ignore`-free
   `TodoRepository(session)` must be a mypy error (required kwarg). Enforced via the
   existing typecheck gate.
6. (Option A) `::test_do_orm_execute_injects_tenant_filter` and
   `::test_strict_mode_raises_without_tenant` — prove the hook filters and
   fail-closes.

Delete/rewrite `tests/unit/test_db_tenant_scoping.py`'s hand-written-WHERE DB tests
so they cannot mask a regression again.

---

## Landing order

1. **Choose Option B** (recommended) — make `project_id` a required typed kwarg on
   scoped repositories; add `.unscoped()` + `TaskReturnRepository.scoped`.
2. **Update all construction sites** — `event_loop/loop.py:745-748/764-767/1817-1818`
   (scope with `self._tick_project_id`), routers, worker. Let mypy find them.
3. **Convert the genuine cross-project readers** (`routers/accounting.py:163`,
   facts) to explicit `.unscoped()`.
4. **Land the RED-first isolation tests** (above) and delete the false-assurance
   contextvar DB tests.
5. **Remove the dead contextvar** (`db/tenant.py` set/get/reset) from the loop path,
   OR (if Option A is also adopted) wire it into `do_orm_execute` as a
   defense-in-depth backstop with strict-mode fail-closed.
6. Update the stale `accounting.py:10-11` docstring and any docs still asserting the
   MemoryRecordModel bleed (finding 9) or a DONE C.3 (finding 10).

## Risk / rollback
- **Fail-closed direction only:** the change can *hide* rows a caller previously saw
  (a scoped caller loses other-project rows) but can never *expose* new rows. A
  missed `.unscoped()` on a legitimate admin path surfaces as an obviously-empty
  result in testing, not as a silent leak.
- Option B is mechanically revertible (restore the `= None` default), but doing so
  re-opens the leak — so land the isolation tests FIRST so any revert turns them RED.
- No data migration to roll back; this is code-level enforcement over an
  already-present column.
- CI is the gate (local gate OOMs). Land with the RED→GREEN isolation tests and a CI
  run id as evidence per `AGENTS.md`.
