"""Provider-neutral accelerator topology and model-runner right-sizing.

This module extends the discovered-resource contract from
``hardware.accelerator_types`` through structural composition.  It does not
probe hardware, name SKUs, or provision anything.  Provider and runner adapters
declare measured topology/capability facts; the pure planner either returns a
bounded placement or fails closed.
"""

from __future__ import annotations

import enum
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast, runtime_checkable

_GIB = 1024**3
_MAX_TEXT = 512
_MAX_DEVICES = 100_000
_MAX_HOSTS = 10_000
_MAX_BYTES = (1 << 63) - 1
_MAX_MICROUSD = 10**15
_MAX_CONTEXT_TOKENS = 100_000_000
_MAX_SEQUENCES = 1_000_000


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT:
        raise ValueError(f"{field_name} must be bounded non-empty text")
    if any(delimiter in value for delimiter in "\x00\r\n"):
        raise ValueError(f"{field_name} must not contain control delimiters")
    return value


def _enum_text(value: object, field_name: str) -> str:
    raw = value.value if isinstance(value, enum.Enum) else value
    return _require_text(raw, field_name).casefold()


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


def _validate_text_set(values: object, field_name: str) -> frozenset[str]:
    if not isinstance(values, frozenset) or not values:
        raise ValueError(f"{field_name} must be a non-empty frozenset")
    for value in values:
        _require_text(value, field_name)
    return values


def _validate_size_set(values: object, field_name: str) -> frozenset[int]:
    if not isinstance(values, frozenset) or not values:
        raise ValueError(f"{field_name} must be a non-empty frozenset")
    for value in values:
        _bounded_int(value, field_name, minimum=1, maximum=_MAX_DEVICES)
    return values


class PartitioningMode(enum.StrEnum):
    """Isolation semantics of each allocatable accelerator unit."""

    WHOLE_DEVICE = "whole_device"
    HARDWARE_PARTITION = "hardware_partition"
    SHARED = "shared"


class DistributionMode(enum.StrEnum):
    """How a model runner consumes multiple accelerator units."""

    SINGLE_DEVICE = "single_device"
    EXPLICIT_PARALLEL = "explicit_parallel"
    RUNNER_MANAGED = "runner_managed"


class PlanningEvent(enum.StrEnum):
    """Content-free placement events suitable for telemetry."""

    PLANNING_STARTED = "planning_started"
    CANDIDATE_REJECTED = "candidate_rejected"
    CANDIDATE_ACCEPTED = "candidate_accepted"
    PLANNING_COMPLETED = "planning_completed"
    PLANNING_FAILED = "planning_failed"


@runtime_checkable
class DiscoveredAccelerator(Protocol):
    """Read-only subset of S83.159's ``AcceleratorResource`` contract."""

    @property
    def kind(self) -> object:
        """Return the discovered accelerator kind value."""
        ...

    @property
    def location(self) -> object:
        """Return the discovered locality value."""
        ...

    @property
    def backend(self) -> str:
        """Return the discovered runtime backend."""
        ...

    @property
    def model(self) -> str:
        """Return the discovered hardware model."""
        ...

    @property
    def vendor(self) -> str:
        """Return the discovered hardware vendor."""
        ...

    @property
    def resource_key(self) -> str:
        """Return the discovery resource identity."""
        ...

    @property
    def total_count(self) -> int:
        """Return the total discovered device count."""
        ...

    @property
    def available_count(self) -> int:
        """Return the currently available device count."""
        ...

    @property
    def memory_gb(self) -> float | None:
        """Return memory per device in GiB when known."""
        ...

    @property
    def source(self) -> str:
        """Return the discovery evidence source."""
        ...


def _memory_bytes(resource: DiscoveredAccelerator) -> int | None:
    memory_gb = resource.memory_gb
    if memory_gb is None:
        return None
    if isinstance(memory_gb, bool) or not isinstance(memory_gb, int | float):
        raise ValueError("resource.memory_gb must be numeric or None")
    if not math.isfinite(float(memory_gb)) or float(memory_gb) <= 0:
        raise ValueError("resource.memory_gb must be finite and positive")
    memory_bytes = int(float(memory_gb) * _GIB)
    return _bounded_int(
        memory_bytes,
        "resource.memory_gb",
        minimum=1,
        maximum=_MAX_BYTES,
    )


