"""Unified daemon — FastAPI app with embedded event loop and hot-reload admin endpoints."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_daemon_state: dict[str, Any] = {
    "todos": [],
    "tick_metrics": {},
}


class AddTodoRequest(BaseModel):
    title: str
    description: str = ""
    queue: str = "core"
    priority: str = "medium"
    work_type: str = "code"


class LogLevelRequest(BaseModel):
    level: str


class ReloadRequest(BaseModel):
    scope: str = "all"


class AddModelRequest(BaseModel):
    model_id: str
    provider: str = "openai"
    model: str = ""
    api_key_env: str | None = None
    api_base_alias: str | None = None


class RegisterHookRequest(BaseModel):
    event_name: str
    url: str
    headers: dict[str, str] | None = None
    retry_count: int = 1
    timeout_seconds: int = 10


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    tick_interval = app.state.tick_interval
    event_loop = None
    task = None

    try:
        from general_ludd.ansible.runner import AnsibleRunnerAdapter
        from general_ludd.event_loop.loop import EventLoop

        runner = AnsibleRunnerAdapter()
        event_loop = EventLoop(
            worker_base_url="http://localhost:8000",
            runner=runner,
        )
        app.state.event_loop = event_loop
        app.state.event_loop._runner = runner
        task = asyncio.create_task(event_loop.run_forever(interval=tick_interval))
    except Exception as exc:
        logger.warning("Could not start event loop: %s", exc)

    yield

    if event_loop is not None:
        event_loop.stop()
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _get_or_create_subsystems(app: FastAPI) -> dict[str, Any]:
    from general_ludd.events.bus import EventBus
    from general_ludd.events.hooks import HookSystem
    from general_ludd.reload.worker_broadcast import WorkerBroadcaster

    if not hasattr(app.state, "_event_bus") or app.state._event_bus is None:
        app.state._event_bus = EventBus(history_size=100)
    if not hasattr(app.state, "_hook_system") or app.state._hook_system is None:
        app.state._hook_system = HookSystem(event_bus=app.state._event_bus)
    if not hasattr(app.state, "_worker_broadcaster") or app.state._worker_broadcaster is None:
        app.state._worker_broadcaster = WorkerBroadcaster()
    return {
        "bus": app.state._event_bus,
        "hooks": app.state._hook_system,
        "broadcaster": app.state._worker_broadcaster,
    }


def create_daemon_app(
    tick_interval: float = 1.0,
    log_level: str = "info",
    config_dir: str | None = None,
    templates_dir: str | None = None,
    playbooks_dir: str | None = None,
) -> FastAPI:
    app = FastAPI(title="General Ludd Agent", version="0.1.0", lifespan=_lifespan)
    app.state.tick_interval = tick_interval
    app.state.event_loop = None
    app.state.log_level = log_level
    app.state._event_bus = None
    app.state._hook_system = None
    app.state._worker_broadcaster = None
    app.state._config_dir = config_dir
    app.state._templates_dir = templates_dir
    app.state._playbooks_dir = playbooks_dir

    if log_level == "debug":
        logging.getLogger("httpx").setLevel(logging.DEBUG)
        logging.getLogger("httpcore").setLevel(logging.DEBUG)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "healthy"}

    @app.post("/api/todos", status_code=201)
    async def api_add_todo(req: AddTodoRequest) -> dict[str, Any]:
        todo_id = f"TODO-{uuid.uuid4().hex[:8].upper()}"
        todo: dict[str, Any] = {
            "todo_id": todo_id,
            "title": req.title,
            "description": req.description,
            "queue": req.queue,
            "priority": req.priority,
            "work_type": req.work_type,
            "status": "queued",
        }
        _daemon_state["todos"].append(todo)
        return todo

    @app.get("/api/todos")
    async def api_list_todos(queue: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        results = list(_daemon_state["todos"])
        if queue is not None:
            results = [t for t in results if t.get("queue") == queue]
        if status is not None:
            results = [t for t in results if t.get("status") == status]
        return results

    @app.get("/api/todos/{todo_id}")
    async def api_get_todo(todo_id: str) -> dict[str, Any]:
        for todo in _daemon_state["todos"]:
            if str(todo.get("todo_id", "")) == todo_id:
                return dict(todo)
        raise HTTPException(status_code=404, detail="Todo not found")

    @app.get("/api/status")
    async def api_status() -> dict[str, Any]:
        queue_depths: dict[str, int] = {}
        for todo in _daemon_state["todos"]:
            q = todo.get("queue", "unknown")
            queue_depths[q] = queue_depths.get(q, 0) + 1
        return {
            "queue_depths": queue_depths,
            "tick_metrics": _daemon_state["tick_metrics"],
        }

    @app.get("/api/deployments")
    async def api_deployments() -> list[dict[str, Any]]:
        return []

    @app.post("/admin/log-level")
    async def admin_log_level(req: LogLevelRequest) -> dict[str, str]:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        level_upper = req.level.upper()
        if level_upper not in valid_levels:
            raise HTTPException(status_code=422, detail=f"Invalid log level: {req.level}")
        logging.getLogger().setLevel(level_upper)
        return {"status": "ok", "level": req.level}

    @app.post("/admin/reload")
    async def admin_reload(req: ReloadRequest) -> dict[str, Any]:
        from general_ludd.reload.hot_reloader import HotReloader, ReloadScope

        subsys = _get_or_create_subsystems(app)
        reloader = HotReloader(
            config_dir=app.state._config_dir or "/tmp/gl-config",
            event_bus=subsys["bus"],
            hook_system=subsys["hooks"],
            worker_broadcaster=subsys["broadcaster"],
            templates_dir=app.state._templates_dir,
            playbooks_dir=app.state._playbooks_dir,
        )
        scope = ReloadScope(req.scope)
        result = reloader.reload(scope)
        return {"success": result.success, "scope": result.scope, "details": result.details, "error": result.error}

    @app.get("/admin/reload/status")
    async def admin_reload_status() -> dict[str, Any]:
        subsys = _get_or_create_subsystems(app)
        history = subsys["bus"].get_history()
        recent = [
            {"type": e.type, "payload": e.payload, "timestamp": e.timestamp}
            for e in history[-20:]
        ]
        return {"recent_events": recent, "total_events": len(history)}

    @app.post("/admin/models")
    async def admin_add_model(req: AddModelRequest) -> dict[str, Any]:
        from general_ludd.models.gateway import ModelGateway
        from general_ludd.models.provider_registry import ProviderRegistry
        from general_ludd.models.router import ModelRouter

        subsys = _get_or_create_subsystems(app)
        if not hasattr(app.state, "_model_gateway") or app.state._model_gateway is None:
            app.state._model_gateway = ModelGateway(
                provider_registry=ProviderRegistry(),
                router=ModelRouter(),
                event_bus=subsys["bus"],
                hook_system=subsys["hooks"],
                worker_broadcaster=subsys["broadcaster"],
            )
        gateway: ModelGateway = app.state._model_gateway
        profile = gateway.add_profile(
            model_id=req.model_id,
            provider=req.provider,
            model=req.model,
            api_key_env=req.api_key_env,
            api_base_alias=req.api_base_alias,
        )
        return {"model_id": req.model_id, "profile": profile.model_dump()}

    @app.delete("/admin/models/{model_id}")
    async def admin_remove_model(model_id: str) -> dict[str, Any]:
        if hasattr(app.state, "_model_gateway") and app.state._model_gateway is not None:
            app.state._model_gateway.remove_profile(model_id)
        return {"removed": model_id}

    @app.get("/admin/models")
    async def admin_list_models() -> dict[str, Any]:
        if hasattr(app.state, "_model_gateway") and app.state._model_gateway is not None:
            profiles = app.state._model_gateway.list_profiles()
            return {"profiles": [p.model_dump() for p in profiles]}
        return {"profiles": []}

    @app.post("/admin/templates/refresh")
    async def admin_templates_refresh() -> dict[str, Any]:
        from general_ludd.prompts.registry import PromptRegistry

        subsys = _get_or_create_subsystems(app)
        if not hasattr(app.state, "_prompt_registry") or app.state._prompt_registry is None:
            app.state._prompt_registry = PromptRegistry(
                template_dir=app.state._templates_dir,
                event_bus=subsys["bus"],
            )
        result = app.state._prompt_registry.refresh()
        return {"success": True, "templates": result.get("templates", [])}

    @app.get("/admin/templates")
    async def admin_list_templates() -> dict[str, Any]:
        if hasattr(app.state, "_prompt_registry") and app.state._prompt_registry is not None:
            return {"templates": app.state._prompt_registry.list_templates()}
        return {"templates": []}

    @app.post("/admin/playbooks/refresh")
    async def admin_playbooks_refresh() -> dict[str, Any]:
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        subsys = _get_or_create_subsystems(app)
        if not hasattr(app.state, "_runner") or app.state._runner is None:
            app.state._runner = AnsibleRunnerAdapter(
                playbooks_dir=app.state._playbooks_dir,
                event_bus=subsys["bus"],
            )
        result = app.state._runner.refresh_playbooks()
        return {"success": True, "playbooks": result.get("playbooks", [])}

    @app.get("/admin/playbooks")
    async def admin_list_playbooks() -> dict[str, Any]:
        if hasattr(app.state, "_runner") and app.state._runner is not None:
            return {"playbooks": app.state._runner.list_playbooks()}
        return {"playbooks": []}

    @app.get("/admin/hooks")
    async def admin_list_hooks() -> dict[str, Any]:
        subsys = _get_or_create_subsystems(app)
        hooks = subsys["hooks"].list_hooks()
        return {
            "hooks": [
                {
                    "hook_id": h.hook_id,
                    "event_name": h.event_name,
                    "hook_type": h.hook_type,
                    "url": h.webhook_config.url if h.webhook_config else None,
                    "priority": h.priority,
                }
                for h in hooks
            ]
        }

    @app.post("/admin/hooks")
    async def admin_register_hook(req: RegisterHookRequest) -> dict[str, Any]:
        subsys = _get_or_create_subsystems(app)
        hook_id = subsys["hooks"].register_webhook(
            event_name=req.event_name,
            url=req.url,
            headers=req.headers,
            retry_count=req.retry_count,
            timeout_seconds=req.timeout_seconds,
        )
        return {"hook_id": hook_id, "event_name": req.event_name}

    @app.delete("/admin/hooks/{hook_id}")
    async def admin_delete_hook(hook_id: str) -> dict[str, Any]:
        subsys = _get_or_create_subsystems(app)
        subsys["hooks"].unregister(hook_id)
        return {"removed": hook_id}

    @app.post("/admin/workers/ping")
    async def admin_workers_ping() -> dict[str, Any]:
        subsys = _get_or_create_subsystems(app)
        results = subsys["broadcaster"].ping_all()
        return {"workers": results}

    @app.get("/admin/workers")
    async def admin_list_workers() -> dict[str, Any]:
        subsys = _get_or_create_subsystems(app)
        workers = subsys["broadcaster"].list_workers()
        return {
            "workers": [
                {
                    "worker_id": w.worker_id,
                    "address": w.address,
                    "last_seen": w.last_seen,
                }
                for w in workers
            ]
        }

    return app
