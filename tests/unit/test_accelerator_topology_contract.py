"""Provider-neutral accelerator topology and model-runner placement tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import cast

import pytest

from general_ludd.hardware.accelerator_topology import (
    AcceleratorCost,
    AcceleratorTopology,
    DistributionMode,
    ModelRunnerDemand,
    PartitioningMode,
    PlanningEvent,
    RunnerCapabilities,
    TopologyConstraints,
    TopologyPlan,
    TopologyPlanningError,
    TopologyTrace,
    plan_model_runner_topology,
)

_GIB = 1024**3


@dataclass(frozen=True, slots=True)
class _Resource:
    """Minimal structural stand-in for S83.159's discovered resource."""

    kind: str = "gpu"
    location: str = "cloud"
    backend: str = "cuda"
    model: str = "observed-device-model"
    vendor: str = "observed-vendor"
    resource_key: str = "dynamic:pool:0"
    total_count: int = 8
    available_count: int = 8
    memory_gb: float | None = 24.0
    source: str = "provider-inventory"
    node: str | None = None
    partitions: tuple[str, ...] = ()


def _pool(
    *,
    resource: _Resource | None = None,
    provider: str = "azure",
    region: str = "west",
    host_count: int = 1,
    devices_per_host: int = 8,
    max_devices_per_workload: int = 8,
    intra_host_interconnect: str = "observed-fabric",
    intra_host_interconnect_group_size: int = 8,
    cross_host_interconnect: str | None = None,
    partitioning: PartitioningMode = PartitioningMode.WHOLE_DEVICE,
    memory_isolated: bool = True,
    cost: int | None = 100_000,
    devices_per_cost_unit: int = 1,
    attested: bool = True,
) -> AcceleratorTopology:
    return AcceleratorTopology(
        resource=resource or _Resource(),
        provider=provider,
        region=region,
        zone="zone-observed",
        host_count=host_count,
        devices_per_host=devices_per_host,
        max_devices_per_workload=max_devices_per_workload,
        intra_host_interconnect=intra_host_interconnect,
        intra_host_interconnect_group_size=intra_host_interconnect_group_size,
        cross_host_interconnect=cross_host_interconnect,
        partitioning=partitioning,
        memory_isolated=memory_isolated,
        cost=(
            AcceleratorCost(
                hourly_microusd_per_unit=cost,
                devices_per_unit=devices_per_cost_unit,
            )
            if cost is not None
            else None
        ),
        facts_attested=attested,
    )


def _runner(
    *,
    runner_id: str = "dynamic-runner",
    kinds: frozenset[str] = frozenset({"gpu"}),
    backends: frozenset[str] = frozenset({"cuda"}),
    partitioning: frozenset[PartitioningMode] = frozenset(
        {PartitioningMode.WHOLE_DEVICE}
    ),
    distribution: DistributionMode = DistributionMode.EXPLICIT_PARALLEL,
    model_sizes: frozenset[int] = frozenset({1, 2, 4, 8}),
    tensor_sizes: frozenset[int] = frozenset({1, 2, 4, 8}),
    pipeline_sizes: frozenset[int] = frozenset({1}),
    tensor_links: frozenset[str] = frozenset({"observed-fabric"}),
    pipeline_links: frozenset[str] = frozenset(),
    cross_host_links: frozenset[str] = frozenset(),
    max_hosts: int = 1,
    max_data_parallel_replicas: int = 16,
) -> RunnerCapabilities:
    return RunnerCapabilities(
        runner_id=runner_id,
        supported_device_kinds=kinds,
        supported_backends=backends,
        supported_partitioning=partitioning,
        distribution_mode=distribution,
        supported_model_parallel_sizes=model_sizes,
        supported_tensor_parallel_sizes=tensor_sizes,
        supported_pipeline_parallel_sizes=pipeline_sizes,
        tensor_parallel_interconnects=tensor_links,
        pipeline_parallel_interconnects=pipeline_links,
        cross_host_interconnects=cross_host_links,
        max_hosts=max_hosts,
        max_data_parallel_replicas=max_data_parallel_replicas,
    )


