# Post-Commit Backlog — 2026-06-24

Consolidated from 6 parallel audit runs. Items within each section ordered highest→lowest severity.

---

## PERFORMANCE

| # | Title | File:Line | Issue | Fix | Severity |
|---|-------|-----------|-------|-----|----------|
| P1 | status_summary N+1 | `db/repository.py:381-399` | Fetches all records then aggregates in Python | Replace with SQL `GROUP BY` query | HIGH |
| P2 | sync file I/O blocks async startup | `daemon.py:120-157` | Blocking file reads/writes in async startup path | Wrap with `asyncio.to_thread` or pre-load at import | HIGH |
| P3 | unbounded ledger growth | `event_loop/loop.py` | Ledgers accumulate without eviction (partially fixed) | Add TTL-based expiry or `maxlen` on ledger collections | MEDIUM |
| P4 | prompt-template re-parse per dispatch | `agents/dispatcher.py` (dispatch path) | Template parsed from string on every invocation | Cache parsed template at agent-load time | MEDIUM |
| P5 | session-per-job thrash | `db/repository.py` (job dispatch path) | New DB session created per job call | Use a shared session pool / session factory scoped to request | MEDIUM |
| P6 | work_summary N+1 (CONFIRMED 2026-06-25) | `db/repository.py:484-506` TaskReturnRepository.work_summary | `select(TaskReturnModel)` all rows + Python count by status/queue/work_type (feeds /api/facts) | 3× `func.count()`+`group_by` queries | HIGH |
| P7 | history_summary unbounded fetch | `db/repository.py:508-537` | Selects ALL task returns (no LIMIT despite recent_limit=10) just to count + head-slice | aggregate `count()`/`sum(case)` + separate `.order_by().limit()` | HIGH |
| P8 | count_by_role Python GROUP BY | `db/repository.py:1350-1360` | Loads all role-run rows, counts per role in Python | `select(role, func.count()).group_by(role)` | MEDIUM |
| P9 | unread_counts Python GROUP BY + Python TTL | `db/repository.py:1081-1100` | Loads all unread, counts per recipient in Python; TTL filtered in Python (unbounded) | push TTL into WHERE + `group_by(recipient)` | MEDIUM |
| P10 | list_for_task_type full scan + Python JSON filter | `db/repository.py:901-915` | Selects ALL prompt profiles, parses task_types JSON per row in Python | SQLite json_each/LIKE prefilter + Python backstop | MEDIUM |
| P11 | purge_expired row-by-row delete | `db/repository.py:1066-1079` | Fetch all TTL rows + per-row `session.delete()` (N deletes) | single set-based `delete().where(...)` + rowcount | MEDIUM |
| P12 | unbounded `.all()` list methods | `db/repository.py` (list_all/list_by_* across Todo/PromptProfile/Queue/Project/Feature/RoleRun/Spend) | several read methods can return whole table, no LIMIT | add optional limit/offset + server-side max (copy TodoRepository.list_all shape) | LOW |

NOTE (2026-06-25 audit): P1 status_summary N+1 is CONFIRMED STILL PRESENT (the GROUP BY patch in POST_COMMIT_VERIFIED_PATCHES_2026-06-24.md is unapplied). `count_active`, `BenchmarkRepository.get_aggregate_scores`, `SpendRepository.total_since` already use correct SQL aggregation — the model the HIGH fixes should follow. The per-row guarded UPDATE in `claim_runnable`/`claim_unreviewed` is a deliberate optimistic-concurrency pattern (NOT a perf defect).

---

## ASYNC

| # | Title | File:Line | Issue | Fix | Severity |
|---|-------|-----------|-------|-----|----------|
| A1 | admin_selftest subprocess blocks loop | `routers/integrity.py:220` | Synchronous subprocess call on async event loop | Wrap with `asyncio.to_thread` | HIGH |
| A2 | gather without timeout | `event_loop/loop.py:872`, `agents/dispatcher.py:121` | `asyncio.gather` has no timeout; a hung coroutine stalls the loop indefinitely | Add `asyncio.wait_for(..., timeout=N)` or `asyncio.gather` with `return_exceptions=True` + deadline | HIGH |
| A3 | `_background_tasks` set mutated without lock | `event_loop/loop.py` (background task tracking) | Race condition: set add/discard from multiple coroutines without an asyncio lock | Guard mutations with `asyncio.Lock` or use a thread-safe collection | MEDIUM |

---

## ERROR-HANDLING

| # | Title | File:Line | Issue | Fix | Severity |
|---|-------|-----------|-------|-----|----------|
| E1 | Silent except-pass in core loop | `event_loop/loop.py:114,283,394,422,743` | Bare `except: pass` swallows errors silently | Add `logger.exception(...)`, narrow exception type, or fail-closed | HIGH |
| E2 | Silent except-pass in worker | `worker/app.py:71,124` | Exceptions caught and discarded without logging | Log at ERROR level + propagate or mark job failed | HIGH |
| E3 | Silent except in job invocation | `models/job_invocation.py:103,183` | Broad catch-and-continue hides invocation failures | Narrow to specific exceptions; log + set failure state | MEDIUM |

