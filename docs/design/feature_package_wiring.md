# Feature Package Wiring Specification

> **Status:** Design document, uncommitted — 2026-06-16
> **Scope:** Integration/wiring specs for feature packages that exist in the live
> source tree under `src/general_ludd/` but are not yet mounted in the daemon,
> plus packages from the prior-attempt list that do not yet exist.
> **How to use:** Work top-to-bottom by priority tier. Each stanza specifies
> exactly which lines to add/change and which `_daemon_state` keys to register.
> None of these changes are applied here — this document is read-only.

---

## Inventory: Live vs Worktree-Only

The following packages from the original ~27-item list were checked against the
live source tree. Packages marked **LIVE** exist in `src/general_ludd/` and have
passing unit tests. Packages marked **NOT EXISTS** are absent from the live tree
entirely; they must be built before they can be wired.

| Package | Live path | Status |
|---|---|---|
| `connectors/registry` | `src/general_ludd/connectors/registry.py` | **LIVE** |
| `observe` | `src/general_ludd/observe/facade.py` | **LIVE** |
| `receiver` | `src/general_ludd/receiver/router.py`, `buffer.py` | **LIVE** |
| `issue_sources` | `src/general_ludd/issue_sources/` (15 adapters + engine) | **LIVE** |
| `self_update` | `src/general_ludd/self_update/` (router + applier + classifier) | **LIVE** |
| `pipeline` | `src/general_ludd/pipeline/controller.py` | **LIVE** (already wired) |
| `scoring` (AdaptiveRouter) | `src/general_ludd/scoring/router.py` | **LIVE** (already wired) |
| `self_improve` (SelfImprovementHarness) | `src/general_ludd/self_improve/harness.py` | **LIVE** (already wired via router) |
| `event_bus` (EventBus) | `src/general_ludd/events/bus.py` | **LIVE** (already wired) |
| `planner` (OrchestrationPlanner) | `src/general_ludd/scheduling/planner.py` | **LIVE** (partial — tool only, no daemon seam) |
| `context` (ContextCompactor) | `src/general_ludd/agents/context.py` | **LIVE** (partial — used in agents, no daemon state key) |
| `memory` (MemoryRecordModel) | `src/general_ludd/db/models.py:529` only; no service layer | **PARTIAL** (DB model exists, no repository or router) |
| `eval` | — | **NOT EXISTS** |
| `retrieval` | — | **NOT EXISTS** |
| `sandbox` | — | **NOT EXISTS** |
| `self_improve/outcome_loop` | — | **NOT EXISTS** |
| `prompt_versioning` | — | **NOT EXISTS** |
| `hitl` | — | **NOT EXISTS** |
| `scoring/pareto` | — | **NOT EXISTS** |
| `replay` | — | **NOT EXISTS** |
| `tool_registry` | `src/general_ludd/mcp/registry.py` (MCPToolRegistry) — distinct concept | **PARTIAL** (MCP tools only; generic tool registry not built) |
| `consensus` | — | **NOT EXISTS** |
| `rate_limit` | `src/general_ludd/receiver/router.py:139` (_RateLimiter internal) | **PARTIAL** (in-receiver only, not a shared service) |
| `cost_report` | `src/general_ludd/metrics/collector.py` (MetricsCollector) — overlapping | **PARTIAL** (metrics exist; named cost_report package not built) |
| `run_timeline` | — | **NOT EXISTS** |
| `output_schema` | — | **NOT EXISTS** |
| `resilience` | `src/general_ludd/models/timeout_detector.py` + `models/gateway.py` — partial coverage | **PARTIAL** (gateway retry/health exists; no named resilience package) |
| `preview` | — | **NOT EXISTS** |
| `patch_apply` | `src/general_ludd/integration/safe_merge.py` — partial | **PARTIAL** (3-way merge exists; no named patch_apply package) |
| `provenance` | — | **NOT EXISTS** |
| `config_schema` | `src/general_ludd/config/user_config.py` (UserConfig, partial pydantic-settings) | **PARTIAL** (config model exists; guide-3 W4.4 upgrades it) |
| `repro` | — | **NOT EXISTS** |
| `redaction` | — | **NOT EXISTS** |
| `audit_log` | `src/general_ludd/events/bus.py` (history) + `src/general_ludd/observability/` — partial | **PARTIAL** (event history exists; no structured audit_log table) |
| `health` | `src/general_ludd/models/timeout_detector.py` (ModelHealthTracker) + `/healthz` + `/readyz` | **LIVE** (already wired) |