@dataclass(frozen=True, slots=True)
class AcceleratorCost:
    """Indivisible hourly billing unit reported by a provider adapter."""

    hourly_microusd_per_unit: int
    devices_per_unit: int

    def __post_init__(self) -> None:
        """Require finite non-negative price and a bounded allocation size."""
        _bounded_int(
            self.hourly_microusd_per_unit,
            "hourly_microusd_per_unit",
            minimum=0,
            maximum=_MAX_MICROUSD,
        )
        _bounded_int(
            self.devices_per_unit,
            "devices_per_unit",
            minimum=1,
            maximum=_MAX_DEVICES,
        )


@dataclass(frozen=True, slots=True)
class AcceleratorTopology:
    """Measured topology facts attached to one discovered resource pool.

    ``resource`` remains the source of device identity, availability, memory,
    and discovery locality.  The remaining fields add provider scheduling,
    interconnect, isolation, and billing facts without duplicating probing.
    ``max_devices_per_workload`` expresses platform limits such as a managed
    service exposing one accelerator to each model-server replica.
    """

    resource: DiscoveredAccelerator
    provider: str
    region: str
    zone: str | None
    host_count: int
    devices_per_host: int
    max_devices_per_workload: int
    intra_host_interconnect: str | None
    intra_host_interconnect_group_size: int
    cross_host_interconnect: str | None
    partitioning: PartitioningMode
    memory_isolated: bool
    cost: AcceleratorCost | None
    facts_attested: bool

    def __post_init__(self) -> None:
        """Reject inconsistent, mutable, or unbounded provider facts."""
        if not isinstance(self.resource, DiscoveredAccelerator):
            raise ValueError("resource must satisfy DiscoveredAccelerator")
        for field_name in ("backend", "model", "vendor", "resource_key", "source"):
            _require_text(getattr(self.resource, field_name), f"resource.{field_name}")
        _enum_text(self.resource.kind, "resource.kind")
        _enum_text(self.resource.location, "resource.location")
        total = _bounded_int(
            self.resource.total_count,
            "resource.total_count",
            minimum=1,
            maximum=_MAX_DEVICES,
        )
        available = _bounded_int(
            self.resource.available_count,
            "resource.available_count",
            minimum=0,
            maximum=_MAX_DEVICES,
        )
        if available > total:
            raise ValueError("resource.available_count cannot exceed total_count")
        _memory_bytes(self.resource)
        _require_text(self.provider, "provider")
        _require_text(self.region, "region")
        if self.zone is not None:
            _require_text(self.zone, "zone")
        hosts = _bounded_int(
            self.host_count,
            "host_count",
            minimum=1,
            maximum=_MAX_HOSTS,
        )
        per_host = _bounded_int(
            self.devices_per_host,
            "devices_per_host",
            minimum=1,
            maximum=_MAX_DEVICES,
        )
        platform_limit = _bounded_int(
            self.max_devices_per_workload,
            "max_devices_per_workload",
            minimum=1,
            maximum=_MAX_DEVICES,
        )
        capacity = hosts * per_host
        if available > capacity:
            raise ValueError("available devices exceed declared host topology")
        if platform_limit > capacity:
            raise ValueError("max_devices_per_workload exceeds host topology")
        group_size = _bounded_int(
            self.intra_host_interconnect_group_size,
            "intra_host_interconnect_group_size",
            minimum=1,
            maximum=_MAX_DEVICES,
        )
        if group_size > per_host:
            raise ValueError("interconnect group cannot exceed devices_per_host")
        if self.intra_host_interconnect is None and group_size != 1:
            raise ValueError("unknown intra-host interconnect requires group size one")
        if self.intra_host_interconnect is not None:
            _require_text(self.intra_host_interconnect, "intra_host_interconnect")
        if self.cross_host_interconnect is not None:
            _require_text(self.cross_host_interconnect, "cross_host_interconnect")
        if not isinstance(self.partitioning, PartitioningMode):
            raise ValueError("partitioning must be a PartitioningMode")
        if not isinstance(self.memory_isolated, bool):
            raise ValueError("memory_isolated must be a bool")
        if self.cost is not None and not isinstance(self.cost, AcceleratorCost):
            raise ValueError("cost must be AcceleratorCost or None")
        if not isinstance(self.facts_attested, bool):
            raise ValueError("facts_attested must be a bool")

    @property
    def resource_key(self) -> str:
        """Return the discovered resource identity without copying it."""
        return self.resource.resource_key

    @property
    def device_kind(self) -> str:
        """Return normalized discovered device kind."""
        return _enum_text(self.resource.kind, "resource.kind")

    @property
    def location(self) -> str:
        """Return normalized discovery locality."""
        return _enum_text(self.resource.location, "resource.location")

    @property
    def memory_bytes_per_device(self) -> int | None:
        """Return binary bytes derived from discovered per-unit memory."""
        return _memory_bytes(self.resource)

    def to_dict(self) -> dict[str, object]:
        """Return the exact provider-neutral topology evidence schema."""
        return {
            "schema_version": 1,
            "resource_key": self.resource_key,
            "kind": self.device_kind,
            "location": self.location,
            "backend": self.resource.backend,
            "model": self.resource.model,
            "vendor": self.resource.vendor,
            "provider": self.provider,
            "region": self.region,
            "zone": self.zone,
            "available_devices": self.resource.available_count,
            "memory_bytes_per_device": self.memory_bytes_per_device,
            "host_count": self.host_count,
            "devices_per_host": self.devices_per_host,
            "max_devices_per_workload": self.max_devices_per_workload,
            "intra_host_interconnect": self.intra_host_interconnect,
            "intra_host_interconnect_group_size": (
                self.intra_host_interconnect_group_size
            ),
            "cross_host_interconnect": self.cross_host_interconnect,
            "partitioning": self.partitioning.value,
            "memory_isolated": self.memory_isolated,
            "cost": (
                {
                    "hourly_microusd_per_unit": self.cost.hourly_microusd_per_unit,
                    "devices_per_unit": self.cost.devices_per_unit,
                }
                if self.cost is not None
                else None
            ),
            "facts_attested": self.facts_attested,
        }


