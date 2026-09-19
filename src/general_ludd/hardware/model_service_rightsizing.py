"""Derive provider-neutral model-runner demand from immutable evidence."""

from __future__ import annotations

import math
from dataclasses import dataclass

from general_ludd.hardware.accelerator_topology import ModelRunnerDemand

_MAX_TEXT = 512
_MAX_BYTES = (1 << 63) - 1
_MAX_TOKENS = 100_000_000
_MAX_SEQUENCES = 1_000_000
_MAX_LATENCY_MILLIS = 86_400_000
_MAX_QUALITY_MILLIS = 1_000


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT:
        raise ValueError(f"{field_name} must be bounded non-empty text")
    if any(delimiter in value for delimiter in "\x00\r\n"):
        raise ValueError(f"{field_name} must not contain control delimiters")
    return value


def _bounded_int(
    value: object,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{field_name} is outside its bounded range")
    return value


def _text_set(value: object, field_name: str) -> frozenset[str]:
    if not isinstance(value, frozenset) or not value:
        raise ValueError(f"{field_name} must be a non-empty frozenset")
    for item in value:
        _require_text(item, field_name)
    return value


@dataclass(frozen=True, slots=True)
class ModelVariantEvidence:
    """One immutable, already-materialized model artifact variant."""

    variant_id: str
    model_id: str
    architecture: str
    quantization: str
    weight_bytes: int
    runtime_overhead_bytes: int
    kv_cache_bytes_per_token: int
    max_context_tokens: int
    tensor_parallel_divisor: int
    allowed_device_kinds: frozenset[str]
    supported_runner_ids: frozenset[str]
    quality_millis: int
    facts_attested: bool

    def __post_init__(self) -> None:
        """Reject mutable, incomplete, or unbounded artifact evidence."""
        for field_name in ("variant_id", "model_id", "architecture", "quantization"):
            _require_text(getattr(self, field_name), field_name)
        _bounded_int(self.weight_bytes, "weight_bytes", minimum=1, maximum=_MAX_BYTES)
        _bounded_int(
            self.runtime_overhead_bytes,
            "runtime_overhead_bytes",
            minimum=0,
            maximum=_MAX_BYTES,
        )
        _bounded_int(
            self.kv_cache_bytes_per_token,
            "kv_cache_bytes_per_token",
            minimum=0,
            maximum=_MAX_BYTES,
        )
        _bounded_int(
            self.max_context_tokens,
            "max_context_tokens",
            minimum=1,
            maximum=_MAX_TOKENS,
        )
        _bounded_int(
            self.tensor_parallel_divisor,
            "tensor_parallel_divisor",
            minimum=1,
            maximum=_MAX_SEQUENCES,
        )
        _text_set(self.allowed_device_kinds, "allowed_device_kinds")
        _text_set(self.supported_runner_ids, "supported_runner_ids")
        _bounded_int(
            self.quality_millis,
            "quality_millis",
            minimum=0,
            maximum=_MAX_QUALITY_MILLIS,
        )
        if not isinstance(self.facts_attested, bool):
            raise ValueError("facts_attested must be a bool")


@dataclass(frozen=True, slots=True)
class InferenceWorkloadDemand:
    """Task-graph demand without provider or hardware model assumptions."""

    workload_id: str
    input_tokens: int
    output_tokens: int
    concurrent_sequences: int
    minimum_quality_millis: int
    max_p95_latency_millis: int
    allow_shared_accelerator: bool = False

    def __post_init__(self) -> None:
        """Require explicit finite task budgets."""
        _require_text(self.workload_id, "workload_id")
        for field_name in ("input_tokens", "output_tokens"):
            _bounded_int(
                getattr(self, field_name),
                field_name,
                minimum=1,
                maximum=_MAX_TOKENS,
            )
        if self.input_tokens + self.output_tokens > _MAX_TOKENS:
            raise ValueError("combined context tokens exceed the bounded range")
        _bounded_int(
            self.concurrent_sequences,
            "concurrent_sequences",
            minimum=1,
            maximum=_MAX_SEQUENCES,
        )
        _bounded_int(
            self.minimum_quality_millis,
            "minimum_quality_millis",
            minimum=0,
            maximum=_MAX_QUALITY_MILLIS,
        )
        _bounded_int(
            self.max_p95_latency_millis,
            "max_p95_latency_millis",
            minimum=1,
            maximum=_MAX_LATENCY_MILLIS,
        )
        if not isinstance(self.allow_shared_accelerator, bool):
            raise ValueError("allow_shared_accelerator must be a bool")


@dataclass(frozen=True, slots=True)
class RunnerSizingEvidence:
    """Observed runner capacity used to translate task demand into replicas."""

    runner_id: str
    max_batch_size_per_replica: int
    max_context_tokens: int
    max_output_tokens: int
    max_data_parallel_replicas: int
    observed_p95_latency_millis: int
    facts_attested: bool

    def __post_init__(self) -> None:
        """Require bounded, explicitly attested runner limits."""
        _require_text(self.runner_id, "runner_id")
        for field_name, maximum in (
            ("max_batch_size_per_replica", _MAX_SEQUENCES),
            ("max_context_tokens", _MAX_TOKENS),
            ("max_output_tokens", _MAX_TOKENS),
            ("max_data_parallel_replicas", _MAX_SEQUENCES),
            ("observed_p95_latency_millis", _MAX_LATENCY_MILLIS),
        ):
            _bounded_int(
                getattr(self, field_name),
                field_name,
                minimum=1,
                maximum=maximum,
            )
        if not isinstance(self.facts_attested, bool):
            raise ValueError("facts_attested must be a bool")


@dataclass(frozen=True, slots=True)
class DerivedModelDemand:
    """Auditable bridge from task/model evidence to the topology planner."""

    variant_id: str
    runner_id: str
    architecture: str
    quantization: str
    quality_millis: int
    context_tokens: int
    output_tokens: int
    batch_size_per_replica: int
    data_parallel_replicas: int
    topology_demand: ModelRunnerDemand

    def to_dict(self) -> dict[str, object]:
        """Return exact desired demand without task content or provider secrets."""
        demand = self.topology_demand
        return {
            "schema_version": 1,
            "variant_id": self.variant_id,
            "runner_id": self.runner_id,
            "architecture": self.architecture,
            "quantization": self.quantization,
            "quality_millis": self.quality_millis,
            "context_tokens": self.context_tokens,
            "output_tokens": self.output_tokens,
            "batch_size_per_replica": self.batch_size_per_replica,
            "data_parallel_replicas": self.data_parallel_replicas,
            "topology_demand": {
                "model_id": demand.model_id,
                "allowed_device_kinds": sorted(demand.allowed_device_kinds),
                "weight_bytes": demand.weight_bytes,
                "runtime_overhead_bytes": demand.runtime_overhead_bytes,
                "kv_cache_bytes_per_token": demand.kv_cache_bytes_per_token,
                "context_tokens": demand.context_tokens,
                "concurrent_sequences": demand.concurrent_sequences,
                "data_parallel_replicas": demand.data_parallel_replicas,
                "tensor_parallel_divisor": demand.tensor_parallel_divisor,
                "allow_shared_accelerator": demand.allow_shared_accelerator,
            },
        }


class DemandDerivationError(ValueError):
    """Stable fail-closed refusal from immutable sizing evidence."""

    def __init__(self, reason_code: str) -> None:
        """Retain one bounded reason without model or workload content."""
        self.reason_code = _require_text(reason_code, "reason_code")
        super().__init__(f"model demand derivation refused: {self.reason_code}")


def _refuse_unmet_evidence(
    variant: ModelVariantEvidence,
    workload: InferenceWorkloadDemand,
    runner: RunnerSizingEvidence,
    context_tokens: int,
) -> None:
    if not variant.facts_attested:
        raise DemandDerivationError("variant_unattested")
    if not runner.facts_attested:
        raise DemandDerivationError("runner_unattested")
    if runner.runner_id not in variant.supported_runner_ids:
        raise DemandDerivationError("runner_unsupported")
    if variant.quality_millis < workload.minimum_quality_millis:
        raise DemandDerivationError("quality_below_minimum")
    if context_tokens > variant.max_context_tokens:
        raise DemandDerivationError("model_context_exceeded")
    if context_tokens > runner.max_context_tokens:
        raise DemandDerivationError("runner_context_exceeded")
    if workload.output_tokens > runner.max_output_tokens:
        raise DemandDerivationError("runner_output_exceeded")
    if runner.observed_p95_latency_millis > workload.max_p95_latency_millis:
        raise DemandDerivationError("latency_budget_exceeded")


def derive_model_runner_demand(
    *,
    variant: ModelVariantEvidence,
    workload: InferenceWorkloadDemand,
    runner: RunnerSizingEvidence,
) -> DerivedModelDemand:
    """Derive context, batching, replicas, and memory demand or refuse safely."""
    if not isinstance(variant, ModelVariantEvidence):
        raise ValueError("variant must be ModelVariantEvidence")
    if not isinstance(workload, InferenceWorkloadDemand):
        raise ValueError("workload must be InferenceWorkloadDemand")
    if not isinstance(runner, RunnerSizingEvidence):
        raise ValueError("runner must be RunnerSizingEvidence")

    context_tokens = workload.input_tokens + workload.output_tokens
    _refuse_unmet_evidence(variant, workload, runner, context_tokens)
    batch_size = min(
        workload.concurrent_sequences,
        runner.max_batch_size_per_replica,
    )
    replicas = math.ceil(workload.concurrent_sequences / batch_size)
    if replicas > runner.max_data_parallel_replicas:
        raise DemandDerivationError("replica_limit_exceeded")
    topology_demand = ModelRunnerDemand(
        model_id=variant.model_id,
        allowed_device_kinds=variant.allowed_device_kinds,
        weight_bytes=variant.weight_bytes,
        runtime_overhead_bytes=variant.runtime_overhead_bytes,
        kv_cache_bytes_per_token=variant.kv_cache_bytes_per_token,
        context_tokens=context_tokens,
        concurrent_sequences=batch_size,
        data_parallel_replicas=replicas,
        tensor_parallel_divisor=variant.tensor_parallel_divisor,
        allow_shared_accelerator=workload.allow_shared_accelerator,
    )
    return DerivedModelDemand(
        variant_id=variant.variant_id,
        runner_id=runner.runner_id,
        architecture=variant.architecture,
        quantization=variant.quantization,
        quality_millis=variant.quality_millis,
        context_tokens=context_tokens,
        output_tokens=workload.output_tokens,
        batch_size_per_replica=batch_size,
        data_parallel_replicas=replicas,
        topology_demand=topology_demand,
    )


__all__ = (
    "DemandDerivationError",
    "DerivedModelDemand",
    "InferenceWorkloadDemand",
    "ModelVariantEvidence",
    "RunnerSizingEvidence",
    "derive_model_runner_demand",
)