---

## Priority Tiers

**Tier 1 — High user value, package fully built, wiring is a small delta:**
receiver, observe/connectors/registry, issue_sources, self_update

**Tier 2 — Medium value, package built, wiring needs design:**
planner, context, memory (repository layer first), OrchestrationPlanner seam

**Tier 3 — Value is real but package needs partial build before wiring:**
rate_limit (generalize from receiver), resilience (generalize gateway health),
patch_apply (expose safe_merge), cost_report (report endpoint over MetricsCollector),
audit_log (structured event table)

**Tier 4 — Package does not exist; build first, then wire:**
eval, retrieval, sandbox, outcome_loop, prompt_versioning, hitl, pareto,
replay, consensus, run_timeline, output_schema, provenance, repro, redaction

---

## Summary Table (ordered by value)

| # | Package | Priority | Effort | Caller | Wiring point |
|---|---|---|---|---|---|
| 1 | `receiver` | P1 | S | Daemon lifespan + create_daemon_app | `_lifespan` + `create_daemon_app` |
| 2 | `connectors/registry` + `observe` | P1 | S | `create_daemon_app` | one `wire_observability(...)` call |
| 3 | `issue_sources` | P1 | M | Event-loop tick (`_phase_claim`) | `EventLoop.__init__` + lifespan |
| 4 | `self_update` | P1 | S | CLI (`scripts/gludd_update.py`) + daemon endpoint | `create_daemon_app` + `cli.py` |
| 5 | `planner` (OrchestrationPlanner) | P2 | S | Event-loop scheduler or orchestrator | `EventLoop.__init__` |
| 6 | `context` (ContextCompactor) | P2 | S | `execution/engine.py` | `ExecutionEngine.__init__` |
| 7 | `memory` (MemoryRepository) | P2 | M | Event-loop phases + agent roles | lifespan + `EventLoop.__init__` |
| 8 | `cost_report` | P3 | S | `/admin/accounting/report` or `/api/facts` | `routers/accounting.py` |
| 9 | `rate_limit` (general) | P3 | S | Worker endpoints | `worker/app.py` |
| 10 | `resilience` (generalized) | P3 | M | ModelGateway | `models/gateway.py` |
| 11 | `patch_apply` | P3 | M | `gludd_git` Ansible module / review phase | `review/decision_applier.py` |
| 12 | `audit_log` | P3 | M | All write endpoints | Middleware in `create_daemon_app` |
| 13 | `eval` | P4 | L | Worker execute phase | build first |
| 14 | `retrieval` | P4 | L | ExecutionEngine | build first |
| 15 | `sandbox` | P4 | L | Worker execute | build first |
| 16 | `outcome_loop` | P4 | L | self_improve phase | build first |
| 17 | `hitl` | P4 | L | Review phase | build first |
| 18 | `prompt_versioning` | P4 | M | PromptRegistry | build first |
| 19 | `pareto` | P4 | M | AdaptiveRouter | build first |
| 20 | `replay` | P4 | L | Worker execute | build first |
| 21 | `consensus` | P4 | L | Review phase | build first |
| 22 | `run_timeline` | P4 | M | `/api/facts` | build first |
| 23 | `output_schema` | P4 | M | ExecutionEngine | build first |
| 24 | `provenance` | P4 | M | All git commit operations | build first |
| 25 | `repro` | P4 | L | Worker execute | build first |
| 26 | `redaction` | P4 | M | Log/metrics pipeline | build first |
| 27 | `config_schema` | P4 | S | Loader; guide-3 W4.4 owns this | guide-3 W4.4 |

---

## Per-Package Wiring Stanzas (P1–P3 only)

Packages marked P4 (NOT EXISTS) are omitted — they require a build pass before
a wiring spec is meaningful. When each is built, append its stanza here.

---

### 1. `receiver` — OTLP / webhook / gelf / fluent / beats ingest

**Package:** `src/general_ludd/receiver/` (router.py + buffer.py + parsers.py)