@dataclass(frozen=True, slots=True)
class RunnerCapabilities:
    """Empirically discovered limits for one model-server implementation."""

    runner_id: str
    supported_device_kinds: frozenset[str]
    supported_backends: frozenset[str]
    supported_partitioning: frozenset[PartitioningMode]
    distribution_mode: DistributionMode
    supported_model_parallel_sizes: frozenset[int]
    supported_tensor_parallel_sizes: frozenset[int]
    supported_pipeline_parallel_sizes: frozenset[int]
    tensor_parallel_interconnects: frozenset[str]
    pipeline_parallel_interconnects: frozenset[str]
    cross_host_interconnects: frozenset[str]
    max_hosts: int
    max_data_parallel_replicas: int

    def __post_init__(self) -> None:
        """Require explicit immutable capability evidence."""
        _require_text(self.runner_id, "runner_id")
        _validate_text_set(self.supported_device_kinds, "supported_device_kinds")
        _validate_text_set(self.supported_backends, "supported_backends")
        if not isinstance(self.supported_partitioning, frozenset) or not self.supported_partitioning:
            raise ValueError("supported_partitioning must be a non-empty frozenset")
        if any(
            not isinstance(item, PartitioningMode)
            for item in self.supported_partitioning
        ):
            raise ValueError("supported_partitioning has an invalid value")
        if not isinstance(self.distribution_mode, DistributionMode):
            raise ValueError("distribution_mode must be a DistributionMode")
        model_sizes = _validate_size_set(
            self.supported_model_parallel_sizes,
            "supported_model_parallel_sizes",
        )
        tensor_sizes = _validate_size_set(
            self.supported_tensor_parallel_sizes,
            "supported_tensor_parallel_sizes",
        )
        pipeline_sizes = _validate_size_set(
            self.supported_pipeline_parallel_sizes,
            "supported_pipeline_parallel_sizes",
        )
        for field_name in (
            "tensor_parallel_interconnects",
            "pipeline_parallel_interconnects",
            "cross_host_interconnects",
        ):
            values = getattr(self, field_name)
            if not isinstance(values, frozenset):
                raise ValueError(f"{field_name} must be a frozenset")
            for value in values:
                _require_text(value, field_name)
        _bounded_int(self.max_hosts, "max_hosts", minimum=1, maximum=_MAX_HOSTS)
        _bounded_int(
            self.max_data_parallel_replicas,
            "max_data_parallel_replicas",
            minimum=1,
            maximum=_MAX_DEVICES,
        )
        if self.distribution_mode is DistributionMode.SINGLE_DEVICE and (
            model_sizes != frozenset({1})
            or tensor_sizes != frozenset({1})
            or pipeline_sizes != frozenset({1})
        ):
            raise ValueError("single-device runners may only declare parallel size one")
        if self.distribution_mode is DistributionMode.RUNNER_MANAGED and (
            tensor_sizes != frozenset({1}) or pipeline_sizes != frozenset({1})
        ):
            raise ValueError("runner-managed distribution cannot declare explicit TP/PP sizes")


