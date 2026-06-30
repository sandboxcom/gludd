"""Unified daemon — FastAPI app with embedded event loop and hot-reload admin endpoints."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from pydantic import BaseModel, Field

from general_ludd.ansible.isolation import ProcessIsolationConfig
from general_ludd.ansible.runner import AnsibleRunnerAdapter
from general_ludd.config.binary_paths import BinaryPaths
from general_ludd.config.loader import load_user_config
from general_ludd.config.model_routing import ModelRoutingConfig, load_model_routing
from general_ludd.config.project_dir import find_project_gludd_dir, merge_config, project_config_path
from general_ludd.config.task_loader import discover_task_definitions
from general_ludd.config.user_config import UserConfig
from general_ludd.controllers.budget import RunBudgetGuard
from general_ludd.db.repository import AuditEventRepository, BenchmarkRepository
from general_ludd.db.session import (
    create_async_session_factory,
    ensure_tables,
    init_engine_from_config,
    is_sqlite_url,
    seed_initial_queues,
)
from general_ludd.event_loop.loop import EventLoop
from general_ludd.events.bus import EventBus
from general_ludd.events.hooks import HookSystem
from general_ludd.filestore.bootstrap import BinaryBootstrapper
from general_ludd.filestore.store import FileStore as _FS
from general_ludd.infra.utilization import UtilizationTracker
from general_ludd.logging.project_log import ProjectLogAdapter
from general_ludd.mcp.loader import load_mcp_config
from general_ludd.metrics.collector import MetricsCollector
from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.models.model_registry import ModelRegistry
from general_ludd.models.timeout_detector import ModelHealthTracker
from general_ludd.observability.dashboard_data import DashboardDataProvider
from general_ludd.observability.otel_bridge import OTelBridge
from general_ludd.observability.recorder import AutoBenchmarkRecorder
from general_ludd.projects.manager import seed_from_config
from general_ludd.projects.workspace import ProjectWorkspace
from general_ludd.prompts.registry import PromptRegistry
from general_ludd.quality.preflight import run_preflight
from general_ludd.reload.worker_broadcast import WorkerBroadcaster
from general_ludd.scoring.router import AdaptiveRouter
from general_ludd.scoring.task_embeddings import TaskEmbeddingStore
from general_ludd.secrets.config import OpenBaoConfig
from general_ludd.secrets.env import EnvSecretsManager
from general_ludd.secrets.manager import SecretsManager
from general_ludd.secrets.migration import migrate_profile_secrets
from general_ludd.secrets.project_secrets import ProjectSecretsManager
from general_ludd.skills.loader import discover_skills
from general_ludd.skills.registry import SkillRegistry

logger = ProjectLogAdapter(logging.getLogger(__name__))

# Back-compat default. ``create_daemon_app()`` builds a FRESH per-app dict
# (see ``app.state.daemon_state``) so state no longer bleeds between FastAPI
# instances in the same process. This module-level name is rebound to the most
# recently created app's dict by the factory, preserving legacy callers that
# import/observe ``_daemon_state`` directly (e.g. scripts/dogfood.py, test
# fixtures). It must never again be the authoritative store for a running app.
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
        "rules": [],
        "project_gludd_dir": find_project_gludd_dir(),
        "remediation_config": None,
    }

    def _apply_project_overlay() -> None:
        """Deep-merge .gludd/general-ludd.yml over the user config (project wins).

        Called before every return so the overlay applies even when no user config
        directory exists — a repo may have ``.gludd/general-ludd.yml`` without any
        ``~/.config/general-ludd`` present.
        """
        proj_cfg = project_config_path(cfg["project_gludd_dir"])
        if proj_cfg is None:
            return
        try:
            with open(proj_cfg) as _f:
                proj_data = yaml.safe_load(_f) or {}
        except Exception as exc:
            logger.warning("Failed to load project config overlay %s: %s", proj_cfg, exc)
            return
        if not proj_data:
            return
        uc = cfg["user_config"]
        user_dict: dict[str, Any] = (
            uc.model_dump() if hasattr(uc, "model_dump") else dict(vars(uc))
        )
        merged = merge_config(user_dict, proj_data)
        try:
            cfg["user_config"] = UserConfig(**merged)
        except Exception as exc:
            logger.warning("Project config overlay failed validation: %s", exc)

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
            _apply_project_overlay()
            return cfg

    cdir = Path(config_dir)
    if not cdir.is_dir():
        logger.info("Config directory %s does not exist; daemon running unconfigured", config_dir)
        _apply_project_overlay()
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

    # Apply project overlay (.gludd/general-ludd.yml) BEFORE extracting rules so
    # any rules defined in the project overlay are captured in cfg["rules"].
    _apply_project_overlay()

    # Surface the rules engine: copy UserConfig.rules into startup_config so the
    # EventLoop (which reads startup_config["rules"]) receives operator rules.
    uc_loaded = cfg.get("user_config")
    if uc_loaded is not None:
        cfg["rules"] = list(getattr(uc_loaded, "rules", []) or [])

    return cfg


def _openbao_reachable(mgr: Any) -> bool:
    """Bounded reachability/auth check for an OpenBao SecretsManager.

    Returns True only if the backend answers `is_authenticated()` truthfully.
    Any exception (connection refused, timeout, auth error) is treated as
    unreachable so the caller falls back to env vars instead of hanging or
    silently failing every resolution. W2.9 (H17).
    """
    client = getattr(mgr, "_client", None)
    if client is None:
        return False
    try:
        return bool(client.is_authenticated())
    except Exception:
        return False


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
                # W2.9 (H17): auto mode TRIES OpenBao but verifies reachability
                # with a bounded health check before committing to it. A built
                # hvac client does not prove the backend is up — without this
                # check, an unreachable OpenBao would silently swallow every
                # secret resolution. On any failure we fall back to env vars and
                # log which path won.
                try:
                    mgr = SecretsManager(config=openbao_config)
                    mgr.connect()
                    if _openbao_reachable(mgr):
                        logger.info(
                            "OpenBao auto-mode: connected and healthy at %s",
                            openbao_config.external_url,
                        )
                        base = mgr
                    else:
                        logger.warning(
                            "OpenBao auto-mode: %s unreachable/unauthenticated, using env fallback",
                            openbao_config.external_url,
                        )
                        base = EnvSecretsManager(overrides=env_overrides)
                except Exception as exc:
                    _url = openbao_config.external_url or ""
                    if _url.startswith("http://"):
                        logger.error(
                            "OpenBao auto-mode: rejected plaintext URL %r — "
                            "external_url must use https:// to avoid leaking the auth "
                            "token over unencrypted transport; falling back to env",
                            _url,
                        )
                    else:
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
                result = self._base.resolve(alias_name)
                if isinstance(result, str):
                    return result
                return None
            def for_project(self, project_id: str) -> ProjectSecretsManager:
                return ProjectSecretsManager(base_manager=self._base, project_id=project_id)
        return _LazyProjectSecrets(base)
    return base


def resolve_secret_manager_for_call(
    app: FastAPI, authorization: str | None
) -> Any:
    """Return a SecretsManager scoped to the request's auth context.

    When ``authorization`` carries an STS Bearer token (``Bearer <sts_token>``)
    that resolves in the daemon's STSRegistry, a NEW SecretsManager is built
    sharing the daemon-wide hvac client but scoped to the token's PermissionSpec
    — narrowest-effective-scope for the duration of this one request.

    When ``authorization`` is absent, malformed, or carries the daemon PSK, the
    daemon-wide resolver (built with the default ``build`` spec, or None when
    unconfigured) is returned unchanged so existing callers and tests are
    unaffected.
    """
    resolver = getattr(app.state, "_secrets_resolver", None)
    if authorization is None or not authorization.startswith("Bearer "):
        return resolver
    token = authorization[len("Bearer ") :].strip()
    registry = getattr(app.state, "_sts_registry", None)
    if registry is None:
        return resolver
    claim = registry.resolve(token)
    if claim is None:
        # Unknown / expired / revoked token — return the daemon-wide resolver
        # rather than a scoped one. The caller's PSK check (which runs first)
        # gates whether this code path is reached at all.
        return resolver
    # The daemon-wide resolver may be a SecretsManager, an EnvSecretsManager,
    # or a LazyProjectSecrets wrapper. Only SecretsManager carries an hvac
    # client we can re-scope; EnvSecretsManager has no path-gated backend.
    base_client = getattr(resolver, "_client", None)
    base_config = getattr(resolver, "_config", None)
    if base_client is None or base_config is None:
        return resolver
    from general_ludd.secrets.manager import SecretsManager

    return SecretsManager(
        client=base_client,
        config=base_config,
        permission_spec=claim.spec,
    )


async def _restore_persisted_projects(project_manager: Any, session_factory: Any) -> None:
    """W3.11 (H13): rehydrate runtime-added projects from the DB and clone their repos.

    Config-seeded projects already live in the manager; this merges in any project
    persisted via ProjectRepository (e.g. added through /admin/projects in a prior
    run) that the config does not cover, and materializes each project's repo_url
    into its workspace. Best-effort: a failure here must not abort startup.
    """
    if project_manager is None or session_factory is None:
        return
    try:
        from general_ludd.db.repository import ProjectRepository
        from general_ludd.projects.manager import (
            materialize_project_workspace,
            rebuild_manager_from_db,
        )

        async with session_factory() as session:
            repo = ProjectRepository(session)
            db_mgr = await rebuild_manager_from_db(repo)

        existing_ids = {p.project_id for p in project_manager.list_projects(active_only=False)}
        for proj in db_mgr.list_active():
            if proj.project_id not in existing_ids:
                project_manager._projects[proj.project_id] = proj
            if proj.repo_url:
                materialize_project_workspace(
                    repo_url=proj.repo_url,
                    workspace_path=proj.workspace_path or proj.project_id,
                )
    except Exception:  # pragma: no cover - defensive startup guard
        logger.error("Failed to restore persisted projects", exc_info=True)


async def _restore_persisted_spend(
    spend_limiter: Any,
    session_factory: Any,
    *,
    window_seconds: float,
) -> None:
    """#49 (#2): rehydrate the rolling spend window from the DB on startup.

    Without this, a daemon restart resets the in-memory window to zero — the
    spend cap could be evaded simply by restarting.  Records persisted by
    SpendRepository within the current rolling window are loaded back into the
    limiter via ``restore()``.

    The daemon limiter uses a WALL-CLOCK clock (``time.time``) so persisted
    timestamps remain comparable across process restarts (a monotonic clock
    resets its origin each process and could not be persisted meaningfully).

    Best-effort: a failure here must not abort startup, but it is logged loudly
    because a silent failure would re-open the restart-bypass.
    """
    if spend_limiter is None or session_factory is None:
        return
    try:
        import time as _time

        from general_ludd.db.repository import SpendRepository

        since = _time.time() - float(window_seconds)
        async with session_factory() as session:
            repo = SpendRepository(session)
            rows = await repo.list_since(since)
        records = [(float(r.ts), float(r.cost_usd)) for r in rows]
        spend_limiter.restore(records)
        logger.info(
            "SpendLimiter: restored %d persisted spend record(s) from DB "
            "(window_spend=%.6f USD)",
            len(records),
            spend_limiter.window_spend(),
        )
    except Exception as exc:  # pragma: no cover - defensive startup guard
        logger.warning("Failed to restore persisted spend: %s", exc)


def _init_project_workspaces(project_manager: Any) -> dict[str, Any]:
    workspaces: dict[str, Any] = {}
    if project_manager is not None:
        try:
            for p in project_manager.list_active():
                pid = getattr(p, "project_id", str(p))
                workspaces[pid] = ProjectWorkspace(project_id=pid)
                workspaces[pid].ensure_dirs()
        except Exception as exc:
            logger.warning("Failed to initialize project workspaces: %s", exc, exc_info=True)
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


@dataclass
class _BudgetConfig:
    daily_limit: float
    per_task_limit: float
    timeout_seconds: float
    spend_window_usd: float
    spend_window_seconds: float


def _parse_budget_config(uc: Any) -> _BudgetConfig:
    raw = (getattr(uc, "budget", None) or {}) if uc is not None else {}
    return _BudgetConfig(
        daily_limit=float(raw.get("daily_limit", float("inf"))),
        per_task_limit=float(raw.get("per_task_limit", float("inf"))),
        timeout_seconds=float(raw.get("timeout_seconds", float("inf"))),
        spend_window_usd=float(raw.get("spend_window_usd", 0.0)),
        spend_window_seconds=float(raw.get("spend_window_seconds", 3600.0)),
    )


def _on_event_loop_done(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        logger.info("EventLoop task cancelled")
        return
    exc = task.exception()
    if exc is not None:
        logger.error("EventLoop task terminated with exception: %s", exc)
    else:
        logger.error("EventLoop task exited unexpectedly without exception")


def _check_degraded(app: FastAPI) -> Any:
    """Return a 503 JSONResponse when the daemon lifespan failed, else None.

    Mutating handlers (dispatch, self-update, spend/configure) must call this
    at entry and short-circuit when enforcement infrastructure is inert:

        resp = _check_degraded(app)
        if resp is not None:
            return resp

    Read-only handlers and probes (/healthz, /readyz) are intentionally exempt
    — they must keep serving so operators can observe the degraded state.
    """
    degraded = getattr(app.state, "_degraded", None)
    if not degraded:
        return None
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=503,
        content={"error": "degraded", "reason": str(degraded)[:200]},
    )


def _build_pipeline_controller(pipeline_cfg: Any, dispatcher: Any) -> Any:
    """Construct a PipelineController bound to real daemon subsystems (#77).

    Translates the user-facing ``pipeline`` config block into the internal
    ``PipelineConfig`` and wires the dispatch/merge/gate callables via the
    pipeline daemon adapters. The repo merged into is the process's git root
    (cwd); disk-pressure back-pressure uses the same floor as ``disk-guard``.
    The default gate is a conservative no-op-green (the real gate is the
    separate ``make gate`` pipeline); operators wire a stricter gate callable
    by replacing it on the returned controller before start.
    """
    from general_ludd.pipeline.controller import PipelineController
    from general_ludd.pipeline.daemon_adapters import (
        make_disk_ok,
        make_dispatch_fn,
        make_merge_fn,
    )
    from general_ludd.pipeline.state import PipelineConfig

    repo_path = os.getcwd()
    cfg = PipelineConfig(
        enabled=True,
        floor=int(getattr(pipeline_cfg, "floor", 1)),
        target=int(getattr(pipeline_cfg, "target", 3)),
        gate_debounce_s=float(getattr(pipeline_cfg, "gate_debounce_s", 30.0)),
        max_worktrees=int(getattr(pipeline_cfg, "max_worktrees", 6)),
        dispatch_interval_s=float(getattr(pipeline_cfg, "dispatch_interval_s", 0.5)),
        integrate_interval_s=float(getattr(pipeline_cfg, "integrate_interval_s", 0.5)),
        gate_poll_interval_s=float(getattr(pipeline_cfg, "gate_poll_interval_s", 0.5)),
        heartbeat_interval_s=float(getattr(pipeline_cfg, "heartbeat_interval_s", 5.0)),
    )

    async def _gate_green() -> bool:
        # Conservative default: the in-process pipeline does not run the full
        # ~16-min suite on the event loop. A stricter gate callable can be
        # injected by an operator before start(); the lane treats True as green.
        return True

    return PipelineController(
        cfg,
        make_dispatch_fn(dispatcher),
        make_merge_fn(repo_path),
        _gate_green,
        disk_ok=make_disk_ok(repo_path),
    )


def build_event_loop_mcp_dispatcher(
    *,
    mcp_client: Any | None,
    mcp_tool_registry: Any | None,
    skill_registry: Any | None = None,
    agent_dispatcher: Any | None = None,
) -> Any:
    """Build the DynamicDispatcher the EventLoop uses to execute model tool-calls.

    Completion-integrity HIGH fix (audit a30dc5ac): without this, the daemon
    constructed an ``MCPClient`` and handed it to the ``EventLoop`` purely to
    *advertise* tool names, but never built a dispatcher with an ``mcp`` handler.
    At dispatch time the loop saw ``_dispatcher is None`` and DROPPED the model's
    MCP tool-call ("no dispatcher is wired — skipping dispatch"). This builder
    closes that gap by returning a fully-wired
    :class:`~general_ludd.dispatch.dynamic_dispatcher.DynamicDispatcher`.

    Wiring decisions:

    * **Role** — the dispatcher acts under the ``"event_loop"`` role, which the
      capability lattice grants ``{"role", "mcp", "skill"}`` (and deliberately
      NOT ``"collection"``: the loop never self-modifies). Using a real,
      mcp-capable role avoids the fail-closed ``capability_denied`` trap that a
      ``None`` role would hit, WITHOUT widening to the ``UNRESTRICTED_ROLE``
      sentinel. No ``default_registry`` switch is required — the gate is on the
      role, not on an AgentRegistry.
    * **mcp_handler** — routes a model tool-call ``name`` of the form
      ``"<server_id>/<tool_name>"`` to ``mcp_client.call_tool(server_id,
      tool_name, args)`` (the same resolution the HTTP dispatch path and
      ``daemon_wiring.make_mcp_handler`` use). The registry-backed server_id
      validation inside ``MCPClient.call_tool`` defends against tool-name
      hijack. Because ``DynamicDispatcher.dispatch`` invokes handlers
      *synchronously* (and the EventLoop dispatch site runs INSIDE the active
      asyncio loop, so ``run_until_complete`` would raise), the async
      ``call_tool`` coroutine is driven to completion on a short-lived worker
      thread running its own event loop.
    * **skill_handler** — wired from the live skill registry so the same
      dispatcher also serves the ``skill`` kind the lattice grants; a ``None``
      registry simply leaves that kind unregistered (fail-closed).
    * **role_handler** — wired from the live ``AgentDispatcher`` via
      :func:`make_role_handler`. Like mcp, the handler is *async* and the
      ``DynamicDispatcher`` invokes handlers synchronously, so it is driven to
      completion through the same ``_sync_bridge`` (worker thread owning its
      own loop) — registering it raw would store an un-awaited coroutine.

    Args:
        mcp_client: A connected ``MCPClient`` (or None). When None, no ``mcp``
            handler is registered and mcp calls fail-closed.
        mcp_tool_registry: The ``MCPToolRegistry`` (currently advisory — server
            resolution is name-prefixed; passed through to keep the call-site
            explicit and for future per-tool server resolution).
        skill_registry: A ``SkillRegistry`` (or None) for the ``skill`` kind.
        agent_dispatcher: An ``AgentDispatcher`` (or None) for the ``role``
            kind. When None, no ``role`` handler is registered.

    Returns:
        A configured ``DynamicDispatcher`` bound to the ``event_loop`` role, or
        ``None`` when there is nothing to dispatch (no mcp client, no skill
        registry, and no agent dispatcher) so the EventLoop keeps its existing
        no-dispatcher behaviour.
    """
    from general_ludd.daemon_wiring import (
        make_mcp_handler,
        make_role_handler,
        make_skill_handler,
    )
    from general_ludd.dispatch.dynamic_dispatcher import DynamicDispatcher

    if (
        mcp_client is None
        and skill_registry is None
        and agent_dispatcher is None
    ):
        return None

    # The MCP and role handlers from daemon_wiring are async; the
    # DynamicDispatcher calls handlers synchronously, so bridge each coroutine
    # on a worker thread that owns its own event loop. We cannot use
    # asyncio.run / run_until_complete on the dispatch site's loop because
    # that loop is already running.
    async_mcp_handler = make_mcp_handler(mcp_client)
    sync_mcp_handler = _sync_bridge(async_mcp_handler) if async_mcp_handler is not None else None

    async_role_handler = make_role_handler(agent_dispatcher)
    sync_role_handler = (
        _sync_bridge(async_role_handler) if async_role_handler is not None else None
    )

    return DynamicDispatcher(
        role="event_loop",
        mcp_handler=sync_mcp_handler,
        skill_handler=make_skill_handler(skill_registry),
        role_handler=sync_role_handler,
    )


def _sync_bridge(
    async_handler: Callable[[str, dict[str, Any]], Any],
) -> Callable[[str, dict[str, Any]], Any]:
    """Wrap an async ``(name, args) -> awaitable`` handler as a sync handler.

    The ``DynamicDispatcher`` invokes handlers synchronously and stores their
    return value verbatim, so an un-awaited coroutine would never execute. This
    drives the coroutine to completion: directly via ``asyncio.run`` when no loop
    is running on the calling thread, or — when the call site is already inside a
    running loop (the EventLoop dispatch path) — on a short-lived worker thread
    that owns its own loop, so the running loop is never re-entered.
    """

    def _bridged(name: str, args: dict[str, Any]) -> Any:
        import asyncio as _asyncio
        from concurrent.futures import ThreadPoolExecutor

        def _run() -> Any:
            return _asyncio.run(async_handler(name, args))

        try:
            _asyncio.get_running_loop()
        except RuntimeError:
            return _run()
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_run).result()

    return _bridged


# Tracks fire-and-forget self-update audit writes so the GC never reaps a task
# mid-flight (asyncio only holds a weakref to tasks). Mirrors the pattern used
# for the event-loop tick task.
_SELF_UPDATE_AUDIT_TASKS: set[asyncio.Task[Any]] = set()


def _build_self_update_audit_sink(
    session_factory: Any,
) -> Callable[[Any], None]:
    """Build a sync ``AuditSink`` that persists self-update ``AuditRecord``s.

    The apply ladder (``self_update.apply.apply_plan``) invokes its
    ``audit_sink`` *synchronously* (see ``AuditSink = Callable[[AuditRecord],
    None]``), but :class:`AuditEventRepository` is async. The returned closure
    bridges the two: it opens no session inline and instead schedules a
    fire-and-forget background task on the running loop (the sink is only ever
    reached from inside an async router handler) which opens its own
    short-lived session per record so it never shares state with the request
    handler's session.

    The sink is **fail-soft**: any persistence error is logged and swallowed —
    an audit-write failure must never break the self-update endpoint, which
    still returns the in-memory :class:`ApplyResult`. The full ``AuditRecord``
    payload is serialised into ``details`` so no decision is lost invisibly
    even when typed enumeration is absent (the event loop already uses raw
    ``event_type`` strings like ``"return_reviewed"``, so ``"self_update_*"``
    follows that precedent rather than extending the ``AuditEventType`` enum).
    """

    async def _persist(record: Any) -> None:
        import json as _json

        try:
            async with session_factory() as session:
                repo = AuditEventRepository(session)
                await repo.create(
                    event_type=f"self_update_{record.outcome}",
                    entity_type="self_update",
                    entity_id=record.requested_by,
                    project_id="default",
                    details=_json.dumps(record.as_dict()),
                )
                await session.commit()
        except Exception:
            logger.error(
                "self_update audit-sink write failed (outcome=%s)",
                getattr(record, "outcome", "?"),
                exc_info=True,
            )

    def _sink(record: Any) -> None:
        try:
            task = asyncio.create_task(_persist(record))
        except RuntimeError:
            # No running loop (e.g. a unit test invoking apply_plan directly):
            # audit is best-effort, so drop the row rather than raise.
            logger.warning(
                "self_update audit-sink skipped: no running event loop "
                "(outcome=%s)",
                getattr(record, "outcome", "?"),
            )
            return
        _SELF_UPDATE_AUDIT_TASKS.add(task)
        task.add_done_callback(_SELF_UPDATE_AUDIT_TASKS.discard)

    return _sink


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    tick_interval = app.state.tick_interval
    # Per-app state dict (created in create_daemon_app). Read from app.state so
    # concurrently-running apps never share/overwrite one another's state. If a
    # caller invokes the lifespan on a bare app (unit tests), materialise a fresh
    # per-app dict rather than falling back to the shared module global.
    daemon_state: dict[str, Any] = getattr(app.state, "daemon_state", None) or {
        "todos": [], "tick_metrics": {}, "quality_gate": {}
    }
    event_loop = None
    task = None
    engine = None

    try:
        startup_config = getattr(app.state, "_startup_config", {}) or {}
        db_config: dict[str, Any] = {}
        uc = startup_config.get("user_config")
        bc = _parse_budget_config(uc)
        if uc and hasattr(uc, "database"):
            db_config = uc.database or {}
        engine = init_engine_from_config(db_config)
        await ensure_tables(engine)

        session_factory = create_async_session_factory(engine)
        async with session_factory() as session:
            await seed_initial_queues(session)
            await session.commit()

        # Phase 2 Step 3 (self-improve wiring): build the audit_sink closure over
        # session_factory + AuditEventRepository and publish it on app.state so
        # the /admin/self-update/plan router can pass it through to apply_plan.
        # Built once here (after session_factory exists) so every request reuses
        # the same sink; the sink opens its own short-lived session per record.
        app.state._self_update_audit_sink = _build_self_update_audit_sink(
            session_factory
        )

        if is_sqlite_url(str(engine.url)):
            try:
                from general_ludd.db.migrations import get_alembic_config, stamp_head
                alembic_cfg = get_alembic_config(str(engine.url))
                # Run the synchronous alembic stamp off the event loop so it
                # doesn't stall every other coroutine during daemon startup.
                await asyncio.to_thread(stamp_head, alembic_cfg)
                logger.info("Alembic stamped head on SQLite database")
            except Exception as exc:
                logger.warning("Alembic stamp failed: %s", exc)

        # Phase 2: resolve Ansible collections/roles search paths via the 3-tier
        # resolver (project .gludd/collections → user → bundled) so project-local
        # roles/collections shadow the bundled ones. The adapter self-resolves
        # _collections_env from project_root; we ALSO publish the resolved
        # paths/env on app.state for observability + the EventLoop project-switch
        # rebuild path.
        from general_ludd.ansible.paths import (
            resolve_collections_paths,
            to_ansible_env,
        )

        _proj_gludd = startup_config.get("project_gludd_dir")
        _initial_project_root = (
            str(Path(_proj_gludd).parent) if _proj_gludd is not None else None
        )
        _collections_paths = resolve_collections_paths(_initial_project_root)
        _ansible_env: dict[str, str] = to_ansible_env(_collections_paths)
        app.state._collections_paths = _collections_paths
        app.state._ansible_env = dict(_ansible_env)
        logger.info(
            "Resolved Ansible collections paths (%d tier(s)): %s",
            len(_collections_paths),
            ", ".join(f"{e.source}={e.path}" for e in _collections_paths),
        )

        def _update_ansible_env(
            paths: list[Any], env: dict[str, str]
        ) -> None:
            """Callback the EventLoop invokes on project switch.

            Republishes the resolved paths/env on app.state. The adapter's own
            ``set_project_root`` (invoked separately by the EventLoop) handles
            rebinding its ``_collections_env``.
            """
            app.state._collections_paths = paths
            app.state._ansible_env = dict(env)

        app.state._ansible_env_updater = _update_ansible_env

        runner = AnsibleRunnerAdapter(
            default_env=dict(_ansible_env) if _ansible_env else None,
            project_root=_initial_project_root,
        )
        subsys = _get_or_create_subsystems(app)

        # CA-T7/CA-T8 fix: create and assign the health tracker BEFORE calling
        # _get_or_create_extended_subsystems so the AdaptiveRouter constructor
        # receives a live ModelHealthTracker (not None).  The tracker was
        # previously assigned ~50 lines later, after the router was already built,
        # making the health-filtering + quantization-penalty logic permanently
        # inert in the running daemon.  (ModelHealthTracker is imported at module
        # level so tests can patch general_ludd.daemon.ModelHealthTracker.)
        _pre_health_tracker = ModelHealthTracker()
        app.state._health_tracker = _pre_health_tracker

        # CA-T9 fix: create and assign the quantization tracker BEFORE calling
        # _get_or_create_extended_subsystems so the AdaptiveRouter constructor
        # receives a populated quantization_map rather than an empty {}.
        # Without this, getattr(app.state, "_quantization_tracker", None) inside
        # _get_or_create_extended_subsystems always returns None → quantization_map
        # stays {} → _apply_quantization_penalty never fires in the running daemon.
        # The tracker starts empty (no detections yet) but is the live instance
        # that /admin/quantization/detect will populate at runtime.
        from general_ludd.models.quantization import QuantizationTracker as _QuantizationTracker
        app.state._quantization_tracker = _QuantizationTracker()

        # Tier 2 RAG routing: construct + seed TaskEmbeddingStore before the
        # AdaptiveRouter is built so the router can borrow strength from
        # neighboring task types via cosine similarity. The store holds a
        # long-lived session (the router calls similarity_to() on every route),
        # so the session is kept open for the app lifetime and closed in the
        # lifespan teardown. ensure_embeddings() is idempotent — only empty rows
        # are embedded, so a warm restart never recomputes paid-for vectors.
        # Best-effort: on failure the store is left None and the router falls
        # back to exact-match history.
        app.state._embedding_store = None
        app.state._embedding_session = None
        try:
            _embedding_session = session_factory()
            _embedding_store = TaskEmbeddingStore(session=_embedding_session)
            await _embedding_store.ensure_embeddings()
            await _embedding_session.commit()
            app.state._embedding_store = _embedding_store
            app.state._embedding_session = _embedding_session
        except Exception:
            logger.error("TaskEmbeddingStore seeding failed", exc_info=True)
            with contextlib.suppress(Exception):
                if "_embedding_session" in locals():
                    await _embedding_session.close()

        ext = _get_or_create_extended_subsystems(app, session_factory=session_factory)
        daemon_state["receiver_buffer"] = app.state._receiver_buffer

        # W3.11 (H13): merge DB-persisted projects into the manager so projects
        # added at runtime survive a restart, and materialize each repo_url into
        # its workspace so dispatched jobs have real code to edit.
        await _restore_persisted_projects(ext.get("projects"), session_factory)

        # H1 fix: build_secrets_resolver() calls hvac client.is_authenticated()
        # synchronously (a blocking HTTP call).  Offload to a thread so the
        # event loop is never stalled if OpenBao is slow or unreachable.
        secrets_resolver = await asyncio.to_thread(
            build_secrets_resolver,
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
            except Exception:
                logger.error("Secret migration failed", exc_info=True)

        templates_dir = getattr(app.state, "_templates_dir", None)
        # Phase 2: prepend project .gludd/templates/ so project-local templates
        # shadow same-named global ones.  No-op when the dir does not exist.
        _proj_for_prompts = startup_config.get("project_gludd_dir")
        _extra_tmpl_dirs: list[str] = []
        if _proj_for_prompts is not None:
            _proj_tmpl_dir = Path(_proj_for_prompts) / "templates"
            if _proj_tmpl_dir.is_dir():
                _extra_tmpl_dirs = [str(_proj_tmpl_dir)]
        prompt_registry = PromptRegistry(
            template_dir=templates_dir,
            event_bus=subsys["bus"],
            extra_template_dirs=_extra_tmpl_dirs or None,
        )
        # P2 (perf): refresh() globs the template dir and read_text()s each *.j2
        # file — blocking filesystem IO. Offload it so the daemon-boot coroutine
        # does not stall the event loop while templates load. Return value is
        # unused; error handling is unchanged (an unreadable dir still raises and
        # is caught by the outer startup try/except → degraded mode).
        await asyncio.to_thread(prompt_registry.refresh)
        app.state._prompt_registry = prompt_registry

        # Build budget guard from config
        budget_guard = None
        if uc is not None:
            raw_budget = getattr(uc, "budget", None) or {}
            if raw_budget and any(raw_budget.values()):
                budget_guard = RunBudgetGuard(
                    run_budget_usd=bc.daily_limit,
                    run_timeout_seconds=bc.timeout_seconds,
                    per_call_budget_usd=bc.per_task_limit,
                )
        app.state._budget_guard = budget_guard

        # Build the model gateway once (H4/H12): both the in-process reviewer and
        # the agent dispatcher reuse the SAME gateway instance.
        # CA-T7/CA-T8: reuse the health_tracker pre-created before extended-subsystem
        # construction so the AdaptiveRouter holds the live instance (not None).
        health_tracker = app.state._health_tracker

        model_gateway = None
        if model_profiles:
            from general_ludd.models.provider_registry import ProviderRegistry

            _resolved_profiles = [
                p if isinstance(p, ModelProfile) else ModelProfile(**p)
                for p in model_profiles
                if isinstance(p, (ModelProfile, dict))
            ]
            model_gateway = ModelGateway(
                profiles=_resolved_profiles,
                # CI-1 fix: register each profile's provider so live calls have a
                # usable provider class. With provider_registry=None the gateway's
                # _registry was None and every live call raised "No provider registry
                # configured" — the daemon could not make a single live model call.
                provider_registry=ProviderRegistry.from_profiles(_resolved_profiles),
                secrets_manager=secrets_resolver,
                metrics_collector=ext.get("metrics_collector"),
                health_tracker=health_tracker,
                # Wire the operator-configured budget guard so a configured spend
                # ceiling is actually enforced — it was built above but never passed,
                # leaving budgets silently inert in the daemon.
                budget_guard=budget_guard,
            )
            app.state._model_gateway = model_gateway
            # Warn when a reasoning-model profile has a low max_output_tokens budget.
            _REASONING_MODEL_PREFIXES = ("glm-4.5", "glm-5")
            for _p in model_gateway._profiles.values():
                _mn = (_p.model_name or "").lower()
                if (
                    any(_mn.startswith(_pfx) for _pfx in _REASONING_MODEL_PREFIXES)
                    and (_p.max_output_tokens or 0) < 8192
                ):
                    logger.warning(
                        "Profile %s: max_output_tokens=%d may be too low for "
                        "reasoning model %s (reasoning_content fills first; "
                        "content may be empty). Recommend >= 8192.",
                        _p.model_profile_id,
                        _p.max_output_tokens,
                        _p.model_name,
                    )

        # H4 (W3.2): wire a real ReturnReviewer into the review phase when a
        # gateway exists. Review failure escalates the todo; it is never a
        # silent pass.
        return_reviewer = None
        if model_gateway is not None:
            from general_ludd.review.reviewer import ReturnReviewer

            return_reviewer = ReturnReviewer(
                gateway=model_gateway,
                prompt_registry=prompt_registry,
                router=ext.get("adaptive_router"),
                budget_guard=budget_guard,
            )

        # H2 (W3.7): self-improvement interval comes from config; 0 disables it.
        # interval=0 → disabled; default is 10 minutes so the feature is on out-of-the-box.
        self_improve_interval = 0
        if uc is not None:
            si_cfg = getattr(uc, "self_improve", None) or {}
            with contextlib.suppress(Exception):
                self_improve_interval = int(si_cfg.get("interval", 10))
        if not self_improve_interval:
            with contextlib.suppress(Exception):
                self_improve_interval = int(
                    startup_config.get("self_improve_interval", 10)
                )

        # W3.9 MCP wiring: build MCPToolRegistry and conditionally start MCPClient
        from general_ludd.mcp.client import MCPClient
        from general_ludd.mcp.config import MCPServerConfig
        from general_ludd.mcp.registry import MCPToolRegistry

        mcp_tool_registry = MCPToolRegistry()
        mcp_client = None
        mcp_configs = startup_config.get("mcp_servers", {}) or {}
        if mcp_configs:
            # Ensure values are MCPServerConfig instances
            typed_configs: dict[str, MCPServerConfig] = {}
            for srv_id, srv_cfg in mcp_configs.items():
                if isinstance(srv_cfg, MCPServerConfig):
                    typed_configs[srv_id] = srv_cfg
                elif isinstance(srv_cfg, dict):
                    typed_configs[srv_id] = MCPServerConfig(**srv_cfg)
            if typed_configs:
                try:
                    mcp_client = MCPClient(
                        configs=typed_configs,
                        registry=mcp_tool_registry,
                        secrets_mgr=secrets_resolver,
                    )
                    await mcp_client.start_all()
                    logger.info("MCPClient started with %d server(s)", len(typed_configs))
                except Exception as _mcp_exc:
                    logger.error(
                        "MCP startup failed (continuing without MCP)",
                        exc_info=True,
                    )
                    mcp_client = None
        app.state._mcp_client = mcp_client

        # Completion-integrity HIGH fix (audit a30dc5ac): wire a DynamicDispatcher
        # so the EventLoop can EXECUTE a model's MCP tool-call instead of dropping
        # it ("no dispatcher is wired"). Acts under the mcp-capable "event_loop"
        # role; None when there's nothing to dispatch (loop keeps prior behaviour).
        event_loop_dispatcher = build_event_loop_mcp_dispatcher(
            mcp_client=mcp_client,
            mcp_tool_registry=mcp_tool_registry,
            skill_registry=ext["skill_registry"],
        )

        # H3 fix: SpendLimiter must be constructed and rehydrated BEFORE
        # asyncio.create_task(event_loop.run_forever(...)) so the event loop's
        # first tick cannot bypass the operator spend cap.  PricingCatalog and
        # SpendLimiter are built here (including the persisted-spend rehydration
        # await) and then passed into the EventLoop constructor so _spend_limiter
        # is never None once the loop task is scheduled.
        # W: event-loop-wiring (#27) — SpendLimiter pre-call budget gate.
        # Build a rolling-window limiter from budget config when configured.
        from general_ludd.controllers.spend_limiter import SpendLimiter
        from general_ludd.pricing_intel import PricingCatalog

        # PricingCatalog is the PRIMARY price source for cost projection;
        # SpendLimiter.token_cost_usd() falls back to the static
        # infra/pricing.py table when the catalog has no live price.  Shared
        # across the limiter and any other subsystem that needs live rates.
        pricing_catalog = PricingCatalog()
        # Publish the catalog on app.state so /api/pricing (routers/observe.py)
        # can serve the SAME instance the SpendLimiter consumes — not a second
        # copy. Routers read it via ``_get_pricing_catalog(app)``.
        app.state._pricing_catalog = pricing_catalog

        spend_limiter: SpendLimiter | None = None
        if uc is not None:
            spend_window_usd = bc.spend_window_usd
            spend_window_seconds = bc.spend_window_seconds
            if spend_window_usd > 0.0:
                import time as _time

                # Wall-clock (time.time), NOT monotonic: persisted spend
                # timestamps must survive a process restart so the rolling
                # window can be rehydrated from the DB (#49 #2).
                spend_limiter = SpendLimiter(
                    limit_usd=spend_window_usd,
                    window_seconds=spend_window_seconds,
                    clock=_time.time,
                    catalog=pricing_catalog,
                )
                logger.info(
                    "SpendLimiter configured: limit=%.4f USD / %.0f s window",
                    spend_window_usd,
                    spend_window_seconds,
                )
                # Rehydrate accumulated spend so a restart can't reset the cap.
                await _restore_persisted_spend(
                    spend_limiter,
                    session_factory,
                    window_seconds=spend_window_seconds,
                )
        app.state._spend_limiter = spend_limiter

        event_loop = EventLoop(
            worker_base_url="http://localhost:8000",
            runner=runner,
            session=session_factory,
            http_client=None,
            todo_repo=None,
            task_return_repo=None,
            budget_guard=budget_guard,
            model_gateway=model_gateway,
            mcp_client=mcp_client,
            mcp_tool_registry=mcp_tool_registry,
            dispatcher=event_loop_dispatcher,
            event_bus=subsys["bus"],
            project_manager=ext["projects"],
            skill_registry=ext["skill_registry"],
            prompt_registry=prompt_registry,
            config={
                "default_playbook": "noop.yml",
                "model_profiles": startup_config.get("model_profiles", []),
                "rules": startup_config.get("rules", []),
                "queues": getattr(uc, "queues", []) if uc else [],
                "budget": getattr(uc, "budget", {}) if uc else {},
                "self_improve": getattr(uc, "self_improve", {}) if uc else {},
                # Daemon-level default repo_root: the process cwd at startup time
                # is a reasonable single-project fallback so verify_completion can
                # check commit:/artifact: refs without a resolved per-project
                # workspace. EventLoop._resolve_repo_root() overrides this with the
                # per-project workspace.repo_dir when available.
                "repo_root": os.getcwd(),
            },
            adaptive_router=ext["adaptive_router"],
            daemon_state=daemon_state,
            project_workspace=_init_project_workspaces(ext["projects"]),
            project_secrets_manager=secrets_resolver,
            reviewer=return_reviewer,
            self_improve_interval=self_improve_interval,
            # H3: spend_limiter passed via constructor so _spend_limiter is set
            # before the run_forever task is scheduled — the first tick can never
            # bypass the operator spend cap.
            spend_limiter=spend_limiter,
            # #31 (multi-agent safety): share the coordination router's
            # FileClaimRegistry (created in routers/coordination.register and
            # surfaced via /api/coordination + /api/facts) with the event loop's
            # git-delivery path so concurrent todos cannot clobber the same file.
            file_claim_registry=getattr(app.state, "_file_claims", None),
            ansible_env_updater=getattr(app.state, "_ansible_env_updater", None),
        )
        app.state.event_loop = event_loop
        app.state.event_loop._runner = runner
        app.state._runner = runner
        app.state._db_engine = engine
        app.state._session_factory = session_factory
        task = asyncio.create_task(event_loop.run_forever(interval=tick_interval))
        task.add_done_callback(_on_event_loop_done)
        app.state._event_loop_task = task  # W3.4: readyz checks this

        from general_ludd.controllers.budget_manager import BudgetManager
        from general_ludd.observability.metrics_exporter import get_metrics_exporter
        from general_ludd.observability.run_history import RunHistoryRecorder

        app.state._budget_manager = BudgetManager(
            daily_limit_usd=bc.daily_limit,
            per_todo_limit_usd=bc.per_task_limit,
        )
        app.state._run_history = RunHistoryRecorder()
        app.state._dashboard_data = DashboardDataProvider(
            metrics_exporter=get_metrics_exporter(),
            session_factory=session_factory,
        )

        benchmark_recorder = AutoBenchmarkRecorder(
            benchmark_repo=BenchmarkRepository(session_factory=session_factory),
            trace_buffer=getattr(app.state, "_recent_traces", None),
        )
        event_loop._benchmark_recorder = benchmark_recorder

        from general_ludd.worktree.core import WorktreeMonitor, WorktreeMonitorConfig
        config_dir = getattr(app.state, "_config_dir", None)
        wt_monitor = WorktreeMonitor(
            config=WorktreeMonitorConfig(
                watch_paths=[config_dir] if config_dir else [],
            ),
        )
        app.state._worktree_monitor = wt_monitor

        from general_ludd.agents.dispatcher import AgentDispatcher
        from general_ludd.agents.registry import default_registry
        from general_ludd.agents.types import AgentTask

        # Use default_registry() so the 4 built-in agents (build/plan/explore/
        # general) are registered. A bare AgentRegistry() leaves the registry
        # empty, which makes the dispatcher's can_invoke permission gate
        # (dispatcher.py) reject every dispatch ("not found in registry") —
        # silently disabling the agent-permission matrix in the daemon.
        registry = default_registry()
        dispatcher_executor = None

        # SpendLimiter, PricingCatalog, and spend_limiter are built and
        # rehydrated above, BEFORE the EventLoop constructor (H3 fix).
        # make_spend_guarded_executor is imported here for the gateway executor
        # block below.
        from general_ludd.daemon_wiring import make_spend_guarded_executor

        # InfraTracker wraps infra_cost_usd() the same way SpendLimiter wraps
        # token_cost_usd(): PricingCatalog is the PRIMARY price source for GPU
        # compute projection; infra/pricing.py:infra_cost_usd is the static
        # fallback when the catalog misses / errors / returns a non-time
        # granularity.  Shares the SAME pricing_catalog instance as the
        # SpendLimiter so catalog refreshes are observed by both.  Published on
        # app.state so routers/consumers can serve live compute pricing without
        # re-instantiating the catalog (mirrors _pricing_catalog above).
        from general_ludd.infra.pricing import InfraTracker

        infra_tracker = InfraTracker(catalog=pricing_catalog)
        app.state._infra_tracker = infra_tracker

        if model_gateway is not None:
            logger.info(
                "Gateway-backed executor enabled with %d model profile(s)",
                len(model_profiles),
            )

            # W: event-loop-wiring (#27) — compute the per-call cost projection
            # BEFORE defining _gateway_executor so the closure captures the real
            # value for the BudgetManager pre-checks.  Both layers use this same
            # projection: BudgetManager gates daily/per-todo ceilings against
            # (cumulative actuals + this projection); SpendLimiter enforces the
            # rolling-window soft cap atomically.  Passing 0.0 to BudgetManager
            # made its pre-checks purely reactive (could only block AFTER a prior
            # call's actual cost already crossed the limit, never the call that
            # would itself exceed it).
            #
            # Prefer the SpendLimiter's projection (PricingCatalog primary,
            # static table fallback) when a limiter is wired; otherwise use the
            # standalone static token_cost_usd() so projection still works when
            # budgeting is disabled.
            from general_ludd.infra.pricing import token_cost_usd

            _projected_cost_usd = 0.0
            _default_profile = model_gateway.get_profile("default")
            if _default_profile is not None:
                _project_model = _default_profile.model_name or "__default__"
                _project_in = min(_default_profile.max_input_tokens, 1000)
                _project_out = _default_profile.max_output_tokens
                if spend_limiter is not None:
                    _projected_cost_usd = spend_limiter.token_cost_usd(
                        _project_model, _project_in, _project_out
                    )
                else:
                    _projected_cost_usd = token_cost_usd(
                        _project_model, _project_in, _project_out
                    )

            async def _gateway_executor(task: AgentTask) -> str:
                profile_id = "default"
                budget_manager = getattr(app.state, "_budget_manager", None)
                if budget_manager is not None:
                    daily = budget_manager.check_daily_budget_reserved(
                        task.task_id, _projected_cost_usd
                    )
                    if not daily.get("allowed", True):
                        logger.warning(
                            "Gateway executor deferred for %s: daily budget exhausted",
                            task.task_id,
                        )
                        return "deferred:budget_exhausted"
                    per_todo = budget_manager.check_todo_budget(
                        task.task_id, _projected_cost_usd
                    )
                    if not per_todo.get("allowed", True):
                        logger.warning(
                            "Gateway executor deferred for %s: per-todo budget exhausted",
                            task.task_id,
                        )
                        # Release the daily reservation made above so a deferred
                        # call does not leak held budget.
                        budget_manager.release_reservation(task.task_id)
                        return "deferred:budget_exhausted"
                try:
                    result = await asyncio.to_thread(
                        model_gateway.call_model_with_retry,
                        profile_id,
                        [{"role": "user", "content": task.prompt}],
                    )
                    if budget_manager is not None:
                        budget_manager.record_spend(
                            task.task_id,
                            float(getattr(result, "cost_estimate", 0.0) or 0.0),
                        )
                    return result.content
                except Exception as exc:
                    logger.warning("Gateway executor failed for %s: %s", task.task_id, exc)
                    # The call never produced a cost, so release both reservations
                    # instead of leaking the held projected budget.
                    if budget_manager is not None:
                        budget_manager.release_reservation(task.task_id)
                    return f"Error: {exc}"

            dispatcher_executor = make_spend_guarded_executor(
                executor=_gateway_executor,
                spend_limiter=spend_limiter,
                projected_cost_usd=_projected_cost_usd,
            )

        app.state._agent_dispatcher = AgentDispatcher(
            registry=registry,
            executor=dispatcher_executor,
        )

        # --- 3-lane multitask+merge pipeline (#77), behind config flag ----- #
        # Default OFF: only starts when pipeline.enabled is true. Owns its own
        # dispatch/integrate/gate asyncio tasks + heartbeat. The daemon owns the
        # repo it merges into (the process cwd's git root).
        app.state._pipeline_controller = None
        pipeline_cfg = getattr(uc, "pipeline", None) if uc else None
        if pipeline_cfg is not None and getattr(pipeline_cfg, "enabled", False):
            try:
                pipeline_controller = _build_pipeline_controller(
                    pipeline_cfg, app.state._agent_dispatcher,
                )
                await pipeline_controller.start()
                app.state._pipeline_controller = pipeline_controller
                logger.info("Pipeline (#77) started: 3 lanes + heartbeat")
            except Exception as exc:
                logger.error("Pipeline startup failed (continuing degraded): %s", exc)

        logger.info("Daemon started: db=%s event_loop=running", engine.url)

        bootloader = BinaryBootstrapper(store=_FS())
        # P2 (perf): sync_bundled_to_filestore() read_bytes() each bundled binary
        # (multi-MB) and write_bytes() it into the filestore — blocking IO that
        # would stall the loop on boot. Offload to a thread; the returned list of
        # synced names and the method's own internal try/except are unchanged.
        synced = await asyncio.to_thread(bootloader.sync_bundled_to_filestore)
        if synced:
            logger.info("Synced bundled binaries to filestore: %s", ", ".join(synced))

        async def _init_preflight() -> None:
            loop = asyncio.get_running_loop()
            result: dict[str, Any] = await loop.run_in_executor(None, run_preflight)
            daemon_state["quality_gate"] = result
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

    # Phase 1 minimal hook: optionally launch the Ornith MCP server subprocess.
    app.state._ornith_mcp_proc = None
    _ornith_env_enabled = os.environ.get("ORNITH_ENABLED", "").lower() in {"1", "true", "yes"}
    _ornith_cfg_enabled = bool(getattr(uc, "ornith_enabled", False)) if uc is not None else False
    if _ornith_env_enabled or _ornith_cfg_enabled:
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "general_ludd.ornith.mcp_server",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            app.state._ornith_mcp_proc = proc
            logger.info("Ornith MCP server subprocess launched (pid=%s)", proc.pid)
        except Exception as ornith_exc:
            logger.warning("Failed to launch Ornith MCP subprocess: %s", ornith_exc)

    yield

    if getattr(app.state, "_degraded", None):
        logger.warning("Daemon is running in degraded mode: %s", app.state._degraded)
    pipeline_controller = getattr(app.state, "_pipeline_controller", None)
    if pipeline_controller is not None:
        with contextlib.suppress(Exception):
            await pipeline_controller.stop()
    mcp_client_ref = getattr(app.state, "_mcp_client", None)
    if mcp_client_ref is not None:
        with contextlib.suppress(Exception):
            await mcp_client_ref.stop_all()
    _el = event_loop if event_loop is not None else getattr(app.state, "event_loop", None)
    if _el is not None:
        _el.stop()
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    preflight_task_ref = getattr(app.state, "_preflight_task", None)
    if preflight_task_ref is not None:
        preflight_task_ref.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await preflight_task_ref
    if engine is not None:
        await engine.dispose()
    _embedding_session_ref = getattr(app.state, "_embedding_session", None)
    if _embedding_session_ref is not None:
        with contextlib.suppress(Exception):
            await _embedding_session_ref.close()
    otel_bridge_ref = getattr(app.state, "_otel_bridge", None)
    if otel_bridge_ref is not None and hasattr(otel_bridge_ref, "shutdown"):
        otel_bridge_ref.shutdown()
    _ornith_proc = getattr(app.state, "_ornith_mcp_proc", None)
    if _ornith_proc is not None:
        with contextlib.suppress(Exception):
            _ornith_proc.terminate()
            try:
                await asyncio.wait_for(_ornith_proc.wait(), timeout=5.0)
            except TimeoutError:
                _ornith_proc.kill()
                with contextlib.suppress(Exception):
                    await _ornith_proc.wait()


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
    if not hasattr(app.state, "_recent_traces") or app.state._recent_traces is None:
        from general_ludd.observability.trace_store import RecentTracesBuffer
        app.state._recent_traces = RecentTracesBuffer()
    if not hasattr(app.state, "_receiver_buffer") or app.state._receiver_buffer is None:
        from general_ludd.receiver.buffer import OverflowPolicy, ReceiverBuffer
        app.state._receiver_buffer = ReceiverBuffer(
            maxlen=10_000,
            overflow=OverflowPolicy.REJECT,
            retention_s=3600,
        )
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
        # Phase 2: register project skills AFTER global ones so same-named project
        # skills shadow (overwrite) the global entry — last write wins in the dict.
        _proj_for_skills = getattr(app.state, "_project_gludd_dir", None)
        if _proj_for_skills is not None:
            _proj_skills_dir = Path(_proj_for_skills) / "skills"
            if _proj_skills_dir.is_dir():
                registry.refresh(search_paths=[str(_proj_skills_dir)])
        app.state._skill_registry = registry

    adaptive_router = None
    if session_factory is not None and not hasattr(app.state, "_adaptive_router"):
        benchmark_repo = BenchmarkRepository(session_factory=session_factory)
        quantization_map: dict[str, tuple[str, float]] = {}
        tracker = getattr(app.state, "_quantization_tracker", None)
        if tracker is not None:
            quantization_map = {
                mid: (info.precision, info.confidence)
                for mid, info in tracker._data.items()
            }
        # Project-hierarchy phase 3: derive cross-project borrowing flags from
        # UserConfig.relationship_routing (default None → borrowing OFF, router
        # behaves exactly as before). The app-level router is GLOBAL
        # (project_id=None); per-project borrowing is opt-in via config + a
        # project-scoped router. relationship_repo stays None here (no global
        # relationship graph) so even with the flag on the global router never
        # borrows — borrowing requires a project_id + a relationship_repo.
        rr_enabled = False
        rr_edge_decay = 0.5
        rr_external_penalty = 0.5
        rr_min_borrow_weight = 0.05
        startup_cfg = getattr(app.state, "_startup_config", {}) or {}
        user_cfg = startup_cfg.get("user_config")
        rr_cfg = getattr(user_cfg, "relationship_routing", None) if user_cfg else None
        if rr_cfg is not None:
            rr_enabled = bool(getattr(rr_cfg, "enable_cross_project_borrowing", False))
            rr_edge_decay = float(getattr(rr_cfg, "edge_decay", 0.5))
            rr_external_penalty = float(getattr(rr_cfg, "external_penalty", 0.5))
            rr_min_borrow_weight = float(getattr(rr_cfg, "min_borrow_weight", 0.05))
        adaptive_router = AdaptiveRouter(
            benchmark_repo=benchmark_repo,
            quantization_map=quantization_map,
            health_tracker=getattr(app.state, "_health_tracker", None),
            embedding_store=getattr(app.state, "_embedding_store", None),
            enable_cross_project_borrowing=rr_enabled,
            edge_decay=rr_edge_decay,
            external_penalty=rr_external_penalty,
            min_borrow_weight=rr_min_borrow_weight,
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
        "worktree_monitor": getattr(app.state, "_worktree_monitor", None),
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
    # Per-app daemon state: each app owns a fresh dict so todos / tick_metrics /
    # quality_gate cannot bleed across FastAPI instances in one process (the
    # module-level ``_daemon_state`` used to be shared — a test-isolation hazard).
    daemon_state: dict[str, Any] = {
        "todos": [],
        "tick_metrics": {},
        "quality_gate": {},
    }
    app.state.daemon_state = daemon_state
    # Rebind the module-level name so legacy observers (scripts/dogfood.py, test
    # fixtures that read ``daemon_mod._daemon_state``) see this app's state. The
    # per-app dict on ``app.state.daemon_state`` remains the authoritative store.
    global _daemon_state
    _daemon_state = daemon_state
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
    app.state._self_update_audit_sink = None
    app.state._startup_config = load_startup_config(config_dir)
    app.state._project_gludd_dir = app.state._startup_config.get("project_gludd_dir")
    app.state._stats_start_time = time.monotonic()
    app.state._stats_requests = 0
    app.state._stats_responses = 0

    from general_ludd.hardware.probe import probe_hardware
    app.state._hardware = probe_hardware()

    _psk = os.environ.get("GLUDD_PSK", "")
    # P1 fix: FAIL-CLOSED by default when no PSK is set.
    # Non-public paths are DENIED (503) unless the operator explicitly opts out
    # via GLUDD_ALLOW_NO_AUTH=1 (development/test only).
    # GLUDD_REQUIRE_AUTH is kept for backward compat: when set it forces
    # fail-closed even if GLUDD_ALLOW_NO_AUTH=1 is also set.
    _allow_no_auth = os.environ.get("GLUDD_ALLOW_NO_AUTH", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    _require_auth_env = os.environ.get("GLUDD_REQUIRE_AUTH", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    # GLUDD_REQUIRE_AUTH overrides GLUDD_ALLOW_NO_AUTH — fail-closed wins.
    if _require_auth_env:
        _allow_no_auth = False
    _no_auth = not _psk
    # When no PSK: require_auth is True (fail-closed) unless the operator has
    # explicitly opted out with GLUDD_ALLOW_NO_AUTH=1.
    _require_auth = _no_auth and not _allow_no_auth
    app.state._psk = _psk
    app.state._no_auth = _no_auth
    app.state._require_auth = _require_auth
    app.state._allow_no_auth = _allow_no_auth
    if _no_auth and not _allow_no_auth:
        # Default fail-closed posture: LOUD warning that non-public paths will
        # be refused (503) until a PSK is configured.
        logger.warning(
            "SECURITY: GLUDD_PSK is not set — the daemon will REFUSE all "
            "non-public paths (503, fail-closed). Set GLUDD_PSK to enable auth. "
            "For development only, set GLUDD_ALLOW_NO_AUTH=1 to allow unauthenticated "
            "access (leaves the entire /admin surface open to any caller)."
        )
    elif _no_auth and _allow_no_auth:
        # Explicit dev opt-out: LOUD warning that auth is intentionally disabled.
        logger.warning(
            "SECURITY: GLUDD_PSK is not set and GLUDD_ALLOW_NO_AUTH=1 — the "
            "daemon is running with admin auth DISABLED (no_auth mode). The "
            "entire /admin surface is open to any caller that can reach the port. "
            "Set GLUDD_PSK to enable auth."
        )

    _PUBLIC_PATHS = {
        "/healthz", "/readyz", "/api/status", "/api/todos", "/api/human-todos",
        "/api/webmcp",
        "/docs", "/openapi.json", "/redoc",
    }

    # Receiver ingest paths use their own ingest-token auth (GLUDD_INGEST_TOKEN),
    # separate from the admin PSK. The PSK middleware must not challenge them so
    # the receiver router's internal auth runs instead (least-privilege: a leaked
    # ingest token cannot access /admin, a leaked PSK cannot push telemetry).
    _RECEIVER_PREFIXES = ("/v1/", "/ingest/")

    # AUTH-1: public access is (method, path)-aware. A path on the public list
    # is only public for SAFE, read-only methods (GET/HEAD/OPTIONS). The same
    # path under a mutating method (POST/PUT/PATCH/DELETE) is NOT public — e.g.
    # `GET /api/todos` lists todos without auth, but `POST /api/todos` CREATES a
    # todo and must go through the auth gate like any other write.
    _SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

    def _is_public(method: str, path: str) -> bool:
        if path.startswith(_RECEIVER_PREFIXES):
            return True
        if method.upper() not in _SAFE_METHODS:
            return False
        if path in _PUBLIC_PATHS or path == "/docs" or path.startswith("/docs/"):
            return True
        # /render/<name> is a public read-only HTML page (the renderer output).
        # It must be reachable without the admin PSK so operators can share a
        # rendered report URL. Only GET/HEAD/OPTIONS land here (mutating methods
        # were rejected above by the _SAFE_METHODS gate).
        return path.startswith("/render/")

    @app.middleware("http")
    async def auth_and_stats_middleware(request: Any, call_next: Any) -> Any:
        app.state._stats_requests += 1
        from general_ludd.observability.metrics_exporter import get_metrics_exporter
        metrics = get_metrics_exporter()
        metrics.counter_inc("gludd_http_requests_total", {"method": request.method})
        start = time.monotonic()
        path = request.url.path
        method = request.method
        if _no_auth and _require_auth and not _is_public(method, path):
            # A-3: fail-closed — no PSK configured but auth is required.
            from fastapi.responses import JSONResponse

            app.state._stats_responses += 1
            return JSONResponse(
                status_code=503,
                content={"error": "auth_required", "reason": "no PSK configured"},
            )
        if _psk:
            # A-2: never log any portion of the PSK — only whether it is configured.
            logger.debug(
                "Auth check: psk_configured=%s path=%s public=%s",
                True,
                path,
                _is_public(method, path),
            )
            if not _is_public(method, path):
                auth = request.headers.get("Authorization", "")
                # A-1: constant-time comparison via the shared check_bearer_token
                # helper (hmac.compare_digest) to prevent timing side-channels.
                from general_ludd.security.auth import check_bearer_token

                if not check_bearer_token(auth, _psk):
                    from fastapi.responses import JSONResponse

                    app.state._stats_responses += 1
                    return JSONResponse(status_code=401, content={"error": "unauthorized"})
        # When the daemon failed its lifespan init it runs _degraded: spend /
        # budget / dispatch enforcement infrastructure is inert. Mutating calls
        # to the dispatch + self-update + spend-configure surface must fail
        # closed (503) rather than silently bypass enforcement. Read-only calls
        # and probes still serve so operators can observe the degraded state.
        if method.upper() not in _SAFE_METHODS:
            _DEGRADED_GUARDED_PREFIXES = ("/api/dispatch", "/admin/self-update", "/api/spend")
            if path.startswith(_DEGRADED_GUARDED_PREFIXES):
                degraded_resp = _check_degraded(app)
                if degraded_resp is not None:
                    app.state._stats_responses += 1
                    return degraded_resp
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

    @app.get(
        "/healthz",
        summary="Liveness probe — daemon process is alive",
        description=(
            "Returns 200 with security-posture + budget flags when alive; "
            "503 on degraded startup. Public, no auth."
        ),
    )
    async def healthz() -> dict[str, Any]:
        degraded = getattr(app.state, "_degraded", None)
        # A-3: advertise the no-auth security posture so operators (and the
        # red-team test) can detect an unprotected daemon via the liveness probe.
        # The top-level `status` keeps its existing liveness semantics
        # ("healthy" unless catastrophic) so back-compat callers/tests are
        # unaffected; the security posture rides on the `no_auth`/`auth_degraded`
        # fields instead.
        no_auth = bool(getattr(app.state, "_no_auth", False))
        require_auth = bool(getattr(app.state, "_require_auth", False))
        allow_no_auth = bool(getattr(app.state, "_allow_no_auth", False))
        # auth_degraded = no PSK AND opted-out of fail-closed (open dev mode).
        # When no PSK and fail-closed is active, auth is not "degraded" in the
        # permissive sense — it is enforced; the 503 is the correct response.
        auth_degraded = no_auth and allow_no_auth
        budget_manager = getattr(app.state, "_budget_manager", None)
        budget_status = budget_manager.get_status() if budget_manager is not None else {}
        # SECURITY (gateway-health-budget P1): /healthz is an UNAUTHENTICATED
        # public path (in `_PUBLIC_PATHS`). Never expose the numeric
        # budget/spend figures (daily_spend, daily_limit, daily_pct,
        # per_todo_limit) returned by BudgetManager.get_status() to anonymous
        # callers — that leaks the operator's spend posture and remaining
        # headroom. Only the coarse boolean `budget_exhausted` is public; the
        # full numbers live behind the auth'd surface (/api/spend, dashboard).
        budget_exhausted = bool(budget_status.get("paused", False))
        # N1/C6: a dead/cancelled event-loop task after a successful startup must
        # NOT serve green — the daemon is alive but no longer processing work.
        # Mirror /readyz's check so /healthz also reports degraded in that case
        # (the `_degraded` flag alone only catches STARTUP failures).
        el_task = getattr(app.state, "_event_loop_task", None)
        if el_task is not None and el_task.done():
            degraded = degraded or (
                "event_loop_cancelled" if el_task.cancelled() else "event_loop_done"
            )
        if degraded:
            return {
                "status": "degraded",
                "reason": str(degraded)[:200],
                "no_auth": no_auth,
                "require_auth": require_auth,
                "allow_no_auth": allow_no_auth,
                "auth_degraded": auth_degraded,
                "budget_exhausted": budget_exhausted,
            }
        return {
            "status": "healthy",
            "no_auth": no_auth,
            "require_auth": require_auth,
            "allow_no_auth": allow_no_auth,
            "auth_degraded": auth_degraded,
            "budget_exhausted": budget_exhausted,
        }

    @app.get(
        "/readyz",
        summary="Readiness probe — daemon can accept work",
        description=(
            "200 when ready (not degraded, event loop alive); 503 otherwise. "
            "Public, no auth."
        ),
    )
    async def readyz() -> Any:
        """Readiness probe (N1/C6, W3.4): 503 when degraded or event-loop done/cancelled.

        Distinct from /healthz (liveness):
          - /healthz: process is alive (always 200 unless catastrophic)
          - /readyz: process can accept work (503 when degraded or loop finished)
        """
        from fastapi.responses import JSONResponse

        degraded = getattr(app.state, "_degraded", None)
        if degraded:
            return JSONResponse(
                status_code=503,
                content={"status": "degraded", "reason": str(degraded)[:200]},
            )
        # Check whether the event-loop task has completed/been cancelled
        el_task = getattr(app.state, "_event_loop_task", None)
        if el_task is not None and el_task.done():
            reason = "event_loop_cancelled" if el_task.cancelled() else "event_loop_done"
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": reason},
            )
        return {"status": "ready"}

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

    @app.get("/admin/dashboard/overview")
    async def admin_dashboard_overview() -> dict[str, Any]:
        provider: DashboardDataProvider | None = getattr(
            app.state, "_dashboard_data", None
        )
        if provider is not None:
            return await provider.get_overview()
        return {"error": "Dashboard data provider not initialized"}

    @app.get("/admin/daemon/stats")
    async def admin_daemon_stats() -> dict[str, Any]:
        import asyncio
        import os

        import psutil

        uptime = time.monotonic() - app.state._stats_start_time

        # AB-4: psutil.Process().memory_info() issues blocking OS syscalls; run
        # it off the event loop so the async stats handler does not stall the
        # daemon under load.
        def _sample_rss_mb() -> float:
            proc = psutil.Process(os.getpid())
            return proc.memory_info().rss / (1024 * 1024)

        mem_mb = await asyncio.to_thread(_sample_rss_mb)
        return {
            "pid": os.getpid(),
            "requests_total": app.state._stats_requests,
            "responses_total": app.state._stats_responses,
            "memory_mb": round(mem_mb, 2),
            "uptime_s": round(uptime, 2),
        }

    # Lazy to avoid circular import: routers/*.py import from daemon at module level
    from general_ludd.routers import (
        accounting,
        ansible,
        benchmark,
        compute,
        embeddings,
        environment,
        facts,
        features,
        filestore,
        human_todos,
        integrity,
        maintenance,
        mcp,
        messages,
        models,
        ornith,
        processes,
        projects,
        quantization,
        reload,
        remediation,
        render,
        schedule,
        security,
        self_improve,
        self_update,
        signing,
        skills,
        slurm,
        spend,
        todos,
        webmcp,
        worktree,
    )
    from general_ludd.routers import (
        dispatch as dispatch_router,
    )

    webmcp.register(app, daemon_state)
    todos.register(app, daemon_state)
    messages.register(app, daemon_state)
    accounting.register(app, daemon_state)
    facts.register(app, daemon_state)
    environment.register(app, daemon_state)
    embeddings.register(app, daemon_state)
    features.register(app, daemon_state)
    schedule.register(app, daemon_state)
    models.register(app, daemon_state)
    benchmark.register(app, daemon_state)
    mcp.register(app, daemon_state)
    skills.register(app, daemon_state)
    compute.register(app, daemon_state)
    processes.register(app, daemon_state)
    filestore.register(app, daemon_state)
    human_todos.register(app, daemon_state)
    integrity.register(app, daemon_state)
    signing.register(app, daemon_state)
    security.register(app, daemon_state)
    projects.register(app, daemon_state)
    quantization.register(app, daemon_state)
    reload.register(app, daemon_state)
    worktree.register(app, daemon_state)
    ansible.register(app, daemon_state)
    slurm.register(app, daemon_state)
    self_improve.register(app, daemon_state)
    self_update.register(app, daemon_state)
    maintenance.register(app, daemon_state)
    remediation.register(app, daemon_state)
    ornith.register(app, daemon_state)
    # Playbook web renderer (Phase 1): /api/renderers (PSK) + /render/<name> (public).
    # Registry discovery is best-effort — a missing playbooks/renderers/ dir must
    # not crash daemon startup (the router serves a 503 in that case).
    try:
        from general_ludd.ansible.runner import _resolve_playbooks_root
        from general_ludd.renderers.cache import RendererCache
        from general_ludd.renderers.registry import RendererRegistry

        _bundled = _resolve_playbooks_root() / "renderers"
        _renderer_registry = RendererRegistry(bundled_dir=_bundled)
        _renderer_registry.discover()
        app.state._renderer_registry = _renderer_registry
        app.state._renderer_cache = RendererCache(ttl_default=30)
    except Exception as exc:
        logger.warning("renderer subsystem unavailable: %s", exc)
    render.register(app, daemon_state)
    # Construct the receiver buffer BEFORE registering the router: the router's
    # routes close over the buffer at register-time (app-creation), which runs
    # before the lifespan. If we left this to the lifespan only, the routes would
    # capture a throwaway default buffer and the configured 10k/REJECT/3600 buffer
    # would never be the one ingest writes into. Idempotent: the lifespan's
    # _get_or_create_extended_subsystems reuses this same instance.
    if getattr(app.state, "_receiver_buffer", None) is None:
        from general_ludd.receiver.buffer import OverflowPolicy, ReceiverBuffer
        app.state._receiver_buffer = ReceiverBuffer(
            maxlen=10_000,
            overflow=OverflowPolicy.REJECT,
            retention_s=3600,
        )
    daemon_state["receiver_buffer"] = app.state._receiver_buffer
    from general_ludd.receiver import router as receiver_router
    receiver_router.register(app, daemon_state)
    # Dynamic dispatch router — handlers close over ``app`` and look up
    # subsystems lazily at call time so they resolve against the live
    # lifespan-initialised state rather than the not-yet-started state at
    # app-creation time.  W: event-loop-wiring (#26).
    from general_ludd.daemon_wiring import make_mcp_handler, make_role_handler, make_skill_handler

    async def _lazy_mcp_handler(name: str, args: dict[str, Any]) -> Any:
        mcp_client = getattr(app.state, "_mcp_client", None)
        h = make_mcp_handler(mcp_client)
        if h is None:
            raise RuntimeError("MCP client not available")
        return await h(name, args)

    def _lazy_skill_handler(name: str, args: dict[str, Any]) -> Any:
        skill_registry = getattr(app.state, "_skill_registry", None)
        h = make_skill_handler(skill_registry)
        if h is None:
            raise RuntimeError("SkillRegistry not available")
        return h(name, args)

    async def _lazy_role_handler(name: str, args: dict[str, Any]) -> Any:
        agent_dispatcher = getattr(app.state, "_agent_dispatcher", None)
        h = make_role_handler(agent_dispatcher)
        if h is None:
            raise RuntimeError("AgentDispatcher not available")
        return await h(name, args)

    dispatch_router.register(
        app,
        daemon_state,
        role_handler=_lazy_role_handler,
        mcp_handler=_lazy_mcp_handler,
        skill_handler=_lazy_skill_handler,
        collection_handler=None,  # TODO(integration): wire to collection loader — no loader exists
    )
    spend.register(app, daemon_state)
    from general_ludd.routers import coordination as _coord_router
    _coord_router.register(app, daemon_state)

    from general_ludd.routers import stream as _stream_router
    _stream_router.register(app, daemon_state)

    from general_ludd.routers.observe import wire_observability

    _uc = (getattr(app.state, "_startup_config", {}) or {}).get("user_config")
    _connector_cfg = getattr(_uc, "connectors", None) if _uc else None
    wire_observability(app, daemon_state, _connector_cfg)

    return app
