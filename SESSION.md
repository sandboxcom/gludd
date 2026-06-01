# Session State

> This file is maintained automatically. Update it at session start to restore context.

## Last Updated
- 2026-05-31

## Current Status
- **Phase**: Project rename complete (agentic_harness -> general_ludd, hottentot -> gludd)
- **Test Suite**: Pending verification after rename
- **Branch**: master

## Rename Completed
All references renamed:
- `agentic_harness` (Python namespace) -> `general_ludd`
- `hottentot-agent` (pip package) -> `general-ludd-agent`
- `hottentot` (CLI binary) -> `gludd`
- `hottentot-agent:latest` (container image) -> `gl-agent:latest`
- `hottentot.service` -> `general-ludd.service`
- `hottentot.spec` -> `gludd.spec`
- `~/.config/hottentot/` -> `~/.config/general-ludd/`
- `.hottentot/` -> `.general-ludd/`
- Source directory moved: `src/agentic_harness/` -> `src/general_ludd/`

## Sprint0 Objectives (ALL COMPLETE)
obj01-obj16 all complete.

## Architecture
- Entry: `gludd daemon` -> FastAPI lifespan -> EventLoop.run_forever() as asyncio.Task
- Client: `gludd add/status/list/log-level/deployments/health` -> httpx -> daemon HTTP API
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