**Public API:**
- `ReceiverBuffer(maxlen, overflow, retention_s)` — bounded ring
- `receiver_router.register(app, daemon_state)` — mounts POST endpoints:
  `/v1/logs`, `/v1/metrics`, `/v1/traces`, `/ingest/webhook`, `/ingest/gelf`,
  `/ingest/fluent`, `/ingest/beats`
- `buffer.drain()` — returns list of normalized records and clears them
- Auth: `GLUDD_INGEST_TOKEN` env var; **distinct from** `GLUDD_AUTH_PSK`

**Entry points:** `register(app, daemon_state)` reads `daemon_state["receiver_buffer"]`

**Caller:** `create_daemon_app` (alongside `todos.register`, etc.). The event loop
or a connector bridge drains the buffer each tick via `buffer.drain()`.

**Minimal wiring — two sites:**

**(a) In `_get_or_create_extended_subsystems` (near `_recent_traces`, line ~831):**
```python
if getattr(app.state, "_receiver_buffer", None) is None:
    from general_ludd.receiver.buffer import OverflowPolicy, ReceiverBuffer
    app.state._receiver_buffer = ReceiverBuffer(
        maxlen=10_000, overflow=OverflowPolicy.REJECT, retention_s=3600,
    )
```
Return the key from `_get_or_create_extended_subsystems`:
```python
"receiver_buffer": app.state._receiver_buffer,
```

**(b) In `_lifespan`, after `ext = _get_or_create_extended_subsystems(...)`:
```python
_daemon_state["receiver_buffer"] = ext.get("receiver_buffer")
```

**(c) In `create_daemon_app`, after existing `*.register` calls (line ~1167):
```python
from general_ludd.receiver import router as receiver_router
receiver_router.register(app, _daemon_state)
```

**(d) Auth path — extend `_PUBLIC_PATHS` / `_is_public`:**
The ingest endpoints use their own token, not the admin PSK. Add them to a
separate allowlist so the admin-PSK middleware does NOT block POST to
`/v1/*` and `/ingest/*`:
```python
_INGEST_PATHS_PREFIX = ("/v1/", "/ingest/")

def _is_public(method: str, path: str) -> bool:
    if any(path.startswith(p) for p in _INGEST_PATHS_PREFIX):
        return True  # receiver does its own auth
    ...
```

**`_daemon_state` key:** `"receiver_buffer"` → `ReceiverBuffer` instance

**Capability/config:** `GLUDD_INGEST_TOKEN` env var (name only). No config file
needed. `ReceiverBuffer` parameters can be tuned via `config/user_config.py`
(add `receiver.maxlen` / `receiver.overflow` once W4.4 lands).

**Priority:** P1. Enables observable push-side telemetry without polling. Zero
impact on existing endpoints — purely additive.

---

### 2. `connectors/registry` + `observe` — cross-source observability

**Package:**
- `src/general_ludd/connectors/registry.py` — `ConnectorRegistry`
- `src/general_ludd/observe/facade.py` — `GluddObserve`
- `src/general_ludd/routers/observe.py` — `register()`, `wire_observability()`

**Public API:**
- `ConnectorRegistry.from_config(config_list, factories)` — builds ~50 connectors
- `ConnectorRegistry.query(name, spec)` → list of normalized records
- `ConnectorRegistry.health_all()` → dict of health dicts
- `wire_observability(app, daemon_state, config)` — builds registry + registers routes
- Routes registered: `GET /api/observe/sources`, `GET /api/observe/health`,
  `POST /api/observe/query`
- `GluddObserve(provider)` — higher-level: `query_sources`, `correlate_incident`,
  `timeline`, `topology` — wraps the registry for fan-out queries

**Entry points:** `wire_observability(app, _daemon_state, _connector_config)` is
the single hookup point documented in `routers/observe.py:130-170`.

**Caller:** `create_daemon_app`, after existing `*.register` calls. The connector
config comes from `startup_config.get("connectors", [])` (an operator-supplied
list of connector entries loaded from `config/connectors.yml`).

**Minimal wiring — one site in `create_daemon_app` (line ~1167, after spend.register):**
```python
from general_ludd.routers.observe import wire_observability
_connector_config = (
    app.state._startup_config or {}
).get("connectors") or []
wire_observability(app, _daemon_state, _connector_config)
```

Also register `GluddObserve` on `app.state` so the event loop can use it for
cross-source debugging when a connector registry is present:
```python
reg = getattr(app.state, "_connector_registry", None)
if reg is not None:
    from general_ludd.observe.facade import GluddObserve
    app.state._observe = GluddObserve(reg)
```

