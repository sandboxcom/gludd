"""Unified daemon — FastAPI app with embedded event loop and hot-reload admin endpoints."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from pydantic import BaseModel, Field

from general_ludd.agents.context import ContextCompactor  # noqa: F401
from general_ludd.agents.dispatcher import AgentDispatcher  # noqa: F401
from general_ludd.agents.token_window import TokenWindowManager  # noqa: F401
from general_ludd.agents.tool_adapter import AgentToolAdapter  # noqa: F401
from general_ludd.ansible.isolation import ProcessIsolationConfig
from general_ludd.ansible.runner import AnsibleRunnerAdapter
from general_ludd.ansible.templating import AnsibleTemplater  # noqa: F401
from general_ludd.code_intelligence.git_intel import GitIntelligence  # noqa: F401
from general_ludd.config.binary_paths import BinaryPaths
from general_ludd.config.loader import load_user_config
from general_ludd.config.model_routing import ModelRoutingConfig, load_model_routing
from general_ludd.config.task_loader import discover_task_definitions
from general_ludd.config.user_config import UserConfig
from general_ludd.controllers.budget import RunBudgetGuard  # noqa: F401
from general_ludd.controllers.pid import BudgetController  # noqa: F401
from general_ludd.db.models import AuditEventType  # noqa: F401
from general_ludd.db.repository import BenchmarkRepository, ProjectRepository, QueueRepository  # noqa: F401
from general_ludd.db.session import (
    create_async_session_factory,
    ensure_tables,
    init_engine_from_config,
    seed_initial_queues,
)
from general_ludd.dependency.manager import DependencyManager  # noqa: F401
from general_ludd.dogfood.runner import DogfoodRunner  # noqa: F401
from general_ludd.dogfood.validator import DogfoodValidator  # noqa: F401
from general_ludd.event_loop.loop import EventLoop
from general_ludd.events.bus import EventBus
from general_ludd.events.hooks import HookSystem
from general_ludd.events.types import (  # noqa: F401
    HookTriggeredEvent,
    PlaybookRemovedEvent,
    WorkerPingEvent,
    WorkerPongEvent,
)
from general_ludd.filestore.bootstrap import BinaryBootstrapper
from general_ludd.filestore.store import FileStore as _FS
from general_ludd.git_automation.repo import GitAutomation  # noqa: F401
from general_ludd.infra.deployment import DeploymentManager  # noqa: F401
from general_ludd.infra.utilization import UtilizationTracker
from general_ludd.logging.project_log import ProjectLogAdapter, ProjectLogFilter  # noqa: F401
from general_ludd.mcp.loader import load_mcp_config
from general_ludd.metrics.collector import MetricsCollector
from general_ludd.models.gateway import ModelProfile
from general_ludd.models.langgraph_gateway import LangGraphGateway  # noqa: F401
from general_ludd.models.model_registry import ModelRegistry
from general_ludd.observability.otel_bridge import OTelBridge
from general_ludd.observability.recorder import AutoBenchmarkRecorder  # noqa: F401
from general_ludd.projects.manager import seed_from_config
from general_ludd.projects.workspace import ProjectWorkspace
from general_ludd.prompts.registry import PromptRegistry
from general_ludd.quality.gate import QualityGateChecker  # noqa: F401
from general_ludd.quality.preflight import run_preflight
from general_ludd.reload.self_improve import SelfImprovementWorkflow  # noqa: F401
from general_ludd.reload.worker_broadcast import WorkerBroadcaster
from general_ludd.review.evidence_checker import EvidenceChecker  # noqa: F401
from general_ludd.review.reviewer import ReturnReviewer  # noqa: F401
from general_ludd.runtime.container import ContainerBuilder  # noqa: F401
from general_ludd.runtime.pip_bundle import PipBundleBuilder  # noqa: F401
from general_ludd.runtime.release import ReleaseArtifactValidator  # noqa: F401
from general_ludd.scoring.engine import PromptScoringEngine  # noqa: F401
from general_ludd.scoring.router import AdaptiveRouter
from general_ludd.secrets.config import OpenBaoConfig
from general_ludd.secrets.env import EnvSecretsManager
from general_ludd.secrets.manager import SecretsManager
from general_ludd.secrets.migration import migrate_profile_secrets
from general_ludd.secrets.project_secrets import ProjectSecretsManager
from general_ludd.skills.loader import discover_skills
from general_ludd.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

_daemon_state: dict[str, Any] = {
    "todos": [],
    "tick_metrics": {},
    "quality_gate": {},
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
        home = os.environ.get("HOME", os.path.expanduser("~"))
        candidates = [
            Path(home) / ".config" / "general-ludd",
            Path("/etc/general-ludd"),
        ]
        for candidate in candidates:
            if candidate.is_dir():
                config_dir = str(candidate)
                logger.info("Discovered config dir: %s", config_dir)
                break
        else:
            logger.info("No config directory found; daemon running unconfigured")
            return cfg

    cdir = Path(config_dir)
    if not cdir.is_dir():
        logger.info("Config directory %s does not exist; daemon running unconfigured", config_dir)
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

    mcp_dir = cdir / "mcp_servers"
    if mcp_dir.is_dir():
        all_mcp: dict[str, Any] = {}
        for mcp_file in sorted(mcp_dir.glob("*.yml")):
            try:
                loaded = load_mcp_config(str(mcp_file))
                if isinstance(loaded, dict):
                    all_mcp.update(loaded)
                elif isinstance(loaded, list):
                    for entry in loaded:
                        if isinstance(entry, dict) and "name" in entry:
                            all_mcp[entry["name"]] = entry
            except Exception as exc:
                logger.warning("Failed to load MCP config %s: %s", mcp_file, exc)
        cfg["mcp_servers"] = all_mcp

    tasks_dir = cdir / "tasks"
    if tasks_dir.is_dir():
        cfg["task_definitions"] = discover_task_definitions(str(tasks_dir))

    profiles_dir = cdir / "model_profiles"
    if profiles_dir.is_dir():
        cfg["model_profiles"] = load_model_profiles(profiles_dir=str(profiles_dir))

    return cfg


def build_secrets_resolver(
    openbao_config: OpenBaoConfig | None = None,
    env_overrides: dict[str, str] | None = None,
    projects_active: bool = False,
) -> Any:
    base: Any
    if openbao_config is not None and openbao_config.mode not in ("disabled", None):
        mode = openbao_config.mode
        has_url = bool(openbao_config.external_url)
        if mode == "external" and has_url:
            try:
                mgr = SecretsManager(config=openbao_config)
                mgr.connect()
                logger.info("OpenBao secrets backend configured: %s", openbao_config.external_url)
                base = mgr
            except Exception as exc:
                logger.warning("OpenBao external init failed (%s), using env fallback", exc)
                base = EnvSecretsManager(overrides=env_overrides)
        elif mode == "auto":
            if has_url:
                try:
                    mgr = SecretsManager(config=openbao_config)
                    mgr.connect()
                    logger.info("OpenBao auto-mode: connected to %s", openbao_config.external_url)
                    base = mgr
                except Exception as exc:
                    logger.warning("OpenBao auto-mode: connection failed (%s), using env fallback", exc)
                    base = EnvSecretsManager(overrides=env_overrides)
            else:
                logger.info("OpenBao auto-mode: no external URL configured, using env fallback")
                base = EnvSecretsManager(overrides=env_overrides)
        else:
            logger.info("OpenBao mode=%s: using env fallback", mode)
            base = EnvSecretsManager(overrides=env_overrides)
    else:
        base = EnvSecretsManager(overrides=env_overrides)

    if projects_active:

        class _LazyProjectSecrets:
            def __init__(self, base: Any):
                self._base = base
            def resolve(self, alias_name: str) -> str | None:
                return self._base.resolve(alias_name)  # type: ignore[no-any-return]
            def for_project(self, project_id: str) -> ProjectSecretsManager:
                return ProjectSecretsManager(base_manager=self._base, project_id=project_id)
        return _LazyProjectSecrets(base)
    return base


def _init_project_workspaces(project_manager: Any) -> dict[str, Any]:
    workspaces: dict[str, Any] = {}
    if project_manager is not None:
        try:
            for p in project_manager.list_active():
                pid = getattr(p, "project_id", str(p))
                workspaces[pid] = ProjectWorkspace(project_id=pid)
                workspaces[pid].ensure_dirs()
        except Exception:
            pass
    return workspaces


def load_model_profiles(profiles_dir: str | None = None) -> list[Any]:
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
    title: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=4096)
    queue: str = Field(default="core", pattern=r"^[a-z0-9_\-]+$")
    priority: str = Field(default="medium", pattern=r"^(low|medium|high|critical)$")
    work_type: str = Field(default="code", pattern=r"^[a-z_]+$")
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
    repo_url: str = ""
    workspace_path: str = ""
    dispatch_mode: str = "active"


class SetWeightRequest(BaseModel):
    weight: float


class RebalanceRequest(BaseModel):
    weights: dict[str, float]


class ModelSearchRequest(BaseModel):
    query: str = ""
    limit: int = 20


def _on_event_loop_done(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        logger.info("EventLoop task cancelled")
        return
    exc = task.exception()
    if exc is not None:
        logger.error("EventLoop task terminated with exception: %s", exc)
    else:
        logger.error("EventLoop task exited unexpectedly without exception")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    tick_interval = app.state.tick_interval
    event_loop = None
    task = None
    engine = None

    try:
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

        if is_sqlite_url(str(engine.url)):
            try:
                from general_ludd.db.migrations import get_alembic_config, run_upgrade, stamp_head
                alembic_cfg = get_alembic_config(str(engine.url))
                stamp_head(alembic_cfg)
                logger.info("Alembic stamped head on SQLite database")
            except Exception as exc:
                logger.debug("Alembic stamp skipped: %s", exc)

        runner = AnsibleRunnerAdapter()
        subsys = _get_or_create_subsystems(app)
        ext = _get_or_create_extended_subsystems(app, session_factory=session_factory)

        secrets_resolver = build_secrets_resolver(
            openbao_config=startup_config.get("openbao_config"),
            projects_active=bool(ext.get("projects")),
        )
        app.state._secrets_resolver = secrets_resolver

        model_profiles = startup_config.get("model_profiles", [])
        if model_profiles and hasattr(secrets_resolver, "write_secret"):
            try:
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

        templates_dir = getattr(app.state, "_templates_dir", None)
        prompt_registry = PromptRegistry(
            template_dir=templates_dir,
            event_bus=subsys["bus"],
        )
        prompt_registry.refresh()
        app.state._prompt_registry = prompt_registry

        # Build budget guard from config
        budget_guard = None
        if uc is not None:
            budget_data = getattr(uc, "budget", None) or {}
            if budget_data and any(budget_data.values()):
                from general_ludd.controllers.budget import RunBudgetGuard
                budget_guard = RunBudgetGuard(
                    run_budget_usd=float(budget_data.get("daily_limit", float("inf"))),
                    run_timeout_seconds=float(budget_data.get("timeout_seconds", float("inf"))),
                    per_call_budget_usd=float(budget_data.get("per_task_limit", float("inf"))),
                )

        event_loop = EventLoop(
            worker_base_url="http://localhost:8000",
            runner=runner,
            session=session_factory,
            http_client=None,
            todo_repo=None,
            task_return_repo=None,
            budget_guard=budget_guard,
            mcp_client=None,
            mcp_tool_registry=None,
            event_bus=subsys["bus"],
            project_manager=ext["projects"],
            skill_registry=ext["skill_registry"],
            prompt_registry=prompt_registry,
            config={
                "default_playbook": "noop.yml",
                "model_profiles": startup_config.get("model_profiles", []),
                "rules": startup_config.get("rules", []),
            },
            adaptive_router=ext["adaptive_router"],
            daemon_state=_daemon_state,
            project_workspace=_init_project_workspaces(ext["projects"]),
            project_secrets_manager=secrets_resolver,
        )
        app.state.event_loop = event_loop
        app.state.event_loop._runner = runner
        app.state._db_engine = engine
        app.state._session_factory = session_factory
        task = asyncio.create_task(event_loop.run_forever(interval=tick_interval))
        task.add_done_callback(_on_event_loop_done)
        logger.info("Daemon started: db=%s event_loop=running", engine.url)

        bootloader = BinaryBootstrapper(store=_FS())
        synced = bootloader.sync_bundled_to_filestore()
        if synced:
            logger.info("Synced bundled binaries to filestore: %s", ", ".join(synced))

        async def _init_preflight() -> None:
            loop = asyncio.get_running_loop()
            result: dict[str, Any] = await loop.run_in_executor(None, run_preflight)
            _daemon_state["quality_gate"] = result
            logger.info(
                "Preflight quality gate: %s (%d/%d)",
                result["overall"],
                result["passed_count"],
                result["total_count"],
            )

        app.state._preflight_task = asyncio.create_task(_init_preflight())

        otel_bridge: OTelBridge | None = None
        if uc is not None and hasattr(uc, "observability"):
            obs_cfg = uc.observability
            if obs_cfg.otel_endpoint:
                otel_bridge = OTelBridge(
                    endpoint=obs_cfg.otel_endpoint,
                    service_name=obs_cfg.service_name,
                )
                app.state._otel_bridge = otel_bridge
                if otel_bridge.is_available():
                    logger.info("OTel bridge active: %s", obs_cfg.otel_endpoint)
    except Exception as exc:
        logger.error("Daemon startup failed: %s", exc)
        app.state._degraded = str(exc)

    yield

    if getattr(app.state, "_degraded", None):
        logger.warning("Daemon is running in degraded mode: %s", app.state._degraded)
    if event_loop is not None:
        event_loop.stop()
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    if engine is not None:
        await engine.dispose()
    otel_bridge_ref = getattr(app.state, "_otel_bridge", None)
    if otel_bridge_ref is not None and hasattr(otel_bridge_ref, "shutdown"):
        otel_bridge_ref.shutdown()


def _get_or_create_subsystems(app: FastAPI) -> dict[str, Any]:
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
    if not hasattr(app.state, "_metrics_collector") or app.state._metrics_collector is None:
        app.state._metrics_collector = MetricsCollector()
    if not hasattr(app.state, "_project_manager") or app.state._project_manager is None:
        startup_cfg = app.state._startup_config if hasattr(app.state, "_startup_config") else {}
        app.state._project_manager = seed_from_config(startup_cfg)
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
        benchmark_repo = BenchmarkRepository(session_factory)
        quantization_map: dict[str, tuple[str, float]] = {}
        tracker = getattr(app.state, "_quantization_tracker", None)
        if tracker is not None:
            quantization_map = {
                mid: (info.precision, info.confidence)
                for mid, info in tracker._data.items()
            }
        adaptive_router = AdaptiveRouter(
            benchmark_repo=benchmark_repo,
            quantization_map=quantization_map,
        )
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
        "auto_configurator": getattr(app.state, "_auto_configurator", None),
        "scraper": getattr(app.state, "_scraper", None),
    }


def create_daemon_app(
    tick_interval: float | None = None,
    log_level: str = "info",
    config_dir: str | None = None,
    templates_dir: str | None = None,
    playbooks_dir: str | None = None,
) -> FastAPI:
    if tick_interval is None:
        env_tick = os.environ.get("GLUDD_TICK_INTERVAL")
        tick_interval = float(env_tick) if env_tick else 1.0
    env_log_level = os.environ.get("GLUDD_LOG_LEVEL")
    if env_log_level and log_level == "info":
        log_level = env_log_level
    if config_dir is None:
        config_dir = os.environ.get("GLUDD_CONFIG_DIR")
    if templates_dir is None:
        templates_dir = os.environ.get("GLUDD_TEMPLATES_DIR")
    if playbooks_dir is None:
        playbooks_dir = os.environ.get("GLUDD_PLAYBOOKS_DIR")

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
    app.state._stats_start_time = time.monotonic()
    app.state._stats_requests = 0
    app.state._stats_responses = 0

    _psk = os.environ.get("GLUDD_PSK", "")
    app.state._psk = _psk

    _PUBLIC_PATHS = {"/healthz", "/docs", "/openapi.json", "/redoc"}

    @app.middleware("http")
    async def auth_and_stats_middleware(request: Any, call_next: Any) -> Any:
        app.state._stats_requests += 1
        from general_ludd.observability.metrics_exporter import get_metrics_exporter
        metrics = get_metrics_exporter()
        metrics.counter_inc("gludd_http_requests_total", {"method": request.method})
        start = time.monotonic()
        if _psk:
            path = request.url.path
            if path not in _PUBLIC_PATHS and not path.startswith("/docs"):
                auth = request.headers.get("Authorization", "")
                token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
                if not token or token != _psk:
                    from fastapi.responses import JSONResponse

                    app.state._stats_responses += 1
                    return JSONResponse(status_code=401, content={"error": "unauthorized"})
        response = await call_next(request)
        app.state._stats_responses += 1
        elapsed = time.monotonic() - start
        status = str(response.status_code)
        metrics.histogram_observe("gludd_http_request_duration_seconds", elapsed, {"status": status})
        metrics.counter_inc("gludd_http_responses_total", {"status": status})
        return response

    if log_level == "debug":
        logging.getLogger("httpx").setLevel(logging.DEBUG)
        logging.getLogger("httpcore").setLevel(logging.DEBUG)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        degraded = getattr(app.state, "_degraded", None)
        if degraded:
            return {"status": "degraded", "reason": str(degraded)[:200]}
        return {"status": "healthy"}

    @app.get("/metrics")
    async def metrics_prometheus() -> Any:
        from fastapi.responses import PlainTextResponse
        from general_ludd.observability.metrics_exporter import get_metrics_exporter
        return PlainTextResponse(content=get_metrics_exporter().render_prometheus())

    @app.get("/admin/metrics/export")
    async def admin_metrics_export() -> dict[str, Any]:
        from general_ludd.observability.metrics_exporter import get_metrics_exporter
        m = get_metrics_exporter()
        return {
            "counters": m.get_counters(),
            "gauges": m.get_gauges(),
            "uptime_seconds": time.monotonic() - m._started_at,
        }

    @app.get("/admin/daemon/stats")
    async def admin_daemon_stats() -> dict[str, Any]:
        import os

        import psutil

        uptime = time.monotonic() - app.state._stats_start_time
        proc = psutil.Process(os.getpid())
        mem_mb = proc.memory_info().rss / (1024 * 1024)
        return {
            "pid": os.getpid(),
            "requests_total": app.state._stats_requests,
            "responses_total": app.state._stats_responses,
            "memory_mb": round(mem_mb, 2),
            "uptime_s": round(uptime, 2),
        }

    # Lazy to avoid circular import: routers/*.py import from daemon at module level
    from general_ludd.routers import (
        ansible,
        benchmark,
        compute,
        filestore,
        integrity,
        mcp,
        models,
        projects,
        quantization,
        reload,
        self_improve,
        signing,
        skills,
        slurm,
        todos,
        worktree,
    )

    todos.register(app, _daemon_state)
    models.register(app, _daemon_state)
    benchmark.register(app, _daemon_state)
    mcp.register(app, _daemon_state)
    skills.register(app, _daemon_state)
    compute.register(app, _daemon_state)
    filestore.register(app, _daemon_state)
    integrity.register(app, _daemon_state)
    signing.register(app, _daemon_state)
    projects.register(app, _daemon_state)
    quantization.register(app, _daemon_state)
    reload.register(app, _daemon_state)
    worktree.register(app, _daemon_state)
    ansible.register(app, _daemon_state)
    slurm.register(app, _daemon_state)
    self_improve.register(app, _daemon_state)

    return app
