# Daemon Integration Plan — wiring the built-but-unwired modules

Status: design only (uncommitted). Author pass: integration-planning, read-only.

This is the executable blueprint for wiring gludd's well-tested-but-unreachable
library modules into the running daemon. Every step is grounded in code read
from the live working tree. A follow-up wave should be pure execution.

---

## 0. Ground truth — what is ALREADY wired (do NOT re-wire)

Before planning new work, the live `src/general_ludd/daemon.py` (read in full)
already wires several modules the task list flagged. Re-wiring them would be
churn and would collide with existing code. Confirmed already-live:

| Module | Where it's wired today | Evidence |
|---|---|---|
| `controllers/spend_limiter.py` (`SpendLimiter`) | `_lifespan` builds it from `uc.budget` (`daemon.py:672-702`), rehydrates from DB (`_restore_persisted_spend`, `daemon.py:287-327`), stores on `app.state._spend_limiter` | The "TODO(integration)" in `spend_limiter.py:17` is **stale** — `make_spend_guarded_executor` (`daemon_wiring.py:195`) wraps the gateway executor at `daemon.py:726-730`. |
| `routers/spend.py` | `spend.register(app, _daemon_state)` at `daemon.py:1206` | live |
| `scheduling/scheduler.py` (`Scheduler.plan`) | `EventLoop._dispatch_jobs_via_scheduler` (`event_loop/loop.py:697-786`) calls `Scheduler().plan(items)` (line 740) inside `_phase_dispatch_execute_jobs` | The "TODO(integration)" in `scheduler.py:15` is **stale** — the loop already batches via the scheduler. |
| `dispatch/dynamic_dispatcher.py` handlers | `dispatch_router.register(...)` at `daemon.py:1198-1205` with lazy `role`/`mcp`/`skill` handlers built from `daemon_wiring.make_*_handler` (`daemon.py:1173-1196`) | The "TODO(integration)" in `dynamic_dispatcher.py:8` (call from the event-loop turn handler + re-render from VariableStore) is **NOT** done — see §6 below; the HTTP `/api/dispatch` path IS wired but the in-loop turn handler is not. |
| `pipeline/controller.py` + `pipeline/daemon_adapters.make_dispatch_fn/make_merge_fn/make_disk_ok` | `_build_pipeline_controller` (`daemon.py:430-474`) + start at `daemon.py:743-752`, gated on `uc.pipeline.enabled` | live; but `make_pid_provider` is **NOT** passed — see §5. |
| `controllers/pid` PID controller | `EventLoop._phase_evaluate_pid_controllers` (`event_loop/loop.py:589-616`) feeds `_phase_dispatch_execute_jobs` via `_tick_state["pid_outputs"]` → cap at `loop.py:669-686` | live |

**Genuinely unwired modules** (the actual gap) and their targets:

