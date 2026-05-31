# Session State

> This file is maintained automatically. Update it after every logical unit of work.
> The agent MUST read this file at session start to restore context.

## Last Updated
- 2026-05-31

## Current Status
- **Phase**: Post-sprint0, feature development
- **Test Suite**: 794 passing, 11 skipped, 0 failures, 91.4% coverage
- **Last Commit**: 24a8f7f (PlanArtifact + Conversation)

## Sprint0 Objectives (ALL COMPLETE)
- obj01: Project skeleton
- obj02: Model gateway (LangChain)
- obj03: Prompt registry (Jinja2)
- obj04: Event loop (10-phase tick)
- obj05: DB models + schemas
- obj06: Worker (FastAPI + Gunicorn)
- obj07: Config hot-reload
- obj08: Runtime packaging (native_uv, native_pip, container)
- obj09: Quality tools (ruff, mypy, pytest-cov)
- obj10: OpenBao secrets manager
- obj11: Ansible runner
- obj12: Guardrails (three-layer pattern)
- obj13: Git workflow (granular make targets)
- obj14: Documentation (feature parity matrix, decisions)
- obj15: Molecule coverage for Ansible content
- obj16: Dogfood loop (agent improves itself)

## Additional Features Implemented
1. Per-pattern model routing (models/router.py)
2. Gateway fallback chains (models/gateway.py)
3. RunBudgetGuard (controllers/budget.py)
4. MCP client skeleton (mcp/)
5. MCP stdio transport (mcp/transport.py, mcp/client.py)
6. Context compaction (agents/context.py)
7. SKILL.md format (skills/)
8. PlanArtifact (planning/artifact.py)
9. Conversation persistence (review/conversation.py)
10. AgentBehavior codification (agents/behavior.py) — IN PROGRESS
11. Behavior prompt renderer (agents/behavior.py) — IN PROGRESS

## Architecture
- Entry: `event_loop/cli.py` -> `EventLoop.run_forever()`
- Tick phases: load_config, claim_returns, dispatch_review, evaluate_pid, evaluate_rules, refill_buckets, claim_todos, dispatch_execute, reconcile_decisions, emit_metrics
- Prompt rendering: `PromptRegistry` (Jinja2) -> `ReturnReviewer` -> model call (stub) -> `TaskDecision`
- Agent dispatch: `AgentRegistry` -> `AgentDispatcher` with concurrency control
- Config: `config/agents/`, `config/model_profiles/`, `config/skills/`, `config/mcp_servers/`

## Key Gaps (Known)
- `prompt_profile` declared but never resolved (AgentConfig.prompt_profile is always None)
- `ReturnReviewer._call_model()` is a stub
- Skills `body` field not injected into prompts
- Event loop phases 4 (PID) and 5 (rules) are stubs
- OpenBao not wired into worker/runner pipeline
- MCP client not wired into event loop phases

## Next Steps
1. Complete AgentBehavior codification (currently in progress)
2. Wire prompt_profile resolution into pipeline
3. Integrate MCP tools into event loop phases
4. Wire OpenBao into worker/runner
5. Write cross-cutting e2e tests