def _demand(
    *,
    allowed_kinds: frozenset[str] = frozenset({"gpu"}),
    weight_bytes: int = 8 * _GIB,
    runtime_overhead_bytes: int = _GIB,
    kv_cache_bytes_per_token: int = 0,
    context_tokens: int = 2_048,
    concurrent_sequences: int = 1,
    data_parallel_replicas: int = 1,
    tensor_parallel_divisor: int = 8,
    allow_shared_accelerator: bool = False,
) -> ModelRunnerDemand:
    return ModelRunnerDemand(
        model_id="catalog:model@immutable-revision",
        allowed_device_kinds=allowed_kinds,
        weight_bytes=weight_bytes,
        runtime_overhead_bytes=runtime_overhead_bytes,
        kv_cache_bytes_per_token=kv_cache_bytes_per_token,
        context_tokens=context_tokens,
        concurrent_sequences=concurrent_sequences,
        data_parallel_replicas=data_parallel_replicas,
        tensor_parallel_divisor=tensor_parallel_divisor,
        allow_shared_accelerator=allow_shared_accelerator,
    )


def _constraints(
    *,
    providers: frozenset[str] = frozenset({"azure"}),
    regions: frozenset[str] = frozenset({"west"}),
    max_cost: int = 10_000_000,
    max_devices: int = 64,
) -> TopologyConstraints:
    return TopologyConstraints(
        allowed_providers=providers,
        allowed_regions=regions,
        max_hourly_cost_microusd=max_cost,
        max_total_devices=max_devices,
        memory_reserve_millis=100,
    )


def _plan(
    demand: ModelRunnerDemand | None = None,
    pools: tuple[AcceleratorTopology, ...] | None = None,
    runners: tuple[RunnerCapabilities, ...] | None = None,
    constraints: TopologyConstraints | None = None,
) -> TopologyPlan:
    return plan_model_runner_topology(
        demand=demand or _demand(),
        pools=pools or (_pool(),),
        runners=runners or (_runner(),),
        constraints=constraints or _constraints(),
    )


def test_contract_reuses_discovered_identity_and_serializes_topology() -> None:
    pool = _pool()

    payload = pool.to_dict()

    assert payload["resource_key"] == "dynamic:pool:0"
    assert payload["kind"] == "gpu"
    assert payload["vendor"] == "observed-vendor"
    assert payload["model"] == "observed-device-model"
    assert payload["available_devices"] == 8
    assert payload["memory_bytes_per_device"] == 24 * _GIB
    assert payload["intra_host_interconnect"] == "observed-fabric"
    assert payload["partitioning"] == "whole_device"


def test_plan_serialization_is_exact_desired_state() -> None:
    payload = _plan().to_dict()

    assert payload == {
        "schema_version": 1,
        "resource_key": "dynamic:pool:0",
        "provider": "azure",
        "region": "west",
        "device_kind": "gpu",
        "device_vendor": "observed-vendor",
        "device_model": "observed-device-model",
        "runner_id": "dynamic-runner",
        "partitioning": "whole_device",
        "distribution_mode": "explicit_parallel",
        "model_parallel_devices": 1,
        "data_parallel_replicas": 1,
        "tensor_parallel_size": 1,
        "pipeline_parallel_size": 1,
        "host_count": 1,
        "total_devices": 1,
        "per_device_required_bytes": 9 * _GIB,
        "per_device_usable_bytes": 23_192_823_398,
        "billable_allocation_units": 1,
        "estimated_hourly_cost_microusd": 100_000,
    }


def test_optional_zone_and_zero_cost_are_preserved() -> None:
    pool = replace(
        _pool(),
        zone=None,
        cost=AcceleratorCost(hourly_microusd_per_unit=0, devices_per_unit=1),
    )
    plan = _plan(pools=(pool,))

    assert pool.to_dict()["zone"] is None
    assert plan.estimated_hourly_cost_microusd == 0


