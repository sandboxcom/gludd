# Cross-Tenant, Concurrency, Silent-Swallow & Async-Blocking Findings — 2026-06-26

Consolidated security/audit backlog so these findings are not lost. Each finding
records the exact `file:line`, the problem, and an apply-ready fix. Findings marked
**CONFIRMED** were validated by adversarial verification this session; **needs-fix**
items are recorded for remediation.

> Scope note: line numbers are pinned to the working tree on
> `feature/alpha4-green-the-gate` as of 2026-06-26. Re-pin at apply time.

## Summary table

| Finding ID | File:line | Severity | Status | One-line |
|---|---|---|---|---|
| XT-1 | `routers/facts.py:125` | HIGH | confirmed | `get_aggregate_scores()` not passed `project_id` (model has the column) |
| XT-2 | `routers/facts.py:226` | HIGH | confirmed | `FeatureRepository.list_all()` has no `project_id` filter |
| XT-3 | `routers/facts.py:427` | HIGH | confirmed | `/api/traces` endpoint exposes no `project_id` param |
| XT-4 | `routers/facts.py:403` | HIGH | confirmed | `_traces_facet(app)` not passed `project_id` |
| XT-5 | `routers/features.py:59-90` | HIGH | confirmed | `GET /api/features` returns ALL features (no project filter) |
| XT-6 | `routers/features.py:92-105` | HIGH | confirmed | `GET /api/features/{id}` no project-ownership check |
| XT-7 | `routers/features.py:108-173` | HIGH | confirmed | `POST /api/features/verify` accepts `project_id` but never uses it |
| XT-8 | `routers/embeddings.py:779-883` | HIGH | confirmed | `POST /api/embeddings/search` corpus=events queries ALL audit events unfiltered |
| XT-9 | `routers/messages.py:120-131` | HIGH | FIXED | `GET /api/messages` degraded fallback now scopes by `project_id` (fixed 6ae7e60, 3 tests) |
| CC-1 | `lease.py:79-86` | HIGH | confirmed | `reclaim_expired_leases` requeues by status only → double-dispatch on mid-dispatch lease expiry |
| CC-2 | `repository.py:384-387` | MED | FIXED | `claim_runnable` had no `ORDER BY` → starvation (fixed in 63b2437+) |
| SS-1 | `routers/projects.py:71-93` | HIGH | FIXED | `admin_add_project` swallow → now logs + re-raises → 422 (fixed 4d058a4) |
| AB-1 | `daemon_wiring.py:241-242` | HIGH | confirmed | sync `open()`+`yaml.safe_dump` in async `_collection_handler` |
| AB-2 | `routers/skills.py:127-128` | HIGH | confirmed | sync `open()`+`write` in async `admin_skills_fetch_github` |
| AB-3 | `routers/environment.py:713/644` | HIGH | confirmed | sync `open(/proc/meminfo)` in async `api_environment` |
| TG-1 | `routers/todos.py` (read/update endpoints) | MED | confirmed | read/update endpoints accept `project_id` but don't validate it → enumeration via 404-vs-422 |

REFUTED / already-closed (recorded so they are not re-raised):
- `models.py:362`, `todos.py:447` — silent-swallow **REFUTED** (pre-initialized safe defaults, not bugs).
- Budget spend-limiter TOCTOU — **already closed** (RLock, verified).
- Per-invoker budget accounting — **does not exist by design** (not a bug).
- Gateway circuit-breaker "cost not tracked on failure" — **correct by design**.

---

## Cross-tenant leaks (HIGH — all CONFIRMED)

These are tenant-isolation failures: data belonging to one project is reachable by
callers scoped to (or supplying) a different project, or no project scoping is applied
at all. `BenchmarkResultModel.project_id` (`models.py:639`) and
`FeatureModel.project_id` (`models.py:492`) both exist, so the columns are available to
filter on — these are missing-filter bugs, not schema gaps.

### XT-1 — `routers/facts.py:125` — `get_aggregate_scores()` not passed `project_id` — CONFIRMED

- **File:line:** `src/general_ludd/routers/facts.py:125`
- **Problem:** The benchmark-scores facet calls `get_aggregate_scores()` without a
  `project_id` argument, so it aggregates `BenchmarkResultModel` rows across all
  tenants. `BenchmarkResultModel` already has a `project_id` column (`models.py:639`),
  so the aggregation should be scoped.
