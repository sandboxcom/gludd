# Session State

> This file is maintained automatically. Update it at session start to restore context.

## Last Updated
- 2026-06-07 (session 6)

## Current Status
- **Phase**: Coverage improvement — cli.py and daemon.py coverage lifts
- **Test Suite**: 3262 passed, 26 skipped, 88.39% coverage
- **Branch**: master
- **Latest commit**: 446de31 — Wire quantization tracker into AdaptiveRouter at daemon startup
- **Mypy**: 0 errors
- **Lint**: 0 errors

## This Session: Coverage Improvement (COMPLETE)
- cli.py coverage lift (9ca4817): Extracted 5 TUI table builders (`_build_controls_table`, `_build_daemon_table`, `_build_info_table`, `_build_binary_table`, `_build_config_table`) from `_cmd_tui` closures to module-level. Added 39 tests in `test_tui_extracted_builders.py` covering: table builders, `_cmd_help`, filestore CLI (4 cmds), integrity CLI (5 cmds), ansible CLI (3 cmds), `_scan_local_integrity`, `_load_config_editor`. cli.py: 59% → 66%
- daemon.py coverage lift (14e73c0): Added 22 tests in `test_daemon_filestore_integrity.py` covering 6 filestore endpoints, 5 integrity endpoints, 3 ansible endpoints, selftest endpoint. Fixed `/admin/filestore/write` bug (`request: Any` → `request: Request`). daemon.py: 73% → 81%
- Quantization router wiring (446de31): `AdaptiveRouter` now receives `quantization_map` from `_quantization_tracker` state at daemon startup. 2 tests in `TestQuantizationWiring`. daemon.py: 81%

## Files Below 85% Coverage (priority order)
1. cli.py — 66% (1954 lines, 664 miss — TUI `_cmd_tui` body still untested)
2. daemon.py — 81% (1035 lines, 192 miss — models discover/discovered, local inference, code blocks)
3. ansible/core_runner.py — 79% (136 lines, 29 miss)
4. filestore/bootstrap.py — 87% (133 lines, 17 miss)
5. agents/dispatcher.py — 92% (66 lines, 5 miss)
6. secrets/manager.py — 87% (135 lines, 18 miss)
7. planning/repo_map.py — 90% (163 lines, 17 miss)

## Previous Session: Guardrail Hardening (COMPLETE)
- Guardrail hardening committed as e0916b6
- All guardrail tests passing

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
- **Mypy**: 0 errors (strict mode)
- **Lint**: 0 errors (ruff)
- **Tests**: 3032 passed, 26 skipped, 87.16% coverage

## Key Gaps (Known)
- EventLoop session lifecycle: when session_factory is passed (production), DB-dependent phases silently skip
- `build_secrets_resolver()` cannot call async `health_check()` from sync context
- ZAI API 429 (balance exhaustion) — live identity tests xfail until recharged
- Files still below 85%: quality/config.py (0%), cli.py (58%), daemon.py (75%), ansible/core_runner.py (79%)
- events/bus.py (88%) — async subscriber paths with `asyncio.run()` fallback not covered
- enforce-make plugin has runtime bug: scans full command for forbidden words including target names (fix committed but requires opencode restart)
- No `/admin/todos` daemon endpoint — TUI todos view will show empty until endpoint is added

## Next Steps
- Improve `cli.py` coverage — extract TUI sub-functions for testability
- Improve `daemon.py` coverage — test stub endpoints returning 503 without DB
- Add integration tests for new TUI views against daemon API
- Wire quantization map from daemon state into AdaptiveRouter at daemon startup

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

## Preflight Quality Gate (COMPLETE)
- `src/general_ludd/quality/preflight.py` — 8 pre-commit checks: coverage>85%, lint, mypy, templates, playbooks, molecule, filestore, sprint boxes
- `verify_task_completion(criteria, evidence)` — task completion verification with keyword matching
- `make preflight` target — runs all 8 checks, exits non-zero on failure
- `make test-and-commit` — now runs preflight BEFORE tests
- Enforce-make plugin — pre-commit warning + task-verification injection on completion claims
- `delete-file` Makefile target added
- 15 unit tests + 8 integration tests (real daemon via ASGITransport)

## Status Audit & Fixes (COMPLETE)
- **Bug: uptime_ticks always 0** — EventLoop never wrote `total_ticks` to `_daemon_state`. Fixed by:
  - EventLoop now accepts `daemon_state` parameter
  - `tick()` increments persistent `_total_ticks` counter and writes metrics to daemon_state
  - Daemon lifespan passes `_daemon_state` to EventLoop constructor