1. `connectors/registry.py` + `routers/observe.py` (`wire_observability`) — §1
2. `observe/facade.py` (`GluddObserve`) — §2 (rides on §1's registry)
3. `receiver/router.py` + `receiver/buffer.py` — §3
4. `issue_sources/ingest.py` (+ `issue_sources/base.py` engine) — §4
5. `pipeline/daemon_adapters.make_pid_provider` — §5 (close the pipeline gap)
6. `dispatch/dynamic_dispatcher.py` in-loop turn handler + `variable_store.py` — §6
7. `self_update/apply.py` + `self_update/priority.py` (+ a new `routers/self_update.py`) — §7

---

## 1. ConnectorRegistry + observe router (`wire_observability`)

**Module entry point:** `routers.observe.wire_observability(app, daemon_state, config, *, factories=None)`
(`routers/observe.py:130-173`) — builds `ConnectorRegistry.from_config(config)`
(`connectors/registry.py:89-107`), stores it on `app.state._connector_registry`,
and calls `register(app, daemon_state)` which mounts three routes
(`/api/observe/sources`, `/api/observe/health`, `/api/observe/query`).

### (a) daemon.py construction + state key
- Config source: the operator's connector config list. Load it in
  `load_startup_config` (`daemon.py:72`) from a new `connectors.yml` under the
  config dir, into `cfg["connectors"]` (mirrors how `mcp_servers` is loaded at
  `daemon.py:140-154`). Each entry is a dict carrying `name`/`kind` + a selector
  (`factory`/`class`/`module`) + `*_env` NAMES (per `registry.py` docstring).
- No `_lifespan` construction needed — `wire_observability` builds the registry
  synchronously at app-creation time. **State key:** `app.state._connector_registry`
  (set by `wire_observability` itself, `observe.py:161`).

### (b) router register call (group with the other `*.register` calls)
Add to the `create_daemon_app` router block (`daemon.py:1146-1208`), after
`facts.register(app, _daemon_state)` (line 1148-ish):
```python
from general_ludd.routers.observe import wire_observability
wire_observability(
    app, _daemon_state,
    app.state._startup_config.get("connectors", []),
)
```
PSK posture: the three `/api/observe/*` paths are NOT in `_PUBLIC_PATHS`
(`daemon.py:955-958`) and MUST NOT be added — they are PSK-gated exactly like
`/api/facts` (confirmed by `observe.py:14-19` security note). No change to
`_PUBLIC_PATHS` or `_is_public`.

### (c) event_loop phase
None. This is a pure HTTP surface.

### (d) UserConfig config block
Add a `connectors: list[dict[str, Any]] = []` field to `UserConfig`
(`config/user_config.py:88-97`) so it can also be set via
`GLUDD_CONNECTORS='[...]'` / YAML, and have `load_startup_config` prefer the
dedicated `connectors.yml` file, falling back to `uc.connectors`.

### (e) integration test
`tests/integration/test_observe_router_wired.py`:
- Build app via `create_daemon_app(config_dir=<tmp with connectors.yml holding
  one factory-built fake source>)`; assert `app.state._connector_registry` is a
  `ConnectorRegistry` with the source registered.
- `GET /api/observe/sources` returns the source metadata (name/kind/family),
  NO secret/env value present.
- Assert `GET /api/observe/sources` WITHOUT a Bearer PSK returns 401 when
  `GLUDD_PSK` is set (proves PSK-gated, not public).
- `POST /api/observe/query` with `{"source": "<name>", "spec": {}}` returns
  records; unknown source → 404 (`observe.py:117-121`).

---

## 2. GluddObserve cross-source facade

**Module entry point:** `observe.facade.GluddObserve(provider)`
(`observe/facade.py:100`). Its `provider` is structural — it accepts a
registry-like object exposing `by_kind()`/`list_sources()`
(`facade.py:104-115`), which is exactly what `ConnectorRegistry` exposes
(`registry.py:168-185`). So this composes directly on §1's registry with no new
dependency.

### (a) daemon.py construction + state key
In `create_daemon_app`, immediately after the `wire_observability(...)` call
(§1), construct and store:
```python
from general_ludd.observe.facade import GluddObserve
app.state._observe_facade = GluddObserve(app.state._connector_registry)
```
**State key:** `app.state._observe_facade`. (Construction is cheap and pure; no
`_lifespan` work needed.)

### (b) router register call
Extend `routers/observe.py`'s `register` (or add a sibling) with two debugging
endpoints reading `app.state._observe_facade` lazily (same lazy-lookup idiom as
`_get_registry`, `observe.py:72-74`):
- `POST /api/observe/timeline` body `{window: [kinds], start?, end?, spec?}` →
  `facade.timeline(...)` (`facade.py:244-258`).
- `POST /api/observe/correlate` body `{seed, kinds?, by?, window_s?}` →
  `facade.correlate_incident(...)` (`facade.py:207-241`).
Keep PSK-gated; do NOT add to `_PUBLIC_PATHS`. Degrade to empty when
`_observe_facade` is None (mirrors `observe.py:91-92`).

### (c) event_loop phase
None.

### (d) UserConfig block
None beyond §1's `connectors`.

### (e) integration test
`tests/integration/test_observe_facade_wired.py`: with two fake sources of
different KINDs registered, `POST /api/observe/timeline {"window":["logs"]}`
returns time-ordered records from the matching source only; a failing source
yields an `error`-level record, never a 500 (`facade.py:191-198`).

---

## 3. Receiver router + buffer (push-side ingest)

**Module entry points:** `receiver.router.register(app, _daemon_state, *, rate_per_sec=, rate_burst=)`
(`receiver/router.py:175`) reads/creates `_daemon_state["receiver_buffer"]`
(`router.py:193-196`); `receiver.buffer.ReceiverBuffer` (`buffer.py:53`).
The router's own docstring (`router.py:42-77`) specifies the exact wiring.

### (a) daemon.py / _lifespan construction + state key
Per `router.py:48-60`, construct the buffer in
`_get_or_create_extended_subsystems` (`daemon.py:824-877`), next to
`_recent_traces` (`daemon.py:830-832`):
```python
if not hasattr(app.state, "_receiver_buffer") or app.state._receiver_buffer is None:
    from general_ludd.receiver.buffer import OverflowPolicy, ReceiverBuffer
    app.state._receiver_buffer = ReceiverBuffer(
        maxlen=10_000, overflow=OverflowPolicy.REJECT, retention_s=3600,
    )
```
**State key:** `app.state._receiver_buffer`. Also expose on the shared dict so
`register` finds the same instance: in `create_daemon_app`, before calling
`receiver_router.register`, set `_daemon_state["receiver_buffer"] =
app.state._receiver_buffer`. NOTE: `_get_or_create_extended_subsystems` runs in
`_lifespan` (line 509), but `register` runs at app-creation (before lifespan).
To avoid an ordering trap, ALSO let `register` create its own buffer if absent
(it already does, `router.py:193-196`) and then have the lifespan adopt
`_daemon_state["receiver_buffer"]` if it is already a `ReceiverBuffer`, rather
than overwriting it. Concretely: in the lifespan, do
`app.state._receiver_buffer = _daemon_state.get("receiver_buffer") or
ReceiverBuffer(...)` so the router's request handlers and any drain consumer
share ONE instance.

### (b) router register call (group with the other `*.register` calls)
In `create_daemon_app` router block (`daemon.py:1146-1208`):
```python
from general_ludd.receiver import router as receiver_router
receiver_router.register(app, _daemon_state)
```

### (b-PSK) **CONFLICT / special PSK handling — flag.**
The receiver authenticates with its OWN `GLUDD_INGEST_TOKEN`, NOT the admin PSK
(`router.py:26-33, 200-217`). Its endpoints are POST (`/v1/logs`, `/v1/metrics`,
`/v1/traces`, `/ingest/webhook`, `/ingest/gelf`, `/ingest/fluent`,
`/ingest/beats`). The daemon's `_is_public` only treats SAFE methods as public
(`daemon.py:967-970`), so these POSTs would be challenged by the admin-PSK
middleware (`daemon.py:990-1006`) AND fail because callers present an ingest
token, not the PSK. Required change in `create_daemon_app`:
- Extend `_is_public` so receiver-owned paths bypass the admin PSK gate **for
  POST** (the in-router ingest-token auth then applies). Implement as a prefix
  allow-list, NOT a `_PUBLIC_PATHS` entry (those are method-gated and meant for
  read-only):
```python
_RECEIVER_PREFIXES = ("/v1/", "/ingest/")
def _is_receiver(path: str) -> bool:
    return any(path.startswith(p) for p in _RECEIVER_PREFIXES)
```
and in the middleware (`daemon.py:981` and `:998`) treat
`_is_public(method, path) or _is_receiver(path)` as not-PSK-gated. Keep the
fail-closed `GLUDD_REQUIRE_AUTH` 503 branch (`daemon.py:981-989`) applying only
to non-receiver, non-public paths so an unconfigured ingest token still
fail-closes inside the router (503 `ingest_disabled`, `router.py:203-213`).
This keeps admin PSK and ingest token as DISTINCT credentials (the whole point,
`router.py:31-33`). DO NOT put `/v1/*` or `/ingest/*` in `_PUBLIC_PATHS`.

### (c) event_loop phase — drain consumer
`router.py:76-77` notes the drain seam is out of scope for the build wave but is
needed for the data to be USED. Add a new phase `drain_receiver_buffer` to
`PHASE_ORDER` (`event_loop/loop.py:38-50`), positioned right after
`load_config_snapshot` (index 1, before `claim_unreviewed_task_returns`) so
incoming telemetry is available to the rest of the tick. Implementation
`_phase_drain_receiver_buffer`:
- pull `self._receiver_buffer` (new `EventLoop.__init__` param, default None),
  `records = buffer.drain(limit=500)` (`buffer.py:146-159`),
- feed each record to the metrics/trace store or (later) a connector bridge;
  minimal first cut: count drained records into `self._tick_metrics`. The buffer
  is bounded + thread-safe so this never blocks (`buffer.py:53-61`).
Pass the buffer into the `EventLoop(...)` constructor in `_lifespan`
(`daemon.py:598-624`) as `receiver_buffer=app.state._receiver_buffer`.

### (d) UserConfig block
Add `receiver: dict[str, Any] = {}` to `UserConfig` (`user_config.py:88-97`) for
`maxlen`/`overflow`/`retention_s`/`rate_per_sec`/`rate_burst`. The ingest token
stays an ENV var (`GLUDD_INGEST_TOKEN`), never in config (`router.py:108-110`).

### (e) integration test
`tests/integration/test_receiver_wired.py`:
- With `GLUDD_INGEST_TOKEN` set, `POST /ingest/webhook` with a Bearer ingest
  token → 202 and `app.state._receiver_buffer` length increases.
- Same POST with the admin PSK (wrong credential) → 401 from the router, proving
  the admin PSK does not authenticate ingest and the path bypassed the admin
  middleware (otherwise it'd be the middleware's 401, indistinguishable — so
  assert via `WWW`/body `{"error":"unauthorized"}` from `router.py:216` AND that
  a correct ingest token succeeds).
- With NO ingest token configured → 503 `ingest_disabled` (`router.py:210-213`),
  proving fail-closed.
- Assert `/ingest/webhook` is reachable (not 404) without `GLUDD_PSK` set,
  proving it bypassed the admin gate.

---

## 4. Issue-source ingest poller

**Module entry points:** `issue_sources.ingest.ingest_records(records, source, seen_keys, ...)`
(`ingest.py:137-167`) → new todo dicts; `record_to_todo` (`ingest.py:75-107`);
`lifecycle_write_back(source, external_id, status)` (`ingest.py:179-194`). The
engine `issue_sources.base.IssueSyncEngine` (`base.py:247`) plus `IssueRegistry`
(`base.py:212`) provide the sync primitives. `ingest.py:26-43` specifies the
deferred wiring precisely.

NOTE a naming mismatch to reconcile in execution: `ingest.py` imports
`IssueRecord`, `IssueSource`, `Transition`, `map_external_status` from
`issue_sources.base` (`ingest.py:49-54`), but the `base.py` read here defines
`NormalizedIssue`/`SyncSource`/`IssueSyncEngine` (no `Transition`/`IssueRecord`
symbols visible in the section read). Execution must confirm these symbols exist
in `base.py` (they are referenced, so they likely live further down or in a
sibling) before wiring — flag as a pre-flight check, not a blocker for the plan.

### (a) _lifespan construction + state key
- Build an `IssueRegistry` (`base.py:212`) and register one `SyncSource` per
  configured issue source. Construction in `_lifespan` after the project manager
  is available (`daemon.py:514`), since todos are project-scoped.
- **State key:** `app.state._issue_registry`. Also keep a per-source `seen_keys`
  set on `app.state._issue_seen_keys: dict[str, set[str]]` (the in-memory dedup
  set, since the persistent `external_id` column is explicitly deferred,
  `ingest.py:29-35`).
- Pass both into the `EventLoop(...)` constructor (`daemon.py:598-624`) as
  `issue_registry=` and `issue_seen_keys=` (new params, default None).

### (b) router register call
Optional admin surface (not required for the poller): a small
`/admin/issues/sync` route could trigger a manual sync. Keep PSK-gated, NOT in
`_PUBLIC_PATHS`. Not required for the core wiring; defer.

### (c) event_loop phase (the real integration, per `ingest.py:37-42`)
Add phase `poll_issue_sources` to `PHASE_ORDER` (`loop.py:38-50`). Position:
after `load_config_snapshot`, before `claim_unreviewed_task_returns` (index 1,
alongside §3's drain — both are intake phases; place issue-poll first, then
drain, then claims). `_phase_poll_issue_sources`:
- gate on a config interval (e.g. every N ticks, like `_phase_self_improve`
  uses `self._self_improve_interval`, `loop.py:1208-1213`) so it does not hit
  external APIs every second;
- for each registered source: `records = source.fetch_issues(spec)`
  (`base.py:165`), `new_todos, seen = ingest_records(records, source.name,
  self._issue_seen_keys.get(source.name), project_id=<tick project>)`
  (`ingest.py:137`), persist each via `self._todo_repo.create(payload)` (same
  call `_persist_self_improve_todos` uses, `loop.py:1242-1268`), update the seen
  set;
- on local ACTIVE/COMPLETE transitions in `_phase_reconcile_completed_decisions`
  (`loop.py:1042`), call `lifecycle_write_back(source, external_id, status)`
  (`ingest.py:179`) — this is the outward mirror. Hook it where the reconcile
  loop sets COMPLETE (`loop.py:1096-1101`), guarded on the todo carrying an
  `external_id` tag (`ingest.py:103`).
- best-effort, never abort the tick (mirror `_phase_self_improve`'s
  try/except, `loop.py:1234-1236`).

### (d) UserConfig block
Add `issue_sources: list[dict[str, Any]] = []` to `UserConfig`
(`user_config.py:88-97`): each entry `{name, system, *_env, spec, project_id,
poll_interval_ticks}`. Load from a `issue_sources.yml` in `load_startup_config`
(mirror `connectors`, §1).

### (e) integration test
`tests/integration/test_issue_poller_wired.py`: inject a fake `SyncSource` whose
`fetch_issues` returns one `NormalizedIssue`; run one `EventLoop.tick()`; assert
a todo with `external_id == "<source>:<id>"` (`ingest.py:103`,
`dedup_key`) was created via the repo; run a SECOND tick and assert NO duplicate
(dedup via `seen_keys`, `ingest.py:160-163`).

---

## 5. make_pid_provider → close the pipeline PID gap

**Module entry point:** `pipeline.daemon_adapters.make_pid_provider(queues, *, scrape=, controller=)`
(`daemon_adapters.py:191-229`) → a `PidProvider` the `DispatchLane` consumes for
keep-N-busy concurrency. The pipeline IS wired (`daemon.py:430-474, 743-752`) but
`_build_pipeline_controller` constructs `PipelineController` WITHOUT a
`pid_provider`/`pid_group` (`daemon.py:468-474`), so the lane falls back to the
static `config.target` instead of the live load controller.

### (a) daemon.py construction (no new state key)
In `_build_pipeline_controller` (`daemon.py:430-474`), build the provider from
the configured queues (same `uc.queues` the event loop feeds the PID controller,
`daemon.py:616`, `loop.py:590-602`) and pass it to `PipelineController`:
```python
from general_ludd.pipeline.daemon_adapters import make_pid_provider
queues = getattr(uc, "queues", []) if uc else []   # uc must be threaded into the builder
pid_provider = make_pid_provider(queues) if queues else None
return PipelineController(
    cfg, make_dispatch_fn(dispatcher), make_merge_fn(repo_path),
    _gate_green, disk_ok=make_disk_ok(repo_path),
    pid_provider=pid_provider, pid_group=str(getattr(pipeline_cfg, "pid_group", "") or "") or None,
)
```
**CONFLICT to resolve:** `_build_pipeline_controller(pipeline_cfg, dispatcher)`
(`daemon.py:430`) does NOT currently receive `uc`. Thread `uc` (or the queues
list) through the call site (`daemon.py:745-747`) — a one-line signature change,
grouped into the daemon.py edit batch.

### (b) router register call
None (pipeline status already exposed via `PipelineController.status()`,
`controller.py:183`).

### (c) event_loop phase
None — the pipeline runs its own lanes (`controller.py:114-132`); the PID
provider is internal to the DispatchLane.

### (d) UserConfig block
Add `pid_group: str | None = None` to `PipelineConfigBlock`
(`user_config.py:18-34`) so an operator can scope the lane to a named queue's
`desired_active_buckets_by_queue` (`daemon_adapters.py:205-208`).

### (e) integration test
`tests/unit/test_pipeline_pid_wired.py`: with `pipeline.enabled=true` and a
non-empty `queues` config, build the controller via `_build_pipeline_controller`
and assert `controller._dispatch_lane.desired_target()` reflects the PID
provider's output (not the frozen `config.target`) for a scripted
`LoadSnapshot`. Inject a fake `scrape`/`controller` into `make_pid_provider`
(it is injectable, `daemon_adapters.py:195-196`) to make the snapshot
deterministic.

---

## 6. DynamicDispatcher in-loop turn handler + VariableStore

The HTTP `/api/dispatch` path is wired (`daemon.py:1198-1205`), but the
`dynamic_dispatcher.py:8-11` TODO — "call DynamicDispatcher from the event-loop
turn handler when a model returns tool_calls, then re-render the next prompt from
the VariableStore" — is NOT done. `variable_store.py` (`VariableStore`,
`apply_results`) is entirely unreachable from the loop.

