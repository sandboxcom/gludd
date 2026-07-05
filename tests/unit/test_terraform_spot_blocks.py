"""Tests for spot/preemptible instance configuration in Terraform stacks.

Verifies each provider stack has the correct spot/preemptible configuration
in its compute resource blocks and that the ``use_spot`` variable is declared.

Provider expectations:
- AWS: ``instance_market_options { market_type = "spot" }`` on ``aws_instance``
- GCP: ``scheduling { preemptible = true; automatic_restart = false }`` on ``google_compute_instance``
- Azure: ``priority = "Spot"`` + ``eviction_policy = "Delete"`` on ``azurerm_linux_virtual_machine``
- Container Apps: no compute resource spot block (not supported); variable present for interface consistency.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STACKS_DIR = REPO_ROOT / "infra" / "terraform" / "stacks"

AWS_STACKS = ("aws-vllm", "aws-llamacpp")
GCP_STACKS = ("gcp-vllm", "gcp-llamacpp")
AZURE_VM_STACKS = ("azure-vllm", "azure-llamacpp")
AZURE_CONTAINER_APP_STACKS = ("azure-container-app-vllm", "azure-container-app-llamacpp")
ALL_CLOUD_STACKS = AWS_STACKS + GCP_STACKS + AZURE_VM_STACKS + AZURE_CONTAINER_APP_STACKS


def _read_main_tf(stack_name: str) -> str:
    path = STACKS_DIR / stack_name / "main.tf"
    assert path.is_file(), f"missing {path}"
    return path.read_text()


def _read_variables_tf(stack_name: str) -> str:
    path = STACKS_DIR / stack_name / "variables.tf"
    assert path.is_file(), f"missing {path}"
    return path.read_text()


# ---------------------------------------------------------------------------
# use_spot variable — present in every cloud stack.
# ---------------------------------------------------------------------------


class TestUseSpotVariable:
    @pytest.mark.parametrize("stack_name", sorted(ALL_CLOUD_STACKS))
    def test_use_spot_variable_declared(self, stack_name: str):
        text = _read_variables_tf(stack_name)
        m = re.search(r'variable\s+"use_spot"\s*\{', text)
        assert m is not None, f"{stack_name}: missing `variable \"use_spot\"` declaration"

    @pytest.mark.parametrize("stack_name", sorted(ALL_CLOUD_STACKS))
    def test_use_spot_variable_has_type_bool(self, stack_name: str):
        text = _read_variables_tf(stack_name)
        block_match = re.search(
            r'variable\s+"use_spot"\s*\{(.*?)\n\}', text, re.DOTALL
        )
        assert block_match is not None, f"{stack_name}: could not parse use_spot block"
        block = block_match.group(1)
        assert 'type' in block and 'bool' in block, (
            f"{stack_name}: use_spot variable must have type = bool"
        )

    @pytest.mark.parametrize("stack_name", sorted(AWS_STACKS + GCP_STACKS + AZURE_VM_STACKS))
    def test_use_spot_defaults_to_true_on_vm_stacks(self, stack_name: str):
        text = _read_variables_tf(stack_name)
        block_match = re.search(
            r'variable\s+"use_spot"\s*\{(.*?)\n\}', text, re.DOTALL
        )
        assert block_match is not None
        block = block_match.group(1)
        assert re.search(r'default\s*=\s*true', block), (
            f"{stack_name}: use_spot should default to true on VM stacks"
        )

    @pytest.mark.parametrize("stack_name", sorted(AZURE_CONTAINER_APP_STACKS))
    def test_use_spot_defaults_to_false_on_container_app_stacks(self, stack_name: str):
        text = _read_variables_tf(stack_name)
        block_match = re.search(
            r'variable\s+"use_spot"\s*\{(.*?)\n\}', text, re.DOTALL
        )
        assert block_match is not None
        block = block_match.group(1)
        assert re.search(r'default\s*=\s*false', block), (
            f"{stack_name}: use_spot should default to false on container app stacks"
        )


# ---------------------------------------------------------------------------
# AWS: instance_market_options { market_type = "spot" } on aws_instance
# ---------------------------------------------------------------------------


class TestAwsSpotBlocks:
    @pytest.mark.parametrize("stack_name", sorted(AWS_STACKS))
    def test_has_aws_instance_with_spot_market(self, stack_name: str):
        text = _read_main_tf(stack_name)
        assert 'resource "aws_instance"' in text, (
            f"{stack_name}: missing aws_instance resource"
        )
        assert "instance_market_options" in text, (
            f"{stack_name}: missing instance_market_options block"
        )
        assert 'market_type' in text, (
            f"{stack_name}: missing market_type attribute in spot block"
        )

    @pytest.mark.parametrize("stack_name", sorted(AWS_STACKS))
    def test_spot_market_uses_dynamic_block_gated_on_use_spot(self, stack_name: str):
        text = _read_main_tf(stack_name)
        assert 'dynamic "instance_market_options"' in text, (
            f"{stack_name}: instance_market_options should be wrapped in a dynamic block"
        )
        assert "var.use_spot" in text, (
            f"{stack_name}: spot block not gated on var.use_spot"
        )

    @pytest.mark.parametrize("stack_name", sorted(AWS_STACKS))
    def test_spot_price_variable_declared(self, stack_name: str):
        text = _read_variables_tf(stack_name)
        assert re.search(r'variable\s+"spot_price"\s*\{', text), (
            f"{stack_name}: missing spot_price variable"
        )


# ---------------------------------------------------------------------------
# GCP: scheduling { preemptible = true; automatic_restart = false }
# ---------------------------------------------------------------------------


class TestGcpSpotBlocks:
    @pytest.mark.parametrize("stack_name", sorted(GCP_STACKS))
    def test_has_google_compute_instance_with_scheduling(self, stack_name: str):
        text = _read_main_tf(stack_name)
        assert 'resource "google_compute_instance"' in text, (
            f"{stack_name}: missing google_compute_instance resource"
        )
        assert "scheduling" in text, (
            f"{stack_name}: missing scheduling block"
        )

    @pytest.mark.parametrize("stack_name", sorted(GCP_STACKS))
    def test_preemptible_gated_on_use_spot(self, stack_name: str):
        text = _read_main_tf(stack_name)
        m = re.search(r'scheduling\s*\{(.*?)\}', text, re.DOTALL)
        assert m is not None, f"{stack_name}: could not find scheduling block"
        block = m.group(1)
        assert "var.use_spot" in block, (
            f"{stack_name}: preemptible not gated on var.use_spot"
        )

    @pytest.mark.parametrize("stack_name", sorted(GCP_STACKS))
    def test_preemptible_and_automatic_restart_configured(self, stack_name: str):
        text = _read_main_tf(stack_name)
        m = re.search(r'scheduling\s*\{(.*?)\}', text, re.DOTALL)
        assert m is not None, f"{stack_name}: could not find scheduling block"
        block = m.group(1)
        assert "preemptible" in block, (
            f"{stack_name}: scheduling block missing preemptible attribute"
        )
        assert "automatic_restart" in block, (
            f"{stack_name}: scheduling block missing automatic_restart attribute"
        )


# ---------------------------------------------------------------------------
# Azure: priority = "Spot" + eviction_policy = "Delete"
# ---------------------------------------------------------------------------


class TestAzureSpotBlocks:
    @pytest.mark.parametrize("stack_name", sorted(AZURE_VM_STACKS))
    def test_has_azurerm_linux_virtual_machine_with_spot(self, stack_name: str):
        text = _read_main_tf(stack_name)
        assert 'resource "azurerm_linux_virtual_machine"' in text, (
            f"{stack_name}: missing azurerm_linux_virtual_machine resource"
        )
        assert "priority" in text, (
            f"{stack_name}: missing priority attribute"
        )
        assert "eviction_policy" in text, (
            f"{stack_name}: missing eviction_policy attribute"
        )

    @pytest.mark.parametrize("stack_name", sorted(AZURE_VM_STACKS))
    def test_spot_priority_conditional_on_use_spot(self, stack_name: str):
        text = _read_main_tf(stack_name)
        assert 'var.use_spot ? "Spot" : "Regular"' in text or (
            "var.use_spot" in text and '"Spot"' in text
        ), (
            f"{stack_name}: priority not conditional on var.use_spot"
        )

    @pytest.mark.parametrize("stack_name", sorted(AZURE_VM_STACKS))
    def test_eviction_policy_delete_on_spot(self, stack_name: str):
        text = _read_main_tf(stack_name)
        assert '"Delete"' in text and "eviction_policy" in text, (
            f"{stack_name}: eviction_policy missing Delete value"
        )
        assert "var.use_spot" in text, (
            f"{stack_name}: eviction_policy not gated on var.use_spot"
        )


# ---------------------------------------------------------------------------
# Container App stacks: no compute resource spot blocks (unsupported).
# ---------------------------------------------------------------------------


class TestContainerAppNoComputeSpotBlock:
    @pytest.mark.parametrize("stack_name", sorted(AZURE_CONTAINER_APP_STACKS))
    def test_no_compute_resource_in_container_app_stacks(self, stack_name: str):
        text = _read_main_tf(stack_name)
        assert 'resource "azurerm_linux_virtual_machine"' not in text, (
            f"{stack_name}: container app stack should not have a VM compute resource"
        )
        assert 'resource "aws_instance"' not in text, (
            f"{stack_name}: container app stack should not have an AWS compute resource"
        )
        assert 'resource "google_compute_instance"' not in text, (
            f"{stack_name}: container app stack should not have a GCP compute resource"
        )
