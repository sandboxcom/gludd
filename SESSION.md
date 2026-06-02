# Session State

> This file is maintained automatically. Update it at session start to restore context.

## Last Updated
- 2026-06-01

## Current Status
- **Phase**: Azure ContainerApp generator, CLI bugfixes, Anthropic docs, new endpoint tests
- **Test Suite**: 2160 passed, 26 skipped, 0 failures, 93.91% coverage
- **Branch**: master
- **Latest commit**: 5add075 feat: Azure ContainerApp terraform generator, CLI bugfixes, Anthropic docs, new endpoint tests
- **Mypy**: 0 errors in 132 source files (strict mode)
- **Lint**: 0 errors (ruff), 0 noqa suppressions in src/
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

## Secret Migration (COMPLETE)
- `migrate_profile_secrets()` in `src/general_ludd/secrets/migration.py` — scans model profiles, resolves credential_alias from env vars, writes to OpenBao KV v2, registers SecretAlias
- `scrub_inline_secrets()` — removes inline secret fields (api_key, password, external_token) from YAML config files
- Wired into daemon `_lifespan()`: called after `build_secrets_resolver()` when secrets resolver has `write_secret` (i.e., is a real SecretsManager, not EnvSecretsManager)
- Profile dicts converted via `model_dump()` before passing to migration
- Migration failures logged as warnings, do not block daemon startup
- Fixed lint (SIM102 nested ifs) and mypy errors (tuple type, str assertions)
- Fixed test path mismatch bugs: migration stores at `model-profiles/{id}/credential_alias`, tests were using wrong paths
- 8 tests in `tests/unit/test_secret_migration.py`
- 2 wiring tests in `tests/unit/test_secret_migration_wiring.py`

## Data Flow Wiring (COMPLETE)

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

### Phase 2: TaskReturn persistence (COMPLETE — commit c7ce18c)
- `_persist_task_return()` captures HTTP response from worker and persists via `TaskReturnRepository.create()`
- `_persist_review_response()` for review jobs

### Phase 3: Decision persistence (COMPLETE — commit c7ce18c)
- `_persist_review_response()` writes `TaskDecisionModel` from worker review response
- Reconcile phase transitions todo status with audit event emission

### Phase 4: MetricsCollector into ModelGateway (COMPLETE)
- `metrics_collector` and `metrics_agent_id` params on ModelGateway
- Records input/output tokens, success, cost per `call_model()` invocation
- 2 tests in `test_pipeline_wiring.py`

### Phase 5: AuditEventRepository (COMPLETE)
- `AuditEventRepository` with `create()`, `list_by_entity()`, `list_by_project()`
- Auto-created from session in EventLoop `__init__`
- 3 tests in `test_data_flow_e2e.py`

### Phase 6: Queue seeding in DB (COMPLETE)
- Part of Phase 1 — `seed_initial_queues()` called in daemon lifespan

### Phase 7: Variable namespaces (COMPLETE)
- `VariableNamespaceRepository` with `load_vars_for_project()`, `create_namespace()`, `set_var()`
- Project-scoped loading: project values override global values
- Auto-created from session in EventLoop `__init__`
- 6 tests in `test_variable_repo.py`

### Phase 8: Prompt profile resolution (COMPLETE)
- `_resolve_prompt_text_static()` — resolves profile name to rendered template text
- `prompt_registry` param on EventLoop
- `prompt_text` field added to `JobSpec`
- Resolved text passed in dispatch (both runner and HTTP paths)
- 5 tests in `test_pipeline_wiring.py`

### Phase 9: Config snapshot deep copy (COMPLETE — commit 7a74689)
- `_phase_load_config_snapshot` now uses `copy.deepcopy(self.config)` for isolation
- Config snapshot includes `shared_vars` from `VariableNamespaceRepository` when available
- Nested mutations to `self.config` after snapshot do not affect the snapshot
- 3 tests in `test_pipeline_wiring.py` (deep copy isolation, shared vars, no repo)

### PID Controller Phase (WIRED)
- `_phase_evaluate_pid_controllers()` collects real system metrics via psutil (loadavg, cpu, memory, disk)
- Calls `LoadController.evaluate_snapshot()` with `LoadSnapshot(active_jobs=0)`
- Gracefully handles errors (e.g., `getloadavg` unavailable on macOS)

