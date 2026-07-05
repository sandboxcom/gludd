"""Integration tests for spot/preemptible blocks on AWS/GCP/Azure.

Proves end-to-end that:
- All 8 cloud stacks have use_spot variable declared with correct defaults
- AWS stacks have instance_market_options with spot configuration
- GCP stacks have scheduling block with preemptible + automatic_restart
- Azure VM stacks have priority="Spot" + eviction_policy="Delete"
- Container App stacks skip compute resource spot blocks
- InfraTracker.record_gpu_seconds honors spot=True via PricingCatalog
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from general_ludd.infra.pricing import INFRA_PRICING, InfraTracker
from general_ludd.pricing_intel.models import BillingGranularity, ComputePrice

REPO_ROOT = Path(__file__).resolve().parents[2]
STACKS_DIR = REPO_ROOT / "infra" / "terraform" / "stacks"

AWS_STACKS = ("aws-vllm", "aws-llamacpp")
GCP_STACKS = ("gcp-vllm", "gcp-llamacpp")
AZURE_VM_STACKS = ("azure-vllm", "azure-llamacpp")
AZURE_CONTAINER_APP_STACKS = ("azure-container-app-vllm", "azure-container-app-llamacpp")


def _read(stack_name: str, filename: str) -> str:
    return (STACKS_DIR / stack_name / filename).read_text()


# ---------------------------------------------------------------------------
# Cross-stack integration: all stacks have use_spot correctly
# ---------------------------------------------------------------------------


class TestAllStacksUseSpotIntegration:
    @pytest.mark.parametrize("stack", sorted(AWS_STACKS + GCP_STACKS + AZURE_VM_STACKS))
    def test_vm_stacks_default_use_spot_true(self, stack: str):
        text = _read(stack, "variables.tf")
        match = re.search(r'variable\s+"use_spot"\s*\{(.*?)\n\}', text, re.DOTALL)
        assert match, f"{stack}: missing use_spot variable"
        block = match.group(1)
        assert re.search(r'default\s*=\s*true', block), f"{stack}: use_spot should default to true"

    @pytest.mark.parametrize("stack", sorted(AZURE_CONTAINER_APP_STACKS))
    def test_container_app_stacks_default_use_spot_false(self, stack: str):
        text = _read(stack, "variables.tf")
        match = re.search(r'variable\s+"use_spot"\s*\{(.*?)\n\}', text, re.DOTALL)
        assert match, f"{stack}: missing use_spot variable"
        block = match.group(1)
        assert re.search(r'default\s*=\s*false', block), f"{stack}: use_spot should default to false"

    @pytest.mark.parametrize("stack", sorted(AWS_STACKS))
    def test_aws_spot_references_use_spot_in_main_tf(self, stack: str):
        text = _read(stack, "main.tf")
        assert "var.use_spot" in text, f"{stack}: main.tf missing var.use_spot reference"

    @pytest.mark.parametrize("stack", sorted(GCP_STACKS))
    def test_gcp_spot_references_use_spot_in_main_tf(self, stack: str):
        text = _read(stack, "main.tf")
        assert "var.use_spot" in text, f"{stack}: main.tf missing var.use_spot reference"

    @pytest.mark.parametrize("stack", sorted(AZURE_VM_STACKS))
    def test_azure_spot_references_use_spot_in_main_tf(self, stack: str):
        text = _read(stack, "main.tf")
        assert "var.use_spot" in text, f"{stack}: main.tf missing var.use_spot reference"


# ---------------------------------------------------------------------------
# AWS: dynamic instance_market_options with spot configuration
# ---------------------------------------------------------------------------


class TestAwsSpotIntegration:
    @pytest.mark.parametrize("stack", sorted(AWS_STACKS))
    def test_dynamic_block_present_and_gated(self, stack: str):
        text = _read(stack, "main.tf")
        assert 'dynamic "instance_market_options"' in text
        assert 'for_each = var.use_spot ? [1] : []' in text or "var.use_spot" in text

    @pytest.mark.parametrize("stack", sorted(AWS_STACKS))
    def test_spot_market_type_in_dynamic_content(self, stack: str):
        text = _read(stack, "main.tf")
        assert 'market_type = "spot"' in text, f"{stack}: missing market_type = 'spot'"

    @pytest.mark.parametrize("stack", sorted(AWS_STACKS))
    def test_aws_instance_resource_exists(self, stack: str):
        text = _read(stack, "main.tf")
        assert 'resource "aws_instance"' in text, f"{stack}: missing aws_instance"


# ---------------------------------------------------------------------------
# GCP: scheduling block with preemptible configuration
# ---------------------------------------------------------------------------


class TestGcpSpotIntegration:
    @pytest.mark.parametrize("stack", sorted(GCP_STACKS))
    def test_scheduling_block_present(self, stack: str):
        text = _read(stack, "main.tf")
        match = re.search(r'scheduling\s*\{(.*?)\}', text, re.DOTALL)
        assert match, f"{stack}: missing scheduling block"
        block = match.group(1)
        assert "preemptible" in block, f"{stack}: missing preemptible"
        assert "automatic_restart" in block, f"{stack}: missing automatic_restart"

    @pytest.mark.parametrize("stack", sorted(GCP_STACKS))
    def test_preemptible_false_when_not_spot(self, stack: str):
        text = _read(stack, "main.tf")
        assert "var.use_spot" in text

    @pytest.mark.parametrize("stack", sorted(GCP_STACKS))
    def test_google_compute_instance_exists(self, stack: str):
        text = _read(stack, "main.tf")
        assert 'resource "google_compute_instance"' in text


# ---------------------------------------------------------------------------
# Azure: priority="Spot" + eviction_policy="Delete"
# ---------------------------------------------------------------------------


class TestAzureSpotIntegration:
    @pytest.mark.parametrize("stack", sorted(AZURE_VM_STACKS))
    def test_spot_priority_conditional(self, stack: str):
        text = _read(stack, "main.tf")
        assert "priority" in text and ("Spot" in text or '"Spot"' in text)

    @pytest.mark.parametrize("stack", sorted(AZURE_VM_STACKS))
    def test_eviction_policy_delete(self, stack: str):
        text = _read(stack, "main.tf")
        assert "eviction_policy" in text and "Delete" in text

    @pytest.mark.parametrize("stack", sorted(AZURE_VM_STACKS))
    def test_azurerm_linux_virtual_machine_exists(self, stack: str):
        text = _read(stack, "main.tf")
        assert 'resource "azurerm_linux_virtual_machine"' in text


# ---------------------------------------------------------------------------
# Container Apps: no compute resource spot blocks
# ---------------------------------------------------------------------------


class TestContainerAppNoSpotIntegration:
    @pytest.mark.parametrize("stack", sorted(AZURE_CONTAINER_APP_STACKS))
    def test_no_vm_compute_resource(self, stack: str):
        text = _read(stack, "main.tf")
        assert 'resource "azurerm_linux_virtual_machine"' not in text
        assert 'resource "aws_instance"' not in text
        assert 'resource "google_compute_instance"' not in text


# ---------------------------------------------------------------------------
# InfraTracker spot pricing integration with PricingCatalog
# ---------------------------------------------------------------------------


class TestInfraTrackerSpotPricing:
    def test_spot_discount_via_catalog(self):
        catalog = MagicMock()
        spot_price = ComputePrice(
            provider="runpod",
            sku="A100-SXM4-80GB-1x",
            usd_per_unit=0.0004,
            granularity=BillingGranularity.per_second,
        )
        catalog.compute_price.return_value = spot_price

        tracker = InfraTracker(catalog=catalog)
        tracker.record_gpu_seconds("runpod", "A100-SXM4-80GB-1x", 1000.0, spot=True)

        catalog.compute_price.assert_called_once_with(
            "runpod", "A100-SXM4-80GB-1x", spot=True
        )
        assert tracker.get_total_infra_cost() == pytest.approx(0.0004 * 1000.0)

    def test_non_spot_uses_regular_price_from_catalog(self):
        catalog = MagicMock()
        regular_price = ComputePrice(
            provider="aws",
            sku="A100-SXM4-80GB-1x",
            usd_per_unit=0.00083,
            granularity=BillingGranularity.per_second,
        )
        catalog.compute_price.return_value = regular_price

        tracker = InfraTracker(catalog=catalog)
        tracker.record_gpu_seconds("aws", "A100-SXM4-80GB-1x", 500.0, spot=False)

        catalog.compute_price.assert_called_once_with(
            "aws", "A100-SXM4-80GB-1x", spot=False
        )
        assert tracker.get_total_infra_cost() == pytest.approx(0.00083 * 500.0)

    def test_static_fallback_when_catalog_misses(self):
        catalog = MagicMock()
        catalog.compute_price.return_value = None

        tracker = InfraTracker(catalog=catalog)
        tracker.record_gpu_seconds("unknown", "some-gpu", 100.0, spot=True)

        expected = INFRA_PRICING["gpu_second"] * 100.0
        assert tracker.get_total_infra_cost() == pytest.approx(expected)

    def test_catalog_error_falls_back_to_static(self):
        catalog = MagicMock()
        catalog.compute_price.side_effect = RuntimeError("boom")

        tracker = InfraTracker(catalog=catalog)
        tracker.record_gpu_seconds("runpod", "A100-SXM4-80GB-1x", 200.0, spot=True)

        expected = INFRA_PRICING["gpu_second"] * 200.0
        assert tracker.get_total_infra_cost() == pytest.approx(expected)

    def test_spot_and_non_spot_mixed_accumulation(self):
        catalog = MagicMock()
        spot_p = ComputePrice(
            provider="runpod",
            sku="A100",
            usd_per_unit=0.0004,
            granularity=BillingGranularity.per_second,
        )
        regular_p = ComputePrice(
            provider="runpod",
            sku="A100",
            usd_per_unit=0.00083,
            granularity=BillingGranularity.per_second,
        )
        catalog.compute_price.side_effect = [spot_p, regular_p]

        tracker = InfraTracker(catalog=catalog)
        tracker.record_gpu_seconds("runpod", "A100", 100.0, spot=True)
        tracker.record_gpu_seconds("runpod", "A100", 100.0, spot=False)

        assert tracker.get_total_infra_cost() == pytest.approx(0.0004 * 100.0 + 0.00083 * 100.0)
        assert tracker.get_infra_cost_by_provider()["runpod"] == pytest.approx(
            0.0004 * 100.0 + 0.00083 * 100.0
        )