**`_daemon_state` key:** `"connector_registry"` → `ConnectorRegistry` instance
(set inside `wire_observability`; also stored on `app.state._connector_registry`).

**Config needs:**
- New `connectors:` key in `config/user_config.py` (a list of dicts, each with
  `name`, `module` or `class`, and `*_env` secret NAME fields). Default: `[]`
  (no connectors → router registers with empty registry; all endpoints return
  empty results, never errors).
- No secrets stored in config: only env-var names.

**Priority:** P1. Unlocks the 50+ already-built connectors. The router is SSRF-safe,
PSK-gated, and degrades to empty results when not configured.

---

### 3. `issue_sources` — external tracker sync

**Package:** `src/general_ludd/issue_sources/` (base.py + ingest.py + 15 adapters)

**Public API:**
- `IssueSource` protocol: `.name`, `.health()`, `.fetch_issues()`, `.update_status()`, `.add_comment()`
- `IssueSyncEngine(registry, store)`: `.sync_in()`, `.sync_out()` — fault-tolerant
  bidirectional sync; returns `SyncReport`
- `IssueRegistry`: register/lookup `IssueSource` instances
- `ingest_records(records, source_name, seen_keys)` → (todos, seen_keys) — dedup
- `record_to_todo(record)` → dict shaped for `TodoRepository.add_todo()`
- Adapters: GitHub, GitLab, Jira, Linear, Trello, Asana, ServiceNow, Bitbucket,
  Azure Boards, ClickUp, Monday, Redmine, CSV/Excel, Markdown

**Entry points:** `IssueSyncEngine.sync_in()` / `sync_out()` — called from the
event loop claim phase or on a schedule.

**Caller:** The event loop `_phase_claim` (or a new `_phase_issue_sync` tick phase),
which polls external sources and injects new todos.

**Minimal wiring:**

**(a) Lifespan construction — add to `_lifespan` after `session_factory` is built:**
```python
# W: issue-source wiring — build registry from config and attach sync engine
_issue_sources_config = startup_config.get("issue_sources", [])
if _issue_sources_config:
    from general_ludd.issue_sources.base import IssueRegistry, IssueSyncEngine
    _issue_registry = IssueRegistry()
    # Each entry: {"type": "jira", "name": "prod-jira", ...config...}
    for src_cfg in _issue_sources_config:
        src_type = src_cfg.get("type", "")
        # Factory map (extend as adapters are added):
        _ADAPTER_MAP = {
            "jira": "general_ludd.issue_sources.jira:JiraIssueSource",
            "github": "general_ludd.issue_sources.github_issues:GitHubIssueSource",
            # ... etc
        }
        if src_type in _ADAPTER_MAP:
            mod_path, cls_name = _ADAPTER_MAP[src_type].split(":")
            import importlib
            cls = getattr(importlib.import_module(mod_path), cls_name)
            _issue_registry.register(cls(src_cfg))
    _daemon_state["issue_registry"] = _issue_registry
    app.state._issue_registry = _issue_registry
```

**(b) Event-loop injection — add `issue_registry` param to `EventLoop.__init__`
(in `event_loop/loop.py`) and call `IssueSyncEngine(registry, todo_store).sync_in()`
at the start of `_phase_claim` each tick.**

The `TodoStore` protocol is satisfied by a thin adapter over `TodoRepository`
(which the event loop already holds via `session_factory`).

**`_daemon_state` key:** `"issue_registry"` → `IssueRegistry` instance

**Config needs:**
- New `issue_sources:` key in `config/user_config.py` (list of source configs).
  Default: `[]`. Creds via `*_env` env-var names (same pattern as connectors).
- No sync poll interval config needed initially; sync fires once per event-loop tick.

**Priority:** P1. This is how real backlogs (Jira, GitHub, Linear) become gludd
todos without manual `gludd dispatch`. It is the spine's "input funnel."

---

### 4. `self_update` — operator-driven config/code self-modification

**Package:** `src/general_ludd/self_update/` (router.py + applier.py + classifier.py
+ priority.py + apply.py + model.py)

