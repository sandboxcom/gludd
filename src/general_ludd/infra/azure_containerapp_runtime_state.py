"""Secret-safe state normalization for Azure Container Apps readiness."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

_APP_PROVISIONING_STATES = frozenset(
    {
        "Canceled",
        "Deleting",
        "Failed",
        "InProgress",
        "Provisioning",
        "Succeeded",
        "Updating",
    }
)
_REPLICA_STATES = frozenset({"Running", "NotRunning", "Unknown"})
_CONTAINER_STATES = frozenset({"Running", "Waiting", "Terminated", "Unknown"})
_REPLICA_REASONS = frozenset(
    {
        "capacity_exhausted",
        "container_crash",
        "identity_initializing",
        "image_initializing",
        "image_pull_failure",
        "readiness_probe_failure",
        "resource_exhausted",
        "startup_probe_failure",
    }
)
_TERMINAL_REPLICA_REASONS = frozenset(
    {
        "capacity_exhausted",
        "container_crash",
        "image_pull_failure",
        "resource_exhausted",
        "startup_probe_failure",
    }
)


def _ready(document: object | None) -> bool:
    if not isinstance(document, Mapping):
        return False
    properties = document.get("properties")
    return bool(
        isinstance(properties, Mapping)
        and properties.get("provisioningState") == "Succeeded"
        and isinstance(properties.get("latestReadyRevisionName"), str)
        and properties.get("latestReadyRevisionName")
    )


def _environment_ready(document: object | None) -> bool:
    if not isinstance(document, Mapping):
        return False
    properties = document.get("properties")
    return bool(
        isinstance(properties, Mapping)
        and properties.get("provisioningState") == "Succeeded"
    )


def _fixed_state(value: object, allowed: frozenset[str]) -> str:
    return value if isinstance(value, str) and value in allowed else "Unknown"


def _app_provisioning_state(document: object | None) -> tuple[str, bool]:
    """Return only bounded app readiness facts safe for progress events."""
    properties = document.get("properties") if isinstance(document, Mapping) else None
    values = properties if isinstance(properties, Mapping) else {}
    state = _fixed_state(values.get("provisioningState"), _APP_PROVISIONING_STATES)
    ready_revision = bool(
        isinstance(values.get("latestReadyRevisionName"), str)
        and values.get("latestReadyRevisionName")
    )
    return state, ready_revision


@dataclass(frozen=True, slots=True)
class _RevisionState:
    active: bool
    replicas: int
    health_state: str
    provisioning_state: str
    running_state: str

    def ready(self, minimum_replicas: int) -> bool:
        return bool(
            self.active
            and self.replicas >= minimum_replicas
            and self.health_state == "Healthy"
            and self.provisioning_state == "Provisioned"
            and self.running_state in {"Running", "Unknown"}
        )

    @property
    def terminal(self) -> bool:
        return bool(
            self.health_state == "Unhealthy"
            or self.provisioning_state in {"Failed", "Deprovisioned"}
            or self.running_state in {"Stopped", "Degraded", "Failed"}
        )


def _revision_state(document: object | None) -> _RevisionState:
    properties = document.get("properties") if isinstance(document, Mapping) else None
    values = properties if isinstance(properties, Mapping) else {}
    replicas = values.get("replicas")
    bounded_replicas = (
        replicas
        if isinstance(replicas, int)
        and not isinstance(replicas, bool)
        and 0 <= replicas <= 100_000
        else 0
    )
    return _RevisionState(
        active=values.get("active") is True,
        replicas=bounded_replicas,
        health_state=_fixed_state(
            values.get("healthState"),
            frozenset({"Healthy", "Unhealthy", "None", "Unknown"}),
        ),
        provisioning_state=_fixed_state(
            values.get("provisioningState"),
            frozenset(
                {
                    "Provisioning",
                    "Provisioned",
                    "Failed",
                    "Deprovisioning",
                    "Deprovisioned",
                    "Unknown",
                }
            ),
        ),
        running_state=_fixed_state(
            values.get("runningState"),
            frozenset(
                {"Running", "Processing", "Stopped", "Degraded", "Failed", "Unknown"}
            ),
        ),
    )


def _ready_revision_name(document: object | None) -> str | None:
    properties = document.get("properties") if isinstance(document, Mapping) else None
    value = (
        properties.get("latestReadyRevisionName")
        if isinstance(properties, Mapping)
        else None
    )
    return value if isinstance(value, str) and value else None


def _observed_revision_name(document: object | None, app_name: str) -> str | None:
    value = document.get("name") if isinstance(document, Mapping) else None
    prefix = f"{app_name}--"
    if not isinstance(value, str) or not value.startswith(prefix) or len(value) > 64:
        return None
    suffix = value[len(prefix) :]
    if not suffix or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in suffix
    ):
        return None
    return value


def _bounded_status_count(value: object) -> int:
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= 1_000_000
    ):
        return value
    return 0


def _safe_status_values(value: object, allowed: frozenset[str]) -> tuple[str, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) > 32
    ):
        return ()
    return tuple(
        sorted({item for item in value if isinstance(item, str) and item in allowed})
    )


def _event_value(values: tuple[str, ...]) -> str:
    if not values:
        return "None"
    if len(values) == 1:
        return values[0]
    return "Mixed"


@dataclass(frozen=True, slots=True)
class _ReplicaStatus:
    replicas: int
    ready_containers: int
    started_containers: int
    restarts: int
    replica_states: tuple[str, ...]
    container_states: tuple[str, ...]
    reasons: tuple[str, ...]

    @property
    def terminal(self) -> bool:
        return bool(_TERMINAL_REPLICA_REASONS.intersection(self.reasons))

    def ready(self, minimum_replicas: int) -> bool:
        return bool(
            self.replicas >= minimum_replicas
            and self.ready_containers >= minimum_replicas
            and self.started_containers >= minimum_replicas
            and self.replica_states == ("Running",)
            and self.container_states == ("Running",)
            and not self.terminal
        )


def _replica_status(document: object) -> _ReplicaStatus:
    values = document if isinstance(document, Mapping) else {}
    return _ReplicaStatus(
        replicas=_bounded_status_count(values.get("replicaCount")),
        ready_containers=_bounded_status_count(values.get("readyContainerCount")),
        started_containers=_bounded_status_count(values.get("startedContainerCount")),
        restarts=_bounded_status_count(values.get("restartCount")),
        replica_states=_safe_status_values(
            values.get("replicaRunningStates"), _REPLICA_STATES
        ),
        container_states=_safe_status_values(
            values.get("containerRunningStates"), _CONTAINER_STATES
        ),
        reasons=_safe_status_values(values.get("reasonClasses"), _REPLICA_REASONS),
    )


def _replica_progress(status: _ReplicaStatus, minimum_replicas: int) -> str:
    reason = (
        status.reasons[0]
        if len(status.reasons) == 1
        else "multiple"
        if status.reasons
        else "none"
    )
    state = (
        "terminal"
        if status.terminal
        else "ready"
        if status.ready(minimum_replicas)
        else "heartbeat"
    )
    return (
        f"azure_containerapp_replica_poll phase=readiness state={state} "
        f"replicas={status.replicas} ready_containers={status.ready_containers} "
        f"started_containers={status.started_containers} restarts={status.restarts} "
        f"replica_state={_event_value(status.replica_states)} "
        f"container_state={_event_value(status.container_states)} reason={reason}"
    )


def _with_ready_revision(document: object | None, revision_name: str) -> object | None:
    if not isinstance(document, Mapping):
        return document
    properties = document.get("properties")
    if not isinstance(properties, Mapping):
        return document
    normalized = dict(document)
    normalized["properties"] = {
        **properties,
        "latestReadyRevisionName": revision_name,
    }
    return normalized


__all__ = ()
