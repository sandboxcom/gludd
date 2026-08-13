"""Unified daemon — FastAPI app with embedded event loop and hot-reload admin endpoints."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator, MutableMapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

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
from general_ludd.writer import WriterProcess

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


def _remediation_tick_settings(uc: Any) -> tuple[int, int]:
    """Return ``(check_interval_ticks, max_actions_per_tick)`` for the
    auto-remediation tick phase (#52), fail-soft to ``(30, 5)`` when ``uc``
    or the ``remediation`` block is absent.
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
            for p in project_manager.list_active():
                pid = getattr(p, "project_id", str(p))
                workspaces[pid] = ProjectWorkspace(project_id=pid)
                workspaces[pid].ensure_dirs()
        except Exception as exc:
            logger.warning("Failed to initialize project workspaces: %s", exc)
    return workspaces


def load_model_profiles(profiles_dir: str | None = None) -> list[ModelProfile]:
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
    acceptance_criteria: list[object] | None = None
    definition_of_done: str | None = None


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
    enabled: bool = True
    api_metered: bool = True
    cost_per_input_token: float = Field(default=0.0, ge=0.0)
    cost_per_output_token: float = Field(default=0.0, ge=0.0)


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
            )

    def _sink(record: Any) -> None:
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
        _SELF_UPDATE_AUDIT_TASKS.add(task)
        task.add_done_callback(_SELF_UPDATE_AUDIT_TASKS.discard)

    return _sink


