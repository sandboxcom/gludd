"""Unit contracts for live Azure GPU VM inventory and strategy selection."""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import ModuleType
from typing import Any

import pytest

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
    AzureGpuVmInventoryError,
    AzureStrategyEvent,
    AzureStrategySelectionError,
    AzureStrategyTrace,
    AzureVmssEvidence,
    build_default_azure_gpu_vm_inventory,
    select_azure_execution_strategy,
)
from general_ludd.infra.azure_retail_pricing import (
    AzureContainerAppsRetailPricing,
    AzureRetailMeter,
    AzureRetailPricingError,
)
from general_ludd.self_improve.azure_operational_availability import (
    AzureAvailabilityAssessment,
    AzureAvailabilityIndex,
    AzureAvailabilityScope,
)

_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
_RUNTIME_DIGEST = "sha256:" + "a" * 64
_TOPOLOGY_DIGEST = "b" * 64
_VM_SKU = "Standard_FutureGpu_8"
_REGION = "eastus"


@dataclass
class _Capability:
    name: str | None
    value: str | None


@dataclass
class _LocationInfo:
    location: str | None
    zones: list[str] | None


@dataclass
class _RestrictionInfo:
    locations: list[str] | None = None
    zones: list[str] | None = None


@dataclass
class _Restriction:
    type: str | None
    values: list[str] | None = None
    restriction_info: _RestrictionInfo | None = None
    reason_code: str | None = None


@dataclass
class _Sku:
    name: str | None
    resource_type: str | None = "virtualMachines"
    locations: list[str] | None = None
    location_info: list[_LocationInfo] | None = None
    restrictions: list[_Restriction] | None = None
    capabilities: list[_Capability] | None = None
    family: str | None = "standardFutureGpuFamily"


@dataclass
class _UsageName:
    value: str | None
    localized_value: str | None = None


@dataclass
class _Usage:
    name: _UsageName
    current_value: int | None
    limit: int | None


class _SkuOperations:
    def __init__(self, skus: list[_Sku]) -> None:
        self._skus = skus
        self.calls = 0

    def list(self) -> list[_Sku]:
        self.calls += 1
        return self._skus


class _UsageOperations:
    def __init__(self, usages: list[_Usage]) -> None:
        self._usages = usages
        self.calls: list[str] = []

    def list(self, location: str) -> list[_Usage]:
        self.calls.append(location)
        return self._usages


class _ComputeClient:
    def __init__(self, skus: list[_Sku], usages: list[_Usage]) -> None:
        self.resource_skus = _SkuOperations(skus)
        self.usage = _UsageOperations(usages)


class _PriceResolver:
    def __init__(
        self,
        meters: dict[str, AzureRetailMeter],
        failure: Exception | None = None,
    ) -> None:
        self._meters = meters
        self._failure = failure
        self.calls: list[tuple[str, str]] = []

    def resolve_virtual_machine_arm_sku_meter(
        self,
        *,
        region: str,
        arm_sku_name: str,
    ) -> AzureRetailMeter:
        self.calls.append((region, arm_sku_name))
        if self._failure is not None:
            raise self._failure
        return self._meters[arm_sku_name]


def _capabilities(
    *,
    gpu_count: str = "8",
    gpu_memory_gib: str = "192",
    vcpus: str = "96",
    interconnect: bool = True,
) -> list[_Capability]:
    values = [
        _Capability("GPUs", gpu_count),
        _Capability("GpuMemoryGB", gpu_memory_gib),
        _Capability("vCPUs", vcpus),
        _Capability("GpuBackend", "cuda"),
        _Capability("GpuVendor", "future-vendor"),
    ]
    if interconnect:
        values.extend(
            [
                _Capability("IntraHostGpuInterconnect", "fabric-x"),
                _Capability("IntraHostGpuInterconnectGroupSize", gpu_count),
            ]
        )
    return values


def _sku(
    *,
    name: str = _VM_SKU,
    restrictions: list[_Restriction] | None = None,
    capabilities: list[_Capability] | None = None,
) -> _Sku:
    return _Sku(
        name=name,
        locations=[_REGION],
        location_info=[_LocationInfo(_REGION, ["3", "1", "2", "2"])],
        restrictions=restrictions or [],
        capabilities=capabilities or _capabilities(),
    )


def _usages(*, include_family: bool = True, include_regional: bool = True) -> list[_Usage]:
    values: list[_Usage] = []
    if include_family:
        values.append(_Usage(_UsageName("standardFutureGpuFamily"), 0, 192))
    if include_regional:
        values.append(_Usage(_UsageName("Total Regional vCPUs"), 100, 500))
    return values


def _meter(
    *,
    sku: str = _VM_SKU,
    fetched_at: datetime = _NOW,
    price: float = 12.5,
) -> AzureRetailMeter:
    return AzureRetailMeter(
        region=_REGION,
        sku_name=sku,
        price_type="Consumption",
        meter_id=f"meter-{sku}",
        meter_name=f"{sku} Linux",
        retail_price=price,
        unit_of_measure="1 Hour",
        effective_start_date=_NOW - timedelta(days=1),
        fetched_at=fetched_at,
    )


def _assessment(
    sku: str = _VM_SKU,
    *,
    successful: int = 2,
    failed: int = 0,
    feasible: bool = True,
    observed_at: float | None = None,
) -> AzureAvailabilityAssessment:
    scope = AzureAvailabilityScope(
        location=_REGION,
        resource_sku=sku,
        runtime_version_digest=_RUNTIME_DIGEST,
        topology_digest=_TOPOLOGY_DIGEST,
    )
    observed = successful + failed
    return AzureAvailabilityAssessment(
        scope_digest=scope.scope_digest,
        observed_outcomes=observed,
        successful_outcomes=successful,
        failed_outcomes=failed,
        consecutive_failures=failed,
        availability_score=(successful + 1) / (observed + 2),
        feasible=feasible,
        last_observed_at=(
            _NOW.timestamp() - 10 if observed_at is None else observed_at
        ),
    )