@dataclass(frozen=True, slots=True)
class ModelRunnerDemand:
    """Artifact-derived model memory and parallelism demand."""

    model_id: str
    allowed_device_kinds: frozenset[str]
    weight_bytes: int
    runtime_overhead_bytes: int
    kv_cache_bytes_per_token: int
    context_tokens: int
    concurrent_sequences: int
    data_parallel_replicas: int
    tensor_parallel_divisor: int
    allow_shared_accelerator: bool = False

    def __post_init__(self) -> None:
        """Reject missing or unbounded model metadata."""
        _require_text(self.model_id, "model_id")
        _validate_text_set(self.allowed_device_kinds, "allowed_device_kinds")
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
            self.context_tokens,
            "context_tokens",
            minimum=1,
            maximum=_MAX_CONTEXT_TOKENS,
        )
        _bounded_int(
            self.concurrent_sequences,
            "concurrent_sequences",
            minimum=1,
            maximum=_MAX_SEQUENCES,
        )
        _bounded_int(
            self.data_parallel_replicas,
            "data_parallel_replicas",
            minimum=1,
            maximum=_MAX_DEVICES,
        )
        _bounded_int(
            self.tensor_parallel_divisor,
            "tensor_parallel_divisor",
            minimum=1,
            maximum=_MAX_DEVICES,
        )
        if not isinstance(self.allow_shared_accelerator, bool):
            raise ValueError("allow_shared_accelerator must be a bool")
        replicated = self.replicated_bytes_per_device
        if replicated > _MAX_BYTES:
            raise ValueError("replicated model memory exceeds its bounded range")

    @property
    def replicated_bytes_per_device(self) -> int:
        """Return runtime plus requested KV-cache bytes replicated per rank."""
        return self.runtime_overhead_bytes + (
            self.kv_cache_bytes_per_token
            * self.context_tokens
            * self.concurrent_sequences
        )

    def required_bytes_per_device(self, model_parallel_devices: int) -> int:
        """Conservatively estimate peak bytes after even weight sharding."""
        devices = _bounded_int(
            model_parallel_devices,
            "model_parallel_devices",
            minimum=1,
            maximum=_MAX_DEVICES,
        )
        sharded_weights = (self.weight_bytes + devices - 1) // devices
        required = sharded_weights + self.replicated_bytes_per_device
        if required > _MAX_BYTES:
            raise ValueError("per-device model memory exceeds its bounded range")
        return required


