# Session State

> This file is maintained automatically. Update it at session start to restore context.

## Last Updated
- 2026-06-01

## Current Status
- **Phase**: Multi-project isolation complete, merged to master
- **Test Suite**: 1827 passed, 12 skipped, 0 failures, 92.84% coverage
- **Branch**: master
- **Latest commit**: merge of feature/multi-project-isolation

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

## Key Gaps (Known)
- ReturnReviewer._call_model() is a stub
- Skills body field not injected into prompts
- PID rules engine and rules evaluation are stubs
- OpenBao not fully wired into worker/runner pipeline
- No DB migration for plan_artifact column on TodoModel
- Local inference deps (llama-cpp-python, vllm) not in pyproject.toml
- `tool.uv.dev-dependencies` deprecation warning (cosmetic)

## Next Steps
1. Wire prompt_profile resolution into pipeline
2. Wire OpenBao into worker/runner pipeline
3. DB migration for plan_artifact column on TodoModel
4. Implement PID rules engine and rules evaluation
5. Real LLM call integration in ReturnReviewer
6. Wire CLI `gludd models search/download` subcommands
7. Wire CLI `gludd local-serve` subcommand
8. Add llama-cpp-python and vllm as optional dependencies