def _inventory(
    *,
    skus: list[_Sku] | None = None,
    usages: list[_Usage] | None = None,
    assessments: tuple[AzureAvailabilityAssessment, ...] | None = None,
    meters: dict[str, AzureRetailMeter] | None = None,
    price_failure: Exception | None = None,
) -> tuple[AzureGpuVmInventory, _ComputeClient, _PriceResolver]:
    selected_skus = skus or [_sku()]
    compute = _ComputeClient(selected_skus, usages or _usages())
    prices = _PriceResolver(
        meters or {str(item.name): _meter(sku=str(item.name)) for item in selected_skus},
        failure=price_failure,
    )
    inventory = AzureGpuVmInventory(
        compute_client=compute,
        price_resolver=prices,
        availability_index=AzureAvailabilityIndex(
            assessments=(_assessment(),) if assessments is None else assessments
        ),
        clock=lambda: _NOW,
        max_price_age_seconds=3600,
        max_capacity_age_seconds=3600,
    )
    return inventory, compute, prices


def _discover(inventory: AzureGpuVmInventory, traces: list[AzureStrategyTrace] | None = None):
    return inventory.discover(
        region=_REGION,
        runtime_version_digest=_RUNTIME_DIGEST,
        topology_digest=_TOPOLOGY_DIGEST,
        trace_sink=None if traces is None else traces.append,
    )


def test_live_inventory_normalizes_future_sku_without_hardware_name_keys() -> None:
    inventory, compute, prices = _inventory()

    snapshot = _discover(inventory)

    assert compute.resource_skus.calls == 1
    assert compute.usage.calls == [_REGION]
    assert prices.calls == [(_REGION, _VM_SKU)]
    assert snapshot.region == _REGION
    assert snapshot.observed_at == _NOW
    assert snapshot.rejected_count == 0
    assert len(snapshot.candidates) == 1
    candidate = snapshot.candidates[0]
    assert candidate.arm_sku_name == _VM_SKU
    assert candidate.observed_at == _NOW
    assert candidate.zones == ("1", "2", "3")
    assert candidate.family_quota_remaining == 192
    assert candidate.regional_quota_remaining == 400
    assert candidate.available_hosts == 2
    assert candidate.availability.successful_outcomes == 2
    assert candidate.topology.provider == "azure"
    assert candidate.topology.resource.available_count == 16
    assert candidate.topology.devices_per_host == 8
    assert candidate.topology.max_devices_per_workload == 8
    assert candidate.topology.memory_bytes_per_device == 192 * 1024**3
    assert candidate.topology.intra_host_interconnect == "fabric-x"
    assert candidate.topology.intra_host_interconnect_group_size == 8
    assert candidate.topology.cost == AcceleratorCost(12_500_000, 8)
    assert candidate.topology.facts_attested is True
    assert candidate.topology.resource.model == _VM_SKU


def test_inventory_normalizes_zone_and_location_restrictions() -> None:
    accepted = _sku(
        restrictions=[
            _Restriction(
                "Zone",
                restriction_info=_RestrictionInfo(locations=[_REGION], zones=["2"]),
                reason_code="NotAvailableForSubscription",
            )
        ]
    )
    blocked_name = "Standard_FutureGpu_Blocked"
    blocked = _sku(
        name=blocked_name,
        restrictions=[
            _Restriction(
                "Location",
                values=[_REGION.upper()],
                reason_code="NotAvailableForSubscription",
            )
        ],
    )
    inventory, _, _ = _inventory(
        skus=[accepted, blocked],
        assessments=(_assessment(), _assessment(blocked_name)),
        meters={_VM_SKU: _meter(), blocked_name: _meter(sku=blocked_name)},
    )

    snapshot = _discover(inventory)

    assert snapshot.candidates[0].zones == ("1", "3")
    assert snapshot.rejected_count == 1
    assert snapshot.rejection_reasons == ("location_restricted",)


@pytest.mark.parametrize(
    ("capabilities", "reason"),
    [
        (_capabilities(gpu_count="0"), "invalid_gpu_count"),
        (_capabilities(gpu_memory_gib="unknown"), "invalid_gpu_memory"),
        (_capabilities(vcpus="0"), "invalid_vcpu_count"),
        (
            [item for item in _capabilities() if item.name != "GpuBackend"],
            "missing_gpu_backend",
        ),
        (
            [item for item in _capabilities() if item.name != "GpuVendor"],
            "missing_gpu_vendor",
        ),
    ],
)
def test_inventory_fails_closed_on_incomplete_topology(
    capabilities: list[_Capability],
    reason: str,
) -> None:
    inventory, _, _ = _inventory(skus=[_sku(capabilities=capabilities)])

    with pytest.raises(AzureGpuVmInventoryError) as exc_info:
        _discover(inventory)

    assert reason in exc_info.value.reason_codes


@pytest.mark.parametrize(
    ("usages", "reason"),
    [
        (_usages(include_family=False), "missing_family_quota"),
        (_usages(include_regional=False), "missing_regional_quota"),
        (
            [
                _Usage(_UsageName("standardFutureGpuFamily"), 96, 96),
                _Usage(_UsageName("Total Regional vCPUs"), 0, 500),
            ],
            "insufficient_family_quota",
        ),
    ],
)
def test_inventory_fails_closed_on_missing_or_exhausted_quota(
    usages: list[_Usage],
    reason: str,
) -> None:
    inventory, _, _ = _inventory(usages=usages)

    with pytest.raises(AzureGpuVmInventoryError) as exc_info:
        _discover(inventory)

    assert reason in exc_info.value.reason_codes


