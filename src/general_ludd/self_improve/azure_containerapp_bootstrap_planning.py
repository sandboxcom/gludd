"""Pure ownership and topology planning for configured Azure candidates."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from general_ludd.infra.azure_containerapp_gpu import ModelServingRequirement
from general_ludd.infra.azure_containerapp_topology import (
    AzureFleetConstraints,
    AzureRunnerDemand,
    AzureRunnerTopologyPlan,
    TopologyTrace,
    plan_azure_runner_topology,
)
from general_ludd.self_improve.azure_containerapp_bootstrap_settings import (
    AzureContainerAppBootstrapSettings,
)

_PROTOCOL = "gludd-configured-azure-containerapp-bootstrap-v1"


def azure_bootstrap_owner_digest(
    repo_root: Path,
    settings: AzureContainerAppBootstrapSettings,
) -> str:
    """Bind environment ownership to one canonical project and Azure scope."""
    encoded = json.dumps(
        {
            "environment": settings.environment_name,
            "project_identity": hashlib.sha256(
                str(repo_root).encode("utf-8", errors="surrogatepass")
            ).hexdigest(),
            "protocol": _PROTOCOL,
            "resource_group": settings.resource_group,
            "subscription_id": settings.subscription_id,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def plan_azure_bootstrap_topology(
    settings: AzureContainerAppBootstrapSettings,
    requirement: ModelServingRequirement,
    progress_sink: Callable[[str], None],
) -> AzureRunnerTopologyPlan:
    """Size one bounded runner topology from observed profile capacities."""
    max_profile_replicas = max(
        capacity.max_replicas for capacity in settings.profile_capacities
    )
    constraints = AzureFleetConstraints(
        profile_capacities=settings.profile_capacities,
        max_apps=1,
        max_total_replicas=max_profile_replicas,
        max_replicas_per_app=max_profile_replicas,
        max_hourly_cost_microusd=settings.max_hourly_cost_microusd,
        ttl_minutes=settings.ttl_minutes,
    )

    def trace(event: TopologyTrace) -> None:
        progress_sink(
            "SELF_IMPROVE_AZURE_BOOTSTRAP "
            f"phase={event.phase} task_count={event.task_count} "
            f"batch_count={event.batch_count} app_count={event.app_count} "
            f"total_max_replicas={event.total_max_replicas} secret_output=false"
        )

    return plan_azure_runner_topology(
        (
            AzureRunnerDemand(
                task_id="self-improve",
                requirement=requirement,
                peak_concurrency=settings.peak_concurrency,
                per_replica_concurrency=settings.per_replica_concurrency,
            ),
        ),
        constraints,
        trace_sink=trace,
    )


__all__ = ("azure_bootstrap_owner_digest", "plan_azure_bootstrap_topology")