### (a) construction + state
No daemon.py construction needed; this lives inside the EventLoop. In
`EventLoop.__init__` (`loop.py:131-205`), accept an optional
`dispatcher: DynamicDispatcher | None = None` and instantiate a per-job
`VariableStore` (`variable_store.py:20`) inside the turn handler.

### (b) router register call
None.

### (c) event_loop integration (the real work)
In `_dispatch_execute_job` (`loop.py:826-974`), after a model executor returns
output that may contain tool calls:
- `calls = parse_tool_calls(output)` (`dynamic_dispatcher.py:69`),
- `results = dispatcher.dispatch_all(calls)` (`dynamic_dispatcher.py:231`),
- `apply_results(store, results)` (`variable_store.py:90`),
- re-render the next prompt turn with `store.render(template, **ctx)`
  (`variable_store.py:70`) and loop until no tool calls remain (bounded by a max
  turn count to avoid infinite loops).
This is a larger change than the others (it touches the dispatch inner loop) and
depends on the model executor returning structured tool-call output, which the
current gateway executor (`daemon.py:710-720`) returns as a raw content string —
so `parse_tool_calls` will return `[]` for plain text (safe no-op,
`dynamic_dispatcher.py:84-89`). **Sequence this LAST** (§7 ordering) and treat
the turn-loop as opt-in behind a config flag `dispatch.tool_loop_enabled`
(default off) so existing single-shot dispatch is byte-for-byte unchanged.

