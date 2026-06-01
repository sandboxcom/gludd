# Session State

> This file is maintained automatically. Update it at session start to restore context.

## Last Updated
- 2026-05-31

## Current Status
- **Phase**: Hot-reload and hook system complete
- **Test Suite**: 1498 passed, 12 skipped, 0 failures, 92.16% coverage
- **Branch**: master (feature/hot-reload-hooks merged)
- **Latest commit**: `894dc7d` (merge)

## Sprint0 Objectives (ALL COMPLETE)
obj01-obj16 all complete.

## Hot-Reload System (COMPLETE)

New modules:
- `src/general_ludd/events/bus.py` — EventBus (in-process pub/sub with wildcard support, history, async subscriber support)
- `src/general_ludd/events/hooks.py` — HookSystem (callback + webhook registration with priorities, retries, custom headers)
- `src/general_ludd/events/types.py` — 14 event types: ModelAdded, ModelRemoved, ConfigReloaded, TemplateUpdated, PlaybookRegistered/Removed, SkillUpdated, ReloadRequested/Completed/Failed, WorkerPing/Pong, HookTriggered, Custom
- `src/general_ludd/reload/hot_reloader.py` — HotReloader orchestrates reload by scope (models, templates, playbooks, skills, config, all) with event publishing, hook firing, and worker broadcast
- `src/general_ludd/reload/worker_broadcast.py` — WorkerBroadcaster (register/unregister/heartbeat/stale-cleanup, broadcast reload and model updates to all workers via HTTP)

Updated modules:
- `src/general_ludd/daemon.py` — 14 new admin endpoints for reload, models, templates, playbooks, hooks, workers
- `src/general_ludd/models/gateway.py` — `add_profile()`, `remove_profile()` with event/hook/broadcast integration
- `src/general_ludd/models/router.py` — `set_role_routing()` for dynamic route updates
- `src/general_ludd/prompts/registry.py` — `refresh()` re-reads templates from disk, preserves in-memory templates
- `src/general_ludd/ansible/runner.py` — `refresh_playbooks()`, `register_playbook()`, `unregister_playbook()`, `list_playbooks()`, `event_bus` support
- `src/general_ludd/skills/registry.py` — `refresh()` with search_paths
- `src/general_ludd/worker/gunicorn_conf.py` — `on_reload`, `post_fork`, `pre_exec` hooks, `max_requests` + `max_requests_jitter`

Daemon admin endpoints:
- `POST /admin/reload` — trigger reload by scope
- `GET /admin/reload/status` — recent event history
- `POST /admin/models` — add model profile
- `DELETE /admin/models/{id}` — remove model profile
- `GET /admin/models` — list profiles
- `POST /admin/templates/refresh` — re-read templates from disk
- `GET /admin/templates` — list templates
- `POST /admin/playbooks/refresh` — re-read playbooks from disk
- `GET /admin/playbooks` — list playbooks
- `GET /admin/hooks` — list registered hooks
- `POST /admin/hooks` — register webhook
- `DELETE /admin/hooks/{id}` — remove hook
- `POST /admin/workers/ping` — ping all workers
- `GET /admin/workers` — list registered workers

82 new e2e tests covering: EventBus (14), HookSystem (14), HotReloader (8), WorkerBroadcaster (9), ModelGateway dynamic (6), PromptRegistry dynamic (5), AnsibleRunner dynamic (5), SkillRegistry dynamic (2), Daemon endpoints (15), Full integration (4).

## Anti-Stop Bug Fix
Fixed the bug that allowed the agent to stop and ask "should I continue?" when tasks were pending:
- `.opencode/plugin/enforce-make.ts` — Added explicit forbidden stop patterns to TASK_COMPLETION_WARNING and system prompt injection
- `AGENTS.md` — Added "Anti-Stop Patterns" section with specific examples

## Rename Completed
- `agentic_harness` → `general_ludd`
- `hottentot-agent` → `general-ludd-agent`
- `hottentot` → `gludd`
- `~/.config/hottentot/` → `~/.config/general-ludd/`

## Key Gaps (Known)
- ReturnReviewer._call_model() is a stub (no real LLM calls in tests)
- Skills body field not injected into prompts
- Event loop phases 4 (PID) and 5 (rules) are stubs
- OpenBao not wired into worker/runner pipeline
- No DB migration for plan_artifact column on TodoModel
- EventLoop doesn't subscribe to EventBus for config updates

## Next Steps
1. Wire EventLoop to subscribe to EventBus config reload events
2. Wire prompt_profile resolution into pipeline
3. Wire OpenBao into worker/runner pipeline
4. DB migration for plan_artifact column
5. Implement PID rules engine and rules evaluation
6. Real LLM call integration in ReturnReviewer
