# Feature Package Wiring Status Audit

**Audit date:** 2026-06-16
**Method:** Import-graph trace from `src/general_ludd/daemon.py` → registered routers → Ansible collections/roles.
**Scope:** The ~27 "G1-G27" gap packages landed in commit 4314a6c, plus observability satellites.

---

## Summary

| Bucket | Count |
|---|---|
| WIRED (daemon.py or registered router imports the package) | 3 |
| PARTIAL (module exists + tested; router exists but is not registered in `register_all`) | 2 |
| ORPHANED (source/tests exist; nothing in live import graph touches it) | 3 |
| NOT CREATED (no source directory, no tests) | 24 |
| NAMING MISMATCH (tests exist but target a different namespace) | 4 |

Top 5 to wire next (highest product value first): `connectors/observe` router registration, `receiver`, `issue_sources`, `self_update`, `event_bus` (already lives as `events.bus`).

---

## Full Package Table

| Package | Module exists? | Has tests? | Wired in daemon/router/role? | Importer (file:line) | Status |
|---|---|---|---|---|---|
| `scoring` | YES | YES — `tests/unit/test_scoring.py` | YES | `daemon.py:54` (`AdaptiveRouter`); `routers/benchmark.py:8`; `routers/models.py:33` | **WIRED** |
| `self_improve` | YES (`__init__.py`) | YES — 5 unit + 1 integration | YES | `routers/self_improve.py:8`; `routers/projects.py:11` | **WIRED** |
| `pipeline` | YES (`controller.py`, `daemon_adapters.py`, `state.py`, `lanes.py`) | YES — 3 unit + 1 e2e | YES (lazy, config-gated) | `daemon.py:441–473` inside `_build_pipeline_controller()`, active when `pipeline.enabled=True` | **WIRED** |
| `connectors` | YES (`__init__.py`, `base.py`, `normalize.py`, `registry.py`) | YES — `test_connectors_base.py`, `test_connector_normalize.py` | PARTIAL | `routers/observe.py:6` imports `ConnectorRegistry` — but `observe` router is NOT registered in `register_all` | **PARTIAL** |
| `observe` (router only) | NO standalone package; `routers/observe.py` exists | NO | PARTIAL | `routers/observe.py` exists and imports from `connectors`; not registered in the app | **PARTIAL** |
| `receiver` | YES (`buffer.py`, `parsers.py`, `router.py`) | YES — `test_receiver_buffer.py`, `test_receiver_parsers.py`, `test_receiver_router.py` | NO | no importer found | **ORPHANED** |
| `issue_sources` | YES (`base.py`) | YES — `test_issue_sources_base.py`, `test_issue_sources_ingest.py` | NO | no importer found | **ORPHANED** |
| `self_update` | YES (`__init__.py`, `applier.py`, `router.py`) | YES — `test_self_update_applier.py`, `test_self_update_router.py` | NO | no importer found (no HTTP entry point, not wired into daemon) | **ORPHANED** |
| `memory` | NO | NO | NO | no importer found | NOT CREATED |
| `eval` | NO | NO | NO | no importer found | NOT CREATED |
| `retrieval` | NO | NO | NO | no importer found | NOT CREATED |
| `sandbox` | NO | NO | NO | no importer found | NOT CREATED |
| `self_improve/outcome_loop` | NO | NO | NO | no importer found | NOT CREATED |
| `prompt_versioning` | NO | NO | NO | no importer found | NOT CREATED |
| `hitl` | NO | NO | NO | no importer found | NOT CREATED |
| `scoring/pareto` | NO | NO | NO | no importer found | NOT CREATED |
| `replay` | NO | NO | NO | no importer found | NOT CREATED |
| `tool_registry` | NO | NO | NO | no importer found | NOT CREATED |
| `consensus` | NO | NO | NO | no importer found | NOT CREATED |
| `rate_limit` | NO | NO | NO | no importer found | NOT CREATED |
| `cost_report` | NO | NO | NO | no importer found | NOT CREATED |
| `run_timeline` | NO | NO | NO | no importer found | NOT CREATED |
| `output_schema` | NO | NO | NO | no importer found | NOT CREATED |
| `resilience` | NO | NO | NO | no importer found | NOT CREATED |
| `preview` | NO | NO | NO | no importer found | NOT CREATED |
| `patch_apply` | NO | NO | NO | no importer found | NOT CREATED |
| `provenance` | NO | NO | NO | no importer found | NOT CREATED |
| `config_schema` | NO | NO | NO | no importer found | NOT CREATED |
| `repro` | NO | NO | NO | no importer found | NOT CREATED |
| `redaction` | NO | NO | NO | no importer found | NOT CREATED |
| `audit_log` | NO | NO | NO | no importer found | NOT CREATED |
| `health` | NO | NO | NO | no importer found | NOT CREATED |
| `normalize` (standalone) | NO (lives as `connectors/normalize.py`) | tests via `test_connector_normalize.py` | NO | no importer found as standalone | NOT CREATED |
| `planner` | NO standalone | `test_orchestration_planner.py` tests `general_ludd.scheduling.planner` — wrong namespace | NO | no importer found | NAMING MISMATCH |
| `context` | NO standalone | `test_context_compaction.py` tests context compaction under different namespace | NO | no importer found | NAMING MISMATCH |
| `event_bus` | NO standalone | 3 test files test `general_ludd.events.bus.EventBus` — `events` package, not `event_bus` | YES (as `events.bus`) | `daemon.py` imports `general_ludd.events.bus.EventBus` (top-level) | NAMING MISMATCH (package wired under different name) |
| `health` (naming) | NO standalone | `test_model_health_wiring.py` tests `general_ludd.models.timeout_detector.ModelHealthTracker` | NO as `health` pkg | `models.timeout_detector` path, not a `health` package | NAMING MISMATCH |

