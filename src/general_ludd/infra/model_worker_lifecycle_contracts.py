"""Validated contracts for provider-neutral model-worker ownership."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from general_ludd.hardware.model_runner_launch_contracts import RunnerLaunchPlan

_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_VERSION_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_TEXT = 2_048
_MAX_WORKERS = 10_000


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_TEXT:
        raise ValueError(f"{field_name} must be bounded non-empty text")
    if any(delimiter in value for delimiter in "\x00\r\n"):
        raise ValueError(f"{field_name} contains a control delimiter")
    return value


def _digest(value: object, field_name: str) -> str:
    text = _text(value, field_name)
    if not _HEX_DIGEST.fullmatch(text):
        raise ValueError(f"{field_name} must be a lowercase sha256 digest")
    return text


@dataclass(frozen=True, slots=True)
class ModelWorkerHost:
    """One exact remote host created by a provider infrastructure adapter."""

    host_id: str
    address: str
    ansible_user: str
    ssh_private_key_path: str
    endpoint_url: str

    def __post_init__(self) -> None:
        """Reject missing, unbounded, or control-bearing connection metadata."""
        for field_name in (
            "host_id",
            "address",
            "ansible_user",
            "ssh_private_key_path",
            "endpoint_url",
        ):
            _text(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True)
class ProvisionedModelWorkerPool:
    """Infrastructure-owned hosts and their exact cleanup boundary."""

    deployment_id: str
    hosts: tuple[ModelWorkerHost, ...]
    owned_resource_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        """Require a bounded, unique, non-empty infrastructure result."""
        _text(self.deployment_id, "deployment_id")
        if (
            not isinstance(self.hosts, tuple)
            or not self.hosts
            or len(self.hosts) > _MAX_WORKERS
            or any(not isinstance(host, ModelWorkerHost) for host in self.hosts)
        ):
            raise ValueError("hosts must be a bounded non-empty ModelWorkerHost tuple")
        host_ids = tuple(host.host_id for host in self.hosts)
        if len(set(host_ids)) != len(host_ids):
            raise ValueError("host identifiers must be unique")
        if (
            not isinstance(self.owned_resource_ids, tuple)
            or not self.owned_resource_ids
            or len(self.owned_resource_ids) > _MAX_WORKERS * 16
        ):
            raise ValueError("owned_resource_ids must be a bounded non-empty tuple")
        for resource_id in self.owned_resource_ids:
            _text(resource_id, "owned_resource_id")
        if len(set(self.owned_resource_ids)) != len(self.owned_resource_ids):
            raise ValueError("owned resource identifiers must be unique")


@dataclass(frozen=True, slots=True)
class ModelWorkerEndpoint:
    """One health-attested model endpoint eligible for dispatch."""

    host_id: str
    endpoint_url: str
    service_name: str
    attestation_digest: str

    def __post_init__(self) -> None:
        """Require a bounded endpoint bound to one content-free attestation."""
        for field_name in ("host_id", "endpoint_url", "service_name"):
            _text(getattr(self, field_name), field_name)
        _digest(self.attestation_digest, "attestation_digest")


@dataclass(frozen=True, slots=True)
class ModelWorkerLifecyclePolicy:
    """Immutable runner and attestation desired state for one candidate pool."""

    launch_plan: RunnerLaunchPlan
    release_id: str
    topology_digest: str
    backend: str
    minimum_memory_mib: int
    required_interconnect: str
    runtime_probe: tuple[str, ...]
    expected_runtime_version_digest: str
    configure_timeout_seconds: int

    def __post_init__(self) -> None:
        """Validate all controller-selected attestation and timeout inputs."""
        if not isinstance(self.launch_plan, RunnerLaunchPlan):
            raise ValueError("launch_plan must be RunnerLaunchPlan")
        _digest(self.release_id, "release_id")
        _digest(self.topology_digest, "topology_digest")
        _text(self.backend, "backend")
        _text(self.required_interconnect, "required_interconnect")
        if (
            isinstance(self.minimum_memory_mib, bool)
            or not isinstance(self.minimum_memory_mib, int)
            or self.minimum_memory_mib <= 0
        ):
            raise ValueError("minimum_memory_mib must be a positive integer")
        if (
            not isinstance(self.runtime_probe, tuple)
            or not self.runtime_probe
            or len(self.runtime_probe) > 64
        ):
            raise ValueError("runtime_probe must be a bounded non-empty tuple")
        for argument in self.runtime_probe:
            _text(argument, "runtime_probe")
        if not _VERSION_DIGEST.fullmatch(
            _text(
                self.expected_runtime_version_digest,
                "expected_runtime_version_digest",
            )
        ):
            raise ValueError(
                "expected_runtime_version_digest must be a sha256 version digest"
            )
        if (
            isinstance(self.configure_timeout_seconds, bool)
            or not isinstance(self.configure_timeout_seconds, int)
            or not 1 <= self.configure_timeout_seconds <= 86_400
        ):
            raise ValueError("configure_timeout_seconds is outside 1..86400")

    @property
    def operation_digest(self) -> str:
        """Bind desired state for content-free lifecycle correlation."""
        encoded = json.dumps(
            {
                "backend": self.backend,
                "configure_timeout_seconds": self.configure_timeout_seconds,
                "expected_runtime_version_digest": (
                    self.expected_runtime_version_digest
                ),
                "launch_plan": self.launch_plan.to_dict(),
                "minimum_memory_mib": self.minimum_memory_mib,
                "release_id": self.release_id,
                "required_interconnect": self.required_interconnect,
                "runtime_probe": self.runtime_probe,
                "topology_digest": self.topology_digest,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()


class ModelWorkerLifecycleEvent(StrEnum):
    """Content-free lifecycle progress suitable for durable telemetry."""

    PROVISION_STARTED = "provision_started"
    PROVISIONED = "provisioned"
    CONFIGURE_STARTED = "configure_started"
    READY = "ready"
    PUBLISH_STARTED = "publish_started"
    PUBLISHED = "published"
    DRAIN_STARTED = "drain_started"
    DRAINED = "drained"
    RETIRE_STARTED = "retire_started"
    RETIRED = "retired"
    DESTROY_STARTED = "destroy_started"
    DESTROYED = "destroyed"
    ABSENCE_VERIFIED = "absence_verified"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ModelWorkerLifecycleTrace:
    """One censored lifecycle event with no endpoint or provider identity."""

    event: ModelWorkerLifecycleEvent
    operation_digest: str
    worker_count: int
    reason_code: str | None = None

    def __post_init__(self) -> None:
        """Validate the bounded telemetry envelope."""
        if not isinstance(self.event, ModelWorkerLifecycleEvent):
            raise ValueError("event must be ModelWorkerLifecycleEvent")
        _digest(self.operation_digest, "operation_digest")
        if (
            isinstance(self.worker_count, bool)
            or not isinstance(self.worker_count, int)
            or not 0 <= self.worker_count <= _MAX_WORKERS
        ):
            raise ValueError("worker_count is outside its bounded range")
        if self.reason_code is not None:
            _text(self.reason_code, "reason_code")


class ModelWorkerLifecycleError(RuntimeError):
    """Censored owned-lifecycle failure with cleanup disposition."""

    def __init__(self, phase: str, *, cleanup_failed: bool = False) -> None:
        """Retain only a stable phase and whether ownership cleanup failed."""
        self.phase = _text(phase, "phase")
        self.cleanup_failed = bool(cleanup_failed)
        suffix = "; cleanup failed" if self.cleanup_failed else ""
        super().__init__(f"model worker lifecycle failed during {self.phase}{suffix}")


class ModelWorkerInfrastructureRuntime(Protocol):
    """Provider adapter owning infrastructure creation and exact teardown."""

    def provision(
        self,
        policy: ModelWorkerLifecyclePolicy,
    ) -> ProvisionedModelWorkerPool:
        """Create and return one exactly owned worker pool."""
        ...

    def destroy(self, deployment: ProvisionedModelWorkerPool) -> None:
        """Destroy the exact owned resources for a provisioned pool."""
        ...

    def exists(self, deployment: ProvisionedModelWorkerPool) -> bool:
        """Return whether any exact owned resource remains."""
        ...


class ModelWorkerConfigurationRuntime(Protocol):
    """Guest adapter owning attestation, launch, health, and retirement."""

    def configure(
        self,
        deployment: ProvisionedModelWorkerPool,
        policy: ModelWorkerLifecyclePolicy,
    ) -> tuple[ModelWorkerEndpoint, ...]:
        """Attest and configure endpoints for the provisioned hosts."""
        ...

    def retire(
        self,
        deployment: ProvisionedModelWorkerPool,
        endpoints: tuple[ModelWorkerEndpoint, ...],
    ) -> None:
        """Retire the exact configured endpoint generation."""
        ...


class ModelWorkerDispatchRuntime(Protocol):
    """Router adapter publishing and draining only ready worker endpoints."""

    def publish(
        self,
        deployment: ProvisionedModelWorkerPool,
        endpoints: tuple[ModelWorkerEndpoint, ...],
    ) -> str:
        """Publish ready endpoints and return their dispatch lease."""
        ...

    def withdraw(self, dispatch_lease: str) -> None:
        """Drain and withdraw one exact dispatch lease."""
        ...


__all__ = (
    "ModelWorkerConfigurationRuntime",
    "ModelWorkerDispatchRuntime",
    "ModelWorkerEndpoint",
    "ModelWorkerHost",
    "ModelWorkerInfrastructureRuntime",
    "ModelWorkerLifecycleError",
    "ModelWorkerLifecycleEvent",
    "ModelWorkerLifecyclePolicy",
    "ModelWorkerLifecycleTrace",
    "ProvisionedModelWorkerPool",
)
