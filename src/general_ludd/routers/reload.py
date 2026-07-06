from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import cast

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from general_ludd.ansible.runner import AnsibleRunnerAdapter
from general_ludd.daemon import _get_or_create_extended_subsystems, _get_or_create_subsystems
from general_ludd.events.types import ConfigReloadedEvent
from general_ludd.prompts.registry import PromptRegistry
from general_ludd.reload.hot_reloader import HotReloader, ReloadScope
from general_ludd.security import is_safe_fetch_url

logger = logging.getLogger(__name__)

_FORBIDDEN_HEADERS: frozenset[str] = frozenset(
    {"authorization", "host", "content-length", "transfer-encoding", "cookie"}
)
_MAX_HEADER_KEY_LEN = 1024
_MAX_HEADER_VAL_LEN = 1024


class ReloadRequest(BaseModel):
    scope: str = "all"


class RegisterHookRequest(BaseModel):
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
                "url must use https and must not target loopback, link-local, "
                "RFC-1918, or cloud-metadata addresses"
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
                raise ValueError(
                    f"header key exceeds maximum length of {_MAX_HEADER_KEY_LEN}: {key!r}"
                )
            if len(val) > _MAX_HEADER_VAL_LEN:
                raise ValueError(
                    f"header value for {key!r} exceeds maximum length of {_MAX_HEADER_VAL_LEN}"
                )
            if key.lower() in _FORBIDDEN_HEADERS:
                raise ValueError(
                    f"header {key!r} is not permitted in webhook registrations"
                )
        return v


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:

    @app.post("/admin/reload")
    async def admin_reload(req: ReloadRequest) -> dict[str, object]:
        subsys = _get_or_create_subsystems(app)
        _skills_dirs: list[str] = []
        _config_dir_val = getattr(app.state, "_config_dir", None)
        if _config_dir_val:
            _global_skills = Path(_config_dir_val) / "skills"
            if _global_skills.is_dir():
                _skills_dirs.append(str(_global_skills))
        _proj_dir = getattr(app.state, "_project_gludd_dir", None)
        if _proj_dir is not None:
            _proj_skills = Path(_proj_dir) / "skills"
            if _proj_skills.is_dir():
                _skills_dirs.append(str(_proj_skills))
        reloader = HotReloader(
            config_dir=app.state._config_dir or "/tmp/gl-config",
            event_bus=subsys["bus"],
            hook_system=subsys["hooks"],
            worker_broadcaster=subsys["broadcaster"],
            templates_dir=app.state._templates_dir,
            playbooks_dir=app.state._playbooks_dir,
            skills_dirs=_skills_dirs if _skills_dirs else None,
            skill_registry=getattr(app.state, "_skill_registry", None),
            prompt_registry=getattr(app.state, "_prompt_registry", None),
        )
        scope = ReloadScope(req.scope)
        # reloader.reload is a sync op that (deep inside) does serial blocking
        # httpx.post per worker via the broadcaster — offload it so the whole
        # reload doesn't freeze the event loop.
        result = await asyncio.to_thread(reloader.reload, scope)
        return {"success": result.success, "scope": result.scope, "details": result.details, "error": result.error}

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
        recent = [
            {"type": e.type, "payload": e.payload, "timestamp": e.timestamp}
            for e in history[-20:]
        ]
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