def test_container_apps_single_device_limit_rejects_unshardable_fit() -> None:
    resource = _Resource(total_count=4, available_count=4, memory_gb=16.0)
    pool = _pool(
        resource=resource,
        host_count=4,
        devices_per_host=1,
        max_devices_per_workload=1,
        intra_host_interconnect="none",
        intra_host_interconnect_group_size=1,
    )

    with pytest.raises(TopologyPlanningError) as caught:
        _plan(demand=_demand(weight_bytes=20 * _GIB), pools=(pool,))

    assert "insufficient_memory" in caught.value.reason_codes
    assert "platform_device_limit" in caught.value.reason_codes


def test_single_device_platform_can_scale_independent_model_replicas() -> None:
    resource = _Resource(total_count=4, available_count=4, memory_gb=16.0)
    pool = _pool(
        resource=resource,
        host_count=4,
        devices_per_host=1,
        max_devices_per_workload=1,
        intra_host_interconnect="none",
        intra_host_interconnect_group_size=1,
    )

    plan = _plan(
        demand=_demand(weight_bytes=6 * _GIB, data_parallel_replicas=3),
        pools=(pool,),
    )

    assert plan.model_parallel_devices == 1
    assert plan.data_parallel_replicas == 3
    assert plan.total_devices == 3


def test_multi_gpu_vm_uses_tensor_parallel_when_attested_link_supports_it() -> None:
    plan = _plan(demand=_demand(weight_bytes=72 * _GIB))

    assert plan.model_parallel_devices == 4
    assert plan.tensor_parallel_size == 4
    assert plan.pipeline_parallel_size == 1
    assert plan.host_count == 1


def test_pcie_pool_uses_pipeline_parallel_instead_of_unsupported_tensor_parallel() -> None:
    pool = _pool(
        intra_host_interconnect="pcie-observed",
        intra_host_interconnect_group_size=1,
    )
    runner = _runner(
        pipeline_sizes=frozenset({1, 2, 4, 8}),
        pipeline_links=frozenset({"pcie-observed"}),
    )

    plan = _plan(
        demand=_demand(weight_bytes=72 * _GIB),
        pools=(pool,),
        runners=(runner,),
    )

    assert plan.model_parallel_devices == 4
    assert plan.tensor_parallel_size == 1
    assert plan.pipeline_parallel_size == 4


def test_slurm_multi_host_plan_uses_intra_and_cross_host_capabilities() -> None:
    resource = _Resource(
        location="slurm",
        resource_key="slurm:dynamic-pool",
        total_count=8,
        available_count=8,
        memory_gb=24.0,
    )
    pool = _pool(
        resource=resource,
        provider="slurm",
        region="cluster-a",
        host_count=2,
        devices_per_host=4,
        max_devices_per_workload=8,
        intra_host_interconnect="node-fabric",
        intra_host_interconnect_group_size=4,
        cross_host_interconnect="cluster-fabric",
    )
    runner = _runner(
        tensor_sizes=frozenset({1, 2, 4}),
        pipeline_sizes=frozenset({1, 2}),
        tensor_links=frozenset({"node-fabric"}),
        pipeline_links=frozenset({"cluster-fabric"}),
        cross_host_links=frozenset({"cluster-fabric"}),
        max_hosts=2,
    )

    plan = _plan(
        demand=_demand(weight_bytes=140 * _GIB),
        pools=(pool,),
        runners=(runner,),
        constraints=_constraints(
            providers=frozenset({"slurm"}),
            regions=frozenset({"cluster-a"}),
        ),
    )

    assert plan.model_parallel_devices == 8
    assert plan.tensor_parallel_size == 4
    assert plan.pipeline_parallel_size == 2
    assert plan.host_count == 2


def test_cross_host_plan_fails_without_attested_compatible_link() -> None:
    resource = _Resource(total_count=8, available_count=8, memory_gb=24.0)
    pool = _pool(
        resource=resource,
        host_count=2,
        devices_per_host=4,
        intra_host_interconnect_group_size=4,
        cross_host_interconnect=None,
    )
    runner = _runner(
        tensor_sizes=frozenset({1, 2, 4}),
        pipeline_sizes=frozenset({1, 2}),
        max_hosts=2,
    )

    with pytest.raises(TopologyPlanningError) as caught:
        _plan(demand=_demand(weight_bytes=140 * _GIB), pools=(pool,), runners=(runner,))

    assert "cross_host_interconnect_unsupported" in caught.value.reason_codes


