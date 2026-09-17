"""Integration proof from Azure SDK-shaped inventory to one VM topology plan."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from general_ludd.hardware.accelerator_topology import (
    AcceleratorCost,
    AcceleratorTopology,
    DistributionMode,
    ModelRunnerDemand,
    PartitioningMode,
    RunnerCapabilities,
    TopologyConstraints,
)
from general_ludd.hardware.accelerator_types import (
    AcceleratorKind,
    AcceleratorLocation,
    AcceleratorResource,
)
from general_ludd.infra.azure_gpu_vm_strategy import (
    AzureExecutionOption,
    AzureExecutionStrategy,
    AzureGpuVmInventory,
    AzureStrategyTrace,
    select_azure_execution_strategy,
)
from general_ludd.infra.azure_retail_pricing import AzureRetailMeter
from general_ludd.self_improve.azure_operational_availability import (
    AzureAvailabilityAssessment,
    AzureAvailabilityIndex,
    AzureAvailabilityScope,
)

_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
_REGION = "westus3"
_SKU = "Standard_UnseenAccelerator_4"
_RUNTIME = "sha256:" + "c" * 64
_TOPOLOGY = "d" * 64


class _ListOperation:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def list(self, _location: str | None = None) -> list[object]:
        return self._values


class _ComputeClient:
    def __init__(self) -> None:
        capabilities = [
            SimpleNamespace(name="GPUs", value="4"),
            SimpleNamespace(name="GpuMemoryGB", value="96"),
            SimpleNamespace(name="vCPUs", value="48"),
            SimpleNamespace(name="GpuBackend", value="cuda"),
            SimpleNamespace(name="GpuVendor", value="inventory-vendor"),
            SimpleNamespace(name="IntraHostGpuInterconnect", value="mesh-v2"),
            SimpleNamespace(name="IntraHostGpuInterconnectGroupSize", value="4"),
        ]
        sku = SimpleNamespace(
            name=_SKU,
            resource_type="virtualMachines",
            locations=[_REGION],
            location_info=[SimpleNamespace(location=_REGION, zones=["1", "2"])],
            restrictions=[],
            capabilities=capabilities,
            family="standardUnseenAcceleratorFamily",
        )
        usages = [
            SimpleNamespace(
                name=SimpleNamespace(
                    value="standardUnseenAcceleratorFamily",
                    localized_value=None,
                ),
                current_value=0,
                limit=96,
            ),
            SimpleNamespace(
                name=SimpleNamespace(
                    value="Total Regional vCPUs",
                    localized_value=None,
                ),
                current_value=0,
                limit=500,
            ),
        ]
        self.resource_skus = _ListOperation([sku])
        self.usage = _ListOperation(usages)


class _Pricing:
    def resolve_virtual_machine_arm_sku_meter(
        self,
        *,
        region: str,
        arm_sku_name: str,
    ) -> AzureRetailMeter:
        assert (region, arm_sku_name) == (_REGION, _SKU)
        return AzureRetailMeter(
            region=region,
            sku_name=arm_sku_name,
            price_type="Consumption",
            meter_id="opaque-meter-id",
            meter_name="on-demand-linux",
            retail_price=8.0,
            unit_of_measure="1 Hour",
            effective_start_date=_NOW - timedelta(days=1),
            fetched_at=_NOW,
        )


def _assessment(scope: AzureAvailabilityScope) -> AzureAvailabilityAssessment:
    return AzureAvailabilityAssessment(
        scope_digest=scope.scope_digest,
        observed_outcomes=2,
        successful_outcomes=2,
        failed_outcomes=0,
        consecutive_failures=0,
        availability_score=0.75,
        feasible=True,
        last_observed_at=_NOW.timestamp() - 30,
    )


def _runner(sizes: frozenset[int]) -> RunnerCapabilities:
    return RunnerCapabilities(
        runner_id="runtime-attestation",
        supported_device_kinds=frozenset({"gpu"}),
        supported_backends=frozenset({"cuda"}),
        supported_partitioning=frozenset({PartitioningMode.WHOLE_DEVICE}),
        distribution_mode=DistributionMode.EXPLICIT_PARALLEL,
        supported_model_parallel_sizes=sizes,
        supported_tensor_parallel_sizes=sizes,
        supported_pipeline_parallel_sizes=frozenset({1}),
        tensor_parallel_interconnects=frozenset({"mesh-v2"}),
        pipeline_parallel_interconnects=frozenset(),
        cross_host_interconnects=frozenset(),
        max_hosts=1,
        max_data_parallel_replicas=1,
    )


def test_live_shaped_inventory_routes_host_local_multi_gpu_to_one_vm() -> None:
    vm_scope = AzureAvailabilityScope(
        location=_REGION,
        resource_sku=_SKU,
        runtime_version_digest=_RUNTIME,
        topology_digest=_TOPOLOGY,
    )
    vm_assessment = _assessment(vm_scope)
    inventory = AzureGpuVmInventory(
        compute_client=_ComputeClient(),
        price_resolver=_Pricing(),
        availability_index=AzureAvailabilityIndex((vm_assessment,)),
        clock=lambda: _NOW,
    )
    traces: list[AzureStrategyTrace] = []
    snapshot = inventory.discover(
        region=_REGION,
        runtime_version_digest=_RUNTIME,
        topology_digest=_TOPOLOGY,
        trace_sink=traces.append,
    )
    vm = snapshot.candidates[0]
    vm_option = AzureExecutionOption(
        strategy=AzureExecutionStrategy.SINGLE_VM,
        topology=vm.topology,
        runner=_runner(frozenset({1, 2, 4})),
        measured_startup_seconds=120,
        measured_throughput_units_per_second=500,
        price_fetched_at=vm.price_fetched_at,
        infrastructure_observed_at=vm.observed_at,
        availability=vm.availability,
        runtime_supported=True,
        privacy_allowed=True,
        operator_approved=True,
        identity_attested=True,
    )
    ca_resource = AcceleratorResource(
        kind=AcceleratorKind.GPU,
        location=AcceleratorLocation.CLOUD,
        backend="cuda",
        model="managed-profile",
        vendor="managed-provider",
        resource_key="container-app-profile",
        total_count=1,
        available_count=1,
        memory_gb=40,
        source="managed-attestation",
    )
    ca_topology = AcceleratorTopology(
        resource=ca_resource,
        provider="azure",
        region=_REGION,
        zone=None,
        host_count=1,
        devices_per_host=1,
        max_devices_per_workload=1,
        intra_host_interconnect=None,
        intra_host_interconnect_group_size=1,
        cross_host_interconnect=None,
        partitioning=PartitioningMode.WHOLE_DEVICE,
        memory_isolated=True,
        cost=AcceleratorCost(1_000_000, 1),
        facts_attested=True,
    )
    ca_option = AzureExecutionOption(
        strategy=AzureExecutionStrategy.CONTAINER_APPS,
        topology=ca_topology,
        runner=_runner(frozenset({1})),
        measured_startup_seconds=5,
        measured_throughput_units_per_second=1000,
        price_fetched_at=_NOW,
        infrastructure_observed_at=_NOW,
        availability=vm_assessment,
        runtime_supported=True,
        privacy_allowed=True,
        operator_approved=True,
        identity_attested=True,
    )
    demand = ModelRunnerDemand(
        model_id="never-in-trace",
        allowed_device_kinds=frozenset({"gpu"}),
        weight_bytes=120 * 1024**3,
        runtime_overhead_bytes=2 * 1024**3,
        kv_cache_bytes_per_token=1024,
        context_tokens=4096,
        concurrent_sequences=1,
        data_parallel_replicas=1,
        tensor_parallel_divisor=4,
    )

    decision = select_azure_execution_strategy(
        demand=demand,
        options=(ca_option, vm_option),
        constraints=TopologyConstraints(
            allowed_providers=frozenset({"azure"}),
            allowed_regions=frozenset({_REGION}),
            max_hourly_cost_microusd=10_000_000,
            max_total_devices=4,
        ),
        work_units=50_000,
        now=_NOW,
        trace_sink=traces.append,
    )

    assert decision.strategy is AzureExecutionStrategy.SINGLE_VM
    assert decision.topology_plan.resource_key == f"azure:{_REGION}:{_SKU}"
    assert decision.topology_plan.host_count == 1
    assert decision.topology_plan.model_parallel_devices in {2, 4}
    assert "never-in-trace" not in repr(traces)
    assert _SKU not in repr(traces)
