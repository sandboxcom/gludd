"""Join universal task demand to model selection and runner launch state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from general_ludd.execution.universal_task_types import (
    ExecutionTarget,
    UniversalTaskRequest,
)
from general_ludd.hardware.accelerator_topology import (
    AcceleratorTopology,
    RunnerCapabilities,
    TopologyConstraints,
)
from general_ludd.hardware.model_runner_launch import (
    RunnerLaunchError,
    RunnerLaunchPlan,
    RunnerLaunchProfile,
    render_model_runner_launch,
)
from general_ludd.hardware.model_service_rightsizing import (
    ModelVariantEvidence,
    RunnerSizingEvidence,
)
from general_ludd.hardware.model_service_selection import (
    ModelServiceSelection,
    ModelServiceSelectionError,
    select_model_service,
)

_MAX_REASON_LENGTH = 128
_MAX_INVENTORY_ITEMS = 4_096


def _typed_tuple(value: object, item_type: type[object], field_name: str) -> None:
    if not isinstance(value, tuple) or any(
        not isinstance(item, item_type) for item in value
    ):
        raise ValueError(f"{field_name} must be a {item_type.__name__} tuple")
    if len(value) > _MAX_INVENTORY_ITEMS:
        raise ValueError(f"{field_name} exceeds its bounded item count")


@dataclass(frozen=True, slots=True)
class ModelServiceInventorySnapshot:
    """One immutable set of model, runner, topology, and launch evidence."""

    variants: tuple[ModelVariantEvidence, ...]
    runner_sizing: tuple[RunnerSizingEvidence, ...]
    pools: tuple[AcceleratorTopology, ...]
    runner_capabilities: tuple[RunnerCapabilities, ...]
    constraints: TopologyConstraints
    launch_profiles: tuple[RunnerLaunchProfile, ...]

    def __post_init__(self) -> None:
        """Reject mutable, wrong-type, or unbounded evidence inventories."""
        for value, item_type, field_name in (
            (self.variants, ModelVariantEvidence, "variants"),
            (self.runner_sizing, RunnerSizingEvidence, "runner_sizing"),
            (self.pools, AcceleratorTopology, "pools"),
            (
                self.runner_capabilities,
                RunnerCapabilities,
                "runner_capabilities",
            ),
            (self.launch_profiles, RunnerLaunchProfile, "launch_profiles"),
        ):
            _typed_tuple(value, item_type, field_name)
        if not isinstance(self.constraints, TopologyConstraints):
            raise ValueError("constraints must be TopologyConstraints")


@dataclass(frozen=True, slots=True)
class UniversalModelServicePlan:
    """Selected model/topology and tokenized launch state for one task."""

    selection: ModelServiceSelection
    launch: RunnerLaunchPlan

    def __post_init__(self) -> None:
        """Require one internally consistent immutable service generation."""
        if not isinstance(self.selection, ModelServiceSelection):
            raise ValueError("selection must be ModelServiceSelection")
        if not isinstance(self.launch, RunnerLaunchPlan):
            raise ValueError("launch must be RunnerLaunchPlan")
        if self.selection.demand.runner_id != self.launch.runner_id:
            raise ValueError("selection and launch runner identities differ")
        if self.selection.demand.data_parallel_replicas != self.replica_count:
            raise ValueError("selection and launch replica counts differ")
        if (
            self.selection.topology.model_parallel_devices
            != self.devices_per_replica
        ):
            raise ValueError("selection and launch device counts differ")

    @property
    def resource_key(self) -> str:
        """Return the selected provider-neutral accelerator identity."""
        return self.selection.topology.resource_key

    @property
    def runner_id(self) -> str:
        """Return the attested runner identity."""
        return self.launch.runner_id

    @property
    def replica_count(self) -> int:
        """Return independent data-parallel replacement units."""
        return self.launch.replica_count

    @property
    def devices_per_replica(self) -> int:
        """Return visible accelerators required by each runner process."""
        return self.launch.devices_per_replica

    def to_dict(self) -> dict[str, object]:
        """Serialize exact credential-free task execution desired state."""
        return {
            "schema_version": 1,
            "selection": self.selection.to_dict(),
            "launch": self.launch.to_dict(),
        }


class UniversalModelServicePlanningError(ValueError):
    """Stable task-graph refusal from model-service planning."""

    def __init__(self, reason_codes: tuple[str, ...]) -> None:
        """Retain deduplicated bounded reasons without task content."""
        if not isinstance(reason_codes, tuple):
            raise ValueError("reason_codes must be a tuple")
        for reason in reason_codes:
            if (
                not isinstance(reason, str)
                or not reason
                or len(reason) > _MAX_REASON_LENGTH
                or any(delimiter in reason for delimiter in "\x00\r\n")
            ):
                raise ValueError("reason_codes must contain bounded text")
        unique = tuple(sorted(set(reason_codes)))
        if not unique:
            unique = ("model_service_planning_refused",)
        self.reason_codes = unique
        super().__init__("universal model-service planning refused: " + ", ".join(unique))


class UniversalModelServicePlanner:
    """Plan a model service for any typed universal task workload."""

    def __init__(
        self,
        inventory_source: Callable[
            [UniversalTaskRequest, ExecutionTarget],
            ModelServiceInventorySnapshot,
        ],
    ) -> None:
        """Bind a caller-owned immutable evidence snapshot source."""
        if not callable(inventory_source):
            raise ValueError("inventory_source must be callable")
        self._inventory_source = inventory_source

    def plan(
        self,
        request: UniversalTaskRequest,
        target: ExecutionTarget,
    ) -> UniversalModelServicePlan:
        """Select and render one target-compatible model service."""
        if not isinstance(request, UniversalTaskRequest):
            raise ValueError("request must be UniversalTaskRequest")
        if not isinstance(target, ExecutionTarget):
            raise ValueError("target must be ExecutionTarget")
        if request.model_workload is None:
            raise UniversalModelServicePlanningError(("model_workload_missing",))
        runner_id = target.model_runner_id
        if runner_id is None:
            raise UniversalModelServicePlanningError(
                ("model_runner_identity_missing",)
            )
        snapshot = self._inventory_source(request, target)
        if not isinstance(snapshot, ModelServiceInventorySnapshot):
            raise ValueError("inventory_source must return ModelServiceInventorySnapshot")

        profiles = tuple(
            profile
            for profile in snapshot.launch_profiles
            if profile.runner_id == runner_id
        )
        if not profiles:
            raise UniversalModelServicePlanningError(("launch_profile_missing",))
        if len(profiles) > 1:
            raise UniversalModelServicePlanningError(("launch_profile_ambiguous",))

        try:
            selection = select_model_service(
                variants=snapshot.variants,
                workload=request.model_workload,
                runner_sizing=tuple(
                    sizing
                    for sizing in snapshot.runner_sizing
                    if sizing.runner_id == runner_id
                ),
                pools=tuple(
                    pool
                    for pool in snapshot.pools
                    if pool.provider == target.provider
                ),
                runner_capabilities=tuple(
                    capability
                    for capability in snapshot.runner_capabilities
                    if capability.runner_id == runner_id
                ),
                constraints=snapshot.constraints,
            )
        except ModelServiceSelectionError as error:
            raise UniversalModelServicePlanningError(error.reason_codes) from error
        try:
            launch = render_model_runner_launch(
                selection=selection,
                profile=profiles[0],
            )
        except RunnerLaunchError as error:
            raise UniversalModelServicePlanningError((error.reason_code,)) from error
        return UniversalModelServicePlan(selection=selection, launch=launch)


__all__ = (
    "ModelServiceInventorySnapshot",
    "UniversalModelServicePlan",
    "UniversalModelServicePlanner",
    "UniversalModelServicePlanningError",
)
