> STATUS: POINT-IN-TIME analysis (dated). Reference/history — re-validate against current code/hooks before acting on its recommendations.

# Status Audit — Older In-Progress Tasks (#21, #23, #26-28, #31-32)

> Read-only verification pass, 2026-06-16. Method: code + test reading only
> (no gate/pytest run; Bash is policy-denied in this lane). Every **DONE**
> verdict is grounded in a real, readable passing test; no DONE is asserted
> without one. Where a task's named module is fully unit-tested but **not wired
> into the production daemon / event loop**, the verdict is **PARTIAL** — a
> library exists and is proven in isolation, but the end-to-end capability the
> task describes is not live.
>
> Note on numbering: the repo's evidence ledgers (TASKS.md) use G/H/S/F/M/W
> IDs, not these `#21`-`#32` issue numbers, so each task below is adjudicated by
> *capability* against the named file, not by a matching TASKS.md tick.

## Summary table

| # | Task | Verdict | Grounding (file:line + test) | Remaining work if not DONE |
|---|------|---------|------------------------------|----------------------------|
| 21 | GitHub Action passes in real CI | **PARTIAL** | Workflow exists & is complete: `.github/workflows/build.yml:31-71` (gate matrix 3.11/3.12 + molecule). Local proof only: `make gate` green per TASKS.md W16.1. CI-green itself **UNVERIFIED** — see `TASKS.md:450` ("CI-green is therefore UNVERIFIED-in-CI at commit time; must be confirmed by the next sandboxcom run"). No readable green-run artifact. | Confirm an actual green run on `sandboxcom/gludd` (admin-gated logs); the "Event loop is closed" fix could not be reproduced locally, so real-CI confirmation is the open item. |
| 23 | Non-blocking parallel scheduling (scheduler/event_loop) | **PARTIAL** | `Scheduler.plan()` fully implemented `scheduling/scheduler.py:68-181`; proven by `tests/unit/test_scheduler.py` (≈30 tests: batching, deps, cycles, greenfield). **But not wired**: module docstring `scheduling/scheduler.py:15-19` is a `# TODO(integration)` to "drive the event loop's tick from Scheduler.plan()"; `EventLoop` does not import `Scheduler` (`event_loop/loop.py:16-35`); `PHASE_ORDER` (`loop.py:38-50`) is a sequential sweep; no `test_scheduler_integration.py` exists. | Wire `Scheduler.plan()` into `EventLoop._phase_dispatch_execute_jobs` to launch concurrency-safe batches (e.g. `asyncio.gather`) while serializing shared-resource items; add an integration test proving parallel dispatch. |
| 26 | Dynamic dispatch (roles/collections/MCP/skills + live var/template updates) | **PARTIAL** | `DynamicDispatcher` implemented `dispatch/dynamic_dispatcher.py:142-237` (per-kind routing, capability lattice, fail-closed); `parse_tool_calls` `:69-135`; `VariableStore`+`apply_results` `dispatch/variable_store.py:20-114`. Proven by `tests/unit/test_dynamic_dispatcher.py` (routing, unknown-kind, handler-raise, dispatch_all). **Not wired**: docstring `dynamic_dispatcher.py:8-12` is a `# TODO(integration)` to call it "from the event-loop turn handler"; not imported by `loop.py`/`daemon.py`; no integration test. | Invoke `DynamicDispatcher` from the event-loop turn handler when a model returns `tool_calls`, write results into a `VariableStore`, and re-render the next prompt from it; add an integration test. |
| 27 | Daemon spend limiter $X/window (tokens+cloud) | **PARTIAL** | `SpendLimiter` rolling-window cap fully implemented `controllers/spend_limiter.py:42-267` (`try_charge`, `would_exceed`, fail-closed on unknown/non-finite cost, snapshot/restore, RLock); proven by `tests/unit/test_spend_limiter.py`. **The named module is NOT wired**: docstring `spend_limiter.py:17-27` is a `# TODO(integration)` to wire `would_exceed()` into the dispatch path; `daemon.py` does not import it (`daemon.py:26` imports `RunBudgetGuard`, not `SpendLimiter`). A *different* budget cap IS wired (`RunBudgetGuard` in `EventLoop`, proven by `tests/unit/test_budget_wiring.py::TestEventLoopBudgetGuard`), but it is per-run, not the rolling $X/window limiter this task names. | Either wire `SpendLimiter.try_charge()` into the model/infra dispatch path (with snapshot/restore across restart), or formally reconcile it with `RunBudgetGuard`/`BudgetManager` so there is one enforced window limiter, not two implementations. |
| 28 | Per-project accounting (time/money/LoC/role stats/todo) | **PARTIAL** | Per-project **cost** exists: `MetricsCollector.get_cost_by_project` surfaced via `GET /api/facts.metrics` (TASKS.md W12.1, `routers/facts.py`). Per-project **todo** counts exist via `TodoRepository.status_summary`. **Missing**: `ProjectManager` (`projects/manager.py:33-168`) tracks only weight/dispatch_mode/workspace — no time, no LoC, no per-role stats accounting; `get_summary()` `:147-168` returns allocation, not accounting. No `test_project_accounting.py` proving a unified time/money/LoC/role/todo ledger per project. | Add a per-project accounting aggregate (wall-time, USD, lines-of-code delta, per-role counts, todo throughput) — likely composing `MetricsCollector` + `TaskReturnRepository` + git LoC — and a test asserting all five dimensions per project. |
| 31 | Agent file-overlap coordination + merge-aware waiting | **PARTIAL** | Conceptually modeled two ways but **not as in-product code**: (a) `Scheduler` `resources` frozenset serializes shared-resource items (`scheduling/scheduler.py:52-65`, tested) — an abstract overlap model, not file-path aware; (b) merge-aware tooling exists in the Makefile (`wt-sync` clobber-guard, `wt-apply` 3-way, `wt-reap`) and policy in `docs/ORCHESTRATION.md:20-38`. **Missing**: no module that detects concrete file-path overlap between agent work items and makes one *wait* on another's merge; no `test_file_overlap.py`. | Implement a file-overlap coordinator (map each work item to the file paths it will touch, serialize items whose path sets intersect, and block a dependent until the overlapping item's branch is merged), wired into dispatch, with a test. |
| 32 | Orchestration planner: dogfood scheduler for parallelism | **PARTIAL** | `Scheduler` (`scheduling/scheduler.py`) is the planner and is fully tested in isolation (`tests/unit/test_scheduler.py`). **Not dogfooded**: nothing in the running daemon/event loop consumes `Scheduler.plan()` to drive its own parallelism — same unwired-integration gap as #23 (`# TODO(integration)` `scheduler.py:15-19`; not imported by `loop.py`/`daemon.py`). The orchestration *policy* the daemon should dogfood lives in `docs/ORCHESTRATION.md`, but the code path is sequential `PHASE_ORDER`. | Have the daemon/event loop actually call `Scheduler.plan()` over its own backlog to schedule concurrent ticks (self-hosting the planner), with an integration test proving the daemon dispatches a concurrency-safe batch in parallel. |