def test_inventory_fails_closed_on_missing_negative_or_stale_capacity() -> None:
    cases = (
        ((), "missing_capacity_evidence"),
        ((_assessment(successful=0, failed=3, feasible=False),), "capacity_unavailable"),
        (
            (
                _assessment(
                    observed_at=_NOW.timestamp() - 3601,
                ),
            ),
            "stale_capacity_evidence",
        ),
    )
    for assessments, reason in cases:
        inventory, _, _ = _inventory(assessments=assessments)
        with pytest.raises(AzureGpuVmInventoryError) as exc_info:
            _discover(inventory)
        assert reason in exc_info.value.reason_codes


def test_inventory_fails_closed_on_stale_missing_or_invalid_price() -> None:
    stale, _, _ = _inventory(
        meters={_VM_SKU: _meter(fetched_at=_NOW - timedelta(seconds=3601))}
    )
    with pytest.raises(AzureGpuVmInventoryError) as stale_error:
        _discover(stale)
    assert "stale_price" in stale_error.value.reason_codes

    missing, _, _ = _inventory(
        price_failure=AzureRetailPricingError("provider detail must be censored")
    )
    with pytest.raises(AzureGpuVmInventoryError) as missing_error:
        _discover(missing)
    assert missing_error.value.reason_codes == ("price_unavailable",)
    assert "provider detail" not in str(missing_error.value)

    invalid, _, _ = _inventory(meters={_VM_SKU: _meter(price=float("nan"))})
    with pytest.raises(AzureGpuVmInventoryError) as invalid_error:
        _discover(invalid)
    assert "invalid_price" in invalid_error.value.reason_codes


def test_inventory_traces_are_bounded_and_content_free() -> None:
    inventory, _, _ = _inventory()
    traces: list[AzureStrategyTrace] = []

    _discover(inventory, traces)

    assert [trace.event.value for trace in traces] == [
        "inventory_started",
        "inventory_candidate_accepted",
        "inventory_completed",
    ]
    rendered = repr(traces)
    assert _VM_SKU not in rendered
    assert "future-vendor" not in rendered
    assert _RUNTIME_DIGEST not in rendered
    assert "meter-" not in rendered


def test_inventory_censors_compute_sdk_failures() -> None:
    class BrokenSkus:
        def list(self) -> list[object]:
            raise RuntimeError("provider-secret-detail")

    compute = _ComputeClient([], [])
    compute.resource_skus = BrokenSkus()
    inventory = AzureGpuVmInventory(
        compute_client=compute,
        price_resolver=_PriceResolver({}),
        availability_index=AzureAvailabilityIndex(()),
        clock=lambda: _NOW,
    )

    with pytest.raises(AzureGpuVmInventoryError) as exc_info:
        _discover(inventory)

    assert exc_info.value.reason_codes == ("sdk_inventory_unavailable",)
    assert "provider-secret-detail" not in str(exc_info.value)


def test_inventory_public_boundaries_fail_closed() -> None:
    inventory, _, _ = _inventory()
    with pytest.raises(ValueError, match="availability_index"):
        AzureGpuVmInventory(
            compute_client=object(),
            price_resolver=object(),
            availability_index=object(),
        )
    with pytest.raises(ValueError, match="positive"):
        AzureGpuVmInventory(
            compute_client=object(),
            price_resolver=object(),
            availability_index=AzureAvailabilityIndex(()),
            max_price_age_seconds=0,
        )
    for arguments, match in (
        ({"region": "x'"}, "region"),
        ({"runtime_version_digest": "mutable"}, "runtime_version_digest"),
        ({"topology_digest": "short"}, "topology_digest"),
        ({"trace_sink": "not-callable"}, "trace_sink"),
    ):
        values: dict[str, object] = {
            "region": _REGION,
            "runtime_version_digest": _RUNTIME_DIGEST,
            "topology_digest": _TOPOLOGY_DIGEST,
        }
        values.update(arguments)
        with pytest.raises(ValueError, match=match):
            inventory.discover(**values)


def test_default_inventory_uses_official_compute_sdk_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, object] = {}

    class Credential:
        pass

    class ComputeClient(_ComputeClient):
        def __init__(self, *, credential: object, subscription_id: str) -> None:
            created["credential"] = credential
            created["subscription_id"] = subscription_id
            base = _ComputeClient([_sku()], _usages())
            self.resource_skus = base.resource_skus
            self.usage = base.usage

    azure_module = ModuleType("azure")
    identity_module = ModuleType("azure.identity")
    identity_module.DefaultAzureCredential = Credential
    management_module = ModuleType("azure.mgmt")
    compute_module = ModuleType("azure.mgmt.compute")
    compute_module.ComputeManagementClient = ComputeClient
    azure_module.identity = identity_module
    azure_module.mgmt = management_module
    management_module.compute = compute_module
    monkeypatch.setitem(sys.modules, "azure", azure_module)
    monkeypatch.setitem(sys.modules, "azure.identity", identity_module)
    monkeypatch.setitem(sys.modules, "azure.mgmt", management_module)
    monkeypatch.setitem(sys.modules, "azure.mgmt.compute", compute_module)
    built = build_default_azure_gpu_vm_inventory(
        subscription_id="subscription-id",
        availability_index=AzureAvailabilityIndex((_assessment(),)),
        price_resolver=_PriceResolver({_VM_SKU: _meter()}),
        clock=lambda: _NOW,
    )

    assert _discover(built).candidates[0].arm_sku_name == _VM_SKU
    assert isinstance(created["credential"], Credential)
    assert created["subscription_id"] == "subscription-id"

    monkeypatch.delenv("AZURE_SUBSCRIPTION_ID", raising=False)
    with pytest.raises(ValueError, match="AZURE_SUBSCRIPTION_ID"):
        build_default_azure_gpu_vm_inventory(
            availability_index=AzureAvailabilityIndex(()),
            price_resolver=_PriceResolver({}),
        )


