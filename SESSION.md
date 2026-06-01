# Session State

> This file is maintained automatically. Update it at session start to restore context.

## Last Updated
- 2026-06-01

## Current Status
- **Phase**: Data flow wiring (Phases 1, 4, 5, 8 complete)
- **Test Suite**: 1935 passed, 12 skipped, 0 failures, 92.44% coverage
- **Branch**: master
- **Latest commit**: c7ce18c wire DB into daemon lifespan, SQLite WAL, metrics, audit repo, prompt resolution
- **Distributables**: dist/general-ludd-agent-0.1.0-Darwin-arm64.tar.gz + .sha256 checksum

## Sprint0 Objectives (ALL COMPLETE)
obj01-obj16 all complete.

## Multi-Project Isolation (COMPLETE)

### Database Layer
- `ProjectModel` table with `project_id` primary key, `name`, `workspace_path`, `config`, `active`
- `project_id` FK column (nullable) added to all 8 models: TodoModel, TodoEventModel, TaskReturnModel, TaskDecisionModel, QueueModel, AuditEventModel, VariableNamespaceModel, BucketLeaseModel
- `VariableNamespaceModel` uniqueness now per-project (composite `namespace` + `project_id`)
- `ProjectRepository` with create/get/list_active/deactivate
- `TodoRepository.list_by_status(project_id=)` — project-scoped
- `TodoRepository.claim_runnable(project_id=)` — project-scoped
- `TaskReturnRepository.claim_unreviewed(project_id=)` — project-scoped
- All queries backward-compatible: `project_id=None` returns all (unscoped)

### Schemas
- `Todo` Pydantic model: `project_id: str | None = None`
- `JobSpec`: `project_id: str | None = None`
- `Conversation`: `project_id: str | None = None`, serialization includes project

### EventLoop
- Accepts `project_manager` in constructor
- `_phase_claim_runnable_todos` uses `project_manager.select_project()` for weighted routing
- Falls back to global claim when no project manager

### Logging
- `ProjectLogAdapter` — injects `[project_id]` prefix into log messages
- `ProjectLogFilter` — adds `project_id` attribute to `LogRecord`
- `src/general_ludd/logging/project_log.py`

### Metrics
- `MetricsCollector.list_agents(project=)` — filter agents by project
- `MetricsCollector.get_cost_by_project()` — returns `dict[project, cost_usd]`
- `AgentMetrics.project` field populated on registration

### Secrets
- `ProjectSecretsManager` — wraps base SecretsManager with scoped paths (`projects/{project_id}/{path}`)
- Read/write are project-namespaced, isolated between projects
- `src/general_ludd/secrets/project_secrets.py`

### Filesystem
- `ProjectWorkspace` — per-project directory structure (artifacts, logs, config, repo, runner)
- `ProjectManager.add_project()` accepts `workspace_path` and `repo_url`
- `src/general_ludd/projects/workspace.py`

### CLI
- `--project` flag on: daemon, add, status, list commands
- `_cmd_add` includes `project_id` in POST payload
- `_cmd_list` passes `project_id` as query parameter

### Daemon
- `AddTodoRequest` includes `project_id: str | None`
- `api_add_todo` stores project_id on todo
- `api_list_todos` filters by `project_id` query parameter

### Tests
- `tests/unit/test_project_isolation_db.py` — 19 tests for DB isolation
- `tests/unit/test_project_isolation_subsystems.py` — 21 tests for subsystem isolation
- `tests/unit/test_project_workspace.py` — 5 tests for filesystem isolation
- `tests/e2e/test_multi_project_isolation.py` — 8 E2E tests for daemon API isolation

## Hot-Reload System (COMPLETE)
- EventBus, HookSystem, HotReloader, WorkerBroadcaster
- 82 e2e tests

## Agent Metrics (COMPLETE)
- MetricsCollector, AgentMetrics, ModelUsage, CostEstimate
- 50 unit tests

## Other Completed Systems
- Multi-project allocation (ProjectManager with weights)
- Compute utilization maximizer (UtilizationTracker)
- HuggingFace model registry (ModelRegistry)
- Local inference manager (LocalInferenceManager)
- ansible-core library refactor (CoreAnsibleRunner)
- BinaryPathConfig, DeploymentManager
- Security: SAST, SBOM, pip-audit, OPA
- Unified CLI (`gludd`), PyInstaller spec, tarball installer
- Anti-stop guardrails (3-layer enforcement)

## Anti-Stop Bug Fix (COMPLETE)
- `AGENTS.md` — expanded forbidden patterns list (audit-findings-stop, plan-then-stop, etc.)
- `.opencode/plugin/enforce-make.ts` — expanded TASK_COMPLETION_WARNING and system prompt injection
- Key rule: "THE ONLY VALID RESPONSE TO IDENTIFYING WORK IS TO DO IT."

## Audit Gap Fixes (COMPLETE)
- Alembic migration `002_add_projects_and_project_id.py` — creates `projects` table + `project_id` columns on all 8 models
- EventLoop `_phase_claim_unreviewed_task_returns` now respects project_manager
- EventLoop `_phase_reconcile_completed_decisions` now filters by project
- EventLoop `_dispatch_execute_job` passes `project_id` on JobSpec
- Worker logs and writes `project_id` to job vars
- Integration tests: `tests/integration/test_multi_project_integration.py` (3 tests)
- Self-audit policy codified in AGENTS.md (7-step checklist)
- `feature-done` Makefile target now builds distributables post-merge

