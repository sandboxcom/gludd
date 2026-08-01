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
)
from general_ludd.infra.deploy_strategy import (
    DeployStrategist,
    DeployUrgency,
    ResourceTier,
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


def test_immediate_plan_adds_only_bounded_static_warmup_to_exact_primary() -> None:
    pricing = AzureContainerAppsRetailPricing(
        fetch_json=_SequenceFetcher(_exact_responses()),
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

    primary = (0.000073 + 8 * 0.000024 + 56 * 0.000003) * 600
    warmup = ResourceTier.DEDICATED_VM.cost_per_hour * (120 / 3600)
    assert plan.estimated_cost_usd == pytest.approx(round(primary + warmup, 6))
    assert plan.pricing_source == "azure-retail-prices+legacy-warmup"


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