### (d) UserConfig block
Add `dispatch: dict[str, Any] = {}` to `UserConfig` (`user_config.py:88-97`)
with `tool_loop_enabled` / `max_turns`.

### (e) integration test
`tests/unit/test_dispatch_tool_loop.py`: with a fake executor that returns one
`{"tool_calls":[{"kind":"skill","name":"x","args":{}}]}` then a plain string,
and a `DynamicDispatcher` with a fake skill handler, assert the skill handler was
invoked and the second prompt render reflected `dispatch__x__output`
(`variable_store.py:103`).

---

## 7. self_update apply + priority router

**Module entry points:** `self_update.apply.apply_plan(plan, request, *, role=, validate=, audit_sink=, auto_apply_config=)`
(`apply.py:150`); `self_update.priority.compute_priority/to_todo_spec/to_work_item`
(`priority.py:49,63,108`). There is NO `routers/self_update.py` today — it must
be created. The classifier that turns NL → `SelfUpdatePlan` is referenced by
`model.py` but not read here; execution must locate it (likely
`self_update/classify.py` or similar) — flag as a pre-flight check.

### (a) _lifespan construction + state key
- A real `validate` callable (`apply.py:146`) should run the daemon's lint/type
  gate; the conservative first cut reuses the preflight pattern
  (`daemon.py:761-772`) or passes `validate=None` (which fail-closes any
  code-tier change, `apply.py:292-301` — safe default).