**Public API (from `__init__.py`):**
- `UpdateRequestRouter(subsystem_map, path_exists)` — `route(text)` → `UpdatePlan`
- `UpdatePlan` — `{subsystem, targets, capability, priority, risk, rationale}`
- `UpdateRequest` — free-text request wrapper
- `UpdateTarget` — `{kind: "config"|"yaml"|"role"|"code", path, line_hint}`
- Also: `SelfUpdateApplier.apply(plan)` — filesystem writer (guarded by capability)
- CLI entry: `scripts/gludd_update.py` (`run(request_text, ...)` → `{plan, submitted_todo}`)

**Entry points:**
1. **CLI**: `scripts/gludd_update.py main(["update gludd: <text>"])` — routes the
   request, derives priority, POSTs a todo to the daemon via HTTP.
2. **Daemon endpoint** (not yet built): `POST /admin/self-update/request` — accepts
   `{"text": "..."}`, runs the router, creates a todo with `work_type="self_update"`.

**Caller:**
1. CLI: already exists (`scripts/gludd_update.py`, tested in
   `tests/unit/test_gludd_update_cli.py`). The CLI currently calls `load_router()`
   which returns `None` unless `UpdateRequestRouter` is importable and the module
   is loaded — the import IS available but `load_router` just needs the right import path.
2. Daemon: a new `POST /admin/self-update/request` endpoint in a new
   `routers/self_update.py`.

**Minimal wiring — two independent changes:**

**(a) Fix `scripts/gludd_update.py` `load_router` to actually import:**
Currently `load_router()` swallows `ImportError` and returns `None`. Change it to:
```python
def load_router() -> UpdateRequestRouter | None:
    try:
        from general_ludd.self_update import UpdateRequestRouter
        return UpdateRequestRouter()
    except Exception:
        return None
```

**(b) Add `routers/self_update.py`:**
```python
def register(app: FastAPI, daemon_state: dict) -> None:
    @app.post("/admin/self-update/request")
    async def self_update_request(req: UpdateRequestBody) -> dict:
        from general_ludd.self_update import UpdateRequestRouter
        router = UpdateRequestRouter()
        plan = router.route(req.text)
        # POST todo to daemon (or call TodoRepository directly)
        ...
```
Then register it in `create_daemon_app`:
```python
from general_ludd.routers import self_update as self_update_router
self_update_router.register(app, _daemon_state)
```

**`_daemon_state` key:** none required at lifespan (stateless router). The endpoint
writes through `TodoRepository` via `session_factory`.

**Capability/config:** Three capability strings from `router.py`:
`CAP_CONFIG_WRITE`, `CAP_COLLECTIONS_SELF_MODIFY`, `CAP_CODE_SELF_MODIFY`.
Risk gate: `plan.risk == "high"` must block auto-apply and create a HITL todo
(tie to `hitl` package when it exists; for now, mark with `requires_review=True`).

**Priority:** P1. Enables the "update gludd: ..." operator workflow. The router
and applier are built and tested; only the daemon endpoint and the `load_router`
import fix are missing.

---

### 5. `planner` — deterministic parallelism planning

**Package:** `src/general_ludd/scheduling/planner.py`

**Public API:**
- `OrchestrationPlanner()` — `plan_work(items)` → `PlanResult`
- `PlanResult.parallel_now` — item ids safe to run concurrently right now
- `PlanResult.batches` — full ordered batch list
- `PlanResult.serialized` — explains which pairs are serialized and why
- Depends on: `Scheduler` + `WorkItem` from `scheduling/scheduler.py`

**Entry points:** `planner.plan_work(items)` — pure, deterministic, no I/O.

**Caller:** The orchestrator (`scripts/plan_work.py` already wraps it for CLI use
via `make plan`). For daemon wiring: the `EventLoop` tick should call the planner
before dispatching — specifically in `_phase_claim` or just before forking tasks to
the `AgentDispatcher`.

**Minimal wiring — one site in `EventLoop.__init__` (event_loop/loop.py):**
```python
from general_ludd.scheduling.planner import OrchestrationPlanner
self._planner = OrchestrationPlanner()
```
Then in `_phase_claim` or `_phase_dispatch`, before dispatching pending items:
```python
items = [{"id": t.id, "files": t.files or [], "depends_on": t.depends_on or [],
          "is_greenfield": t.is_greenfield} for t in pending_todos]
plan = self._planner.plan_work(items)
to_dispatch = plan.parallel_now  # dispatch only the safe-now batch
```

