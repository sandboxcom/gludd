"""Unit tests for DeployStrategist — phased deployment with fast-warm/slow-cheap."""

from __future__ import annotations

from collections.abc import Mapping

from general_ludd.infra.azure_retail_pricing import AzureContainerAppsRetailPricing
from general_ludd.infra.deploy_strategy import (
    DeployStrategist,
    DeployUrgency,
    ResourceTier,
)


def _retail_response(url: str, timeout_seconds: float) -> Mapping[str, object]:
    assert timeout_seconds > 0
    if "A100" in url:
        meter_name, price, unit = "Standard NC A100 v4 GPU Usage", 0.000529, "1 Second"
    elif "T4" in url:
        meter_name, price, unit = "Standard NC T4 v3 GPU Usage", 0.000073, "1 Second"
    elif "vCPU" in url:
        meter_name, price, unit = "Standard vCPU Active Usage", 0.000024, "1 Second"
    else:
        meter_name, price, unit = "Standard Memory Active Usage", 0.000003, "1 GiB Second"
    return {
        "Items": [
            {
                "armRegionName": "eastus",
                "currencyCode": "USD",
                "effectiveStartDate": "2026-01-01T00:00:00Z",
                "isPrimaryMeterRegion": True,
                "meterId": meter_name,
                "meterName": meter_name,
                "retailPrice": price,
                "serviceName": "Azure Container Apps",
                "skuName": "Standard",
                "type": "Consumption",
                "unitOfMeasure": unit,
            }
        ],
        "NextPageLink": None,
    }


def _strategist() -> DeployStrategist:
    return DeployStrategist(
        azure_pricing=AzureContainerAppsRetailPricing(fetch_json=_retail_response)
    )


class TestDeployUrgency:
    def test_immediate_value(self) -> None:
        assert DeployUrgency.IMMEDIATE.value == "immediate"

    def test_normal_value(self) -> None:
        assert DeployUrgency.NORMAL.value == "normal"

    def test_background_value(self) -> None:
        assert DeployUrgency.BACKGROUND.value == "background"


class TestResourceTier:
    def test_container_app_is_cheapest(self) -> None:
        assert ResourceTier.CONTAINER_APP.cost_per_hour < ResourceTier.SPOT_VM.cost_per_hour
        assert ResourceTier.CONTAINER_APP.cost_per_hour < ResourceTier.DEDICATED_VM.cost_per_hour

    def test_dedicated_vm_is_fastest_startup(self) -> None:
        assert ResourceTier.DEDICATED_VM.startup_seconds <= ResourceTier.SPOT_VM.startup_seconds
        assert ResourceTier.DEDICATED_VM.startup_seconds <= ResourceTier.CONTAINER_APP.startup_seconds

    def test_container_app_has_slowest_startup(self) -> None:
        assert ResourceTier.CONTAINER_APP.startup_seconds >= ResourceTier.SPOT_VM.startup_seconds
        assert ResourceTier.CONTAINER_APP.startup_seconds >= ResourceTier.DEDICATED_VM.startup_seconds

    def test_tier_id_matches_deploy_type(self) -> None:
        assert ResourceTier.CONTAINER_APP.tier_id == "containerapp"
        assert ResourceTier.SPOT_VM.tier_id == "vm_spot"
        assert ResourceTier.DEDICATED_VM.tier_id == "vm_dedicated"


class TestPhasedDeployPlan:
    def test_immediate_plan_has_warmup_tier(self) -> None:
        strategist = _strategist()
        plan = strategist.plan(DeployUrgency.IMMEDIATE, "a100_80", "test/model")
        assert plan.primary == ResourceTier.CONTAINER_APP
        assert plan.warmup is not None, "immediate urgency must have a warmup tier"
        assert plan.warmup.startup_seconds < ResourceTier.CONTAINER_APP.startup_seconds

    def test_normal_plan_no_warmup(self) -> None:
        strategist = _strategist()
        plan = strategist.plan(DeployUrgency.NORMAL, "t4", "test/model")
        assert plan.warmup is None, "normal urgency should not have a warmup"
        assert plan.primary == ResourceTier.CONTAINER_APP

    def test_background_plan_cheapest(self) -> None:
        strategist = _strategist()
        plan = strategist.plan(DeployUrgency.BACKGROUND, "t4", "test/model")
        assert plan.primary == ResourceTier.CONTAINER_APP
        assert plan.warmup is None

    def test_plan_returns_estimated_cost(self) -> None:
        strategist = _strategist()
        plan = strategist.plan(DeployUrgency.NORMAL, "a100_80", "test/model")
        assert plan.estimated_cost_usd > 0

    def test_plan_returns_reasoning(self) -> None:
        strategist = _strategist()
        plan = strategist.plan(DeployUrgency.IMMEDIATE, "t4", "test/model")
        assert len(plan.reasoning) > 0


class TestDeployStrategistHistory:
    def test_learn_from_history_tracks_cost(self) -> None:
        strategist = _strategist()
        initial_count = len(strategist.cost_history)
        strategist.learn_from_history(ResourceTier.CONTAINER_APP, 0.05, 620)
        assert len(strategist.cost_history) == initial_count + 1

    def test_learn_from_history_multiple_entries(self) -> None:
        strategist = _strategist()
        strategist.learn_from_history(ResourceTier.CONTAINER_APP, 0.12, 600)
        strategist.learn_from_history(ResourceTier.DEDICATED_VM, 4.50, 130)
        strategist.learn_from_history(ResourceTier.SPOT_VM, 1.20, 200)
        assert len(strategist.cost_history) >= 3

    def test_average_cost_by_tier(self) -> None:
        strategist = _strategist()
        strategist.learn_from_history(ResourceTier.CONTAINER_APP, 0.05, 600)
        strategist.learn_from_history(ResourceTier.CONTAINER_APP, 0.15, 650)
        avg = strategist.average_cost(ResourceTier.CONTAINER_APP)
        assert 0.09 < avg < 0.11

    def test_average_cost_empty_returns_zero(self) -> None:
        strategist = _strategist()
        assert strategist.average_cost(ResourceTier.SPOT_VM) == 0.0


class TestDeployStrategistExecute:
    def test_execute_phased_immediate_has_migration(self) -> None:
        strategist = _strategist()
        plan = strategist.plan(DeployUrgency.IMMEDIATE, "a100_80", "test/model")
        result = strategist.execute_phased(plan, "a100_80", "test/model")
        assert result["migration_needed"] is True
        assert result["warmup"] is not None
        assert result["primary"]["status"] == "provisioning"

    def test_execute_phased_normal_no_migration(self) -> None:
        strategist = _strategist()
        plan = strategist.plan(DeployUrgency.NORMAL, "t4", "test/model")
        result = strategist.execute_phased(plan, "t4", "test/model")
        assert result["migration_needed"] is False
        assert result["warmup"] is None

    def test_migrate_work_returns_status(self) -> None:
        strategist = _strategist()
        result = strategist.migrate_work("vm-abc", "ca-xyz")
        assert result["status"] == "migrated"
        assert result["from"] == "vm-abc"
        assert result["to"] == "ca-xyz"
