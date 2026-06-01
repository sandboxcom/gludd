# Session State

> This file is maintained automatically. Update it at session start to restore context.

## Last Updated
- 2026-06-01

## Current Status
- **Phase**: Feature branch `feature/multi-project-agent-metrics-local-inference` merged to master
- **Test Suite**: 1774 passed, 12 skipped, 0 failures, ~92.7% coverage
- **Branch**: master
- **Latest commit**: merge commit (feature/multi-project-agent-metrics-local-inference into master)

## Sprint0 Objectives (ALL COMPLETE)
obj01-obj16 all complete.

## Hot-Reload System (COMPLETE)
- `src/general_ludd/events/bus.py` — EventBus (pub/sub, wildcard, history, async)
- `src/general_ludd/events/hooks.py` — HookSystem (callback + webhook, priority, retry)
- `src/general_ludd/events/types.py` — 14 event types (StrEnum)
- `src/general_ludd/reload/hot_reloader.py` — HotReloader with ReloadScope
- `src/general_ludd/reload/worker_broadcast.py` — WorkerBroadcaster
- 14 daemon admin endpoints for reload/models/templates/playbooks/hooks/workers
- 82 e2e tests

## Agent Metrics (COMPLETE)
- `src/general_ludd/metrics/collector.py` — MetricsCollector, AgentMetrics, ModelUsage, CostEstimate
- Daemon endpoints: `GET /admin/agents`, `GET /admin/agents/{id}`, `GET /admin/metrics/cost`, `GET /admin/metrics/report`
- 50 unit tests

## Multi-Project Allocation (COMPLETE)
- `src/general_ludd/projects/manager.py` — ProjectManager, ProjectWeight (weighted allocation, rebalance)
- Daemon endpoints: `POST/DELETE/PUT /admin/projects`, `POST /admin/projects/rebalance`, `GET /admin/projects`
- 44 unit tests

## Compute Utilization Maximizer (COMPLETE)
- `src/general_ludd/infra/utilization.py` — UtilizationTracker, ComputeEndpoint (least-utilized routing, cache-aware)
- Daemon endpoints: `GET /admin/compute/utilization`, `GET /admin/compute/endpoints`
- 49 unit tests

## HuggingFace Model Registry (COMPLETE)
- `src/general_ludd/models/model_registry.py` — ModelRegistry wraps huggingface_hub.HfApi
- Daemon endpoints: `POST /admin/models/search`, `GET /admin/models/downloaded`
- 18 unit tests

## Local Inference Manager (COMPLETE)
- `src/general_ludd/infra/local_inference.py` — LocalInferenceManager (vllm + llamacpp)
- 33 unit tests

## Anti-Stop Bug Fix (COMPLETE)
- `.opencode/plugin/enforce-make.ts` — forbidden stop patterns
- `AGENTS.md` — Anti-Stop Patterns section

## Rename (COMPLETE)
- `agentic_harness` → `general_ludd`, `hottentot` → `gludd`, all paths updated

## Other Completed
- ansible-core library refactor (CoreAnsibleRunner)
- BinaryPathConfig + BinaryPathResolver
- DeploymentManager (terraform lifecycle)
- Security: SAST (bandit), SBOM (cyclonedx-py), pip-audit, OPA/Rego
- Unified CLI (`gludd` binary)
- PyInstaller spec + tarball installer
- Config docs (`docs/model-setup.md`)
- EventLoop subscribes to EventBus config reload events

## Key Gaps (Known)
- ReturnReviewer._call_model() is a stub (no real LLM calls in tests)
- Skills body field not injected into prompts
- PID rules engine and rules evaluation are stubs
- OpenBao not wired into worker/runner pipeline
- No DB migration for plan_artifact column on TodoModel
- Local inference deps (llama-cpp-python, vllm) not in pyproject.toml — user must install separately
- `tool.uv.dev-dependencies` deprecation warning (cosmetic)

## Next Steps
1. Wire prompt_profile resolution into pipeline
2. Wire OpenBao into worker/runner pipeline
3. DB migration for plan_artifact column on TodoModel
4. Implement PID rules engine and rules evaluation
5. Real LLM call integration in ReturnReviewer
6. Wire CLI `gludd models search/download` subcommands
7. Wire CLI `gludd local-serve` subcommand
8. Add llama-cpp-python and vllm as optional dependencies in pyproject.toml