- An `audit_sink` (`apply.py:147`) should write through the
  `AuditEventRepository` (`event_loop/loop.py:535-549` shows the create call).
  Build a closure over `session_factory` in `_lifespan` and store on
  **state key** `app.state._self_update_audit_sink`.

### (b) router register call (new file `routers/self_update.py`)
Create `routers/self_update.py` with `register(app, _daemon_state)` mirroring
`routers/self_improve.py` (`self_improve.py:15`) and `routers/spend.py`:
- `POST /admin/self-update/plan` body `{raw_text, requested_by, approval_token?}`
  → build `SelfUpdateRequest` (`model.py:82`), classify → `SelfUpdatePlan`, call
  `apply_plan(...)` with the stored audit sink, return the `ApplyResult.outcome`
  + audit dict (`apply.py:93-105`).
- `POST /admin/self-update/enqueue` → `to_todo_spec(plan, request)`
  (`priority.py:63`) then `TodoRepository.create` (same idiom as
  `self_improve.py:38-45`), so the request enters the normal backlog.
Add to the router block in `create_daemon_app` (`daemon.py:1146-1208`):
```python
from general_ludd.routers import self_update as self_update_router
self_update_router.register(app, _daemon_state)
```
And to `routers/__init__.register_all` (`routers/__init__.py:11-46`) for parity.
PSK posture: `/admin/self-update/*` is admin-only — NOT in `_PUBLIC_PATHS`
(`/admin` is never public). No `_is_public` change.