@dataclass(frozen=True, slots=True)
class TopologyConstraints:
    """User-owned placement, cost, and memory safety ceilings."""

    allowed_providers: frozenset[str]
    allowed_regions: frozenset[str]
    max_hourly_cost_microusd: int
    max_total_devices: int
    memory_reserve_millis: int = 150

    def __post_init__(self) -> None:
        """Require explicit finite policy instead of permissive defaults."""
        _validate_text_set(self.allowed_providers, "allowed_providers")
        _validate_text_set(self.allowed_regions, "allowed_regions")
        _bounded_int(
            self.max_hourly_cost_microusd,
            "max_hourly_cost_microusd",
            minimum=1,
            maximum=_MAX_MICROUSD,
        )
        _bounded_int(
            self.max_total_devices,
            "max_total_devices",
            minimum=1,
            maximum=_MAX_DEVICES,
        )
        _bounded_int(
            self.memory_reserve_millis,
            "memory_reserve_millis",
            minimum=50,
            maximum=500,
        )


@dataclass(frozen=True, slots=True)
class TopologyPlan:
    """Auditable desired placement; creating resources is a separate step."""

    resource_key: str
    provider: str
    region: str
    device_kind: str
    device_vendor: str
    device_model: str
    runner_id: str
    partitioning: PartitioningMode
    distribution_mode: DistributionMode
    model_parallel_devices: int
    data_parallel_replicas: int
    tensor_parallel_size: int | None
    pipeline_parallel_size: int | None
    host_count: int
    total_devices: int
    per_device_required_bytes: int
    per_device_usable_bytes: int
    billable_allocation_units: int
    estimated_hourly_cost_microusd: int

    def to_dict(self) -> dict[str, object]:
        """Return exact desired-state data for providers and runner adapters."""
        return {
            "schema_version": 1,
            "resource_key": self.resource_key,
            "provider": self.provider,
            "region": self.region,
            "device_kind": self.device_kind,
            "device_vendor": self.device_vendor,
            "device_model": self.device_model,
            "runner_id": self.runner_id,
            "partitioning": self.partitioning.value,
            "distribution_mode": self.distribution_mode.value,
            "model_parallel_devices": self.model_parallel_devices,
            "data_parallel_replicas": self.data_parallel_replicas,
            "tensor_parallel_size": self.tensor_parallel_size,
            "pipeline_parallel_size": self.pipeline_parallel_size,
            "host_count": self.host_count,
            "total_devices": self.total_devices,
            "per_device_required_bytes": self.per_device_required_bytes,
            "per_device_usable_bytes": self.per_device_usable_bytes,
            "billable_allocation_units": self.billable_allocation_units,
            "estimated_hourly_cost_microusd": (
                self.estimated_hourly_cost_microusd
            ),
        }


@dataclass(frozen=True, slots=True)
class TopologyTrace:
    """Bounded content-free progress; identities never enter telemetry."""

    event: PlanningEvent
    candidate_count: int
    feasible_count: int = 0
    rejected_count: int = 0
    reason_code: str | None = None

    def __post_init__(self) -> None:
        """Validate safe event shape and bounded counters."""
        if not isinstance(self.event, PlanningEvent):
            raise ValueError("event must be a PlanningEvent")
        for field_name in ("candidate_count", "feasible_count", "rejected_count"):
            _bounded_int(
                getattr(self, field_name),
                field_name,
                minimum=0,
                maximum=_MAX_DEVICES,
            )
        if self.reason_code is not None:
            _require_text(self.reason_code, "reason_code")


class TopologyPlanningError(ValueError):
    """No candidate satisfied every attested capability and user ceiling."""

    def __init__(self, reason_codes: tuple[str, ...]) -> None:
        """Initialize the error with stable, deduplicated refusal reasons."""
        unique = tuple(sorted(set(reason_codes)))
        if not unique:
            unique = ("no_feasible_topology",)
        self.reason_codes = unique
        super().__init__("no feasible accelerator topology: " + ", ".join(unique))


@dataclass(frozen=True, slots=True)
class _Candidate:
    plan: TopologyPlan
    memory_waste_bytes: int