- **False-positive test deleted**: `test_enhanced_status.py` duplicated handler code instead of testing real daemon
- **Real integration tests**: `tests/integration/test_enhanced_status_real.py` — 8 tests against actual daemon via ASGITransport
- **test_daemon.py:test_status_endpoint** expanded from 2 field checks to all 11 enhanced fields
- **Benchmark schemas exported** from `schemas/__init__.py` (BenchmarkResult, BenchmarkScores, PromptProfile, RoutingCandidate, RoutingDecision, TaskType)
- **config/prompt_profiles/collected/.gitkeep** created (directory now exists)
- **SESSION.md cleaned**: duplicate/contradictory lines removed, test counts updated, commit hash current

## Commits This Session
1. `c0bdcf8` — fix: VariableNamespaceRepository project-scoped loading with global override semantics
2. `b555167` — feat: wire skill_registry into daemon lifespan, remove stale tool.uv.dev-dependencies
3. `b075b46` — feat: auto-create audit_repo and variable_repo from session in EventLoop
4. `098ed8e` — fix: resolve all mypy errors (19->0), fix LoadSnapshot field names, clean up unused mypy overrides
5. `7a74689` — feat: wire secret migration into daemon startup, deep config snapshot, daemon lifespan integration tests
6. `a5d28e7` — feat: MCP secrets from Vault, MCP catalog, skills catalog, worker isolation tests, HF integration, local inference tests, ZAI live feature tests
7. `038a302` — feat: remove all noqa suppressions, add refresh to caches, improve test coverage across 15 files
8. `5add075` — feat: Azure ContainerApp terraform generator, CLI bugfixes, Anthropic docs, new endpoint tests
9. `ae29f60` — feat: System prompt collection, benchmark scoring engine, adaptive router, DB persistence
10. `3b60aa6` — feat: Wire AdaptiveRouter into EventLoop, add benchmark daemon endpoints and CLI commands
11. `7056549` — fix: allow vcs make targets in enforce-make plugin, add repo alias targets

## System Prompt Collection (COMPLETE — commit ae29f60)

### Collection Script
- `scripts/collect_prompts.py` — fetches system prompts from GitHub repos of known coding agents
- Sources configured: Aider (editblock, wholefile, udiff, func prompts), OpenHands (codeact agent prompts), SWE-agent (default_prompts.yaml), Cline (system.ts, tools.ts)
- Extracts prompts from Python string literals, YAML files, TypeScript template literals
- Outputs YAML files to `config/prompt_profiles/collected/` with metadata (source, source_url, tags, version)
- Make target: `make collect-prompts` (optional `SOURCE=aider` for specific agent)
- Repeatable: re-runs update existing profiles (upsert by name)

### Benchmark Scoring Engine
- `src/general_ludd/scoring/engine.py` — `PromptScoringEngine` with 10 default benchmark tasks
- Each task covers one `TaskType` enum value (bug_fix, feature, refactor, test_write, code_review, documentation, debugging, optimization, security_fix, integration)
- Scoring dimensions: `completion_score` (pattern matching), `code_quality_score` (structure analysis), `instruction_adherence_score` (forbidden patterns, "return only" checks), `token_efficiency_score` (line length analysis)
- Composite score: weighted 35% completion + 25% code_quality + 25% instruction + 15% token_efficiency
- `BenchmarkScores` Pydantic model with `composite_score` computed property

### Adaptive Router
- `src/general_ludd/scoring/router.py` — `AdaptiveRouter` selects best prompt+model based on historical scores
- Queries `BenchmarkRepository.get_aggregate_scores()` grouped by (prompt_profile_id, model_profile_id, task_type)
- Routes to highest composite_score combo with sufficient samples (default min_samples=3)
- Falls back to configured defaults when insufficient historical data
- Cost-aware: `max_cost_usd` parameter switches to cheapest qualifying combo when best is too expensive
- `get_leaderboard(task_type)` returns ranked list of all combos
- Cache with 5-minute TTL, invalidatable

### DB Persistence
- `PromptProfileModel` table: id, name (unique), source, source_url, prompt_text, task_types (JSON), tags (JSON), version, collected_at
- `BenchmarkResultModel` table: id, prompt_profile_id (FK), model_profile_id, task_type, 4 score columns, time_seconds, input_tokens, output_tokens, cost_usd, success, error_message, raw_output, created_at
- `PromptProfileRepository`: upsert (by name), get_by_name, get_by_id, list_all, list_by_source, list_for_task_type
- `BenchmarkRepository`: record_result, get_aggregate_scores (with GROUP BY), get_best_for_task (min_samples), get_model_scores, list_recent
- Alembic migration `004_add_benchmark_tables.py`: creates both tables with indexes

### Schemas
- `src/general_ludd/schemas/benchmark.py` — TaskType (10 values), PromptProfile, BenchmarkScores, BenchmarkResult, RoutingCandidate, RoutingDecision

### Tests
- `tests/unit/test_scoring.py` — 30 tests: TaskType, BenchmarkScores (4 tests), PromptProfile, BenchmarkResult, RoutingDecision, RoutingCandidate, BenchmarkTask (3 tests), PromptScoringEngine (12 tests), AdaptiveRouter (9 tests)
- `tests/unit/test_benchmark_models.py` — 6 tests: DB model field checks, column defaults
- `tests/unit/test_benchmark_repo.py` — 17 tests: PromptProfileRepository (8 tests), BenchmarkRepository (9 tests) with in-memory SQLite