Note: The module-level `TODO(integration)` comment in `planner.py` requests that
live `FileClaimRegistry` claims be sourced instead of caller-declared `files`.
That is a follow-up; the initial wiring just uses the todo's declared files.

**`_daemon_state` key:** none (stateless). The planner is injected into `EventLoop`.

**Config:** None. The planner is deterministic from the todo list.

**Priority:** P2. Replaces ad-hoc "is this blocked?" judgment in the orchestrator
with deterministic batching. No new build required.

---

### 6. `context` — conversation context compaction

**Package:** `src/general_ludd/agents/context.py`

**Public API:**
- `ContextCompactor(max_tokens, compaction_threshold, preserve_recent_count)`
- `compact(messages, summary_fn)` → compacted `list[ContextMessage]`
- `needs_compaction(messages)` → bool
- `estimate_tokens(text)` → int (length/4 heuristic)

**Entry points:** `compact(messages)` — pure, synchronous.

**Caller:** `execution/engine.py` — the `ExecutionEngine` builds a conversation
history from the job context and already sends it to the gateway. The compactor
should fire before the messages are passed to `call_model`.

**Minimal wiring — `ExecutionEngine.__init__` (execution/engine.py, line ~176):**
```python
from general_ludd.agents.context import ContextCompactor
self._context_compactor = ContextCompactor(
    max_tokens=int(os.environ.get("GLUDD_MAX_CONTEXT_TOKENS", "128000")),
)
```
Then in the message-building path, before sending to gateway:
```python
messages = self._context_compactor.compact(messages)
```

The `summary_fn` can be wired to the `ModelGateway` later for model-driven
summarization; the default (truncation) is safe to start.

**`_daemon_state` key:** none (instantiated inside `ExecutionEngine`).

**Config:** `GLUDD_MAX_CONTEXT_TOKENS` env var (default 128000). Optionally add
`context.max_tokens` to `UserConfig` after W4.4.

**Priority:** P2. Prevents context window overflows on long-running tasks. The
class is already built and tested.

---

### 7. `memory` — persistent agent memory

**Package:** `src/general_ludd/db/models.py:529` (`MemoryRecordModel` — DB table only)

**Current state:** `MemoryRecordModel` table is defined in SQLAlchemy and is
created by `ensure_tables`/`create_all`. There is NO repository class, NO service
layer, and NO router endpoint yet. The table exists in the schema but is completely
unreachable from application code.

**Required build (before wiring):**
1. `db/repository.py` — add `MemoryRepository` with:
   - `add(scope, scope_key, kind, text)` → `MemoryRecordModel`
   - `search(scope, scope_key, query, limit)` → list of records (substring match)
   - `summarize(scope, scope_key)` → concatenated recent facts (for context injection)
   - `prune(scope, scope_key, max_records)` → removes oldest beyond cap
2. `routers/memory.py` — `register(app, daemon_state)` with:
   - `GET /api/memory` — list/search records (by scope/scope_key/kind/query params)
   - `POST /api/memory` — add a fact
   - `DELETE /api/memory/{id}` — remove a fact
3. Ansible module `gludd_memory` (W6 collection scope) — `op: store|recall|prune`

**Minimal wiring (after the build above):**

**(a) Lifespan — construct after `session_factory` is ready:**
```python
from general_ludd.db.repository import MemoryRepository
app.state._memory_repo = MemoryRepository(session_factory=session_factory)
_daemon_state["memory_repo"] = app.state._memory_repo
```

**(b) EventLoop injection:**
```python
self._memory_repo = memory_repo  # new param to EventLoop.__init__
```
Use in `_phase_self_improve` and `_phase_review` to store / recall context.

**(c) Register router in `create_daemon_app`:**
```python
from general_ludd.routers import memory as memory_router
memory_router.register(app, _daemon_state)
```

**`_daemon_state` key:** `"memory_repo"` → `MemoryRepository` instance

**Config:** `memory.max_records_per_scope` (default 500). No secrets.

**Priority:** P2. Without memory, every agent run starts cold. This is a prerequisite
for `context` compaction driven by real history and for the self-improve loop.

---

### 8. `cost_report` — cost visibility endpoint

**Current state:** `MetricsCollector` (already wired) tracks per-agent / per-model
costs in memory. There is no HTTP surface that exposes a human-readable cost
breakdown beyond `/api/facts` stats and `/admin/accounting/ledger`.

