"""Read-only Azure GPU VM inventory and Container Apps versus VM selection.

The inventory adapter consumes only maintained Azure Compute SDK list
operations, an exact bounded Retail Prices resolver, and the versioned
operational-availability channel.  It never provisions or mutates Azure.
Hardware names are provider data rather than routing keys.
"""

from __future__ import annotations

import enum
import math
import os
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast, runtime_checkable

from general_ludd.hardware.accelerator_topology import (
    AcceleratorCost,
    AcceleratorTopology,
    ModelRunnerDemand,
    PartitioningMode,
    RunnerCapabilities,
    TopologyConstraints,
    TopologyPlan,
    TopologyPlanningError,
    plan_model_runner_topology,
)
from general_ludd.hardware.accelerator_types import (
    AcceleratorKind,
    AcceleratorLocation,
    AcceleratorResource,
)
from general_ludd.infra.azure_retail_pricing import (
    AzureContainerAppsRetailPricing,
    AzureRetailMeter,
)
from general_ludd.self_improve.azure_operational_availability import (
    AzureAvailabilityAssessment,
    AzureAvailabilityIndex,
    AzureAvailabilityScope,
)

_MAX_TEXT = 512
_MAX_DEVICES = 100_000
_MAX_HOSTS = 10_000
_MAX_MICROUSD = 10**15
_REGION_RE = re.compile(r"[a-z][a-z0-9-]{1,31}")
_RUNTIME_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_TOPOLOGY_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_REGIONAL_QUOTA_NAMES = frozenset(
    {
        "cores",
        "totalregionalcores",
        "totalregionalvcpus",
    }
)


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be bounded non-empty text")
    cleaned = value.strip()
    if (
        not cleaned
        or len(cleaned) > _MAX_TEXT
        or any(character in cleaned for character in "\x00\r\n")
    ):
        raise ValueError(f"{field_name} must be bounded non-empty text")
    return cleaned