def test_inventory_classifies_non_gpu_non_vm_region_and_zone_refusals() -> None:
    non_gpu = _sku(capabilities=[_Capability("vCPUs", "4")])
    non_vm = _sku()
    non_vm.resource_type = "containers"
    wrong_region = _sku()
    wrong_region.locations = ["northcentralus"]
    zones_blocked = _sku(
        restrictions=[
            _Restriction(
                "Zone",
                values=["1", "2", "3"],
                restriction_info=_RestrictionInfo(locations=[_REGION]),
            )
        ]
    )
    cases = (
        ([non_gpu], "empty_gpu_inventory"),
        ([non_vm], "not_virtual_machine"),
        ([wrong_region], "region_not_offered"),
        ([zones_blocked], "zones_restricted"),
    )
    for skus, reason in cases:
        inventory, _, _ = _inventory(skus=skus)
        with pytest.raises(AzureGpuVmInventoryError) as exc_info:
            _discover(inventory)
        assert reason in exc_info.value.reason_codes


def test_inventory_rejects_duplicate_capability_and_bad_interconnect_group() -> None:
    duplicate = _capabilities()
    duplicate.append(_Capability("GPUs", "4"))
    too_wide = _capabilities(gpu_count="2")
    too_wide[-1] = _Capability("IntraHostGpuInterconnectGroupSize", "4")
    for capabilities, reason in (
        (duplicate, "duplicate_capability"),
        (too_wide, "invalid_interconnect_group"),
    ):
        inventory, _, _ = _inventory(skus=[_sku(capabilities=capabilities)])
        with pytest.raises(AzureGpuVmInventoryError) as exc_info:
            _discover(inventory)
        assert reason in exc_info.value.reason_codes


def test_inventory_accepts_single_gpu_without_interconnect_or_zones() -> None:
    candidate_sku = _sku(capabilities=_capabilities(gpu_count="1", interconnect=False))
    candidate_sku.location_info = []
    inventory, _, _ = _inventory(skus=[candidate_sku])

    candidate = _discover(inventory).candidates[0]

    assert candidate.zones == ()
    assert candidate.topology.intra_host_interconnect is None
    assert candidate.topology.intra_host_interconnect_group_size == 1


def test_inventory_rejects_future_capacity_price_and_regional_exhaustion() -> None:
    future_capacity, _, _ = _inventory(
        assessments=(_assessment(observed_at=_NOW.timestamp() + 1),)
    )
    future_price, _, _ = _inventory(
        meters={_VM_SKU: _meter(fetched_at=_NOW + timedelta(seconds=1))}
    )
    regional_exhausted, _, _ = _inventory(
        usages=[
            _Usage(_UsageName("standardFutureGpuFamily"), 0, 192),
            _Usage(_UsageName("Total Regional vCPUs"), 500, 500),
        ]
    )
    for inventory, reason in (
        (future_capacity, "invalid_capacity_timestamp"),
        (future_price, "invalid_price_timestamp"),
        (regional_exhausted, "insufficient_regional_quota"),
    ):
        with pytest.raises(AzureGpuVmInventoryError) as exc_info:
            _discover(inventory)
        assert reason in exc_info.value.reason_codes


def test_trace_and_candidate_value_objects_validate_every_boundary() -> None:
    inventory, _, _ = _inventory()
    candidate = _discover(inventory).candidates[0]
    with pytest.raises(ValueError, match="event"):
        AzureStrategyTrace("bad", 1)
    with pytest.raises(ValueError, match="candidate_count"):
        AzureStrategyTrace(AzureStrategyEvent.INVENTORY_STARTED, True)
    with pytest.raises(ValueError, match="reason_code"):
        AzureStrategyTrace(
            AzureStrategyEvent.INVENTORY_STARTED,
            1,
            reason_code="bad\nreason",
        )
    with pytest.raises(ValueError, match="strategy"):
        AzureStrategyTrace(
            AzureStrategyEvent.INVENTORY_STARTED,
            1,
            strategy="vm",
        )
    with pytest.raises(ValueError, match="zones"):
        replace(candidate, zones=("2", "1"))
    with pytest.raises(ValueError, match="available_hosts"):
        replace(candidate, available_hosts=0)
    with pytest.raises(ValueError, match="availability"):
        replace(candidate, availability=object())


def _retail_item(
    *,
    meter_id: str,
    product: str = "Virtual Machines Future Series",
    sku_name: str = "FutureGpu 8",
    meter_name: str = "FutureGpu 8",
    arm_sku_name: str = _VM_SKU,
    price: float = 12.5,
    effective: str = "2026-09-15T00:00:00Z",
) -> dict[str, Any]:
    return {
        "armRegionName": _REGION,
        "armSkuName": arm_sku_name,
        "currencyCode": "USD",
        "effectiveStartDate": effective,
        "isPrimaryMeterRegion": True,
        "meterId": meter_id,
        "meterName": meter_name,
        "productName": product,
        "retailPrice": price,
        "serviceName": "Virtual Machines",
        "skuName": sku_name,
        "type": "Consumption",
        "unitOfMeasure": "1 Hour",
    }


def test_bounded_retail_resolver_selects_exact_arm_sku_linux_consumption() -> None:
    urls: list[str] = []

    def fetch(url: str, _timeout: float) -> dict[str, object]:
        urls.append(url)
        return {
            "Items": [
                _retail_item(meter_id="spot", sku_name="FutureGpu 8 Spot"),
                _retail_item(
                    meter_id="windows",
                    product="Virtual Machines Future Series Windows",
                ),
                _retail_item(meter_id="exact"),
                _retail_item(meter_id="other", arm_sku_name="Standard_Other"),
            ],
            "NextPageLink": None,
        }

    pricing = AzureContainerAppsRetailPricing(fetch_json=fetch, now=lambda: _NOW)

    meter = pricing.resolve_virtual_machine_arm_sku_meter(
        region=_REGION,
        arm_sku_name=_VM_SKU,
    )

    assert meter.meter_id == "exact"
    assert meter.sku_name == _VM_SKU
    assert meter.fetched_at == _NOW
    assert len(urls) == 1
    assert "armSkuName+eq+%27Standard_FutureGpu_8%27" in urls[0]