**Required build (small — no new package needed):**
In `routers/accounting.py`, add:
```python
@app.get("/admin/accounting/cost-report")
async def cost_report() -> dict:
    collector = getattr(app.state, "_metrics_collector", None)
    if collector is None:
        return {"error": "MetricsCollector not initialized"}
    return collector.get_full_report()
```

Alternatively, expose a `/admin/metrics/cost` endpoint that returns structured
`{by_model: ..., by_project: ..., total_usd: ..., window: ...}`.

**`_daemon_state` key:** `"metrics_collector"` (already present).

**Config:** None. Data comes from in-memory MetricsCollector.

**Priority:** P3. Low effort, high operator value — operators need cost visibility.
Pure additive endpoint, zero new dependencies.

---

### 9. `rate_limit` — generalized rate limiting for worker endpoints

**Current state:** `src/general_ludd/receiver/router.py:139` has a private
`_RateLimiter` (token bucket) used only inside the receiver. Worker endpoints
(`worker/app.py`) have no rate limiting.

**Required build (extract and expose):**
1. Extract `_RateLimiter` from `receiver/router.py` into a standalone
   `general_ludd.rate_limit` module (or `controllers/rate_limiter.py` following
   the `controllers/` pattern). Public API: `RateLimiter(rate_per_sec, burst)`
   with `.acquire() -> bool`.
2. Apply to `worker/app.py` middleware — the same PSK-based rate limit the daemon
   admin endpoints use (or a separate `GLUDD_WORKER_RATE` config).

**Minimal wiring (after build):**
```python
# worker/app.py middleware, after PSK check:
from general_ludd.rate_limit import RateLimiter
_limiter = RateLimiter(rate_per_sec=10.0, burst=20)
if not _limiter.acquire():
    return JSONResponse(status_code=429, content={"error": "rate_limited"})
```

**Config:** `GLUDD_WORKER_RATE` env var (default 10 req/s).

**Priority:** P3. Prerequisite for the worker auth work (W5.6 already identifies
the worker as unauthenticated). Rate limiting is the next defense layer.

---

### 10. `resilience` — gateway health and circuit-breaking

**Current state:** `ModelHealthTracker` (in `models/timeout_detector.py`) and the
`ModelGateway`'s retry logic are built and wired. The gap is: retry semantics are
hand-rolled (guide-3 V3.1 reverts the false tenacity tick); the circuit-breaker
(`is_healthy` / `admit_probe`) exists but is not used as a unified resilience
policy object.

**Required build:**
Implement guide-3 W4.1 — port retry onto tenacity, deleting the hand-rolled loop.
This IS the resilience package implementation. Once W4.1 lands, the `ModelGateway`
resilience surface is: `call_model_with_retry` wrapping a tenacity-decorated
single-profile call, with `ModelHealthTracker` as the circuit breaker.

**No new wiring needed** — tenacity is a drop-in replacement for the hand-rolled
loop in `gateway.py:256-327`. The wiring (gateway construction in lifespan) is
already correct.

**Priority:** P3. Guide-3 W4.1 owns this. Document here for traceability only.

---

### 11. `patch_apply` — structured diff / patch application

**Current state:** `src/general_ludd/integration/safe_merge.py` has a 3-way merge
engine (`SafeMerger`, `three_way_merge`). It is used by `make wt-apply` (CLI) but
has no daemon or Ansible module exposure.

**Required build:**
Add `general_ludd.patch_apply` as a thin wrapper over `SafeMerger` with:
- `PatchApplier(repo_path)` — `apply(patch_text, strategy: "3way"|"force")` → bool
- Returns structured `{applied: bool, conflicts: list, rejected_hunks: list}`

**Wiring:**
- In `review/decision_applier.py` — `apply_decision(decision, workspace)` (W3.2)
  calls `PatchApplier.apply(diff)` to materialize the model's output into the workspace.
- As `gludd_patch` Ansible module in the W6 collection.

**Priority:** P3. Needed for W3.2 (the real reviewer) and W6 (the agent-task role).

---

### 12. `audit_log` — structured event audit trail

**Current state:** `EventBus.history` (in `events/bus.py`) keeps the last N events
in memory. There is no persistent audit log table or structured endpoint.