### Skills Injection (WIRED)
- `_resolve_skill_body()` matches todo title against skill trigger patterns via `SkillRegistry.match_trigger()`
- Body injected as `skill_body` in dispatch vars
- `SkillRegistry` wired into daemon lifespan, auto-discovers skills from config_dir

## Daemon Lifespan Integration (COMPLETE)
- 6 tests in `tests/integration/test_daemon_lifespan.py`
- Tests: startup with real SQLite DB, table creation, queue seeding, EventLoop tick, todo API, engine disposal, config dir loading

## Subsystem Wiring Summary
All subsystems wired into daemon lifespan via `_get_or_create_extended_subsystems()`:
- `metrics`: MetricsCollector
- `projects`: ProjectManager
- `utilization`: UtilizationTracker
- `model_registry`: ModelRegistry
- `skill_registry`: SkillRegistry (auto-discovers from config_dir)

EventLoop auto-creates from session (when available):
- `TodoRepository` (existing pattern)
- `TaskReturnRepository` (existing pattern)
- `AuditEventRepository` (new)
- `VariableNamespaceRepository` (new)

## Quality Status
- **Mypy**: 0 errors in 132 source files (strict mode)
- **Lint**: 0 errors (ruff)
- **Tests**: 2160 passed, 26 skipped, 93.91% coverage
- **No deprecation warnings**: `tool.uv.dev-dependencies` removed

## Key Gaps (Known)
- EventLoop session lifecycle: when session_factory is passed (production), DB-dependent phases silently skip
- `build_secrets_resolver()` cannot call async `health_check()` from sync context
- ZAI API 429 (balance exhaustion) — live identity tests xfail until recharged
- MCP catalog search hits real registry APIs (no offline fallback) — now has `refresh()` for cache invalidation
- Skills catalog is curated-only (no GitHub auto-discovery yet) — now has `refresh()` for cache invalidation
- Files still below 85%: cli.py (73%), ansible/core_runner.py (76%), runtime/pip_bundle.py (88%), runtime/release.py (88%)
- events/bus.py (83%) — async subscriber paths with `asyncio.run()` fallback not covered

## MCP Secrets from Vault (COMPLETE)
- `env_aliases` field on `MCPServerConfig`: maps env var names to credential aliases
- `resolve_mcp_env()` in `src/general_ludd/mcp/secrets.py`: resolves aliases from secrets manager at runtime
- `scrub_mcp_config()`: removes plaintext secrets from MCP YAML config files
- YAML config uses `env_aliases` instead of `env` for sensitive values
- 9 tests in `tests/unit/test_mcp_secrets.py`

## MCP Server Catalog (COMPLETE)
- `MCPCatalog` in `src/general_ludd/mcp/catalog.py`: search/discover MCP servers
- Queries official registry (registry.modelcontextprotocol.io), Smithery (api.smithery.ai), Glama (glama.ai)
- 10 curated known servers with env_aliases_needed (github, gitlab, slack, brave-search, etc.)
- 10 tests in `tests/unit/test_mcp_catalog.py`

## Skills Catalog (COMPLETE)
- `SkillCatalog` in `src/general_ludd/skills/catalog.py`: search/download/install curated skills
- 10 curated skills (tdd-discipline, security-first, git-conventional-commits, etc.)
- Search by query, tags, category. Download as SKILL.md files. Install into config dir.
- 16 tests in `tests/unit/test_skills_catalog.py`
- Research sources: Anthropic skills, OpenAI skills, Antigravity, VoltAgent, SkillsCat

## Worker Multi-Project Isolation Tests (COMPLETE)
- 6 integration tests in `tests/integration/test_worker_isolation.py`
- Verifies: project-scoped variables isolated between projects
- Dispatch claims only one project's todos per tick
- Dispatch jobs contain only the target project's data
- Project workspaces are filesystem-isolated
- EventLoop does not write to project workspace (read-only dispatch)
- EventLoop reads all projects for dispatch (knows about all)

## HuggingFace Model Integration (COMPLETE)
- 7 tests in `tests/integration/test_hf_model_integration.py`
- Live tests (skipif RUN_HF_TESTS): search, get_model_info, download tiny-gpt2, list, remove
- Unit tests: mock hf_hub_download, snapshot_download

## Local Inference Integration (COMPLETE)
- 5 tests in `tests/integration/test_local_inference_integration.py`
- Start/stop llamacpp server, start vllm server, build commands
- Manual test for real inference with tiny model (skipif)

