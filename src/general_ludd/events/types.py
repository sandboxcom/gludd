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
    STALL_DETECTED = "stall_detected"
    SLOW_OPERATION = "slow_operation"
    MODEL_DEPLOY_STARTED = "model_deploy_started"
    MODEL_READY = "model_ready"
    MODEL_ERROR = "model_error"
    CUSTOM = "custom"
    SELF_UPDATE_APPLIED = "self_update_applied"
    BRANCH_EXECUTED = "branch_executed"


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
class StallDetectedEvent(Event):
    """An in-flight operation has run past its deadline (see StallWatchdog).

    ``thread_stacks`` is a snapshot of every thread's stack at detection time —
    the 'investigate why it hung' evidence a subscriber can log or attach.
    """

    def __init__(
        self,
        operation: str,
        elapsed_s: float,
        deadline_s: float,
        thread_stacks: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            type=EventType.STALL_DETECTED,
            payload={
                "operation": operation,
                "elapsed_s": elapsed_s,
                "deadline_s": deadline_s,
                "thread_stacks": thread_stacks,
            },
            **kwargs,
        )


@dataclass
class ModelDeployStartedEvent(Event):
    def __init__(
        self,
        server_id: str,
        engine: str,
        model_path: str,
        host: str,
        port: int,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            type=EventType.MODEL_DEPLOY_STARTED,
            payload={
                "server_id": server_id,
                "engine": engine,
                "model_path": model_path,
                "host": host,
                "port": port,
            },
            **kwargs,
        )


@dataclass
class ModelReadyEvent(Event):
    def __init__(
        self,
        server_id: str,
        engine: str,
        endpoint_url: str,
        pid: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            type=EventType.MODEL_READY,
            payload={
                "server_id": server_id,
                "engine": engine,
                "endpoint_url": endpoint_url,
                "pid": pid,
            },
            **kwargs,
        )


@dataclass
class ModelErrorEvent(Event):
    def __init__(
        self,
        server_id: str,
        engine: str,
        error: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            type=EventType.MODEL_ERROR,
            payload={
                "server_id": server_id,
                "engine": engine,
                "error": error,
            },
            **kwargs,
        )


@dataclass
class SlowOperationEvent(Event):
    """An operation completed but ran anomalously slow vs its learned baseline."""

    def __init__(
        self,
        operation: str,
        duration_s: float,
        baseline_s: float,
        factor: float,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            type=EventType.SLOW_OPERATION,
            payload={
                "operation": operation,
                "duration_s": duration_s,
                "baseline_s": baseline_s,
                "factor": factor,
            },
            **kwargs,
        )


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

    @property
    def name(self) -> str:
        """Return the custom event name from its serialized payload."""
        return str(self.payload.get("name", ""))


@dataclass
class SelfUpdateAppliedEvent(Event):
    def __init__(self, commit_sha: str, reloaded_modules: list[str], **kwargs: Any) -> None:
        super().__init__(
            type=EventType.SELF_UPDATE_APPLIED,
            payload={"commit_sha": commit_sha, "reloaded_modules": reloaded_modules},
            **kwargs,
        )


@dataclass
class BranchEvent(Event):
    """Emitted when gludd takes a code branch decision."""

    module: str = field(default="")
    function: str = field(default="")
    branch_id: str = field(default="")
    decision: str = field(default="")
    context: dict[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        module: str,
        function: str,
        branch_id: str,
        decision: str,
        context: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            type=EventType.BRANCH_EXECUTED,
            payload={
                "module": module,
                "function": function,
                "branch_id": branch_id,
                "decision": decision,
                "context": context or {},
            },
            **kwargs,
        )
