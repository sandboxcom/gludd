"""Focused Terraform materialization coverage."""

from __future__ import annotations

import warnings
from unittest.mock import patch

from general_ludd.infra.compute import (
    ComputeConfig,
    ComputeProvider,
    GPUType,
    InferenceEngine,
)
from general_ludd.infra.terraform import TerraformGenerator


def test_generate_vsphere_is_warning_free_without_pyvmomi() -> None:
    config = ComputeConfig(
        provider=ComputeProvider.VMWARE,
        gpu_type=GPUType.A100_80,
        model_name="test-model",
    )

    with patch("importlib.util.find_spec", return_value=None), warnings.catch_warnings():
        warnings.simplefilter("error")
        hcl = TerraformGenerator().generate(config)

    assert 'source  = "vmware/vsphere"' in hcl


def test_materialize_azure_a100_stack_with_cost_bounded_ttl(tmp_path) -> None:
    config = ComputeConfig(
        provider=ComputeProvider.AZURE,
        gpu_type=GPUType.A100_80,
        gpu_count=2,
        engine=InferenceEngine.VLLM,
        model_name="org/model",
        region="eastus",
        timeout_minutes=60,
        max_cost_usd=5,
        hourly_rate_usd=10,
    )

    stack = TerraformGenerator().materialize(
        config,
        tmp_path,
        deployment_name="gludd-a100-test",
    )
    tfvars = (stack / "terraform.tfvars").read_text(encoding="utf-8")

    assert stack.name == "azure-vllm"
    assert 'instance_type       = "Standard_NC48ads_A100_v4"' in tfvars
    assert "gpus               = 2" in tfvars
    assert "timeout_minutes     = 30.0" in tfvars
    assert (tmp_path / "modules").is_dir()