---

## COVERAGE

| # | Title | File:Line | Issue | Fix | Severity |
|---|-------|-----------|-------|-----|----------|
| C1 | No tests for daemon `/readyz` | `daemon.py` | Health endpoint has zero test coverage | Add `TestClient` unit test asserting 200 + schema | HIGH |
| C2 | `to_thread` / timeout_detector untested | `event_loop/loop.py` | Async-offload paths and timeout detection not exercised | Add targeted async tests with a stubbed executor | MEDIUM |
| C3 | `worker.build_gateway` untested | `worker/app.py` | Gateway construction logic has no unit test | Add test with mocked config to assert gateway shape | MEDIUM |
| C4 | MCP transport pins untested | `mcp/` transport layer | Transport pin/negotiation paths not covered | Add integration-style tests with a loopback MCP server | MEDIUM |
| C5 | Integrity scanner untested | `routers/integrity.py` | Scanner logic only exercised by admin_selftest (E2E) | Extract scanner fn and add unit tests | LOW |

---

## DOCS

| # | Title | File:Line | Issue | Fix | Severity |
|---|-------|-----------|-------|-----|----------|
| D1 | ~85% of HTTP API undocumented | `routers/` | No OpenAPI descriptions on most routes | Add `summary=`, `description=`, and response models to all routers | HIGH |
| D2 | No SECURITY.md / operator guide | `docs/` | `GL_INTEGRITY_KEY`, PSK fail-closed, `allowed_cidr`, env-scrub not documented | Create `SECURITY.md` + operator runbook section in `docs/` | HIGH |
| D3 | Stale audit-file citations | `docs/audit/` older files | References to superseded SHAs and line numbers | Sweep and update or tombstone stale audit docs | LOW |
| D4 | 3 stale docstrings | Various | Docstrings describe pre-refactor behavior | Update or remove during next pass through each file | LOW |

---

## DEPENDENCIES

| # | Title | File:Line | Issue | Fix | Severity |
|---|-------|-----------|-------|-----|----------|
| Dep1 | Remove langchain / langchain-openai / langgraph | `pyproject.toml` | Unused; carries 2 HIGH CVEs | Drop from `[project.dependencies]`; verify no import references remain | CRITICAL |
| Dep2 | Bump starlette ≥ 1.3.1 | `pyproject.toml` | CVE-2026-54283 (HIGH) in current pin | Raise lower bound: `starlette>=1.3.1` | HIGH |
| Dep3 | Bump cryptography ≥ 48.0.1 | `pyproject.toml` | Known CVE in older versions | Raise lower bound: `cryptography>=48.0.1` | HIGH |
| Dep4 | Bump pydantic-settings ≥ 2.14.2 | `pyproject.toml` | CVE in current range | Raise lower bound: `pydantic-settings>=2.14.2` | MEDIUM |
| Dep5 | Bump msgpack ≥ 1.2.1 | `pyproject.toml` | CVE in current range | Raise lower bound: `msgpack>=1.2.1` | MEDIUM |
| Dep6 | diskcache CVE — no upstream fix | `pyproject.toml` | No patched version available | Add compensating control (restrict cache dir perms, document risk) | MEDIUM |

---

## SECURITY FOLLOW-UPS (surfaced 2026-06-24 during D1 router audit — VERIFY then fix)

| # | Title | File | Issue | Action |
|---|-------|------|-------|--------|
| SF1 | slurm `command` injection vector | `routers/slurm.py` (`/admin/slurm/submit`) | User-supplied `command` may reach a shell/sbatch without sanitization | VERIFY how `command` is executed; if shell=True or string-interpolated into a script, switch to arg-list / validate against an allowlist |
| SF2 | `/admin/self-update/*` missing auth deps | `routers/self_update.py` | Plan/apply routes may lack the PSK/auth dependency other admin routes carry (defense-in-depth beyond the daemon middleware) | VERIFY the daemon PSK middleware covers `/admin/self-update/*`; add explicit route-level auth dep if not |

Note: D1 (router OpenAPI docs) full plan is in the session agent transcript — ~76% of ~160
routes undocumented; 20 modules at 0%. Wave 1 = top-10 security/core routes + Pydantic
response models; Wave 2 = batch one-line summaries; Wave 3 = typed response models.

## Top 5 — Do First

1. **Dep1** — Drop langchain/langchain-openai/langgraph: zero usage, 2 HIGH CVEs, pure subtraction.
2. **Dep2/Dep3** — Bump starlette + cryptography: two HIGH CVEs, single-line pyproject.toml changes.
3. **E1** — Silent except-pass in `event_loop/loop.py` (5 sites): hides all runtime errors in the core loop.
4. **A1** — `admin_selftest` subprocess blocking the event loop (`routers/integrity.py:220`): can stall all async traffic.
5. **P1** — N+1 in `status_summary` (`db/repository.py:381-399`): degrades under any non-trivial dataset.