def test_bounded_retail_resolver_rejects_ambiguity_and_bad_identity() -> None:
    pricing = AzureContainerAppsRetailPricing(
        fetch_json=lambda _url, _timeout: {
            "Items": [
                _retail_item(meter_id="first"),
                _retail_item(meter_id="second", meter_name="FutureGpu 8 Gen2"),
            ],
            "NextPageLink": None,
        },
        now=lambda: _NOW,
    )

    with pytest.raises(AzureRetailPricingError, match="ambiguous"):
        pricing.resolve_virtual_machine_arm_sku_meter(
            region=_REGION,
            arm_sku_name=_VM_SKU,
        )
    with pytest.raises(AzureRetailPricingError, match="arm_sku_name"):
        pricing.resolve_virtual_machine_arm_sku_meter(
            region=_REGION,
            arm_sku_name="bad' or true",
        )


def test_bounded_retail_resolver_cache_pagination_and_failures() -> None:
    calls = 0

    def cached_fetch(_url: str, _timeout: float) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"Items": [_retail_item(meter_id="exact")], "NextPageLink": None}

    pricing = AzureContainerAppsRetailPricing(fetch_json=cached_fetch, now=lambda: _NOW)
    first = pricing.resolve_virtual_machine_arm_sku_meter(
        region=_REGION, arm_sku_name=_VM_SKU
    )
    second = pricing.resolve_virtual_machine_arm_sku_meter(
        region=_REGION, arm_sku_name=_VM_SKU
    )
    assert first is second
    assert calls == 1

    bad_payloads = (
        ({"Items": "bad", "NextPageLink": None}, "Items list"),
        (
            {
                "Items": [],
                "NextPageLink": "https://evil.example/api/retail/prices",
            },
            "unsafe NextPageLink",
        ),
        ({"Items": [], "NextPageLink": None}, "no exact current"),
        (
            {
                "Items": [
                    _retail_item(
                        meter_id="future",
                        effective="2026-09-17T00:00:00Z",
                    )
                ],
                "NextPageLink": None,
            },
            "no exact current",
        ),
    )
    for payload, match in bad_payloads:
        resolver = AzureContainerAppsRetailPricing(
            fetch_json=lambda _url, _timeout, value=payload: value,
            now=lambda: _NOW,
        )
        with pytest.raises(AzureRetailPricingError, match=match):
            resolver.resolve_virtual_machine_arm_sku_meter(
                region=_REGION, arm_sku_name=_VM_SKU
            )


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ({"retailPrice": True}, "invalid retail price"),
        ({"retailPrice": "not-a-number"}, "invalid retail price"),
        ({"retailPrice": -1}, "invalid retail price"),
        ({"meterId": ""}, "incomplete meter identity"),
    ],
)
def test_bounded_retail_resolver_rejects_invalid_selected_meter(
    mutation: dict[str, object],
    match: str,
) -> None:
    item = _retail_item(meter_id="exact")
    item.update(mutation)
    pricing = AzureContainerAppsRetailPricing(
        fetch_json=lambda _url, _timeout: {"Items": [item], "NextPageLink": None},
        now=lambda: _NOW,
    )
    with pytest.raises(AzureRetailPricingError, match=match):
        pricing.resolve_virtual_machine_arm_sku_meter(
            region=_REGION, arm_sku_name=_VM_SKU
        )


def test_bounded_retail_resolver_rejects_transport_page_limit_and_naive_clock() -> None:
    transport = AzureContainerAppsRetailPricing(
        fetch_json=lambda _url, _timeout: (_ for _ in ()).throw(OSError("secret")),
        now=lambda: _NOW,
    )
    with pytest.raises(AzureRetailPricingError, match="unable to obtain"):
        transport.resolve_virtual_machine_arm_sku_meter(
            region=_REGION, arm_sku_name=_VM_SKU
        )
    page_limited = AzureContainerAppsRetailPricing(
        fetch_json=lambda _url, _timeout: {
            "Items": [],
            "NextPageLink": "https://prices.azure.com/api/retail/prices?page=2",
        },
        max_pages=1,
        now=lambda: _NOW,
    )
    with pytest.raises(AzureRetailPricingError, match="exceeded"):
        page_limited.resolve_virtual_machine_arm_sku_meter(
            region=_REGION, arm_sku_name=_VM_SKU
        )
    naive = AzureContainerAppsRetailPricing(
        fetch_json=lambda _url, _timeout: {
            "Items": [_retail_item(meter_id="exact")],
            "NextPageLink": None,
        },
        now=lambda: datetime(2026, 9, 16),
    )
    with pytest.raises(AzureRetailPricingError, match="timezone-aware"):
        naive.resolve_virtual_machine_arm_sku_meter(
            region=_REGION, arm_sku_name=_VM_SKU
        )


def _resource(
    *,
    key: str,
    count: int,
    memory_gib: float,
    backend: str = "cuda",
) -> AcceleratorResource:
    return AcceleratorResource(
        kind=AcceleratorKind.GPU,
        location=AcceleratorLocation.CLOUD,
        backend=backend,
        model=f"model-{key}",
        vendor="provider-vendor",
        resource_key=key,
        total_count=count,
        available_count=count,
        memory_gb=memory_gib,
        source="attested-test-fixture",
    )


def _topology(
    *,
    key: str,
    devices: int,
    hourly_microusd: int,
    memory_gib: float = 80,
    interconnect: str | None = None,
    attested: bool = True,
    backend: str = "cuda",
) -> AcceleratorTopology:
    return AcceleratorTopology(
        resource=_resource(
            key=key,
            count=devices,
            memory_gib=memory_gib,
            backend=backend,
        ),
        provider="azure",
        region=_REGION,
        zone=None,
        host_count=1,
        devices_per_host=devices,
        max_devices_per_workload=devices,
        intra_host_interconnect=interconnect,
        intra_host_interconnect_group_size=devices if interconnect else 1,
        cross_host_interconnect=None,
        partitioning=PartitioningMode.WHOLE_DEVICE,
        memory_isolated=True,
        cost=AcceleratorCost(hourly_microusd, devices),
        facts_attested=attested,
    )