def _base_rejections(
    demand: ModelRunnerDemand,
    pool: AcceleratorTopology,
    runner: RunnerCapabilities,
    constraints: TopologyConstraints,
) -> set[str]:
    reasons: set[str] = set()
    if not pool.facts_attested:
        reasons.add("unattested_facts")
    if pool.provider not in constraints.allowed_providers:
        reasons.add("provider_forbidden")
    if pool.region not in constraints.allowed_regions:
        reasons.add("region_forbidden")
    if pool.device_kind not in demand.allowed_device_kinds:
        reasons.add("model_device_kind_unsupported")
    if pool.device_kind not in runner.supported_device_kinds:
        reasons.add("runner_device_kind_unsupported")
    if pool.resource.backend not in runner.supported_backends:
        reasons.add("runner_backend_unsupported")
    if pool.partitioning not in runner.supported_partitioning:
        reasons.add("runner_partitioning_unsupported")
    if pool.partitioning is PartitioningMode.SHARED and not demand.allow_shared_accelerator:
        reasons.add("shared_accelerator_forbidden")
    if not pool.memory_isolated:
        reasons.add("partition_memory_not_isolated")
    if pool.memory_bytes_per_device is None:
        reasons.add("unknown_memory")
    if pool.cost is None:
        reasons.add("unknown_cost")
    if demand.data_parallel_replicas > runner.max_data_parallel_replicas:
        reasons.add("data_parallel_unsupported")
    return reasons


def _explicit_configurations(
    demand: ModelRunnerDemand,
    pool: AcceleratorTopology,
    runner: RunnerCapabilities,
) -> tuple[tuple[tuple[int, int, int], ...], set[str]]:
    configurations: list[tuple[int, int, int]] = []
    reasons: set[str] = set()
    for model_size in sorted(runner.supported_model_parallel_sizes):
        if model_size > pool.max_devices_per_workload:
            reasons.add("platform_device_limit")
        matched_product = False
        for tensor_size in sorted(runner.supported_tensor_parallel_sizes, reverse=True):
            for pipeline_size in sorted(runner.supported_pipeline_parallel_sizes):
                if tensor_size * pipeline_size != model_size:
                    continue
                matched_product = True
                if demand.tensor_parallel_divisor % tensor_size:
                    reasons.add("tensor_parallel_divisor_mismatch")
                    continue
                if tensor_size > 1 and (
                    tensor_size > pool.devices_per_host
                    or tensor_size > pool.intra_host_interconnect_group_size
                    or pool.intra_host_interconnect
                    not in runner.tensor_parallel_interconnects
                ):
                    reasons.add("tensor_parallel_interconnect_unsupported")
                    continue
                hosts = math.ceil(model_size / pool.devices_per_host)
                if pipeline_size > 1:
                    link = (
                        pool.intra_host_interconnect
                        if hosts == 1
                        else pool.cross_host_interconnect
                    )
                    if hosts > 1 and (
                        link is None or link not in runner.cross_host_interconnects
                    ):
                        reasons.add("cross_host_interconnect_unsupported")
                        continue
                    if link not in runner.pipeline_parallel_interconnects:
                        reasons.add("pipeline_parallel_interconnect_unsupported")
                        continue
                configurations.append((model_size, tensor_size, pipeline_size))
        if not matched_product:
            reasons.add("parallel_size_combination_unsupported")
    return tuple(configurations), reasons


def _configurations(
    demand: ModelRunnerDemand,
    pool: AcceleratorTopology,
    runner: RunnerCapabilities,
) -> tuple[tuple[tuple[int, int | None, int | None], ...], set[str]]:
    if runner.distribution_mode is DistributionMode.SINGLE_DEVICE:
        return ((1, None, None),), set()
    if runner.distribution_mode is DistributionMode.RUNNER_MANAGED:
        return (
            tuple(
                (size, None, None)
                for size in sorted(runner.supported_model_parallel_sizes)
            ),
            set(),
        )
    explicit, reasons = _explicit_configurations(demand, pool, runner)
    return tuple(explicit), reasons