- **Apply-ready fix (VERIFIED PURE ONE-LINER 2026-06-26):** at `facts.py:125` change
  `rankings = await repo.get_aggregate_scores()` to
  `rankings = await repo.get_aggregate_scores(project_id=project_id)`. `project_id` is
  already in scope in `_metrics_facet` (used by sibling facets) and propagated from the
  `/api/facts` endpoint (`:402`); `get_aggregate_scores` already accepts `project_id` and
  the query already filters on it (`repository.py:885-886`, `:930-931`). No signature or
  query change needed — highest value/effort item in the backlog. Optionally still decide
  the `project_id is None` policy (global aggregate vs reject) as a follow-up.

### XT-2 — `routers/facts.py:226` — `FeatureRepository.list_all()` has no project filter — CONFIRMED

- **File:line:** `src/general_ludd/routers/facts.py:226`
- **Problem:** The features facet uses `FeatureRepository.list_all()`, which returns
  every `FeatureModel` regardless of project. `FeatureModel.project_id` exists
  (`models.py:492`).
- **Apply-ready fix:** Add a `list_for_project(project_id)` method on
  `FeatureRepository` (`.where(FeatureModel.project_id == project_id)`) and call it here
  with the request-scoped `project_id`. Do not fall back to `list_all()` on a missing
  project — return an empty list or 422.

### XT-3 — `routers/facts.py:427` — `/api/traces` exposes no `project_id` param — CONFIRMED

- **File:line:** `src/general_ludd/routers/facts.py:427-437` (endpoint `api_traces`).
- **Problem:** The `/api/traces` endpoint takes no `project_id` parameter, so any caller
  receives traces across all projects.
- **⚠ SCOPE CORRECTION (2026-06-26): NOT a simple add-a-where fix.** The trace data model
  `ExecutionTrace` (`observability/tracer.py:81-143`) has **no `project_id` field at all**,
  and `RecentTracesBuffer.snapshot()` (`trace_store.py:86-92`) returns every trace with no
  project filter. So this is MULTI-LAYER: (1) add `project_id` to `ExecutionTrace`; (2)
  capture/populate it on the recorder path; (3) add a `project_id` filter to
  `RecentTracesBuffer.recent()/snapshot()`; (4) add a validated `project_id` query param to
  `api_traces`; (5+6) thread it through `_traces_facet` (XT-4). Deferred to a working
  window; do NOT attempt as a one-liner — there is no column to filter on yet.

### XT-4 — `routers/facts.py:403` — `_traces_facet(app)` not passed `project_id` — CONFIRMED

- **File:line:** `_traces_facet` defined at `facts.py:135-139` (sig: `(app, limit, todo_id)`);
  invoked at `:403` with no `project_id`.
- **Problem:** `_traces_facet(app)` has no tenant scope and queries traces globally. This is
  the facet side of XT-3.
- **Apply-ready fix:** Implementation side of the XT-3 multi-layer change — add `project_id`
  to the `_traces_facet` signature and pass it into the (newly project-aware)
  `RecentTracesBuffer` query; update the call site (`:403`) to pass the validated
  `project_id`. Cannot land independently of the tracer/store changes in XT-3.

### XT-5 — `routers/features.py:59-90` — `GET /api/features` returns ALL features — CONFIRMED

- **File:line:** `src/general_ludd/routers/features.py:59-90`
- **Problem:** The list endpoint calls `repo.list_all()` with no project filter, so the
  full cross-tenant feature set is returned.
- **Apply-ready fix:** Require a `project_id` (query param or auth context), validate it,
  and call `repo.list_for_project(project_id)` (see XT-2). Reject unscoped requests.

### XT-6 — `routers/features.py:92-105` — `GET /api/features/{id}` no ownership check — CONFIRMED

- **File:line:** `src/general_ludd/routers/features.py:92-105`
- **Problem:** Fetch-by-id returns the feature without verifying it belongs to the
  caller's project, allowing direct-object reference across tenants (IDOR).
- **Apply-ready fix:** After loading the feature, compare `feature.project_id` against
  the request-scoped `project_id`; return 404 (not 403, to avoid existence disclosure)
  on mismatch or when `project_id` is absent.

