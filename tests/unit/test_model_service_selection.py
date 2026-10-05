"""Universal model-variant, runner, and accelerator selection tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, replace
from pathlib import Path
from typing import cast

import pytest

from general_ludd.hardware.accelerator_topology import (
    AcceleratorCost,
    AcceleratorTopology,
    DistributionMode,
    PartitioningMode,
    RunnerCapabilities,
    TopologyConstraints,
)
from general_ludd.hardware.model_service_rightsizing import (
    InferenceWorkloadDemand,
    ModelVariantEvidence,
    RunnerSizingEvidence,
)
from general_ludd.hardware.model_service_selection import (
    ModelServiceSelection,
    ModelServiceSelectionError,
    select_model_service,
)

_GIB = 1024**3


def test_focused_coverage_profile_measures_selection_contract() -> None:
    profile = Path("config/coverage_model_service_selection.ini").read_text()

    assert "*/src/general_ludd/hardware/model_service_selection.py" in profile


@dataclass(frozen=True, slots=True)
class _Resource:
    """Minimal structural accelerator inventory record."""

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
    cost: int = 100_000,
) -> AcceleratorTopology:
    return AcceleratorTopology(
        resource=resource or _Resource(),
        provider=provider,
        region=region,
        zone="observed-zone",
        host_count=1,
        devices_per_host=8,
        max_devices_per_workload=8,
        intra_host_interconnect="observed-fabric",
        intra_host_interconnect_group_size=8,
        cross_host_interconnect=None,
        partitioning=PartitioningMode.WHOLE_DEVICE,
        memory_isolated=True,
        cost=AcceleratorCost(
            hourly_microusd_per_unit=cost,
            devices_per_unit=1,
        ),
        facts_attested=True,
    )


def _capability(
    *,
    runner_id: str = "vllm-observed",
    kinds: frozenset[str] = frozenset({"gpu"}),
    backends: frozenset[str] = frozenset({"cuda"}),
    distribution: DistributionMode = DistributionMode.EXPLICIT_PARALLEL,
) -> RunnerCapabilities:
    return RunnerCapabilities(
        runner_id=runner_id,
        supported_device_kinds=kinds,
        supported_backends=backends,
        supported_partitioning=frozenset({PartitioningMode.WHOLE_DEVICE}),
        distribution_mode=distribution,
        supported_model_parallel_sizes=frozenset({1, 2, 4, 8}),
        supported_tensor_parallel_sizes=(
            frozenset({1, 2, 4, 8})
            if distribution is DistributionMode.EXPLICIT_PARALLEL
            else frozenset({1})
        ),
        supported_pipeline_parallel_sizes=frozenset({1}),
        tensor_parallel_interconnects=frozenset({"observed-fabric"}),
        pipeline_parallel_interconnects=frozenset(),
        cross_host_interconnects=frozenset(),
        max_hosts=1,
        max_data_parallel_replicas=16,
    )


def _variant(**overrides: object) -> ModelVariantEvidence:
    values: dict[str, object] = {
        "variant_id": "model:revision:q4",
        "model_id": "owner/model@0123456789abcdef",
        "architecture": "decoder-transformer",
        "quantization": "q4_k_m",
        "weight_bytes": 8 * _GIB,
        "runtime_overhead_bytes": _GIB,
        "kv_cache_bytes_per_token": 0,
        "max_context_tokens": 32_768,
        "tensor_parallel_divisor": 8,
        "allowed_device_kinds": frozenset({"gpu"}),
        "supported_runner_ids": frozenset({"vllm-observed"}),
        "quality_millis": 920,
        "facts_attested": True,
    }
    values.update(overrides)
    return ModelVariantEvidence(**values)  # type: ignore[arg-type]


def _workload(**overrides: object) -> InferenceWorkloadDemand:
    values: dict[str, object] = {
        "workload_id": "task:chemistry-design",
        "input_tokens": 4_000,
        "output_tokens": 1_000,
        "concurrent_sequences": 1,
        "minimum_quality_millis": 900,
        "max_p95_latency_millis": 2_000,
    }
    values.update(overrides)
    return InferenceWorkloadDemand(**values)  # type: ignore[arg-type]


def _sizing(**overrides: object) -> RunnerSizingEvidence:
    values: dict[str, object] = {
        "runner_id": "vllm-observed",
        "max_batch_size_per_replica": 8,
        "max_context_tokens": 16_384,
        "max_output_tokens": 4_096,
        "max_data_parallel_replicas": 4,
        "observed_p95_latency_millis": 1_250,
        "facts_attested": True,
    }
    values.update(overrides)
    return RunnerSizingEvidence(**values)  # type: ignore[arg-type]


def _constraints(
    *,
    providers: frozenset[str] = frozenset({"azure"}),
    regions: frozenset[str] = frozenset({"west"}),
) -> TopologyConstraints:
    return TopologyConstraints(
        allowed_providers=providers,
        allowed_regions=regions,
        max_hourly_cost_microusd=10_000_000,
        max_total_devices=64,
        memory_reserve_millis=100,
    )


def _select(
    *,
    variants: tuple[ModelVariantEvidence, ...] | None = None,
    workload: InferenceWorkloadDemand | None = None,
    sizing: tuple[RunnerSizingEvidence, ...] | None = None,
    pools: tuple[AcceleratorTopology, ...] | None = None,
    capabilities: tuple[RunnerCapabilities, ...] | None = None,
    constraints: TopologyConstraints | None = None,
) -> ModelServiceSelection:
    return select_model_service(
        variants=(_variant(),) if variants is None else variants,
        workload=_workload() if workload is None else workload,
        runner_sizing=(_sizing(),) if sizing is None else sizing,
        pools=(_pool(),) if pools is None else pools,
        runner_capabilities=(
            (_capability(),) if capabilities is None else capabilities
        ),
        constraints=_constraints() if constraints is None else constraints,
    )


def test_selects_least_cost_quantization_that_meets_quality() -> None:
    high_quality_large = _variant(
        variant_id="model:revision:q8",
        quantization="q8_0",
        weight_bytes=30 * _GIB,
        quality_millis=980,
    )

    selected = _select(variants=(high_quality_large, _variant()))

    assert selected.demand.variant_id == "model:revision:q4"
    assert selected.demand.quantization == "q4_k_m"
    assert selected.topology.model_parallel_devices == 1
    assert selected.topology.estimated_hourly_cost_microusd == 100_000


def test_equal_cost_tie_prefers_more_quality_then_stable_identity() -> None:
    q5 = _variant(
        variant_id="model:revision:q5",
        quantization="q5_k_m",
        quality_millis=950,
    )
    q6 = replace(q5, variant_id="model:revision:q6", quantization="q6_k")

    selected = _select(variants=(_variant(), q6, q5))

    assert selected.demand.variant_id == "model:revision:q5"
    assert selected.demand.quality_millis == 950


def test_future_accelerator_and_runner_managed_sharding_have_no_gpu_assumption() -> None:
    resource = _Resource(
        kind="future-accelerator",
        backend="future-runtime",
        resource_key="future:pool:0",
        memory_gb=13.0,
    )
    variant = _variant(
        allowed_device_kinds=frozenset({"future-accelerator"}),
        supported_runner_ids=frozenset({"future-runner"}),
        weight_bytes=20 * _GIB,
    )

    selected = _select(
        variants=(variant,),
        sizing=(_sizing(runner_id="future-runner"),),
        pools=(_pool(resource=resource, provider="future", region="edge"),),
        capabilities=(
            _capability(
                runner_id="future-runner",
                kinds=frozenset({"future-accelerator"}),
                backends=frozenset({"future-runtime"}),
                distribution=DistributionMode.RUNNER_MANAGED,
            ),
        ),
        constraints=_constraints(
            providers=frozenset({"future"}),
            regions=frozenset({"edge"}),
        ),
    )

    assert selected.topology.device_kind == "future-accelerator"
    assert selected.topology.runner_id == "future-runner"
    assert selected.topology.model_parallel_devices == 2


def test_serialization_is_exact_immutable_desired_state() -> None:
    selected = _select()

    payload = selected.to_dict()

    assert payload == {
        "schema_version": 1,
        "demand": selected.demand.to_dict(),
        "topology": selected.topology.to_dict(),
    }
    with pytest.raises(FrozenInstanceError):
        selected.demand = selected.demand  # type: ignore[misc]


def test_unusable_pairs_return_all_stable_fail_closed_reasons() -> None:
    with pytest.raises(ModelServiceSelectionError) as caught:
        _select(
            variants=(
                _variant(
                    quality_millis=899,
                    supported_runner_ids=frozenset(
                        {"vllm-observed", "unattested-runner"}
                    ),
                ),
            ),
            sizing=(
                _sizing(runner_id="missing-capability"),
                _sizing(runner_id="unattested-runner", facts_attested=False),
                _sizing(),
            ),
            capabilities=(
                _capability(),
                _capability(runner_id="unattested-runner"),
            ),
        )

    assert caught.value.reason_codes == (
        "quality_below_minimum",
        "runner_capability_missing",
        "runner_unattested",
    )
    assert str(caught.value).startswith("no feasible model service: ")


def test_topology_rejections_are_preserved_without_hidden_fallback() -> None:
    tiny_pool = _pool(resource=_Resource(memory_gb=2.0))

    with pytest.raises(ModelServiceSelectionError) as caught:
        _select(pools=(tiny_pool,))

    assert caught.value.reason_codes == ("insufficient_memory",)


def test_empty_error_and_oversized_inventory_are_bounded() -> None:
    assert ModelServiceSelectionError(()).reason_codes == (
        "no_feasible_model_service",
    )

    with pytest.raises(ModelServiceSelectionError) as caught:
        _select(variants=(_variant(),) * 4_097)

    assert caught.value.reason_codes == ("selection_inventory_too_large",)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"variants": ()}, "empty_variant_inventory"),
        ({"sizing": ()}, "empty_runner_sizing_inventory"),
        ({"capabilities": ()}, "empty_runner_capability_inventory"),
        ({"pools": ()}, "empty_accelerator_inventory"),
        (
            {"variants": (_variant(), _variant())},
            "ambiguous_variant_id",
        ),
        (
            {"sizing": (_sizing(), _sizing())},
            "ambiguous_runner_sizing_id",
        ),
        (
            {"capabilities": (_capability(), _capability())},
            "ambiguous_runner_capability_id",
        ),
    ],
)
def test_empty_or_ambiguous_inventory_fails_closed(
    kwargs: dict[str, object],
    reason: str,
) -> None:
    with pytest.raises(ModelServiceSelectionError) as caught:
        _select(**kwargs)  # type: ignore[arg-type]

    assert caught.value.reason_codes == (reason,)


@pytest.mark.parametrize(
    "call",
    [
        lambda: select_model_service(
            variants=cast(tuple[ModelVariantEvidence, ...], []),
            workload=_workload(),
            runner_sizing=(_sizing(),),
            pools=(_pool(),),
            runner_capabilities=(_capability(),),
            constraints=_constraints(),
        ),
        lambda: select_model_service(
            variants=(_variant(),),
            workload=cast(InferenceWorkloadDemand, object()),
            runner_sizing=(_sizing(),),
            pools=(_pool(),),
            runner_capabilities=(_capability(),),
            constraints=_constraints(),
        ),
        lambda: select_model_service(
            variants=(_variant(),),
            workload=_workload(),
            runner_sizing=cast(tuple[RunnerSizingEvidence, ...], []),
            pools=(_pool(),),
            runner_capabilities=(_capability(),),
            constraints=_constraints(),
        ),
        lambda: select_model_service(
            variants=(_variant(),),
            workload=_workload(),
            runner_sizing=(_sizing(),),
            pools=cast(tuple[AcceleratorTopology, ...], []),
            runner_capabilities=(_capability(),),
            constraints=_constraints(),
        ),
        lambda: select_model_service(
            variants=(_variant(),),
            workload=_workload(),
            runner_sizing=(_sizing(),),
            pools=(_pool(),),
            runner_capabilities=cast(tuple[RunnerCapabilities, ...], []),
            constraints=_constraints(),
        ),
        lambda: select_model_service(
            variants=(_variant(),),
            workload=_workload(),
            runner_sizing=(_sizing(),),
            pools=(_pool(),),
            runner_capabilities=(_capability(),),
            constraints=cast(TopologyConstraints, object()),
        ),
    ],
)
def test_rejects_mutable_or_wrong_contract_shapes(call: object) -> None:
    with pytest.raises(ValueError):
        call()  # type: ignore[operator]
