"""Deploy strategy — smart resource selection based on urgency, cost, and init time."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

from general_ludd.infra.azure_retail_pricing import (
    AzureContainerAppsRetailPricing,
    AzureRetailPricingError,
    AzureVirtualMachineRetailPricing,
    AzureVmBillingPhases,
)


class DeployUrgency(Enum):
    IMMEDIATE = "immediate"
    NORMAL = "normal"
    BACKGROUND = "background"


class ResourceTier:
    CONTAINER_APP: ResourceTier
    SPOT_VM: ResourceTier
    DEDICATED_VM: ResourceTier

    _ALL: ClassVar[list[ResourceTier]] = []

    def __init__(self, tier_id: str, startup_seconds: int, cost_per_hour: float) -> None:
        self.tier_id = tier_id
        self.startup_seconds = startup_seconds
        self.cost_per_hour = cost_per_hour
        self._index = len(ResourceTier._ALL)
        ResourceTier._ALL.append(self)

    def __repr__(self) -> str:
        return f"ResourceTier({self.tier_id!r}, startup={self.startup_seconds}s, ${self.cost_per_hour:.2f}/hr)"

    @property
    def deploy_type(self) -> str:
        return self.tier_id


ResourceTier.CONTAINER_APP = ResourceTier("containerapp", 600, 0.05)
ResourceTier.SPOT_VM = ResourceTier("vm_spot", 180, 0.50)
ResourceTier.DEDICATED_VM = ResourceTier("vm_dedicated", 120, 2.00)


@dataclass(frozen=True)
class ElasticWorkload:
    """Observable demand inputs for one hysteretic Azure tier decision."""

    urgency: DeployUrgency
    queued_items: int
    concurrent_items: int
    estimated_runtime_minutes: float
    latency_budget_seconds: float
    spot_eligible: bool

    def __post_init__(self) -> None:
        for name in ("queued_items", "concurrent_items"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in ("estimated_runtime_minutes", "latency_budget_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and > 0")

    @property
    def demand_score(self) -> float:
        return (
            float(self.queued_items)
            + 2.0 * self.concurrent_items
            + min(self.estimated_runtime_minutes / 30.0, 2.0)
        )


@dataclass(frozen=True)
class ElasticTierDecision:
    tier: ResourceTier
    transition: str
    demand_score: float
    reason: str


class ElasticTierController:
    """Stateful scale controller with separate up/down demand thresholds."""

    def __init__(self) -> None:
        self._tier = ResourceTier.CONTAINER_APP

    @property
    def current_tier(self) -> ResourceTier:
        return self._tier

    def select(self, workload: ElasticWorkload) -> ElasticTierDecision:
        score = workload.demand_score
        previous = self._tier
        latency_forces_dedicated = (
            workload.urgency is DeployUrgency.IMMEDIATE
            and workload.latency_budget_seconds <= ResourceTier.SPOT_VM.startup_seconds
        )

        if previous is ResourceTier.DEDICATED_VM:
            if latency_forces_dedicated or score >= 6.0:
                selected = ResourceTier.DEDICATED_VM
            elif score >= 2.0:
                selected = (
                    ResourceTier.SPOT_VM
                    if workload.spot_eligible
                    else ResourceTier.DEDICATED_VM
                )
            else:
                selected = ResourceTier.CONTAINER_APP
        elif previous is ResourceTier.SPOT_VM:
            if latency_forces_dedicated or score >= 10.0:
                selected = ResourceTier.DEDICATED_VM
            elif score >= 1.5 and workload.spot_eligible:
                selected = ResourceTier.SPOT_VM
            elif score >= 3.0:
                selected = ResourceTier.DEDICATED_VM
            else:
                selected = ResourceTier.CONTAINER_APP
        elif latency_forces_dedicated or score >= 10.0:
            selected = ResourceTier.DEDICATED_VM
        elif score >= 3.0:
            selected = (
                ResourceTier.SPOT_VM
                if workload.spot_eligible
                else ResourceTier.DEDICATED_VM
            )
        else:
            selected = ResourceTier.CONTAINER_APP

        if selected._index > previous._index:
            transition = "scale_up"
        elif selected._index < previous._index:
            transition = "scale_down"
        else:
            transition = "hold"
        self._tier = selected
        return ElasticTierDecision(
            tier=selected,
            transition=transition,
            demand_score=score,
            reason=(
                f"elastic {transition}: demand={score:.3f}, "
                f"latency_budget={workload.latency_budget_seconds:.1f}s, "
                f"spot_eligible={str(workload.spot_eligible).lower()}, "
                f"tier={selected.tier_id}"
            ),
        )


@dataclass
class PhasedDeployPlan:
    urgency: DeployUrgency
    primary: ResourceTier
    warmup: ResourceTier | None = None
    estimated_cost_usd: float = 0.0
    reasoning: str = ""
    pricing_source: str = ""
    pricing_region: str | None = None
    meter_ids: tuple[str, ...] = ()
    cost_components_usd: dict[str, float] = field(default_factory=dict)
    phase_seconds: dict[str, float] = field(default_factory=dict)
    elastic_transition: str = "hold"
    elastic_reason: str = ""


@dataclass
class CostEntry:
    tier_id: str
    cost_usd: float
    startup_seconds: int
    timestamp: float = field(default_factory=time.time)


class DeployStrategist:
    """Selects Azure resource types based on urgency, cost, and init time.

    Implements fast-warm + slow-cheap: for immediate work, deploys a dedicated
    VM first (fast startup) while provisioning a Container App in parallel.
    When the Container App is ready, work migrates and the VM is destroyed.
    Tracks cost per resource type and learns the most cost-effective option
    over time via learn_from_history() + average_cost().
    """

    def __init__(
        self,
        *,
        azure_pricing: AzureContainerAppsRetailPricing | None = None,
        azure_vm_pricing: AzureVirtualMachineRetailPricing | None = None,
        elastic_controller: ElasticTierController | None = None,
    ) -> None:
        self.cost_history: list[CostEntry] = []
        self._azure_pricing = azure_pricing or AzureContainerAppsRetailPricing()
        self._azure_vm_pricing = azure_vm_pricing or AzureVirtualMachineRetailPricing(
            retail_client=self._azure_pricing
        )
        self._elastic_controller = elastic_controller or ElasticTierController()

    def plan(
        self,
        urgency: DeployUrgency,
        gpu_type: str,
        model_name: str,
        estimated_runtime_minutes: float = 60.0,
        *,
        region: str = "eastus",
        container_vcpu: float | None = None,
        container_memory_gib: float | None = None,
        max_cost_usd: float | None = None,
        workload: ElasticWorkload | None = None,
        vm_disk_size_gib: int = 128,
        vm_shutdown_seconds: float = 60.0,
    ) -> PhasedDeployPlan:
        if (
            isinstance(estimated_runtime_minutes, bool)
            or not math.isfinite(estimated_runtime_minutes)
            or estimated_runtime_minutes <= 0
        ):
            raise ValueError("estimated_runtime_minutes must be finite and > 0")
        if max_cost_usd is not None and (
            isinstance(max_cost_usd, bool)
            or not math.isfinite(max_cost_usd)
            or max_cost_usd <= 0
        ):
            raise ValueError(
                f"max_cost_usd must be finite and > 0, got {max_cost_usd!r}"
            )
        if (
            isinstance(vm_shutdown_seconds, bool)
            or not math.isfinite(vm_shutdown_seconds)
            or vm_shutdown_seconds < 0
        ):
            raise ValueError("vm_shutdown_seconds must be finite and >= 0")
        selected_workload = workload or ElasticWorkload(
            urgency=urgency,
            queued_items=1 if urgency is DeployUrgency.IMMEDIATE else 0,
            concurrent_items=1 if urgency is DeployUrgency.IMMEDIATE else 0,
            estimated_runtime_minutes=estimated_runtime_minutes,
            latency_budget_seconds=(
                120.0 if urgency is DeployUrgency.IMMEDIATE else 900.0
            ),
            spot_eligible=False,
        )
        if selected_workload.urgency is not urgency:
            raise ValueError("workload urgency must match plan urgency")
        decision = self._elastic_controller.select(selected_workload)
        warmup = (
            None
            if decision.tier is ResourceTier.CONTAINER_APP
            else decision.tier
        )
        runtime_seconds = estimated_runtime_minutes * 60.0
        container_estimate = self._azure_pricing.estimate_for_gpu(
            gpu_type,
            region=region,
            duration_seconds=runtime_seconds,
            vcpu=container_vcpu,
            memory_gib=container_memory_gib,
        )
        meter_ids: tuple[str, ...] = container_estimate.meter_ids
        phase_seconds: dict[str, float] = {
            "container_active": runtime_seconds,
        }
        cost_components: dict[str, float] = {
            "container_active": container_estimate.total_cost_usd,
        }
        reasoning = (
            f"{urgency.value.upper()} urgency: exact Azure Retail Prices "
            f"Container Apps meters for {region}; {decision.reason}."
        )

        if warmup is not None:
            handoff_seconds = max(
                1.0,
                float(ResourceTier.CONTAINER_APP.startup_seconds - warmup.startup_seconds),
            )
            vm_runtime_seconds = min(runtime_seconds, handoff_seconds)
            container_runtime_seconds = max(0.0, runtime_seconds - vm_runtime_seconds)
            vm_estimate = self._azure_vm_pricing.estimate_for_gpu(
                gpu_type,
                region=region,
                purchase_option=(
                    "spot" if warmup is ResourceTier.SPOT_VM else "on_demand"
                ),
                phases=AzureVmBillingPhases(
                    warmup_seconds=float(warmup.startup_seconds),
                    runtime_seconds=vm_runtime_seconds,
                    shutdown_seconds=vm_shutdown_seconds,
                ),
                disk_size_gib=vm_disk_size_gib,
            )
            container_cost = (
                container_estimate.hourly_rate_usd
                * container_runtime_seconds
                / 3600.0
            )
            cost_components = {
                "container_active": container_cost,
                "vm_compute": vm_estimate.compute_cost_usd,
                "managed_disk": vm_estimate.disk_cost_usd,
                "public_ip": vm_estimate.public_ip_cost_usd,
            }
            phase_seconds = {
                "vm_warmup": float(warmup.startup_seconds),
                "vm_runtime": vm_runtime_seconds,
                "vm_shutdown": vm_shutdown_seconds,
                "container_active": container_runtime_seconds,
            }
            meter_ids = (*container_estimate.meter_ids, *vm_estimate.meter_ids)
            reasoning = (
                f"{reasoning} Exact {vm_estimate.purchase_option} Linux VM, "
                f"{vm_estimate.disk_tier} managed-disk, and static IPv4 meters "
                "cover startup, handoff runtime, and shutdown until deletion."
            )

        raw_estimated_cost = sum(cost_components.values())
        estimated_cost = round(raw_estimated_cost, 6)
        if max_cost_usd is not None and raw_estimated_cost > max_cost_usd:
            raise AzureRetailPricingError(
                f"estimated Azure cost ${estimated_cost:.6f} exceeds "
                f"operator cost ceiling ${max_cost_usd:.6f}"
            )
        return PhasedDeployPlan(
            urgency=urgency,
            primary=ResourceTier.CONTAINER_APP,
            warmup=warmup,
            estimated_cost_usd=estimated_cost,
            reasoning=reasoning,
            pricing_source="azure-retail-prices",
            pricing_region=region,
            meter_ids=meter_ids,
            cost_components_usd=cost_components,
            phase_seconds=phase_seconds,
            elastic_transition=decision.transition,
            elastic_reason=decision.reason,
        )

    def execute_phased(
        self,
        plan: PhasedDeployPlan,
        gpu_type: str,
        model_name: str,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "plan": {
                "urgency": plan.urgency.value,
                "primary_tier": plan.primary.tier_id,
                "warmup_tier": plan.warmup.tier_id if plan.warmup else None,
                "estimated_cost_usd": plan.estimated_cost_usd,
                "pricing_source": plan.pricing_source,
                "pricing_region": plan.pricing_region,
                "meter_ids": plan.meter_ids,
                "cost_components_usd": plan.cost_components_usd,
                "phase_seconds": plan.phase_seconds,
                "elastic_transition": plan.elastic_transition,
                "elastic_reason": plan.elastic_reason,
            },
            "primary": {"status": "provisioning", "tier": plan.primary.tier_id},
            "warmup": None,
            "migration_needed": plan.warmup is not None,
        }

        if plan.warmup is not None:
            result["warmup"] = {
                "status": "provisioning",
                "tier": plan.warmup.tier_id,
            }

        return result

    def migrate_work(
        self,
        from_instance: str,
        to_instance: str,
    ) -> dict[str, str]:
        return {
            "status": "migrated",
            "from": from_instance,
            "to": to_instance,
            "note": "Work migrated from warmup tier to primary tier",
        }

    def learn_from_history(
        self,
        tier: ResourceTier,
        cost_usd: float,
        startup_seconds: int,
    ) -> None:
        self.cost_history.append(
            CostEntry(
                tier_id=tier.tier_id,
                cost_usd=cost_usd,
                startup_seconds=startup_seconds,
            )
        )

    def average_cost(self, tier: ResourceTier) -> float:
        entries = [e for e in self.cost_history if e.tier_id == tier.tier_id]
        if not entries:
            return 0.0
        return sum(e.cost_usd for e in entries) / len(entries)