## Cross-cutting finding

Tasks **#23, #26, #27, #32** all fail for the *same* reason: each names a module
(`Scheduler`, `DynamicDispatcher`+`VariableStore`, `SpendLimiter`) that is
**fully implemented and thoroughly unit-tested in isolation but carries an
explicit `# TODO(integration)` and has no production caller**. Verified by:
- The `# TODO(integration)` docstrings in each module head.
- `EventLoop` imports (`event_loop/loop.py:16-35`) and `daemon.py` imports
  (`daemon.py:1-60`) containing none of these four classes.
- The absence of any `*_integration.py` test for them (reads returned "file
  does not exist").

So the *library layer* of the parallelism/dispatch/spend-cap product is DONE and
green; the *wiring layer* (the part that makes the capability real in the running
daemon) is OPEN. None of these seven can be called DONE on a skeptical reading.

## Evidence index (files read)

- `.github/workflows/build.yml` (CI definition) — #21
- `TASKS.md:444-451` (W16.1 "CI-green UNVERIFIED-in-CI"), `SESSION.md` — #21
- `src/general_ludd/scheduling/scheduler.py` + `tests/unit/test_scheduler.py` — #23, #32
- `src/general_ludd/dispatch/dynamic_dispatcher.py` + `dispatch/variable_store.py` + `tests/unit/test_dynamic_dispatcher.py` — #26
- `src/general_ludd/controllers/spend_limiter.py` + `tests/unit/test_spend_limiter.py`; `controllers/budget_manager.py`; `tests/unit/test_budget_wiring.py` — #27
- `src/general_ludd/projects/manager.py` + `projects/__init__.py`; TASKS.md W12.1 (`get_cost_by_project`) — #28
- `docs/ORCHESTRATION.md`; Makefile `wt-sync`/`wt-apply`/`wt-reap` — #31
- `src/general_ludd/event_loop/loop.py:1-50`, `src/general_ludd/daemon.py:1-60` (wiring negative-confirmation) — #23/#26/#27/#32