### (c) event_loop phase
`priority.describe_scheduler_hook()` (`priority.py:133-151`) documents that
`to_work_item` items feed `Scheduler.plan()`. Since `_dispatch_jobs_via_scheduler`
already builds `WorkItem`s per todo (`loop.py:718-731`), self-update todos
created via §7(b) flow through the existing scheduler automatically once they are
backlog todos — BUT their resource labels (`SELF_UPDATE_CODE_RESOURCE` etc.,
`priority.py:26-29`) are only applied if the loop uses `to_work_item` for
`self_update`-queue todos. Add a branch in `_dispatch_jobs_via_scheduler`
(`loop.py:718-731`): when a todo's `queue == "self_update"`, build its WorkItem
via `priority.to_work_item(plan, todo_id)` (reconstructing tier from the todo's
`tier:` tag, `priority.py:88-91`) so code-tier self-updates serialize on
`self_update:code`. No new phase; this is a refinement of the existing dispatch
phase.

### (d) UserConfig block
Add `self_update: dict[str, Any] = {}` to `UserConfig` (`user_config.py:88-97`)
with `auto_apply_config` (default True, `apply.py:157`) and an approval policy.

### (e) integration test
`tests/integration/test_self_update_router_wired.py`:
- `POST /admin/self-update/plan` with a config-tier request → `outcome ==
  "applied"` (auto-apply, `apply.py:272-280`) and an audit record was written.