**Required build:**
1. `db/models.py` — add `AuditLogModel` (`id`, `ts`, `event_type`, `actor`,
   `resource_type`, `resource_id`, `payload_json`).
2. `db/repository.py` — add `AuditLogRepository.append(event)` + `query(filters)`.
3. Subscribe to `EventBus` wildcard (`"*"`) in lifespan and write each event to
   `AuditLogRepository` asynchronously.
4. `routers/audit.py` — `GET /admin/audit/events?since=&type=&limit=`.

**Minimal wiring (after build):**
```python
# In _lifespan, after event_bus is constructed:
audit_repo = AuditLogRepository(session_factory=session_factory)
_daemon_state["audit_repo"] = audit_repo

def _audit_subscriber(event):
    asyncio.create_task(audit_repo.append(event))

subsys["bus"].subscribe("*", _audit_subscriber)
```

**Priority:** P3. Required for compliance and debugging. Builds on the already-wired
EventBus.

---

## P4 Packages: Build-First Notes

The following packages do not exist in the live tree. They are listed here so
future build passes can reference the expected wiring point immediately after build.
No wiring spec is possible without first reading the built API.

| Package | Expected wiring point | Key considerations |
|---|---|---|
| `eval` | Worker execute phase → `_phase_evaluate` in EventLoop | Needs benchmark repo + gateway |
| `retrieval` | `ExecutionEngine` before model call | Needs vector store / embedding backend |
| `sandbox` | Worker execute phase (container runtime) | Needs container runtime check |
| `outcome_loop` | self_improve `_phase_self_improve` | Must persist todos via W3.7 path |
| `prompt_versioning` | `PromptRegistry.refresh()` | Versions stored in DB |
| `hitl` | Review phase after `apply_decision` | Needs GLUDD_HITL_WEBHOOK or blocking todo |
| `pareto` | `AdaptiveRouter.route()` | Multi-objective selection over benchmark scores |
| `replay` | Worker execute (replay recorded sessions) | Needs session recording first |
| `consensus` | Review phase (multi-model voting) | Needs multiple gateway profiles |
| `run_timeline` | `/api/facts` facet | Derives from RunHistoryRecorder |
| `output_schema` | ExecutionEngine post-processing | Validates model output against schema |
| `provenance` | git commit phase in agent_task role | Records commit → todo mapping |
| `repro` | Worker execute (reproduce a failure) | Needs recorded execution trace |
| `redaction` | Log middleware + metrics pipeline | Applies regex/pattern rules before storage |
| `config_schema` | Guide-3 W4.4 owns `config/loader.py` → `BaseSettings` | Wait for W4.4 |

---

## Wiring Anti-Patterns (do not do these)

1. **Adding packages to `_daemon_state` without a lifespan construct-site.** Every
   key in `_daemon_state` must be constructed in `_lifespan` or
   `_get_or_create_extended_subsystems`. Keys set to `None` at init are fine;
   keys absent from both sites cause `KeyError` in routers at request time.

2. **Registering a router before its `_daemon_state` key is set.** Routers read
   their keys lazily at request time (via `getattr(app.state, key, None)`) so late
   construction in `_lifespan` is fine. But if a router reads a key that was never
   set, it silently returns empty or raises `AttributeError`. Always set to `None`
   in `create_daemon_app` if not set in lifespan.

3. **Adding ingest / receive paths to `_PUBLIC_PATHS`.** The receiver uses its own
   token (`GLUDD_INGEST_TOKEN`) — not the admin PSK — for auth. Its paths must NOT
   be in `_PUBLIC_PATHS` (which bypasses all auth for SAFE methods); instead, the
   `_is_public` predicate must allow POST to `/v1/*` / `/ingest/*` through WITHOUT
   treating them as admin-PSK-exempt — the receiver itself rejects unauthenticated
   callers with 503.

4. **Bypassing `IssueSyncEngine` to write todos directly.** The engine deduplicates
   by `(source, external_id)`. Writing around it will create duplicate todos from
   repeated polls.

5. **Constructing a new `AdaptiveRouter` or `BenchmarkRepository` in a new router.**
   There is already one `AdaptiveRouter` built in `_get_or_create_extended_subsystems`
   and attached to `app.state._adaptive_router`. New code must read it from
   `app.state`, not construct a second instance (double-benchmark-repo = double DB
   connections).