def test_runner_managed_tpu_mesh_right_sizes_without_gpu_assumptions() -> None:
    resource = _Resource(
        kind="tpu",
        backend="jax",
        vendor="runtime-vendor",
        model="runtime-tpu-version",
        resource_key="gcp:dynamic-slice",
        total_count=4,
        available_count=4,
        memory_gb=32.0,
    )
    pool = _pool(resource=resource, provider="gcp", region="central")
    runner = _runner(
        kinds=frozenset({"tpu"}),
        backends=frozenset({"jax"}),
        distribution=DistributionMode.RUNNER_MANAGED,
        model_sizes=frozenset({1, 2, 4}),
        tensor_sizes=frozenset({1}),
        max_hosts=1,
    )

    plan = _plan(
        demand=_demand(allowed_kinds=frozenset({"tpu"}), weight_bytes=60 * _GIB),
        pools=(pool,),
        runners=(runner,),
        constraints=_constraints(
            providers=frozenset({"gcp"}), regions=frozenset({"central"})
        ),
    )

    assert plan.device_kind == "tpu"
    assert plan.model_parallel_devices == 4
    assert plan.tensor_parallel_size is None
    assert plan.pipeline_parallel_size is None


def test_runner_managed_sharding_supports_ollama_style_multi_device_use() -> None:
    resource = _Resource(
        backend="rocm",
        resource_key="local:runtime-pool",
        total_count=4,
        available_count=4,
        memory_gb=12.0,
    )
    pool = _pool(resource=resource, provider="local", region="local")
    runner = _runner(
        backends=frozenset({"rocm"}),
        distribution=DistributionMode.RUNNER_MANAGED,
        model_sizes=frozenset({1, 2, 3, 4}),
        tensor_sizes=frozenset({1}),
    )

    plan = _plan(
        demand=_demand(weight_bytes=36 * _GIB),
        pools=(pool,),
        runners=(runner,),
        constraints=_constraints(
            providers=frozenset({"local"}), regions=frozenset({"local"})
        ),
    )

    assert plan.model_parallel_devices == 4
    assert plan.distribution_mode is DistributionMode.RUNNER_MANAGED


def test_future_accelerator_kind_is_selected_from_discovered_capability() -> None:
    resource = _Resource(
        kind="fpga",
        backend="vendor-runtime",
        resource_key="future:dynamic:0",
        total_count=1,
        available_count=1,
        memory_gb=16.0,
    )
    pool = _pool(
        resource=resource,
        provider="future-provider",
        region="future-region",
        devices_per_host=1,
        max_devices_per_workload=1,
        intra_host_interconnect="none",
        intra_host_interconnect_group_size=1,
    )
    runner = _runner(
        kinds=frozenset({"fpga"}),
        backends=frozenset({"vendor-runtime"}),
        distribution=DistributionMode.SINGLE_DEVICE,
        model_sizes=frozenset({1}),
        tensor_sizes=frozenset({1}),
    )

    plan = _plan(
        demand=_demand(allowed_kinds=frozenset({"fpga"}), weight_bytes=4 * _GIB),
        pools=(pool,),
        runners=(runner,),
        constraints=_constraints(
            providers=frozenset({"future-provider"}),
            regions=frozenset({"future-region"}),
        ),
    )

    assert plan.device_kind == "fpga"
    assert plan.runner_id == "dynamic-runner"