---

## Daemon.py Top-Level Import Inventory

These are the packages currently reachable from daemon startup (lines 19–62):

```text
general_ludd.ansible.isolation        general_ludd.ansible.runner
general_ludd.config.binary_paths      general_ludd.config.loader
general_ludd.config.model_routing     general_ludd.config.task_loader
general_ludd.config.user_config       general_ludd.controllers.budget
general_ludd.db.repository            general_ludd.db.session
general_ludd.event_loop.loop          general_ludd.events.bus
general_ludd.events.hooks             general_ludd.filestore.bootstrap
general_ludd.filestore.store          general_ludd.infra.utilization
general_ludd.logging.project_log      general_ludd.mcp.loader
general_ludd.metrics.collector        general_ludd.models.gateway
general_ludd.models.model_registry    general_ludd.observability.dashboard_data
general_ludd.observability.otel_bridge general_ludd.observability.recorder
general_ludd.projects.manager         general_ludd.projects.workspace
general_ludd.prompts.registry         general_ludd.quality.preflight
general_ludd.reload.worker_broadcast  general_ludd.scoring.router
general_ludd.secrets.*                general_ludd.skills.loader
general_ludd.skills.registry
```

Lazy/deferred imports (loaded inside lifespan or helper functions):

```text
general_ludd.agents.dispatcher        general_ludd.agents.registry
general_ludd.controllers.budget_manager general_ludd.controllers.spend_limiter
general_ludd.daemon_wiring.*          general_ludd.db.migrations
general_ludd.db.repository (extended) general_ludd.hardware.probe
general_ludd.observability.metrics_exporter general_ludd.observability.run_history
general_ludd.observability.trace_store general_ludd.pipeline.controller
general_ludd.pipeline.daemon_adapters  general_ludd.pipeline.state
general_ludd.review.reviewer          general_ludd.routers.* (all registered routers)
general_ludd.security.auth            general_ludd.worktree.core
```

---

## Prioritized "Wire These Next" List

Ordered by estimated product value (unblocking active user-facing workflows first):

1. **`connectors` + `observe` router registration** — The `observe` router and `ConnectorRegistry` already exist and are tested; the only missing step is adding `observe` to `register_all`. Unlocks the live event-feed and connector health endpoints. Zero new code required.

2. **`receiver`** — Buffer, parsers, and internal router are written and tested. Wiring it connects the inbound webhook/event ingestion path that feeds `issue_sources` and downstream pipelines. One import + FastAPI include needed.

3. **`issue_sources`** — Depends on `receiver` being live. Base class + ingest logic tested. Wiring enables GitHub/Linear/Slack issue ingestion that drives the core agent dispatch loop.

4. **`self_update`** — Applier and router are written and tested. Wiring adds the self-hosting update path (`POST /self_update/apply`), completing the dogfood autonomy loop. One router include + daemon lifecycle hook.

5. **`event_bus` / `events.bus`** — Already wired in daemon under the `events.bus` namespace. No wiring work needed; the task is to reconcile the naming mismatch (the G-set calls this `event_bus` but the live symbol is `general_ludd.events.bus.EventBus`). Update the G-set spec or add a re-export alias so the audit label matches reality.

6. **`planner` (as `scheduling.planner`)** — `OrchestrationPlanner` is tested under `general_ludd.scheduling.planner`. Wire it into the daemon's dispatch path so multi-step task planning is active. Same namespace reconciliation issue as `event_bus`.

7. **`self_improve/outcome_loop`** — `self_improve` is already wired; the `outcome_loop` subpackage is the next internal capability to add. No HTTP surface needed — just wire it into the `self_improve` lifespan callback.

8. **`resilience`** — RetryPolicy + CircuitBreaker are referenced in memory notes as wired into the gateway; confirm whether they live under `resilience` or another path, then either create the package or update the spec label.

---

## Findings for the Daemon-Integration Wave

- **3 of 36 audited packages** are genuinely wired end-to-end.
- **24 packages** have no source files at all — they are spec labels, not shipped code.
- **The shortest path to measurable progress** is the `observe` router registration (1-line change) followed by `receiver` and `issue_sources` wiring (2–3 imports each), which together complete the inbound event pipeline that the rest of the agent dispatch loop depends on.
- **Naming mismatches** (`event_bus`, `planner`, `health`, `context`) should be resolved before the integration wave begins to avoid spec drift.