def _runner(*, sizes: frozenset[int] = frozenset({1})) -> RunnerCapabilities:
    return RunnerCapabilities(
        runner_id="runner-digest",
        supported_device_kinds=frozenset({"gpu"}),
        supported_backends=frozenset({"cuda"}),
        supported_partitioning=frozenset({PartitioningMode.WHOLE_DEVICE}),
        distribution_mode=DistributionMode.EXPLICIT_PARALLEL,
        supported_model_parallel_sizes=sizes,
        supported_tensor_parallel_sizes=sizes,
        supported_pipeline_parallel_sizes=frozenset({1}),
        tensor_parallel_interconnects=frozenset({"fabric-x"}),
        pipeline_parallel_interconnects=frozenset(),
        cross_host_interconnects=frozenset(),
        max_hosts=1,
        max_data_parallel_replicas=1,
    )


def _demand(*, weight_gib: int = 20, divisor: int = 1) -> ModelRunnerDemand:
    return ModelRunnerDemand(
        model_id="private-model-identity",
        allowed_device_kinds=frozenset({"gpu"}),
        weight_bytes=weight_gib * 1024**3,
        runtime_overhead_bytes=2 * 1024**3,
        kv_cache_bytes_per_token=1024,
        context_tokens=4096,
        concurrent_sequences=1,
        data_parallel_replicas=1,
        tensor_parallel_divisor=divisor,
    )


def _option(
    strategy: AzureExecutionStrategy,
    *,
    topology: AcceleratorTopology,
    runner: RunnerCapabilities | None = None,
    startup: float,
    throughput: float,
    availability: AzureAvailabilityAssessment | None = None,
    runtime_supported: bool = True,
    privacy_allowed: bool = True,
    operator_approved: bool = True,
    identity_attested: bool = True,
    price_fetched_at: datetime = _NOW,
    infrastructure_observed_at: datetime = _NOW,
    vmss_evidence: AzureVmssEvidence | None = None,
) -> AzureExecutionOption:
    return AzureExecutionOption(
        strategy=strategy,
        topology=topology,
        runner=runner or _runner(),
        measured_startup_seconds=startup,
        measured_throughput_units_per_second=throughput,
        price_fetched_at=price_fetched_at,
        infrastructure_observed_at=infrastructure_observed_at,
        availability=availability or _assessment(),
        runtime_supported=runtime_supported,
        privacy_allowed=privacy_allowed,
        operator_approved=operator_approved,
        identity_attested=identity_attested,
        vmss_evidence=vmss_evidence,
    )


def _constraints(max_cost: int = 100_000_000) -> TopologyConstraints:
    return TopologyConstraints(
        allowed_providers=frozenset({"azure"}),
        allowed_regions=frozenset({_REGION}),
        max_hourly_cost_microusd=max_cost,
        max_total_devices=8,
    )


def _complete_vmss_evidence() -> AzureVmssEvidence:
    return AzureVmssEvidence(
        replica_semantics_attested=True,
        high_availability_attested=True,
        rdma_topology_attested=True,
        provisioning_contract_attested=True,
        bootstrap_attested=True,
        application_health_attested=True,
        telemetry_attested=True,
        work_dispatch_attested=True,
        teardown_attested=True,
    )


def test_container_apps_requires_attested_single_gpu_pareto_win() -> None:
    container = _option(
        AzureExecutionStrategy.CONTAINER_APPS,
        topology=_topology(key="ca", devices=1, hourly_microusd=2_000_000),
        startup=10,
        throughput=100,
    )
    vm = _option(
        AzureExecutionStrategy.SINGLE_VM,
        topology=_topology(key="vm", devices=1, hourly_microusd=10_000_000),
        startup=100,
        throughput=50,
    )

    decision = select_azure_execution_strategy(
        demand=_demand(),
        options=(vm, container),
        constraints=_constraints(),
        work_units=10_000,
        now=_NOW,
    )

    assert decision.strategy is AzureExecutionStrategy.CONTAINER_APPS
    assert decision.topology_plan.resource_key == "ca"
    assert decision.reason_code == "container_apps_measured_win"


def test_container_apps_without_pareto_win_uses_single_vm() -> None:
    container = _option(
        AzureExecutionStrategy.CONTAINER_APPS,
        topology=_topology(key="ca", devices=1, hourly_microusd=20_000_000),
        startup=10,
        throughput=100,
    )
    vm = _option(
        AzureExecutionStrategy.SINGLE_VM,
        topology=_topology(key="vm", devices=1, hourly_microusd=5_000_000),
        startup=20,
        throughput=90,
    )

    decision = select_azure_execution_strategy(
        demand=_demand(),
        options=(container, vm),
        constraints=_constraints(),
        work_units=10_000,
        now=_NOW,
    )

    assert decision.strategy is AzureExecutionStrategy.SINGLE_VM
    assert decision.reason_code == "single_vm_safe_default"


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("runtime_supported", "runtime_unsupported"),
        ("privacy_allowed", "privacy_forbidden"),
        ("operator_approved", "approval_missing"),
        ("identity_attested", "identity_unattested"),
    ],
)
def test_hard_constraints_are_applied_before_availability(
    field: str,
    reason: str,
) -> None:
    values = {field: False}
    container = _option(
        AzureExecutionStrategy.CONTAINER_APPS,
        topology=_topology(key="ca", devices=1, hourly_microusd=1),
        startup=1,
        throughput=1_000,
        availability=_assessment(successful=100),
        **values,
    )
    vm = _option(
        AzureExecutionStrategy.SINGLE_VM,
        topology=_topology(key="vm", devices=1, hourly_microusd=5_000_000),
        startup=20,
        throughput=90,
    )
    traces: list[AzureStrategyTrace] = []

    decision = select_azure_execution_strategy(
        demand=_demand(),
        options=(container, vm),
        constraints=_constraints(),
        work_units=100,
        now=_NOW,
        trace_sink=traces.append,
    )

    assert decision.strategy is AzureExecutionStrategy.SINGLE_VM
    assert reason in {trace.reason_code for trace in traces}