## User-Facing Dist & Docs (COMPLETE)
- `config/general-ludd.yml` — main user-facing config with commented sections
- `config/model_routing.yml` — fixed non-null default_profile
- `config/agents/default_agents.yml` — fixed non-null model_profile on all agents
- `config/examples/` — fixed to reference real profiles (zai_coder, build, plan)
- `UserConfig.database` field added for PostgreSQL config
- `docs/quickstart.md` — step-by-step getting started guide
- `docs/configuration.md` — full config reference with all sections
- `docs/architecture.md` — system overview for users
- `docs/internal/` — moved sprint0.md, features-to-decide.md, feature-parity-matrix.md out of dist
- `dist/README.md` — tarball readme with quick start, CLI reference, directory layout
- `dist/install.sh` — rewritten with preflight checks, no auto-start, config preservation, macOS support
- `dist/general-ludd.service` — localhost binding, dedicated user, env file, read-only home
- `Makefile dist` target — includes README, selective docs only, sha256 checksums
- `Makefile dist-clean` — cleans stale hottentot artifacts
- 16 dist readiness tests + 2 UserConfig database tests
- `tests/unit/test_dist_readiness.py` — validates config, docs, install script, systemd unit

## Secrets Wiring (COMPLETE)
- `build_secrets_resolver()` in daemon.py — creates OpenBao SecretsManager if configured, falls back to EnvSecretsManager
- `load_model_profiles()` in daemon.py — reads config/model_profiles/*.yml into ModelProfile objects
- `load_startup_config()` loads model profiles into cfg["model_profiles"]
- Startup config stored in app.state._startup_config
- docs/configuration.md updated with complete credential flow docs (OpenBao → env vars → error)
- dist/README.md rewritten with credential flow, per-provider examples, vault commands
- dist/install.sh env template expanded with all provider env vars
- 12 tests in test_secrets_wiring_startup.py

## Data Flow Wiring (IN PROGRESS)

### Phase 1: Wire DB into daemon lifespan (COMPLETE — commit c7ce18c)
- `init_engine_from_config()` — creates engine from config dict, defaults to SQLite
- `ensure_tables()` — auto-creates all tables for SQLite
- `seed_initial_queues()` — idempotent, seeds 12 queues on first run
- `run_wal_pragmas()` — WAL mode, busy_timeout=5000, FK ON, mmap, cache
- `get_default_db_url()` — returns `sqlite+aiosqlite:///~/.local/share/general-ludd/general-ludd.db`
- `is_sqlite_url()` — detects SQLite vs PostgreSQL URLs
- Session factory passed to EventLoop as `async_sessionmaker`
- EventLoop accepts both `AsyncSession` and `async_sessionmaker` for `session` param
- `aiosqlite` moved to production dependencies
- Daemon lifespan: creates engine, ensures tables, seeds queues, creates session factory
- Engine disposed on shutdown
- EventBus, HookSystem, ProjectManager wired into EventLoop from daemon subsystems

### Phase 2: TaskReturn persistence (NOT STARTED)
- Worker response captured and persisted via `TaskReturnRepository.create()`

### Phase 3: Decision persistence (NOT STARTED)
- `TaskDecisionModel` written after ReturnReviewer

### Phase 4: MetricsCollector into ModelGateway (COMPLETE)
- `metrics_collector` and `metrics_agent_id` params on ModelGateway
- Records input/output tokens, success, cost per `call_model()` invocation
- 2 tests in `test_pipeline_wiring.py`

### Phase 5: AuditEventRepository (COMPLETE)
- `AuditEventRepository` with `create()`, `list_by_entity()`, `list_by_project()`
- 3 tests in `test_data_flow_e2e.py`

### Phase 6: Queue seeding in DB (COMPLETE)
- Part of Phase 1 — `seed_initial_queues()` called in daemon lifespan

### Phase 7: Variable namespaces (NOT STARTED)
- `shared_vars` still `None` everywhere

### Phase 8: Prompt profile resolution (COMPLETE)
- `_resolve_prompt_text_static()` — resolves profile name to rendered template text
- `prompt_registry` param on EventLoop
- `prompt_text` field added to `JobSpec`
- Resolved text passed in dispatch (both runner and HTTP paths)
- 5 tests in `test_pipeline_wiring.py`

### Phase 9: Config snapshot (PARTIAL)
- `_phase_load_config_snapshot` does `dict(self.config)` but config now contains model_profiles, rules

## Key Gaps (Known)
- Skills body field not injected into prompts
- PID controller phase is still no-op (rules engine wired, PID not)
- `_phase_evaluate_pid_controllers` still pass
- `tool.uv.dev-dependencies` deprecation warning (cosmetic)
- TaskReturn capture from worker HTTP response not wired
- Decision persistence after ReturnReviewer not wired
- Variable namespaces never loaded from DB
- Config snapshot still shallow copy

## Remaining Next Steps
1. Phase 2: Capture worker HTTP response → persist TaskReturn
2. Phase 3: Write TaskDecisionModel after ReturnReviewer
3. Phase 7: Load variable namespaces from DB into dispatch
4. Wire PID controller phase
5. Inject skills body into prompts
6. Fix `tool.uv.dev-dependencies` deprecation warning
