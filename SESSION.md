# Session State

> This file is maintained automatically. Update it at session start to restore context.

## Last Updated
- 2026-05-31

## Current Status
- **Phase**: Audit complete, all gaps fixed
- **Test Suite**: 1416 passed, 0 failed, 12 skipped, 91.85% coverage
- **Mypy**: 0 errors (111 source files)
- **Lint**: 0 errors (ruff)
- **SAST**: 0 high-severity issues (bandit)
- **Last Commit**: 638ef49
- **Branch**: master

## Sprint0 Objectives (ALL COMPLETE)
obj01-obj16 all complete.

## Additional Features Implemented
1. Per-pattern model routing (models/router.py)
2. Gateway fallback chains (models/gateway.py)
3. RunBudgetGuard (controllers/budget.py)
4. MCP client skeleton + stdio transport (mcp/)
5. Context compaction (agents/context.py)
6. SKILL.md format (skills/)
7. PlanArtifact (planning/artifact.py)
8. Conversation persistence (review/conversation.py)
9. AgentBehavior codification (agents/behavior.py)
10. Behavior prompt renderer (agents/behavior.py)
11. Ephemeral GPU compute + Terraform generator (infra/)
12. Tree-sitter repo map (planning/repo_map.py)
13. YAML-driven task definitions (schemas/task_definition.py, config/task_loader.py)
14. MCP tools wired into event loop + agent tool adapter
15. Budget caps wired into event loop + BUDGET_EXCEEDED status
16. Model router wired into gateway + reviewer
17. Conversation wired into ReturnReviewer
18. PlanArtifact wired into Todo + JobSpec + event loop dispatch
19. Model routing YAML config (config/model_routing.yml, config/model_routing.py)
20. User config layer — read-only override + agent-editable (config/user_config.py, config/loader.py)
21. Ansible process_isolation_* options (ansible/isolation.py)
22. Codify directive skill (config/skills/codify_directive.md)
23. BinaryPathConfig + BinaryPathResolver (config/binary_paths.py) — 18 tests
24. DeploymentManager — terraform/opentofu lifecycle (infra/deployment.py) — 13 tests
25. OpenBaoConfig backend/binary_path fields + container launch + health_check (secrets/) — 13 tests
26. Containerfile updated with terraform+tofu, Makefile container-build/run/push targets
27. ansible-core library runner — CoreAnsibleRunner + AnsibleTemplater (ansible/core_runner.py, ansible/templating.py) — 34 tests
28. AnsibleRunnerAdapter delegates to CoreAnsibleRunner (ansible-runner dep removed)
29. All mypy type errors resolved (0 errors across 111 source files)
30. 53 e2e tests for infra features (binary paths, deployment, secrets, ansible, containerfile)
31. Unified CLI: single `hottentot` binary with daemon/add/status/list/log-level/deployments/version/health
32. Daemon app: FastAPI with embedded EventLoop as lifespan background task (daemon.py)
33. Direct dispatch: EventLoop accepts runner param — daemon passes AnsibleRunnerAdapter (no HTTP loopback)
34. Hot-loading: heavy modules lazy-imported only in daemon mode; client imports only argparse + httpx
35. Runtime log level: POST /admin/log-level + httpx debug logging when log_level=debug
36. PyInstaller spec (hottentot.spec) + make build-executable target
37. Tarball installer: make dist — systemd unit, install.sh, config/, templates/, docs/, binary
38. Deprecated hottentot-worker and hottentot-loop deleted; README updated
39. Security: SAST (bandit), SBOM (cyclonedx-py), pip-audit, OPA/Rego policies — 14 tests
40. 40 CLI + daemon tests, 18 audit gap e2e tests, 27 installer tests

## Architecture
- Entry: `hottentot daemon` -> FastAPI lifespan -> EventLoop.run_forever() as asyncio.Task
- Client: `hottentot add/status/list/log-level/deployments/health` -> httpx -> daemon HTTP API
- Direct dispatch: EventLoop calls AnsibleRunnerAdapter directly (no HTTP loopback)
- Hot-loading: daemon mode lazy-imports event_loop, gateway, ansible, db, secrets, mcp
- Tick phases: load_config, claim_returns, dispatch_review, evaluate_pid, evaluate_rules, refill_buckets, claim_todos, dispatch_execute, reconcile_decisions, emit_metrics
- Config layer: UserConfig (read-only) > AgentConfig (agent-editable) > project defaults
- Model routing: config/model_routing.yml -> ModelRoutingConfig -> ModelRouter -> ModelGateway
- Agent behavior: AgentBehavior -> BehaviorRenderer -> system prompt section
- Ansible: CoreAnsibleRunner (ansible-core library) -> AnsibleRunnerAdapter
- Infra: TerraformGenerator -> DeploymentManager -> BinaryPathResolver -> terraform/tofu
- Secrets: OpenBaoConfig (backend=vault|openbao) + SecretsManager + BinaryPathResolver
- Security: bandit SAST + cyclonedx-py SBOM + pip-audit + OPA/Rego policies

## Key Gaps (Known)
- ReturnReviewer._call_model() is a stub (no real LLM calls in tests)
- Skills body field not injected into prompts
- Event loop phases 4 (PID) and 5 (rules) are stubs
- OpenBao not wired into worker/runner pipeline
- No DB migration for plan_artifact column on TodoModel

## Next Steps
1. Wire prompt_profile resolution into pipeline
2. Wire OpenBao into worker/runner
3. DB migration for plan_artifact column
4. Implement PID rules engine and rules evaluation
5. Real LLM call integration in ReturnReviewer
