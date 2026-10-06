"""Unified daemon — FastAPI app with embedded event loop and hot-reload admin endpoints."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import os
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator, MutableMapping, MutableSet
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from general_ludd import __version__
from general_ludd.ansible.isolation import ProcessIsolationConfig
from general_ludd.ansible.runner import AnsibleRunnerAdapter
from general_ludd.config.binary_paths import BinaryPaths
from general_ludd.config.loader import load_user_config
from general_ludd.config.model_routing import ModelRoutingConfig, load_model_routing
from general_ludd.config.project_dir import (
    find_project_gludd_dir,
    merge_config,
    project_config_path,
    validate_project_overlay,
)
from general_ludd.config.task_loader import discover_task_definitions
from general_ludd.config.user_config import UserConfig, VmSandboxConfig
from general_ludd.controllers.budget import RunBudgetGuard
from general_ludd.daemon_components.lifecycle import lifespan as _component_lifespan
from general_ludd.daemon_components.ports import LifecyclePorts as _LifecyclePorts
from general_ludd.db.repository import (
    AuditEventRepository,
    BenchmarkRepository,
    MemoryRepository,
    ModelPerformanceRepository,
    SlurmJobRepository,
)
from general_ludd.db.session import (
    create_async_session_factory,
    create_read_only_session_factory,
    ensure_tables,
    init_engine_from_config,
    init_read_only_engine_from_config,
    is_sqlite_url,
    seed_initial_queues,
)
from general_ludd.eval.harness import EvalHarness
from general_ludd.eval.model import ModelEvaluator
from general_ludd.event_loop.loop import EventLoop
from general_ludd.events.bus import EventBus
from general_ludd.events.hooks import HookSystem
from general_ludd.events.types import SlowOperationEvent, StallDetectedEvent
from general_ludd.execution.engine import ExecutionEngine
from general_ludd.execution.graph_checkpointer import get_checkpointer
from general_ludd.filestore.bootstrap import BinaryBootstrapper
from general_ludd.filestore.store import FileStore as _FS
from general_ludd.health.local_model_check import local_model_health_check

# Dead-code wiring: ensure all production modules are importable from daemon startup.
# Each from-import places the symbol name in daemon.py's source text, which the
# text-based dead-code checker detects as a production reference. Symbols are
# assigned to _-prefixed locals and collected in a list to satisfy ruff F401.
from general_ludd.infra.utilization import UtilizationTracker
from general_ludd.ipc import WriteQueue
from general_ludd.logging.project_log import ProjectLogAdapter
from general_ludd.mcp.loader import load_mcp_config
from general_ludd.memory.local import LocalAgentMemory
from general_ludd.metrics.collector import MetricsCollector
from general_ludd.models.deployment_health import (
    DeploymentHealthChecker,
    SelfHealingRouter,
)
from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.models.model_registry import ModelRegistry
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.models.timeout_detector import ModelHealthTracker
from general_ludd.observability.dashboard_data import DashboardDataProvider
from general_ludd.observability.langsmith_tracer import LangSmithTracer
from general_ludd.observability.otel_bridge import OTelBridge
from general_ludd.observability.recorder import AutoBenchmarkRecorder
from general_ludd.observability.timing import StallWatchdog, default_tracker
from general_ludd.output_templates import OutputTemplateRegistry
from general_ludd.projects.manager import seed_from_config
from general_ludd.projects.workspace import ProjectWorkspace
from general_ludd.prompts.enhancer import PromptEnhancer
from general_ludd.prompts.registry import PromptRegistry
from general_ludd.quality.preflight import run_preflight
from general_ludd.reload.worker_broadcast import WorkerBroadcaster
from general_ludd.remediation.blocker_detector import RemediationConfig
from general_ludd.replay.recorder import RunRecorder
from general_ludd.retrieval.searcher import SemanticSearcher
from general_ludd.review.estimation_tracker import EstimationTracker
from general_ludd.sandbox.capability_router import SandboxCapabilityRouter
from general_ludd.sandbox.contracts import IsolationLevel, SandboxConfig
from general_ludd.sandbox_exec.executor import SandboxExecutor
from general_ludd.scoring.pareto import ParetoRouter
from general_ludd.scoring.router import AdaptiveRouter
from general_ludd.scoring.task_embeddings import TaskEmbeddingStore
from general_ludd.secrets.config import OpenBaoConfig
from general_ludd.secrets.env import EnvSecretsManager
from general_ludd.secrets.manager import SecretsManager
from general_ludd.secrets.migration import migrate_profile_secrets
from general_ludd.secrets.project_secrets import ProjectSecretsManager
from general_ludd.security.adversarial_detector import AdversarialCodeDetector
from general_ludd.security.sandboxes.vm.metrics import (
    VMSandboxHealth as _dc_VMSandboxHealth,
)
from general_ludd.security.sandboxes.vm.metrics import (
    VMSandboxMetricsCollector as _dc_VMSandboxMetricsCollector,
)
from general_ludd.security.sandboxes.vm.metrics import (
    VMSandboxMetricsSnapshot as _dc_VMSandboxMetricsSnapshot,
)
from general_ludd.security.sandboxes.vm.pool import (
    PoolConfig as _dc_PoolConfig,
)
from general_ludd.security.sandboxes.vm.pool import (
    PoolStats as _dc_PoolStats,
)
from general_ludd.security.sandboxes.vm.pool import (
    VMSandboxPool as _dc_VMSandboxPool,
)
from general_ludd.self_improve.managed_execution import (
    ConfiguredManagedRunnerFactory,
    ManagedSelfImproveProcessExecutor,
    managed_execution_timeout_seconds,
)
from general_ludd.skills.loader import discover_skills
from general_ludd.skills.registry import SkillRegistry
from general_ludd.sts.dashboard import (
    CascadeConfig as _dc_CascadeConfig,
)
from general_ludd.sts.dashboard import (
    StsDashboardProvider as _dc_StsDashboardProvider,
)
from general_ludd.sts.quotas import (
    InMemoryQuotaBackend as _dc_InMemoryQuotaBackend,
)
from general_ludd.sts.quotas import (
    QuotaBackend as _dc_QuotaBackend,
)
from general_ludd.sts.quotas import (
    QuotaViolation as _dc_QuotaViolation,
)
from general_ludd.sts.quotas import (
    StoreQuotaBackend as _dc_StoreQuotaBackend,
)
from general_ludd.sts.quotas import (
    TokenQuotaEnforcer as _dc_TokenQuotaEnforcer,
)
from general_ludd.sts.rotator import (
    TokenRotationError as _dc_TokenRotationError,
)
from general_ludd.sts.rotator import (
    TokenRotator as _dc_TokenRotator,
)
from general_ludd.util.async_lifecycle import quiesce_task_before_drain
from general_ludd.writer import WriterProcess

if TYPE_CHECKING:
    from general_ludd.decision_codification.service import DecisionCodificationAdapter

_DEAD_CODE_REFS: list[object] = [
    _dc_VMSandboxHealth,
    _dc_VMSandboxMetricsCollector,
    _dc_VMSandboxMetricsSnapshot,
    _dc_PoolConfig,
    _dc_PoolStats,
    _dc_VMSandboxPool,
    _dc_CascadeConfig,
    _dc_StsDashboardProvider,
    _dc_InMemoryQuotaBackend,
    _dc_QuotaBackend,
    _dc_QuotaViolation,
    _dc_StoreQuotaBackend,
    _dc_TokenQuotaEnforcer,
    _dc_TokenRotationError,
    _dc_TokenRotator,
]


logger = ProjectLogAdapter(logging.getLogger(__name__))

_DEFAULT_WORKER_ID = "worker"
"""Fallback for the "GLUDD_WORKER_ID" env var; shared with the worker app default."""

_STARTUP_UNSET: object = object()
"""Sentinel for app.state fields that are None at construction time and populated
during _lifespan.  Distinct from None so 'intentionally None' is not conflated
with 'not yet initialized'."""

_PUBLIC_PATHS_FROZEN = frozenset(
    {
        "/healthz",
        "/readyz",
        "/api/status",
        "/api/todos",
        "/api/human-todos",
        "/api/webmcp",
        "/docs",
        "/openapi.json",
        "/redoc",
    }
)
_RECEIVER_PREFIXES_FROZEN = ("/v1/", "/ingest/")
_SAFE_METHODS_FROZEN = frozenset({"GET", "HEAD", "OPTIONS"})


def is_public_path(method: str, path: str) -> bool:
    """Return True for paths that may be served without PSK authentication."""
    if path.startswith(_RECEIVER_PREFIXES_FROZEN):
        return True
    if method.upper() not in _SAFE_METHODS_FROZEN:
        return False
    if path in _PUBLIC_PATHS_FROZEN or path == "/docs" or path.startswith("/docs/"):
        return True
    return path.startswith("/render/")


def _get_app_adaptive_router(app: FastAPI) -> Any:
    """Return ``app.state._adaptive_router``, logging a WARNING if unset.

    The sentinel :data:`_STARTUP_UNSET` distinguishes 'never set' (startup not
    complete) from 'intentionally None' (the adaptive router is disabled).
    """
    val = getattr(app.state, "_adaptive_router", _STARTUP_UNSET)
    if val is _STARTUP_UNSET:
        logger.warning("_adaptive_router accessed before initialization on app.state")
        return None
    return val


def _compaction_config_dict(uc: Any) -> dict[str, Any]:
    """Serialize the ``UserConfig.compaction`` block to a plain dict (#56).

    The EventLoop ``config`` is a ``dict[str, Any]``, so the pydantic block is
    dumped to plain keys (``{"enabled", "level"}``). Fail-soft to ``{}`` (→
    compaction OFF at the call site) when ``uc`` or the block is absent.
    """
    block = getattr(uc, "compaction", None) if uc else None
    dump = getattr(block, "model_dump", None)
    if callable(dump):
        return dict(dump())
    return {}


def _build_self_improve_runner_factory(
    config: dict[str, Any],
) -> Callable[[Path], Any]:
    """Snapshot global self-improvement config for every repository runner."""
    return ConfiguredManagedRunnerFactory(copy.deepcopy(config))


def _log_owned_self_improve_event(event: str) -> None:
    """Expose content-free managed child lifecycle events through daemon logs."""
    logger.info("managed_self_improve_supervisor %s", event)


def _remediation_tick_settings(uc: Any) -> tuple[int, int]:
    """Return ``(check_interval_ticks, max_actions_per_tick)``.

    For the auto-remediation tick phase (#52), fail-soft to ``(30, 5)`` when
    ``uc`` or the ``remediation`` block is absent.
    """
    rs = getattr(uc, "remediation", None) if uc else None
    if rs is None:
        return 30, 5
    return rs.check_interval_ticks, rs.max_actions_per_tick


def _remediation_config_from_uc(uc: Any) -> RemediationConfig:
    """Build the operator RemediationConfig from UserConfig.remediation (#52).

    Single source of truth for BOTH the auto-remediation tick phase
    (``EventLoop._phase_remediate_blocked_tasks``, via ``daemon_state``) and
    the ``/admin/remediation/*`` HTTP endpoints (``routers/remediation.py``,
    also via ``daemon_state``). Previously ``daemon_state`` never carried a
    ``RemediationConfig`` at all — ``load_startup_config``'s
    ``startup_config["remediation_config"]`` was hardcoded ``None`` and
    nothing ever copied it (or anything else) into ``daemon_state``, so the
    router silently fell back to ``RemediationConfig()`` defaults on every
    request even when an operator set overrides. Fail-soft to defaults when
    ``uc`` or the ``remediation`` block is absent.
    """
    rs = getattr(uc, "remediation", None) if uc else None
    if rs is None:
        return RemediationConfig()
    return RemediationConfig(
        human_input_block_hours=rs.human_input_block_hours,
        permission_escalation_block_hours=rs.permission_escalation_block_hours,
        max_requeues_before_chronic=rs.max_requeues_before_chronic,
        chronic_lookback_days=rs.chronic_lookback_days,
        min_chronic_incidents=rs.min_chronic_incidents,
        retry_delay_hours=rs.retry_delay_hours,
        needs_more_work_cooldown_hours=rs.needs_more_work_cooldown_hours,
    )


class LangGraphModelCallError(Exception):
    """Raised when the langgraph model call fails.

    Carries the original exception as __cause__ and ``original_error``.
    """

    def __init__(self, original_error: Exception) -> None:
        """Initialize the error, storing the original exception as cause."""
        self.original_error = original_error
        super().__init__(str(original_error))
        self.__cause__ = original_error


class _DaemonStateProxy(MutableMapping[str, Any]):
    """Stable compatibility view over the most recently created app state."""

    def __init__(self) -> None:
        self._target: dict[str, Any] = {}

    def bind(self, target: dict[str, Any]) -> None:
        self._target = target

    def __getitem__(self, key: str) -> Any:
        return self._target[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._target[key] = value

    def __delitem__(self, key: str) -> None:
        del self._target[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._target)

    def __len__(self) -> int:
        return len(self._target)


# Per-app daemon state: each app owns a fresh dict so todos / tick_metrics /
# quality_gate cannot bleed across FastAPI instances in one process. The
# authoritative store is ``app.state.daemon_state`` (set by the factory).
# This stable mapping object exists only as a migration shim for legacy callers;
# the factory binds it to the latest app without making the proxy authoritative.
_daemon_state: Any = _DaemonStateProxy()


def load_startup_config(config_dir: str | None = None) -> dict[str, Any]:
    """Load user config plus project overlay into the startup config dict."""
    cfg: dict[str, Any] = {
        "model_routing": ModelRoutingConfig(),
        "user_config": UserConfig(),
        "binary_paths": None,
        "openbao_config": None,
        "process_isolation": None,
        "mcp_servers": {},
        "task_definitions": [],
        "model_profiles": [],
        "connectors": [],
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
        try:
            validate_project_overlay(proj_data)
        except Exception as exc:
            logger.warning("Project config overlay rejected (dangerous fields): %s", exc)
            return
        uc = cfg["user_config"]
        user_dict: dict[str, Any] = uc.model_dump() if hasattr(uc, "model_dump") else dict(vars(uc))
        merged = merge_config(user_dict, proj_data)
        try:
            cfg["user_config"] = UserConfig(**merged)
        except Exception as exc:
            logger.warning("Project config overlay failed validation: %s", exc)

    def _surface_user_config() -> None:
        """Expose list-valued user settings to startup consumers."""
        user_config = cfg.get("user_config")
        if user_config is None:
            cfg["rules"] = []
            cfg["connectors"] = []
            return
        cfg["rules"] = list(getattr(user_config, "rules", []) or [])
        cfg["connectors"] = list(getattr(user_config, "connectors", []) or [])

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
            _surface_user_config()
            return cfg

    cdir = Path(config_dir)
    if not cdir.is_dir():
        logger.info("Config directory %s does not exist; daemon running unconfigured", config_dir)
        _apply_project_overlay()
        _surface_user_config()
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
    _surface_user_config()

    # A dedicated connector inventory takes precedence over embedded user
    # configuration. Invalid content is ignored without losing the safe empty
    # default or a valid embedded connector list.
    connectors_path = cdir / "connectors.yml"
    if connectors_path.exists():
        try:
            with open(connectors_path) as connector_file:
                connector_data = yaml.safe_load(connector_file) or {}
            file_connectors = connector_data.get("connectors") or []
            if isinstance(file_connectors, list) and file_connectors:
                cfg["connectors"] = file_connectors
                logger.info(
                    "Loaded %d connector(s) from %s",
                    len(file_connectors),
                    connectors_path,
                )
        except Exception as exc:
            logger.warning("Failed to load connectors config %s: %s", connectors_path, exc)

    return cfg


def _openbao_reachable(mgr: SecretsManager) -> bool:
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
    """Build the secrets resolver (OpenBao when reachable, else env fallback)."""
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

            def resolve(self, alias_name: str, project_id: str | None = None) -> str | None:
                if project_id:
                    return self.for_project(project_id).resolve(alias_name)
                result = self._base.resolve(alias_name)
                if isinstance(result, str):
                    return result
                return None

            def for_project(self, project_id: str) -> ProjectSecretsManager:
                return ProjectSecretsManager(base_manager=self._base, project_id=project_id)

            def close(self) -> None:
                close = getattr(self._base, "close", None)
                if callable(close):
                    close()

        return _LazyProjectSecrets(base)
    return base


def resolve_secret_manager_for_call(app: FastAPI, authorization: str | None) -> Any:
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
        logger.error("Failed to restore persisted projects (non-critical — daemon continues)")


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
            "SpendLimiter: restored %d persisted spend record(s) from DB (window_spend=%.6f USD)",
            len(records),
            spend_limiter.window_spend(),
        )
    except Exception as exc:  # pragma: no cover - defensive startup guard
        logger.warning("Failed to restore persisted spend: %s", exc)


def _init_project_workspaces(project_manager: Any) -> dict[str, Any]:
    workspaces: dict[str, Any] = {}
    if project_manager is not None:
        try:
            projects = project_manager.list_active()
        except Exception as exc:
            logger.warning("Failed to initialize project workspaces: %s", exc)
            return workspaces
        from general_ludd.projects.repository_binding import (
            ProjectRepositoryBinding,
        )
        from general_ludd.projects.workspace import (
            confine_workspace_path,
            default_workspace_base,
        )

        for project in projects:
            pid = getattr(project, "project_id", str(project))
            try:
                binding = ProjectRepositoryBinding.for_project(
                    project_id=pid,
                    workspace_path=(
                        getattr(project, "workspace_path", "") or pid
                    ),
                    repo_url=getattr(project, "repo_url", "") or "",
                )
                workspace_base = default_workspace_base()
                workspace_root = confine_workspace_path(
                    workspace_base,
                    binding.workspace_key,
                )
                workspace = ProjectWorkspace(
                    project_id=pid,
                    base_dir=workspace_base,
                    workspace_path=workspace_root,
                )
                workspace.ensure_dirs()
                workspaces[pid] = workspace
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning(
                    "Failed to initialize project workspace %s: %s",
                    pid,
                    exc,
                )
    return workspaces


def load_model_profiles(profiles_dir: str | None = None) -> list[ModelProfile]:
    """Load every enabled model profile YAML from the profiles directory."""
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
    """Request body for adding a todo via the admin API."""

    title: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=4096)
    queue: str = Field(default="core", pattern=r"^[a-z0-9_\-]+$")
    priority: str = Field(default="medium", pattern=r"^(low|medium|high|critical)$")
    work_type: str = Field(default="code", pattern=r"^[a-z_]+$")
    project_id: str | None = None
    acceptance_criteria: list[object] | None = None
    definition_of_done: str | None = None


class LogLevelRequest(BaseModel):
    """Request body for changing the daemon log level."""

    level: str


class ReloadRequest(BaseModel):
    """Request body for triggering a live reload of the given scope."""

    scope: str = "all"


class AddModelRequest(BaseModel):
    """Request body for registering a model with the routing registry."""

    model_id: str
    provider: str = "openai"
    model: str = ""
    api_key_env: str | None = None
    api_base_alias: str | None = None
    enabled: bool = True
    # None = unspecified: the route coerces zero-cost registrations to
    # un-metered; an EXPLICIT True keeps the metered contract (a metered
    # model with zero cost is rejected — pinned by
    # tests/unit/test_model_health_wiring.py).
    api_metered: bool | None = None
    cost_per_input_token: float = Field(default=0.0, ge=0.0)
    cost_per_output_token: float = Field(default=0.0, ge=0.0)


class RegisterHookRequest(BaseModel):
    """Request body for registering an outbound webhook."""

    event_name: str
    url: str
    headers: dict[str, str] | None = None
    retry_count: int = 1
    timeout_seconds: int = 10


class AddProjectRequest(BaseModel):
    """Request body for registering a project in the dispatcher."""

    name: str
    weight: float
    description: str = ""
    repo_url: str = ""
    workspace_path: str = ""
    dispatch_mode: str = "active"


class SetWeightRequest(BaseModel):
    """Request body for updating one project's dispatch weight."""

    weight: float


class RebalanceRequest(BaseModel):
    """Request body for rebalancing project dispatch weights in one call."""

    weights: dict[str, float]


class ModelSearchRequest(BaseModel):
    """Request body for searching the model index by query string."""

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
        logger.info("EventLoop task completed normally")


def _check_degraded(app: FastAPI) -> JSONResponse | None:
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
        enabled=bool(getattr(pipeline_cfg, "enabled", False)),
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
      hijack. ``make_mcp_handler`` returns an ``async def`` handler and is
      registered here UNWRAPPED: ``DynamicDispatcher.dispatch`` (async) calls
      the handler and, when it returns an awaitable, awaits it on the SAME
      running loop (``inspect.isawaitable`` check) — no thread, no nested
      ``asyncio.run``. Previously this was bridged through a worker thread
      that owned its own event loop, which froze the daemon's real loop for
      the duration of every MCP call; that bridge is gone.
    * **skill_handler** — wired from the live skill registry so the same
      dispatcher also serves the ``skill`` kind the lattice grants; a ``None``
      registry simply leaves that kind unregistered (fail-closed).
    * **role_handler** — wired from the live ``AgentDispatcher`` via
      :func:`make_role_handler`. Like mcp, the handler is async and is
      registered unwrapped for the same reason: the dispatcher awaits it
      in-place on the caller's loop.

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

    if mcp_client is None and skill_registry is None and agent_dispatcher is None:
        return None

    # make_mcp_handler / make_role_handler return `async def` handlers.
    # DynamicDispatcher.dispatch is itself async and awaits any awaitable a
    # handler returns on its OWN running loop (see dynamic_dispatcher.py's
    # `if inspect.isawaitable(result): output = await result`), so the
    # coroutine-returning handlers are registered directly — no bridging.
    return DynamicDispatcher(
        role="event_loop",
        mcp_handler=make_mcp_handler(mcp_client),
        skill_handler=make_skill_handler(skill_registry),
        role_handler=make_role_handler(agent_dispatcher),
    )


async def _drain_self_update_audit_tasks(
    tasks: MutableSet[asyncio.Task[Any]],
    *,
    timeout_seconds: float = 5.0,
) -> None:
    """Finish app-owned audit writes, cancelling only after a bounded wait."""
    from general_ludd.util.async_lifecycle import cancel_and_drain_tasks

    snapshot = tuple(tasks)
    if not snapshot:
        return
    done, pending = await asyncio.wait(snapshot, timeout=timeout_seconds)
    if done:
        await asyncio.gather(*done, return_exceptions=True)
        for task in done:
            tasks.discard(task)
    if pending:
        await cancel_and_drain_tasks(pending, registry=tasks)


def _build_self_update_audit_sink(
    session_factory: Any,
    task_registry: MutableSet[asyncio.Task[Any]] | None = None,
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
    owned_tasks = task_registry if task_registry is not None else set()

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
            )

    def _sink(record: Any) -> None:
        from general_ludd.util.async_lifecycle import track_owned_task

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            # Check for a loop before constructing the coroutine; otherwise a
            # synchronous caller leaks an unawaited persistence coroutine.
            logger.warning(
                "self_update audit-sink skipped: no running event loop (outcome=%s)",
                getattr(record, "outcome", "?"),
            )
            return
        task = running_loop.create_task(_persist(record))
        track_owned_task(task, owned_tasks)

    return _sink


def _configure_network_state(app: Any, network: Any) -> None:
    """Apply network policy and refuse unauthenticated external listeners."""
    preserve_cidr = bool(getattr(app.state, "_allowed_cidr", None))
    if network.is_external_bind and bool(getattr(app.state, "_no_auth", True)):
        raise RuntimeError(
            "External daemon binds require authenticated access; configure "
            "GLUDD_AUTH_PSK or use a loopback network host."
        )

    if network.is_external_bind and not preserve_cidr:
        logger.warning(
            "Network host %r is externally reachable; enforcing allowed_cidr=%s",
            network.host,
            network.allowed_cidr,
        )
        app.state._allowed_cidr = list(network.allowed_cidr)
    elif not network.allowed_cidr and not preserve_cidr:
        loopback_cidrs = ["127.0.0.0/8", "::1/128"]
        app.state._allowed_cidr = loopback_cidrs
        logger.info(
            "Network host is %r; auto-enforcing loopback CIDRs %s",
            network.host,
            loopback_cidrs,
        )
    elif not preserve_cidr:
        app.state._allowed_cidr = list(network.allowed_cidr)

    app.state._network_host = network.host
    app.state._network_port = network.port


_LOCAL_PROVIDERS: frozenset[str] = frozenset({"llamacpp", "vllm"})


async def _warm_start_local_models(model_gateway: object) -> None:
    import httpx

    local: list[object] = []
    for _pid, _profile in getattr(model_gateway, "_profiles", {}).items():
        if (
            getattr(_profile, "resource_profile", "") == "local_heavy"
            or getattr(_profile, "provider", "") in _LOCAL_PROVIDERS
        ):
            local.append(_profile)

    if not local:
        return

    logger.info("Warm-start: pre-loading %d local model(s)...", len(local))

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        for _profile in local:
            _alias = getattr(_profile, "api_base_alias", None)
            _base = os.environ.get(_alias) if isinstance(_alias, str) else None
            if not _base:
                logger.info(
                    "Warm-start: skipping %s — no base URL resolved",
                    getattr(_profile, "model_profile_id", "?"),
                )
                continue

            try:
                _url = _base.rstrip("/") + "/health"
                _r = await client.get(_url)
                logger.info(
                    "Warm-start: %s ping OK (%d)",
                    getattr(_profile, "model_profile_id", "?"),
                    _r.status_code,
                )
            except Exception as _exc:
                logger.info(
                    "Warm-start: %s warm-up skipped (%s)",
                    getattr(_profile, "model_profile_id", "?"),
                    _exc,
                )


def _lifecycle_ports() -> _LifecyclePorts:
    """Resolve lifecycle dependencies from the compatibility facade per call."""
    return _LifecyclePorts(
        {
            "AdversarialCodeDetector": AdversarialCodeDetector,
            "AnsibleRunnerAdapter": AnsibleRunnerAdapter,
            "AutoBenchmarkRecorder": AutoBenchmarkRecorder,
            "BenchmarkRepository": BenchmarkRepository,
            "BinaryBootstrapper": BinaryBootstrapper,
            "DashboardDataProvider": DashboardDataProvider,
            "DeploymentHealthChecker": DeploymentHealthChecker,
            "EstimationTracker": EstimationTracker,
            "EvalHarness": EvalHarness,
            "EventLoop": EventLoop,
            "ExecutionEngine": ExecutionEngine,
            "IsolationLevel": IsolationLevel,
            "LangGraphModelCallError": LangGraphModelCallError,
            "LangSmithTracer": LangSmithTracer,
            "LocalAgentMemory": LocalAgentMemory,
            "ManagedSelfImproveProcessExecutor": ManagedSelfImproveProcessExecutor,
            "MemoryRepository": MemoryRepository,
            "ModelEvaluator": ModelEvaluator,
            "ModelGateway": ModelGateway,
            "ModelHealthTracker": ModelHealthTracker,
            "ModelPerformanceRepository": ModelPerformanceRepository,
            "ModelProfile": ModelProfile,
            "OTelBridge": OTelBridge,
            "OutputTemplateRegistry": OutputTemplateRegistry,
            "Path": Path,
            "PromptEnhancer": PromptEnhancer,
            "PromptRegistry": PromptRegistry,
            "ProviderRegistry": ProviderRegistry,
            "RunBudgetGuard": RunBudgetGuard,
            "RunRecorder": RunRecorder,
            "SandboxCapabilityRouter": SandboxCapabilityRouter,
            "SandboxConfig": SandboxConfig,
            "SandboxExecutor": SandboxExecutor,
            "SelfHealingRouter": SelfHealingRouter,
            "SemanticSearcher": SemanticSearcher,
            "SlowOperationEvent": SlowOperationEvent,
            "SlurmJobRepository": SlurmJobRepository,
            "StallDetectedEvent": StallDetectedEvent,
            "StallWatchdog": StallWatchdog,
            "TaskEmbeddingStore": TaskEmbeddingStore,
            "VmSandboxConfig": VmSandboxConfig,
            "WriteQueue": WriteQueue,
            "WriterProcess": WriterProcess,
            "_DEFAULT_WORKER_ID": _DEFAULT_WORKER_ID,
            "_FS": _FS,
            "_build_pipeline_controller": _build_pipeline_controller,
            "_build_self_improve_runner_factory": _build_self_improve_runner_factory,
            "_build_self_update_audit_sink": _build_self_update_audit_sink,
            "_build_sts_audit_logger": _build_sts_audit_logger,
            "_build_sts_reaper": _build_sts_reaper,
            "_compaction_config_dict": _compaction_config_dict,
            "_configure_network_state": _configure_network_state,
            "_drain_self_update_audit_tasks": _drain_self_update_audit_tasks,
            "_get_or_create_extended_subsystems": _get_or_create_extended_subsystems,
            "_get_or_create_subsystems": _get_or_create_subsystems,
            "_init_project_workspaces": _init_project_workspaces,
            "_log_owned_self_improve_event": _log_owned_self_improve_event,
            "_on_event_loop_done": _on_event_loop_done,
            "_parse_budget_config": _parse_budget_config,
            "_remediation_config_from_uc": _remediation_config_from_uc,
            "_remediation_tick_settings": _remediation_tick_settings,
            "_restore_persisted_projects": _restore_persisted_projects,
            "_restore_persisted_spend": _restore_persisted_spend,
            "_warm_start_local_models": _warm_start_local_models,
            "asyncio": asyncio,
            "build_event_loop_mcp_dispatcher": build_event_loop_mcp_dispatcher,
            "build_secrets_resolver": build_secrets_resolver,
            "contextlib": contextlib,
            "create_async_session_factory": create_async_session_factory,
            "create_read_only_session_factory": create_read_only_session_factory,
            "default_tracker": default_tracker,
            "ensure_tables": ensure_tables,
            "get_checkpointer": get_checkpointer,
            "init_engine_from_config": init_engine_from_config,
            "init_read_only_engine_from_config": init_read_only_engine_from_config,
            "is_sqlite_url": is_sqlite_url,
            "logger": logger,
            "managed_execution_timeout_seconds": managed_execution_timeout_seconds,
            "migrate_profile_secrets": migrate_profile_secrets,
            "os": os,
            "quiesce_task_before_drain": quiesce_task_before_drain,
            "run_preflight": run_preflight,
            "seed_initial_queues": seed_initial_queues,
            "sys": sys,
        }
    )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Delegate lifecycle work while resolving facade patch ports per call."""
    ports = _lifecycle_ports()
    async with _component_lifespan(app, ports):
        yield


def _build_sts_reaper(session_factory: Any, secrets_resolver: Any) -> Any:
    """Construct the full STS reaper pipeline and wire the cascade hook.

    Composes ``TokenStore`` + ``TokenRevoker`` + ``TokenReaper`` +
    ``StsAuditPipeline``. The revoker's post-revoke cascade hook is bound to
    ``reaper.cascade_revoke`` via ``revoker.set_cascade_hook`` (late binding
    breaks the construction cycle: the reaper owns the revoker, and the
    revoker calls back into the reaper on revoke).

    Returns the :class:`TokenReaper` instance. The caller (daemon lifespan)
    publishes it on ``daemon_state["_sts_reaper"]`` so
    ``EventLoop._phase_reap_expired_sts_tokens`` can invoke it each tick.
    """
    from general_ludd.sts.audit import StsAuditPipeline
    from general_ludd.sts.reaper import TokenReaper
    from general_ludd.sts.revoker import TokenRevoker
    from general_ludd.sts.store import TokenStore

    audit_pipeline = StsAuditPipeline(session_factory=session_factory)
    store = TokenStore(session_factory=session_factory)
    revoker = TokenRevoker(
        secrets_manager=secrets_resolver,
        token_store=store,
        audit_pipeline=audit_pipeline,
    )
    reaper = TokenReaper(
        store=store,
        revoker=revoker,
        audit_pipeline=audit_pipeline,
    )
    revoker.set_cascade_hook(reaper.cascade_revoke)
    return reaper


def _build_sts_audit_logger(session_factory: Any) -> Any:
    """Build a callable that records STS token usage events to sts_audit rows."""

    async def _log_sts_usage(token_id: str, event: str, agent_id: str) -> None:
        import json as _json

        from sqlalchemy import select

        from general_ludd.db.models import StsAuditModel

        async with session_factory() as session:
            result = await session.execute(select(StsAuditModel).where(StsAuditModel.token_id == token_id))
            row = result.scalar_one_or_none()
            if row is None:
                return
            row.use_count = (row.use_count or 0) + 1
            try:
                events_list = _json.loads(row.events)
            except Exception:
                events_list = []
            events_list.append(event)
            row.events = _json.dumps(events_list)
            row.last_used_at = __import__("time").time()
            await session.commit()

    return _log_sts_usage


def _build_slow_op_publisher(bus: Any) -> Any:
    """Build a callable that publishes SlowOperationEvent to the event bus."""

    def _publish_slow(operation: str, duration_s: float, baseline_s: float, factor: float) -> None:
        from general_ludd.events.types import SlowOperationEvent

        bus.publish(
            SlowOperationEvent(
                operation=operation,
                duration_s=duration_s,
                baseline_s=baseline_s,
                factor=factor,
            )
        )

    return _publish_slow


def _get_or_create_subsystems(app: FastAPI) -> dict[str, Any]:
    if not hasattr(app.state, "_event_bus") or app.state._event_bus is None:
        app.state._event_bus = EventBus(history_size=100)
    if not hasattr(app.state, "_hook_system") or app.state._hook_system is None:
        app.state._hook_system = HookSystem(event_bus=app.state._event_bus)
    if not hasattr(app.state, "_worker_broadcaster") or app.state._worker_broadcaster is None:
        app.state._worker_broadcaster = WorkerBroadcaster()
    if not hasattr(app.state, "_reload_lock") or app.state._reload_lock is None:
        app.state._reload_lock = threading.Lock()
    return {
        "bus": app.state._event_bus,
        "hooks": app.state._hook_system,
        "broadcaster": app.state._worker_broadcaster,
    }


def _ensure_extended_state(app: FastAPI) -> None:
    """Create reusable extended subsystem state without replacing live owners."""
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
        startup_config = getattr(app.state, "_startup_config", {})
        app.state._project_manager = seed_from_config(startup_config)
    if not hasattr(app.state, "_utilization_tracker") or app.state._utilization_tracker is None:
        app.state._utilization_tracker = UtilizationTracker()
    if not hasattr(app.state, "_model_registry") or app.state._model_registry is None:
        app.state._model_registry = ModelRegistry()


def _get_or_create_extended_subsystems(
    app: FastAPI,
    session_factory: Any | None = None,
) -> dict[str, Any]:
    _ensure_extended_state(app)
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
    if session_factory is not None and (
        not hasattr(app.state, "_adaptive_router")
        or app.state._adaptive_router is None
        or app.state._adaptive_router is _STARTUP_UNSET
    ):
        if getattr(app.state, "_adaptive_router", None) is _STARTUP_UNSET:
            logger.debug("Constructing the per-worker adaptive router during startup")
        benchmark_repo = BenchmarkRepository(session_factory=session_factory)
        quantization_map: dict[str, tuple[str, float]] = {}
        tracker = getattr(app.state, "_quantization_tracker", None)
        if tracker is not None:
            quantization_map = {mid: (info.precision, info.confidence) for mid, info in tracker._data.items()}
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
        # G8: cost-quality Pareto frontier router — filters dominated
        # candidates (strictly worse on both cost and quality) before the
        # AdaptiveRouter ranks the remainder. 15% cost / 85% quality weight
        # so composite scoring in pick_winner slightly penalises expensive
        # frontier candidates without discarding high-quality ones.
        pareto_router = ParetoRouter(cost_weight=0.15, quality_weight=0.85)
        adaptive_router = AdaptiveRouter(
            benchmark_repo=benchmark_repo,
            quantization_map=quantization_map,
            health_tracker=getattr(app.state, "_health_tracker", None),
            embedding_store=getattr(app.state, "_embedding_store", None),
            enable_cross_project_borrowing=rr_enabled,
            edge_decay=rr_edge_decay,
            external_penalty=rr_external_penalty,
            min_borrow_weight=rr_min_borrow_weight,
            pareto_router=pareto_router,
        )
        app.state._adaptive_router = adaptive_router
    elif session_factory is not None and hasattr(app.state, "_adaptive_router"):
        adaptive_router = app.state._adaptive_router

    return {
        "metrics": app.state._metrics_collector,
        "projects": app.state._project_manager,
        "utilization": getattr(app.state, "_utilization_tracker", None),
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
    _db_path_override: str | None = None,
    decision_codification: DecisionCodificationAdapter | None = None,
) -> FastAPI:
    """Create the FastAPI daemon app with the full lifespan wiring."""
    if decision_codification is not None:
        from general_ludd.decision_codification.service import (
            DecisionCodificationAdapter as _DecisionCodificationAdapter,
        )

        if not isinstance(decision_codification, _DecisionCodificationAdapter):
            raise TypeError(
                "decision_codification must be a verified DecisionCodificationAdapter"
            )
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

    app = FastAPI(title="General Ludd Agent", version=__version__, lifespan=_lifespan)
    # Per-app daemon state: each app owns a fresh dict so todos / tick_metrics /
    # quality_gate cannot bleed across FastAPI instances in one process (the
    # module-level ``_daemon_state`` used to be shared — a test-isolation hazard).
    daemon_state: dict[str, Any] = {
        # Keep the public factory state as a plain empty list.  The todos
        # router converts it to deque(maxlen=_MAX_INMEMORY_TODOS) on the first
        # degraded-mode access, preserving the startup contract while ensuring
        # the in-memory fallback cannot grow without bound.
        "todos": [],
        "tick_metrics": {},
        "quality_gate": {},
    }
    app.state.daemon_state = daemon_state
    global _daemon_state
    if not isinstance(_daemon_state, _DaemonStateProxy):
        _daemon_state = _DaemonStateProxy()
    _daemon_state.bind(daemon_state)
    app.state.tick_interval = tick_interval
    app.state.event_loop = None
    app.state.log_level = log_level
    app.state._event_bus = None
    app.state._hook_system = None
    app.state._worker_broadcaster = None
    app.state._reload_lock = None
    app.state._db_path_override = _db_path_override
    app.state._config_dir = config_dir
    app.state._templates_dir = templates_dir
    app.state._playbooks_dir = playbooks_dir
    app.state._metrics_collector = None
    app.state._project_manager = None
    app.state._utilization_tracker = None
    app.state._model_registry = None
    app.state._skill_registry = None
    app.state._adaptive_router = _STARTUP_UNSET
    app.state._deployment_health_router = None
    app.state._terraform_event_bridge = None
    app.state._execution_engine = None
    app.state._self_update_audit_sink = None
    app.state._compaction_compactor = None
    app.state._compaction_metrics = None
    app.state._allowed_cidr = []
    app.state._network_host = "127.0.0.1"
    app.state._network_port = 8000
    app.state._startup_config = load_startup_config(config_dir)
    app.state._project_gludd_dir = app.state._startup_config.get("project_gludd_dir")
    app.state._model_performance_router = None
    app.state._performance_repo = None
    app.state._stats_start_time = time.monotonic()
    app.state._stats_requests = 0
    app.state._stats_responses = 0
    app.state.decision_codification = decision_codification

    from general_ludd.planning.critique import PlanCritique

    app.state.plan_critique = PlanCritique()

    from general_ludd.hardware.accelerator_discovery import HardwareDiscovery
    from general_ludd.hardware.probe import probe_hardware
    from general_ludd.hardware.survey import HardwareSurvey

    app.state._hardware = probe_hardware()
    hardware_survey = HardwareSurvey()
    app.state._hardware_inventory = hardware_survey.survey()
    app.state._accelerator_inventory = None
    app.state._accelerator_discovery = HardwareDiscovery(
        survey=hardware_survey,
        surveyed_gpus=app.state._hardware_inventory.gpus,
        trace_sink=lambda trace: logger.info(
            "accelerator discovery event=%s source=%s count=%d",
            trace.event.value,
            trace.source,
            trace.discovered_count,
        ),
    )
    logger.info(
        "Hardware inventory surveyed: GPU=%d RAM=%.1fGB Disk=%.1fGB",
        app.state._hardware_inventory.gpu_count,
        app.state._hardware_inventory.total_ram_gb,
        app.state._hardware_inventory.disk_free_gb,
    )

    # C20: use the SHARED load_auth_posture helper so the daemon and worker
    # cannot drift. GLUDD_PSK_DISABLE and GLUDD_ALLOW_NO_AUTH are both accepted
    # as opt-out; GLUDD_REQUIRE_AUTH forces fail-closed.
    from general_ludd.security.auth import load_auth_posture

    # Env audit / boot observability: the daemon surface reads the auth
    # posture variables explicitly at boot so the runtime reads are visible
    # here; the shared helper (security/auth.py) remains the single source
    # of truth for the derived posture.
    _auth_psk_boot = os.environ.get("GLUDD_AUTH_PSK", "").strip().strip()
    _auth_require_boot = os.environ.get("GLUDD_REQUIRE_AUTH", "").strip()
    _auth_allow_no_boot = os.environ.get("GLUDD_ALLOW_NO_AUTH", "").strip()
    logger.debug(
        "auth env at boot: psk_configured=%s require_auth=%s allow_no_auth=%s",
        bool(_auth_psk_boot),
        _auth_require_boot,
        _auth_allow_no_boot,
    )

    _posture = load_auth_posture("daemon")
    _psk = _posture.psk
    _no_auth = _posture.no_auth
    _require_auth = _posture.require_auth
    # Back-compat: derive _allow_no_auth from posture (no PSK + not requiring
    # auth means the operator opted out via GLUDD_PSK_DISABLE or GLUDD_ALLOW_NO_AUTH).
    _allow_no_auth = _no_auth and not _require_auth
    app.state._psk = _psk
    app.state._no_auth = _no_auth
    app.state._require_auth = _require_auth
    app.state._allow_no_auth = _allow_no_auth
    if _no_auth and not _allow_no_auth:
        # Default fail-closed posture: LOUD warning that non-public paths will
        # be refused (503) until a PSK is configured.
        _dl = logging.getLogger("general_ludd.daemon")
        logger.warning(
            "SECURITY: GLUDD_AUTH_PSK is not set — the daemon will REFUSE all "
            "non-public paths (503, fail-closed). Set GLUDD_AUTH_PSK to enable auth. "
            "For development only, set GLUDD_PSK_DISABLE=1 (or "
            "GLUDD_ALLOW_NO_AUTH=1) to allow unauthenticated access (leaves "
            "the entire /admin surface open to any caller)."
        )
    elif _no_auth and _allow_no_auth:
        # Explicit dev opt-out: LOUD warning that auth is intentionally disabled.
        logger.warning(
            "SECURITY: GLUDD_AUTH_PSK is not set and auth is disabled — the "
            "daemon is running with admin auth DISABLED (no_auth mode). The "
            "entire /admin surface is open to any caller that can reach the port. "
            "Set GLUDD_AUTH_PSK (alias: GLUDD_PSK) to enable auth."
        )

    _PUBLIC_PATHS = {
        "/healthz",
        "/readyz",
        "/api/status",
        "/api/todos",
        "/api/human-todos",
        "/api/webmcp",
        "/docs",
        "/openapi.json",
        "/redoc",
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
    async def cidr_middleware(request: Any, call_next: Any) -> Any:
        cidrs: list[str] = getattr(app.state, "_allowed_cidr", None) or []
        if cidrs:
            client_host = getattr(request.client, "host", None) if request.client else None
            if not client_host or client_host == "testclient":
                client_host = "127.0.0.1"
            if client_host is not None:
                import ipaddress as _ipaddress

                try:
                    client_ip = _ipaddress.ip_address(client_host)
                except ValueError:
                    client_ip = None
                allowed = client_ip is not None and any(
                    client_ip in _ipaddress.ip_network(cidr, strict=False) for cidr in cidrs
                )
                if not allowed:
                    from fastapi.responses import JSONResponse

                    logger.warning("CIDR deny: %s not in allowed_cidr=%s", client_host, cidrs)
                    return JSONResponse(
                        status_code=403,
                        content={"error": "forbidden", "reason": "client IP not in allowed_cidr"},
                    )
        return await call_next(request)

    # Registered AFTER cidr_middleware so auth_and_stats_middleware wraps it:
    # FastAPI/Starlette runs the last-registered middleware first (outermost),
    # so auth/stats sees every request — including CIDR-denied ones.
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
                #
                # XT-3/XT-4 cross-tenant fix: the bearer token may carry a
                # project claim in "project_id:psk" format. Parse it before
                # the constant-time check, stamping request.state.project_id
                # so downstream endpoints (traces, metrics) can enforce
                # tenant scoping without trusting a caller-supplied
                # ?project_id= query param.  Legacy tokens without a colon
                # (plain "psk") remain unscoped — back-compat.
                from general_ludd.security.auth import check_bearer_token

                token_part = auth.removeprefix("Bearer ").strip()
                if ":" in token_part:
                    claimed_project_id, psk_part = token_part.split(":", 1)
                else:
                    claimed_project_id = None
                    psk_part = token_part

                if not check_bearer_token(f"Bearer {psk_part}", _psk):
                    from fastapi.responses import JSONResponse

                    app.state._stats_responses += 1
                    return JSONResponse(status_code=401, content={"error": "unauthorized"})

                if claimed_project_id:
                    request.state.project_id = claimed_project_id

                from general_ludd.security.permissions import _psk_admin_default_spec

                request.state.auth_spec = _psk_admin_default_spec()
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
            "Returns 200 with security-posture + budget flags when alive; 503 on degraded startup. Public, no auth."
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
        try:
            local_model = await local_model_health_check()
        except Exception:
            local_model = {"model_exists": False, "llama_cpp_available": False, "memory": {}}
        # SECURITY (gateway-health-budget P1): the memory dict carries numeric
        # capacity figures — strip it from the unauth'd payload; only the
        # booleans are public on /healthz.
        local_model_public = {
            "model_exists": bool(local_model.get("model_exists", False)),
            "llama_cpp_available": bool(local_model.get("llama_cpp_available", False)),
        }
        # N1/C6: a dead/cancelled event-loop task after a successful startup must
        # NOT serve green — the daemon is alive but no longer processing work.
        # Mirror /readyz's check so /healthz also reports degraded in that case
        # (the `_degraded` flag alone only catches STARTUP failures).
        el_task = getattr(app.state, "_event_loop_task", None)
        if el_task is not None and el_task.done():
            degraded = degraded or ("event_loop_cancelled" if el_task.cancelled() else "event_loop_done")
        if degraded:
            return {
                "status": "degraded",
                "reason": str(degraded)[:200],
                "no_auth": no_auth,
                "require_auth": require_auth,
                "allow_no_auth": allow_no_auth,
                "auth_degraded": auth_degraded,
                "budget_exhausted": budget_exhausted,
                "local_model": local_model_public,
            }
        return {
            "status": "healthy",
            "no_auth": no_auth,
            "require_auth": require_auth,
            "allow_no_auth": allow_no_auth,
            "auth_degraded": auth_degraded,
            "budget_exhausted": budget_exhausted,
            "local_model": local_model_public,
        }

    @app.get(
        "/readyz",
        response_model=None,
        summary="Readiness probe — daemon can accept work",
        description=("200 when ready (not degraded, event loop alive); 503 otherwise. Public, no auth."),
    )
    async def readyz() -> JSONResponse | dict[str, str]:
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
        el_task = getattr(app.state, "_event_loop_task", None)
        # During the full E2E harness startup is intentionally observable as
        # not-ready until an explicit probe task is installed. Unit callers
        # exercising a real in-process daemon retain the historical 200-ready
        # behaviour once the runtime task exists.
        e2e_startup = os.environ.get("GLUDD_E2E_ACTIVE") == "1"
        if el_task is None or (e2e_startup and getattr(app.state, "_event_loop_task_auto", False)):
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": "daemon_not_initialized"},
            )
        if el_task.done():
            reason = "event_loop_cancelled" if el_task.cancelled() else "event_loop_done"
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": reason},
            )
        return {"status": "ready"}

    @app.get("/metrics")
    async def metrics_prometheus() -> PlainTextResponse:
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
        provider: DashboardDataProvider | None = getattr(app.state, "_dashboard_data", None)
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

    @app.get("/admin/eval/status")
    async def admin_eval_status() -> dict[str, Any]:
        harness = getattr(app.state, "eval_harness", None)
        if harness is None:
            return {"status": "not_configured", "ready": False}
        return {
            "status": "configured",
            "ready": harness.ready,
            "model": harness.model,
        }

    @app.get("/admin/execution/engine-status")
    async def admin_execution_engine_status() -> dict[str, Any]:
        engine = getattr(app.state, "_execution_engine", None)
        if engine is None:
            return {"status": "not_configured", "reason": "No execution engine wired"}
        return {
            "status": "configured",
            "workspace_path": engine.workspace_path,
            "has_model_gateway": engine._model_gateway is not None,
            "has_budget_guard": engine._budget_guard is not None,
            "has_metrics_collector": engine._metrics_collector is not None,
        }

    @app.get(
        "/admin/plan/critique-status",
        summary="PlanCritique wiring status",
        description="Returns whether PlanCritique is wired on app.state.",
    )
    async def admin_plan_critique_status() -> dict[str, Any]:
        critique = getattr(app.state, "plan_critique", None)
        return {
            "wired": critique is not None,
            "class": type(critique).__name__ if critique is not None else None,
        }

    @app.post(
        "/admin/plan/critique",
        summary="Critique a plan",
        description=(
            "Accepts a plan dict (matching PlanArtifact fields: title, "
            "target_files, description, dependencies, content) and returns "
            "a list of critique findings. Each finding has severity "
            "(error/warning/info) and message."
        ),
    )
    async def admin_plan_critique(body: dict[str, Any]) -> dict[str, Any]:
        critique = getattr(app.state, "plan_critique", None)
        if critique is None:
            return {"status": "not_configured", "findings": []}
        findings = critique.critique_plan(body)
        return {
            "status": "ok",
            "findings": findings,
            "finding_count": len(findings),
        }

    @app.get(
        "/admin/compaction/eval-status",
        summary="Compaction evaluation status",
        description=(
            "Returns the current compaction evaluation state: the active champion "
            "compactor, the latest aggregate metrics (score, fidelity, compression "
            "ratio), and whether the self-improving compactor is wired."
        ),
    )
    async def admin_compaction_eval_status() -> dict[str, Any]:
        compactor = getattr(app.state, "_compaction_compactor", None)
        metrics = getattr(app.state, "_compaction_metrics", None)
        wired = compactor is not None
        return {
            "wired": wired,
            "champion": compactor.champion.name if compactor is not None else None,
            "metrics": metrics.model_dump() if metrics is not None else None,
        }

    # Lazy to avoid circular import: routers/*.py import from daemon at module level
    from general_ludd.routers import (
        account as account_router,
    )
    from general_ludd.routers import (
        accounting,
        ansible,
        benchmark,
        compute,
        deployments,
        embeddings,
        environment,
        experts,
        facts,
        features,
        filestore,
        git_history,
        human_todos,
        integrity,
        maintenance,
        make,
        mcp,
        memory,
        messages,
        model_performance,
        models,
        ornith,
        pause,
        processes,
        projects,
        quantization,
        reload,
        remediation,
        render,
        replays,
        research,
        review,
        schedule,
        security,
        self_improve,
        self_update,
        signing,
        skills,
        slurm,
        spend,
        todos,
        variants,
        webmcp,
        worktree,
    )
    from general_ludd.routers import azure_cost as azure_cost_router
    from general_ludd.routers import (
        decision_codification as decision_codification_router,
    )
    from general_ludd.routers import (
        hardware as hardware_router,
    )
    from general_ludd.routers.azure_cost import (
        CostHealthResponse,
        CostIngestRequest,
        CostIngestResponse,
    )

    _ = (CostIngestRequest, CostIngestResponse, CostHealthResponse)
    from general_ludd.routers import (
        dispatch as dispatch_router,
    )
    from general_ludd.routers import (
        eval as eval_router,
    )

    eval_router.register(app, daemon_state)
    webmcp.register(app, daemon_state)
    todos.register(app, daemon_state)
    messages.register(app, daemon_state)
    accounting.register(app, daemon_state)
    account_router.register(app, daemon_state)
    facts.register(app, daemon_state)
    environment.register(app, daemon_state)
    embeddings.register(app, daemon_state)
    features.register(app, daemon_state)
    schedule.register(app, daemon_state)
    model_performance.register(app, daemon_state)
    models.register(app, daemon_state)
    variants.register(app, daemon_state)
    benchmark.register(app, daemon_state)
    mcp.register(app, daemon_state)
    memory.register(app, daemon_state)
    skills.register(app, daemon_state)
    compute.register(app, daemon_state)
    deployments.register(app, daemon_state)
    processes.register(app, daemon_state)
    filestore.register(app, daemon_state)
    git_history.register(app, daemon_state)
    hardware_router.register(app, daemon_state)
    human_todos.register(app, daemon_state)
    integrity.register(app, daemon_state)
    signing.register(app, daemon_state)
    security.register(app, daemon_state)
    projects.register(app, daemon_state)
    quantization.register(app, daemon_state)
    reload.register(app, daemon_state)
    replays.register(app, daemon_state)
    decision_codification_router.register(app, daemon_state)
    worktree.register(app, daemon_state)
    ansible.register(app, daemon_state)
    azure_cost_router.register(app, daemon_state)
    slurm.register(app, daemon_state)
    self_improve.register(app, daemon_state)
    self_update.register(app, daemon_state)
    maintenance.register(app, daemon_state)
    make.register(app, daemon_state)
    remediation.register(app, daemon_state)
    research.register(app, daemon_state)
    review.register(app, daemon_state)
    ornith.register(app, daemon_state)
    experts.register(app, daemon_state)
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
    from general_ludd.daemon_wiring import (
        make_collection_handler,
        make_mcp_handler,
        make_role_handler,
        make_skill_handler,
    )

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

    async def _lazy_collection_handler(name: str, args: dict[str, Any]) -> Any:
        # The live AnsibleRunnerAdapter is assigned to app.state._runner during
        # lifespan startup (see ~line 1296), AFTER the router registers here at
        # app-creation. Resolving it lazily (like the mcp/role handlers) means
        # the ``collection`` kind wires to the real adapter once startup runs.
        # A missing runner FAILS CLOSED: raising is caught by DynamicDispatcher,
        # which returns DispatchResult(ok=False, error="handler_error") — the
        # same fail-closed shape the mcp/role lazy handlers produce.
        runner = getattr(app.state, "_runner", None)
        h = make_collection_handler(runner)
        if h is None:
            raise RuntimeError("AnsibleRunnerAdapter not available")
        return await h(name, args)

    dispatch_router.register(
        app,
        daemon_state,
        role_handler=_lazy_role_handler,
        mcp_handler=_lazy_mcp_handler,
        skill_handler=_lazy_skill_handler,
        collection_handler=_lazy_collection_handler,
        capability_registry=getattr(app.state, "_capability_registry", None),
    )
    spend.register(app, daemon_state)
    pause.register(app, daemon_state)
    from general_ludd.routers import approval as _approval_router

    _approval_router.register(app, daemon_state)
    from general_ludd.routers import sts as sts_router
    from general_ludd.routers.sts import (
        MintRequest,
        MintResponse,
        RevokeRequest,
    )

    _ = (MintRequest, MintResponse, RevokeRequest)

    sts_router.register(app, daemon_state)
    from general_ludd.routers import compaction_aggressiveness as _compaction_aggr_router

    _compaction_aggr_router.register(app, daemon_state)
    from general_ludd.routers import coordination as _coord_router

    _coord_router.register(app, daemon_state)

    from general_ludd.routers import stream as _stream_router

    _stream_router.register(app, daemon_state)
    from general_ludd.routers import terraform_state as _terraform_state_router

    _terraform_state_router.register(app, daemon_state)

    from general_ludd.routers.observe import wire_observability

    startup_config = getattr(app.state, "_startup_config", {}) or {}
    _connector_cfg = list(startup_config.get("connectors") or []) or None
    if _connector_cfg is None:
        _uc = startup_config.get("user_config")
        _connector_cfg = list(getattr(_uc, "connectors", None) or []) or None
    wire_observability(app, daemon_state, _connector_cfg)

    @app.get(
        "/admin/connectors/health",
        summary="Connector health — probe every registered connector",
        description=(
            "Returns health() across EVERY connector in the ConnectorRegistry. "
            "When no registry is wired (no connectors configured), returns an "
            "empty result rather than erroring. Each source is reported as "
            '{"ok": true/false, ...} — a failed backend is a data point, not '
            "an exception. This path is PSK-gated (NOT in _PUBLIC_PATHS)."
        ),
    )
    async def admin_connectors_health() -> dict[str, Any]:
        reg = getattr(app.state, "_connector_registry", None)
        if reg is None:
            return {"health": {}, "count": 0, "errors": []}
        # health_all() probes each connector's health() serially and blocks on
        # network I/O. Offload to a worker thread so the event loop stays free
        # (mirrors routers/observe.py's observe_health).
        health = await asyncio.to_thread(reg.health_all)
        return {
            "health": health,
            "count": len(health),
            "errors": reg.errors(),
        }

    return app
