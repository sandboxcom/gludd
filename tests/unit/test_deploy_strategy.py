"""Unit tests for DeployStrategist — phased deployment with fast-warm/slow-cheap."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import parse_qs, urlparse

from general_ludd.infra.azure_retail_pricing import (
    AzureContainerAppsRetailPricing,
    AzureVirtualMachineRetailPricing,
)
from general_ludd.infra.deploy_strategy import (
    DeployStrategist,
    DeployUrgency,
    ElasticTierController,
    ElasticWorkload,
    ResourceTier,
)


def _retail_response(url: str, timeout_seconds: float) -> Mapping[str, object]:
    assert timeout_seconds > 0
    selector = parse_qs(urlparse(url).query)["$filter"][0]
    if "serviceName eq 'Virtual Machines'" in selector:
        a100 = "Standard_NC24ads_A100_v4" in selector
        base_meter = "NC24ads_A100_v4" if a100 else "NC8as T4 v3"
        product_name = (
            "NCads A100 v4 Series Linux"
            if a100
            else "Virtual Machines NCasT4 v3 Series"
        )
        arm_sku_name = (
            "Standard_NC24ads_A100_v4" if a100 else "Standard_NC8as_T4_v3"
        )

        def vm_item(*, spot: bool) -> dict[str, object]:
            suffix = " Spot" if spot else ""
            meter_name = f"{base_meter}{suffix}"
            sku_name = (
                f"Standard_NC24ads_A100_v4{suffix}" if a100 else meter_name
            )
            price = (
                (0.98 if spot else 3.673)
                if a100
                else (0.2256 if spot else 0.752)
            )
            return {
                "armRegionName": "eastus",
                "armSkuName": arm_sku_name,
                "currencyCode": "USD",
                "effectiveStartDate": "2026-01-01T00:00:00Z",
                "isPrimaryMeterRegion": True,
                "meterId": meter_name,
                "meterName": meter_name,
                "productName": product_name,
                "retailPrice": price,
                "serviceName": "Virtual Machines",
                "skuName": sku_name,
                "type": "Consumption",
                "unitOfMeasure": "1 Hour",
            }

        return {
            "Items": [vm_item(spot=False), vm_item(spot=True)],
            "NextPageLink": None,
        }
    elif "serviceName eq 'Storage'" in selector:
        meter_name, price, unit = "E10 LRS Disk", 9.6, "1/Month"
        service_name = "Storage"
        product_name = "Standard SSD Managed Disks"
        sku_name = "E10 LRS"
        arm_sku_name = "StandardSSD_LRS"
    elif "serviceName eq 'Virtual Network'" in selector:
        meter_name = "Standard IPv4 Static Public IP"
        price, unit = 0.005, "1 Hour"
        service_name = "Virtual Network"
        product_name = "IP Addresses"
        sku_name = "Standard"
        arm_sku_name = ""
    elif "A100" in url:
        meter_name, price, unit = "Standard NC A100 v4 GPU Usage", 0.000529, "1 Second"
        service_name, product_name, sku_name, arm_sku_name = (
            "Azure Container Apps",
            "Azure Container Apps",
            "Standard",
            "",
        )
    elif "T4" in url:
        meter_name, price, unit = "Standard NC T4 v3 GPU Usage", 0.000073, "1 Second"
        service_name, product_name, sku_name, arm_sku_name = (
            "Azure Container Apps",
            "Azure Container Apps",
            "Standard",
            "",
        )
    elif "vCPU" in url:
        meter_name, price, unit = "Standard vCPU Active Usage", 0.000024, "1 Second"
        service_name, product_name, sku_name, arm_sku_name = (
            "Azure Container Apps",
            "Azure Container Apps",
            "Standard",
            "",
        )
    else:
        meter_name, price, unit = "Standard Memory Active Usage", 0.000003, "1 GiB Second"
        service_name, product_name, sku_name, arm_sku_name = (
            "Azure Container Apps",
            "Azure Container Apps",
            "Standard",
            "",
        )
    return {
        "Items": [
            {
                "armRegionName": (
                    "Global" if service_name == "Virtual Network" else "eastus"
                ),
                "armSkuName": arm_sku_name,
                "currencyCode": "USD",
                "effectiveStartDate": "2026-01-01T00:00:00Z",
                "isPrimaryMeterRegion": True,
                "meterId": meter_name,
                "meterName": meter_name,
                "productName": product_name,
                "retailPrice": price,
                "serviceName": service_name,
                "skuName": sku_name,
                "type": "Consumption",
                "unitOfMeasure": unit,
            }
        ],
        "NextPageLink": None,
    }


def _strategist() -> DeployStrategist:
    retail = AzureContainerAppsRetailPricing(fetch_json=_retail_response)
    return DeployStrategist(
        azure_pricing=retail,
        azure_vm_pricing=AzureVirtualMachineRetailPricing(retail_client=retail),
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

    def test_immediate_plan_prices_vm_disk_and_ip_without_static_fallback(self) -> None:
        plan = _strategist().plan(
            DeployUrgency.IMMEDIATE,
            "t4",
            "test/model",
            estimated_runtime_minutes=10,
            region="eastus",
        )
        assert plan.pricing_source == "azure-retail-prices"
        assert len(plan.meter_ids) == 6
        assert plan.cost_components_usd["vm_compute"] > 0
        assert plan.cost_components_usd["managed_disk"] > 0
        assert plan.cost_components_usd["public_ip"] > 0
        assert plan.cost_components_usd["container_active"] > 0
        assert plan.phase_seconds["vm_warmup"] == 120
        assert plan.phase_seconds["vm_shutdown"] > 0


class TestElasticTierController:
    def test_hysteresis_prevents_tier_thrashing_and_emits_transitions(self) -> None:
        controller = ElasticTierController()
        up = controller.select(
            ElasticWorkload(
                urgency=DeployUrgency.NORMAL,
                queued_items=8,
                concurrent_items=2,
                estimated_runtime_minutes=30,
                latency_budget_seconds=300,
                spot_eligible=True,
            )
        )
        assert up.tier is ResourceTier.DEDICATED_VM
        assert up.transition == "scale_up"

        held = controller.select(
            ElasticWorkload(
                urgency=DeployUrgency.NORMAL,
                queued_items=5,
                concurrent_items=1,
                estimated_runtime_minutes=10,
                latency_budget_seconds=300,
                spot_eligible=True,
            )
        )
        assert held.tier is ResourceTier.DEDICATED_VM
        assert held.transition == "hold"

        down = controller.select(
            ElasticWorkload(
                urgency=DeployUrgency.BACKGROUND,
                queued_items=0,
                concurrent_items=0,
                estimated_runtime_minutes=1,
                latency_budget_seconds=900,
                spot_eligible=True,
            )
        )
        assert down.tier is ResourceTier.CONTAINER_APP
        assert down.transition == "scale_down"

    def test_workload_selected_spot_warmup_uses_exact_spot_meter(self) -> None:
        plan = _strategist().plan(
            DeployUrgency.NORMAL,
            "t4",
            "test/model",
            estimated_runtime_minutes=30,
            workload=ElasticWorkload(
                urgency=DeployUrgency.NORMAL,
                queued_items=4,
                concurrent_items=1,
                estimated_runtime_minutes=30,
                latency_budget_seconds=300,
                spot_eligible=True,
            ),
        )
        assert plan.warmup is ResourceTier.SPOT_VM
        assert plan.elastic_transition == "scale_up"
        assert "NC8as T4 v3 Spot" in plan.meter_ids


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
        assert result["plan"]["cost_components_usd"] == plan.cost_components_usd
        assert result["plan"]["phase_seconds"] == plan.phase_seconds
        assert result["plan"]["elastic_transition"] == plan.elastic_transition

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
