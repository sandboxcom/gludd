"""Select a right-sized model, runner, and accelerator topology."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

from general_ludd.hardware.accelerator_topology import (
    AcceleratorTopology,
    RunnerCapabilities,
    TopologyConstraints,
    TopologyPlan,
    TopologyPlanningError,
    plan_model_runner_topology,
)
from general_ludd.hardware.model_service_rightsizing import (
    DemandDerivationError,
    DerivedModelDemand,
    InferenceWorkloadDemand,
    ModelVariantEvidence,
    RunnerSizingEvidence,
    derive_model_runner_demand,
)

_MAX_INVENTORY_ITEMS = 4_096
_EvidenceT = TypeVar("_EvidenceT")


@dataclass(frozen=True, slots=True)
class ModelServiceSelection:
    """Immutable desired model service selected from attested evidence."""

    demand: DerivedModelDemand
    topology: TopologyPlan

    def to_dict(self) -> dict[str, object]:
        """Serialize the complete desired state for audit and reconciliation."""
        return {
            "schema_version": 1,
            "demand": self.demand.to_dict(),
            "topology": self.topology.to_dict(),
        }


class ModelServiceSelectionError(ValueError):
    """Stable fail-closed refusal when no unambiguous service is feasible."""

    def __init__(self, reason_codes: tuple[str, ...]) -> None:
        """Retain bounded, deduplicated reasons without workload content."""
        unique = tuple(sorted(set(reason_codes)))
        if not unique:
            unique = ("no_feasible_model_service",)
        self.reason_codes = unique
        super().__init__("no feasible model service: " + ", ".join(unique))


@dataclass(frozen=True, slots=True)
class _Candidate:
    selection: ModelServiceSelection


def _validate_tuple(
    value: object,
    item_type: type[object],
    field_name: str,
) -> None:
    if not isinstance(value, tuple) or any(
        not isinstance(item, item_type) for item in value
    ):
        raise ValueError(f"{field_name} must be a {item_type.__name__} tuple")
    if len(value) > _MAX_INVENTORY_ITEMS:
        raise ModelServiceSelectionError(("selection_inventory_too_large",))


def _validate_inputs(
    *,
    variants: object,
    workload: object,
    runner_sizing: object,
    pools: object,
    runner_capabilities: object,
    constraints: object,
) -> None:
    _validate_tuple(variants, ModelVariantEvidence, "variants")
    if not isinstance(workload, InferenceWorkloadDemand):
        raise ValueError("workload must be InferenceWorkloadDemand")
    _validate_tuple(runner_sizing, RunnerSizingEvidence, "runner_sizing")
    _validate_tuple(pools, AcceleratorTopology, "pools")
    _validate_tuple(
        runner_capabilities,
        RunnerCapabilities,
        "runner_capabilities",
    )
    if not isinstance(constraints, TopologyConstraints):
        raise ValueError("constraints must be TopologyConstraints")


def _require_inventory(
    *,
    variants: tuple[ModelVariantEvidence, ...],
    runner_sizing: tuple[RunnerSizingEvidence, ...],
    pools: tuple[AcceleratorTopology, ...],
    runner_capabilities: tuple[RunnerCapabilities, ...],
) -> None:
    for inventory, reason in (
        (variants, "empty_variant_inventory"),
        (runner_sizing, "empty_runner_sizing_inventory"),
        (runner_capabilities, "empty_runner_capability_inventory"),
        (pools, "empty_accelerator_inventory"),
    ):
        if not inventory:
            raise ModelServiceSelectionError((reason,))


def _unique_by_id(
    items: tuple[_EvidenceT, ...],
    attribute: str,
    reason: str,
) -> dict[str, _EvidenceT]:
    indexed: dict[str, _EvidenceT] = {}
    for item in items:
        item_id = getattr(item, attribute)
        if item_id in indexed:
            raise ModelServiceSelectionError((reason,))
        indexed[item_id] = item
    return indexed


def _candidate_rank(candidate: _Candidate) -> tuple[object, ...]:
    selected = candidate.selection
    plan = selected.topology
    demand = selected.demand
    return (
        plan.estimated_hourly_cost_microusd,
        plan.total_devices,
        -demand.quality_millis,
        demand.variant_id,
        demand.runner_id,
        plan.resource_key,
    )


def select_model_service(
    *,
    variants: tuple[ModelVariantEvidence, ...],
    workload: InferenceWorkloadDemand,
    runner_sizing: tuple[RunnerSizingEvidence, ...],
    pools: tuple[AcceleratorTopology, ...],
    runner_capabilities: tuple[RunnerCapabilities, ...],
    constraints: TopologyConstraints,
) -> ModelServiceSelection:
    """Choose the least-cost sufficient model service or refuse safely."""
    _validate_inputs(
        variants=variants,
        workload=workload,
        runner_sizing=runner_sizing,
        pools=pools,
        runner_capabilities=runner_capabilities,
        constraints=constraints,
    )
    _require_inventory(
        variants=variants,
        runner_sizing=runner_sizing,
        pools=pools,
        runner_capabilities=runner_capabilities,
    )
    _unique_by_id(
        variants,
        "variant_id",
        "ambiguous_variant_id",
    )
    _unique_by_id(
        runner_sizing,
        "runner_id",
        "ambiguous_runner_sizing_id",
    )
    capabilities_by_id = _unique_by_id(
        runner_capabilities,
        "runner_id",
        "ambiguous_runner_capability_id",
    )

    candidates: list[_Candidate] = []
    reasons: set[str] = set()
    for variant in variants:
        for sizing in runner_sizing:
            capability = capabilities_by_id.get(sizing.runner_id)
            if capability is None:
                reasons.add("runner_capability_missing")
                continue
            try:
                demand = derive_model_runner_demand(
                    variant=variant,
                    workload=workload,
                    runner=sizing,
                )
                topology = plan_model_runner_topology(
                    demand=demand.topology_demand,
                    pools=pools,
                    runners=(capability,),
                    constraints=constraints,
                )
            except DemandDerivationError as error:
                reasons.add(error.reason_code)
                continue
            except TopologyPlanningError as error:
                reasons.update(error.reason_codes)
                continue
            candidates.append(
                _Candidate(ModelServiceSelection(demand=demand, topology=topology))
            )

    if not candidates:
        raise ModelServiceSelectionError(tuple(reasons))
    return min(candidates, key=_candidate_rank).selection


__all__ = (
    "ModelServiceSelection",
    "ModelServiceSelectionError",
    "select_model_service",
)
