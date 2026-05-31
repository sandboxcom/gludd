# Session State

> This file is maintained automatically. Update it at session start to restore context.

## Last Updated
- 2026-05-31

## Current Status
- **Phase**: Post-sprint0, feature development, ready for merge
- **Test Suite**: 1319 passed, 0 failed, 10 skipped, 91.32% coverage
- **Mypy**: 0 errors (112 source files checked)
- **Lint**: 0 errors (ruff)
- **Last Commit**: 137beff
- **Branch**: feature/ephemeral-gpu-compute

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
28. AnsibleRunnerAdapter delegates to CoreAnsibleRunner instead of ansible_runner subprocess
29. All 29 mypy type errors resolved across 13 files (0 errors now)
30. 53 e2e tests for new features (binary paths, deployment, secrets, ansible, containerfile)

## Architecture
- Entry: `event_loop/cli.py` -> `EventLoop.run_forever()`
- Tick phases: load_config, claim_returns, dispatch_review, evaluate_pid, evaluate_rules, refill_buckets, claim_todos, dispatch_execute, reconcile_decisions, emit_metrics
- Config layer: UserConfig (read-only) > AgentConfig (agent-editable) > project defaults
- Model routing: config/model_routing.yml -> ModelRoutingConfig -> ModelRouter -> ModelGateway
- Agent behavior: AgentBehavior -> BehaviorRenderer -> system prompt section
- Ansible: CoreAnsibleRunner (ansible-core library) -> AnsibleRunnerAdapter (no subprocess)
- Ansible templating: AnsibleTemplater wraps CoreAnsibleRunner.render_template() for skills/prompts
- Ansible isolation: ProcessIsolationConfig -> CoreAnsibleRunner options
- Infra: TerraformGenerator -> HCL for AWS/GCP/Azure/RunPod/Vast.ai
- Deployment: DeploymentManager -> BinaryPathResolver -> terraform/tofu init/apply/destroy
- Binary paths: BinaryPaths (Pydantic) + BinaryPathResolver (shutil.which + overrides)
- Secrets: OpenBaoConfig (backend=vault|openbao) + SecretsManager.start_local_container() + health_check()
- Container: Containerfile (podman/docker) + make container-build/run/push

## Key Gaps (Known)
- ReturnReviewer._call_model() is a stub (no real LLM calls in tests)
- Skills body field not injected into prompts
- Event loop phases 4 (PID) and 5 (rules) are stubs
- OpenBao not wired into worker/runner pipeline
- No DB migration for plan_artifact column on TodoModel

## Next Steps
1. Merge feature branch to master
2. Wire prompt_profile resolution into pipeline
3. Wire OpenBao into worker/runner
4. DB migration for plan_artifact column
5. Implement PID rules engine and rules evaluation