@pytest.mark.parametrize(
    ("pool", "runner", "demand", "reason"),
    [
        (_pool(attested=False), _runner(), _demand(), "unattested_facts"),
        (
            _pool(resource=_Resource(memory_gb=None)),
            _runner(),
            _demand(),
            "unknown_memory",
        ),
        (_pool(cost=None), _runner(), _demand(), "unknown_cost"),
        (
            _pool(),
            _runner(backends=frozenset({"different-runtime"})),
            _demand(),
            "runner_backend_unsupported",
        ),
        (
            _pool(partitioning=PartitioningMode.HARDWARE_PARTITION),
            _runner(),
            _demand(),
            "runner_partitioning_unsupported",
        ),
        (
            _pool(
                partitioning=PartitioningMode.SHARED,
                memory_isolated=False,
            ),
            _runner(partitioning=frozenset({PartitioningMode.SHARED})),
            _demand(),
            "shared_accelerator_forbidden",
        ),
    ],
)
def test_uncertain_or_unsupported_topologies_fail_closed(
    pool: AcceleratorTopology,
    runner: RunnerCapabilities,
    demand: ModelRunnerDemand,
    reason: str,
) -> None:
    with pytest.raises(TopologyPlanningError) as caught:
        plan_model_runner_topology(
            demand=demand,
            pools=(pool,),
            runners=(runner,),
            constraints=_constraints(),
        )
    assert reason in caught.value.reason_codes


def test_explicit_shared_device_opt_in_still_requires_isolated_memory() -> None:
    pool = _pool(partitioning=PartitioningMode.SHARED, memory_isolated=False)
    runner = _runner(partitioning=frozenset({PartitioningMode.SHARED}))

    with pytest.raises(TopologyPlanningError) as caught:
        _plan(
            demand=_demand(allow_shared_accelerator=True),
            pools=(pool,),
            runners=(runner,),
        )

    assert "partition_memory_not_isolated" in caught.value.reason_codes


def test_hardware_partition_with_isolated_memory_can_be_explicitly_used() -> None:
    pool = _pool(partitioning=PartitioningMode.HARDWARE_PARTITION)
    runner = _runner(
        partitioning=frozenset({PartitioningMode.HARDWARE_PARTITION})
    )

    plan = _plan(pools=(pool,), runners=(runner,))

    assert plan.partitioning is PartitioningMode.HARDWARE_PARTITION


def test_shared_partition_requires_both_runner_and_model_opt_in() -> None:
    pool = _pool(partitioning=PartitioningMode.SHARED)
    runner = _runner(partitioning=frozenset({PartitioningMode.SHARED}))

    plan = _plan(
        demand=_demand(allow_shared_accelerator=True),
        pools=(pool,),
        runners=(runner,),
    )

    assert plan.partitioning is PartitioningMode.SHARED


def test_budget_and_capacity_are_hard_bounds() -> None:
    with pytest.raises(TopologyPlanningError) as budget_error:
        _plan(constraints=_constraints(max_cost=99_999))
    assert "hourly_budget_exceeded" in budget_error.value.reason_codes

    with pytest.raises(TopologyPlanningError) as device_error:
        _plan(
            demand=_demand(data_parallel_replicas=9),
            constraints=_constraints(max_devices=8),
        )
    assert "device_budget_exceeded" in device_error.value.reason_codes


def test_indivisible_multi_device_instance_cost_is_not_underestimated() -> None:
    pool = _pool(cost=800_000, devices_per_cost_unit=8)

    plan = _plan(demand=_demand(weight_bytes=30 * _GIB), pools=(pool,))

    assert plan.model_parallel_devices == 2
    assert plan.billable_allocation_units == 1
    assert plan.estimated_hourly_cost_microusd == 800_000


def test_right_sizing_prefers_lowest_cost_then_least_waste() -> None:
    costly_small = _pool(
        resource=_Resource(resource_key="dynamic:costly", memory_gb=24.0),
        cost=300_000,
    )
    cheaper = _pool(
        resource=_Resource(resource_key="dynamic:cheaper", memory_gb=48.0),
        cost=100_000,
    )

    plan = _plan(pools=(costly_small, cheaper))

    assert plan.resource_key == "dynamic:cheaper"

    tighter = _pool(
        resource=_Resource(resource_key="dynamic:tight", memory_gb=16.0),
        cost=100_000,
    )
    tied_large = _pool(
        resource=_Resource(resource_key="dynamic:large", memory_gb=48.0),
        cost=100_000,
    )
    tied_plan = _plan(pools=(tied_large, tighter))
    assert tied_plan.resource_key == "dynamic:tight"