### XT-7 — `routers/features.py:108-173` — `POST /api/features/verify` ignores `project_id` — CONFIRMED

- **File:line:** `src/general_ludd/routers/features.py:108-173` (leak at line 127)
- **Problem:** The endpoint accepts a `project_id` in its request body but never uses it;
  line 127 calls `list_all()`, so verification runs against every project's features.
- **Apply-ready fix:** Use the accepted `project_id` to scope the query —
  `repo.list_for_project(project_id)` — and validate `project_id` is a known project
  (422 if not). Remove the `list_all()` call from this path.

### XT-8 — `routers/embeddings.py` corpus=events search queries ALL audit events — CONFIRMED

- **File:line (re-pinned 2026-06-26):** handler `api_embeddings_search` at
  `routers/embeddings.py:986-997`; `EmbeddingSearchRequest` at `:191-228` (no
  `project_id` field today); `_search_events` query at `:821-825`.
- **Problem:** `POST /api/embeddings/search` with `corpus=events` runs semantic search
  over the entire audit-event corpus with no tenant filter, leaking other projects'
  audit history through nearest-neighbour results.
- **Apply-ready fix:** `AuditEventModel.project_id` exists and is indexed
  (`models.py:374-378`, FK to projects, nullable) — no migration. Add
  `.where(AuditEventModel.project_id == project_id)` to the `_search_events` query
  (`:824`), add a validated `project_id` to `EmbeddingSearchRequest`/auth context, and
  thread it from the handler into `_search_events`. Apply the same scoping to any other
  corpus that contains per-project data. Effort: low (one-line SQL filter + param thread,
  NOT a store refactor) but it changes the endpoint signature, so it is NOT a pure
  one-liner — deferred to a working window, not this throttled one.

### XT-9 — `routers/messages.py:120-131` — degraded fallback path ignored `project_id` — FIXED

- **File:line:** `src/general_ludd/routers/messages.py:120-131`
- **Problem (historical):** `GET /api/messages` honoured `project_id` on the primary path,
  but the degraded/in-memory fallback branch dropped the filter and returned messages
  across projects whenever the daemon ran without a DB session factory.
- **Status:** **FIXED** (commit `6ae7e60`). The fallback loop now applies
  `if project_id is not None and m.get("project_id") != project_id: continue`, mirroring
  the primary path's `repo.inbox(project_id=...)`. Covered by 3 regression tests in
  `tests/unit/test_messages_fallback_project_isolation.py` (`3 passed`). An unscoped query
  (no `project_id`) still returns all recipient messages — back-compat preserved.

---

## Concurrency

### CC-1 — `lease.py:79-86` — `reclaim_expired_leases` double-dispatch — CONFIRMED

- **File:line:** `src/general_ludd/event_loop/lease.py:79-86` (path re-pinned 2026-06-26;
  `TodoModel.version` confirmed present at `models.py:198`, no migration needed)
- **Problem:** `reclaim_expired_leases` transitions `ACTIVE -> QUEUED` guarded by status
  only, with no version or holder check. When a 300s lease expires mid-dispatch, the row
  is requeued while the original holder is still working it, producing double-dispatch of
  the same todo.
- **Apply-ready fix:** Use compare-and-swap on `TodoModel.version` (the column already
  exists — no migration needed). Read `(status, version, holder)`, then perform the
  requeue as an `UPDATE ... WHERE id = :id AND version = :seen_version`, bumping
  `version` on success; if zero rows are affected, another writer won the race and the
  reclaim must be skipped. Optionally also gate on holder/lease-expiry timestamp to avoid
  reclaiming a lease that was renewed.

### CC-2 — `repository.py:384-387` — `claim_runnable` starvation — FIXED

- **File:line:** `src/general_ludd/.../repository.py:384-387`
- **Problem (historical):** `claim_runnable` had no `ORDER BY`, so claim order was
  non-deterministic and older queued todos could be starved.
- **Status:** **ALREADY FIXED this session** in commit `63b2437`+ via
  `.order_by(created_at, id)`. Recorded here for completeness; no further action.

---

## Silent-swallow

### SS-1 — `routers/projects.py:71-93` — `admin_add_project` swallowed persist+commit — FIXED

