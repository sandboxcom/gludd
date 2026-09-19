"""End-to-end model-service planning over canonical accelerator inventory."""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from general_ludd.execution.model_service_planner import (
    ModelServiceInventorySnapshot,
    UniversalModelServicePlanner,
    UniversalModelServicePlanningError,
)
from general_ludd.execution.universal_task import (
    ExecutionTarget,
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
from general_ludd.hardware.accelerator_types import (
    AcceleratorKind,
    AcceleratorLocation,
    AcceleratorResource,
)
from general_ludd.hardware.model_runner_launch import (
    LaunchTarget,
    LaunchValue,
    RunnerLaunchBinding,
    RunnerLaunchProfile,
    llama_cpp_launch_profile,
    ollama_launch_profile,
    vllm_launch_profile,
)
from general_ludd.hardware.model_service_rightsizing import (
    InferenceWorkloadDemand,
    ModelVariantEvidence,
    RunnerSizingEvidence,
)

_GIB = 1024**3


@dataclass(frozen=True, slots=True)
class _HardwareCase:
    case_id: str
    provider: str
    region: str
    kind: AcceleratorKind | str
    location: AcceleratorLocation
    backend: str
    vendor: str
    model: str
    total_count: int
    available_count: int
    memory_gb: float
    host_count: int
    devices_per_host: int
    max_devices_per_workload: int
    intra_host_interconnect: str
    intra_host_group: int
    cross_host_interconnect: str | None
    partitioning: PartitioningMode
    partitions: tuple[str, ...]
    distribution: DistributionMode
    expected_devices: int
    weight_gib: int
    profile_kind: str
    tensor_size: int | None = None
    pipeline_size: int | None = None


_CASES = (
    _HardwareCase(
        case_id="local-apple-unified-memory",
        provider="local",
        region="local",
        kind=AcceleratorKind.GPU,
        location=AcceleratorLocation.LOCAL,
        backend="mps",
        vendor="apple",
        model="observed-apple-gpu",
        total_count=1,
        available_count=1,
        memory_gb=32.0,
        host_count=1,
        devices_per_host=1,
        max_devices_per_workload=1,
        intra_host_interconnect="unified-memory",
        intra_host_group=1,
        cross_host_interconnect=None,
        partitioning=PartitioningMode.WHOLE_DEVICE,
        partitions=(),
        distribution=DistributionMode.RUNNER_MANAGED,
        expected_devices=1,
        weight_gib=8,
        profile_kind="llama-cpp",
    ),
    _HardwareCase(
        case_id="local-intel-xpu",
        provider="local",
        region="local",
        kind=AcceleratorKind.GPU,
        location=AcceleratorLocation.LOCAL,
        backend="xpu",
        vendor="intel",
        model="observed-intel-xpu",
        total_count=1,
        available_count=1,
        memory_gb=16.0,
        host_count=1,
        devices_per_host=1,
        max_devices_per_workload=1,
        intra_host_interconnect="none",
        intra_host_group=1,
        cross_host_interconnect=None,
        partitioning=PartitioningMode.WHOLE_DEVICE,
        partitions=(),
        distribution=DistributionMode.RUNNER_MANAGED,
        expected_devices=1,
        weight_gib=8,
        profile_kind="compatible",
    ),
    _HardwareCase(
        case_id="azure-container-apps-single-nvidia-gpu",
        provider="azure",
        region="eastus",
        kind=AcceleratorKind.GPU,
        location=AcceleratorLocation.CLOUD,
        backend="cuda",
        vendor="nvidia",
        model="observed-container-apps-gpu",
        total_count=4,
        available_count=4,
        memory_gb=24.0,
        host_count=4,
        devices_per_host=1,
        max_devices_per_workload=1,
        intra_host_interconnect="none",
        intra_host_group=1,
        cross_host_interconnect=None,
        partitioning=PartitioningMode.WHOLE_DEVICE,
        partitions=(),
        distribution=DistributionMode.EXPLICIT_PARALLEL,
        expected_devices=1,
        weight_gib=8,
        profile_kind="vllm",
        tensor_size=1,
        pipeline_size=1,
    ),
    _HardwareCase(
        case_id="azure-vm-multi-nvidia-gpu",
        provider="azure",
        region="eastus",
        kind=AcceleratorKind.GPU,
        location=AcceleratorLocation.CLOUD,
        backend="cuda",
        vendor="nvidia",
        model="observed-azure-vm-gpu",
        total_count=8,
        available_count=8,
        memory_gb=24.0,
        host_count=1,
        devices_per_host=8,
        max_devices_per_workload=8,
        intra_host_interconnect="observed-nvlink",
        intra_host_group=8,
        cross_host_interconnect=None,
        partitioning=PartitioningMode.WHOLE_DEVICE,
        partitions=(),
        distribution=DistributionMode.EXPLICIT_PARALLEL,
        expected_devices=4,
        weight_gib=70,
        profile_kind="vllm",
        tensor_size=4,
        pipeline_size=1,
    ),
    _HardwareCase(
        case_id="azure-vm-multi-amd-gpu",
        provider="azure",
        region="westus",
        kind=AcceleratorKind.GPU,
        location=AcceleratorLocation.CLOUD,
        backend="rocm",
        vendor="amd",
        model="observed-azure-amd-gpu",
        total_count=4,
        available_count=4,
        memory_gb=24.0,
        host_count=1,
        devices_per_host=4,
        max_devices_per_workload=4,
        intra_host_interconnect="observed-infinity-fabric",
        intra_host_group=4,
        cross_host_interconnect=None,
        partitioning=PartitioningMode.WHOLE_DEVICE,
        partitions=(),
        distribution=DistributionMode.RUNNER_MANAGED,
        expected_devices=2,
        weight_gib=36,
        profile_kind="ollama",
    ),
    _HardwareCase(
        case_id="cloud-hardware-partition",
        provider="cloud-provider",
        region="region-a",
        kind=AcceleratorKind.GPU,
        location=AcceleratorLocation.CLOUD,
        backend="rocm",
        vendor="amd",
        model="observed-partition",
        total_count=2,
        available_count=2,
        memory_gb=12.0,
        host_count=1,
        devices_per_host=2,
        max_devices_per_workload=2,
        intra_host_interconnect="partition-fabric",
        intra_host_group=2,
        cross_host_interconnect=None,
        partitioning=PartitioningMode.HARDWARE_PARTITION,
        partitions=("partition-0", "partition-1"),
        distribution=DistributionMode.RUNNER_MANAGED,
        expected_devices=1,
        weight_gib=8,
        profile_kind="ollama",
    ),
    _HardwareCase(
        case_id="gcp-tpu-slice",
        provider="gcp",
        region="central",
        kind=AcceleratorKind.TPU,
        location=AcceleratorLocation.CLOUD,
        backend="jax",
        vendor="google",
        model="observed-tpu-version",
        total_count=4,
        available_count=4,
        memory_gb=32.0,
        host_count=1,
        devices_per_host=4,
        max_devices_per_workload=4,
        intra_host_interconnect="observed-tpu-mesh",
        intra_host_group=4,
        cross_host_interconnect=None,
        partitioning=PartitioningMode.WHOLE_DEVICE,
        partitions=("slice-observed",),
        distribution=DistributionMode.RUNNER_MANAGED,
        expected_devices=4,
        weight_gib=60,
        profile_kind="compatible",
    ),
    _HardwareCase(
        case_id="future-fpga-cloud",
        provider="future-provider",
        region="future-region",
        kind="fpga",
        location=AcceleratorLocation.CLOUD,
        backend="future-runtime",
        vendor="future-vendor",
        model="observed-future-device",
        total_count=1,
        available_count=1,
        memory_gb=8.0,
        host_count=1,
        devices_per_host=1,
        max_devices_per_workload=1,
        intra_host_interconnect="none",
        intra_host_group=1,
        cross_host_interconnect=None,
        partitioning=PartitioningMode.WHOLE_DEVICE,
        partitions=(),
        distribution=DistributionMode.SINGLE_DEVICE,
        expected_devices=1,
        weight_gib=4,
        profile_kind="compatible",
    ),
    _HardwareCase(
        case_id="slurm-multi-host-nvidia",
        provider="slurm",
        region="cluster-a",
        kind=AcceleratorKind.GPU,
        location=AcceleratorLocation.SLURM,
        backend="cuda",
        vendor="nvidia",
        model="observed-slurm-gpu",
        total_count=8,
        available_count=8,
        memory_gb=24.0,
        host_count=2,
        devices_per_host=4,
        max_devices_per_workload=8,
        intra_host_interconnect="node-fabric",
        intra_host_group=4,
        cross_host_interconnect="cluster-fabric",
        partitioning=PartitioningMode.WHOLE_DEVICE,
        partitions=("accelerated",),
        distribution=DistributionMode.EXPLICIT_PARALLEL,
        expected_devices=8,
        weight_gib=140,
        profile_kind="vllm",
        tensor_size=4,
        pipeline_size=2,
    ),
)


def _kind_text(kind: AcceleratorKind | str) -> str:
    return kind.value if isinstance(kind, AcceleratorKind) else kind


def _resource(case: _HardwareCase) -> AcceleratorResource:
    return AcceleratorResource(
        kind=case.kind,
        location=case.location,
        backend=case.backend,
        model=case.model,
        vendor=case.vendor,
        resource_key=f"{case.provider}:{case.case_id}",
        total_count=case.total_count,
        available_count=case.available_count,
        memory_gb=case.memory_gb,
        source="canonical-integration-inventory",
        node="observed-node" if case.location is AcceleratorLocation.SLURM else None,
        partitions=case.partitions,
    )


def _pool(case: _HardwareCase, resource: AcceleratorResource) -> AcceleratorTopology:
    return AcceleratorTopology(
        resource=resource,
        provider=case.provider,
        region=case.region,
        zone="observed-zone",
        host_count=case.host_count,
        devices_per_host=case.devices_per_host,
        max_devices_per_workload=case.max_devices_per_workload,
        intra_host_interconnect=case.intra_host_interconnect,
        intra_host_interconnect_group_size=case.intra_host_group,
        cross_host_interconnect=case.cross_host_interconnect,
        partitioning=case.partitioning,
        memory_isolated=True,
        cost=AcceleratorCost(
            hourly_microusd_per_unit=100_000,
            devices_per_unit=1,
        ),
        facts_attested=True,
    )


def _runner(case: _HardwareCase, runner_id: str) -> RunnerCapabilities:
    if case.distribution is DistributionMode.EXPLICIT_PARALLEL:
        tensor_sizes = frozenset({case.tensor_size or 1})
        pipeline_sizes = frozenset({case.pipeline_size or 1})
        tensor_links = frozenset({case.intra_host_interconnect})
        pipeline_links = frozenset(
            {case.cross_host_interconnect}
            if case.cross_host_interconnect is not None
            else {case.intra_host_interconnect}
        )
        cross_host_links = frozenset(
            {case.cross_host_interconnect}
            if case.cross_host_interconnect is not None
            else set()
        )
    else:
        tensor_sizes = frozenset({1})
        pipeline_sizes = frozenset({1})
        tensor_links = frozenset()
        pipeline_links = frozenset()
        cross_host_links = frozenset()
    return RunnerCapabilities(
        runner_id=runner_id,
        supported_device_kinds=frozenset({_kind_text(case.kind)}),
        supported_backends=frozenset({case.backend}),
        supported_partitioning=frozenset({case.partitioning}),
        distribution_mode=case.distribution,
        supported_model_parallel_sizes=frozenset({case.expected_devices}),
        supported_tensor_parallel_sizes=tensor_sizes,
        supported_pipeline_parallel_sizes=pipeline_sizes,
        tensor_parallel_interconnects=tensor_links,
        pipeline_parallel_interconnects=pipeline_links,
        cross_host_interconnects=cross_host_links,
        max_hosts=case.host_count,
        max_data_parallel_replicas=4,
    )


def _compatible_profile(
    case: _HardwareCase,
    runner_id: str,
) -> RunnerLaunchProfile:
    return RunnerLaunchProfile(
        runner_id=runner_id,
        adapter_id="compatible-runner",
        source_revision=f"{case.case_id}-observed-contract",
        executable=("compatible-runner", "serve"),
        fixed_arguments=(),
        bindings=(
            RunnerLaunchBinding(
                LaunchValue.MODEL_ID,
                LaunchTarget.POSITIONAL_ARGUMENT,
            ),
            RunnerLaunchBinding(
                LaunchValue.CONTEXT_TOKENS,
                LaunchTarget.ARGUMENT,
                "--context",
            ),
            RunnerLaunchBinding(
                LaunchValue.MODEL_PARALLEL_DEVICES,
                LaunchTarget.ARGUMENT,
                "--devices",
            ),
            RunnerLaunchBinding(
                LaunchValue.DEVICE_KIND,
                LaunchTarget.ENVIRONMENT,
                "ACCELERATOR_KIND",
            ),
            RunnerLaunchBinding(
                LaunchValue.OUTPUT_TOKENS,
                LaunchTarget.REQUEST_OPTION,
                "max_output",
            ),
        ),
        distribution_mode=case.distribution,
        facts_attested=True,
    )


def _profile(case: _HardwareCase, runner_id: str) -> RunnerLaunchProfile:
    common = {
        "runner_id": runner_id,
        "source_revision": f"{case.case_id}-observed-contract",
        "facts_attested": True,
    }
    if case.profile_kind == "vllm":
        return vllm_launch_profile(**common)
    if case.profile_kind == "llama-cpp":
        return llama_cpp_launch_profile(**common)
    if case.profile_kind == "ollama":
        return ollama_launch_profile(**common)
    return _compatible_profile(case, runner_id)


def _snapshot(case: _HardwareCase) -> ModelServiceInventorySnapshot:
    runner_id = f"runner:{case.case_id}"
    resource = _resource(case)
    return ModelServiceInventorySnapshot(
        variants=(
            ModelVariantEvidence(
                variant_id=f"model:{case.case_id}:q4",
                model_id=f"catalog/{case.case_id}@immutable-revision",
                architecture="decoder-transformer",
                quantization="q4_k_m",
                weight_bytes=case.weight_gib * _GIB,
                runtime_overhead_bytes=_GIB,
                kv_cache_bytes_per_token=0,
                max_context_tokens=16_384,
                tensor_parallel_divisor=8,
                allowed_device_kinds=frozenset({_kind_text(case.kind)}),
                supported_runner_ids=frozenset({runner_id}),
                quality_millis=925,
                facts_attested=True,
            ),
        ),
        runner_sizing=(
            RunnerSizingEvidence(
                runner_id=runner_id,
                max_batch_size_per_replica=2,
                max_context_tokens=16_384,
                max_output_tokens=4_096,
                max_data_parallel_replicas=4,
                observed_p95_latency_millis=1_000,
                facts_attested=True,
            ),
        ),
        pools=(_pool(case, resource),),
        runner_capabilities=(_runner(case, runner_id),),
        constraints=TopologyConstraints(
            allowed_providers=frozenset({case.provider}),
            allowed_regions=frozenset({case.region}),
            max_hourly_cost_microusd=10_000_000,
            max_total_devices=case.total_count,
            memory_reserve_millis=100,
        ),
        launch_profiles=(_profile(case, runner_id),),
    )


def _request(case: _HardwareCase) -> UniversalTaskRequest:
    return UniversalTaskRequest(
        task_id=f"task:{case.case_id}",
        capability="universal.generate",
        instruction="Produce a bounded candidate.",
        budget_usd=10.0,
        model_workload=InferenceWorkloadDemand(
            workload_id=f"workload:{case.case_id}",
            input_tokens=1_024,
            output_tokens=512,
            concurrent_sequences=2,
            minimum_quality_millis=900,
            max_p95_latency_millis=2_000,
        ),
    )


def _target(case: _HardwareCase) -> ExecutionTarget:
    return ExecutionTarget(
        profile_id=f"gateway:{case.case_id}",
        provider=case.provider,
        accelerator_sku=case.model,
        capabilities=frozenset({"universal.generate"}),
        allowed_data_classifications=frozenset({"public"}),
        estimated_cost_usd=0.25,
        healthy=True,
        health_evidence="observed-healthy",
        capability_evidence="observed-task-quality",
        cost_evidence="observed-provider-price",
        privacy_evidence="public-data-policy",
        offline=case.location is AcceleratorLocation.LOCAL,
        model_runner_id=f"runner:{case.case_id}",
    )


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.case_id)
def test_canonical_inventory_reaches_selection_and_launch_without_sku_keys(
    case: _HardwareCase,
) -> None:
    snapshot = _snapshot(case)
    planner = UniversalModelServicePlanner(lambda _request, _target: snapshot)

    planned = planner.plan(_request(case), _target(case))

    resource = snapshot.pools[0].resource
    assert isinstance(resource, AcceleratorResource)
    assert resource.location is case.location
    assert resource.available_count == case.available_count
    assert resource.partitions == case.partitions
    assert planned.resource_key == resource.resource_key
    assert planned.selection.topology.device_kind == _kind_text(case.kind)
    assert planned.selection.topology.device_vendor == case.vendor
    assert planned.selection.topology.device_model == case.model
    assert planned.selection.topology.partitioning is case.partitioning
    assert planned.selection.topology.model_parallel_devices == case.expected_devices
    assert planned.selection.topology.tensor_parallel_size == case.tensor_size
    assert planned.selection.topology.pipeline_parallel_size == case.pipeline_size
    assert planned.launch.devices_per_replica == case.expected_devices
    assert planned.launch.distribution_mode is case.distribution
    assert case.vendor not in planned.launch.command
    assert case.model not in planned.launch.command