def test_tensor_parallel_size_must_divide_model_constraint() -> None:
    runner = _runner(
        model_sizes=frozenset({1, 3}),
        tensor_sizes=frozenset({1, 3}),
    )

    with pytest.raises(TopologyPlanningError) as caught:
        _plan(
            demand=_demand(weight_bytes=40 * _GIB, tensor_parallel_divisor=8),
            runners=(runner,),
        )

    assert "tensor_parallel_divisor_mismatch" in caught.value.reason_codes


def test_missing_parallel_product_fails_closed() -> None:
    runner = _runner(
        model_sizes=frozenset({3}),
        tensor_sizes=frozenset({1}),
        pipeline_sizes=frozenset({1}),
    )

    with pytest.raises(TopologyPlanningError) as caught:
        _plan(runners=(runner,))

    assert "parallel_size_combination_unsupported" in caught.value.reason_codes


def test_provider_region_kind_and_runner_caps_are_all_policy_inputs() -> None:
    runner = _runner(
        kinds=frozenset({"different-kind"}),
        max_data_parallel_replicas=1,
    )
    demand = _demand(
        allowed_kinds=frozenset({"different-kind"}),
        data_parallel_replicas=2,
    )

    with pytest.raises(TopologyPlanningError) as caught:
        _plan(
            demand=demand,
            runners=(runner,),
            constraints=_constraints(
                providers=frozenset({"different-provider"}),
                regions=frozenset({"different-region"}),
            ),
        )

    assert {
        "provider_forbidden",
        "region_forbidden",
        "model_device_kind_unsupported",
        "runner_device_kind_unsupported",
        "data_parallel_unsupported",
    }.issubset(caught.value.reason_codes)


def test_availability_and_host_limits_are_not_inferred_from_pool_total() -> None:
    resource = _Resource(total_count=8, available_count=2)
    pool = _pool(resource=resource, host_count=1, devices_per_host=8)
    runner = _runner(max_hosts=1)

    with pytest.raises(TopologyPlanningError) as caught:
        _plan(
            demand=_demand(weight_bytes=72 * _GIB),
            pools=(pool,),
            runners=(runner,),
        )
    assert "insufficient_available_devices" in caught.value.reason_codes

    multi_host_pool = _pool(
        host_count=2,
        devices_per_host=4,
        intra_host_interconnect_group_size=4,
        cross_host_interconnect="cluster-link",
    )
    host_limited_runner = _runner(
        tensor_sizes=frozenset({1, 2, 4}),
        pipeline_sizes=frozenset({1, 2}),
        pipeline_links=frozenset({"cluster-link"}),
        cross_host_links=frozenset({"cluster-link"}),
        max_hosts=1,
    )
    with pytest.raises(TopologyPlanningError) as host_error:
        _plan(
            demand=_demand(weight_bytes=140 * _GIB),
            pools=(multi_host_pool,),
            runners=(host_limited_runner,),
        )
    assert "host_limit_exceeded" in host_error.value.reason_codes


def test_safe_progress_traces_never_expose_resource_or_model_identity() -> None:
    traces: list[TopologyTrace] = []

    plan = plan_model_runner_topology(
        demand=_demand(),
        pools=(_pool(),),
        runners=(_runner(),),
        constraints=_constraints(),
        trace_sink=traces.append,
    )

    assert [trace.event for trace in traces] == [
        PlanningEvent.PLANNING_STARTED,
        PlanningEvent.CANDIDATE_ACCEPTED,
        PlanningEvent.PLANNING_COMPLETED,
    ]
    assert traces[-1].feasible_count == 1
    serialized = repr(traces)
    assert plan.resource_key not in serialized
    assert "catalog:model" not in serialized