## ZAI/GLM Live Feature Tests (COMPLETE)
- 6 tests in `tests/live/test_zai_live_features.py` (skipif ZAI_API_KEY)
- ReturnReviewer with real GLM model
- ModelGateway routing with real model
- Code generation with real model
- Conversation planning with real model

## Commits This Session
1. `c0bdcf8` — fix: VariableNamespaceRepository project-scoped loading with global override semantics
2. `b555167` — feat: wire skill_registry into daemon lifespan, remove stale tool.uv.dev-dependencies
3. `b075b46` — feat: auto-create audit_repo and variable_repo from session in EventLoop
4. `098ed8e` — fix: resolve all mypy errors (19->0), fix LoadSnapshot field names, clean up unused mypy overrides
5. `7a74689` — feat: wire secret migration into daemon startup, deep config snapshot, daemon lifespan integration tests
6. `a5d28e7` — feat: MCP secrets from Vault, MCP catalog, skills catalog, worker isolation tests, HF integration, local inference tests, ZAI live feature tests
7. `038a302` — feat: remove all noqa suppressions, add refresh to caches, improve test coverage across 15 files
8. `5add075` — feat: Azure ContainerApp terraform generator, CLI bugfixes, Anthropic docs, new endpoint tests

## Azure ContainerApp, CLI Fixes, Docs, Endpoint Tests (COMPLETE — commit 5add075)

### Azure ContainerApp Terraform Generator
- `_generate_azure_containerapp()` in terraform.py: generates Azure ContainerApp HCL with VNet, ACR, environment, ingress, cost tags
- `deploy_type` field on `ComputeConfig`: `"vm"` (default) or `"containerapp"`
- `TerraformGenerator.generate()` dispatches to ContainerApp when `provider=azure` + `deploy_type="containerapp"`
- 9 tests in `TestTerraformGeneratorAzureContainerApp`

### CLI Bugfixes
- `_cmd_mcp_search`: fixed `r.get("name")` → `r.get("server_name")` to match daemon response
- `_cmd_skills_install`: fixed `data.get('path')` → `data.get('installed')` to match daemon response

### Anthropic/Claude Model Profile
- `config/model_profiles/anthropic_example.yml`: claude-sonnet-4-20250514, credential_alias ANTHROPIC_API_KEY, langchain-anthropic package

### New CLI Subcommand Tests
- `tests/unit/test_new_cli_commands.py`: 13 tests for mcp search/list/info, skills search/list/install, compute endpoints/register

### New Daemon Endpoint Tests
- `tests/unit/test_new_daemon_endpoints.py`: 12 tests for MCP catalog search/list/detail, skills catalog search/list/install, compute endpoint register/unregister/list

### Documentation Updates
- `dist/README.md`: Added Anthropic/Claude provider section, MCP server workflow, skills workflow, compute endpoint workflow, Azure ContainerApp section, new CLI commands, new daemon API endpoints
- `docs/quickstart.md`: Updated prerequisites (SQLite default, Anthropic provider), simplified database setup, added MCP/skills/compute quickstart sections

## Noqa Cleanup and Cache Refresh (COMPLETE — commit 038a302)
- **Removed all noqa suppressions** from src/ (E501 in skills/catalog.py, RUF006 in events/bus.py x2, E712 in db/repository.py x2) and tests/ (E731 in test_deployment.py, test_binary_paths.py; RUF006 in test_obj04_event_loop.py)
- **Fixed EventBus RUF006**: Added `_background_tasks` set to store asyncio.Task references, preventing GC of fire-and-forget tasks
- **Fixed QueueRepository/ProjectRepository E712**: Changed `== True` to `.is_(True)` for SQLAlchemy boolean comparisons
- **Fixed skills/catalog.py E501**: Broke long description string across lines
- **Fixed test lambdas E731**: Replaced `lambda` assignments with proper `def` functions
- **Added `refresh()` method** to MCPCatalog, SkillCatalog, and ModelRegistry for cache invalidation
- **New test files**: `test_db_migrations.py` (5 tests), `test_ansible_manifest.py` (11 tests)
- **Extended test coverage** across 13 existing test files
- **Coverage**: 94.23% (up from 91.95%), 2126 tests (up from 2037)
- **Files now at 85%+**: db/migrations.py (100%), mcp/catalog.py (99%), ansible/manifest.py (98%), skills/catalog.py (100%), worker/gunicorn_conf.py (100%), agents/registry.py (100%), runtime/validator.py (95%), controllers/load_scrape.py (97%), runtime/container.py (96%), review/decision_applier.py (94%)