def test_host_local_multi_gpu_demand_uses_one_vm() -> None:
    container = _option(
        AzureExecutionStrategy.CONTAINER_APPS,
        topology=_topology(key="ca", devices=1, hourly_microusd=1_000_000),
        runner=_runner(sizes=frozenset({1})),
        startup=1,
        throughput=500,
    )
    vm = _option(
        AzureExecutionStrategy.SINGLE_VM,
        topology=_topology(
            key="vm-8",
            devices=8,
            hourly_microusd=12_000_000,
            interconnect="fabric-x",
        ),
        runner=_runner(sizes=frozenset({1, 2, 4, 8})),
        startup=80,
        throughput=800,
    )

    decision = select_azure_execution_strategy(
        demand=_demand(weight_gib=300, divisor=8),
        options=(container, vm),
        constraints=_constraints(),
        work_units=10_000,
        now=_NOW,
    )

    assert decision.strategy is AzureExecutionStrategy.SINGLE_VM
    assert decision.topology_plan.resource_key == "vm-8"
    assert decision.topology_plan.host_count == 1
    assert decision.topology_plan.model_parallel_devices in {4, 8}


@pytest.mark.parametrize(
    ("option", "reason"),
    [
        (
            _option(
                AzureExecutionStrategy.SINGLE_VM,
                topology=_topology(
                    key="vm",
                    devices=1,
                    hourly_microusd=2_000_000,
                    attested=False,
                ),
                startup=10,
                throughput=10,
            ),
            "topology_unattested",
        ),
        (
            _option(
                AzureExecutionStrategy.SINGLE_VM,
                topology=_topology(key="vm", devices=1, hourly_microusd=2_000_000),
                startup=10,
                throughput=10,
                availability=_assessment(successful=0, failed=0),
            ),
            "capacity_evidence_missing",
        ),
        (
            _option(
                AzureExecutionStrategy.SINGLE_VM,
                topology=_topology(key="vm", devices=1, hourly_microusd=2_000_000),
                startup=10,
                throughput=10,
                price_fetched_at=_NOW - timedelta(seconds=3601),
            ),
            "price_stale",
        ),
    ],
)
def test_strategy_fails_closed_without_required_evidence(
    option: AzureExecutionOption,
    reason: str,
) -> None:
    with pytest.raises(AzureStrategySelectionError) as exc_info:
        select_azure_execution_strategy(
            demand=_demand(),
            options=(option,),
            constraints=_constraints(),
            work_units=100,
            now=_NOW,
        )
    assert reason in exc_info.value.reason_codes


def test_budget_remains_a_hard_gate() -> None:
    vm = _option(
        AzureExecutionStrategy.SINGLE_VM,
        topology=_topology(key="vm", devices=1, hourly_microusd=2_000_000),
        startup=10,
        throughput=10,
        availability=_assessment(successful=100),
    )

    with pytest.raises(AzureStrategySelectionError) as exc_info:
        select_azure_execution_strategy(
            demand=_demand(),
            options=(vm,),
            constraints=_constraints(max_cost=1_000_000),
            work_units=100,
            now=_NOW,
        )

    assert "hourly_budget_exceeded" in exc_info.value.reason_codes


def test_vmss_requires_every_lifecycle_attestation() -> None:
    incomplete = AzureVmssEvidence(
        replica_semantics_attested=True,
        high_availability_attested=False,
        rdma_topology_attested=True,
    )
    complete = _complete_vmss_evidence()
    assert incomplete.eligible is False
    assert complete.eligible is True

    vmss = _option(
        AzureExecutionStrategy.VMSS,
        topology=_topology(key="vmss", devices=1, hourly_microusd=1),
        startup=1,
        throughput=1000,
        vmss_evidence=incomplete,
    )
    with pytest.raises(AzureStrategySelectionError) as exc_info:
        select_azure_execution_strategy(
            demand=_demand(),
            options=(vmss,),
            constraints=_constraints(),
            work_units=100,
            now=_NOW,
        )
    assert "vmss_evidence_missing" in exc_info.value.reason_codes


def test_selection_traces_do_not_expose_model_or_resource_identity() -> None:
    vm = _option(
        AzureExecutionStrategy.SINGLE_VM,
        topology=_topology(key="secret-resource-key", devices=1, hourly_microusd=1),
        startup=1,
        throughput=1,
    )
    traces: list[AzureStrategyTrace] = []

    select_azure_execution_strategy(
        demand=_demand(),
        options=(vm,),
        constraints=_constraints(),
        work_units=1,
        now=_NOW,
        trace_sink=traces.append,
    )

    rendered = repr(traces)
    assert "private-model-identity" not in rendered
    assert "secret-resource-key" not in rendered
    assert _VM_SKU not in rendered


