"""Universal model/workload evidence to topology-demand derivation tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from general_ludd.hardware.model_service_rightsizing import (
    DemandDerivationError,
    DerivedModelDemand,
    InferenceWorkloadDemand,
    ModelVariantEvidence,
    RunnerSizingEvidence,
    derive_model_runner_demand,
)

_GIB = 1024**3


def test_focused_coverage_profile_measures_the_universal_contract() -> None:
    profile = Path("config/coverage_model_service_rightsizing.ini").read_text()

    assert "*/src/general_ludd/hardware/model_service_rightsizing.py" in profile


def _variant(**overrides: object) -> ModelVariantEvidence:
    values: dict[str, object] = {
        "variant_id": "model:revision:q4",
        "model_id": "owner/model@0123456789abcdef",
        "architecture": "decoder-transformer",
        "quantization": "q4_k_m",
        "weight_bytes": 8 * _GIB,
        "runtime_overhead_bytes": _GIB,
        "kv_cache_bytes_per_token": 4096,
        "max_context_tokens": 32_768,
        "tensor_parallel_divisor": 32,
        "allowed_device_kinds": frozenset({"gpu", "future-accelerator"}),
        "supported_runner_ids": frozenset({"vllm-observed", "llamacpp-observed"}),
        "quality_millis": 920,
        "facts_attested": True,
    }
    values.update(overrides)
    return ModelVariantEvidence(**values)  # type: ignore[arg-type]


def _workload(**overrides: object) -> InferenceWorkloadDemand:
    values: dict[str, object] = {
        "workload_id": "task:firmware-generation",
        "input_tokens": 6_000,
        "output_tokens": 2_000,
        "concurrent_sequences": 17,
        "minimum_quality_millis": 900,
        "max_p95_latency_millis": 2_000,
        "allow_shared_accelerator": False,
    }
    values.update(overrides)
    return InferenceWorkloadDemand(**values)  # type: ignore[arg-type]


def _runner(**overrides: object) -> RunnerSizingEvidence:
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


def test_derives_context_batch_replicas_and_topology_demand() -> None:
    derived = derive_model_runner_demand(
        variant=_variant(),
        workload=_workload(),
        runner=_runner(),
    )

    assert derived == DerivedModelDemand(
        variant_id="model:revision:q4",
        runner_id="vllm-observed",
        architecture="decoder-transformer",
        quantization="q4_k_m",
        quality_millis=920,
        context_tokens=8_000,
        output_tokens=2_000,
        batch_size_per_replica=8,
        data_parallel_replicas=3,
        topology_demand=derived.topology_demand,
    )
    assert derived.topology_demand.model_id == "owner/model@0123456789abcdef"
    assert derived.topology_demand.allowed_device_kinds == frozenset(
        {"gpu", "future-accelerator"}
    )
    assert derived.topology_demand.weight_bytes == 8 * _GIB
    assert derived.topology_demand.runtime_overhead_bytes == _GIB
    assert derived.topology_demand.kv_cache_bytes_per_token == 4096
    assert derived.topology_demand.context_tokens == 8_000
    assert derived.topology_demand.concurrent_sequences == 8
    assert derived.topology_demand.data_parallel_replicas == 3
    assert derived.topology_demand.tensor_parallel_divisor == 32
    assert derived.topology_demand.allow_shared_accelerator is False


def test_concurrency_smaller_than_runner_batch_uses_one_right_sized_replica() -> None:
    derived = derive_model_runner_demand(
        variant=_variant(),
        workload=_workload(concurrent_sequences=3, allow_shared_accelerator=True),
        runner=_runner(max_batch_size_per_replica=64),
    )

    assert derived.batch_size_per_replica == 3
    assert derived.data_parallel_replicas == 1
    assert derived.topology_demand.allow_shared_accelerator is True


def test_exact_capacity_boundary_is_accepted() -> None:
    derived = derive_model_runner_demand(
        variant=_variant(max_context_tokens=8_000),
        workload=_workload(max_p95_latency_millis=1_250),
        runner=_runner(
            max_context_tokens=8_000,
            max_output_tokens=2_000,
            max_data_parallel_replicas=3,
        ),
    )

    assert derived.context_tokens == 8_000
    assert derived.output_tokens == 2_000
    assert derived.data_parallel_replicas == 3


@pytest.mark.parametrize(
    ("variant", "workload", "runner", "reason"),
    [
        (_variant(facts_attested=False), _workload(), _runner(), "variant_unattested"),
        (_variant(), _workload(), _runner(facts_attested=False), "runner_unattested"),
        (
            _variant(supported_runner_ids=frozenset({"other"})),
            _workload(),
            _runner(),
            "runner_unsupported",
        ),
        (_variant(quality_millis=899), _workload(), _runner(), "quality_below_minimum"),
        (
            _variant(max_context_tokens=7_999),
            _workload(),
            _runner(),
            "model_context_exceeded",
        ),
        (
            _variant(),
            _workload(),
            _runner(max_context_tokens=7_999),
            "runner_context_exceeded",
        ),
        (
            _variant(),
            _workload(),
            _runner(max_output_tokens=1_999),
            "runner_output_exceeded",
        ),
        (
            _variant(),
            _workload(max_p95_latency_millis=1_249),
            _runner(),
            "latency_budget_exceeded",
        ),
        (
            _variant(),
            _workload(concurrent_sequences=33),
            _runner(max_data_parallel_replicas=4),
            "replica_limit_exceeded",
        ),
    ],
)
def test_derivation_fails_closed_on_unmet_evidence(
    variant: ModelVariantEvidence,
    workload: InferenceWorkloadDemand,
    runner: RunnerSizingEvidence,
    reason: str,
) -> None:
    with pytest.raises(DemandDerivationError) as caught:
        derive_model_runner_demand(variant=variant, workload=workload, runner=runner)

    assert caught.value.reason_code == reason
    assert str(caught.value) == f"model demand derivation refused: {reason}"


@pytest.mark.parametrize(
    "factory",
    [
        lambda: _variant(variant_id=""),
        lambda: _variant(architecture="bad\narchitecture"),
        lambda: _variant(allowed_device_kinds={"gpu"}),
        lambda: _variant(supported_runner_ids=frozenset()),
        lambda: _variant(weight_bytes=True),
        lambda: _variant(quality_millis=1_001),
        lambda: _variant(facts_attested=1),
        lambda: _workload(input_tokens=0),
        lambda: _workload(input_tokens=100_000_000, output_tokens=1),
        lambda: _workload(output_tokens=True),
        lambda: _workload(concurrent_sequences=0),
        lambda: _workload(minimum_quality_millis=-1),
        lambda: _workload(allow_shared_accelerator=1),
        lambda: _runner(max_batch_size_per_replica=0),
        lambda: _runner(observed_p95_latency_millis=True),
        lambda: _runner(facts_attested=1),
    ],
)
def test_contracts_reject_mutable_ambiguous_or_unbounded_values(factory: object) -> None:
    with pytest.raises(ValueError):
        factory()  # type: ignore[operator]


def test_derived_contract_is_frozen_and_serializes_exactly() -> None:
    derived = derive_model_runner_demand(
        variant=_variant(),
        workload=_workload(),
        runner=_runner(),
    )

    assert derived.to_dict() == {
        "schema_version": 1,
        "variant_id": "model:revision:q4",
        "runner_id": "vllm-observed",
        "architecture": "decoder-transformer",
        "quantization": "q4_k_m",
        "quality_millis": 920,
        "context_tokens": 8_000,
        "output_tokens": 2_000,
        "batch_size_per_replica": 8,
        "data_parallel_replicas": 3,
        "topology_demand": {
            "model_id": "owner/model@0123456789abcdef",
            "allowed_device_kinds": ["future-accelerator", "gpu"],
            "weight_bytes": 8 * _GIB,
            "runtime_overhead_bytes": _GIB,
            "kv_cache_bytes_per_token": 4096,
            "context_tokens": 8_000,
            "concurrent_sequences": 8,
            "data_parallel_replicas": 3,
            "tensor_parallel_divisor": 32,
            "allow_shared_accelerator": False,
        },
    }
    with pytest.raises(FrozenInstanceError):
        replace(derived, output_tokens=1).output_tokens = 2


@pytest.mark.parametrize(
    ("variant", "workload", "runner"),
    [
        (None, _workload(), _runner()),
        (_variant(), object(), _runner()),
        (_variant(), _workload(), "bad"),
    ],
)
def test_derivation_rejects_wrong_contract_types(
    variant: object,
    workload: object,
    runner: object,
) -> None:
    with pytest.raises(ValueError):
        derive_model_runner_demand(
            variant=variant,  # type: ignore[arg-type]
            workload=workload,  # type: ignore[arg-type]
            runner=runner,  # type: ignore[arg-type]
        )
