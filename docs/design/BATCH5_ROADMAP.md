# BATCH5_ROADMAP — single ordered execution roadmap

Status: roadmap / uncommitted. Built 2026-06-16 by consolidating every design
doc under `docs/design/` + the audit ledgers under `docs/audit/` into ONE
dependency-ordered checklist. This is the plan the next wave runs from: pure
execution, no re-design. Each item cites the source design doc that holds the
detail — **read that doc's named section before implementing**; do not re-derive.

## How to read this

- Items are grouped by THEME and ordered by DEPENDENCY across themes (Wave 1 → 4).
- Every item = **{what · design doc · files to touch · deps/order · size · capability/test}**.
- **Size:** S ≤ ~½ day, M ≈ 1–2 days, L ≈ 3–5 days, XL > 1 wk.
- **daemon.py is a single-writer churn hotspot.** Every item that edits
  `daemon.py` is tagged **[daemon.py]** and MUST be serialized — one agent owns
  the `create_daemon_app` pass and one owns the `_lifespan` pass; never two
  concurrent editors. These are flagged in §"Serialization constraints" and are
  the gating bottleneck of Wave 1.
- **Audit grounding:** `docs/audit/BURNDOWN.md` Section B lists the PARTIAL
  "built-but-unwired" items this roadmap closes; the daemon-integration wave is
  named there as "the single biggest lever." `docs/audit/security_tasks_status.md`
  supplies the two security PARTIALs (#49 spend projection, #59/#69 avg_cost) folded
  into Wave 1.

---

## WAVE 1 — DAEMON-INTEGRATION (the #1 lever)

Source of record: `docs/design/daemon_integration_plan.md` (the executable
blueprint; its §0 lists what is ALREADY wired — do NOT re-wire SpendLimiter
construction, spend router, Scheduler.plan, PID controller, the pipeline
controller's existence, or the HTTP `/api/dispatch` path). The connector-wiring
detail is in `docs/design/connector_wiring_plan.md`; the cross-source observe
detail in `docs/design/observe_debugging_roles.md`; the receiver in
`docs/design/observability_receiver.md`; the issue poller in
`docs/design/issue_sources.md`; the pipeline PID gap in
`docs/design/pipeline_controller.md`.

Per `BURNDOWN.md` Section B, this wave flips ~38 connector modules + observe +
receiver + issue_sources + self_update + the spend/pipeline residuals from
PARTIAL→DONE. **Do it once, prove each module e2e, the PARTIAL column collapses.**

### Batch 1A — `config/user_config.py` (one file, all new config fields) — NO daemon coupling, do FIRST, parallelizable-from-everything-else

- **1A.1** Add `connectors: list[dict[str, Any]] = []` to `UserConfig`.
  - doc: `daemon_integration_plan.md §1d` / `connector_wiring_plan.md §4.1`.
  - files: `src/general_ludd/config/user_config.py`.
  - deps: none. size: S.
  - test: `tests/unit/test_user_config*.py` — YAML default + `GLUDD_CONNECTORS` env override round-trip; malformed entry must NOT abort `UserConfig` construction (keep `list[dict]`, loader owns validation).
- **1A.2** Add `receiver: dict[str, Any] = {}` (or a `ReceiverConfig`/`DataSourceConfigBlock`-style sub-model) to `UserConfig`.
  - doc: `daemon_integration_plan.md §3d` / `observability_receiver.md §3.3`.
  - files: `config/user_config.py`. deps: none. size: S.
  - test: `GLUDD_RECEIVER__ENABLED=true` nested-env override; `enabled=false` default.
- **1A.3** Add `issue_sources: list[dict[str, Any]] = []` to `UserConfig`.
  - doc: `daemon_integration_plan.md §4d` / `issue_sources.md §3.6`.
  - files: `config/user_config.py`. deps: none. size: S.
- **1A.4** Add `dispatch: dict[str, Any] = {}` (`tool_loop_enabled`, `max_turns`).
  - doc: `daemon_integration_plan.md §6d`. files: `config/user_config.py`. deps: none. size: S.
- **1A.5** Add `self_update: dict[str, Any] = {}` (`auto_apply_config`, approval policy).
  - doc: `daemon_integration_plan.md §7d` / `self_update_router.md §4.1`. files: `config/user_config.py`. deps: none. size: S.
- **1A.6** Add `pid_group: str | None = None` to `PipelineConfigBlock`.
  - doc: `daemon_integration_plan.md §5d` / `pipeline_controller.md §3.2`. files: `config/user_config.py`. deps: none. size: S.

> **Bundle 1A.1–1A.6 into ONE edit pass on `config/user_config.py`** (Batch A in
> `daemon_integration_plan.md §8`). One file, no daemon coupling, isolated.

### Batch 1B — `load_startup_config` config loaders (isolated function in daemon.py)

- **1B.1** Load `connectors.yml` (or `uc.connectors`) into `cfg["connectors"]`, mirroring the `mcp_servers` load.
  - doc: `daemon_integration_plan.md §1a` (load location `daemon.py:72-164`).
  - files: `src/general_ludd/daemon.py` (`load_startup_config` only). deps: 1A.1. size: S. **[daemon.py — but isolated; safe to batch with 1B.2]**
- **1B.2** Load `issue_sources.yml` into `cfg["issue_sources"]` (mirror connectors).
  - doc: `daemon_integration_plan.md §4d`. files: `daemon.py` (`load_startup_config`). deps: 1A.3. size: S. **[daemon.py]**

### Batch 1C — new / extended router files (NO daemon.py edit — fully parallelizable)

- **1C.1** Build `connectors/loader.py` (`load_connectors`, `_resolve_class`, `_reject_raw_secrets`, `_instantiate`, `_BUILTIN_CLASSES`, `ConnectorConfigError`) + `connectors/transport.py` (`make_default_transport`).
  - doc: `connector_wiring_plan.md §1.1, §1.2` (signature-tolerant via `inspect.signature` over divergent transport kwargs). **Fill `_BUILTIN_CLASSES` from the full connector inventory in `docs/OBSERVABILITY_SOURCES.md` / `docs/privileges/README.md` — do NOT invent connectors.**
  - files: NEW `src/general_ludd/connectors/loader.py`, `connectors/transport.py`. deps: none. size: M.
  - capability/test: `tests/unit/test_connector_loader.py` — raw-secret guard rejects literal `token:`; a require-transport connector with `transport_factory=None` fails-closed (skip+log); kwarg-name selection picks `http_get` vs `transport` correctly.
- **1C.2** Extend `routers/observe.py` with the higher-level `timeline` / `correlate` / (optional `topology`) operations on the SAME router path prefix.
  - doc: `observe_debugging_roles.md §1.5` (one path prefix, F1/F3 hardening: defer SSRF to connector constructor; scrub `raw` from error records before HTTP return).
  - files: `src/general_ludd/routers/observe.py`. deps: 1C.1 (registry shape). size: M.
  - test: `tests/integration/test_observe_facade_wired.py` — two fake sources of different KINDs; failing source yields an `error` record, never a 500; PSK-gated (401 without bearer).
- **1C.3** Create `routers/self_update.py` (`register(app, _daemon_state)`) mirroring `routers/self_improve.py`/`spend.py`: `POST /admin/self-update/plan` and `POST /admin/self-update/enqueue`.
  - doc: `daemon_integration_plan.md §7b` + `self_update_router.md §5.1`. **Pre-flight: locate the NL→`SelfUpdatePlan` classifier (`self_update/classify.py` or similar) before building — flagged unread in the source doc.**
  - files: NEW `src/general_ludd/routers/self_update.py`. deps: none (handlers are lazy). size: M.
  - test: `tests/integration/test_self_update_router_wired.py` — config-tier request → `outcome=="applied"` + audit record; protected `.claude/` path → `outcome=="refused"`; no PSK → 401.
- **1C.4** Build `parse_otlp_logs`/`parse_otlp_metrics`/`parse_otlp_traces` + `parse_syslog` (the ONE new parser family) in `connectors/ingest_formats.py`; build `observability/receiver_buffer.py` (`ReceiverBuffer`).
  - doc: `observability_receiver.md §2.2, §2.3` (pure, fail-soft, `_too_big`-guarded, `MAX_EVENTS`-capped). The existing `parse_fluent_forward`/`parse_beats_lumberjack`/`parse_gelf` are REUSED, not rewritten.
  - files: `connectors/ingest_formats.py`, NEW `observability/receiver_buffer.py`. deps: none (pure). size: M.
  - test: parser fail-soft (garbage→`[]`, never raises); `MAX_EVENTS` truncation; buffer overflow policies (`drop_oldest`/`reject`/`spill`) + retention sweep.
- **1C.5** Build `routers/receiver.py` (`register` mounts only when `receiver_config.enabled`); endpoints `POST /v1/{logs,metrics,traces}`, `/ingest/{webhook,gelf,fluent,beats}`.
  - doc: `observability_receiver.md §1` + `daemon_integration_plan.md §3` (ingest-token auth, NOT admin PSK; body-size/rate/backpressure caps; NEVER in `_PUBLIC_PATHS`).
  - files: NEW `src/general_ludd/routers/receiver.py`. deps: 1C.4 (buffer). size: M.
  - test: `tests/integration/test_receiver_wired.py` — valid ingest token → 202 + buffer depth up; admin PSK alone → 401 from router; no token+no PSK → 503 `ingest_disabled` (fail-closed); reachable without `GLUDD_PSK` (bypassed admin gate).

### Batch 1D — the SINGLE `create_daemon_app` daemon.py edit (group ALL router registrations + middleware + cheap constructions) — **[daemon.py — SERIALIZE: one owner]**

Order WITHIN this single pass (per `daemon_integration_plan.md §8 Batch D`):

- **1D.1** `wire_observability(app, _daemon_state, app.state._startup_config.get("connectors", []))` + construct `GluddObserve`/`Observability` on `app.state._connector_registry` / `_observe_facade`.
  - doc: `daemon_integration_plan.md §1b, §2a` / `connector_wiring_plan.md §1.3, §2`. deps: 1B.1, 1C.1, 1C.2. size: S (within the pass).
  - PSK posture: `/api/observe/*` NOT added to `_PUBLIC_PATHS`.
- **1D.2** `receiver_router.register(app, _daemon_state)` + the `_is_public`/`_is_receiver` middleware change (prefix bypass for `/v1/`,`/ingest/` POSTs; keep fail-closed `GLUDD_REQUIRE_AUTH` branch for non-receiver/non-public).
  - doc: `daemon_integration_plan.md §3b, §3-PSK` (CONFLICT flagged: receiver POSTs must NOT use `_PUBLIC_PATHS`; distinct prefix allow-list). deps: 1C.5. size: M (the middleware change is the subtle bit).
- **1D.3** `self_update_router.register(app, _daemon_state)` + add to `routers/__init__.register_all` for parity.
  - doc: `daemon_integration_plan.md §7b`. deps: 1C.3. size: S.
- **1D.4** Add the `connectors` facet to `/api/facts` (`_connectors_facet`, mirroring `_spend_facet`; health probe gated behind `?include_connector_health=true`).
  - doc: `connector_wiring_plan.md §3`. files: `routers/facts.py` (separate file — can land in parallel with 1D, not part of the daemon.py pass). deps: 1D.1. size: S.

### Batch 1E — the SINGLE `_lifespan` daemon.py edit (group ALL lifespan constructions + EventLoop constructor args) — **[daemon.py — SERIALIZE: one owner; runs AFTER 1D lands]**

Order WITHIN this single pass (per `daemon_integration_plan.md §8 Batch E`):

- **1E.1** Construct `ReceiverBuffer` in `_get_or_create_extended_subsystems`; ADOPT `_daemon_state["receiver_buffer"]` rather than overwrite (one shared instance — mandatory or drained data is lost).
  - doc: `daemon_integration_plan.md §3a` (the buffer ordering CONFLICT). deps: 1C.5, 1D.2. size: S.
- **1E.2** Construct `IssueRegistry` + per-source `seen_keys` (`app.state._issue_registry` / `_issue_seen_keys`) after the project manager is available.
  - doc: `daemon_integration_plan.md §4a` + `issue_sources.md §3.6`. **Pre-flight: confirm `base.py` exports the symbols `ingest.py` imports (`Transition`/`IssueRecord`/`map_external_status`) — flagged mismatch in source doc.** deps: 1B.2, 1A.3. size: M.
- **1E.3** Construct the self-update audit sink closure over `session_factory` (`app.state._self_update_audit_sink`); `validate=None` is the safe fail-closed default for code-tier.
  - doc: `daemon_integration_plan.md §7a` / `self_update_router.md §3`. deps: 1C.3. size: S.
- **1E.4** Thread `uc`/`queues` into `_build_pipeline_controller`, build `make_pid_provider(queues)`, pass `pid_provider`/`pid_group` to `PipelineController`.
  - doc: `daemon_integration_plan.md §5a` (CONFLICT: `_build_pipeline_controller(pipeline_cfg, dispatcher)` does not receive `uc` today — one-line signature change at `daemon.py:745-747`). deps: 1A.6. size: S.
  - test: `tests/unit/test_pipeline_pid_wired.py` — `controller._dispatch_lane.desired_target()` reflects the PID provider, not the frozen `config.target`.
- **1E.5** Add `receiver_buffer=`, `issue_registry=`, `issue_seen_keys=`, `dispatcher=` to the `EventLoop(...)` constructor call.
  - doc: `daemon_integration_plan.md §8 Batch E`. deps: 1E.1–1E.3, plus Batch 1F's `EventLoop.__init__` params landing first or together. size: S.

### Batch 1F — `event_loop/loop.py` (new phases + `EventLoop.__init__` params) — ONE pass over loop.py

- **1F.1** Add `EventLoop.__init__` params: `receiver_buffer`, `issue_registry`, `issue_seen_keys`, `dispatcher` (all default `None`).
  - doc: `daemon_integration_plan.md §8 Batch F`. files: `event_loop/loop.py`. deps: none (additive). size: S.
- **1F.2** Add `drain_receiver_buffer` phase to `PHASE_ORDER` at index 1 (after `load_config_snapshot`); `_phase_drain_receiver_buffer` drains ≤500 records/tick into metrics (minimal first cut).
  - doc: `daemon_integration_plan.md §3c` + `observability_receiver.md §2`. deps: 1F.1. size: M.
- **1F.3** Add `poll_issue_sources` phase at index 1 (before drain; gated on a poll interval); `_phase_poll_issue_sources` fetches→`ingest_records`→`todo_repo.create`→update seen set; best-effort, never aborts the tick.
  - doc: `daemon_integration_plan.md §4c` + `issue_sources.md §3.3` (the additive nullable `external_id`/`issue_source` migration in `issue_sources.md §3.1` is a prerequisite for the dedup `UniqueConstraint`). deps: 1F.1, 1E.2. size: L (includes the DB migration + `IssueIngestRepository` upsert).
  - test: `tests/integration/test_issue_poller_wired.py` — one tick creates a todo with `external_id`; a SECOND tick creates NO duplicate (dedup via seen_keys + `uq_todo_external`).
- **1F.4** Add the `lifecycle_write_back` hook in `_phase_reconcile_completed_decisions` (guarded on the todo carrying an `external_id`); the write-back outbox phase per `issue_sources.md §3.4`.
  - doc: `daemon_integration_plan.md §4c` + `issue_sources.md §3.4` (at-least-once + idempotent apply). deps: 1F.3. size: M.

### Batch 1G — DynamicDispatcher in-loop tool loop (the DEEPEST change, behind a flag, LAST)

- **1G.1** In `_dispatch_execute_job`, after a model executor returns output: `parse_tool_calls` → `dispatcher.dispatch_all` → `apply_results(store, results)` → re-render next prompt from `VariableStore`, bounded by a max-turn count. Gated behind `dispatch.tool_loop_enabled` (default OFF) so existing single-shot dispatch is byte-for-byte unchanged.
  - doc: `daemon_integration_plan.md §6` (closes the `dynamic_dispatcher.py:8` TODO; `BURNDOWN.md` Section B #26). deps: ALL of 1A–1F green; 1A.4 config; the `collection` handler from Wave 2 item 2.2 if a tool-call of `kind=collection` is to resolve (else `collection` calls fail-closed harmlessly until 2.2 lands).
  - size: L.
  - test: `tests/unit/test_dispatch_tool_loop.py` — fake executor returns one tool_call then plain text; assert the handler was invoked and the second render reflected `dispatch__x__output`.

### Wave 1 security residuals (fold in here — small, high-value, from `security_tasks_status.md`)

- **1S.1** Feed a real per-call token-cost projection into `make_spend_guarded_executor` (today passes `projected_cost_usd=0.0`, so the cap is inert).
  - doc: `BURNDOWN.md §B #49/#27` + `security_tasks_status.md §#49`. files: `daemon.py` (the guard call site, in the 1D or 1E pass), a `token_cost_usd(model, est_in, est_out)` helper. deps: none beyond locating the call site. size: S. **[daemon.py — fold into 1E]**
  - test: a dispatch-path/lifespan test proving the cap engages on a real projection + survives restart-rehydrate.
- **1S.2** Add `func.avg(cost)` to `BenchmarkRepository.get_aggregate_scores` + a `cost` column to `BenchmarkResultModel`, so the scoring cost-cap (`route(max_cost_usd=)`) is not a silent no-op on production data.
  - doc: `BURNDOWN.md §B #59/#69` + `security_tasks_status.md §#59`. files: `db/repository.py`, `db/models.py` (+ migration). deps: none. size: M. (No daemon.py.)
  - test: `test_scoring.py` against the REAL aggregate (not mocked avg_cost) → cap engages.

---

## WAVE 2 — TOOL-CALLS-VIA-ANSIBLE MIGRATION

Source of record: `docs/design/tool_calls_via_ansible.md` (audit + migration
plan; the principle is "actions execute through Ansible collections & roles, not
bespoke Python"). General rule for every item: **wrap, don't rewrite** — a new
`gludd_<x>` module imports the existing `general_ludd.*` logic via a
`module_utils` shim (the `gludd_git` local-shim or `gludd_db` daemon-HTTP
pattern) and calls `capability_policy.for_role(role).check_*` before acting.
The git-playbook repoint detail is in `docs/design/git_execution_architecture.md`
and is **partly done already by `gludd_git`** (which holds the per-repo lock via
`GitAutomation`).

Ordering note: 2.1 and 2.3 are independent and parallelizable; 2.2 (the
`collection` handler) is what makes 2.3's modules reachable from a model
tool-call and from Wave 1's §1G loop — sequence 2.2 before relying on
collection-kind dispatch.

- **2.1** Retire/collapse `ExecutionEngine` (the dead parallel Python action stack).
  - doc: `tool_calls_via_ansible.md M1` (G1: `execution/engine.py` never instantiated — grep-confirmed). Either delete its action methods, or reduce it to a thin client that dispatches a playbook via `AnsibleRunnerAdapter` (the worker already does this at `worker/app.py:204-231`). Keep `_resolve_in_workspace` jail logic only if reused.
  - files: `src/general_ludd/execution/engine.py` (+ its tests, repointed to assert the playbook path). deps: none. size: M.
  - test: assert the engine commits through `GitAutomation`/playbook, not raw subprocess; the dead un-locked git path is gone.
- **2.2** Wire the `collection` dispatch kind (today hard-`None` at `daemon_wiring.py:184`).
  - doc: `tool_calls_via_ansible.md M2` (G2,G3). Add `make_collection_handler(ansible_runner)` → an async handler running a generic `dispatch_module.yml` parameterized by `module_name`/`module_args` extravars; replace `collection_handler=None`. Capability gating stays two-layer (`role_may_dispatch` in dispatcher + `capability_policy` in module).
  - files: `src/general_ludd/daemon_wiring.py`, NEW `playbooks/dispatch_module.yml`. deps: none (the in-loop call is Wave 1 §1G). size: M.
  - test: a collection tool-call dispatches the named module via the runner; a denied role fails-closed before the handler runs.
- **2.3** Add the missing `gludd_*` module surfaces for the Wave-1 subsystems (thin shims; daemon-HTTP where a single-writer/router boundary applies):
  - **2.3a (complete in beta.3)** `gludd_observe` now unblocks the six `observe_*` roles and removes every "DEFERRED WIRING #73" comment. It implements `query_sources`/`correlate_incident`/`timeline`/`topology` as a capability-gated `GluddClient` adapter over the existing daemon source endpoints and `GluddObserve` facade. Focused module tests cover the four ops, fail-closed discovery, source isolation, and local-only role grants.
  - **2.3b** `gludd_fetch` (HTTP as a gated action over `skills/fetcher`, gated by `check_network_host`). doc: `tool_calls_via_ansible.md M4`. size: S.
  - **2.3c** `gludd_receiver` / `gludd_issue_source` / `gludd_self_update` (thin shims; `self_update` requires `collections_self_modify`). doc: `tool_calls_via_ansible.md M5`. deps: Wave 1 receiver/issue/self_update endpoints. size: L.
  - **2.3d** Promote `gludd_mcp_tool` from placeholder against `MCPClient.call_tool`; record the inner-loop policy decision (recommendation: inner MCP loop stays Python, an E5 exception). doc: `tool_calls_via_ansible.md M6`. size: S.
  - **2.3e** `gludd_secret` (auditable, capability-gated secret resolution via `check_secret_access`). doc: `tool_calls_via_ansible.md M7`. size: S. (LOW.)
  - capability/test (all of 2.3): each new module needs a `CapabilityPolicy` entry in `_builtin_table()` granting ONLY the ops it invokes; extend the wiring unit test (`capability_policy.py:374-380` enforces "a new op REQUIRES a grant") to cover the new dimensions so a module added without a grant fails closed. doc: `tool_calls_via_ansible.md §(d)`.
- **2.4** Repoint the git playbooks onto a lock-aware path (PARTLY DONE via `gludd_git`).
  - doc: `git_execution_architecture.md §5a, §6 Step 3` — build a `gludd_git` Ansible role/module that opens `<repo>/.git/gludd-git.lock` + `fcntl.flock` with the SAME stale/timeout constants as `locking.py` (import them, do not re-hardcode), then convert `git_repo_init.yml`/`git_automate_change.yml`/`git_manage_worktree.yml`/`gitsign_configure.yml` to use it instead of raw `ansible.builtin.command: git`.
  - files: NEW `roles/gludd_git/` (or `library/gludd_git.py`); the four playbooks; expose `_LOCK_FILENAME`/`_DEFAULT_ACQUIRE_TIMEOUT`/`_DEFAULT_STALE_AFTER` as public constants in `git_automation/locking.py`. deps: none. size: M.
  - test: a role-git commit and a daemon-git commit on the same tree serialize on the one flock (cannot collide on `.git/index.lock`).

---

## WAVE 3 — UNIFIED DATA-SOURCE RELEVANCE + THRESHOLD PIPELINE

Source of record: `docs/design/unified_data_source_relevance.md` (Ansible
task-output parsing feeds the SAME pipeline as log/metric/trace parsing; each
source is rated for usefulness per task; user sets a threshold; the agent may
opt into sub-threshold sources). **All new packages — NO edits to `base.py`,
`normalize.py`, `observe/facade.py`, or `registry.py`** (the run source + tool
descriptors satisfy the existing contracts; that is the point). No daemon.py
churn except the one config field (3.3). Fully parallelizable with Wave 2.

Build order is the doc's §5.2:

- **3.1** `ansible/run_source.py` — `normalize_ansible_result(AnsibleResult) -> list[NormalizedRecord]` (KIND `"run"`) + `AnsibleRunSource` (duck-typed `Source`, provider-injected).
  - doc: `unified_data_source_relevance.md §1`. files: NEW `src/general_ludd/ansible/run_source.py`. deps: none (pure normalizer + wrapper). size: M.
  - test: synthetic `AnsibleResult` (failed/ok/skipped/unreachable) → every dict has all 8 `NormalizedRecord` keys, `kind=="run"`, `host`/`service` land in `labels` so `normalize_join_keys`/`topology` pick them up; provider that throws → error record, never raises.
- **3.2** `relevance/context.py` (`TaskContext`, `SourceDescriptor`) + `relevance/scorer.py` (`relevance(task, source) -> RelevanceScore`, the affinity table, the optional `model_scorer` hook). Pure, offline, heuristic-only.
  - doc: `unified_data_source_relevance.md §2` (reuse `TaskType` from `schemas/benchmark.py`; mirror `AdaptiveRouter`'s composite-with-weights shape). files: NEW `relevance/context.py`, `relevance/scorer.py`. deps: none. size: M.
  - test: affinity table (`DEBUGGING`→run/logs high, `OPTIMIZATION`→metrics high); usefulness `None` renormalizes (no crash); non-finite cost ⇒ max penalty; score clamped `[0,1]`.
- **3.3** `DataSourceConfigBlock` + `data_sources` field on `UserConfig` + `relevance/selection.py` (`SourceSelector.select_sources` / `honor_optin`, `Selection`/`RatedSource`) over a fake provider.
  - doc: `unified_data_source_relevance.md §3`. files: `config/user_config.py` (one field — bundle with Batch 1A if Wave 1 hasn't shipped yet, else its own tiny edit), NEW `relevance/selection.py`. deps: 3.2. size: M.
  - test: `threshold=0.6` splits auto/optional; `per_task_type` override lowers the bar for debugging; `max_auto_sources` caps width; `GLUDD_DATA_SOURCES__THRESHOLD=0.4` env override; opt-in honoring admits an enumerated optional name and REJECTS a name in neither set (SSRF/allowlist posture).
- **3.4** `relevance/catalog.py` (`ToolCatalog` → tool/collection/role descriptors) + capability gating of opt-in + dispatcher opt-in honoring.
  - doc: `unified_data_source_relevance.md §3.4, §4.2` (a `coder` role's optional menu excludes `collection` sources via `role_may_dispatch`; dispatch fail-closes a denied kind a second time). files: NEW `relevance/catalog.py`. deps: 3.3; Wave 2 §2.2 (`collection` handler) for the execution backend of opt-in tool sources. size: M.
- **3.5** `BenchmarkRepository.get_source_usefulness(task_type, source_kind)` learned signal (LAST; the system is useful without it; renormalizes to `None` until the table is populated).
  - doc: `unified_data_source_relevance.md §2.3 signal 2, §5.1`. files: `db/repository.py`. deps: 3.2. size: M. (Pairs naturally with Wave 1 §1S.2's cost column work on the same repo — coordinate the two `db/repository.py` edits.)

---

## WAVE 4 — CONSOLIDATION CLEANUPS

Source of record: `git_execution_architecture.md §5b, §6` (the lock gap) plus
the BURNDOWN housekeeping flags. These close the remaining un-locked git
bypasses and dedupe. Independent of Waves 1–3; can run in parallel once Wave 2
§2.4's lock constants are public.

- **4.1** Make `GitAutomation` uniformly lock-aware: wrap `init_repo`, `clone`, `create_worktree`, `remove_worktree`, `merge_branch`, the tag helpers, `push_to_remote`, `create_local_bare_mirror` in `git_repo_lock(target/repo_path)` (today only `_run_git` locks; these raw `subprocess.run` methods bypass it).
  - doc: `git_execution_architecture.md §5b, §6 Step 2`. **This is the "repoint repo.py's racy worktree fns to the serialized path" item — `create_worktree`/`remove_worktree`/`list_worktrees` (#62/#64) currently bypass the flock.** files: `src/general_ludd/git_automation/repo.py`. deps: Wave 2 §2.4 not strictly required but shares the lock contract. size: M.
  - test: each mutating method occurs inside the lock; a concurrent worktree-create + integration-commit on one repo serialize.
- **4.2** Wrap the remaining Python git bypasses: `worktree/core.py:_reclaim_worktree_dir` (worktree remove/prune), `pr_delivery.py` `git push`, `manager.py` clone.
  - doc: `git_execution_architecture.md §5b`. files: `worktree/core.py`, `git_automation/pr_delivery.py`, `projects/manager.py`. deps: none. size: S each.
- **4.3** Add a guardrail test that fails if a NEW `subprocess.run(["git", ...])` or `ansible.builtin.command: git ...` is introduced outside the two sanctioned choke points (`GitAutomation` / the `gludd_git` role) — prevents the lock gap silently reopening.
  - doc: `git_execution_architecture.md §6 Step 6`. files: `tests/unit/test_guardrails.py`. deps: 4.1, 4.2, Wave 2 §2.4. size: S.
- **4.4** Housekeeping (from `BURNDOWN.md §Housekeeping`): commit/fence the untracked `pipeline/`, `receiver/`, `issue_sources/`, `self_update/`, `observe/`, `orchestration/` packages with a TASKS.md ledger row + e2e proof each (do NOT tick "delivered" until reachable); confirm `RATCHET_MAX` in `test_guardrails.py` equals live `config/ratchet.yml`; gitignore `nested/`/`proj-ok/`/`.claude/`; centralize `routers/coordination.py`+`routers/dispatch.py` into `register_all`.
  - doc: `BURNDOWN.md` Housekeeping + Section B (orchestration/ "verify if it duplicates the already-wired `scheduling/Scheduler`"). deps: respective waves landed. size: M (spread).

---

## Serialization constraints — the hard `daemon.py`-churn items (single writer)

These MUST NOT be edited by two agents at once. Sequence strictly:

1. **Batch 1A** (`config/user_config.py`) — isolated file, do first, then it's done.
2. **Batch 1B** (`load_startup_config`) — isolated function in daemon.py; safe to do as one small pass.
3. **Batch 1D** (`create_daemon_app`) — **ONE owner**, one pass, ALL router registrations + the `_is_public`/`_is_receiver` middleware change together. Lands before 1E.
4. **Batch 1E** (`_lifespan`) — **ONE owner**, one pass, ALL lifespan constructions + EventLoop constructor args + the `_build_pipeline_controller` signature change + the §1S.1 spend-projection call site. Runs after 1D.
5. **Batch 1F** (`event_loop/loop.py`) — **ONE owner**, one pass, all new phases + `__init__` params. Coordinates with 1E.5 (the EventLoop call must match the new signature).

Everything else (Batch 1C new/extended router files, all of Wave 2's modules,
all of Wave 3's new packages, Wave 4's repo.py/worktree edits) touches
DISTINCT files and is freely parallelizable — these are the "aggressive
parallelism on independent new-files work" candidates.

## Cross-wave dependency summary

- Wave 1 Batch 1A/1C are the unblockers — start them first and in parallel.
- Wave 1 §1G (tool loop) and Wave 2 §2.2 (`collection` handler) are mutually
  reinforcing: §2.2 makes collection-kind tool-calls resolve; §1G is the loop
  that emits them. Land §2.2 before depending on collection dispatch in §1G.
- Wave 2 §2.3 modules depend on the Wave 1 endpoints they shim (observe →
  §1C.2/§1D.1; receiver/issue/self_update → their Wave 1 routers).
- Wave 3 §3.4 opt-in execution depends on Wave 2 §2.2.
- Wave 1 §1S.2 (avg_cost) and Wave 3 §3.5 (source usefulness) both edit
  `db/repository.py` — coordinate as one owner of that file.
- Wave 4 §4.1/§4.3 share the lock contract with Wave 2 §2.4 (public lock
  constants in `locking.py`).

## Source docs cited (read the named section before implementing an item)

`docs/design/daemon_integration_plan.md`, `connector_wiring_plan.md`,
`unified_data_source_relevance.md`, `git_execution_architecture.md`,
`tool_calls_via_ansible.md`, `pipeline_controller.md`,
`observe_debugging_roles.md`, `issue_sources.md`, `self_update_router.md`,
`observability_receiver.md`, `backlog_audit_system.md`, `feature_gap_backlog.md`;
`docs/audit/BURNDOWN.md`, `docs/audit/security_tasks_status.md`.

> NOT scheduled in Waves 1–4: `backlog_audit_system.md` (#65) and
> `feature_gap_backlog.md` G1–G13 (#38) are the NEXT-AFTER-THIS backlog
> (`BURNDOWN.md` Section C, "OPEN / design-only"). They depend on nothing in
> Waves 1–4 and are deliberately deferred until the built-but-unwired PARTIAL
> column (Waves 1–2) is closed — wiring existing tested code is strictly higher
> ROI than greenfield. Pull them forward only after Wave 1 proves green e2e.