@pytest.mark.parametrize(
    "factory",
    [
        lambda: _pool(host_count=0),
        lambda: _pool(devices_per_host=0),
        lambda: _pool(max_devices_per_workload=9),
        lambda: _pool(
            intra_host_interconnect=None,
            intra_host_interconnect_group_size=2,
        ),
        lambda: _runner(kinds=frozenset()),
        lambda: _runner(model_sizes=frozenset({0})),
        lambda: _runner(
            distribution=DistributionMode.SINGLE_DEVICE,
            model_sizes=frozenset({1, 2}),
        ),
        lambda: _demand(weight_bytes=0),
        lambda: _demand(context_tokens=0),
        lambda: _constraints(providers=frozenset()),
        lambda: _constraints(max_cost=0),
    ],
)
def test_invalid_contract_values_are_rejected(factory: Callable[[], object]) -> None:
    with pytest.raises(ValueError):
        factory()


def test_empty_inventory_fails_closed_and_emits_failure() -> None:
    traces: list[TopologyTrace] = []

    with pytest.raises(TopologyPlanningError) as caught:
        plan_model_runner_topology(
            demand=_demand(),
            pools=(),
            runners=(_runner(),),
            constraints=_constraints(),
            trace_sink=traces.append,
        )

    assert caught.value.reason_codes == ("empty_inventory",)
    assert traces[-1].event is PlanningEvent.PLANNING_FAILED


def test_empty_runner_inventory_fails_closed_and_emits_failure() -> None:
    traces: list[TopologyTrace] = []

    with pytest.raises(TopologyPlanningError) as caught:
        plan_model_runner_topology(
            demand=_demand(),
            pools=(_pool(),),
            runners=(),
            constraints=_constraints(),
            trace_sink=traces.append,
        )

    assert caught.value.reason_codes == ("empty_runner_inventory",)
    assert traces[-1].reason_code == "empty_runner_inventory"


def test_empty_error_reason_and_trace_validation_are_fail_closed() -> None:
    assert TopologyPlanningError(()).reason_codes == ("no_feasible_topology",)
    with pytest.raises(ValueError, match="PlanningEvent"):
        TopologyTrace(cast(PlanningEvent, "bad-event"), 0)
    with pytest.raises(ValueError, match="reason_code"):
        TopologyTrace(PlanningEvent.PLANNING_FAILED, 0, reason_code="bad\ncode")


@pytest.mark.parametrize(
    "call",
    [
        lambda: plan_model_runner_topology(
            demand=cast(ModelRunnerDemand, object()),
            pools=(),
            runners=(),
            constraints=_constraints(),
        ),
        lambda: plan_model_runner_topology(
            demand=_demand(),
            pools=cast(tuple[AcceleratorTopology, ...], []),
            runners=(),
            constraints=_constraints(),
        ),
        lambda: plan_model_runner_topology(
            demand=_demand(),
            pools=(_pool(),),
            runners=cast(tuple[RunnerCapabilities, ...], []),
            constraints=_constraints(),
        ),
        lambda: plan_model_runner_topology(
            demand=_demand(),
            pools=(_pool(),),
            runners=(_runner(),),
            constraints=cast(TopologyConstraints, object()),
        ),
        lambda: plan_model_runner_topology(
            demand=_demand(),
            pools=(_pool(),),
            runners=(_runner(),),
            constraints=_constraints(),
            trace_sink=cast(Callable[[TopologyTrace], None], object()),
        ),
    ],
)
def test_planner_rejects_mutable_or_wrong_contract_shapes(
    call: Callable[[], object],
) -> None:
    with pytest.raises(ValueError):
        call()


def test_memory_demand_overflow_and_invalid_shard_count_are_rejected() -> None:
    with pytest.raises(ValueError, match="replicated model memory"):
        _demand(
            runtime_overhead_bytes=(1 << 63) - 1,
            kv_cache_bytes_per_token=1,
            context_tokens=1,
        )
    demand = _demand(runtime_overhead_bytes=1, weight_bytes=(1 << 63) - 1)
    with pytest.raises(ValueError, match="per-device model memory"):
        demand.required_bytes_per_device(1)
    with pytest.raises(ValueError, match="model_parallel_devices"):
        demand.required_bytes_per_device(0)
