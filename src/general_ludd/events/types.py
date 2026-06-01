from __future__ import annotations

import enum as _enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


class EventType(_enum.StrEnum):
    MODEL_ADDED = "model_added"
    MODEL_REMOVED = "model_removed"
    CONFIG_RELOADED = "config_reloaded"
    TEMPLATE_UPDATED = "template_updated"
    PLAYBOOK_REGISTERED = "playbook_registered"
    PLAYBOOK_REMOVED = "playbook_removed"
    SKILL_UPDATED = "skill_updated"
    RELOAD_REQUESTED = "reload_requested"
    RELOAD_COMPLETED = "reload_completed"
    RELOAD_FAILED = "reload_failed"
    WORKER_PING = "worker_ping"
    WORKER_PONG = "worker_pong"
    HOOK_TRIGGERED = "hook_triggered"
    CUSTOM = "custom"


@dataclass
class Event:
    type: EventType | str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str | None = None
    correlation_id: str | None = None
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class ModelAddedEvent(Event):
    def __init__(self, model_id: str, profile: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(type=EventType.MODEL_ADDED, payload={"model_id": model_id, "profile": profile}, **kwargs)


@dataclass
class ModelRemovedEvent(Event):
    def __init__(self, model_id: str, **kwargs: Any) -> None:
        super().__init__(type=EventType.MODEL_REMOVED, payload={"model_id": model_id}, **kwargs)


@dataclass
class ConfigReloadedEvent(Event):
    def __init__(self, scope: str, **kwargs: Any) -> None:
        super().__init__(type=EventType.CONFIG_RELOADED, payload={"scope": scope}, **kwargs)


@dataclass
class TemplateUpdatedEvent(Event):
    def __init__(self, templates: list[str], **kwargs: Any) -> None:
        super().__init__(type=EventType.TEMPLATE_UPDATED, payload={"templates": templates}, **kwargs)


@dataclass
class PlaybookRegisteredEvent(Event):
    def __init__(self, playbook: str, **kwargs: Any) -> None:
        super().__init__(type=EventType.PLAYBOOK_REGISTERED, payload={"playbook": playbook}, **kwargs)


@dataclass
class PlaybookRemovedEvent(Event):
    def __init__(self, playbook: str, **kwargs: Any) -> None:
        super().__init__(type=EventType.PLAYBOOK_REMOVED, payload={"playbook": playbook}, **kwargs)


@dataclass
class SkillUpdatedEvent(Event):
    def __init__(self, skill: str, **kwargs: Any) -> None:
        super().__init__(type=EventType.SKILL_UPDATED, payload={"skill": skill}, **kwargs)


@dataclass
class ReloadRequestedEvent(Event):
    def __init__(self, scope: str, **kwargs: Any) -> None:
        super().__init__(type=EventType.RELOAD_REQUESTED, payload={"scope": scope}, **kwargs)


@dataclass
class ReloadCompletedEvent(Event):
    def __init__(self, scope: str, **kwargs: Any) -> None:
        super().__init__(type=EventType.RELOAD_COMPLETED, payload={"scope": scope}, **kwargs)


@dataclass
class ReloadFailedEvent(Event):
    def __init__(self, scope: str, error: str, **kwargs: Any) -> None:
        super().__init__(type=EventType.RELOAD_FAILED, payload={"scope": scope, "error": error}, **kwargs)


@dataclass
class WorkerPingEvent(Event):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(type=EventType.WORKER_PING, payload={}, **kwargs)


@dataclass
class WorkerPongEvent(Event):
    def __init__(self, worker_id: str, **kwargs: Any) -> None:
        super().__init__(type=EventType.WORKER_PONG, payload={"worker_id": worker_id}, **kwargs)


@dataclass
class HookTriggeredEvent(Event):
    def __init__(self, event_name: str, **kwargs: Any) -> None:
        super().__init__(type=EventType.HOOK_TRIGGERED, payload={"event_name": event_name}, **kwargs)


@dataclass
class CustomEvent(Event):
    def __init__(self, name: str, payload: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(type=EventType.CUSTOM, payload={"name": name, **(payload or {})}, **kwargs)