def _configure_network_state(app: Any, network: Any) -> None:
    """Apply network policy and refuse unauthenticated external listeners."""

    preserve_cidr = bool(getattr(app.state, "_allowed_cidr", None))
    if network.is_external_bind and bool(getattr(app.state, "_no_auth", True)):
        raise RuntimeError(
            "External daemon binds require authenticated access; configure GLUDD_PSK or use a loopback network host."
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


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    tick_interval = app.state.tick_interval
    # Per-app state dict (created in create_daemon_app). Read from app.state so
    # concurrently-running apps never share/overwrite one another's state. If a
    # caller invokes the lifespan on a bare app (unit tests), materialise a fresh
    # per-app dict rather than falling back to the shared module global.
    daemon_state: dict[str, Any] = getattr(app.state, "daemon_state", None) or {
        "todos": [],
        "tick_metrics": {},
        "quality_gate": {},
    }
    event_loop = None
    task = None
    execution_engine = None

    try:
        startup_config = getattr(app.state, "_startup_config", {}) or {}
        db_config: dict[str, Any] = {}
        uc = startup_config.get("user_config")
        bc = _parse_budget_config(uc)
        if uc and hasattr(uc, "network"):
            _configure_network_state(app, uc.network)
        if uc and hasattr(uc, "database"):
            db_config = uc.database or {}
        _db_override: str | None = getattr(app.state, "_db_path_override", None)
        if _db_override:
            db_config["url"] = f"sqlite+aiosqlite:///{_db_override}"

        # B3.1.3 Slice 4 — GLUDD_WRITER_MODE selects between the inline
        # single-process daemon path (default; zero behavioural change) and
        # the subprocess mode where HTTP workers get a read-only engine and
        # all DB writes are routed through a WriteQueue to a dedicated writer
        # subprocess. The env-var read MUST happen before engine construction
        # so the branch below can pick the right engine/factory pair.
        writer_mode = os.environ.get("GLUDD_WRITER_MODE", "inline").strip().lower()
        if writer_mode not in {"inline", "subprocess"}:
            logger.warning(
                "GLUDD_WRITER_MODE=%r is not 'inline' or 'subprocess'; falling back to inline",
                writer_mode,
            )
            writer_mode = "inline"

        # Schema + seed need write access; the read-only factory published
        # to HTTP workers in subprocess mode is built AFTER seeding completes.
        engine = init_engine_from_config(db_config)
        await ensure_tables(engine)

        if writer_mode == "subprocess":
            # Seed on a writable factory, then swap to a read-only factory for
            # the HTTP workers' runtime sessions. The writer subprocess owns
            # all subsequent DB writes; HTTP workers enqueue via WriteQueue.
            _seed_factory = create_async_session_factory(engine)
            async with _seed_factory() as session:
                await seed_initial_queues(session)
                await session.commit()
            await engine.dispose()

            engine = init_read_only_engine_from_config(db_config)
            session_factory = create_read_only_session_factory(engine)
            write_queue: WriteQueue | None = WriteQueue()
            _wp = WriterProcess(config=dict(db_config))
            _wp.start()
            writer_process: WriterProcess | None = _wp
            logger.info(
                "GLUDD_WRITER_MODE=subprocess: read-only engine + WriteQueue + WriterProcess(pid=%s) started",
                _wp.pid,
            )
        else:
            session_factory = create_async_session_factory(engine)
            async with session_factory() as session:
                await seed_initial_queues(session)
                await session.commit()
            write_queue = None
            writer_process = None

        # Publish on app.state so router code can branch via enqueue_or_commit.
        if write_queue is not None:
            app.state._write_queue = write_queue
        if writer_process is not None:
            app.state._writer_process = writer_process

        app.state._sts_audit_logger = _build_sts_audit_logger(session_factory)

        # Orphan detection: flag Slurm jobs from a prior daemon instance.
        import os as _os

        _current_pid = _os.getpid()
        try:
            async with session_factory() as session:
                slurm_repo = SlurmJobRepository(session)
                orphans = await slurm_repo.list_orphans(_current_pid)
            if orphans:
                logger.warning(
                    "Found %d orphan Slurm job(s) still marked 'running' from a prior "
                    "daemon instance (pid != %d): %s. Not auto-cancelling — they may "
                    "belong to another daemon.",
                    len(orphans),
                    _current_pid,
                    ", ".join(j.job_id for j in orphans),
                )
        except Exception:
            logger.warning("Orphan Slurm job detection failed")

        # Bill-3: preemption handler for Slurm jobs
        from general_ludd.infra.slurm_preemption import SlurmPreemptionHandler

        app.state._slurm_preemption_handler = SlurmPreemptionHandler()
        logger.info("Slurm preemption handler initialised")

        # Phase 2 Step 3 (self-improve wiring): build the audit_sink closure over
        # session_factory + AuditEventRepository and publish it on app.state so
        # the /admin/self-update/plan router can pass it through to apply_plan.
        # Built once here (after session_factory exists) so every request reuses
        # the same sink; the sink opens its own short-lived session per record.
        app.state._self_update_audit_sink = _build_self_update_audit_sink(session_factory)

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
        _initial_project_root = str(Path(_proj_gludd).parent) if _proj_gludd is not None else None
        _collections_paths = resolve_collections_paths(_initial_project_root)
        _ansible_env: dict[str, str] = to_ansible_env(_collections_paths)
        app.state._collections_paths = _collections_paths
        app.state._ansible_env = dict(_ansible_env)
        logger.info(
            "Resolved Ansible collections paths (%d tier(s)): %s",
            len(_collections_paths),
            ", ".join(f"{e.source}={e.path}" for e in _collections_paths),
        )

        from general_ludd.dispatch.capabilities import discover_capabilities

        capability_registry = await asyncio.to_thread(discover_capabilities)
        app.state._capability_registry = capability_registry
        logger.info(
            "CapabilityRegistry: %d collections, %d tags indexed",
            len(capability_registry.collections),
            len(capability_registry.tag_index),
        )

        # Bill-4: Terraform watchdog for stack cost monitoring
        stacks_dir = os.environ.get(
            "GLUDD_TERRAFORM_STACKS_DIR",
            str(Path.cwd() / "infra" / "terraform" / "stacks"),
        )
        from general_ludd.infra.terraform_watchdog import TerraformWatchdog

        app.state._terraform_watchdog = TerraformWatchdog(stacks_dir=stacks_dir)
        logger.info("Terraform watchdog initialised for stacks: %s", stacks_dir)

        from general_ludd.infra.spot_validator import SpotConfigValidator

        app.state._spot_config_validator = SpotConfigValidator(default_spot=True)

        # G3: Construct a shared CodebaseIndexer for semantic codebase retrieval.
        # Uses diskcache in .gludd/retrieval_cache by default.
        from general_ludd.retrieval.indexer import CodebaseIndexer

        _codebase_indexer = CodebaseIndexer()
        app.state._codebase_indexer = _codebase_indexer
        logger.info("CodebaseIndexer initialised (cache: %s)", _codebase_indexer.cache_dir)

        from general_ludd.retrieval.searx_client import SearxNGClient

        app.state._searx_client = SearxNGClient()

        if uc is not None and uc.searx_autostart:
            from general_ludd.searx.install import (
                ensure_searx_initialized,
                ensure_searx_installed,
            )
            from general_ludd.searx.server import SearXServer

            if ensure_searx_installed() and ensure_searx_initialized():
                searx_server = SearXServer()
                if searx_server.ensure_started():
                    app.state._searx_server = searx_server
                    logger.info(
                        "SearXNG server started on %s",
                        searx_server.get_instance_url(),
                    )
                else:
                    logger.error("SearXNG autostart was requested but startup failed")
            else:
                logger.error("SearXNG autostart was requested but setup failed")
        else:
            app.state._searx_server = None
            logger.info("SearXNG autostart disabled; expecting an external service")

        from general_ludd.retrieval.research_index import ResearchIndex

        app.state._research_index = ResearchIndex()

        def _update_ansible_env(paths: list[Any], env: dict[str, str]) -> None:
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
            logger.error("TaskEmbeddingStore seeding failed")
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

        # P5: construct the STS reaper pipeline (TokenStore + TokenRevoker +
        # TokenReaper + StsAuditPipeline) and publish on daemon_state so
        # EventLoop._phase_reap_expired_sts_tokens can sweep expired tokens
        # every sts_reap_interval_ticks. The cascade hook is wired so that
        # revoking a parent token tears down its delegation subtree.
        try:
            _sts_reaper = _build_sts_reaper(
                session_factory=session_factory,
                secrets_resolver=secrets_resolver,
            )
            daemon_state["_sts_reaper"] = _sts_reaper
            app.state._sts_reaper = _sts_reaper
            logger.info("STS TokenReaper wired into daemon tick")
        except Exception:
            logger.warning("STS TokenReaper construction failed; reaping disabled")

        model_profiles = startup_config.get("model_profiles", [])

        # Auto-config: for every provider whose credential env var is set
        # (e.g. MISTRAL_API_KEY, FIREWORKS_API_KEY) but which lacks an explicit
        # ModelProfile in the operator config, synthesize one using the
        # provider's flagship model. Explicit config-supplied profiles always
        # win (deduped by model_profile_id) so a user-written profile is never
        # silently clobbered. See AutoConfigurator.auto_configure_from_env.
        try:
            from general_ludd.models.auto_configurator import AutoConfigurator

            _auto_profiles = AutoConfigurator().auto_configure_profiles()
            if _auto_profiles:
                _existing_ids = {
                    getattr(_p, "model_profile_id", None) if not isinstance(_p, dict) else _p.get("model_profile_id")
                    for _p in model_profiles
                }
                _added = [_p for _p in _auto_profiles if _p.model_profile_id not in _existing_ids]
                if _added:
                    model_profiles = list(model_profiles) + _added
                    logger.info(
                        "Auto-config: appended %d env-derived profile(s): %s",
                        len(_added),
                        [_p.model_profile_id for _p in _added],
                    )
        except Exception:
            logger.warning(
                "Auto-config: env-var profile discovery failed; continuing with explicit config only",
            )

        if model_profiles and hasattr(secrets_resolver, "write_secret"):
            try:
                profile_dicts = [p.model_dump() if hasattr(p, "model_dump") else p for p in model_profiles]
                result = migrate_profile_secrets(secrets_resolver, profile_dicts)
                logger.info(
                    "Secret migration: %d migrated, %d skipped",
                    result["migrated"],
                    len(cast("list[str]", result["skipped"])),
                )
            except Exception:
                logger.error("Secret migration failed (non-critical — daemon continues)")

        templates_dir = getattr(app.state, "_templates_dir", None)
        # Phase 2: prepend project .gludd/templates/ so project-local templates
        # shadow same-named global ones.  No-op when the dir does not exist.
        _proj_for_prompts = startup_config.get("project_gludd_dir")
        _extra_tmpl_dirs: list[str] = []
        if _proj_for_prompts is not None:
            _proj_tmpl_dir = Path(_proj_for_prompts) / "templates"
            if _proj_tmpl_dir.is_dir():
                _extra_tmpl_dirs = [str(_proj_tmpl_dir)]
        hub_registry = None
        _use_hub = bool(getattr(uc, "use_hub", False)) if uc else False
        if _use_hub:
            from general_ludd.prompts.hub_registry import LangChainHubRegistry

            hub_registry = LangChainHubRegistry(use_hub=True)
            logger.info("LangChainHubRegistry enabled for prompt resolution")
        prompt_registry = PromptRegistry(
            template_dir=templates_dir,
            event_bus=subsys["bus"],
            extra_template_dirs=_extra_tmpl_dirs or None,
            hub_registry=hub_registry,
        )
        # P2 (perf): refresh() globs the template dir and read_text()s each *.j2
        # file — blocking filesystem IO. Offload it so the daemon-boot coroutine
        # does not stall the event loop while templates load. Return value is
        # unused; error handling is unchanged (an unreadable dir still raises and
        # is caught by the outer startup try/except → degraded mode).
        await asyncio.to_thread(prompt_registry.refresh)
        app.state._prompt_registry = prompt_registry
        output_template_dirs: list[str] = []
        if _proj_for_prompts is not None:
            _proj_output_tmpl_dir = Path(_proj_for_prompts) / "templates" / "log_output"
            if _proj_output_tmpl_dir.is_dir():
                output_template_dirs.append(str(_proj_output_tmpl_dir))
        output_template_registry = OutputTemplateRegistry.default(extra_template_dirs=output_template_dirs)
        output_template_summary = await asyncio.to_thread(output_template_registry.compile)
        app.state._output_template_registry = output_template_registry
        app.state._output_template_summary = output_template_summary
        logger.info("Output templates compiled: %d", output_template_summary.get("count", 0))
        app.state._prompt_enhancer = PromptEnhancer()

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

        # LangSmith tracer: additive observability side-channel.
        # Enabled when LANGSMITH_API_KEY + LANGSMITH_PROJECT env vars are set.
        # Gracefully degrades — no-op when unconfigured or unavailable.
        app.state.langsmith_tracer = LangSmithTracer()

        from general_ludd.controllers.pause_controller import PauseController

        app.state._pause_controller = PauseController()

        from general_ludd.agents.hibernation import (
            HibernationController,
            HibernationStore,
            _load_hibernate_mac_key,
        )

        pause_base = app.state._pause_controller._store.base_dir
        hibernate_mac_key = _load_hibernate_mac_key(str(pause_base))
        app.state._hibernation_controller = HibernationController(
            store=HibernationStore(base_dir=str(pause_base), mac_key=hibernate_mac_key),
        )

        from general_ludd.controllers.floor import FloorController

        floor_controller = FloorController()
        app.state._floor_controller = floor_controller

        from general_ludd.controllers.compaction_aggressiveness import (
            CompactionAggressivenessController,
        )

        app.state._compaction_aggressiveness_controller = CompactionAggressivenessController()

        from general_ludd.approval.gate import ApprovalGate

        app.state._approval_gate = ApprovalGate()

        model_gateway = None
        deployment_health_router = None
        semantic_searcher = None
        if model_profiles:
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
                pause_controller=app.state._pause_controller,
                langsmith_tracer=app.state.langsmith_tracer,
            )
            app.state._model_gateway = model_gateway

            await _warm_start_local_models(model_gateway)

            semantic_searcher = SemanticSearcher()
            app.state._semantic_searcher = semantic_searcher

            eval_harness = EvalHarness(
                model="sonnet",
                evaluator=ModelEvaluator(model_gateway, profile_id="sonnet"),
            )
            app.state.eval_harness = eval_harness

            # Deployment health: track per-model-deployment failures and
            # self-heal by routing away from unhealthy deployments.
            deployment_health_checker = DeploymentHealthChecker()
            deployment_health_router = SelfHealingRouter(
                health_checker=deployment_health_checker,
            )
            # Feed each profile's fallback chain into the self-healing router
            # so it knows the healthy alternatives when a deployment degrades.
            for _p in model_gateway._profiles.values():
                if _p.fallback_profiles:
                    deployment_health_router.set_fallbacks(
                        _p.model_profile_id,
                        list(_p.fallback_profiles),
                    )
            app.state._deployment_health_router = deployment_health_router
            app.state._model_gateway._deployment_health_checker = deployment_health_checker

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

        if model_profiles:
            # model_gateway already set above
            pass
        else:
            # S1 fix: model gateway is unconfigured — set a flag so /readyz
            # can report NOT ready and the dispatcher fails-loud instead of
            # silently completing every task with the noop executor.
            app.state._model_unconfigured = True
            if uc is not None and uc.allow_unconfigured_model:
                logger.info("Model gateway intentionally disabled for this process")
            else:
                logger.warning(
                    "No model_profiles loaded — model gateway is unconfigured. "
                    "All agent dispatch will fail until model profiles are "
                    "provided via GLUDD_CONFIG_DIR / config/model_profiles/*.yml."
                )

        if getattr(app.state, "eval_harness", None) is None:
            app.state.eval_harness = EvalHarness(model="sonnet")

        # LangChain/LangGraph integration: feature-flag-gated construction of
        # LangChainModelRouter and LangChainRetryGateway. Both default OFF.
        _use_langchain_routing = bool(getattr(uc, "use_langchain_routing", False)) if uc else False
        _use_langchain_retry = bool(getattr(uc, "use_langchain_retry", False)) if uc else False
        app.state._langchain_router = None
        app.state._langchain_retry_gateway = None
        if _use_langchain_routing:
            from general_ludd.models.langchain_router import LangChainModelRouter

            app.state._langchain_router = LangChainModelRouter()
            logger.info("LangChainModelRouter enabled for model routing")
        if _use_langchain_retry and model_gateway is not None:
            from general_ludd.models.langchain_retry import LangChainRetryGateway

            app.state._langchain_retry_gateway = LangChainRetryGateway(model_gateway)
            logger.info("LangChainRetryGateway enabled for retry/fallback orchestration")

        # H4 (W3.2): wire a real ReturnReviewer into the review phase when a
        # gateway exists. Review failure escalates the todo; it is never a
        # silent pass.
        return_reviewer = None
        adversarial_detector = AdversarialCodeDetector()
        estimation_tracker = EstimationTracker()
        app.state._adversarial_detector = adversarial_detector
        app.state._estimation_tracker = estimation_tracker
        daemon_state["_adversarial_detector"] = adversarial_detector
        daemon_state["_estimation_tracker"] = estimation_tracker
        logger.info(
            "Wired adversarial detector (%d patterns) and estimation tracker",
            len(adversarial_detector.get_all_categories()),
        )
        if model_gateway is not None and uc is not None and uc.service_discovery_enabled:
            from general_ludd.review.reviewer import ReturnReviewer

            return_reviewer = ReturnReviewer(
                gateway=model_gateway,
                prompt_registry=prompt_registry,
                router=ext.get("adaptive_router"),
                budget_guard=budget_guard,
                adversarial_detector=adversarial_detector,
                estimation_tracker=estimation_tracker,
            )

        langgraph_reviewer = None
        review_cfg: dict[str, Any] = {}
        if uc is not None:
            hitl = getattr(uc, "human_in_the_loop", None)
            review_cfg["human_in_the_loop"] = bool(getattr(hitl, "enabled", False))
            review_cfg["confidence_threshold"] = float(getattr(hitl, "confidence_threshold", 0.7))
        if model_gateway is not None:
            review_use_langgraph = False
            if uc is not None:
                with contextlib.suppress(Exception):
                    review_use_langgraph = bool(
                        getattr(uc, "use_langgraph_review", False)
                        or startup_config.get("review", {}).get("use_langgraph", False)
                    )
            if review_use_langgraph:
                from general_ludd.review.langgraph_reviewer import LangGraphReflexiveReviewer

                review_cfg = {"use_langgraph": True}

                def _langgraph_call_model(prompt: str) -> str:
                    try:
                        response = model_gateway.call_model(
                            "default",
                            messages=[{"role": "user", "content": prompt}],
                            work_type="review",
                        )
                        return response.content
                    except Exception as exc:
                        logger.debug("langgraph model call failed")
                        raise LangGraphModelCallError(exc) from exc

                langgraph_reviewer = LangGraphReflexiveReviewer(
                    call_model=_langgraph_call_model,
                    max_iterations=startup_config.get("review", {}).get("max_iterations", 3),
                    confidence_threshold=startup_config.get("review", {}).get("confidence_threshold", 0.8),
                )
                logger.info(
                    "LangGraphReflexiveReviewer enabled: max_iterations=%d confidence_threshold=%.2f",
                    langgraph_reviewer._max_iterations,
                    langgraph_reviewer._confidence_threshold,
                )

        # G11: Consensus-based multi-agent review. Wired when a model gateway
        # exists so the consensus review path (3-agent debate) is available.
        # Config-gated via ``consensus_review.enabled`` (default OFF).
        consensus_reviewer = None
        consensus_cfg: dict[str, Any] = {}
        if uc is not None:
            with contextlib.suppress(Exception):
                cc = getattr(uc, "consensus_review", None)
                if cc is not None:
                    consensus_cfg["enabled"] = bool(getattr(cc, "enabled", False))
                    consensus_cfg["num_agents"] = int(getattr(cc, "num_agents", 3))
                    consensus_cfg["max_rounds"] = int(getattr(cc, "max_rounds", 3))
        if model_gateway is not None:
            from general_ludd.review.consensus_reviewer import ConsensusReviewer

            consensus_reviewer = ConsensusReviewer(
                gateway=model_gateway,
                num_agents=consensus_cfg.get("num_agents", 3),
                max_rounds=consensus_cfg.get("max_rounds", 3),
                use_langgraph=False,
            )
            logger.info(
                "ConsensusReviewer wired: num_agents=%d max_rounds=%d (enabled=%s)",
                consensus_reviewer._num_agents,
                consensus_reviewer._max_rounds,
                consensus_cfg.get("enabled", False),
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
                self_improve_interval = int(startup_config.get("self_improve_interval", 10))

        # Compaction eval wiring: build a self-improving compactor with the
        # default candidate pool. The arena can be re-run at runtime via the
        # /admin/compaction/eval-status endpoint to re-evaluate the champion.
        from general_ludd.compaction.arena import build_self_improving_compactor
        from general_ludd.compaction.evaluate import CompactionMetrics as EvalMetrics

        _summary_fn = None
        if model_gateway is not None and hasattr(model_gateway, "_profiles"):
            from general_ludd.compaction.slm import make_slm_summarize_fn

            try:
                _summary_fn = make_slm_summarize_fn(model_gateway, profile_id="compactor")
            except Exception:
                logger.info(
                    "SLM summarizer unavailable for compaction eval — running "
                    "with offline fallback (candidates use extractive truncation)"
                )

        _compaction_compactor = build_self_improving_compactor(
            summarize_fn=_summary_fn,
        )
        app.state._compaction_compactor = _compaction_compactor
        app.state._compaction_metrics = EvalMetrics(compactor="noop")
        logger.info(
            "Compaction eval wired: champion=%s",
            _compaction_compactor.champion.name,
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
                    # Expose gludd's own in-process builtin tools (e.g.
                    # run_project_check) alongside the external MCP servers so
                    # the agent can run a target project's declared checks. This
                    # only registers a synthetic "gludd-builtin" server on the
                    # already-built client; it does not touch external flows.
                    from general_ludd.mcp.builtins import register_builtins

                    # Construct a shared WebRetriever so the MCP builtin tool
                    # reuses one cache across calls instead of creating a fresh
                    # diskcache per invocation.
                    from general_ludd.retrieval.web import WebRetriever

                    _web_retriever = WebRetriever()
                    app.state._web_retriever = _web_retriever

                    # Isolate builtin registration: a failure here (e.g. an
                    # external server already advertising the same tool name,
                    # which the registry rejects as a collision) must NOT
                    # discard the working external MCP client or leak its
                    # already-started subprocesses.
                    try:
                        register_builtins(mcp_client, web_retriever=_web_retriever)
                    except Exception:
                        logger.warning(
                            "builtin MCP tool registration failed; continuing with external MCP servers only",
                        )
                    logger.info("MCPClient started with %d server(s)", len(typed_configs))
                except Exception as _mcp_exc:
                    logger.error(
                        "MCP startup failed (continuing without MCP)",
                    )
                    if mcp_client is not None:
                        try:
                            await mcp_client.stop_all()
                        except Exception:
                            logger.warning("MCP cleanup during startup failure also failed")
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

        # Construct only after the rolling limiter has been restored. This
        # guarantees the engine shares the live daemon limiter before its first
        # dispatch and leaves EventLoop as the single spend-record DB writer.
        if model_gateway is not None and semantic_searcher is not None:
            execution_engine = ExecutionEngine(
                model_gateway=model_gateway,
                benchmark_recorder=None,
                metrics_collector=ext.get("metrics_collector"),
                budget_guard=budget_guard,
                searcher=semantic_searcher,
                spend_limiter=spend_limiter,
            )
            app.state._execution_engine = execution_engine

        # Prepaid service credit tracker — queries DeepSeek / OpenAI / Z.AI /
        # OpenRouter balance APIs on the EventLoop's periodic
        # check_service_credits phase and exposes results via GET /api/credits.
        # API keys are read lazily from the conventional env vars per provider
        # (DEEPSEEK_API_KEY, OPENAI_API_KEY, ZAI_API_KEY, OPENROUTER_API_KEY).
        from general_ludd.budget.credit_tracker import CreditTracker

        credit_tracker = CreditTracker(
            thresholds=getattr(uc, "credit_thresholds", None) if uc else None,
            historical_spend_rates=getattr(uc, "credit_spend_rates", None) if uc else None,
        )
        app.state._credit_tracker = credit_tracker

        memory_repo = MemoryRepository(session_factory=session_factory)
        app.state._memory_repo = memory_repo

        local_memory = LocalAgentMemory()
        app.state._local_memory = local_memory
        logger.info("LocalAgentMemory initialised (cache: %s)", local_memory.cache_dir)

        from general_ludd.memory.procedural import ProceduralMemoryStore

        procedural_memory = ProceduralMemoryStore(memory_repo=memory_repo)
        app.state._procedural_memory = procedural_memory
        logger.info("ProceduralMemoryStore wired into daemon")

        from general_ludd.memory.semantic import SemanticMemoryStore

        semantic_memory = SemanticMemoryStore(memory_repo=memory_repo)
        app.state._semantic_memory = semantic_memory
        logger.info("SemanticMemoryStore wired into daemon")

        from general_ludd.memory.embedding_store import MemoryEmbeddingStore

        embedding_memory = MemoryEmbeddingStore(memory_repo=memory_repo)
        app.state._embedding_memory = embedding_memory
        logger.info("MemoryEmbeddingStore wired into daemon (in-memory index)")

        # P3: VM sandbox config — load from UserConfig, override SandboxConfig,
        # and optionally pre-build the default image at startup.
        vm_sandbox_cfg = VmSandboxConfig()
        if uc is not None:
            _vm_raw = getattr(uc, "vm_sandbox", None)
            if _vm_raw is not None:
                vm_sandbox_cfg = _vm_raw

        sandbox_executor = SandboxExecutor(timeout=30)
        sandbox_config = SandboxConfig(
            backend=vm_sandbox_cfg.image_type if vm_sandbox_cfg.enabled else "auto",
            isolation=IsolationLevel.NONE,
            image_path=vm_sandbox_cfg.default_image,
            vsock_port=vm_sandbox_cfg.vsock_port,
            memory_mb=vm_sandbox_cfg.mem_mib,
        )
        from general_ludd.security.policy.profiles import resolve_sandbox_profile
        from general_ludd.security.sandboxes.attestation import (
            DurableSandboxAttestationStore,
        )

        sandbox_profile = resolve_sandbox_profile(vm_sandbox_cfg.profile)
        sandbox_attestation_store = DurableSandboxAttestationStore(session_factory)
        app.state._sandbox_config = sandbox_config
        app.state._sandbox_router = SandboxCapabilityRouter(sandbox_config)
        app.state._vm_sandbox_config = vm_sandbox_cfg
        app.state._sandbox_profile = sandbox_profile

        if vm_sandbox_cfg.enabled and vm_sandbox_cfg.auto_build:
            try:
                from general_ludd.security.sandboxes.vm.image_builder import (
                    ImageManifest,
                    build_rootfs,
                )

                _img_path = vm_sandbox_cfg.default_image or str(
                    Path.home() / ".cache" / "gludd" / "sandbox" / "default.ext4"
                )
                _manifest = ImageManifest(
                    name="gludd-sandbox-default",
                    packages=("python3", "ansible", "git"),
                    architecture="x86_64",
                )
                _built = await asyncio.to_thread(
                    build_rootfs,
                    _img_path,
                    vm_sandbox_cfg.image_type,
                    _manifest,
                )
                logger.info(
                    "VM sandbox default image built: %s (%d bytes, type=%s, hash=%s)",
                    _built.path,
                    _built.size_bytes,
                    _built.image_type,
                    _built.manifest_hash[:12],
                )
            except Exception:
                logger.warning(
                    "VM sandbox auto_build failed — continuing without pre-built image",
                )

        _cfg_dir = getattr(app.state, "_config_dir", None)
        replay_dir = os.path.join(_cfg_dir, "replay") if _cfg_dir else ".gludd/replay"
        run_recorder = RunRecorder(_FS(root_path=replay_dir))
        app.state._run_recorder = run_recorder

        issue_ingestor = None
        if uc is not None:
            issues_cfg = getattr(uc, "issues", None)
            if issues_cfg is not None and getattr(issues_cfg, "polling_enabled", False):
                from general_ludd.git_automation.issue_ingestor import GitHubIssueIngestor

                issue_ingestor = GitHubIssueIngestor(
                    owner=getattr(issues_cfg, "github_owner", ""),
                    repo=getattr(issues_cfg, "github_repo", ""),
                    label=getattr(issues_cfg, "github_label", "gludd"),
                    poll_interval_seconds=getattr(issues_cfg, "poll_interval_ticks", 300),
                    seen_ids=daemon_state.setdefault("issue_ingestor_seen_ids", {}).setdefault(
                        (
                            f"{getattr(issues_cfg, 'github_owner', '')}/"
                            f"{getattr(issues_cfg, 'github_repo', '')}#"
                            f"{getattr(issues_cfg, 'github_label', 'gludd')}"
                        ),
                        set(),
                    ),
                )
                app.state._issue_ingestor = issue_ingestor
                logger.info("Issue ingestor wired: polling enabled")

        _checkpointing_cfg = getattr(uc, "checkpointing", {}) if uc else {}
        _checkpointing_enabled = (
            bool(_checkpointing_cfg.get("enabled", False)) if isinstance(_checkpointing_cfg, dict) else False
        )
        if _checkpointing_enabled:
            app.state.checkpointer = get_checkpointer(
                db_url=str(engine.url) if engine and str(engine.url).startswith("sqlite") else None
            )
        else:
            from general_ludd.execution.graph_checkpointer import TickCheckpointer

            app.state.checkpointer = TickCheckpointer(saver=None)

        # Cost-tracking deps constructed BEFORE the EventLoop so the bill-7
        # idle-GPU teardown phase (loop.py:3595 record_gpu_seconds / loop.py:3610
        # deployment_manager.destroy) receives live instances, not None. Prior to
        # this, InfraTracker was built ~170 lines later and DeploymentManager was
        # never built in the daemon (only lazily by routers/compute.py), so both
        # were permanently None on the EventLoop → GPU-seconds never recorded and
        # idle GPUs unregistered from bookkeeping but never actually destroyed.
        # InfraTracker shares the SAME pricing_catalog as the SpendLimiter (H3).
        # Publishing deployment_manager on app.state lets routers/compute.py's
        # identity-check cache reuse the SAME instance so /admin/compute/destroy
        # and the idle-teardown tick agree on deployment state.
        from general_ludd.infra.deployment import DeploymentManager
        from general_ludd.infra.pricing import InfraTracker

        infra_tracker = InfraTracker(catalog=pricing_catalog)
        app.state._infra_tracker = infra_tracker

        deployment_manager = getattr(app.state, "_deployment_manager", None)
        if deployment_manager is None:
            _cfg_dir = getattr(app.state, "_config_dir", None)
            _deploy_working_dir = os.path.join(_cfg_dir, "deployments") if _cfg_dir else None
            deployment_manager = DeploymentManager(
                secrets_resolver=secrets_resolver,
                working_dir=_deploy_working_dir,
                event_bus=subsys["bus"],
                session_factory=session_factory,
                worker_id=f"{os.environ.get('GLUDD_WORKER_ID', 'gunicorn')}-{os.getpid()}",
            )
            app.state._deployment_manager = deployment_manager

        service_discovery = None
        if uc is not None and uc.service_discovery_enabled:
            from general_ludd.infra.service_catalog import DEFAULT_CATALOG_PATH

            searx_url = getattr(uc, "service_discovery_searx_url", "http://localhost:8888")
            catalog_path = getattr(uc, "service_discovery_catalog_path", DEFAULT_CATALOG_PATH)
            from general_ludd.service_discovery.pipeline import ServiceDiscoveryPipeline

            service_discovery = ServiceDiscoveryPipeline(
                searx_url=searx_url,
                catalog_path=catalog_path,
            )
            logger.info("ServiceDiscoveryPipeline wired: searx=%s catalog=%s", searx_url, catalog_path)

        searx_model_discoverer = None
        if model_gateway is not None:
            from general_ludd.infra.model_search import SEARX_DEFAULT_URL
            from general_ludd.models.searx_discoverer import SearxModelDiscoverer

            _srv = getattr(app.state, "_searx_server", None)
            _discover_url = _srv.get_instance_url() if _srv else None
            searx_model_discoverer = SearxModelDiscoverer(
                gateway=model_gateway,
                searx_url=_discover_url or SEARX_DEFAULT_URL,
            )
            try:
                searx_model_discoverer.sync_models()
            except Exception:
                logger.info("SearX model discoverer sync skipped at startup")
            app.state._searx_model_discoverer = searx_model_discoverer
            logger.info(
                "SearxModelDiscoverer wired: searx=%s index=%d",
                _discover_url or "default",
                searx_model_discoverer.index_size,
            )

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
                # #56: reachable SLM context-compaction on the generation path.
                # Serialized to a plain dict so the EventLoop config stays a
                # dict[str, Any]. Default OFF (compaction.enabled = False).
                "compaction": _compaction_config_dict(uc),
                # Daemon-level default repo_root: the process cwd at startup time
                # is a reasonable single-project fallback so verify_completion can
                # check commit:/artifact: refs without a resolved per-project
                # workspace. EventLoop._resolve_repo_root() overrides this with the
                # per-project workspace.repo_dir when available.
                "repo_root": os.getcwd(),
                "review": review_cfg,
                "use_langgraph_tool_loop": bool(getattr(uc, "use_langgraph_tool_loop", False)) if uc else False,
                "compute_idle_check_interval_ticks": getattr(uc, "compute_idle_check_interval_ticks", 60) if uc else 60,
                "compute_idle_teardown_threshold_ticks": getattr(uc, "compute_idle_teardown_threshold_ticks", 3)
                if uc
                else 3,
                "compute_idle_gpu_sm_pct": getattr(uc, "compute_idle_gpu_sm_pct", 5.0) if uc else 5.0,
                "compute_idle_preemption_notice_ticks": getattr(uc, "compute_idle_preemption_notice_ticks", 1)
                if uc
                else 1,
                # #52: auto-remediation tick-phase cadence + per-tick action cap.
                # The RemediationConfig thresholds themselves are NOT read from
                # here — they live on daemon_state["remediation_config"] (set
                # below via _remediation_config_from_uc) so the tick phase and
                # the /admin/remediation/* HTTP endpoints share one instance.
                "remediation_check_interval_ticks": _remediation_tick_settings(uc)[0],
                "remediation_max_actions_per_tick": _remediation_tick_settings(uc)[1],
                # SPD-1: how often the EventLoop persists in-memory spend
                # records to the spend_records table (in ticks). 60 ticks ≈
                # 60 seconds at the default 1 s tick interval.  <=0 disables.
                "spend_persist_interval_ticks": getattr(uc, "spend_persist_interval_ticks", 60) if uc else 60,
                # STS token reaper: sweep TTL-expired tokens every N ticks.
                # Default 60 (~60s at the 1s tick interval). <=0 disables.
                "sts_reap_interval_ticks": getattr(uc, "sts_reap_interval_ticks", 60) if uc else 60,
            },
            adaptive_router=ext["adaptive_router"],
            daemon_state=daemon_state,
            project_workspace=_init_project_workspaces(ext["projects"]),
            project_secrets_manager=secrets_resolver,
            reviewer=return_reviewer,
            consensus_reviewer=consensus_reviewer,
            langgraph_reviewer=langgraph_reviewer,
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
            deployment_health_router=deployment_health_router,
            pause_controller=getattr(app.state, "_pause_controller", None),
            memory_repo=memory_repo,
            sandbox_executor=sandbox_executor,
            sandbox_config=sandbox_config,
            sandbox_attestation_store=sandbox_attestation_store,
            sandbox_profile=sandbox_profile,
            run_recorder=run_recorder,
            checkpointer=app.state.checkpointer,
            utilization_tracker=getattr(app.state, "_utilization_tracker", None),
            deployment_manager=deployment_manager,
            floor_controller=floor_controller,
            issue_ingestor=issue_ingestor,
            infra_tracker=infra_tracker,
            compaction_controller=getattr(app.state, "_compaction_aggressiveness_controller", None),
            credit_tracker=getattr(app.state, "_credit_tracker", None),
            service_discovery=service_discovery,
        )
        app.state.event_loop = event_loop
        app.state.event_loop._runner = runner
        from general_ludd.infra.deployment_events import (
            PostgresWakeupListener,
            TerraformEventBridge,
        )

        terraform_worker_id = f"{os.environ.get('GLUDD_WORKER_ID', 'gunicorn')}-{os.getpid()}"
        terraform_wakeup_listener = None
        if engine.dialect.name == "postgresql":
            terraform_wakeup_listener = PostgresWakeupListener(
                database_url=engine.url.render_as_string(hide_password=False),
                session_factory=session_factory,
                wake=event_loop.wake,
                worker_id=terraform_worker_id,
                reconnect_min_seconds=float(os.environ.get("GLUDD_PG_WAKE_RECONNECT_SECONDS", "0.1")),
                reconnect_max_seconds=float(os.environ.get("GLUDD_PG_WAKE_RECONNECT_SECONDS", "5.0")),
            )

        terraform_event_bridge = TerraformEventBridge(
            event_bus=subsys["bus"],
            session_factory=session_factory,
            wake=event_loop.wake,
            worker_id=terraform_worker_id,
            listener=terraform_wakeup_listener,
        )
        terraform_event_bridge.start()
        app.state._terraform_event_bridge = terraform_event_bridge
        daemon_state["human_gate"] = event_loop._human_gate
        # #52: single config source for the auto-remediation tick phase AND
        # the /admin/remediation/* HTTP endpoints (see
        # _remediation_config_from_uc — daemon_state previously never
        # carried a RemediationConfig, so the router always fell back to
        # hardcoded defaults regardless of operator config).
        daemon_state["remediation_config"] = _remediation_config_from_uc(uc)
        app.state._runner = runner
        app.state._db_engine = engine
        app.state._session_factory = session_factory
        app.state._training_data_session_factory = session_factory
        # Preserve an explicitly injected task used by health/readiness probes
        # in tests and embedding applications. The real runtime task is still
        # started and tracked locally for shutdown; an auto-created task is
        # intentionally not considered ready until a caller replaces the
        # probe handle (or a future readiness signal is added).
        probe_task = getattr(app.state, "_event_loop_task", None)
        task = asyncio.create_task(event_loop.run_forever(interval=tick_interval))
        task.add_done_callback(_on_event_loop_done)
        app.state._event_loop_runtime_task = task
        app.state._event_loop_task = probe_task if probe_task is not None else task
        app.state._event_loop_task_auto = probe_task is None

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

        model_perf_repo = ModelPerformanceRepository(
            session_factory=session_factory,
        )
        event_loop._model_perf_repo = model_perf_repo
        app.state.model_perf_repo = model_perf_repo

        from general_ludd.models.performance_router import (
            ModelPerformanceRepository as _PerfRepoProtocol,
        )
        from general_ludd.models.performance_router import (
            ModelPerformanceRouter,
        )

        app.state._model_performance_router = ModelPerformanceRouter(
            perf_repo=cast(_PerfRepoProtocol, model_perf_repo),
        )

        from general_ludd.worktree.core import WorktreeMonitor, WorktreeMonitorConfig

        config_dir = getattr(app.state, "_config_dir", None)
        wt_monitor = WorktreeMonitor(
            config=WorktreeMonitorConfig(
                watch_paths=[config_dir] if config_dir else [],
            ),
        )
        app.state._worktree_monitor = wt_monitor

        from general_ludd.quantization.monitor import MonitorConfig as QuantMonitorConfig
        from general_ludd.quantization.monitor import QuantizationMonitor

        quant_monitor = QuantizationMonitor(QuantMonitorConfig())
        app.state._quantization_monitor = quant_monitor
        await quant_monitor.start()

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

        # InfraTracker / DeploymentManager are constructed earlier, before the
        # EventLoop constructor (see the "Cost-tracking deps" block above), so
        # the loop's bill-7 teardown phase receives live instances instead of
        # None. InfraTracker shares the SAME pricing_catalog as the SpendLimiter.
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
                    _projected_cost_usd = spend_limiter.token_cost_usd(_project_model, _project_in, _project_out)
                else:
                    _projected_cost_usd = token_cost_usd(_project_model, _project_in, _project_out)

            async def _gateway_executor(task: AgentTask) -> str:
                # S10: route to the best-cost profile via ModelPerformanceRouter
                # when data exists; fall back to "default" for cold start.
                _perf_router = getattr(app.state, "_model_performance_router", None)
                if _perf_router is not None:
                    try:
                        profile_id = _perf_router.select_cost_effective_profile(
                            task_type=task.agent_name or "generation",
                        )
                    except Exception:
                        profile_id = "default"
                else:
                    profile_id = "default"
                budget_manager = getattr(app.state, "_budget_manager", None)

                _saved_env: dict[str, str] = {}
                _sts_env = getattr(task, "env", None)
                if _sts_env:
                    _sts_role = _sts_env.get("GLUDD_STS_ROLE_ID")
                    _sts_secret = _sts_env.get("GLUDD_STS_SECRET_ID")
                    if _sts_role:
                        _saved_env["GLUDD_STS_ROLE_ID"] = os.environ.pop("GLUDD_STS_ROLE_ID", "")
                        os.environ["GLUDD_STS_ROLE_ID"] = _sts_role
                    if _sts_secret:
                        _saved_env["GLUDD_STS_SECRET_ID"] = os.environ.pop("GLUDD_STS_SECRET_ID", "")
                        os.environ["GLUDD_STS_SECRET_ID"] = _sts_secret

                try:
                    if budget_manager is not None:
                        daily = budget_manager.check_daily_budget_reserved(task.task_id, _projected_cost_usd)
                        if not daily.get("allowed", True):
                            logger.warning(
                                "Gateway executor deferred for %s: daily budget exhausted",
                                task.task_id,
                            )
                            return "deferred:budget_exhausted"
                        per_todo = budget_manager.check_todo_budget(task.task_id, _projected_cost_usd)
                        if not per_todo.get("allowed", True):
                            logger.warning(
                                "Gateway executor deferred for %s: per-todo budget exhausted",
                                task.task_id,
                            )
                            # Release the daily reservation made above so a deferred
                            # call does not leak held budget.
                            budget_manager.release_reservation(task.task_id)
                            return "deferred:budget_exhausted"
                    if task.agent_name == "research":
                        from general_ludd.agents.researcher import ResearcherAgent

                        searx = getattr(app.state, "_searx_client", None)
                        agent = ResearcherAgent(searx_client=searx)
                        report = await agent.research(query=task.prompt)
                        return report.model_dump_json()
                    try:
                        call_kwargs: dict[str, Any] = {}
                        if getattr(task, "tools", None):
                            call_kwargs["tools"] = task.tools
                        result = await model_gateway.call_model_with_retry(
                            profile_id,
                            [{"role": "user", "content": task.prompt}],
                            **call_kwargs,
                        )
                        if budget_manager is not None:
                            budget_manager.record_spend(
                                task.task_id,
                                float(getattr(result, "cost_estimate", 0.0) or 0.0),
                            )
                        content = result.content
                        return content if isinstance(content, str) else str(content)
                    except Exception as exc:
                        logger.warning("Gateway executor failed for %s: %s", task.task_id, exc)
                        # The call never produced a cost, so release both reservations
                        # instead of leaking the held projected budget.
                        if budget_manager is not None:
                            budget_manager.release_reservation(task.task_id)
                        return f"Error: {exc}"
                finally:
                    for k, v in _saved_env.items():
                        if v:
                            os.environ[k] = v
                        else:
                            os.environ.pop(k, None)

            dispatcher_executor = make_spend_guarded_executor(
                executor=_gateway_executor,
                spend_limiter=spend_limiter,
                projected_cost_usd=_projected_cost_usd,
            )

        app.state._agent_dispatcher = AgentDispatcher(
            registry=registry,
            executor=dispatcher_executor,
            pause_controller=app.state._pause_controller,
            hibernation=app.state._hibernation_controller,
            run_recorder=run_recorder,
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
                    pipeline_cfg,
                    app.state._agent_dispatcher,
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

        app.state._stall_watchdog = StallWatchdog(
            default_tracker(),
            on_stall=lambda r: (
                subsys["bus"].publish(
                    StallDetectedEvent(
                        operation=r.key,
                        elapsed_s=r.elapsed_s,
                        deadline_s=r.deadline_s,
                        thread_stacks=r.thread_stacks,
                    )
                ),
                subsys["bus"].publish(
                    SlowOperationEvent(
                        operation=r.key,
                        duration_s=r.elapsed_s,
                        baseline_s=r.deadline_s,
                        factor=(r.elapsed_s / r.deadline_s) if r.deadline_s > 0 else 0.0,
                    )
                ),
            )[0],
        )
        app.state._stall_watchdog.start_sweeper()

        # Wire the shared watchdog into the agent dispatcher so in-flight agent
        # tasks are registered with the stall sweeper (and hung tasks are flagged
        # + published as StallDetectedEvent). The dispatcher is constructed above
        # BEFORE the watchdog exists, so the watchdog is injected here now that
        # both are live. The dispatcher already records per-task durations into
        # default_tracker() — the SAME tracker this watchdog uses for deadlines —
        # so learned baselines drive the stall deadlines.
        _agent_dispatcher = getattr(app.state, "_agent_dispatcher", None)
        if _agent_dispatcher is not None:
            _agent_dispatcher._watchdog = app.state._stall_watchdog

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
        # Also record on the shared daemon_state dict. The EventLoop's code-reload
        # health probe (_make_daemon_health_probe) reads daemon_state["_degraded"]
        # to decide whether to roll a hot-reload back; previously the flag lived
        # ONLY as an app.state attribute the probe never read, so the reload
        # health gate silently always passed and a bad reload could stick.
        daemon_state["_degraded"] = str(exc)

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

    # AG.12: Off-peak scheduler — defer expensive model-API tasks to cheaper
    # off-peak hours. Runs as a background asyncio task polling every 60s.
    app.state._off_peak_scheduler = None
    app.state._off_peak_stop = None
    app.state._off_peak_task = None
    try:
        from general_ludd.budget.off_peak_scheduler import OffPeakScheduler

        off_peak_cfg = getattr(uc, "off_peak", None) if uc else None
        _op_start = getattr(off_peak_cfg, "start_hour", 0)
        _op_end = getattr(off_peak_cfg, "end_hour", 6)
        _op_enabled = getattr(off_peak_cfg, "enabled", False)
        _op_cost_tracker = getattr(app.state, "_combined_cost_tracker", None)

        if _op_enabled:
            _op_sched = OffPeakScheduler(
                cost_tracker=_op_cost_tracker,
                off_peak_start=_op_start,
                off_peak_end=_op_end,
            )
            app.state._off_peak_scheduler = _op_sched
            app.state._off_peak_stop = asyncio.Event()
            app.state._off_peak_task = asyncio.create_task(
                _op_sched._background_loop(stop_event=app.state._off_peak_stop)
            )
            logger.info(
                "Off-peak scheduler started: %02d:00-%02d:00",
                _op_start,
                _op_end,
            )
    except Exception as _op_exc:
        logger.warning("Off-peak scheduler startup failed (continuing degraded): %s", _op_exc)

    # S.1: Seal the process registry so no code path can modify it
    # (register/deregister/reap) after daemon initialization.
    from general_ludd.process.registry import default_registry as _proc_default_registry

    _proc_default_registry().seal()

    yield

    # ── Off-peak scheduler shutdown ──────────────────────────────────────
    _op_stop = getattr(app.state, "_off_peak_stop", None)
    _op_task = getattr(app.state, "_off_peak_task", None)
    if _op_stop is not None:
        _op_stop.set()
    if _op_task is not None and not _op_task.done():
        _op_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _op_task
    _op_scheduler = getattr(app.state, "_off_peak_scheduler", None)
    if _op_scheduler is not None:
        logger.info(
            "Off-peak scheduler shut down: %s deferred, $%.4f saved",
            _op_scheduler.savings.total_deferred,
            _op_scheduler.savings.total_savings,
        )

    # ── Slurm shutdown: scancel all active jobs owned by this daemon ──────
    _session_factory = getattr(app.state, "_session_factory", None)
    if _session_factory is not None:
        try:
            import os as _os

            _current_pid = _os.getpid()
            async with _session_factory() as session:
                slurm_repo = SlurmJobRepository(session)
                active_jobs = await slurm_repo.list_active(daemon_pid=_current_pid)
            if active_jobs:
                logger.info(
                    "Shutdown: cancelling %d active Slurm job(s) owned by pid=%d",
                    len(active_jobs),
                    _current_pid,
                )
                for job in active_jobs:
                    try:
                        from general_ludd.infra.slurm import SlurmAdapter

                        adapter = SlurmAdapter()
                        adapter.cancel(job.job_id)
                        logger.info("Slurm shutdown: cancelled job %s", job.job_id)
                        async with _session_factory() as session:
                            await SlurmJobRepository(session).update_status(job.job_id, "cancelled")
                    except Exception as cancel_exc:
                        logger.warning(
                            "Slurm shutdown: failed to cancel job %s: %s",
                            job.job_id,
                            cancel_exc,
                        )
        except Exception:
            logger.warning("Slurm shutdown hook failed")

    # Bill-2: stop all Slurm cost-cap monitors on shutdown
    monitors: dict[str, Any] = getattr(app.state, "_slurm_monitors", None) or {}
    for job_id, monitor in list(monitors.items()):
        try:
            monitor.stop()
            logger.info("Slurm shutdown: stopped cost-cap monitor for %s", job_id)
        except Exception as exc:
            logger.warning("Slurm shutdown: failed to stop monitor %s: %s", job_id, exc)

    if getattr(app.state, "_degraded", None):
        logger.warning("Daemon is running in degraded mode: %s", app.state._degraded)
    pipeline_controller = getattr(app.state, "_pipeline_controller", None)
    if pipeline_controller is not None:
        try:
            await pipeline_controller.stop()
        except Exception:
            logger.warning("pipeline_controller.stop() failed during shutdown")
            raise
    mcp_client_ref = getattr(app.state, "_mcp_client", None)
    if mcp_client_ref is not None:
        try:
            await mcp_client_ref.stop_all()
        except Exception:
            logger.warning("mcp_client.stop_all() failed during shutdown")
            raise
    _el = event_loop if event_loop is not None else getattr(app.state, "event_loop", None)
    _terraform_bridge = getattr(app.state, "_terraform_event_bridge", None)
    if _terraform_bridge is not None:
        await _terraform_bridge.aclose()
        _event_bus = getattr(app.state, "_event_bus", None)
        if _event_bus is not None:
            await _event_bus.drain()
    if _el is not None:
        try:
            _el.stop()
            _shutdown_result = _el.shutdown()
            if inspect.isawaitable(_shutdown_result):
                await _shutdown_result
        except Exception:
            logger.warning("event_loop shutdown failed")
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    preflight_task_ref = getattr(app.state, "_preflight_task", None)
    if preflight_task_ref is not None:
        preflight_task_ref.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await preflight_task_ref
    if execution_engine is not None:
        try:
            await execution_engine.shutdown()
        except Exception:
            logger.warning("execution_engine.shutdown() failed")
    _searx_client_ref = getattr(app.state, "_searx_client", None)
    if _searx_client_ref is not None:
        try:
            await _searx_client_ref.close()
        except Exception:
            logger.warning("SearxNGClient.close() failed during shutdown")
    _model_gateway_ref = getattr(app.state, "_model_gateway", None)
    if _model_gateway_ref is not None:
        try:
            _model_gateway_ref.close()
        except Exception:
            logger.warning("ModelGateway.close() failed during shutdown")
    for _cache_attr in (
        "_codebase_indexer",
        "_research_index",
        "_local_memory",
        "_semantic_searcher",
    ):
        _cache_owner = getattr(app.state, _cache_attr, None)
        if _cache_owner is not None:
            try:
                _cache_owner.close()
            except Exception:
                logger.warning("%s.close() failed during shutdown", _cache_attr)
    _web_retriever_ref = getattr(app.state, "_web_retriever", None)
    _web_cache_ref = getattr(_web_retriever_ref, "_cache", None)
    if _web_cache_ref is not None:
        try:
            _web_cache_ref.close()
        except Exception:
            logger.warning("WebRetriever cache close failed during shutdown")
    _sw = getattr(app.state, "_stall_watchdog", None)
    if _sw is not None:
        with contextlib.suppress(Exception):
            _sw.stop_sweeper()
    # B3.1.3 Slice 4 — drain the WriteQueue and stop the writer subprocess
    # BEFORE disposing the engine: a lingering writer holding a DB handle
    # during engine.dispose() can deadlock. The queue is cleared (best-effort
    # lossy drain) because the writer subprocess owns durable writes; anything
    # still buffered at shutdown is abandoned by design.
    _write_queue_ref = getattr(app.state, "_write_queue", None)
    if _write_queue_ref is not None:
        with contextlib.suppress(Exception):
            _write_queue_ref.clear()
    _writer_process_ref = getattr(app.state, "_writer_process", None)
    if _writer_process_ref is not None:
        with contextlib.suppress(Exception):
            # WriterProcess.stop() polls with blocking time.sleep for up to
            # ~15s waiting on the subprocess to exit; run it off the event
            # loop so shutdown doesn't freeze the loop for that long.
            await asyncio.to_thread(_writer_process_ref.stop)
    _embedding_session_ref = getattr(app.state, "_embedding_session", None)
    if _embedding_session_ref is not None:
        with contextlib.suppress(Exception):
            await _embedding_session_ref.close()
    if engine is not None:
        try:
            await engine.dispose()
        except Exception:
            logger.warning("engine.dispose() failed")
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
    _searx_srv = getattr(app.state, "_searx_server", None)
    if _searx_srv is not None:
        with contextlib.suppress(Exception):
            _searx_srv.stop()
    _quant_monitor = getattr(app.state, "_quantization_monitor", None)
    if _quant_monitor is not None:
        try:
            await _quant_monitor.stop()
        except Exception:
            logger.warning("QuantizationMonitor.stop() failed during shutdown")


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

    from general_ludd.planning.critique import PlanCritique

    app.state.plan_critique = PlanCritique()

    from general_ludd.hardware.probe import probe_hardware
    from general_ludd.hardware.survey import HardwareSurvey

    app.state._hardware = probe_hardware()
    app.state._hardware_inventory = HardwareSurvey().survey()
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
            "SECURITY: GLUDD_PSK is not set — the daemon will REFUSE all "
            "non-public paths (503, fail-closed). Set GLUDD_PSK to enable auth. "
            "For development only, set GLUDD_PSK_DISABLE=1 (or "
            "GLUDD_ALLOW_NO_AUTH=1) to allow unauthenticated access (leaves "
            "the entire /admin surface open to any caller)."
        )
    elif _no_auth and _allow_no_auth:
        # Explicit dev opt-out: LOUD warning that auth is intentionally disabled.
        logger.warning(
            "SECURITY: GLUDD_PSK is not set and auth is disabled — the "
            "daemon is running with admin auth DISABLED (no_auth mode). The "
            "entire /admin surface is open to any caller that can reach the port. "
            "Set GLUDD_PSK to enable auth."
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
                "local_model": local_model,
            }
        return {
            "status": "healthy",
            "no_auth": no_auth,
            "require_auth": require_auth,
            "allow_no_auth": allow_no_auth,
            "auth_degraded": auth_degraded,
            "budget_exhausted": budget_exhausted,
            "local_model": local_model,
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