def _normalized(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _sequence(value: object) -> tuple[object, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(value)


def _string_sequence(value: object) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in _sequence(value)
        if isinstance(item, str) and item.strip()
    )


def _finite_nonnegative(value: object, field_name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise ValueError(f"{field_name} must be finite and non-negative")
    return float(value)


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


class AzureExecutionStrategy(enum.StrEnum):
    """Azure execution surfaces understood by the strategy boundary."""

    CONTAINER_APPS = "container_apps"
    SINGLE_VM = "single_vm"
    VMSS = "vmss"


class AzureStrategyEvent(enum.StrEnum):
    """Content-free inventory and selection progress events."""

    INVENTORY_STARTED = "inventory_started"
    INVENTORY_CANDIDATE_ACCEPTED = "inventory_candidate_accepted"
    INVENTORY_CANDIDATE_REJECTED = "inventory_candidate_rejected"
    INVENTORY_COMPLETED = "inventory_completed"
    STRATEGY_STARTED = "strategy_started"
    STRATEGY_OPTION_REJECTED = "strategy_option_rejected"
    STRATEGY_SELECTED = "strategy_selected"
    STRATEGY_FAILED = "strategy_failed"


@dataclass(frozen=True, slots=True)
class AzureStrategyTrace:
    """Bounded counts and reason codes without workload or Azure identity."""

    event: AzureStrategyEvent
    candidate_count: int
    accepted_count: int = 0
    rejected_count: int = 0
    reason_code: str | None = None
    strategy: AzureExecutionStrategy | None = None

    def __post_init__(self) -> None:
        """Validate the censored trace envelope."""
        if not isinstance(self.event, AzureStrategyEvent):
            raise ValueError("event must be an AzureStrategyEvent")
        for field_name in ("candidate_count", "accepted_count", "rejected_count"):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= _MAX_DEVICES
            ):
                raise ValueError(f"{field_name} is outside its bounded range")
        if self.reason_code is not None:
            _text(self.reason_code, "reason_code")
        if self.strategy is not None and not isinstance(
            self.strategy, AzureExecutionStrategy
        ):
            raise ValueError("strategy must be an AzureExecutionStrategy")


class AzureGpuVmInventoryError(ValueError):
    """No live VM SKU had complete, current, attested feasibility evidence."""

    def __init__(self, reason_codes: Iterable[str]) -> None:
        """Initialize with stable censored refusal reasons."""
        self.reason_codes = tuple(sorted(set(reason_codes))) or (
            "empty_gpu_inventory",
        )
        super().__init__("Azure GPU VM inventory unavailable: " + ", ".join(self.reason_codes))


class AzureStrategySelectionError(ValueError):
    """No Azure execution surface passed every hard and evidence gate."""

    def __init__(self, reason_codes: Iterable[str]) -> None:
        """Initialize with stable censored refusal reasons."""
        self.reason_codes = tuple(sorted(set(reason_codes))) or (
            "no_eligible_azure_strategy",
        )
        super().__init__("Azure execution strategy unavailable: " + ", ".join(self.reason_codes))


@runtime_checkable
class _AzureCapability(Protocol):
    name: str | None
    value: str | None


@runtime_checkable
class _AzureLocationInfo(Protocol):
    location: str | None
    zones: Sequence[str] | None


@runtime_checkable
class _AzureRestrictionInfo(Protocol):
    locations: Sequence[str] | None
    zones: Sequence[str] | None


@runtime_checkable
class _AzureRestriction(Protocol):
    type: str | None
    values: Sequence[str] | None
    restriction_info: _AzureRestrictionInfo | None
    reason_code: str | None


@runtime_checkable
class _AzureResourceSku(Protocol):
    name: str | None
    resource_type: str | None
    locations: Sequence[str] | None
    location_info: Sequence[_AzureLocationInfo] | None
    restrictions: Sequence[_AzureRestriction] | None
    capabilities: Sequence[_AzureCapability] | None
    family: str | None


@runtime_checkable
class _AzureUsageName(Protocol):
    value: str | None
    localized_value: str | None


@runtime_checkable
class _AzureUsage(Protocol):
    name: _AzureUsageName
    current_value: int | None
    limit: int | None


class _AzureResourceSkuOperations(Protocol):
    def list(self) -> Iterable[_AzureResourceSku]: ...


class _AzureUsageOperations(Protocol):
    def list(self, location: str) -> Iterable[_AzureUsage]: ...


class AzureGpuVmComputeClient(Protocol):
    """Read-only subset of ``ComputeManagementClient`` used by inventory."""

    resource_skus: _AzureResourceSkuOperations
    usage: _AzureUsageOperations


class AzureArmSkuPriceResolver(Protocol):
    """Bounded exact ARM-SKU retail-price resolver."""

    def resolve_virtual_machine_arm_sku_meter(
        self,
        *,
        region: str,
        arm_sku_name: str,
    ) -> AzureRetailMeter:
        """Return one exact current on-demand Linux meter."""
        ...


@dataclass(frozen=True, slots=True)
class AzureGpuVmCandidate:
    """One normalized VM SKU with current admission and topology evidence."""

    arm_sku_name: str
    region: str
    zones: tuple[str, ...]
    quota_family: str
    family_quota_remaining: int
    regional_quota_remaining: int
    available_hosts: int
    price_meter_id: str
    price_fetched_at: datetime
    observed_at: datetime
    availability: AzureAvailabilityAssessment
    topology: AcceleratorTopology

    def __post_init__(self) -> None:
        """Reject incomplete or inconsistent candidate evidence."""
        _text(self.arm_sku_name, "arm_sku_name")
        _text(self.region, "region")
        _text(self.quota_family, "quota_family")
        _text(self.price_meter_id, "price_meter_id")
        _aware_utc(self.price_fetched_at, "price_fetched_at")
        _aware_utc(self.observed_at, "observed_at")
        if not isinstance(self.zones, tuple) or tuple(sorted(set(self.zones))) != self.zones:
            raise ValueError("zones must be a sorted unique tuple")
        for zone in self.zones:
            _text(zone, "zone")
        for field_name in (
            "family_quota_remaining",
            "regional_quota_remaining",
            "available_hosts",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")
        if not isinstance(self.availability, AzureAvailabilityAssessment):
            raise ValueError("availability must be an AzureAvailabilityAssessment")
        if not isinstance(self.topology, AcceleratorTopology):
            raise ValueError("topology must be an AcceleratorTopology")


@dataclass(frozen=True, slots=True)
class AzureGpuVmInventorySnapshot:
    """One current read-only inventory observation."""

    region: str
    observed_at: datetime
    candidates: tuple[AzureGpuVmCandidate, ...]
    rejected_count: int
    rejection_reasons: tuple[str, ...]


def _capability_map(sku: _AzureResourceSku) -> dict[str, str]:
    capabilities: dict[str, str] = {}
    for raw in _sequence(getattr(sku, "capabilities", None)):
        name = _normalized(getattr(raw, "name", None))
        value = getattr(raw, "value", None)
        if not name or not isinstance(value, str) or not value.strip():
            continue
        cleaned = value.strip()
        existing = capabilities.get(name)
        if existing is not None and existing != cleaned:
            raise ValueError("duplicate_capability")
        capabilities[name] = cleaned
    return capabilities


def _capability_value(
    capabilities: dict[str, str],
    *names: str,
) -> str | None:
    for name in names:
        value = capabilities.get(_normalized(name))
        if value is not None:
            return value
    return None


def _positive_int_capability(
    capabilities: dict[str, str],
    reason: str,
    *names: str,
) -> int:
    raw = _capability_value(capabilities, *names)
    try:
        value = int(raw or "")
    except ValueError as exc:
        raise ValueError(reason) from exc
    if value <= 0 or value > _MAX_DEVICES:
        raise ValueError(reason)
    return value


def _positive_float_capability(
    capabilities: dict[str, str],
    reason: str,
    *names: str,
) -> float:
    raw = _capability_value(capabilities, *names)
    try:
        value = float(raw or "")
    except ValueError as exc:
        raise ValueError(reason) from exc
    if not math.isfinite(value) or value <= 0 or value > 1_000_000:
        raise ValueError(reason)
    return value


def _region_zones(sku: _AzureResourceSku, region: str) -> tuple[str, ...]:
    offered = {_normalized(value) for value in _string_sequence(sku.locations)}
    if _normalized(region) not in offered:
        raise ValueError("region_not_offered")
    zones: set[str] = set()
    for info in _sequence(getattr(sku, "location_info", None)):
        if _normalized(getattr(info, "location", None)) != _normalized(region):
            continue
        zones.update(_string_sequence(getattr(info, "zones", None)))
    return tuple(sorted(zones))


def _apply_restrictions(
    sku: _AzureResourceSku,
    region: str,
    zones: tuple[str, ...],
) -> tuple[str, ...]:
    remaining = set(zones)
    for restriction in _sequence(getattr(sku, "restrictions", None)):
        restriction_type = _normalized(getattr(restriction, "type", None))
        values = _string_sequence(getattr(restriction, "values", None))
        info = getattr(restriction, "restriction_info", None)
        locations = values if restriction_type == "location" else ()
        restricted_zones = values if restriction_type == "zone" else ()
        if info is not None:
            locations += _string_sequence(getattr(info, "locations", None))
            restricted_zones += _string_sequence(getattr(info, "zones", None))
        applies = not locations or _normalized(region) in {
            _normalized(location) for location in locations
        }
        if restriction_type == "location" and applies:
            raise ValueError("location_restricted")
        if restriction_type == "zone" and applies:
            remaining.difference_update(restricted_zones)
    if zones and not remaining:
        raise ValueError("zones_restricted")
    return tuple(sorted(remaining))


def _usage_name(usage: _AzureUsage) -> str:
    name = getattr(usage, "name", None)
    return _normalized(
        getattr(name, "value", None) or getattr(name, "localized_value", None)
    )


def _quota_remaining(usages: tuple[_AzureUsage, ...], names: set[str]) -> int | None:
    normalized_names = {_normalized(name) for name in names}
    for usage in usages:
        if _usage_name(usage) not in normalized_names:
            continue
        current = getattr(usage, "current_value", None)
        limit = getattr(usage, "limit", None)
        if (
            isinstance(current, bool)
            or isinstance(limit, bool)
            or not isinstance(current, int)
            or not isinstance(limit, int)
            or current < 0
            or limit < 0
        ):
            return None
        return max(limit - current, 0)
    return None


def _assessment_reason(
    assessment: AzureAvailabilityAssessment,
    *,
    now_epoch: float,
    max_age_seconds: float,
) -> str | None:
    if assessment.observed_outcomes == 0 or assessment.last_observed_at is None:
        return "missing_capacity_evidence"
    age = now_epoch - assessment.last_observed_at
    if not math.isfinite(age) or age < 0:
        return "invalid_capacity_timestamp"
    if age > max_age_seconds:
        return "stale_capacity_evidence"
    if not assessment.feasible or assessment.successful_outcomes == 0:
        return "capacity_unavailable"
    return None


class AzureGpuVmInventory:
    """Normalize current Azure SDK inventory without creating resources."""

    def __init__(
        self,
        *,
        compute_client: AzureGpuVmComputeClient,
        price_resolver: AzureArmSkuPriceResolver,
        availability_index: AzureAvailabilityIndex,
        clock: Callable[[], datetime] | None = None,
        max_price_age_seconds: float = 3600,
        max_capacity_age_seconds: float = 3600,
    ) -> None:
        """Bind read-only SDK operations and finite evidence horizons."""
        if not isinstance(availability_index, AzureAvailabilityIndex):
            raise ValueError("availability_index must be an AzureAvailabilityIndex")
        self._compute = compute_client
        self._pricing = price_resolver
        self._availability = availability_index
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_price_age_seconds = _finite_nonnegative(
            max_price_age_seconds, "max_price_age_seconds"
        )
        self._max_capacity_age_seconds = _finite_nonnegative(
            max_capacity_age_seconds, "max_capacity_age_seconds"
        )
        if self._max_price_age_seconds == 0 or self._max_capacity_age_seconds == 0:
            raise ValueError("evidence horizons must be positive")

    def discover(
        self,
        *,
        region: str,
        runtime_version_digest: str,
        topology_digest: str,
        trace_sink: Callable[[AzureStrategyTrace], None] | None = None,
    ) -> AzureGpuVmInventorySnapshot:
        """Return current eligible GPU VM topologies or fail closed."""
        normalized_region = _text(region, "region").casefold()
        if _REGION_RE.fullmatch(normalized_region) is None:
            raise ValueError("region is invalid")
        if _RUNTIME_DIGEST_RE.fullmatch(runtime_version_digest) is None:
            raise ValueError("runtime_version_digest is invalid")
        if _TOPOLOGY_DIGEST_RE.fullmatch(topology_digest) is None:
            raise ValueError("topology_digest is invalid")
        if trace_sink is not None and not callable(trace_sink):
            raise ValueError("trace_sink must be callable")
        now = _aware_utc(self._clock(), "clock")
        sink = trace_sink or (lambda _trace: None)
        sink(AzureStrategyTrace(AzureStrategyEvent.INVENTORY_STARTED, 0))
        try:
            skus = tuple(self._compute.resource_skus.list())
            usages = tuple(self._compute.usage.list(normalized_region))
        except Exception:
            sink(
                AzureStrategyTrace(
                    AzureStrategyEvent.INVENTORY_COMPLETED,
                    0,
                    reason_code="sdk_inventory_unavailable",
                )
            )
            raise AzureGpuVmInventoryError(("sdk_inventory_unavailable",)) from None
        if len(skus) > _MAX_DEVICES:
            raise AzureGpuVmInventoryError(("inventory_too_large",))
        accepted: list[AzureGpuVmCandidate] = []
        reasons: list[str] = []
        rejected = 0
        for sku in skus:
            candidate, reason = self._candidate(
                sku=sku,
                usages=usages,
                region=normalized_region,
                runtime_version_digest=runtime_version_digest,
                topology_digest=topology_digest,
                now=now,
            )
            if candidate is not None:
                accepted.append(candidate)
                sink(
                    AzureStrategyTrace(
                        AzureStrategyEvent.INVENTORY_CANDIDATE_ACCEPTED,
                        len(skus),
                        accepted_count=len(accepted),
                        rejected_count=rejected,
                    )
                )
                continue
            if reason == "not_gpu_sku":
                continue
            rejected += 1
            reasons.append(reason or "invalid_sku_evidence")
            sink(
                AzureStrategyTrace(
                    AzureStrategyEvent.INVENTORY_CANDIDATE_REJECTED,
                    len(skus),
                    accepted_count=len(accepted),
                    rejected_count=rejected,
                    reason_code=reason or "invalid_sku_evidence",
                )
            )
        accepted.sort(
            key=lambda item: (
                item.topology.cost.hourly_microusd_per_unit
                if item.topology.cost
                else _MAX_MICROUSD,
                item.arm_sku_name,
            )
        )
        if not accepted:
            sink(
                AzureStrategyTrace(
                    AzureStrategyEvent.INVENTORY_COMPLETED,
                    len(skus),
                    rejected_count=rejected,
                    reason_code=min(reasons or ["empty_gpu_inventory"]),
                )
            )
            raise AzureGpuVmInventoryError(reasons)
        sink(
            AzureStrategyTrace(
                AzureStrategyEvent.INVENTORY_COMPLETED,
                len(skus),
                accepted_count=len(accepted),
                rejected_count=rejected,
            )
        )
        return AzureGpuVmInventorySnapshot(
            region=normalized_region,
            observed_at=now,
            candidates=tuple(accepted),
            rejected_count=rejected,
            rejection_reasons=tuple(sorted(set(reasons))),
        )

    def _candidate(
        self,
        *,
        sku: _AzureResourceSku,
        usages: tuple[_AzureUsage, ...],
        region: str,
        runtime_version_digest: str,
        topology_digest: str,
        now: datetime,
    ) -> tuple[AzureGpuVmCandidate | None, str | None]:
        try:
            capabilities = _capability_map(sku)
            if _capability_value(capabilities, "GPUs", "GpuCount") is None:
                return None, "not_gpu_sku"
            if _normalized(getattr(sku, "resource_type", None)) != "virtualmachines":
                return None, "not_virtual_machine"
            arm_sku_name = _text(getattr(sku, "name", None), "arm_sku_name")
            zones = _apply_restrictions(sku, region, _region_zones(sku, region))
            gpu_count = _positive_int_capability(
                capabilities, "invalid_gpu_count", "GPUs", "GpuCount"
            )
            gpu_memory_gib = _positive_float_capability(
                capabilities,
                "invalid_gpu_memory",
                "GpuMemoryGB",
                "GpuVramGB",
                "MemoryPerGpuInGB",
            )
            vcpus = _positive_int_capability(
                capabilities, "invalid_vcpu_count", "vCPUs", "vCpuCount"
            )
            backend = _capability_value(capabilities, "GpuBackend")
            if backend is None:
                raise ValueError("missing_gpu_backend")
            vendor = _capability_value(capabilities, "GpuVendor")
            if vendor is None:
                raise ValueError("missing_gpu_vendor")
            family = _text(getattr(sku, "family", None), "quota_family")
            family_remaining = _quota_remaining(usages, {family})
            if family_remaining is None:
                raise ValueError("missing_family_quota")
            regional_remaining = _quota_remaining(usages, set(_REGIONAL_QUOTA_NAMES))
            if regional_remaining is None:
                raise ValueError("missing_regional_quota")
            if family_remaining < vcpus:
                raise ValueError("insufficient_family_quota")
            if regional_remaining < vcpus:
                raise ValueError("insufficient_regional_quota")
            available_hosts = min(
                family_remaining // vcpus,
                regional_remaining // vcpus,
                _MAX_HOSTS,
                _MAX_DEVICES // gpu_count,
            )
            if available_hosts <= 0:
                raise ValueError("insufficient_quota")
            scope = AzureAvailabilityScope(
                location=region,
                resource_sku=arm_sku_name,
                runtime_version_digest=runtime_version_digest,
                topology_digest=topology_digest,
            )
            assessment = self._availability.assess(scope)
            capacity_reason = _assessment_reason(
                assessment,
                now_epoch=now.timestamp(),
                max_age_seconds=self._max_capacity_age_seconds,
            )
            if capacity_reason is not None:
                raise ValueError(capacity_reason)
            try:
                meter = self._pricing.resolve_virtual_machine_arm_sku_meter(
                    region=region,
                    arm_sku_name=arm_sku_name,
                )
            except Exception:
                raise ValueError("price_unavailable") from None
            if (
                meter.region != region
                or meter.sku_name != arm_sku_name
                or meter.price_type != "Consumption"
                or meter.unit_of_measure != "1 Hour"
                or not math.isfinite(meter.retail_price)
                or meter.retail_price <= 0
            ):
                raise ValueError("invalid_price")
            price_time = _aware_utc(meter.fetched_at, "price_fetched_at")
            price_age = (now - price_time).total_seconds()
            if price_age < 0:
                raise ValueError("invalid_price_timestamp")
            if price_age > self._max_price_age_seconds:
                raise ValueError("stale_price")
            hourly_microusd = round(meter.retail_price * 1_000_000)
            if not 0 < hourly_microusd <= _MAX_MICROUSD:
                raise ValueError("invalid_price")
            interconnect = _capability_value(
                capabilities, "IntraHostGpuInterconnect"
            )
            if interconnect is None:
                group_size = 1
            else:
                group_size = _positive_int_capability(
                    capabilities,
                    "invalid_interconnect_group",
                    "IntraHostGpuInterconnectGroupSize",
                )
                if group_size > gpu_count:
                    raise ValueError("invalid_interconnect_group")
            available_devices = available_hosts * gpu_count
            resource = AcceleratorResource(
                kind=AcceleratorKind.GPU,
                location=AcceleratorLocation.CLOUD,
                backend=_text(backend, "gpu_backend"),
                model=arm_sku_name,
                vendor=_text(vendor, "gpu_vendor"),
                resource_key=f"azure:{region}:{arm_sku_name}",
                total_count=available_devices,
                available_count=available_devices,
                memory_gb=gpu_memory_gib,
                source="azure_compute_resource_skus",
            )
            topology = AcceleratorTopology(
                resource=resource,
                provider="azure",
                region=region,
                zone=zones[0] if len(zones) == 1 else None,
                host_count=available_hosts,
                devices_per_host=gpu_count,
                max_devices_per_workload=gpu_count,
                intra_host_interconnect=interconnect,
                intra_host_interconnect_group_size=group_size,
                cross_host_interconnect=None,
                partitioning=PartitioningMode.WHOLE_DEVICE,
                memory_isolated=True,
                cost=AcceleratorCost(hourly_microusd, gpu_count),
                facts_attested=True,
            )
            return (
                AzureGpuVmCandidate(
                    arm_sku_name=arm_sku_name,
                    region=region,
                    zones=zones,
                    quota_family=family,
                    family_quota_remaining=family_remaining,
                    regional_quota_remaining=regional_remaining,
                    available_hosts=available_hosts,
                    price_meter_id=meter.meter_id,
                    price_fetched_at=price_time,
                    observed_at=now,
                    availability=assessment,
                    topology=topology,
                ),
                None,
            )
        except ValueError as exc:
            reason = str(exc)
            allowed = {
                "capacity_unavailable",
                "duplicate_capability",
                "insufficient_family_quota",
                "insufficient_quota",
                "insufficient_regional_quota",
                "invalid_capacity_timestamp",
                "invalid_gpu_count",
                "invalid_gpu_memory",
                "invalid_interconnect_group",
                "invalid_price",
                "invalid_price_timestamp",
                "invalid_vcpu_count",
                "location_restricted",
                "missing_capacity_evidence",
                "missing_family_quota",
                "missing_gpu_backend",
                "missing_gpu_vendor",
                "missing_regional_quota",
                "not_virtual_machine",
                "price_unavailable",
                "region_not_offered",
                "stale_capacity_evidence",
                "stale_price",
                "zones_restricted",
            }
            return None, reason if reason in allowed else "invalid_sku_evidence"


@dataclass(frozen=True, slots=True)
class AzureVmssEvidence:
    """Independent evidence required before a later VMSS strategy can qualify."""

    replica_semantics_attested: bool
    high_availability_attested: bool
    rdma_topology_attested: bool

    def __post_init__(self) -> None:
        """Require explicit booleans for every VMSS prerequisite."""
        for field_name in (
            "replica_semantics_attested",
            "high_availability_attested",
            "rdma_topology_attested",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be a bool")

    @property
    def eligible(self) -> bool:
        """Return whether every VMSS prerequisite is independently attested."""
        return (
            self.replica_semantics_attested
            and self.high_availability_attested
            and self.rdma_topology_attested
        )


@dataclass(frozen=True, slots=True)
class AzureExecutionOption:
    """Measured infrastructure option after model identity has been fixed."""

    strategy: AzureExecutionStrategy
    topology: AcceleratorTopology
    runner: RunnerCapabilities
    measured_startup_seconds: float
    measured_throughput_units_per_second: float
    price_fetched_at: datetime
    infrastructure_observed_at: datetime
    availability: AzureAvailabilityAssessment
    runtime_supported: bool
    privacy_allowed: bool
    operator_approved: bool
    identity_attested: bool
    vmss_evidence: AzureVmssEvidence | None = None

    def __post_init__(self) -> None:
        """Reject mutable, missing, or inferred option evidence."""
        if not isinstance(self.strategy, AzureExecutionStrategy):
            raise ValueError("strategy must be an AzureExecutionStrategy")
        if not isinstance(self.topology, AcceleratorTopology):
            raise ValueError("topology must be an AcceleratorTopology")
        if not isinstance(self.runner, RunnerCapabilities):
            raise ValueError("runner must be RunnerCapabilities")
        _finite_nonnegative(
            self.measured_startup_seconds, "measured_startup_seconds"
        )
        throughput = _finite_nonnegative(
            self.measured_throughput_units_per_second,
            "measured_throughput_units_per_second",
        )
        if throughput == 0:
            raise ValueError("measured_throughput_units_per_second must be positive")
        _aware_utc(self.price_fetched_at, "price_fetched_at")
        _aware_utc(self.infrastructure_observed_at, "infrastructure_observed_at")
        if not isinstance(self.availability, AzureAvailabilityAssessment):
            raise ValueError("availability must be an AzureAvailabilityAssessment")
        for field_name in (
            "runtime_supported",
            "privacy_allowed",
            "operator_approved",
            "identity_attested",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be a bool")
        if self.vmss_evidence is not None and not isinstance(
            self.vmss_evidence, AzureVmssEvidence
        ):
            raise ValueError("vmss_evidence must be AzureVmssEvidence or None")


@dataclass(frozen=True, slots=True)
class AzureStrategyDecision:
    """One desired execution surface; resource creation remains separate."""

    strategy: AzureExecutionStrategy
    topology_plan: TopologyPlan
    projected_cost_microusd: int
    projected_completion_millis: int
    reason_code: str


@dataclass(frozen=True, slots=True)
class _FeasibleOption:
    option: AzureExecutionOption
    plan: TopologyPlan
    projected_cost_microusd: int
    completion_seconds: float


def _option_hard_rejections(
    option: AzureExecutionOption,
    *,
    now: datetime,
    max_price_age_seconds: float,
    max_capacity_age_seconds: float,
    max_infrastructure_age_seconds: float,
) -> set[str]:
    reasons: set[str] = set()
    if not option.privacy_allowed:
        reasons.add("privacy_forbidden")
    if not option.operator_approved:
        reasons.add("approval_missing")
    if not option.identity_attested:
        reasons.add("identity_unattested")
    if not option.runtime_supported:
        reasons.add("runtime_unsupported")
    if not option.topology.facts_attested:
        reasons.add("topology_unattested")
    price_age = (now - option.price_fetched_at.astimezone(UTC)).total_seconds()
    if not math.isfinite(price_age) or price_age < 0:
        reasons.add("price_timestamp_invalid")
    elif price_age > max_price_age_seconds:
        reasons.add("price_stale")
    infrastructure_age = (
        now - option.infrastructure_observed_at.astimezone(UTC)
    ).total_seconds()
    if not math.isfinite(infrastructure_age) or infrastructure_age < 0:
        reasons.add("infrastructure_timestamp_invalid")
    elif infrastructure_age > max_infrastructure_age_seconds:
        reasons.add("infrastructure_evidence_stale")
    assessment = option.availability
    if assessment.observed_outcomes == 0 or assessment.last_observed_at is None:
        reasons.add("capacity_evidence_missing")
    else:
        capacity_age = now.timestamp() - assessment.last_observed_at
        if not math.isfinite(capacity_age) or capacity_age < 0:
            reasons.add("capacity_timestamp_invalid")
        elif capacity_age > max_capacity_age_seconds:
            reasons.add("capacity_evidence_stale")
        if not assessment.feasible or assessment.successful_outcomes == 0:
            reasons.add("capacity_unavailable")
    if option.strategy is AzureExecutionStrategy.CONTAINER_APPS and (
        option.topology.partitioning is not PartitioningMode.WHOLE_DEVICE
        or option.topology.devices_per_host != 1
        or option.topology.max_devices_per_workload != 1
    ):
        reasons.add("container_apps_not_single_whole_gpu")
    if option.strategy is AzureExecutionStrategy.VMSS:
        if option.vmss_evidence is None or not option.vmss_evidence.eligible:
            reasons.add("vmss_evidence_missing")
        else:
            reasons.add("vmss_tranche_deferred")
    return reasons


def _feasible_option(
    *,
    option: AzureExecutionOption,
    demand: ModelRunnerDemand,
    constraints: TopologyConstraints,
    work_units: float,
    now: datetime,
    max_price_age_seconds: float,
    max_capacity_age_seconds: float,
    max_infrastructure_age_seconds: float,
) -> tuple[_FeasibleOption | None, set[str]]:
    reasons = _option_hard_rejections(
        option,
        now=now,
        max_price_age_seconds=max_price_age_seconds,
        max_capacity_age_seconds=max_capacity_age_seconds,
        max_infrastructure_age_seconds=max_infrastructure_age_seconds,
    )
    if reasons:
        return None, reasons
    try:
        plan = plan_model_runner_topology(
            demand=demand,
            pools=(option.topology,),
            runners=(option.runner,),
            constraints=constraints,
        )
    except TopologyPlanningError as exc:
        return None, set(exc.reason_codes)
    if (
        option.strategy is AzureExecutionStrategy.CONTAINER_APPS
        and plan.model_parallel_devices != 1
    ):
        return None, {"container_apps_model_not_single_gpu"}
    completion_seconds = option.measured_startup_seconds + (
        work_units / option.measured_throughput_units_per_second
    )
    projected_cost = math.ceil(
        plan.estimated_hourly_cost_microusd * completion_seconds / 3600
    )
    return (
        _FeasibleOption(
            option=option,
            plan=plan,
            projected_cost_microusd=projected_cost,
            completion_seconds=completion_seconds,
        ),
        set(),
    )


def _feasible_rank(candidate: _FeasibleOption) -> tuple[object, ...]:
    return (
        candidate.projected_cost_microusd,
        candidate.completion_seconds,
        candidate.plan.total_devices,
        candidate.plan.resource_key,
    )


def select_azure_execution_strategy(
    *,
    demand: ModelRunnerDemand,
    options: tuple[AzureExecutionOption, ...],
    constraints: TopologyConstraints,
    work_units: float,
    now: datetime,
    max_price_age_seconds: float = 3600,
    max_capacity_age_seconds: float = 3600,
    max_infrastructure_age_seconds: float = 3600,
    trace_sink: Callable[[AzureStrategyTrace], None] | None = None,
) -> AzureStrategyDecision:
    """Choose Container Apps only for a measured win, otherwise one VM.

    Availability is an infrastructure admission signal only.  Privacy,
    approval, immutable identity, topology, and budget gates run before it can
    influence the eligible set, and its numerical score is never used as model
    quality or as a ranking feature.
    """
    if not isinstance(demand, ModelRunnerDemand):
        raise ValueError("demand must be ModelRunnerDemand")
    if not isinstance(options, tuple) or any(
        not isinstance(option, AzureExecutionOption) for option in options
    ):
        raise ValueError("options must be an AzureExecutionOption tuple")
    if not isinstance(constraints, TopologyConstraints):
        raise ValueError("constraints must be TopologyConstraints")
    checked_work = _finite_nonnegative(work_units, "work_units")
    if checked_work == 0:
        raise ValueError("work_units must be positive")
    checked_now = _aware_utc(now, "now")
    price_horizon = _finite_nonnegative(
        max_price_age_seconds, "max_price_age_seconds"
    )
    capacity_horizon = _finite_nonnegative(
        max_capacity_age_seconds, "max_capacity_age_seconds"
    )
    infrastructure_horizon = _finite_nonnegative(
        max_infrastructure_age_seconds, "max_infrastructure_age_seconds"
    )
    if (
        price_horizon == 0
        or capacity_horizon == 0
        or infrastructure_horizon == 0
    ):
        raise ValueError("evidence horizons must be positive")
    if trace_sink is not None and not callable(trace_sink):
        raise ValueError("trace_sink must be callable")
    sink = trace_sink or (lambda _trace: None)
    sink(AzureStrategyTrace(AzureStrategyEvent.STRATEGY_STARTED, len(options)))
    feasible: list[_FeasibleOption] = []
    all_reasons: set[str] = set()
    rejected = 0
    for option in options:
        candidate, reasons = _feasible_option(
            option=option,
            demand=demand,
            constraints=constraints,
            work_units=checked_work,
            now=checked_now,
            max_price_age_seconds=price_horizon,
            max_capacity_age_seconds=capacity_horizon,
            max_infrastructure_age_seconds=infrastructure_horizon,
        )
        if candidate is not None:
            feasible.append(candidate)
            continue
        rejected += 1
        all_reasons.update(reasons)
        sink(
            AzureStrategyTrace(
                AzureStrategyEvent.STRATEGY_OPTION_REJECTED,
                len(options),
                accepted_count=len(feasible),
                rejected_count=rejected,
                reason_code=min(reasons or {"option_ineligible"}),
                strategy=option.strategy,
            )
        )
    single_vms = sorted(
        (
            candidate
            for candidate in feasible
            if candidate.option.strategy is AzureExecutionStrategy.SINGLE_VM
        ),
        key=_feasible_rank,
    )
    container_apps = sorted(
        (
            candidate
            for candidate in feasible
            if candidate.option.strategy is AzureExecutionStrategy.CONTAINER_APPS
        ),
        key=_feasible_rank,
    )
    selected: _FeasibleOption | None = None
    reason_code = ""
    if single_vms:
        selected = single_vms[0]
        reason_code = "single_vm_safe_default"
        if container_apps:
            container = container_apps[0]
            cost_wins = (
                container.projected_cost_microusd
                <= selected.projected_cost_microusd
            )
            time_wins = container.completion_seconds <= selected.completion_seconds
            strict_win = (
                container.projected_cost_microusd
                < selected.projected_cost_microusd
                or container.completion_seconds < selected.completion_seconds
            )
            if cost_wins and time_wins and strict_win:
                selected = container
                reason_code = "container_apps_measured_win"
    elif container_apps:
        all_reasons.add("container_apps_requires_vm_comparator")
    if selected is None:
        sink(
            AzureStrategyTrace(
                AzureStrategyEvent.STRATEGY_FAILED,
                len(options),
                accepted_count=len(feasible),
                rejected_count=rejected,
                reason_code=min(all_reasons or {"no_eligible_azure_strategy"}),
            )
        )
        raise AzureStrategySelectionError(all_reasons)
    sink(
        AzureStrategyTrace(
            AzureStrategyEvent.STRATEGY_SELECTED,
            len(options),
            accepted_count=len(feasible),
            rejected_count=rejected,
            reason_code=reason_code,
            strategy=selected.option.strategy,
        )
    )
    return AzureStrategyDecision(
        strategy=selected.option.strategy,
        topology_plan=selected.plan,
        projected_cost_microusd=selected.projected_cost_microusd,
        projected_completion_millis=math.ceil(selected.completion_seconds * 1000),
        reason_code=reason_code,
    )


def build_default_azure_gpu_vm_inventory(
    *,
    availability_index: AzureAvailabilityIndex,
    subscription_id: str | None = None,
    price_resolver: AzureArmSkuPriceResolver | None = None,
    clock: Callable[[], datetime] | None = None,
    max_price_age_seconds: float = 3600,
    max_capacity_age_seconds: float = 3600,
) -> AzureGpuVmInventory:
    """Build the live read-only inventory from standard Azure SDK credentials."""
    resolved_subscription = (
        subscription_id or os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    ).strip()
    if not resolved_subscription:
        raise ValueError("AZURE_SUBSCRIPTION_ID is required for Azure GPU VM inventory")
    try:
        from azure.identity import DefaultAzureCredential
        from azure.mgmt.compute import ComputeManagementClient
    except ImportError as exc:
        raise RuntimeError(
            "Azure SDK unavailable; install general-ludd-agent[azure]"
        ) from exc
    compute_client = ComputeManagementClient(
        credential=DefaultAzureCredential(),
        subscription_id=resolved_subscription,
    )
    return AzureGpuVmInventory(
        compute_client=cast(AzureGpuVmComputeClient, compute_client),
        price_resolver=price_resolver or AzureContainerAppsRetailPricing(),
        availability_index=availability_index,
        clock=clock,
        max_price_age_seconds=max_price_age_seconds,
        max_capacity_age_seconds=max_capacity_age_seconds,
    )


__all__ = (
    "AzureArmSkuPriceResolver",
    "AzureExecutionOption",
    "AzureExecutionStrategy",
    "AzureGpuVmCandidate",
    "AzureGpuVmComputeClient",
    "AzureGpuVmInventory",
    "AzureGpuVmInventoryError",
    "AzureGpuVmInventorySnapshot",
    "AzureStrategyDecision",
    "AzureStrategyEvent",
    "AzureStrategySelectionError",
    "AzureStrategyTrace",
    "AzureVmssEvidence",
    "build_default_azure_gpu_vm_inventory",
    "select_azure_execution_strategy",
)
