"""Plan a bounded Azure Container Apps model-runner fleet.

The planner is pure and deterministic.  It turns task dependencies and immutable
model requirements into the smallest set of shared runner apps needed at the
maximum simultaneous demand.  It never creates infrastructure; callers can
audit the returned plan before handing it to the Terraform lifecycle runtime.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass

from general_ludd.infra.azure_containerapp_gpu import (
    A100_PROFILE,
    T4_PROFILE,
    AzureContainerAppGPUUnavailable,
    ModelServingRequirement,
    select_smallest_sufficient_profile,
)
from general_ludd.scheduling.scheduler import CycleError, Scheduler, WorkItem

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
        _bounded_positive("max_apps", self.max_apps, _MAX_APPS)
        _bounded_positive(
            "max_total_replicas", self.max_total_replicas, _MAX_REPLICAS
        )
        _bounded_positive(
            "max_replicas_per_app",
            self.max_replicas_per_app,
            _MAX_REPLICAS_PER_APP,
        )
        _bounded_positive(
            "max_hourly_cost_microusd",
            self.max_hourly_cost_microusd,
            _MAX_COST_MICROUSD,
        )
        _bounded_positive("ttl_minutes", self.ttl_minutes, _MAX_TTL_MINUTES)


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
        _bounded_positive(
            "peak_concurrency", self.peak_concurrency, _MAX_CONCURRENCY
        )
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


_TraceSink = Callable[[TopologyTrace], None]
_RunnerKey = tuple[str, str, int, int, int, int, str]


def _bounded_positive(name: str, value: int, maximum: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 < value <= maximum
    ):
        raise ValueError(f"{name} must be a positive bounded integer")


def _discard_trace(_trace: TopologyTrace) -> None:
    return None


def _emit(sink: _TraceSink, trace: TopologyTrace) -> None:
    try:
        sink(trace)
    except Exception:
        raise RuntimeError("topology trace publication failed") from None


def _runner_key(demand: AzureRunnerDemand) -> _RunnerKey:
    requirement = demand.requirement
    return (
        requirement.model_id,
        requirement.revision,
        requirement.parameter_count,
        requirement.weight_bits,
        requirement.kv_cache_mib,
        requirement.runtime_overhead_mib,
        demand.runtime,
    )


def _runner_id(key: _RunnerKey) -> str:
    encoded = json.dumps(key, separators=(",", ":"), ensure_ascii=True).encode()
    return f"gludd-runner-{hashlib.sha256(encoded).hexdigest()[:12]}"


def _plan_batches(
    demands: tuple[AzureRunnerDemand, ...],
) -> tuple[tuple[str, ...], ...]:
    items = [
        WorkItem(id=demand.task_id, depends_on=demand.depends_on)
        for demand in demands
    ]
    try:
        planned = Scheduler().plan(items)
    except CycleError:
        raise AzureRunnerTopologyError("task dependency cycle is not deployable") from None
    except ValueError:
        raise AzureRunnerTopologyError("task depends on an unknown task") from None
    return tuple(tuple(batch) for batch in planned)


def _required_replicas(
    task_ids: set[str],
    batches: tuple[tuple[str, ...], ...],
    demands: dict[str, AzureRunnerDemand],
) -> tuple[int, int]:
    per_replica = min(demands[task_id].per_replica_concurrency for task_id in task_ids)
    simultaneous_peak = max(
        (
            sum(
                demands[task_id].peak_concurrency
                for task_id in batch
                if task_id in task_ids
            )
            for batch in batches
        ),
        default=0,
    )
    replicas = (simultaneous_peak + per_replica - 1) // per_replica
    return per_replica, replicas


def _app_plan(
    key: _RunnerKey,
    grouped_demands: tuple[AzureRunnerDemand, ...],
    batches: tuple[tuple[str, ...], ...],
    all_demands: dict[str, AzureRunnerDemand],
    profile_types: frozenset[str],
) -> AzureRunnerAppPlan:
    representative = grouped_demands[0]
    try:
        selection = select_smallest_sufficient_profile(
            representative.requirement,
            available_profile_types=set(profile_types),
        )
    except AzureContainerAppGPUUnavailable:
        raise AzureRunnerTopologyError(
            "right-sized profile is unavailable for a requested model"
        ) from None
    task_ids = {demand.task_id for demand in grouped_demands}
    per_replica, replicas = _required_replicas(
        task_ids, batches, all_demands
    )
    return AzureRunnerAppPlan(
        runner_id=_runner_id(key),
        task_ids=tuple(sorted(task_ids)),
        model_id=representative.requirement.model_id,
        model_revision=representative.requirement.revision,
        runtime=representative.runtime,
        profile_name=_PROFILE_NAMES[selection.profile.workload_profile_type],
        workload_profile_type=selection.profile.workload_profile_type,
        per_replica_concurrency=per_replica,
        min_replicas=0,
        max_replicas=replicas,
    )


def _canonical_digest(
    *,
    apps: tuple[AzureRunnerAppPlan, ...],
    profiles: tuple[AzureEnvironmentProfilePlan, ...],
    routes: tuple[GatewayRoute, ...],
    batches: tuple[tuple[str, ...], ...],
    cost: int,
    ttl_minutes: int,
) -> str:
    payload = {
        "apps": [asdict(app) for app in apps],
        "profiles": [asdict(profile) for profile in profiles],
        "routes": [asdict(route) for route in routes],
        "batches": batches,
        "max_hourly_cost_microusd": cost,
        "ttl_minutes": ttl_minutes,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_unique_tasks(demands: tuple[AzureRunnerDemand, ...]) -> None:
    identifiers = [demand.task_id for demand in demands]
    if len(identifiers) != len(set(identifiers)):
        raise AzureRunnerTopologyError("duplicate task_id is not deployable")


def plan_azure_runner_topology(
    demands: Iterable[AzureRunnerDemand],
    constraints: AzureFleetConstraints,
    *,
    trace_sink: _TraceSink = _discard_trace,
) -> AzureRunnerTopologyPlan:
    """Return the smallest bounded fleet that serves simultaneous task demand."""
    if not isinstance(constraints, AzureFleetConstraints):
        raise ValueError("constraints must be AzureFleetConstraints")
    if not callable(trace_sink):
        raise ValueError("trace_sink must be callable")
    try:
        ordered = tuple(sorted(demands, key=lambda demand: demand.task_id))
    except (AttributeError, TypeError):
        raise ValueError("demands must contain AzureRunnerDemand values") from None
    if not all(isinstance(demand, AzureRunnerDemand) for demand in ordered):
        raise ValueError("demands must contain AzureRunnerDemand values")
    _validate_unique_tasks(ordered)
    _emit(trace_sink, TopologyTrace("topology_started", len(ordered)))

    batches = _plan_batches(ordered)
    _emit(
        trace_sink,
        TopologyTrace(
            "topology_batches_planned", len(ordered), batch_count=len(batches)
        ),
    )
    demand_map = {demand.task_id: demand for demand in ordered}
    grouped: dict[_RunnerKey, list[AzureRunnerDemand]] = defaultdict(list)
    for demand in ordered:
        grouped[_runner_key(demand)].append(demand)
    profile_capacities = {
        profile.workload_profile_type: profile
        for profile in constraints.profile_capacities
    }
    apps = tuple(
        sorted(
            (
                _app_plan(
                    key,
                    tuple(group),
                    batches,
                    demand_map,
                    frozenset(profile_capacities),
                )
                for key, group in grouped.items()
            ),
            key=lambda app: app.runner_id,
        )
    )
    if len(apps) > constraints.max_apps:
        raise AzureRunnerTopologyError("fleet exceeds the app limit")
    if any(app.max_replicas > constraints.max_replicas_per_app for app in apps):
        raise AzureRunnerTopologyError("fleet exceeds the per-app replica limit")
    total_replicas = sum(app.max_replicas for app in apps)
    if total_replicas > constraints.max_total_replicas:
        raise AzureRunnerTopologyError("fleet exceeds the total replica limit")

    profile_totals: dict[str, int] = defaultdict(int)
    for app in apps:
        profile_totals[app.workload_profile_type] += app.max_replicas
    for profile_type, replicas in profile_totals.items():
        if replicas > profile_capacities[profile_type].max_replicas:
            raise AzureRunnerTopologyError("fleet exceeds a profile quota")
    profiles = tuple(
        AzureEnvironmentProfilePlan(
            profile_name=_PROFILE_NAMES[profile_type],
            workload_profile_type=profile_type,
            max_replicas=replicas,
        )
        for profile_type, replicas in sorted(profile_totals.items())
    )
    cost = sum(
        profile.max_replicas
        * profile_capacities[profile.workload_profile_type].hourly_cost_microusd_per_replica
        for profile in profiles
    )
    if cost > constraints.max_hourly_cost_microusd:
        raise AzureRunnerTopologyError("fleet exceeds the hourly cost limit")
    _emit(
        trace_sink,
        TopologyTrace(
            "topology_capacity_planned",
            len(ordered),
            batch_count=len(batches),
            app_count=len(apps),
            total_max_replicas=total_replicas,
        ),
    )
    routes = tuple(
        GatewayRoute(task_id=task_id, runner_id=app.runner_id)
        for app in apps
        for task_id in app.task_ids
    )
    digest = _canonical_digest(
        apps=apps,
        profiles=profiles,
        routes=routes,
        batches=batches,
        cost=cost,
        ttl_minutes=constraints.ttl_minutes,
    )
    plan = AzureRunnerTopologyPlan(
        apps=apps,
        profiles=profiles,
        routes=routes,
        batches=batches,
        max_hourly_cost_microusd=cost,
        ttl_minutes=constraints.ttl_minutes,
        plan_digest=digest,
    )
    _emit(
        trace_sink,
        TopologyTrace(
            "topology_completed",
            len(ordered),
            batch_count=len(batches),
            app_count=len(apps),
            total_max_replicas=total_replicas,
        ),
    )
    return plan


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
    "plan_azure_runner_topology",
]