- **File:line:** `src/general_ludd/routers/projects.py:71-93`
- **Problem (historical):** `admin_add_project` swallowed exceptions from
  `persist_project()` and `session.commit()`, so a failed write returned HTTP 200 while
  the project was not actually persisted — silent data loss with a success response.
- **Status:** **FIXED** (commit `4d058a4`). The handler now `logger.error(...)`s and
  `raise`s on persist/commit failure; the outer handler converts it to HTTP 422, so a
  2xx response now implies a committed row. Re-verified 2026-06-26. No further action;
  a regression test asserting "commit failure → non-2xx" would still be a worthwhile add.

### Refuted silent-swallow candidates (NOT bugs)

- `models.py:362` — **REFUTED.** Pre-initialized safe default; no data loss.
- `todos.py:447` — **REFUTED.** Pre-initialized safe default; no data loss.

---

## Async-blocking (HIGH per-request)

These perform synchronous blocking I/O on the event loop inside `async` request
handlers, stalling all concurrent requests for the duration of the I/O.

### AB-1 — `daemon_wiring.py:241-242` — blocking file write in async handler — CONFIRMED

- **File:line:** `src/general_ludd/.../daemon_wiring.py:241-242`
- **Problem:** `_collection_handler` is `async` but calls synchronous `open()` followed by
  `yaml.safe_dump(...)` directly, blocking the event loop during serialization + disk
  write.
- **Apply-ready fix:** Move the blocking work off the loop with
  `await anyio.to_thread.run_sync(...)` (or `asyncio.to_thread`), or precompute the YAML
  string and use an async file API. Keep the serialization + write together inside the
  threaded call.

### AB-2 — `routers/skills.py:127-128` — blocking file write in async handler — CONFIRMED

- **File:line:** `src/general_ludd/routers/skills.py:127-128`
- **Problem:** `admin_skills_fetch_github` is `async` but performs synchronous
  `open()`+`write` of the fetched skill content, blocking the loop on disk I/O.
- **Apply-ready fix:** Wrap the `open()`+`write` in `await asyncio.to_thread(...)` (or
  `anyio.to_thread.run_sync`), or use an async file library.

### AB-3 — `routers/environment.py:713/644` — blocking `/proc/meminfo` read in async handler — CONFIRMED

- **File:line:** `src/general_ludd/routers/environment.py:713` (read) / `:644` (call site in `api_environment`)
- **Problem:** `_system_facet` opens and reads `/proc/meminfo` synchronously and is
  called directly from the `async api_environment` handler, blocking the loop on the
  procfs read.
- **Apply-ready fix:** Offload `_system_facet` via `await asyncio.to_thread(_system_facet, ...)`
  (or `anyio.to_thread.run_sync`) at the call site (`:644`), keeping the synchronous file
  read confined to the worker thread.

---

## Test-gap / recon vector

### TG-1 — `routers/todos.py` read/update endpoints don't validate `project_id` — CONFIRMED

- **File:line:** `src/general_ludd/routers/todos.py` —
  `api_get_todo`, `api_list_scheduled_todos`, `api_pause_schedule`, `api_resume_schedule`
- **Problem:** These read/update endpoints accept a `project_id` but never validate that
  it refers to a known project. The CREATE endpoints DO validate (returning 422 for an
  unknown project). The inconsistency means a probe with an unknown `project_id` returns
  404 from read/update but 422 from create — the differing status codes let an attacker
  enumerate which project IDs exist (recon vector), and the missing validation is an
  isolation gap.
- **Apply-ready fix:** Add the same project-existence validation used by the CREATE path
  to all four read/update endpoints, returning a consistent 422 for unknown projects (or
  a uniform 404 across both create and read/update so existence is never disclosed). Add
  tests asserting identical status codes for unknown-project requests across create vs.
  read/update endpoints to close the enumeration vector.

---

## Budget audit notes (no action — recorded for completeness)

- **Spend-limiter TOCTOU:** already closed via `RLock` (verified). No fix needed.
- **Per-invoker accounting:** does not exist by design; absence is not a defect.
- **Gateway circuit-breaker — cost not tracked on failure:** correct by design (a failed
  call incurs no tracked spend). Not a bug.
