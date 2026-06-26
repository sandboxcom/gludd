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
| XT-9 | `routers/messages.py:101-128` | HIGH | confirmed | `GET /api/messages` degraded fallback path ignores `project_id` |
| CC-1 | `lease.py:79-86` | HIGH | confirmed | `reclaim_expired_leases` requeues by status only → double-dispatch on mid-dispatch lease expiry |
| CC-2 | `repository.py:384-387` | MED | FIXED | `claim_runnable` had no `ORDER BY` → starvation (fixed in 63b2437+) |
| SS-1 | `routers/projects.py:80` | HIGH | confirmed | `admin_add_project` swallows persist+commit → silent data loss, returns 200 |
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
- **Apply-ready fix:** Thread `project_id` from the request down into the facet and into
  `get_aggregate_scores(project_id=project_id)`; add `.where(BenchmarkResultModel.project_id == project_id)`
  to the aggregation query. When `project_id` is `None`/unset, reject or scope to the
  caller's resolved project rather than returning the global aggregate.

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

- **File:line:** `src/general_ludd/routers/facts.py:427`
- **Problem:** The `/api/traces` endpoint takes no `project_id` parameter, so any caller
  receives traces across all projects.
- **Apply-ready fix:** Add a `project_id` query parameter (required, or resolved from the
  caller's auth context), validate it against known projects, and pass it through to
  `_traces_facet` (see XT-4).

### XT-4 — `routers/facts.py:403` — `_traces_facet(app)` not passed `project_id` — CONFIRMED

- **File:line:** `src/general_ludd/routers/facts.py:403`
- **Problem:** `_traces_facet(app)` is invoked with only `app`; it has no tenant scope
  and therefore queries traces globally. This is the implementation side of XT-3.
- **Apply-ready fix:** Change the signature to `_traces_facet(app, project_id)` and add
  `.where(<trace>.project_id == project_id)` to the underlying query. Update the call
  site (XT-3) to pass the validated `project_id`.

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

### XT-8 — `routers/embeddings.py:779-883` — corpus=events search queries ALL audit events — CONFIRMED

- **File:line:** `src/general_ludd/routers/embeddings.py:779-883`
- **Problem:** `POST /api/embeddings/search` with `corpus=events` runs semantic search
  over the entire audit-event corpus with no tenant filter, leaking other projects'
  audit history through nearest-neighbour results.
- **Apply-ready fix:** Add a `project_id` filter to the events search path (require it
  from the request/auth context, validate it, and constrain the candidate event set to
  that project before scoring). Apply the same scoping to any other corpus that contains
  per-project data.

### XT-9 — `routers/messages.py:101-128` — degraded fallback path ignores `project_id` — CONFIRMED

- **File:line:** `src/general_ludd/routers/messages.py:101-128`
- **Problem:** `GET /api/messages` honours `project_id` on the primary path, but the
  degraded/fallback branch drops the filter and returns messages across projects.
- **Apply-ready fix:** Apply the same `project_id` `.where(...)` constraint inside the
  fallback branch. If the fallback cannot enforce scoping, fail closed (return empty /
  error) rather than returning unscoped data.

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

### SS-1 — `routers/projects.py:80` — `admin_add_project` swallows persist+commit — CONFIRMED

- **File:line:** `src/general_ludd/routers/projects.py:80`
- **Problem:** `admin_add_project` swallows exceptions from `persist_project()` and
  `session.commit()`, so a failed write returns HTTP 200 while the project is not
  actually persisted — silent data loss with a success response.
- **Apply-ready fix:** Remove the broad swallow around `persist_project()` /
  `session.commit()`. Let the failure propagate (or catch, log with context, and return
  5xx). Only return 200/201 after the commit has succeeded. Add a test asserting that a
  commit failure yields a non-2xx response and that a 2xx response implies a readable
  row.

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