def _configuration_candidate(
    demand: ModelRunnerDemand,
    pool: AcceleratorTopology,
    runner: RunnerCapabilities,
    constraints: TopologyConstraints,
    configuration: tuple[int, int | None, int | None],
) -> tuple[_Candidate | None, set[str]]:
    model_devices, tensor_size, pipeline_size = configuration
    reasons: set[str] = set()
    if model_devices > pool.max_devices_per_workload:
        reasons.add("platform_device_limit")
    total_devices = model_devices * demand.data_parallel_replicas
    if total_devices > pool.resource.available_count:
        reasons.add("insufficient_available_devices")
    if total_devices > constraints.max_total_devices:
        reasons.add("device_budget_exceeded")
    hosts = math.ceil(model_devices / pool.devices_per_host)
    if hosts > pool.host_count or hosts > runner.max_hosts:
        reasons.add("host_limit_exceeded")
    if hosts > 1 and (
        pool.cross_host_interconnect is None
        or pool.cross_host_interconnect not in runner.cross_host_interconnects
    ):
        reasons.add("cross_host_interconnect_unsupported")
    memory = cast(int, pool.memory_bytes_per_device)
    usable = memory * (1000 - constraints.memory_reserve_millis) // 1000
    required = demand.required_bytes_per_device(model_devices)
    if required > usable:
        reasons.add("insufficient_memory")
    cost = cast(AcceleratorCost, pool.cost)
    units_per_replica = math.ceil(model_devices / cost.devices_per_unit)
    billable_units = units_per_replica * demand.data_parallel_replicas
    hourly_cost = billable_units * cost.hourly_microusd_per_unit
    if hourly_cost > constraints.max_hourly_cost_microusd:
        reasons.add("hourly_budget_exceeded")
    if reasons:
        return None, reasons
    return (
        _Candidate(
            plan=TopologyPlan(
                resource_key=pool.resource_key,
                provider=pool.provider,
                region=pool.region,
                device_kind=pool.device_kind,
                device_vendor=pool.resource.vendor,
                device_model=pool.resource.model,
                runner_id=runner.runner_id,
                partitioning=pool.partitioning,
                distribution_mode=runner.distribution_mode,
                model_parallel_devices=model_devices,
                data_parallel_replicas=demand.data_parallel_replicas,
                tensor_parallel_size=tensor_size,
                pipeline_parallel_size=pipeline_size,
                host_count=hosts,
                total_devices=total_devices,
                per_device_required_bytes=required,
                per_device_usable_bytes=usable,
                billable_allocation_units=billable_units,
                estimated_hourly_cost_microusd=hourly_cost,
            ),
            memory_waste_bytes=usable - required,
        ),
        reasons,
    )


def _pair_candidates(
    demand: ModelRunnerDemand,
    pool: AcceleratorTopology,
    runner: RunnerCapabilities,
    constraints: TopologyConstraints,
) -> tuple[tuple[_Candidate, ...], set[str]]:
    reasons = _base_rejections(demand, pool, runner, constraints)
    if reasons:
        return (), reasons
    configurations, configuration_reasons = _configurations(demand, pool, runner)
    reasons.update(configuration_reasons)
    candidates: list[_Candidate] = []
    for configuration in configurations:
        candidate, rejected = _configuration_candidate(
            demand,
            pool,
            runner,
            constraints,
            configuration,
        )
        reasons.update(rejected)
        if candidate is not None:
            candidates.append(candidate)
    return tuple(candidates), reasons


def _candidate_rank(candidate: _Candidate) -> tuple[object, ...]:
    plan = candidate.plan
    return (
        plan.estimated_hourly_cost_microusd,
        plan.total_devices,
        candidate.memory_waste_bytes,
        plan.billable_allocation_units,
        -(plan.tensor_parallel_size or 0),
        plan.resource_key,
        plan.runner_id,
    )


