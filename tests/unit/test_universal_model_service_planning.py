"""Universal task-graph integration for model-service planning."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from general_ludd.execution.model_service_planner import (
    ModelServiceInventorySnapshot,
    UniversalModelServicePlan,
    UniversalModelServicePlanner,
    UniversalModelServicePlanningError,
)
from general_ludd.execution.universal_task import (
    AdapterDecision,
    CandidateAssessment,
    ExecutionTarget,
    ModelServicePlannerProtocol,
    ModelServicePlanProtocol,
    TaskStatus,
    UniversalTaskExecutor,
    UniversalTaskRequest,
)
from general_ludd.hardware.accelerator_topology import (
    AcceleratorCost,
    AcceleratorTopology,
    DistributionMode,
    PartitioningMode,
    RunnerCapabilities,
    TopologyConstraints,
)
from general_ludd.hardware.model_runner_launch import (
    RunnerLaunchPlan,
    vllm_launch_profile,
)
from general_ludd.hardware.model_service_rightsizing import (
    InferenceWorkloadDemand,
    ModelVariantEvidence,
    RunnerSizingEvidence,
)
from general_ludd.hardware.model_service_selection import ModelServiceSelection
from general_ludd.scheduling.scheduler import WorkItem

_GIB = 1024**3


def test_focused_coverage_profile_measures_universal_planner() -> None:
    profile = Path("config/coverage_universal_model_service_planning.ini").read_text()

    assert "*/src/general_ludd/execution/model_service_planner.py" in profile


def _workload(capability: str = "chemistry.design") -> InferenceWorkloadDemand:
    return InferenceWorkloadDemand(
        workload_id=f"task:{capability}",
        input_tokens=4_000,
        output_tokens=1_000,
        concurrent_sequences=2,
        minimum_quality_millis=900,
        max_p95_latency_millis=2_000,
    )


def _request(
    capability: str,
    *,
    with_workload: bool = True,
) -> UniversalTaskRequest:
    return UniversalTaskRequest(
        task_id=f"task:{capability}",
        capability=capability,
        instruction="Produce one bounded, validated candidate.",
        budget_usd=10.0,
        resources=frozenset({"workspace:task"}),
        model_workload=_workload(capability) if with_workload else None,
    )


def _target(capability: str) -> ExecutionTarget:
    return ExecutionTarget(
        profile_id="gateway-profile",
        provider="azure",
        accelerator_sku="observed-gpu",
        capabilities=frozenset({capability}),
        allowed_data_classifications=frozenset({"public"}),
        estimated_cost_usd=0.25,
        healthy=True,
        health_evidence="live-health-observation",
        capability_evidence="task-quality-observation",
        cost_evidence="provider-price-observation",
        privacy_evidence="public-data-policy",
        offline=False,
        model_runner_id="vllm-observed",
    )


@dataclass(frozen=True, slots=True)
class _FakeServicePlan:
    resource_key: str = "azure:pool:0"
    runner_id: str = "vllm-observed"
    replica_count: int = 2
    devices_per_replica: int = 4

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_key": self.resource_key,
            "runner_id": self.runner_id,
            "replica_count": self.replica_count,
            "devices_per_replica": self.devices_per_replica,
        }


class _FakeServicePlanner:
    def __init__(self, *, refusal: bool = False) -> None:
        self.refusal = refusal
        self.calls: list[tuple[UniversalTaskRequest, ExecutionTarget]] = []

    def plan(
        self,
        request: UniversalTaskRequest,
        target: ExecutionTarget,
    ) -> _FakeServicePlan:
        self.calls.append((request, target))
        if self.refusal:
            raise UniversalModelServicePlanningError(("insufficient_memory",))
        return _FakeServicePlan()


@dataclass(frozen=True, slots=True)
class _MalformedServicePlan:
    resource_key: str = "azure:pool:0"
    runner_id: str = "vllm-observed"
    replica_count: object = 1
    devices_per_replica: object = 1
    payload_fails: bool = False

    def to_dict(self) -> dict[str, object]:
        if self.payload_fails:
            raise ValueError("malformed payload")
        return {"resource_key": self.resource_key}


class _ControlledServicePlanner:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome

    def plan(
        self,
        request: UniversalTaskRequest,
        target: ExecutionTarget,
    ) -> object:
        del request, target
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class _Gateway:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: object,
    ) -> SimpleNamespace:
        del profile_id, messages
        self.kwargs = kwargs
        return SimpleNamespace(content='{"candidate": true}', cost_estimate=0.25)


class _Scheduler:
    def __init__(self) -> None:
        self.resources: frozenset[str] = frozenset()

    def plan(self, items: list[WorkItem]) -> list[list[str]]:
        item = items[0]
        self.resources = cast(frozenset[str], item.resources)
        return [[str(item.id)]]


class _Accelerators:
    def discover_hardware(self) -> tuple[SimpleNamespace, ...]:
        return (
            SimpleNamespace(
                sku="observed-gpu",
                provider="azure",
                approved=True,
            ),
        )


class _Adapter:
    def __init__(self, capability: str) -> None:
        self.capability = capability

    def build_messages(
        self,
        request: UniversalTaskRequest,
        target: ExecutionTarget,
    ) -> list[dict[str, str]]:
        del target
        return [{"role": "user", "content": request.instruction}]

    def preflight(
        self,
        request: UniversalTaskRequest,
        target: ExecutionTarget,
    ) -> AdapterDecision:
        del request, target
        return AdapterDecision(True, evidence={"preflight": "passed"})

    def parse_candidate(self, content: str) -> object:
        return content

    def assess_candidate(
        self,
        request: UniversalTaskRequest,
        candidate: object,
        tool_runner: object | None,
    ) -> CandidateAssessment:
        del request, candidate, tool_runner
        return CandidateAssessment(True, evidence={"assessment": "passed"})


@pytest.mark.parametrize(
    "capability",
    ["chemistry.design", "embedded.firmware", "self_improve.proposal"],
)
def test_every_capability_uses_the_same_model_service_task_path(
    capability: str,
) -> None:
    gateway = _Gateway()
    scheduler = _Scheduler()
    service_planner = _FakeServicePlanner()
    target = _target(capability)
    executor = UniversalTaskExecutor(
        gateway=gateway,
        scheduler=scheduler,
        accelerator_planner=_Accelerators(),
        target_source=lambda: (target,),
        model_service_planner=service_planner,
    )

    result = executor.execute(_request(capability), _Adapter(capability))

    assert result.status is TaskStatus.SUCCEEDED
    assert len(service_planner.calls) == 1
    assert gateway.kwargs["model_service_plan"] == _FakeServicePlan().to_dict()
    assert scheduler.resources == frozenset(
        {
            "workspace:task",
            "accelerator:observed-gpu",
            "accelerator-resource:azure:pool:0",
            "model-runner:vllm-observed",
        }
    )
    assert result.evidence["model_service"] == _FakeServicePlan().to_dict()


def test_workload_fails_closed_when_planner_is_absent_or_refuses() -> None:
    target = _target("chemistry.design")
    without_planner = UniversalTaskExecutor(
        gateway=_Gateway(),
        scheduler=_Scheduler(),
        accelerator_planner=_Accelerators(),
        target_source=lambda: (target,),
    )

    absent = without_planner.execute(
        _request("chemistry.design"),
        _Adapter("chemistry.design"),
    )

    assert absent.status is TaskStatus.REFUSED
    assert absent.reasons == ("model_service_planner_unavailable",)

    refusing_planner = _FakeServicePlanner(refusal=True)
    refusing = UniversalTaskExecutor(
        gateway=_Gateway(),
        scheduler=_Scheduler(),
        accelerator_planner=_Accelerators(),
        target_source=lambda: (target,),
        model_service_planner=refusing_planner,
    )
    refused = refusing.execute(
        _request("chemistry.design"),
        _Adapter("chemistry.design"),
    )

    assert refused.status is TaskStatus.REFUSED
    assert refused.reasons == ("model_service:insufficient_memory",)


def test_tasks_without_typed_workload_preserve_existing_gateway_path() -> None:
    gateway = _Gateway()
    planner = _FakeServicePlanner()
    target = _target("universal.text")
    executor = UniversalTaskExecutor(
        gateway=gateway,
        scheduler=_Scheduler(),
        accelerator_planner=_Accelerators(),
        target_source=lambda: (target,),
        model_service_planner=planner,
    )

    result = executor.execute(
        _request("universal.text", with_workload=False),
        _Adapter("universal.text"),
    )

    assert result.status is TaskStatus.SUCCEEDED
    assert planner.calls == []
    assert "model_service_plan" not in gateway.kwargs
    assert "model_service" not in result.evidence


@pytest.mark.parametrize(
    ("outcome", "status", "reason"),
    [
        (RuntimeError("planner crashed"), TaskStatus.FAILED, "model_service_planner_failed:RuntimeError"),
        (object(), TaskStatus.REFUSED, "invalid_model_service_plan"),
        (
            _MalformedServicePlan(resource_key=""),
            TaskStatus.REFUSED,
            "invalid_model_service_plan",
        ),
        (
            _MalformedServicePlan(runner_id=""),
            TaskStatus.REFUSED,
            "invalid_model_service_plan",
        ),
        (
            _MalformedServicePlan(replica_count=True),
            TaskStatus.REFUSED,
            "invalid_model_service_plan",
        ),
        (
            _MalformedServicePlan(devices_per_replica=0),
            TaskStatus.REFUSED,
            "invalid_model_service_plan",
        ),
        (
            _MalformedServicePlan(payload_fails=True),
            TaskStatus.REFUSED,
            "invalid_model_service_plan",
        ),
        (ValueError("refused"), TaskStatus.REFUSED, "model_service_plan_refused"),
    ],
)
def test_executor_fails_closed_on_broken_or_malformed_service_plans(
    outcome: object,
    status: TaskStatus,
    reason: str,
) -> None:
    target = _target("chemistry.design")
    executor = UniversalTaskExecutor(
        gateway=_Gateway(),
        scheduler=_Scheduler(),
        accelerator_planner=_Accelerators(),
        target_source=lambda: (target,),
        model_service_planner=cast(
            ModelServicePlannerProtocol,
            _ControlledServicePlanner(outcome),
        ),
    )

    result = executor.execute(
        _request("chemistry.design"),
        _Adapter("chemistry.design"),
    )

    assert result.status is status
    assert result.reasons == (reason,)


def test_executor_discards_non_tuple_or_unsafe_planner_reasons() -> None:
    error = ValueError("refused")
    error.reason_codes = ["do-not-trust"]  # type: ignore[attr-defined]
    target = _target("chemistry.design")
    executor = UniversalTaskExecutor(
        gateway=_Gateway(),
        scheduler=_Scheduler(),
        accelerator_planner=_Accelerators(),
        target_source=lambda: (target,),
        model_service_planner=cast(
            ModelServicePlannerProtocol,
            _ControlledServicePlanner(error),
        ),
    )

    result = executor.execute(
        _request("chemistry.design"),
        _Adapter("chemistry.design"),
    )

    assert result.reasons == ("model_service_plan_refused",)


def test_model_service_protocols_are_runtime_checkable() -> None:
    assert isinstance(_FakeServicePlan(), ModelServicePlanProtocol)
    assert isinstance(_FakeServicePlanner(), ModelServicePlannerProtocol)


def test_task_and_target_reject_untyped_model_service_fields() -> None:
    with pytest.raises(ValueError, match="model_workload"):
        replace(
            _request("chemistry.design"),
            model_workload=cast(InferenceWorkloadDemand, object()),
        )
    with pytest.raises(ValueError, match="model_runner_id"):
        replace(_target("chemistry.design"), model_runner_id="")
    with pytest.raises(ValueError, match="model_runner_id"):
        replace(
            _target("chemistry.design"),
            model_runner_id=cast(str, object()),
        )


@dataclass(frozen=True, slots=True)
class _Resource:
    kind: str = "gpu"
    location: str = "cloud"
    backend: str = "cuda"
    model: str = "observed-gpu"
    vendor: str = "observed-vendor"
    resource_key: str = "azure:pool:0"
    total_count: int = 4
    available_count: int = 4
    memory_gb: float | None = 24.0
    source: str = "provider-inventory"
    node: str | None = None
    partitions: tuple[str, ...] = ()


def _inventory() -> ModelServiceInventorySnapshot:
    resource = _Resource()
    pool = AcceleratorTopology(
        resource=resource,
        provider="azure",
        region="west",
        zone="observed-zone",
        host_count=1,
        devices_per_host=4,
        max_devices_per_workload=4,
        intra_host_interconnect="observed-fabric",
        intra_host_interconnect_group_size=4,
        cross_host_interconnect=None,
        partitioning=PartitioningMode.WHOLE_DEVICE,
        memory_isolated=True,
        cost=AcceleratorCost(100_000, 1),
        facts_attested=True,
    )
    variant = ModelVariantEvidence(
        variant_id="model:revision:q4",
        model_id="owner/model@0123456789abcdef",
        architecture="decoder-transformer",
        quantization="q4_k_m",
        weight_bytes=8 * _GIB,
        runtime_overhead_bytes=_GIB,
        kv_cache_bytes_per_token=0,
        max_context_tokens=16_384,
        tensor_parallel_divisor=4,
        allowed_device_kinds=frozenset({"gpu"}),
        supported_runner_ids=frozenset({"vllm-observed"}),
        quality_millis=925,
        facts_attested=True,
    )
    sizing = RunnerSizingEvidence(
        runner_id="vllm-observed",
        max_batch_size_per_replica=4,
        max_context_tokens=16_384,
        max_output_tokens=4_096,
        max_data_parallel_replicas=4,
        observed_p95_latency_millis=1_000,
        facts_attested=True,
    )
    capability = RunnerCapabilities(
        runner_id="vllm-observed",
        supported_device_kinds=frozenset({"gpu"}),
        supported_backends=frozenset({"cuda"}),
        supported_partitioning=frozenset({PartitioningMode.WHOLE_DEVICE}),
        distribution_mode=DistributionMode.EXPLICIT_PARALLEL,
        supported_model_parallel_sizes=frozenset({1, 2, 4}),
        supported_tensor_parallel_sizes=frozenset({1, 2, 4}),
        supported_pipeline_parallel_sizes=frozenset({1}),
        tensor_parallel_interconnects=frozenset({"observed-fabric"}),
        pipeline_parallel_interconnects=frozenset(),
        cross_host_interconnects=frozenset(),
        max_hosts=1,
        max_data_parallel_replicas=4,
    )
    return ModelServiceInventorySnapshot(
        variants=(variant,),
        runner_sizing=(sizing,),
        pools=(pool,),
        runner_capabilities=(capability,),
        constraints=TopologyConstraints(
            allowed_providers=frozenset({"azure"}),
            allowed_regions=frozenset({"west"}),
            max_hourly_cost_microusd=1_000_000,
            max_total_devices=4,
            memory_reserve_millis=100,
        ),
        launch_profiles=(
            vllm_launch_profile(
                runner_id="vllm-observed",
                source_revision="vllm-observed-test",
                facts_attested=True,
            ),
        ),
    )


def test_real_planner_joins_task_workload_selection_and_launch() -> None:
    planner = UniversalModelServicePlanner(lambda _request, _target: _inventory())

    planned = planner.plan(_request("chemistry.design"), _target("chemistry.design"))

    assert isinstance(planned, UniversalModelServicePlan)
    assert planned.resource_key == "azure:pool:0"
    assert planned.runner_id == "vllm-observed"
    assert planned.replica_count == 1
    assert planned.devices_per_replica == 1
    assert planned.selection.demand.quantization == "q4_k_m"
    assert planned.launch.command[:3] == (
        "vllm",
        "serve",
        "owner/model@0123456789abcdef",
    )
    assert planned.to_dict() == {
        "schema_version": 1,
        "selection": planned.selection.to_dict(),
        "launch": planned.launch.to_dict(),
    }


def _real_plan() -> UniversalModelServicePlan:
    planner = UniversalModelServicePlanner(lambda _request, _target: _inventory())
    return planner.plan(_request("chemistry.design"), _target("chemistry.design"))


@pytest.mark.parametrize(
    ("task_request", "target", "reason"),
    [
        (
            _request("chemistry.design", with_workload=False),
            _target("chemistry.design"),
            "model_workload_missing",
        ),
        (
            _request("chemistry.design"),
            replace(_target("chemistry.design"), model_runner_id=None),
            "model_runner_identity_missing",
        ),
    ],
)
def test_real_planner_requires_typed_workload_and_runner_identity(
    task_request: UniversalTaskRequest,
    target: ExecutionTarget,
    reason: str,
) -> None:
    planner = UniversalModelServicePlanner(lambda _request, _target: _inventory())

    with pytest.raises(UniversalModelServicePlanningError) as caught:
        planner.plan(task_request, target)

    assert caught.value.reason_codes == (reason,)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: replace(
            _inventory(),
            variants=cast(tuple[ModelVariantEvidence, ...], []),
        ),
        lambda: replace(
            _inventory(),
            launch_profiles=_inventory().launch_profiles * 4_097,
        ),
        lambda: replace(
            _inventory(),
            constraints=cast(TopologyConstraints, object()),
        ),
    ],
)
def test_inventory_snapshot_rejects_mutable_unbounded_or_wrong_evidence(
    factory: object,
) -> None:
    with pytest.raises(ValueError):
        factory()  # type: ignore[operator]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: UniversalModelServicePlan(
            cast(ModelServiceSelection, object()),
            _real_plan().launch,
        ),
        lambda: UniversalModelServicePlan(
            _real_plan().selection,
            cast(RunnerLaunchPlan, object()),
        ),
        lambda: UniversalModelServicePlan(
            _real_plan().selection,
            replace(_real_plan().launch, runner_id="different-runner"),
        ),
        lambda: UniversalModelServicePlan(
            _real_plan().selection,
            replace(_real_plan().launch, replica_count=2),
        ),
        lambda: UniversalModelServicePlan(
            _real_plan().selection,
            replace(_real_plan().launch, devices_per_replica=2),
        ),
    ],
)
def test_universal_plan_rejects_inconsistent_selection_and_launch(
    factory: object,
) -> None:
    with pytest.raises(ValueError):
        factory()  # type: ignore[operator]


@pytest.mark.parametrize(
    "reasons",
    [
        cast(tuple[str, ...], []),
        ("",),
        ("bad\nreason",),
        ("x" * 129,),
    ],
)
def test_planning_error_rejects_mutable_or_unbounded_reasons(
    reasons: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError):
        UniversalModelServicePlanningError(reasons)


def test_planning_error_defaults_deduplicates_and_sorts() -> None:
    assert UniversalModelServicePlanningError(()).reason_codes == (
        "model_service_planning_refused",
    )
    assert UniversalModelServicePlanningError(("z", "a", "z")).reason_codes == (
        "a",
        "z",
    )


def test_planner_rejects_wrong_dependencies_and_contract_types() -> None:
    with pytest.raises(ValueError, match="callable"):
        UniversalModelServicePlanner(cast(object, object()))  # type: ignore[arg-type]

    planner = UniversalModelServicePlanner(lambda _request, _target: _inventory())
    with pytest.raises(ValueError, match="request"):
        planner.plan(cast(UniversalTaskRequest, object()), _target("chemistry.design"))
    with pytest.raises(ValueError, match="target"):
        planner.plan(
            _request("chemistry.design"),
            cast(ExecutionTarget, object()),
        )

    wrong_snapshot = UniversalModelServicePlanner(
        lambda _request, _target: cast(ModelServiceInventorySnapshot, object())
    )
    with pytest.raises(ValueError, match="inventory_source"):
        wrong_snapshot.plan(
            _request("chemistry.design"),
            _target("chemistry.design"),
        )


@pytest.mark.parametrize(
    ("snapshot", "reason"),
    [
        (
            replace(_inventory(), launch_profiles=()),
            "launch_profile_missing",
        ),
        (
            replace(
                _inventory(),
                launch_profiles=_inventory().launch_profiles * 2,
            ),
            "launch_profile_ambiguous",
        ),
        (
            replace(_inventory(), variants=()),
            "empty_variant_inventory",
        ),
        (
            replace(
                _inventory(),
                launch_profiles=(
                    replace(
                        _inventory().launch_profiles[0],
                        facts_attested=False,
                    ),
                ),
            ),
            "launch_profile_unattested",
        ),
    ],
)
def test_planner_preserves_profile_selection_and_launch_refusals(
    snapshot: ModelServiceInventorySnapshot,
    reason: str,
) -> None:
    planner = UniversalModelServicePlanner(lambda _request, _target: snapshot)

    with pytest.raises(UniversalModelServicePlanningError) as caught:
        planner.plan(_request("chemistry.design"), _target("chemistry.design"))

    assert caught.value.reason_codes == (reason,)


def test_planner_filters_unrelated_profiles_runner_evidence_and_providers() -> None:
    inventory = _inventory()
    unrelated_profile = replace(
        inventory.launch_profiles[0],
        runner_id="unrelated-runner",
    )
    unrelated_sizing = replace(
        inventory.runner_sizing[0],
        runner_id="unrelated-runner",
    )
    unrelated_capability = replace(
        inventory.runner_capabilities[0],
        runner_id="unrelated-runner",
    )
    unrelated_pool = replace(inventory.pools[0], provider="unrelated-provider")
    snapshot = replace(
        inventory,
        launch_profiles=(unrelated_profile, inventory.launch_profiles[0]),
        runner_sizing=(unrelated_sizing, inventory.runner_sizing[0]),
        runner_capabilities=(
            unrelated_capability,
            inventory.runner_capabilities[0],
        ),
        pools=(unrelated_pool, inventory.pools[0]),
    )
    planner = UniversalModelServicePlanner(lambda _request, _target: snapshot)

    planned = planner.plan(_request("chemistry.design"), _target("chemistry.design"))

    assert planned.runner_id == "vllm-observed"
    assert planned.selection.topology.provider == "azure"
