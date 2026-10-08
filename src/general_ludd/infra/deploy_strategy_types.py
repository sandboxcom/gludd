"""Value contracts and hysteresis controller for Azure deployment planning.

Keeping these provider-neutral planning values separate lets other workloads use
the same deployment policy without depending on the pricing/execution adapter.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar


class DeployUrgency(Enum):
    """Operator-visible urgency classes used by deployment policy."""

    IMMEDIATE = "immediate"
    NORMAL = "normal"
    BACKGROUND = "background"


class ResourceTier:
    """Ordered deployment tier with startup and fallback price metadata."""

    CONTAINER_APP: ResourceTier
    SPOT_VM: ResourceTier
    DEDICATED_VM: ResourceTier

    _ALL: ClassVar[list[ResourceTier]] = []

    def __init__(self, tier_id: str, startup_seconds: int, cost_per_hour: float) -> None:
        """Register one ordered deployment tier."""
        self.tier_id = tier_id
        self.startup_seconds = startup_seconds
        self.cost_per_hour = cost_per_hour
        self._index = len(ResourceTier._ALL)
        ResourceTier._ALL.append(self)

    def __repr__(self) -> str:
        """Return an operator-readable tier summary."""
        return (
            f"ResourceTier({self.tier_id!r}, startup={self.startup_seconds}s, "
            f"${self.cost_per_hour:.2f}/hr)"
        )

    @property
    def deploy_type(self) -> str:
        """Return the stable deployment-provider identifier."""
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
        """Reject negative, non-integral, or non-finite demand inputs."""
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
        """Return the normalized demand score used by hysteresis policy."""
        return (
            float(self.queued_items)
            + 2.0 * self.concurrent_items
            + min(self.estimated_runtime_minutes / 30.0, 2.0)
        )


@dataclass(frozen=True)
class ElasticTierDecision:
    """One selected tier with transition and operator-readable evidence."""

    tier: ResourceTier
    transition: str
    demand_score: float
    reason: str


class ElasticTierController:
    """Stateful scale controller with separate up/down demand thresholds."""

    def __init__(self) -> None:
        """Start the hysteresis controller at the smallest resource tier."""
        self._tier = ResourceTier.CONTAINER_APP

    @property
    def current_tier(self) -> ResourceTier:
        """Return the tier retained from the most recent selection."""
        return self._tier

    def select(self, workload: ElasticWorkload) -> ElasticTierDecision:
        """Select and retain a tier using bounded up/down thresholds."""
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
    """Serializable result of exact-price phased deployment planning."""

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
    """One observed resource-tier cost and startup duration."""

    tier_id: str
    cost_usd: float
    startup_seconds: int
    timestamp: float = field(default_factory=time.time)


__all__ = (
    "CostEntry",
    "DeployUrgency",
    "ElasticTierController",
    "ElasticTierDecision",
    "ElasticWorkload",
    "PhasedDeployPlan",
    "ResourceTier",
)