def test_current_allocation_reduction_fails_closed_end_to_end() -> None:
    case = next(item for item in _CASES if item.case_id == "azure-vm-multi-nvidia-gpu")
    snapshot = _snapshot(case)
    unavailable_resource = replace(snapshot.pools[0].resource, available_count=2)
    unavailable_pool = replace(snapshot.pools[0], resource=unavailable_resource)
    unavailable = replace(snapshot, pools=(unavailable_pool,))
    planner = UniversalModelServicePlanner(lambda _request, _target: unavailable)

    with pytest.raises(UniversalModelServicePlanningError) as caught:
        planner.plan(_request(case), _target(case))

    assert caught.value.reason_codes == ("insufficient_available_devices",)


def test_container_apps_never_claims_multi_gpu_replica_support() -> None:
    case = next(
        item
        for item in _CASES
        if item.case_id == "azure-container-apps-single-nvidia-gpu"
    )
    snapshot = _snapshot(case)
    oversized_variant = replace(
        snapshot.variants[0],
        weight_bytes=30 * _GIB,
    )
    oversized = replace(snapshot, variants=(oversized_variant,))
    planner = UniversalModelServicePlanner(lambda _request, _target: oversized)

    with pytest.raises(UniversalModelServicePlanningError) as caught:
        planner.plan(_request(case), _target(case))

    assert caught.value.reason_codes == ("insufficient_memory",)
    assert oversized.pools[0].max_devices_per_workload == 1