## AdaptiveRouter EventLoop Wiring, Benchmark Endpoints, CLI (COMPLETE — commit 3b60aa6)

### EventLoop Wiring
- `adaptive_router` param added to `EventLoop.__init__()` (optional, defaults to None)
- `_resolve_adaptive_prompt(todo)` — async method that classifies todo's work_type into TaskType, calls router.route(), returns (prompt_id, model_id, decision)
- `_dispatch_execute_job()` now tries adaptive routing first; uses adaptive results when decision.fallback=False, falls back to static prompt_profile/model_profile from todo when fallback=True or no router
- Both runner path and HTTP path use resolved profiles
- 7 tests in `tests/unit/test_adaptive_routing.py`: no router, override, fallback, task type classification, unknown work type default, no router returns None, runner path uses adaptive

### Daemon Admin Endpoints
- `GET /admin/benchmark/scores` — aggregate benchmark scores, optional task_type filter
- `GET /admin/benchmark/recent` — recent benchmark results (default limit 50)
- `GET /admin/benchmark/leaderboard` — ranked prompt+model combos via AdaptiveRouter
- `POST /admin/benchmark/record` — record a new benchmark result
- `GET /admin/prompt-profiles` — list all prompt profiles
- All endpoints gracefully return empty data when no DB session available
- 8 tests in `tests/unit/test_benchmark_endpoints.py`

### CLI Commands
- `gludd scores [--task-type TYPE] [--daemon-url URL]` — view benchmark scores from daemon
- `gludd leaderboard [--task-type TYPE] [--daemon-url URL]` — view ranked prompt+model leaderboard with formatted table output
- 10 tests in `tests/unit/test_benchmark_cli.py`

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

## Azure IAM Least-Privilege Policy and Provider Auth (COMPLETE)
- **IAM Policy**: `config/infra/azure-iam-policy.json` — Custom role with minimal permissions for Container App, ACR, VNet, Subnet, Resource Group, NSG, Public IP, NIC, VM, Disk, Deployments, Tags, Diagnostics
- **Documentation**: `docs/azure-iam-setup.md` — Step-by-step Azure Portal and CLI instructions for creating the role, assigning the managed identity/SP, and configuring agent auth
- **ComputeConfig**: Added `provider_auth_aliases: dict[str, str] | None` field mapping ARM env var names to secret aliases (e.g., `{"ARM_CLIENT_ID": "AZURE_CLIENT_ID"}`)
- **DeploymentManager**: Added `secrets_resolver` param (implements `SecretsResolver` protocol), `_inject_auth_env()` and `_restore_auth_env()` methods to inject/restore provider credentials around Terraform subprocess calls
- **Destroy auth**: `destroy()` now uses `_last_config` to restore auth context when tearing down infrastructure
- **Env template**: `dist/install.sh` now includes ARM_SUBSCRIPTION_ID, ARM_TENANT_ID, ARM_CLIENT_ID, ARM_CLIENT_SECRET, ARM_USE_MSI Azure credential env vars
- **Tests**: 7 new tests in `test_provider_auth.py` (ComputeConfig auth aliases, deploy injection, env restore, cleanup on failure, destroy injection, MSI flags); 3 updated tests in `test_deployment.py` (init with/without secrets_resolver); 3 updated tests in `test_infra_compute.py` (provider_auth_aliases defaults, full config, serialization)

## CLI Graceful Error Handling and Wiring Fix (COMPLETE)
- **Graceful offline errors**: All daemon-requiring CLI commands (`status`, `add`, `list`, `health`, `deployments`, `log-level`, `models`, `mcp`, `skills`, `compute`, `scores`, `leaderboard`, `local-serve`) now detect `httpx.ConnectError`/`httpx.ConnectTimeout` and print a user-friendly message: "Cannot connect to daemon at URL. Is the daemon running? Start it with: gludd daemon"
- **`_handle_connection_error()` helper**: Centralized error handler that distinguishes ConnectError, ConnectTimeout, and generic exceptions
- **Subcommand help**: `gludd models`, `gludd mcp`, `gludd skills`, `gludd compute` without a subcommand now show their specific help text instead of the root help
- **New command**: `gludd compute unregister <endpoint_id>` wires up the existing `DELETE /admin/compute/endpoints/{endpoint_id}` daemon endpoint
- **Top-level httpx import**: Moved `import httpx` to module top-level since `_handle_connection_error` needs exception types
- **Tests**: 13 new offline-error tests in `tests/e2e/test_cli_e2e.py`, 4 subcommand-help tests, 3 compute-unregister tests, 1 command-existence audit; 6 new tests in `tests/unit/test_cli.py` (unregister, status/add offline errors, subcommand-help tests, unregister parsing)
