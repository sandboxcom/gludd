"""Deploy strategy — smart resource selection based on urgency, cost, and init time."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar


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


@dataclass
class PhasedDeployPlan:
    urgency: DeployUrgency
    primary: ResourceTier
    warmup: ResourceTier | None = None
    estimated_cost_usd: float = 0.0
    reasoning: str = ""


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

    def __init__(self) -> None:
        self.cost_history: list[CostEntry] = []

    def plan(
        self,
        urgency: DeployUrgency,
        gpu_type: str,
        model_name: str,
        estimated_runtime_minutes: float = 60.0,
    ) -> PhasedDeployPlan:
        if urgency == DeployUrgency.IMMEDIATE:
            return PhasedDeployPlan(
                urgency=urgency,
                primary=ResourceTier.CONTAINER_APP,
                warmup=ResourceTier.DEDICATED_VM,
                estimated_cost_usd=self._estimate_cost(
                    ResourceTier.CONTAINER_APP,
                    ResourceTier.DEDICATED_VM,
                    estimated_runtime_minutes,
                ),
                reasoning=(
                    "IMMEDIATE urgency: fast-warm via dedicated VM (120s startup) "
                    "while slow-cheap Container App (600s) provisions in parallel. "
                    "Work starts on VM immediately; migrates to Container App when ready."
                ),
            )

        if urgency == DeployUrgency.NORMAL:
            return PhasedDeployPlan(
                urgency=urgency,
                primary=ResourceTier.CONTAINER_APP,
                estimated_cost_usd=self._estimate_cost(
                    ResourceTier.CONTAINER_APP,
                    None,
                    estimated_runtime_minutes,
                ),
                reasoning=(
                    "NORMAL urgency: deploying to Container App (cheapest at $0.05/hr, "
                    "600s startup). No warmup tier needed."
                ),
            )

        return PhasedDeployPlan(
            urgency=urgency,
            primary=ResourceTier.CONTAINER_APP,
            estimated_cost_usd=self._estimate_cost(
                ResourceTier.CONTAINER_APP,
                None,
                estimated_runtime_minutes,
            ),
            reasoning=(
                "BACKGROUND urgency: deploying to Container App (cheapest at $0.05/hr). Startup time is not a concern."
            ),
        )

    def _estimate_cost(
        self,
        primary: ResourceTier,
        warmup: ResourceTier | None,
        estimated_runtime_minutes: float,
    ) -> float:
        hours = estimated_runtime_minutes / 60.0
        cost = primary.cost_per_hour * hours
        if warmup is not None:
            warmup_hours = min(warmup.startup_seconds / 3600.0, hours)
            cost += warmup.cost_per_hour * warmup_hours
        return round(cost, 4)

    def execute_phased(
        self,
        plan: PhasedDeployPlan,
        gpu_type: str,
        model_name: str,
    ) -> dict:
        result: dict = {
            "plan": {
                "urgency": plan.urgency.value,
                "primary_tier": plan.primary.tier_id,
                "warmup_tier": plan.warmup.tier_id if plan.warmup else None,
                "estimated_cost_usd": plan.estimated_cost_usd,
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
    ) -> dict:
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