def _validate_planning_inputs(
    demand: object,
    pools: object,
    runners: object,
    constraints: object,
    trace_sink: object,
) -> None:
    """Reject malformed topology-planning inputs before emitting progress."""
    if not isinstance(demand, ModelRunnerDemand):
        raise ValueError("demand must be ModelRunnerDemand")
    if not isinstance(pools, tuple) or any(
        not isinstance(pool, AcceleratorTopology) for pool in pools
    ):
        raise ValueError("pools must be an AcceleratorTopology tuple")
    if not isinstance(runners, tuple) or any(
        not isinstance(runner, RunnerCapabilities) for runner in runners
    ):
        raise ValueError("runners must be a RunnerCapabilities tuple")
    if not isinstance(constraints, TopologyConstraints):
        raise ValueError("constraints must be TopologyConstraints")
    if trace_sink is not None and not callable(trace_sink):
        raise ValueError("trace_sink must be callable")


def plan_model_runner_topology(
    *,
    demand: ModelRunnerDemand,
    pools: tuple[AcceleratorTopology, ...],
    runners: tuple[RunnerCapabilities, ...],
    constraints: TopologyConstraints,
    trace_sink: Callable[[TopologyTrace], None] | None = None,
) -> TopologyPlan:
    """Choose the least-cost right-sized attested topology or fail closed.

    The function is side-effect free except for bounded progress delivery.  A
    returned plan is desired state only, which lets callers create a replacement,
    attest it, switch traffic, and then retire the prior deployment (ZDD).
    """
    _validate_planning_inputs(demand, pools, runners, constraints, trace_sink)

    sink = trace_sink or (lambda _trace: None)
    candidate_count = len(pools) * len(runners)
    sink(TopologyTrace(PlanningEvent.PLANNING_STARTED, candidate_count))
    if not pools:
        sink(
            TopologyTrace(
                PlanningEvent.PLANNING_FAILED,
                candidate_count,
                rejected_count=0,
                reason_code="empty_inventory",
            )
        )
        raise TopologyPlanningError(("empty_inventory",))
    if not runners:
        sink(
            TopologyTrace(
                PlanningEvent.PLANNING_FAILED,
                candidate_count,
                rejected_count=0,
                reason_code="empty_runner_inventory",
            )
        )
        raise TopologyPlanningError(("empty_runner_inventory",))

    candidates: list[_Candidate] = []
    all_reasons: set[str] = set()
    accepted_pairs = 0
    rejected_pairs = 0
    for pool in pools:
        for runner in runners:
            pair_candidates, reasons = _pair_candidates(
                demand,
                pool,
                runner,
                constraints,
            )
            all_reasons.update(reasons)
            if pair_candidates:
                accepted_pairs += 1
                candidates.extend(pair_candidates)
                sink(
                    TopologyTrace(
                        PlanningEvent.CANDIDATE_ACCEPTED,
                        candidate_count,
                        feasible_count=accepted_pairs,
                        rejected_count=rejected_pairs,
                    )
                )
            else:
                rejected_pairs += 1
                sink(
                    TopologyTrace(
                        PlanningEvent.CANDIDATE_REJECTED,
                        candidate_count,
                        feasible_count=accepted_pairs,
                        rejected_count=rejected_pairs,
                        reason_code=min(reasons or {"no_feasible_topology"}),
                    )
                )
    if not candidates:
        sink(
            TopologyTrace(
                PlanningEvent.PLANNING_FAILED,
                candidate_count,
                rejected_count=rejected_pairs,
                reason_code=min(all_reasons or {"no_feasible_topology"}),
            )
        )
        raise TopologyPlanningError(tuple(all_reasons))
    selected = min(candidates, key=_candidate_rank).plan
    sink(
        TopologyTrace(
            PlanningEvent.PLANNING_COMPLETED,
            candidate_count,
            feasible_count=accepted_pairs,
            rejected_count=rejected_pairs,
        )
    )
    return selected


__all__ = (
    "AcceleratorCost",
    "AcceleratorTopology",
    "DiscoveredAccelerator",
    "DistributionMode",
    "ModelRunnerDemand",
    "PartitioningMode",
    "PlanningEvent",
    "RunnerCapabilities",
    "TopologyConstraints",
    "TopologyPlan",
    "TopologyPlanningError",
    "TopologyTrace",
    "plan_model_runner_topology",
)
