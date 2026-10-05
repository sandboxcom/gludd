"""Red-first contracts for exact Azure Container Apps serverless-GPU pricing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from general_ludd.infra.azure_retail_pricing import (
    AzureContainerAppsRetailPricing,
    AzureRetailPricingError,
    AzureVirtualMachineRetailPricing,
    AzureVmBillingPhases,
)
from general_ludd.infra.deploy_strategy import (
    DeployStrategist,
    DeployUrgency,
)

T4_SKU = "Standard NC T4 v3 GPU Usage"
A100_SKU = "Standard NC A100 v4 GPU Usage"
VCPU_SKU = "Standard vCPU Active Usage"
MEMORY_SKU = "Standard Memory Active Usage"


def _item(
    meter_name: str,
    price: float,
    unit: str,
    *,
    region: str = "eastus",
    price_type: str = "Consumption",
    effective: str = "2026-01-01T00:00:00Z",
    meter_id: str | None = None,
) -> dict[str, object]:
    return {
        "armRegionName": region,
        "currencyCode": "USD",
        "effectiveStartDate": effective,
        "isPrimaryMeterRegion": True,
        "meterId": meter_id or f"meter-{meter_name.lower().replace(' ', '-')}",
        "meterName": meter_name,
        "retailPrice": price,
        "serviceName": "Azure Container Apps",
        "skuName": "Standard",
        "type": price_type,
        "unitOfMeasure": unit,
    }


def _page(item: Mapping[str, object], *, next_page: str | None = None) -> dict[str, object]:
    return {"Items": [dict(item)], "NextPageLink": next_page, "Count": 1}


class _SequenceFetcher:
    def __init__(self, responses: list[Mapping[str, object] | Exception]) -> None:
        self.responses = list(responses)
        self.urls: list[str] = []

    def __call__(self, url: str, timeout_seconds: float) -> Mapping[str, object]:
        assert timeout_seconds > 0
        self.urls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _exact_responses(gpu_sku: str = T4_SKU) -> list[Mapping[str, object]]:
    gpu_price = 0.000073 if gpu_sku == T4_SKU else 0.000529
    return [
        _page(_item(gpu_sku, gpu_price, "1 Second")),
        _page(_item(VCPU_SKU, 0.000024, "1 Second")),
        _page(_item(MEMORY_SKU, 0.000003, "1 GiB Second")),
    ]


def _vm_item(
    *,
    service_name: str,
    product_name: str,
    sku_name: str,
    meter_name: str,
    unit: str,
    price: float,
    arm_sku_name: str = "",
    meter_id: str | None = None,
    region: str = "eastus",
) -> dict[str, object]:
    return {
        "armRegionName": region,
        "armSkuName": arm_sku_name,
        "currencyCode": "USD",
        "effectiveStartDate": "2026-01-01T00:00:00Z",
        "isPrimaryMeterRegion": True,
        "meterId": meter_id or f"meter-{service_name}-{meter_name}",
        "meterName": meter_name,
        "productName": product_name,
        "retailPrice": price,
        "serviceName": service_name,
        "skuName": sku_name,
        "type": "Consumption",
        "unitOfMeasure": unit,
    }


def _vm_responses(*, spot: bool = False) -> list[Mapping[str, object]]:
    suffix = " Spot" if spot else ""
    return [
        _page(
            _vm_item(
                service_name="Virtual Machines",
                product_name="Virtual Machines NCasT4 v3 Series",
                sku_name=f"NC8as T4 v3{suffix}",
                meter_name=f"NC8as T4 v3{suffix}",
                unit="1 Hour",
                price=0.2256 if spot else 0.752,
                arm_sku_name="Standard_NC8as_T4_v3",
                meter_id="vm-spot" if spot else "vm-ondemand",
            )
        ),
        _page(
            _vm_item(
                service_name="Storage",
                product_name="Standard SSD Managed Disks",
                sku_name="E10 LRS",
                meter_name="E10 LRS Disk",
                unit="1/Month",
                price=9.6,
                arm_sku_name="StandardSSD_LRS",
                meter_id="disk-e10",
            )
        ),
        _page(
            _vm_item(
                service_name="Virtual Network",
                product_name="IP Addresses",
                sku_name="Standard",
                meter_name="Standard IPv4 Static Public IP",
                unit="1 Hour",
                price=0.005,
                meter_id="ip-v4-standard",
                region="Global",
            )
        ),
    ]


def test_vm_estimate_uses_exact_compute_disk_ip_meters_and_elapsed_phases() -> None:
    fetcher = _SequenceFetcher(_vm_responses())
    pricing = AzureVirtualMachineRetailPricing(
        fetch_json=fetcher,
        now=lambda: datetime(2026, 8, 1, tzinfo=UTC),
    )
    phases = AzureVmBillingPhases(
        warmup_seconds=120,
        runtime_seconds=300,
        shutdown_seconds=45,
    )

    estimate = pricing.estimate_for_gpu(
        "t4",
        region="eastus",
        purchase_option="on_demand",
        phases=phases,
        disk_size_gib=100,
    )

    elapsed_hours = 465 / 3600
    assert estimate.arm_sku_name == "Standard_NC8as_T4_v3"
    assert estimate.disk_tier == "E10"
    assert estimate.compute_cost_usd == pytest.approx(0.752 * elapsed_hours)
    assert estimate.disk_cost_usd == pytest.approx(9.6 * elapsed_hours / 730)
    assert estimate.public_ip_cost_usd == pytest.approx(0.005 * elapsed_hours)
    assert estimate.total_cost_usd == pytest.approx(
        estimate.compute_cost_usd
        + estimate.disk_cost_usd
        + estimate.public_ip_cost_usd
    )
    assert estimate.phase_costs_usd["warmup"] == pytest.approx(0.752 * 120 / 3600)
    assert estimate.phase_costs_usd["runtime"] == pytest.approx(0.752 * 300 / 3600)
    assert estimate.phase_costs_usd["shutdown"] == pytest.approx(0.752 * 45 / 3600)
    assert estimate.meter_ids == ("vm-ondemand", "disk-e10", "ip-v4-standard")
    selectors = [parse_qs(urlparse(url).query)["$filter"][0] for url in fetcher.urls]
    assert "armSkuName eq 'Standard_NC8as_T4_v3'" in selectors[0]
    assert "serviceName eq 'Virtual Machines'" in selectors[0]
    assert "skuName eq 'E10 LRS'" in selectors[1]
    assert "armRegionName eq 'Global'" in selectors[2]
    assert "serviceName eq 'Virtual Network'" in selectors[2]


def test_spot_vm_requires_the_exact_spot_meter_identity() -> None:
    pricing = AzureVirtualMachineRetailPricing(
        fetch_json=_SequenceFetcher(_vm_responses(spot=True)),
        now=lambda: datetime(2026, 8, 1, tzinfo=UTC),
    )
    estimate = pricing.estimate_for_gpu(
        "t4",
        region="eastus",
        purchase_option="spot",
        phases=AzureVmBillingPhases(60, 120, 30),
        disk_size_gib=128,
    )
    assert estimate.compute_meter.meter_name == "NC8as T4 v3 Spot"
    assert estimate.compute_meter.meter_id == "vm-spot"

    inexact = AzureVirtualMachineRetailPricing(
        fetch_json=_SequenceFetcher(_vm_responses(spot=False)),
        now=lambda: datetime(2026, 8, 1, tzinfo=UTC),
    )
    with pytest.raises(AzureRetailPricingError, match="no exact"):
        inexact.estimate_for_gpu(
            "t4",
            region="eastus",
            purchase_option="spot",
            phases=AzureVmBillingPhases(60, 120, 30),
        )


def test_vm_estimate_builds_durable_work_item_prediction_with_all_meters() -> None:
    pricing = AzureVirtualMachineRetailPricing(
        fetch_json=_SequenceFetcher(_vm_responses()),
        now=lambda: datetime(2026, 8, 1, tzinfo=UTC),
    )
    estimate = pricing.estimate_for_gpu(
        "t4",
        region="eastus",
        phases=AzureVmBillingPhases(120, 300, 45),
    )
    started_at = datetime(2026, 8, 1, 12, tzinfo=UTC)

    prediction = estimate.to_cost_prediction(
        prediction_id="prediction-1",
        todo_id="AZL.2",
        subscription_id="subscription-1",
        resource_group="rg-gludd-1",
        resource_ids=(
            "/subscriptions/subscription-1/resourceGroups/rg-gludd-1/providers/Microsoft.Compute/virtualMachines/vm-1",
            "/subscriptions/subscription-1/resourceGroups/rg-gludd-1/providers/Microsoft.Compute/disks/os-1",
            "/subscriptions/subscription-1/resourceGroups/rg-gludd-1/providers/Microsoft.Network/publicIPAddresses/ip-1",
        ),
        workload="fps-game-e2e",
        usage_started_at=started_at,
        conservative_multiplier=1.2,
    )

    assert prediction.meter_ids == estimate.meter_ids
    assert prediction.sku == "Standard_NC8as_T4_v3:on_demand"
    assert prediction.predicted_cost_usd == pytest.approx(estimate.total_cost_usd)
    assert prediction.conservative_ceiling_usd == pytest.approx(
        estimate.total_cost_usd * 1.2
    )
    assert (prediction.usage_ended_at - prediction.usage_started_at).total_seconds() == 465
    assert prediction.tags["gludd-pricing-source"] == "azure-retail-prices"


@pytest.mark.parametrize(
    "values",
    [
        (-1, 1, 1),
        (1, 0, 1),
        (1, 1, float("inf")),
    ],
)
def test_vm_billing_phases_reject_invalid_elapsed_times(
    values: tuple[float, float, float],
) -> None:
    with pytest.raises(ValueError, match="seconds"):
        AzureVmBillingPhases(*values)


def test_t4_estimate_selects_exact_region_skus_and_consumption_type() -> None:
    fetcher = _SequenceFetcher(_exact_responses())
    pricing = AzureContainerAppsRetailPricing(
        fetch_json=fetcher,
        now=lambda: datetime(2026, 8, 1, tzinfo=UTC),
    )

    estimate = pricing.estimate_for_gpu(
        "t4",
        region="eastus",
        duration_seconds=60,
    )

    assert estimate.workload_profile == "Consumption-GPU-NC8as-T4"
    assert estimate.vcpu == 8
    assert estimate.memory_gib == 56
    assert estimate.gpu_meter.sku_name == "Standard"
    assert estimate.gpu_meter.meter_name == T4_SKU
    assert estimate.total_cost_usd == pytest.approx(
        (0.000073 + 8 * 0.000024 + 56 * 0.000003) * 60
    )
    assert len(fetcher.urls) == 3
    for url, expected_sku in zip(fetcher.urls, (T4_SKU, VCPU_SKU, MEMORY_SKU), strict=True):
        query = parse_qs(urlparse(url).query)
        selector = query["$filter"][0]
        assert "armRegionName eq 'eastus'" in selector
        assert "serviceName eq 'Azure Container Apps'" in selector
        assert "skuName eq 'Standard'" in selector
        assert f"meterName eq '{expected_sku}'" in selector
        assert "priceType eq 'Consumption'" in selector


def test_a100_profile_uses_exact_a100_meter_and_resource_shape() -> None:
    fetcher = _SequenceFetcher(_exact_responses(A100_SKU))
    pricing = AzureContainerAppsRetailPricing(
        fetch_json=fetcher,
        now=lambda: datetime(2026, 8, 1, tzinfo=UTC),
    )

    estimate = pricing.estimate_for_gpu(
        "a100_80",
        region="eastus",
        duration_seconds=3600,
    )

    assert estimate.workload_profile == "Consumption-GPU-NC24-A100"
    assert estimate.gpu_meter.sku_name == "Standard"
    assert estimate.gpu_meter.meter_name == A100_SKU
    assert estimate.vcpu == 24
    assert estimate.memory_gib == 220
    assert estimate.hourly_rate_usd == pytest.approx(
        (0.000529 + 24 * 0.000024 + 220 * 0.000003) * 3600
    )


def test_identical_lookup_is_cached_only_until_ttl() -> None:
    monotonic = [10.0]
    fetcher = _SequenceFetcher(_exact_responses() + _exact_responses())
    pricing = AzureContainerAppsRetailPricing(
        fetch_json=fetcher,
        cache_ttl_seconds=60,
        monotonic=lambda: monotonic[0],
        now=lambda: datetime(2026, 8, 1, tzinfo=UTC),
    )

    first = pricing.estimate_for_gpu("t4", region="eastus", duration_seconds=1)
    monotonic[0] = 69.0
    cached = pricing.estimate_for_gpu("t4", region="eastus", duration_seconds=1)
    assert cached == first
    assert len(fetcher.urls) == 3

    monotonic[0] = 71.0
    refreshed = pricing.estimate_for_gpu("t4", region="eastus", duration_seconds=1)
    assert refreshed == first
    assert len(fetcher.urls) == 6


def test_stale_cache_is_not_returned_when_refresh_fails() -> None:
    monotonic = [10.0]
    fetcher = _SequenceFetcher(
        [*_exact_responses(), OSError("temporary Retail Prices outage")]
    )
    pricing = AzureContainerAppsRetailPricing(
        fetch_json=fetcher,
        cache_ttl_seconds=10,
        monotonic=lambda: monotonic[0],
        now=lambda: datetime(2026, 8, 1, tzinfo=UTC),
    )
    pricing.estimate_for_gpu("t4", region="eastus", duration_seconds=1)

    monotonic[0] = 21.0
    with pytest.raises(AzureRetailPricingError, match="fresh Azure retail price"):
        pricing.estimate_for_gpu("t4", region="eastus", duration_seconds=1)


def test_latest_effective_meter_must_be_unambiguous() -> None:
    duplicate_latest = {
        "Items": [
            _item(T4_SKU, 0.000073, "1 Second", meter_id="meter-a"),
            _item(T4_SKU, 0.000074, "1 Second", meter_id="meter-b"),
        ],
        "NextPageLink": None,
    }
    pricing = AzureContainerAppsRetailPricing(
        fetch_json=_SequenceFetcher([duplicate_latest]),
        now=lambda: datetime(2026, 8, 1, tzinfo=UTC),
    )

    with pytest.raises(AzureRetailPricingError, match="ambiguous"):
        pricing.resolve_meter(
            region="eastus",
            sku_name="Standard",
            meter_name=T4_SKU,
            price_type="Consumption",
            unit_of_measure="1 Second",
        )


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ({"armRegionName": "westus3"}, "no exact"),
        ({"type": "Reservation"}, "no exact"),
        ({"skuName": "Standard NC T4 v3 GPU Spot"}, "no exact"),
        ({"currencyCode": "EUR"}, "no exact"),
        ({"unitOfMeasure": "1 Hour"}, "no exact"),
        ({"isPrimaryMeterRegion": False}, "no exact"),
        ({"effectiveStartDate": "2027-01-01T00:00:00Z"}, "no exact"),
        ({"retailPrice": 0.0}, "invalid retail price"),
        ({"retailPrice": {"amount": 0.1}}, "invalid retail price"),
        ({"retailPrice": "not-a-number"}, "invalid retail price"),
        ({"meterId": ""}, "incomplete meter identity"),
    ],
)
def test_inexact_or_invalid_meter_data_fails_closed(
    mutation: dict[str, object], match: str
) -> None:
    raw = _item(T4_SKU, 0.000073, "1 Second")
    raw.update(mutation)
    pricing = AzureContainerAppsRetailPricing(
        fetch_json=_SequenceFetcher([_page(raw)]),
        now=lambda: datetime(2026, 8, 1, tzinfo=UTC),
    )

    with pytest.raises(AzureRetailPricingError, match=match):
        pricing.resolve_meter(
            region="eastus",
            sku_name="Standard",
            meter_name=T4_SKU,
            price_type="Consumption",
            unit_of_measure="1 Second",
        )


def test_naive_pricing_clock_fails_closed() -> None:
    raw = _item(T4_SKU, 0.000073, "1 Second")
    pricing = AzureContainerAppsRetailPricing(
        fetch_json=_SequenceFetcher([_page(raw)]),
        now=lambda: datetime(2026, 8, 1),
    )

    with pytest.raises(AzureRetailPricingError, match="timezone-aware"):
        pricing.resolve_meter(
            region="eastus",
            sku_name="Standard",
            meter_name=T4_SKU,
            price_type="Consumption",
            unit_of_measure="1 Second",
        )


def test_response_requires_an_items_list() -> None:
    pricing = AzureContainerAppsRetailPricing(
        fetch_json=_SequenceFetcher([{"Items": {}, "NextPageLink": None}]),
        now=lambda: datetime(2026, 8, 1, tzinfo=UTC),
    )

    with pytest.raises(AzureRetailPricingError, match="no Items list"):
        pricing.resolve_meter(
            region="eastus",
            sku_name="Standard",
            meter_name=T4_SKU,
            price_type="Consumption",
            unit_of_measure="1 Second",
        )


def test_historical_meter_is_ignored_in_favor_of_latest_effective_meter() -> None:
    pricing = AzureContainerAppsRetailPricing(
        fetch_json=_SequenceFetcher(
            [
                {
                    "Items": [
                        _item(
                            T4_SKU,
                            0.00008,
                            "1 Second",
                            effective="2025-01-01T00:00:00Z",
                            meter_id="old",
                        ),
                        _item(
                            T4_SKU,
                            0.000073,
                            "1 Second",
                            effective="2026-01-01T00:00:00Z",
                            meter_id="current",
                        ),
                    ],
                    "NextPageLink": None,
                }
            ]
        ),
        now=lambda: datetime(2026, 8, 1, tzinfo=UTC),
    )

    meter = pricing.resolve_meter(
        region="eastus",
        sku_name="Standard",
        meter_name=T4_SKU,
        price_type="Consumption",
        unit_of_measure="1 Second",
    )
    assert meter.meter_id == "current"
    assert meter.retail_price == pytest.approx(0.000073)


def test_pagination_rejects_non_azure_next_page_host() -> None:
    pricing = AzureContainerAppsRetailPricing(
        fetch_json=_SequenceFetcher(
            [_page(_item(T4_SKU, 0.000073, "1 Second"), next_page="https://evil.example/prices")]
        ),
        now=lambda: datetime(2026, 8, 1, tzinfo=UTC),
    )

    with pytest.raises(AzureRetailPricingError, match="unsafe NextPageLink"):
        pricing.resolve_meter(
            region="eastus",
            sku_name="Standard",
            meter_name=T4_SKU,
            price_type="Consumption",
            unit_of_measure="1 Second",
        )


@pytest.mark.parametrize(
    ("gpu_type", "region", "duration", "match"),
    [
        ("h100", "eastus", 1, "unsupported Azure Container Apps GPU"),
        ("t4", "eastus' or 1 eq 1", 1, "invalid Azure region"),
        ("t4", "eastus", 0, "duration_seconds"),
        ("t4", "eastus", float("inf"), "duration_seconds"),
    ],
)
def test_invalid_estimate_inputs_fail_before_network(
    gpu_type: str, region: str, duration: float, match: str
) -> None:
    pricing = AzureContainerAppsRetailPricing(fetch_json=_SequenceFetcher([]))
    with pytest.raises((AzureRetailPricingError, ValueError), match=match):
        pricing.estimate_for_gpu(
            gpu_type,
            region=region,
            duration_seconds=duration,
        )


def test_default_fetcher_reads_json_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps(_page(_item(T4_SKU, 0.000073, "1 Second"))).encode()

    class _Response(BytesIO):
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_args: Any) -> None:
            self.close()

    seen: dict[str, object] = {}

    def fake_urlopen(request: Any, timeout: float) -> _Response:
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return _Response(payload)

    monkeypatch.setattr(
        "general_ludd.infra.azure_retail_pricing.urlopen",
        fake_urlopen,
    )
    pricing = AzureContainerAppsRetailPricing(
        now=lambda: datetime(2026, 8, 1, tzinfo=UTC),
    )
    meter = pricing.resolve_meter(
        region="eastus",
        sku_name="Standard",
        meter_name=T4_SKU,
        price_type="Consumption",
        unit_of_measure="1 Second",
    )
    assert meter.retail_price == pytest.approx(0.000073)
    assert str(seen["url"]).startswith("https://prices.azure.com/")
    assert seen["timeout"] == 10.0


def test_deploy_strategist_uses_exact_container_app_estimate() -> None:
    pricing = AzureContainerAppsRetailPricing(
        fetch_json=_SequenceFetcher(_exact_responses()),
        now=lambda: datetime(2026, 8, 1, tzinfo=UTC),
    )
    strategist = DeployStrategist(azure_pricing=pricing)

    plan = strategist.plan(
        DeployUrgency.NORMAL,
        "t4",
        "Qwen/Qwen2.5-0.5B-Instruct",
        estimated_runtime_minutes=1,
        region="eastus",
    )

    expected = (0.000073 + 8 * 0.000024 + 56 * 0.000003) * 60
    assert plan.estimated_cost_usd == pytest.approx(expected)
    assert plan.pricing_source == "azure-retail-prices"
    assert plan.pricing_region == "eastus"
    assert len(plan.meter_ids) == 3
    assert "exact Azure Retail Prices" in plan.reasoning
    result = strategist.execute_phased(plan, "t4", "Qwen/Qwen2.5-0.5B-Instruct")
    assert result["plan"]["pricing_source"] == "azure-retail-prices"
    assert result["plan"]["pricing_region"] == "eastus"
    assert result["plan"]["meter_ids"] == plan.meter_ids


def test_immediate_plan_uses_exact_elapsed_vm_and_ancillary_costs() -> None:
    pricing = AzureContainerAppsRetailPricing(
        fetch_json=_SequenceFetcher([*_exact_responses(), *_vm_responses()]),
        now=lambda: datetime(2026, 8, 1, tzinfo=UTC),
    )
    strategist = DeployStrategist(azure_pricing=pricing)

    plan = strategist.plan(
        DeployUrgency.IMMEDIATE,
        "t4",
        "Qwen/Qwen2.5-0.5B-Instruct",
        estimated_runtime_minutes=10,
        region="eastus",
    )

    container = (0.000073 + 8 * 0.000024 + 56 * 0.000003) * 120
    vm_hours = (120 + 480 + 60) / 3600
    expected = container + 0.752 * vm_hours + 9.6 * vm_hours / 730 + 0.005 * vm_hours
    assert plan.estimated_cost_usd == pytest.approx(round(expected, 6))
    assert plan.pricing_source == "azure-retail-prices"
    assert len(plan.meter_ids) == 6


def test_deploy_strategist_fails_closed_when_exact_pricing_is_unavailable() -> None:
    pricing = AzureContainerAppsRetailPricing(
        fetch_json=_SequenceFetcher([OSError("offline")]),
    )
    strategist = DeployStrategist(azure_pricing=pricing)

    with pytest.raises(AzureRetailPricingError, match="fresh Azure retail price"):
        strategist.plan(
            DeployUrgency.NORMAL,
            "t4",
            "Qwen/Qwen2.5-0.5B-Instruct",
            region="eastus",
        )


def test_deploy_strategist_rejects_plan_above_operator_cost_ceiling() -> None:
    pricing = AzureContainerAppsRetailPricing(
        fetch_json=_SequenceFetcher(_exact_responses(A100_SKU)),
        now=lambda: datetime(2026, 8, 1, tzinfo=UTC),
    )
    strategist = DeployStrategist(azure_pricing=pricing)

    with pytest.raises(AzureRetailPricingError, match="cost ceiling"):
        strategist.plan(
            DeployUrgency.NORMAL,
            "a100_80",
            "Qwen/Qwen2.5-0.5B-Instruct",
            estimated_runtime_minutes=60,
            region="eastus",
            max_cost_usd=5.0,
        )
