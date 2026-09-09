"""Plan a bounded Azure Container Apps model-runner fleet.

The pure planner turns task dependencies and immutable model requirements into
the smallest shared runner fleet at maximum simultaneous demand.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict

from general_ludd.infra.azure_containerapp_gpu import (
    AzureContainerAppGPUUnavailable,
    select_smallest_sufficient_profile,
)
from general_ludd.infra.azure_containerapp_topology_types import (
    _PROFILE_NAMES,
    AzureEnvironmentProfilePlan,
    AzureFleetConstraints,
    AzureProfileCapacity,
    AzureRunnerAppPlan,
    AzureRunnerDemand,
    AzureRunnerTopologyError,
    AzureRunnerTopologyPlan,
    GatewayRoute,
    TopologyTrace,
    TraceSink,
)
from general_ludd.scheduling.scheduler import CycleError, Scheduler, WorkItem

_RunnerKey = tuple[str, str, int, int, int, int, str]


def _discard_trace(_trace: TopologyTrace) -> None:
    return None


def _emit(sink: TraceSink, trace: TopologyTrace) -> None:
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
    return per_replica, (simultaneous_peak + per_replica - 1) // per_replica


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
    per_replica, replicas = _required_replicas(task_ids, batches, all_demands)
    return AzureRunnerAppPlan(
        runner_id=_runner_id(key),
        task_ids=tuple(sorted(task_ids)),
        model_id=representative.requirement.model_id,
        model_revision=representative.requirement.revision,
        runtime=representative.runtime,
        profile_name=_PROFILE_NAMES[selection.profile.workload_profile_type],
        workload_profile_type=selection.profile.workload_profile_type,
        per_replica_concurrency=per_replica,
        min_replicas=1,
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
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_unique_tasks(demands: tuple[AzureRunnerDemand, ...]) -> None:
    identifiers = [demand.task_id for demand in demands]
    if len(identifiers) != len(set(identifiers)):
        raise AzureRunnerTopologyError("duplicate task_id is not deployable")


def _group_apps(
    demands: tuple[AzureRunnerDemand, ...],
    batches: tuple[tuple[str, ...], ...],
    capacities: dict[str, AzureProfileCapacity],
) -> tuple[AzureRunnerAppPlan, ...]:
    demand_map = {demand.task_id: demand for demand in demands}
    grouped: dict[_RunnerKey, list[AzureRunnerDemand]] = defaultdict(list)
    for demand in demands:
        grouped[_runner_key(demand)].append(demand)
    return tuple(
        sorted(
            (
                _app_plan(
                    key,
                    tuple(group),
                    batches,
                    demand_map,
                    frozenset(capacities),
                )
                for key, group in grouped.items()
            ),
            key=lambda app: app.runner_id,
        )
    )


def _profile_plan(
    apps: tuple[AzureRunnerAppPlan, ...],
    capacities: dict[str, AzureProfileCapacity],
) -> tuple[tuple[AzureEnvironmentProfilePlan, ...], int]:
    totals: dict[str, int] = defaultdict(int)
    for app in apps:
        totals[app.workload_profile_type] += app.max_replicas
    for profile_type, replicas in totals.items():
        if replicas > capacities[profile_type].max_replicas:
            raise AzureRunnerTopologyError("fleet exceeds a profile quota")
    profiles = tuple(
        AzureEnvironmentProfilePlan(
            profile_name=_PROFILE_NAMES[profile_type],
            workload_profile_type=profile_type,
            max_replicas=replicas,
        )
        for profile_type, replicas in sorted(totals.items())
    )
    cost = sum(
        profile.max_replicas
        * capacities[profile.workload_profile_type].hourly_cost_microusd_per_replica
        for profile in profiles
    )
    return profiles, cost


def plan_azure_runner_topology(
    demands: Iterable[AzureRunnerDemand],
    constraints: AzureFleetConstraints,
    *,
    trace_sink: TraceSink = _discard_trace,
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
            "topology_batches_planned",
            len(ordered),
            batch_count=len(batches),
        ),
    )
    capacities = {
        profile.workload_profile_type: profile
        for profile in constraints.profile_capacities
    }
    apps = _group_apps(ordered, batches, capacities)
    if len(apps) > constraints.max_apps:
        raise AzureRunnerTopologyError("fleet exceeds the app limit")
    if any(app.max_replicas > constraints.max_replicas_per_app for app in apps):
        raise AzureRunnerTopologyError("fleet exceeds the per-app replica limit")
    total_replicas = sum(app.max_replicas for app in apps)
    if total_replicas > constraints.max_total_replicas:
        raise AzureRunnerTopologyError("fleet exceeds the total replica limit")
    profiles, cost = _profile_plan(apps, capacities)
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
    plan = AzureRunnerTopologyPlan(
        apps=apps,
        profiles=profiles,
        routes=routes,
        batches=batches,
        max_hourly_cost_microusd=cost,
        ttl_minutes=constraints.ttl_minutes,
        plan_digest=_canonical_digest(
            apps=apps,
            profiles=profiles,
            routes=routes,
            batches=batches,
            cost=cost,
            ttl_minutes=constraints.ttl_minutes,
        ),
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
