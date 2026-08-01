"""Credential-free live contract for Azure Container Apps retail meters.

This test performs no provisioning and incurs no Azure resource charge.  It is
opt-in because the public Retail Prices endpoint is still an external service.
"""

from __future__ import annotations

import os

import pytest

from general_ludd.infra.azure_retail_pricing import AzureContainerAppsRetailPricing


@pytest.mark.skipif(
    os.environ.get("AZURE_RETAIL_PRICES_LIVE") != "1",
    reason="set AZURE_RETAIL_PRICES_LIVE=1 for the public live meter check",
)
def test_current_eastus_serverless_gpu_meters_are_exact_and_nonzero() -> None:
    pricing = AzureContainerAppsRetailPricing(cache_ttl_seconds=300)

    t4 = pricing.estimate_for_gpu("t4", region="eastus", duration_seconds=1)
    a100 = pricing.estimate_for_gpu(
        "a100_80",
        region="eastus",
        duration_seconds=1,
    )

    assert t4.gpu_meter.sku_name == "Standard"
    assert t4.gpu_meter.meter_name == "Standard NC T4 v3 GPU Usage"
    assert a100.gpu_meter.meter_name == "Standard NC A100 v4 GPU Usage"
    assert t4.vcpu_meter.meter_name == "Standard vCPU Active Usage"
    assert t4.memory_meter.meter_name == "Standard Memory Active Usage"
    assert t4.total_cost_usd > 0
    assert a100.total_cost_usd > t4.total_cost_usd
    assert all(t4.meter_ids)
    assert all(a100.meter_ids)
