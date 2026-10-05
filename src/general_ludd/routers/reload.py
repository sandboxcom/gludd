"""Hot-reload, rollback, and worker coordination HTTP routes."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field, StrictFloat, StrictStr, field_validator

from general_ludd.ansible.runner import AnsibleRunnerAdapter
from general_ludd.daemon import _get_or_create_extended_subsystems, _get_or_create_subsystems
from general_ludd.events.types import ConfigReloadedEvent
from general_ludd.prompts.registry import PromptRegistry
from general_ludd.reload.hot_reloader import HotReloader, ReloadScope
from general_ludd.routers._runtime import IdempotencyStore, StrictRuntimeRequest
from general_ludd.security import is_safe_fetch_url
from general_ludd.security.capability_guard import RequireCapability
from general_ludd.security.state import project_state
from general_ludd.self_update.module_snapshot import (
    ModuleSnapshot,
    restore_modules,
    snapshot_modules,
)

logger = logging.getLogger(__name__)

_FORBIDDEN_HEADERS: frozenset[str] = frozenset(
    {"authorization", "host", "content-length", "transfer-encoding", "cookie"}
)
_MAX_HEADER_KEY_LEN = 1024
_MAX_HEADER_VAL_LEN = 1024


class ReloadRequest(BaseModel):
    """Describe a scoped hot-reload request and optional rollback snapshot."""

    scope: str = "all"
    snapshot_modules: list[str] | None = Field(
        default=None,
        description="Module names to snapshot before reload (for rollback support). "
        "When None, snapshots all currently-loaded general_ludd modules.",
    )


class RollbackRequest(BaseModel):
    """Select module snapshots to restore after a failed reload."""

    module_names: list[str] | None = Field(
        default=None,
        description="Module names to restore. When None, restores all modules in the most recent snapshot.",
    )


class RegisterWorkerRequest(BaseModel):
    """Register one safe remote worker endpoint."""

    worker_id: str
    address: str

    @field_validator("address")
    @classmethod
    def _validate_address_ssrf(cls, v: str) -> str:
        """Reject non-safe worker addresses at registration time (SSRF/PSK-leak guard)."""
        if not is_safe_fetch_url(v):
            raise ValueError(
                "address must use https and must not target loopback, link-local, RFC-1918, or cloud-metadata addresses"
            )
        return v


class RegisterHookRequest(BaseModel):
    """Register one bounded and SSRF-checked reload event hook."""

    event_name: str
    url: str
    headers: dict[str, str] | None = Field(default=None, repr=False)
    retry_count: int = 1
    timeout_seconds: int = 10

    @field_validator("url")
    @classmethod
    def _validate_url_ssrf(cls, v: str) -> str:
        """Reject non-safe URLs at registration time (SSRF guard)."""
        if not is_safe_fetch_url(v):
            raise ValueError(
                "url must use https and must not target loopback, link-local, RFC-1918, or cloud-metadata addresses"
            )
        return v

    @field_validator("headers")
    @classmethod
    def _validate_headers(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        """Reject forbidden headers and cap key/value lengths."""
        if v is None:
            return v
        for key, val in v.items():
            if len(key) > _MAX_HEADER_KEY_LEN:
                raise ValueError(f"header key exceeds maximum length of {_MAX_HEADER_KEY_LEN}: {key!r}")
            if len(val) > _MAX_HEADER_VAL_LEN:
                raise ValueError(f"header value for {key!r} exceeds maximum length of {_MAX_HEADER_VAL_LEN}")
            if key.lower() in _FORBIDDEN_HEADERS:
                raise ValueError(f"header {key!r} is not permitted in webhook registrations")
        return v


class CodeReloadRequest(StrictRuntimeRequest):
    """Digest-bound leaf-module rotation request."""

    module_name: StrictStr = Field(
        min_length=1,
        max_length=512,
        pattern=r"^general_ludd(?:\.[A-Za-z_][A-Za-z0-9_]*)+$",
    )
    candidate_source_path: StrictStr = Field(min_length=1, max_length=4096)
    expected_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    base_source_path: StrictStr | None = Field(default=None, max_length=4096)
    role: StrictStr | None = Field(default=None, max_length=128)
    health_url: StrictStr | None = Field(default=None, max_length=2048)
    health_timeout: StrictFloat = Field(default=5.0, ge=0.1, le=30.0)
    idempotency_key: StrictStr | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("candidate_source_path", "base_source_path")
    @classmethod
    def _require_absolute_source_path(cls, value: str | None) -> str | None:
        if value is not None and not Path(value).is_absolute():
            raise ValueError("source paths must be absolute")
        return value

    @field_validator("health_url")
    @classmethod
    def _confine_health_gate(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.path not in {"/readyz", "/healthz"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("health_url must be a loopback /readyz or /healthz endpoint")
        return value


def _make_hot_reloader(app: FastAPI) -> HotReloader:
    subsys = _get_or_create_subsystems(app)
    skills_dirs: list[str] = []
    config_dir = getattr(app.state, "_config_dir", None)
    if config_dir:
        global_skills = Path(config_dir) / "skills"
        if global_skills.is_dir():
            skills_dirs.append(str(global_skills))
    project_dir = getattr(app.state, "_project_gludd_dir", None)
    if project_dir is not None:
        project_skills = Path(project_dir) / "skills"
        if project_skills.is_dir():
            skills_dirs.append(str(project_skills))
    return HotReloader(
        config_dir=config_dir or str(project_state().directory("config")),
        event_bus=subsys["bus"],
        hook_system=subsys["hooks"],
        worker_broadcaster=subsys["broadcaster"],
        templates_dir=app.state._templates_dir,
        playbooks_dir=app.state._playbooks_dir,
        skills_dirs=skills_dirs or None,
        skill_registry=getattr(app.state, "_skill_registry", None),
        prompt_registry=getattr(app.state, "_prompt_registry", None),
        reload_lock=getattr(app.state, "_reload_lock", None),
    )

def _register_admin_routes(app: FastAPI) -> None:
    code_reload_store = IdempotencyStore()

    @app.post(
        "/admin/reload/code",
        dependencies=[Depends(RequireCapability(resource="admin:reload", action="write"))],
    )
    async def admin_reload_code(req: CodeReloadRequest) -> dict[str, object]:
        async def _run() -> dict[str, object]:
            reloader = _make_hot_reloader(app)

            def _health_check() -> bool:
                return not bool(getattr(app.state, "_degraded", False))

            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        reloader.reload_code_module,
                        module_name=req.module_name,
                        candidate_source_path=req.candidate_source_path,
                        health_check=_health_check if req.health_url else None,
                        role=req.role,
                        base_source_path=req.base_source_path,
                        expected_sha256=req.expected_sha256,
                    ),
                    timeout=req.health_timeout + 15.0,
                )
            except TimeoutError as exc:
                raise HTTPException(status_code=504, detail="code reload timed out") from exc
            details = dict(result.details)
            return {
                "success": result.success,
                "rolled_back": bool(details.get("rolled_back", False)),
                "module": req.module_name,
                "details": details,
                "error": result.error,
            }

        return await code_reload_store.run(
            key=req.idempotency_key,
            payload=req.model_dump(exclude={"idempotency_key"}, mode="json"),
            producer=_run,
        )

    @app.post("/admin/reload")
    async def admin_reload(req: ReloadRequest) -> dict[str, object]:
        # Snapshot modules before reload so a failed reload can be rolled back.
        import logging
        import sys

        _rl_logger = logging.getLogger(__name__)
        names_to_snapshot = req.snapshot_modules
        if names_to_snapshot is None:
            names_to_snapshot = [n for n in sys.modules if n.startswith("general_ludd") and sys.modules[n] is not None]
        if names_to_snapshot:
            pre_snapshot = snapshot_modules(names_to_snapshot)
            app.state._module_snapshot = pre_snapshot
            _rl_logger.info(
                "module snapshot taken: %d modules, %d warnings",
                len(pre_snapshot.modules),
                len(pre_snapshot.warnings),
            )

        reloader = _make_hot_reloader(app)
        scope = ReloadScope(req.scope)
        # reloader.reload is a sync op that (deep inside) does serial blocking
        # httpx.post per worker via the broadcaster — offload it so the whole
        # reload doesn't freeze the event loop.
        result = await asyncio.to_thread(reloader.reload, scope)
        return {
            "success": result.success,
            "scope": result.scope,
            "details": result.details,
            "error": result.error,
            "snapshot_modules": len(pre_snapshot.modules) if names_to_snapshot else 0,
        }

    @app.post("/admin/rollback")
    async def admin_rollback(req: RollbackRequest) -> dict[str, object]:
        snapshot: ModuleSnapshot | None = getattr(app.state, "_module_snapshot", None)
        if snapshot is None or not snapshot.modules:
            return {
                "success": False,
                "error": "no module snapshot available — run /admin/reload first",
                "restored": [],
            }
        if req.module_names is not None:
            filtered = {n: m for n, m in snapshot.modules.items() if n in set(req.module_names)}
            snapshot = ModuleSnapshot(
                modules=filtered,
                snapshot_at=snapshot.snapshot_at,
                warnings=snapshot.warnings,
            )
        restored = restore_modules(snapshot)
        app.state._module_snapshot = None
        return {
            "success": len(restored) > 0,
            "restored": restored,
            "warnings": snapshot.warnings,
        }

    @app.post("/admin/config/reload")
    async def admin_config_reload() -> dict[str, object]:
        from general_ludd.daemon import load_startup_config

        config_dir = getattr(app.state, "_config_dir", None)
        try:
            new_startup_config = load_startup_config(config_dir)
        except Exception as exc:
            logger.error("load_startup_config failed during config reload: %s", exc)
            return {"success": False, "error": str(exc)}

        app.state._startup_config = new_startup_config

        # Extract live-reloadable values from the new config
        new_uc = new_startup_config.get("user_config")
        live_reloadable: dict[str, object] = {
            "rules": new_startup_config.get("rules", []),
            "model_profiles": new_startup_config.get("model_profiles", []),
            "queues": getattr(new_uc, "queues", []) if new_uc else [],
            "budget": getattr(new_uc, "budget", {}) if new_uc else {},
            "self_improve": getattr(new_uc, "self_improve", {}) if new_uc else {},
        }

        merged: dict[str, str] = {}
        event_loop = getattr(app.state, "event_loop", None)
        if event_loop is not None and hasattr(event_loop, "config"):
            cfg = event_loop.config  # mutate in-place — preserve object identity
            for key, new_val in live_reloadable.items():
                old_val = cfg.get(key)
                if new_val != old_val:
                    cfg[key] = new_val
                    merged[key] = "updated"
                else:
                    merged[key] = "unchanged"
        else:
            logger.warning("admin_config_reload: no event_loop on app.state; config not merged")

        subsys = _get_or_create_subsystems(app)
        bus = subsys.get("bus")
        if bus is not None:
            bus.publish(ConfigReloadedEvent(scope="config"))

        return {"success": True, "merged": merged}

    @app.get("/admin/reload/status")
    async def admin_reload_status() -> dict[str, object]:
        subsys = _get_or_create_subsystems(app)
        history = subsys["bus"].get_history()
        recent = [{"type": e.type, "payload": e.payload, "timestamp": e.timestamp} for e in history[-20:]]
        return {"recent_events": recent, "total_events": len(history)}

    @app.post("/admin/templates/refresh")
    async def admin_templates_refresh() -> dict[str, object]:
        subsys = _get_or_create_subsystems(app)
        if not hasattr(app.state, "_prompt_registry") or app.state._prompt_registry is None:
            app.state._prompt_registry = PromptRegistry(
                template_dir=app.state._templates_dir,
                event_bus=subsys["bus"],
            )
        result = app.state._prompt_registry.refresh()
        return {"success": True, "templates": result.get("templates", [])}

    @app.get("/admin/templates")
    async def admin_list_templates() -> dict[str, object]:
        if hasattr(app.state, "_prompt_registry") and app.state._prompt_registry is not None:
            return {"templates": app.state._prompt_registry.list_templates()}
        return {"templates": []}

    @app.post("/admin/playbooks/refresh")
    async def admin_playbooks_refresh() -> dict[str, object]:
        subsys = _get_or_create_subsystems(app)
        if not hasattr(app.state, "_runner") or app.state._runner is None:
            app.state._runner = AnsibleRunnerAdapter(
                playbooks_dir=app.state._playbooks_dir,
                event_bus=subsys["bus"],
            )
        result = app.state._runner.refresh_playbooks()
        if hasattr(app.state, "event_loop") and hasattr(app.state.event_loop, "_runner"):
            loop_runner = app.state.event_loop._runner
            if loop_runner is not None and loop_runner is not app.state._runner:
                loop_runner.refresh_playbooks()
        return {"success": True, "playbooks": result.get("playbooks", [])}

    @app.get("/admin/playbooks")
    async def admin_list_playbooks() -> dict[str, object]:
        if hasattr(app.state, "_runner") and app.state._runner is not None:
            return {"playbooks": app.state._runner.list_playbooks()}
        return {"playbooks": []}

    @app.get("/admin/hooks")
    async def admin_list_hooks() -> dict[str, object]:
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
    async def admin_register_hook(req: RegisterHookRequest) -> dict[str, object]:
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
    async def admin_delete_hook(hook_id: str) -> dict[str, object]:
        subsys = _get_or_create_subsystems(app)
        subsys["hooks"].unregister(hook_id)
        return {"removed": hook_id}

    @app.post("/admin/workers")
    async def admin_register_worker(req: RegisterWorkerRequest) -> dict[str, object]:
        subsys = _get_or_create_subsystems(app)
        from general_ludd.reload.worker_broadcast import WorkerInfo

        subsys["broadcaster"].register(WorkerInfo(worker_id=req.worker_id, address=req.address))
        registered = subsys["broadcaster"].list_workers()
        was_registered = any(w.worker_id == req.worker_id for w in registered)
        return {
            "success": was_registered,
            "worker_id": req.worker_id,
            "address": req.address if was_registered else "",
        }

    @app.post("/admin/workers/ping")
    async def admin_workers_ping() -> dict[str, object]:
        subsys = _get_or_create_subsystems(app)
        # ping_all does serial blocking httpx.get per worker — offload it.
        results = await asyncio.to_thread(subsys["broadcaster"].ping_all)
        return {"workers": results}

    @app.get("/admin/workers")
    async def admin_list_workers() -> dict[str, object]:
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


def _register_agent_routes(app: FastAPI) -> None:

    @app.get("/admin/agents")
    async def admin_list_agents() -> dict[str, object]:
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
    async def admin_get_agent(agent_id: str) -> dict[str, object]:
        ext = _get_or_create_extended_subsystems(app)
        summary = ext["metrics"].get_agent_summary(agent_id)
        if not summary:
            raise HTTPException(status_code=404, detail="Agent not found")
        return cast(dict[str, object], summary)

    @app.get("/admin/metrics/cost")
    async def admin_metrics_cost(
        subscription_name: str = "",
        subscription_cost_per_month: float = 0.0,
        tokens_per_week: int = 0,
    ) -> dict[str, object]:
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
    async def admin_metrics_report() -> dict[str, object]:
        ext = _get_or_create_extended_subsystems(app)
        return cast(dict[str, object], ext["metrics"].get_full_report())


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    """Register reload administration and agent-facing routes."""
    _register_admin_routes(app)
    _register_agent_routes(app)
