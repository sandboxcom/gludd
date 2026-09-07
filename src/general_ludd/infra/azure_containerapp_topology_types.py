"""Immutable contracts for a bounded Azure Container Apps runner fleet."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from general_ludd.infra.azure_containerapp_gpu import (
    A100_PROFILE,
    T4_PROFILE,
    ModelServingRequirement,
)

_TASK_ID_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?")
_SUPPORTED_RUNTIMES = frozenset({"vllm-openai"})
_PROFILE_NAMES = {
    T4_PROFILE.workload_profile_type: "gpu-t4",
    A100_PROFILE.workload_profile_type: "gpu-a100",
}
_MAX_APPS = 64
_MAX_REPLICAS = 1_024
_MAX_REPLICAS_PER_APP = 100
_MAX_CONCURRENCY = 100_000
_MAX_TTL_MINUTES = 24 * 60
_MAX_COST_MICROUSD = 1_000_000_000_000


class AzureRunnerTopologyError(ValueError):
    """Refuse an ambiguous, oversized, or unaffordable fleet plan."""


def _bounded_positive(name: str, value: int, maximum: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 < value <= maximum
    ):
        raise ValueError(f"{name} must be a positive bounded integer")


@dataclass(frozen=True, slots=True)
class AzureProfileCapacity:
    """One permitted serverless-GPU profile and its hard budget."""

    workload_profile_type: str
    max_replicas: int
    hourly_cost_microusd_per_replica: int

    def __post_init__(self) -> None:
        """Require a documented profile and finite positive bounds."""
        if self.workload_profile_type not in _PROFILE_NAMES:
            raise ValueError("workload_profile_type must be a supported GPU profile")
        _bounded_positive("max_replicas", self.max_replicas, _MAX_REPLICAS)
        _bounded_positive(
            "hourly_cost_microusd_per_replica",
            self.hourly_cost_microusd_per_replica,
            _MAX_COST_MICROUSD,
        )


@dataclass(frozen=True, slots=True)
class AzureFleetConstraints:
    """User-supplied ceilings applied before infrastructure can be planned."""

    profile_capacities: tuple[AzureProfileCapacity, ...]
    max_apps: int
    max_total_replicas: int
    max_replicas_per_app: int
    max_hourly_cost_microusd: int
    ttl_minutes: int

    def __post_init__(self) -> None:
        """Reject mutable, duplicate, or effectively unbounded constraints."""
        if not isinstance(self.profile_capacities, tuple) or not all(
            isinstance(item, AzureProfileCapacity) for item in self.profile_capacities
        ):
            raise ValueError("profile_capacities must be an immutable profile tuple")
        profile_types = [item.workload_profile_type for item in self.profile_capacities]
        if len(profile_types) != len(set(profile_types)):
            raise ValueError("duplicate profile capacity is not allowed")
        for name, value, maximum in (
            ("max_apps", self.max_apps, _MAX_APPS),
            ("max_total_replicas", self.max_total_replicas, _MAX_REPLICAS),
            (
                "max_replicas_per_app",
                self.max_replicas_per_app,
                _MAX_REPLICAS_PER_APP,
            ),
            (
                "max_hourly_cost_microusd",
                self.max_hourly_cost_microusd,
                _MAX_COST_MICROUSD,
            ),
            ("ttl_minutes", self.ttl_minutes, _MAX_TTL_MINUTES),
        ):
            _bounded_positive(name, value, maximum)


@dataclass(frozen=True, slots=True)
class AzureRunnerDemand:
    """A task's immutable model demand and dependency edges."""

    task_id: str
    requirement: ModelServingRequirement
    peak_concurrency: int
    per_replica_concurrency: int
    depends_on: frozenset[str] = frozenset()
    runtime: str = "vllm-openai"

    def __post_init__(self) -> None:
        """Validate all topology inputs before grouping or scheduling."""
        if not isinstance(self.task_id, str) or _TASK_ID_PATTERN.fullmatch(
            self.task_id
        ) is None:
            raise ValueError("task_id must be a bounded lowercase identifier")
        if not isinstance(self.requirement, ModelServingRequirement):
            raise ValueError("requirement must be a ModelServingRequirement")
        _bounded_positive("peak_concurrency", self.peak_concurrency, _MAX_CONCURRENCY)
        _bounded_positive(
            "per_replica_concurrency",
            self.per_replica_concurrency,
            _MAX_CONCURRENCY,
        )
        if not isinstance(self.depends_on, frozenset) or not all(
            isinstance(item, str) and _TASK_ID_PATTERN.fullmatch(item) is not None
            for item in self.depends_on
        ):
            raise ValueError("depends_on must contain bounded task identifiers")
        if self.task_id in self.depends_on:
            raise ValueError("depends_on cannot contain task_id")
        if self.runtime not in _SUPPORTED_RUNTIMES:
            raise ValueError("runtime must be an approved immutable runner")


@dataclass(frozen=True, slots=True)
class AzureRunnerAppPlan:
    """One immutable model runner shared by all compatible tasks."""

    runner_id: str
    task_ids: tuple[str, ...]
    model_id: str
    model_revision: str
    runtime: str
    profile_name: str
    workload_profile_type: str
    per_replica_concurrency: int
    min_replicas: int
    max_replicas: int


@dataclass(frozen=True, slots=True)
class AzureEnvironmentProfilePlan:
    """Aggregate environment capacity needed for one workload profile."""

    profile_name: str
    workload_profile_type: str
    max_replicas: int


@dataclass(frozen=True, slots=True)
class GatewayRoute:
    """Map one task role to a runner through the Gludd policy gateway."""

    task_id: str
    runner_id: str
    via: str = "gludd-gateway"


@dataclass(frozen=True, slots=True)
class AzureRunnerTopologyPlan:
    """Auditable desired state for an ephemeral Azure runner fleet."""

    apps: tuple[AzureRunnerAppPlan, ...]
    profiles: tuple[AzureEnvironmentProfilePlan, ...]
    routes: tuple[GatewayRoute, ...]
    batches: tuple[tuple[str, ...], ...]
    max_hourly_cost_microusd: int
    ttl_minutes: int
    plan_digest: str


@dataclass(frozen=True, slots=True)
class TopologyTrace:
    """Content-free planning progress safe for telemetry sinks."""

    phase: str
    task_count: int
    batch_count: int = 0
    app_count: int = 0
    total_max_replicas: int = 0


TraceSink = Callable[[TopologyTrace], None]


__all__ = [
    "AzureEnvironmentProfilePlan",
    "AzureFleetConstraints",
    "AzureProfileCapacity",
    "AzureRunnerAppPlan",
    "AzureRunnerDemand",
    "AzureRunnerTopologyError",
    "AzureRunnerTopologyPlan",
    "GatewayRoute",
    "TopologyTrace",
    "TraceSink",
]
