"""Unified daemon — FastAPI app with embedded event loop and hot-reload admin endpoints."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from general_ludd.ansible.isolation import ProcessIsolationConfig
from general_ludd.config.binary_paths import BinaryPaths
from general_ludd.config.loader import load_user_config
from general_ludd.config.model_routing import ModelRoutingConfig, load_model_routing
from general_ludd.config.user_config import UserConfig
from general_ludd.secrets.config import OpenBaoConfig

logger = logging.getLogger(__name__)

_daemon_state: dict[str, Any] = {
    "todos": [],
    "tick_metrics": {},
}


def load_startup_config(config_dir: str | None = None) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "model_routing": ModelRoutingConfig(),
        "user_config": UserConfig(),
        "binary_paths": None,
        "openbao_config": None,
        "process_isolation": None,
        "mcp_servers": {},
        "task_definitions": [],
        "model_profiles": [],
    }
    if config_dir is None:
        return cfg
    cdir = Path(config_dir)
    if not cdir.is_dir():
        return cfg

    mr_path = cdir / "model_routing.yml"
    if mr_path.exists():
        cfg["model_routing"] = load_model_routing(mr_path)

    gl_path = cdir / "general-ludd.yml"
    if gl_path.exists():
        with open(gl_path) as f:
            data = yaml.safe_load(f) or {}
        cfg["user_config"] = UserConfig(**data)
        if cfg["user_config"].model_routing is None and cfg["model_routing"].default_profile is None:
            mr_data = data.get("model_routing")
            if mr_data:
                cfg["model_routing"] = ModelRoutingConfig(**mr_data)
    else:
        cfg["user_config"] = load_user_config()

    bp_path = cdir / "binary_paths.yml"
    if bp_path.exists():
        with open(bp_path) as f:
            data = yaml.safe_load(f) or {}
        bp_data = data.get("binary_paths", {})
        cfg["binary_paths"] = BinaryPaths(**bp_data) if bp_data else None

    ob_path = cdir / "openbao" / "default.yml"
    if ob_path.exists():
        with open(ob_path) as f:
            data = yaml.safe_load(f) or {}
        cfg["openbao_config"] = OpenBaoConfig(**data)

    iso_path = cdir / "ansible" / "isolation.yml"
    if iso_path.exists():
        with open(iso_path) as f:
            data = yaml.safe_load(f) or {}
        pi_data = data.get("process_isolation", {})
        cfg["process_isolation"] = ProcessIsolationConfig(**pi_data) if pi_data else None

    mcp_path = cdir / "mcp_servers" / "example.yml"
    if mcp_path.exists():
        from general_ludd.mcp.loader import load_mcp_config
        cfg["mcp_servers"] = load_mcp_config(str(mcp_path))

    tasks_dir = cdir / "tasks"
    if tasks_dir.is_dir():
        from general_ludd.config.task_loader import discover_task_definitions
        cfg["task_definitions"] = discover_task_definitions(str(tasks_dir))

    profiles_dir = cdir / "model_profiles"
    if profiles_dir.is_dir():
        cfg["model_profiles"] = load_model_profiles(profiles_dir=str(profiles_dir))

    return cfg


def build_secrets_resolver(
    openbao_config: OpenBaoConfig | None = None,
    env_overrides: dict[str, str] | None = None,
) -> Any:
    from general_ludd.secrets.env import EnvSecretsManager

    if openbao_config is not None and openbao_config.mode not in ("disabled", None):
        try:
            from general_ludd.secrets.manager import SecretsManager

            mgr = SecretsManager(config=openbao_config)
            if openbao_config.mode == "external" and openbao_config.external_url:
                mgr.connect()
                logger.info("OpenBao secrets backend connected: %s", openbao_config.external_url)
                return mgr
            logger.warning("OpenBao not reachable, falling back to env var secrets")
        except Exception as exc:
            logger.warning("OpenBao init failed (%s), falling back to env var secrets", exc)

    return EnvSecretsManager(overrides=env_overrides)


def load_model_profiles(profiles_dir: str | None = None) -> list[Any]:
    from general_ludd.models.gateway import ModelProfile

    if profiles_dir is None:
        return []
    pdir = Path(profiles_dir)
    if not pdir.is_dir():
        return []
    profiles: list[ModelProfile] = []
    for yml_file in sorted(pdir.glob("*.yml")):
        if yml_file.name.startswith("_"):
            continue
        try:
            with open(yml_file) as f:
                data = yaml.safe_load(f) or {}
            if data.get("enabled", True) is False:
                continue
            profiles.append(ModelProfile(**data))
        except Exception as exc:
            logger.warning("Skipping model profile %s: %s", yml_file.name, exc)
    return profiles


class AddTodoRequest(BaseModel):
    title: str
    description: str = ""
    queue: str = "core"
    priority: str = "medium"
    work_type: str = "code"
    project_id: str | None = None


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


class AddProjectRequest(BaseModel):
    name: str
    weight: float
    description: str = ""


class SetWeightRequest(BaseModel):
    weight: float


class RebalanceRequest(BaseModel):
    weights: dict[str, float]


class ModelSearchRequest(BaseModel):
    query: str = ""
    limit: int = 20


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    tick_interval = app.state.tick_interval
    event_loop = None
    task = None
    engine = None

    try:
        from general_ludd.ansible.runner import AnsibleRunnerAdapter
        from general_ludd.db.session import (
            create_async_session_factory,
            ensure_tables,
            init_engine_from_config,
            seed_initial_queues,
        )
        from general_ludd.event_loop.loop import EventLoop

        startup_config = getattr(app.state, "_startup_config", {}) or {}
        db_config: dict[str, Any] = {}
        uc = startup_config.get("user_config")
        if uc and hasattr(uc, "database"):
            db_config = uc.database or {}
        engine = init_engine_from_config(db_config)
        await ensure_tables(engine)

        session_factory = create_async_session_factory(engine)
        async with session_factory() as session:
            await seed_initial_queues(session)
            await session.commit()

        runner = AnsibleRunnerAdapter()
        subsys = _get_or_create_subsystems(app)
        ext = _get_or_create_extended_subsystems(app, session_factory=session_factory)

        secrets_resolver = build_secrets_resolver(
            openbao_config=startup_config.get("openbao_config"),
        )
        app.state._secrets_resolver = secrets_resolver

        model_profiles = startup_config.get("model_profiles", [])
        if model_profiles and hasattr(secrets_resolver, "write_secret"):
            try:
                from general_ludd.secrets.migration import migrate_profile_secrets

                profile_dicts = [
                    p.model_dump() if hasattr(p, "model_dump") else p
                    for p in model_profiles
                ]
                result = migrate_profile_secrets(secrets_resolver, profile_dicts)
                logger.info(
                    "Secret migration: %d migrated, %d skipped",
                    result["migrated"],
                    len(result["skipped"]),
                )
            except Exception as exc:
                logger.warning("Secret migration failed: %s", exc)

        event_loop = EventLoop(
            worker_base_url="http://localhost:8000",
            runner=runner,
            session=session_factory,
            http_client=None,
            todo_repo=None,
            task_return_repo=None,
            budget_guard=None,
            mcp_client=None,
            mcp_tool_registry=None,
            event_bus=subsys["bus"],
            project_manager=ext["projects"],
            skill_registry=ext["skill_registry"],
            config={
                "default_playbook": "noop.yml",
                "model_profiles": startup_config.get("model_profiles", []),
                "rules": startup_config.get("rules", []),
            },
            adaptive_router=ext["adaptive_router"],
        )
        app.state.event_loop = event_loop
        app.state.event_loop._runner = runner
        app.state._db_engine = engine
        app.state._session_factory = session_factory
        task = asyncio.create_task(event_loop.run_forever(interval=tick_interval))
        logger.info("Daemon started: db=%s event_loop=running", engine.url)
    except Exception as exc:
        logger.warning("Could not start event loop: %s", exc)

    yield

    if event_loop is not None:
        event_loop.stop()
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    if engine is not None:
        await engine.dispose()


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


def _get_or_create_extended_subsystems(
    app: FastAPI,
    session_factory: Any | None = None,
) -> dict[str, Any]:
    from general_ludd.infra.utilization import UtilizationTracker
    from general_ludd.metrics.collector import MetricsCollector
    from general_ludd.models.model_registry import ModelRegistry
    from general_ludd.projects.manager import ProjectManager
    from general_ludd.skills.loader import discover_skills
    from general_ludd.skills.registry import SkillRegistry

    if not hasattr(app.state, "_metrics_collector") or app.state._metrics_collector is None:
        app.state._metrics_collector = MetricsCollector()
    if not hasattr(app.state, "_project_manager") or app.state._project_manager is None:
        app.state._project_manager = ProjectManager()
    if not hasattr(app.state, "_utilization_tracker") or app.state._utilization_tracker is None:
        app.state._utilization_tracker = UtilizationTracker()
    if not hasattr(app.state, "_model_registry") or app.state._model_registry is None:
        app.state._model_registry = ModelRegistry()
    if not hasattr(app.state, "_skill_registry") or app.state._skill_registry is None:
        registry = SkillRegistry()
        config_dir = getattr(app.state, "_config_dir", None)
        if config_dir:
            discovered = discover_skills(config_dir)
            for skill in discovered:
                registry.register(skill)
        app.state._skill_registry = registry

    adaptive_router = None
    if session_factory is not None and not hasattr(app.state, "_adaptive_router"):
        from general_ludd.db.repository import BenchmarkRepository
        from general_ludd.scoring.router import AdaptiveRouter

        benchmark_repo = BenchmarkRepository(session_factory)  # type: ignore[arg-type]
        adaptive_router = AdaptiveRouter(benchmark_repo=benchmark_repo)
        app.state._adaptive_router = adaptive_router
    elif session_factory is not None and hasattr(app.state, "_adaptive_router"):
        adaptive_router = app.state._adaptive_router

    return {
        "metrics": app.state._metrics_collector,
        "projects": app.state._project_manager,
        "utilization": app.state._utilization_tracker,
        "model_registry": app.state._model_registry,
        "skill_registry": app.state._skill_registry,
        "adaptive_router": adaptive_router,
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
    app.state._metrics_collector = None
    app.state._project_manager = None
    app.state._utilization_tracker = None
    app.state._model_registry = None
    app.state._skill_registry = None
    app.state._adaptive_router = None
    app.state._startup_config = load_startup_config(config_dir)

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
            "project_id": req.project_id,
        }
        _daemon_state["todos"].append(todo)
        return todo

    @app.get("/api/todos")
    async def api_list_todos(
        queue: str | None = None,
        status: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        results = list(_daemon_state["todos"])
        if queue is not None:
            results = [t for t in results if t.get("queue") == queue]
        if status is not None:
            results = [t for t in results if t.get("status") == status]
        if project_id is not None:
            results = [t for t in results if t.get("project_id") == project_id]
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

    @app.get("/admin/agents")
    async def admin_list_agents() -> dict[str, Any]:
        ext = _get_or_create_extended_subsystems(app)
        agents = ext["metrics"].list_agents()
        return {
            "agents": [
                {
                    "agent_id": a.agent_id,
                    "agent_name": a.agent_name,
                    "status": a.status,
                    "project": a.project,
                    "uptime_seconds": a.uptime_seconds,
                    "total_tokens": a.total_tokens,
                    "total_cost_usd": a.total_cost_usd,
                    "models_used": {
                        mid: {
                            "total_calls": u.total_calls,
                            "successful_calls": u.successful_calls,
                            "success_rate": u.success_rate,
                            "cost_usd": u.total_cost_usd,
                        }
                        for mid, u in a.model_usage.items()
                    },
                }
                for a in agents
            ]
        }

    @app.get("/admin/agents/{agent_id}")
    async def admin_get_agent(agent_id: str) -> dict[str, Any]:
        ext = _get_or_create_extended_subsystems(app)
        summary = ext["metrics"].get_agent_summary(agent_id)
        if not summary:
            raise HTTPException(status_code=404, detail="Agent not found")
        return cast(dict[str, Any], summary)

    @app.get("/admin/metrics/cost")
    async def admin_metrics_cost(
        subscription_name: str = "",
        subscription_cost_per_month: float = 0.0,
        tokens_per_week: int = 0,
    ) -> dict[str, Any]:
        ext = _get_or_create_extended_subsystems(app)
        estimate = ext["metrics"].get_cost_estimate(
            subscription_name=subscription_name,
            subscription_cost_usd_per_month=subscription_cost_per_month,
            tokens_per_week=tokens_per_week,
        )
        return {
            "total_cost_usd": estimate.total_cost_usd,
            "subscription_name": estimate.subscription_name,
            "subscription_cost_usd_per_month": estimate.subscription_cost_usd_per_month,
            "tokens_per_week": estimate.tokens_per_week,
            "tokens_used": estimate.tokens_used,
            "cost_as_pct_of_subscription": estimate.cost_as_pct_of_subscription,
            "tokens_as_pct_of_weekly": estimate.tokens_as_pct_of_weekly,
            "tokens_remaining_this_week": estimate.tokens_remaining_this_week,
        }

    @app.get("/admin/metrics/report")
    async def admin_metrics_report() -> dict[str, Any]:
        ext = _get_or_create_extended_subsystems(app)
        return cast(dict[str, Any], ext["metrics"].get_full_report())

    @app.post("/admin/projects")
    async def admin_add_project(req: AddProjectRequest) -> dict[str, Any]:
        ext = _get_or_create_extended_subsystems(app)
        try:
            project = ext["projects"].add_project(
                name=req.name, weight=req.weight, description=req.description
            )
            return {
                "project_id": project.project_id,
                "name": project.name,
                "weight": project.weight,
                "description": project.description,
                "active": project.active,
            }
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/admin/projects/{project_id}")
    async def admin_delete_project(project_id: str) -> dict[str, Any]:
        ext = _get_or_create_extended_subsystems(app)
        ext["projects"].remove_project(project_id)
        return {"removed": project_id}

    @app.put("/admin/projects/{project_id}/weight")
    async def admin_set_project_weight(project_id: str, req: SetWeightRequest) -> dict[str, Any]:
        ext = _get_or_create_extended_subsystems(app)
        try:
            ext["projects"].set_weight(project_id, req.weight)
            project = ext["projects"].get_project(project_id)
            return {"project_id": project_id, "weight": project.weight if project else req.weight}
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/admin/projects/rebalance")
    async def admin_rebalance_projects(req: RebalanceRequest) -> dict[str, Any]:
        ext = _get_or_create_extended_subsystems(app)
        try:
            ext["projects"].rebalance(req.weights)
            return {"rebalanced": list(req.weights.keys())}
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/admin/projects")
    async def admin_list_projects() -> dict[str, Any]:
        ext = _get_or_create_extended_subsystems(app)
        return cast(dict[str, Any], ext["projects"].get_summary())

    @app.get("/admin/compute/utilization")
    async def admin_compute_utilization() -> dict[str, Any]:
        ext = _get_or_create_extended_subsystems(app)
        return cast(dict[str, Any], ext["utilization"].get_utilization_report())

    @app.get("/admin/compute/endpoints")
    async def admin_compute_endpoints() -> dict[str, Any]:
        ext = _get_or_create_extended_subsystems(app)
        endpoints = ext["utilization"].list_endpoints()
        return {
            "endpoints": [
                {
                    "endpoint_id": e.endpoint_id,
                    "url": e.url,
                    "model": e.model,
                    "utilization_pct": e.utilization * 100,
                    "current_load": e.current_load,
                    "max_concurrent": e.max_concurrent,
                    "available_slots": e.available_slots,
                    "active": e.active,
                }
                for e in endpoints
            ]
        }

    @app.post("/admin/models/search")
    async def admin_models_search(req: ModelSearchRequest) -> dict[str, Any]:
        ext = _get_or_create_extended_subsystems(app)
        results = ext["model_registry"].search(query=req.query, limit=req.limit)
        return {
            "results": [
                {
                    "model_id": r.model_id,
                    "author": r.author,
                    "downloads": r.downloads,
                    "tags": r.tags,
                    "pipeline_tag": r.pipeline_tag,
                    "library_name": r.library_name,
                }
                for r in results
            ]
        }

    @app.get("/admin/models/downloaded")
    async def admin_models_downloaded() -> dict[str, Any]:
        ext = _get_or_create_extended_subsystems(app)
        models = ext["model_registry"].list_downloaded()
        return {
            "models": [
                {
                    "model_id": m.model_id,
                    "local_path": m.local_path,
                    "engine": m.engine,
                    "size_bytes": m.size_bytes,
                }
                for m in models
            ]
        }

    @app.post("/admin/local-inference/start")
    async def admin_local_inference_start(payload: dict[str, Any]) -> dict[str, Any]:
        from general_ludd.infra.local_inference import LocalInferenceManager, LocalServerConfig

        if not hasattr(app.state, "_local_inference") or app.state._local_inference is None:
            subsys = _get_or_create_subsystems(app)
            app.state._local_inference = LocalInferenceManager(event_bus=subsys["bus"])
        manager: LocalInferenceManager = app.state._local_inference
        config = LocalServerConfig(
            engine=payload.get("engine", "vllm"),
            model_path=payload.get("model_path", ""),
            model_name=payload.get("model_name", ""),
            host=payload.get("host", "localhost"),
            port=payload.get("port", 8001),
            gpu_layers=payload.get("gpu_layers", -1),
            context_size=payload.get("context_size", 4096),
        )
        server = manager.create_server(config)
        await manager.start_server(server.server_id)
        return {
            "server_id": server.server_id,
            "engine": config.engine,
            "model": config.model_path or config.model_name,
            "endpoint_url": server.endpoint_url,
            "status": server.status,
        }

    @app.post("/admin/mcp/catalog/search")
    async def admin_mcp_catalog_search(req: dict[str, Any]) -> dict[str, Any]:
        from general_ludd.mcp.catalog import MCPCatalog

        catalog = MCPCatalog()
        results = catalog.search(query=req.get("query", ""), limit=req.get("limit", 20))
        return {
            "results": [
                {
                    "server_name": r.server_name,
                    "display_name": r.display_name,
                    "description": r.description,
                    "source": r.source,
                    "command": r.command,
                    "env_aliases_needed": r.env_aliases_needed,
                    "tags": r.tags,
                    "downloads": r.downloads,
                }
                for r in results
            ]
        }

    @app.get("/admin/mcp/catalog/servers")
    async def admin_mcp_catalog_servers() -> dict[str, Any]:
        from general_ludd.mcp.catalog import MCPCatalog

        catalog = MCPCatalog()
        servers = catalog.get_known_servers()
        return {
            "servers": [
                {
                    "server_name": s.server_name,
                    "display_name": s.display_name,
                    "description": s.description,
                    "source": s.source,
                    "command": s.command,
                    "env_aliases_needed": s.env_aliases_needed,
                    "tags": s.tags,
                }
                for s in servers
            ]
        }

    @app.get("/admin/mcp/catalog/servers/{name}")
    async def admin_mcp_catalog_server(name: str) -> dict[str, Any]:
        from general_ludd.mcp.catalog import MCPCatalog

        catalog = MCPCatalog()
        server = catalog.get_server(name)
        if server is None:
            raise HTTPException(status_code=404, detail=f"MCP server {name} not found")
        return {
            "server": {
                "server_name": server.server_name,
                "display_name": server.display_name,
                "description": server.description,
                "source": server.source,
                "command": server.command,
                "env_aliases_needed": server.env_aliases_needed,
                "tags": server.tags,
            }
        }

    @app.post("/admin/skills/catalog/search")
    async def admin_skills_catalog_search(req: dict[str, Any]) -> dict[str, Any]:
        from general_ludd.skills.catalog import SkillCatalog

        catalog = SkillCatalog()
        results = catalog.search(
            query=req.get("query", ""),
            tags=req.get("tags"),
            category=req.get("category"),
            limit=req.get("limit", 20),
        )
        return {
            "results": [
                {
                    "name": r.name,
                    "description": r.description,
                    "source": r.source,
                    "tags": r.tags,
                    "category": r.category,
                }
                for r in results
            ]
        }

    @app.get("/admin/skills/catalog")
    async def admin_skills_catalog() -> dict[str, Any]:
        from general_ludd.skills.catalog import SkillCatalog

        catalog = SkillCatalog()
        results = catalog.search(limit=100)
        return {
            "skills": [
                {
                    "name": r.name,
                    "description": r.description,
                    "source": r.source,
                    "tags": r.tags,
                    "category": r.category,
                }
                for r in results
            ]
        }

    @app.post("/admin/skills/catalog/install")
    async def admin_skills_catalog_install(req: dict[str, Any]) -> dict[str, Any]:
        from general_ludd.skills.catalog import SkillCatalog

        catalog = SkillCatalog()
        name = req.get("name", "")
        config_dir = getattr(app.state, "_config_dir", None) or "/etc/general-ludd"
        path = catalog.install_skill(name, config_dir)
        if path is None:
            raise HTTPException(status_code=404, detail=f"Skill {name} not found")
        return {"installed": str(path), "name": name}

    @app.post("/admin/compute/endpoints")
    async def admin_register_compute_endpoint(req: dict[str, Any]) -> dict[str, Any]:
        ext = _get_or_create_extended_subsystems(app)
        endpoint_id = req.get("endpoint_id", "")
        url = req.get("url", "")
        if not endpoint_id or not url:
            raise HTTPException(status_code=422, detail="endpoint_id and url required")
        ep = ext["utilization"].register_endpoint(
            endpoint_id=endpoint_id,
            url=url,
            model=req.get("model", ""),
            gpu_type=req.get("gpu_type", ""),
            gpu_count=req.get("gpu_count", 1),
            max_concurrent=req.get("max_concurrent", 4),
        )
        return {"endpoint_id": ep.endpoint_id, "url": ep.url, "model": ep.model}

    @app.delete("/admin/compute/endpoints/{endpoint_id}")
    async def admin_unregister_compute_endpoint(endpoint_id: str) -> dict[str, Any]:
        ext = _get_or_create_extended_subsystems(app)
        ext["utilization"].unregister_endpoint(endpoint_id)
        return {"removed": endpoint_id}

    @app.get("/admin/benchmark/scores")
    async def admin_benchmark_scores(
        task_type: str | None = None,
    ) -> dict[str, Any]:
        from general_ludd.db.repository import BenchmarkRepository

        session = getattr(app.state, "_session", None)
        if session is None:
            return {"scores": []}
        repo = BenchmarkRepository(session)
        scores = await repo.get_aggregate_scores(task_type=task_type)
        return {"scores": [dict(s) for s in scores]}

    @app.get("/admin/benchmark/recent")
    async def admin_benchmark_recent(limit: int = 50) -> dict[str, Any]:
        from general_ludd.db.repository import BenchmarkRepository

        session = getattr(app.state, "_session", None)
        if session is None:
            return {"results": []}
        repo = BenchmarkRepository(session)
        results = await repo.list_recent(limit=limit)
        return {
            "results": [
                {
                    "id": r.id,
                    "prompt_profile_id": r.prompt_profile_id,
                    "model_profile_id": r.model_profile_id,
                    "task_type": r.task_type,
                    "completion_score": r.completion_score,
                    "code_quality_score": r.code_quality_score,
                    "instruction_adherence_score": r.instruction_adherence_score,
                    "token_efficiency_score": r.token_efficiency_score,
                    "success": r.success,
                    "cost_usd": r.cost_usd,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in results
            ]
        }

    @app.get("/admin/benchmark/leaderboard")
    async def admin_benchmark_leaderboard(
        task_type: str | None = None,
    ) -> dict[str, Any]:
        from general_ludd.db.repository import BenchmarkRepository
        from general_ludd.schemas.benchmark import TaskType
        from general_ludd.scoring.router import AdaptiveRouter

        session = getattr(app.state, "_session", None)
        if session is None:
            return {"leaderboard": []}
        repo = BenchmarkRepository(session)
        router = AdaptiveRouter(benchmark_repo=repo)
        tt = TaskType(task_type) if task_type else None
        lb = await router.get_leaderboard(task_type=tt)
        return {
            "leaderboard": [
                {
                    "prompt_profile_id": c.prompt_profile_id,
                    "model_profile_id": c.model_profile_id,
                    "composite_score": c.composite_score,
                    "avg_cost_usd": c.avg_cost_usd,
                    "sample_count": c.sample_count,
                    "task_type": c.task_type.value,
                }
                for c in lb
            ]
        }

    @app.post("/admin/benchmark/record")
    async def admin_benchmark_record(req: dict[str, Any]) -> dict[str, Any]:
        from general_ludd.db.repository import BenchmarkRepository

        session = getattr(app.state, "_session", None)
        if session is None:
            raise HTTPException(status_code=503, detail="No database session")
        repo = BenchmarkRepository(session)
        row = await repo.record_result(
            model_profile_id=req.get("model_profile_id", ""),
            task_type=req.get("task_type", "feature"),
            scores=req.get("scores", {}),
            success=req.get("success", True),
            prompt_profile_id=req.get("prompt_profile_id"),
            task_description=req.get("task_description", ""),
            time_seconds=req.get("time_seconds", 0.0),
            input_tokens=req.get("input_tokens", 0),
            output_tokens=req.get("output_tokens", 0),
            cost_usd=req.get("cost_usd", 0.0),
            error_message=req.get("error_message", ""),
            raw_output=req.get("raw_output", ""),
        )
        return {"id": row.id, "success": row.success}

    @app.get("/admin/prompt-profiles")
    async def admin_prompt_profiles() -> dict[str, Any]:
        from general_ludd.db.repository import PromptProfileRepository

        session = getattr(app.state, "_session", None)
        if session is None:
            return {"profiles": []}
        repo = PromptProfileRepository(session)
        profiles = await repo.list_all()
        return {
            "profiles": [
                {
                    "id": p.id,
                    "name": p.name,
                    "source": p.source,
                    "source_url": p.source_url,
                    "version": p.version,
                }
                for p in profiles
            ]
        }

    return app