def test_option_validation_rejects_nonfinite_metrics_and_time() -> None:
    with pytest.raises(ValueError, match="measured_startup_seconds"):
        _option(
            AzureExecutionStrategy.SINGLE_VM,
            topology=_topology(key="vm", devices=1, hourly_microusd=1),
            startup=float("nan"),
            throughput=1,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        _option(
            AzureExecutionStrategy.SINGLE_VM,
            topology=_topology(key="vm", devices=1, hourly_microusd=1),
            startup=1,
            throughput=1,
            price_fetched_at=datetime(2026, 9, 16),
        )


def test_option_and_vmss_value_objects_reject_invalid_shapes() -> None:
    with pytest.raises(ValueError, match="replica_semantics_attested"):
        AzureVmssEvidence(
            replica_semantics_attested=1,
            high_availability_attested=True,
            rdma_topology_attested=True,
        )
    valid = _option(
        AzureExecutionStrategy.SINGLE_VM,
        topology=_topology(key="vm", devices=1, hourly_microusd=1),
        startup=1,
        throughput=1,
    )
    with pytest.raises(ValueError, match="throughput"):
        replace(valid, measured_throughput_units_per_second=0)
    with pytest.raises(ValueError, match="runtime_supported"):
        replace(valid, runtime_supported=1)
    with pytest.raises(ValueError, match="vmss_evidence"):
        replace(valid, vmss_evidence=object())


def test_strategy_rejects_invalid_call_boundaries() -> None:
    demand = _demand()
    constraints = _constraints()
    option = _option(
        AzureExecutionStrategy.SINGLE_VM,
        topology=_topology(key="vm", devices=1, hourly_microusd=1),
        startup=1,
        throughput=1,
    )
    cases = (
        ({"demand": object()}, "demand"),
        ({"options": [option]}, "options"),
        ({"constraints": object()}, "constraints"),
        ({"work_units": 0}, "work_units"),
        ({"now": datetime(2026, 9, 16)}, "timezone-aware"),
        ({"max_price_age_seconds": 0}, "horizons"),
        ({"max_capacity_age_seconds": 0}, "horizons"),
        ({"max_infrastructure_age_seconds": 0}, "horizons"),
        ({"trace_sink": "bad"}, "trace_sink"),
    )
    for overrides, match in cases:
        values: dict[str, object] = {
            "demand": demand,
            "options": (option,),
            "constraints": constraints,
            "work_units": 1,
            "now": _NOW,
        }
        values.update(overrides)
        with pytest.raises(ValueError, match=match):
            select_azure_execution_strategy(**values)


def test_strategy_rejects_future_or_stale_capacity_and_future_price() -> None:
    base = _option(
        AzureExecutionStrategy.SINGLE_VM,
        topology=_topology(key="vm", devices=1, hourly_microusd=1),
        startup=1,
        throughput=1,
    )
    cases = (
        (
            replace(base, price_fetched_at=_NOW + timedelta(seconds=1)),
            "price_timestamp_invalid",
        ),
        (
            replace(
                base,
                infrastructure_observed_at=_NOW - timedelta(seconds=3601),
            ),
            "infrastructure_evidence_stale",
        ),
        (
            replace(
                base,
                availability=_assessment(observed_at=_NOW.timestamp() + 1),
            ),
            "capacity_timestamp_invalid",
        ),
        (
            replace(
                base,
                availability=_assessment(observed_at=_NOW.timestamp() - 3601),
            ),
            "capacity_evidence_stale",
        ),
        (
            replace(
                base,
                availability=_assessment(successful=0, failed=1, feasible=False),
            ),
            "capacity_unavailable",
        ),
    )
    for option, reason in cases:
        with pytest.raises(AzureStrategySelectionError) as exc_info:
            select_azure_execution_strategy(
                demand=_demand(),
                options=(option,),
                constraints=_constraints(),
                work_units=1,
                now=_NOW,
            )
        assert reason in exc_info.value.reason_codes


def test_container_apps_rejects_non_single_whole_gpu_topology() -> None:
    container = _option(
        AzureExecutionStrategy.CONTAINER_APPS,
        topology=_topology(
            key="ca-invalid",
            devices=2,
            hourly_microusd=1,
            interconnect="fabric-x",
        ),
        runner=_runner(sizes=frozenset({1, 2})),
        startup=1,
        throughput=100,
    )
    with pytest.raises(AzureStrategySelectionError) as exc_info:
        select_azure_execution_strategy(
            demand=_demand(),
            options=(container,),
            constraints=_constraints(),
            work_units=1,
            now=_NOW,
        )
    assert "container_apps_not_single_whole_gpu" in exc_info.value.reason_codes


def test_vmss_with_complete_evidence_is_a_real_execution_strategy() -> None:
    vmss = _option(
        AzureExecutionStrategy.VMSS,
        topology=_topology(key="vmss", devices=1, hourly_microusd=1),
        startup=1,
        throughput=100,
        vmss_evidence=_complete_vmss_evidence(),
    )
    decision = select_azure_execution_strategy(
        demand=_demand(),
        options=(vmss,),
        constraints=_constraints(),
        work_units=1,
        now=_NOW,
    )

    assert decision.strategy is AzureExecutionStrategy.VMSS
    assert decision.reason_code == "vmss_required_topology"


def test_vmss_must_pareto_beat_a_feasible_single_vm() -> None:
    vmss = _option(
        AzureExecutionStrategy.VMSS,
        topology=_topology(key="vmss", devices=1, hourly_microusd=1_000_000),
        startup=10,
        throughput=100,
        vmss_evidence=_complete_vmss_evidence(),
    )
    vm = _option(
        AzureExecutionStrategy.SINGLE_VM,
        topology=_topology(key="vm", devices=1, hourly_microusd=10_000_000),
        startup=100,
        throughput=10,
    )

    decision = select_azure_execution_strategy(
        demand=_demand(),
        options=(vm, vmss),
        constraints=_constraints(),
        work_units=10_000,
        now=_NOW,
    )

    assert decision.strategy is AzureExecutionStrategy.VMSS
    assert decision.reason_code == "vmss_measured_win"


def test_container_apps_alone_cannot_claim_a_comparative_win() -> None:
    container = _option(
        AzureExecutionStrategy.CONTAINER_APPS,
        topology=_topology(key="ca", devices=1, hourly_microusd=1),
        startup=1,
        throughput=100,
    )
    with pytest.raises(AzureStrategySelectionError) as exc_info:
        select_azure_execution_strategy(
            demand=_demand(),
            options=(container,),
            constraints=_constraints(),
            work_units=1,
            now=_NOW,
        )
    assert "container_apps_requires_vm_comparator" in exc_info.value.reason_codes
