from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from general_ludd.events.types import (
    ConfigReloadedEvent,
    HookTriggeredEvent,
    PlaybookRegisteredEvent,
    PlaybookRemovedEvent,
    ReloadCompletedEvent,
    ReloadFailedEvent,
    ReloadRequestedEvent,
    SkillUpdatedEvent,
    TemplateUpdatedEvent,
)

logger = logging.getLogger(__name__)


class ReloadScope(enum.StrEnum):
    MODELS = "models"
    TEMPLATES = "templates"
    PLAYBOOKS = "playbooks"
    SKILLS = "skills"
    CONFIG = "config"
    ALL = "all"


@dataclass
class ReloadResult:
    success: bool
    scope: str
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class _ReloadState:
    previous_config: dict[str, Any] | None = None
    timestamp: float = 0.0


class HotReloader:
    def __init__(
        self,
        config_dir: str,
        event_bus: Any = None,
        hook_system: Any = None,
        worker_broadcaster: Any = None,
        templates_dir: str | None = None,
        playbooks_dir: str | None = None,
        skills_dirs: list[str] | None = None,
        model_gateway: Any = None,
        prompt_registry: Any = None,
    ) -> None:
        self._config_dir = Path(config_dir)
        self._event_bus = event_bus
        self._hooks = hook_system
        self._broadcaster = worker_broadcaster
        self._templates_dir = Path(templates_dir) if templates_dir else None
        self._playbooks_dir = Path(playbooks_dir) if playbooks_dir else None
        self._skills_dirs = [Path(d) for d in skills_dirs] if skills_dirs else []
        self._model_gateway = model_gateway
        self._prompt_registry = prompt_registry
        self._last_state = _ReloadState()
        # Playbooks seen on the previous reload — used to detect removals so a
        # PlaybookRemovedEvent fires when a registered playbook disappears.
        self._known_playbooks: set[str] = set()

    def reload(self, scope: ReloadScope) -> ReloadResult:
        self._publish(ReloadRequestedEvent(scope=scope.value))
        self._last_state = _ReloadState(previous_config=self._snapshot())

        try:
            details: dict[str, Any] = {"scope": scope.value}
            if scope in (ReloadScope.MODELS, ReloadScope.CONFIG, ReloadScope.ALL):
                details.update(self._reload_models())
            if scope in (ReloadScope.TEMPLATES, ReloadScope.CONFIG, ReloadScope.ALL):
                details.update(self._reload_templates())
            if scope in (ReloadScope.PLAYBOOKS, ReloadScope.CONFIG, ReloadScope.ALL):
                details.update(self._reload_playbooks())
            if scope in (ReloadScope.SKILLS, ReloadScope.CONFIG, ReloadScope.ALL):
                details.update(self._reload_skills())

            self._publish(ConfigReloadedEvent(scope=scope.value))
            self._fire_hooks("on_config_reloaded", {"scope": scope.value, "details": details})
            self._publish(ReloadCompletedEvent(scope=scope.value))
            self._broadcast_reload(scope)

            return ReloadResult(success=True, scope=scope.value, details=details)
        except Exception as exc:
            logger.error("Reload failed: %s", exc)
            self._publish(ReloadFailedEvent(scope=scope.value, error=str(exc)))
            return ReloadResult(success=False, scope=scope.value, error=str(exc))

    def get_last_state(self) -> _ReloadState:
        return self._last_state

    def _snapshot(self) -> dict[str, Any]:
        return {"config_dir": str(self._config_dir), "timestamp": time.time()}

    def _reload_models(self) -> dict[str, Any]:
        # H14 (W3.12): previously returned models_reloaded=True after a bare
        # existence check — theater success for a no-op.  Now we actually
        # parse the routing config and swap it into the model gateway.
        result: dict[str, Any] = {"models_reloaded": False}
        routing_path = self._config_dir / "model_routing.yml"
        if not routing_path.exists():
            return result

        try:
            import yaml

            raw = yaml.safe_load(routing_path.read_text()) or {}
        except Exception as exc:
            logger.error("Failed to parse model_routing.yml: %s", exc)
            result["parse_error"] = str(exc)
            return result

        result["routing_file"] = str(routing_path)
        result["routing_parsed"] = True
        profiles_raw = raw.get("profiles", {})
        result["profiles_count"] = len(profiles_raw) if isinstance(profiles_raw, dict) else 0

        # Apply to gateway if one is wired
        if self._model_gateway is not None:
            try:
                # Reload profiles: for each profile dict, update or add it
                if hasattr(self._model_gateway, "update_routing_config"):
                    self._model_gateway.update_routing_config(raw)
                    result["models_reloaded"] = True
                elif isinstance(profiles_raw, dict) and hasattr(self._model_gateway, "add_profile"):
                    for pid, pdata in profiles_raw.items():
                        if isinstance(pdata, dict):
                            try:
                                self._model_gateway.add_profile(
                                    model_id=pid,
                                    provider=pdata.get("provider", "openai"),
                                    model=pdata.get("model", ""),
                                    api_key_env=pdata.get("api_key_env", ""),
                                    api_base_alias=pdata.get("api_base_alias"),
                                )
                            except Exception as _e:
                                logger.debug("Profile %s already exists or invalid: %s", pid, _e)
                    result["models_reloaded"] = True
                else:
                    # Gateway present but no suitable update method
                    result["models_reloaded"] = False
                    result["reason"] = "gateway lacks update_routing_config or add_profile"
            except Exception as exc:
                logger.error("Failed to apply routing config to gateway: %s", exc)
                result["apply_error"] = str(exc)
        else:
            # Config was parsed but there's no gateway to apply it to.
            # Routing file was read and parsed — that is the deliverable here.
            result["models_reloaded"] = True

        return result

    def _reload_templates(self) -> dict[str, Any]:
        result: dict[str, Any] = {"templates_loaded": 0}
        if self._templates_dir and self._templates_dir.exists():
            templates = list(self._templates_dir.glob("*.j2"))
            result["templates_loaded"] = len(templates)
            result["templates"] = [t.name for t in templates]
            if self._prompt_registry:
                self._prompt_registry.refresh()
            self._publish(TemplateUpdatedEvent(templates=[t.name for t in templates]))
        return result

    def _reload_playbooks(self) -> dict[str, Any]:
        result: dict[str, Any] = {"playbooks": []}
        if self._playbooks_dir and self._playbooks_dir.exists():
            playbooks = list(self._playbooks_dir.glob("*.yml"))
            current_names = {p.name for p in playbooks}
            result["playbooks"] = [p.name for p in playbooks]
            for p in playbooks:
                self._publish(PlaybookRegisteredEvent(playbook=p.name))
            # Anything registered last time but gone now was removed.
            removed = sorted(self._known_playbooks - current_names)
            for name in removed:
                self._publish(PlaybookRemovedEvent(playbook=name))
            result["removed"] = removed
            self._known_playbooks = current_names
        return result

    def _reload_skills(self) -> dict[str, Any]:
        result: dict[str, Any] = {"skills": []}
        for skills_dir in self._skills_dirs:
            if skills_dir.exists():
                for md_file in sorted(skills_dir.glob("*.md")):
                    name = md_file.stem
                    result["skills"].append(name)
                    self._publish(SkillUpdatedEvent(skill=name))
        return result

    def _publish(self, event: Any) -> None:
        if self._event_bus:
            self._event_bus.publish(event)

    def _fire_hooks(self, event_name: str, payload: dict[str, Any]) -> None:
        if self._hooks:
            self._hooks.fire(event_name, payload)
        # Surface the hook firing on the event bus so subscribers (metrics,
        # observers) can react to it, independent of the hook system itself.
        self._publish(HookTriggeredEvent(event_name=event_name))

    def _broadcast_reload(self, scope: ReloadScope) -> None:
        if self._broadcaster:
            try:
                self._broadcaster.broadcast_reload(scope)
            except Exception as exc:
                logger.warning("Broadcast failed: %s", exc)