- A request targeting a protected path (e.g. a `.claude/` file) → `outcome ==
  "refused"` (`apply.py:208-219`) — proves the guard is live.
- `POST /admin/self-update/enqueue` → a `self_update`-queue todo exists with the
  computed priority (`priority.py:49-60`).
- WITHOUT a Bearer PSK → 401 (admin-gated).

---

## 8. Ordered execution plan (minimize daemon.py churn)

All `daemon.py` edits are grouped so the file is touched in as few passes as
possible. The recommended order:

**Batch A — `config/user_config.py` (one file, all new config fields):**
add `connectors`, `receiver`, `issue_sources`, `dispatch`, `self_update` fields
to `UserConfig` (§1d,3d,4d,6d,7d) and `pid_group` to `PipelineConfigBlock` (§5d).
No daemon coupling → do first, in isolation.

**Batch B — `load_startup_config` (`daemon.py`, isolated function):**
load `connectors.yml` and `issue_sources.yml` into `cfg` (§1a, §4d). Touches
only `daemon.py:72-164`.

**Batch C — new router files (no daemon.py edit):**
- extend `routers/observe.py` with timeline/correlate (§2b),
- create `routers/self_update.py` (§7b).
Independent new-files work — parallelizable.

**Batch D — the single `create_daemon_app` daemon.py edit (group ALL router
registrations + middleware + cheap constructions):**
- `wire_observability(...)` (§1b) + `GluddObserve` construct (§2a),
- receiver buffer adopt + `receiver_router.register` (§3a,3b),
- `_is_public`/`_is_receiver` middleware change (§3-PSK),
- `self_update_router.register` (§7b),
- `routers/__init__.register_all` parity (§7b).
ONE edit pass over `create_daemon_app` (`daemon.py:880-1210`).

**Batch E — the single `_lifespan` daemon.py edit (group ALL lifespan
constructions + EventLoop constructor args):**
- receiver buffer construct in `_get_or_create_extended_subsystems` (§3a),
- `IssueRegistry` + seen-keys construct (§4a),
- self-update audit sink construct (§7a),
- thread `uc`/queues into `_build_pipeline_controller` + `make_pid_provider`
  (§5a),
- add `receiver_buffer=`, `issue_registry=`, `issue_seen_keys=`, `dispatcher=`
  to the `EventLoop(...)` call (`daemon.py:598-624`).
ONE edit pass over `_lifespan` (`daemon.py:477-807`) + `_build_pipeline_controller`.

**Batch F — `event_loop/loop.py` (new phases + EventLoop.__init__ params):**
- add `poll_issue_sources` + `drain_receiver_buffer` to `PHASE_ORDER` at index 1
  (§3c, §4c) and their `_phase_*` methods,
- `lifecycle_write_back` hook in `_phase_reconcile_completed_decisions` (§4c),
- `to_work_item` branch in `_dispatch_jobs_via_scheduler` (§7c),
- new `__init__` params: `receiver_buffer`, `issue_registry`, `issue_seen_keys`,
  `dispatcher` (§3c,4a,6a,7c).
ONE pass over `loop.py`.

**Batch G — DynamicDispatcher in-loop tool loop (§6, behind a flag, LAST):**
the deepest change; gated `dispatch.tool_loop_enabled` default-off so it is a
no-op until proven. Do after A–F are green.

### Cross-module conflicts / dependencies (flagged)
- **§5 needs `uc` in `_build_pipeline_controller`** (signature change) — Batch E.
- **§3 buffer ordering** (`register` at app-creation vs lifespan construct):
  resolved by having the lifespan ADOPT `_daemon_state["receiver_buffer"]` rather
  than overwrite (§3a). One shared instance is mandatory or drained data is lost.
- **§3-PSK middleware** must NOT use `_PUBLIC_PATHS` (those are read-only/method-
  gated); receiver POSTs need a distinct prefix bypass (§3-PSK). Getting this
  wrong either blocks ingest (admin 401) or opens an unauthenticated relay.
- **§4 `base.py` symbol check** (`Transition`/`IssueRecord`/`map_external_status`
  referenced by `ingest.py:49-54` but not seen in the `base.py` section read) —
  pre-flight verify before wiring.
- **§7 classifier location** (NL→`SelfUpdatePlan`) not read here — pre-flight
  locate before building `routers/self_update.py`.
- **No two modules write the same `app.state` key** — verified: `_connector_registry`,
  `_observe_facade`, `_receiver_buffer`, `_issue_registry`, `_issue_seen_keys`,
  `_self_update_audit_sink` are all new and distinct.
- **Stale TODOs to delete during execution** (they falsely imply unwired):
  `scheduler.py:15-19`, `spend_limiter.py:17-28` (both already wired, §0).
```text
```
