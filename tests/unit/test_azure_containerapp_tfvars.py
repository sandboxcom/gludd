"""Fail-closed materialization contracts for the Azure Container App stack."""

from __future__ import annotations

import re

import pytest

from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType
from general_ludd.infra.terraform import TerraformGenerator

SUBSCRIPTION_ID = "11111111-2222-3333-4444-555555555555"
MODEL_REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
IMAGE = "vllm/vllm-openai@sha256:" + "a" * 64


def _config(**overrides: object) -> ComputeConfig:
    values: dict[str, object] = {
        "provider": ComputeProvider.AZURE,
        "gpu_type": GPUType.T4,
        "model_name": "Qwen/Qwen2.5-0.5B-Instruct",
        "model_revision": MODEL_REVISION,
        "container_image": IMAGE,
        "deploy_type": "containerapp",
        "region": "eastus",
        "allowed_cidr": "198.51.100.10/32",
        "max_cost_usd": 2.0,
        "timeout_minutes": 45.0,
        "azure_subscription_id": SUBSCRIPTION_ID,
        "azure_resource_group": "gludd-models-eastus",
        "azure_containerapp_environment": "gludd-models-env",
        "azure_workload_profile_name": "gludd-gpu-t4",
    }
    values.update(overrides)
    return ComputeConfig.model_validate(values)


def test_tfvars_bind_existing_environment_and_immutable_artifacts() -> None:
    config = _config()
    tfvars = TerraformGenerator().build_azure_containerapp_tfvars(
        config,
        deployment_name="gludd-proof-a1b2c3",
    )

    resource_group_id = (
        f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/gludd-models-eastus"
    )
    environment_id = (
        f"{resource_group_id}/providers/Microsoft.App/managedEnvironments/"
        "gludd-models-env"
    )
    assert f'resource_group_id = "{resource_group_id}"' in tfvars
    assert f'managed_environment_id = "{environment_id}"' in tfvars
    assert 'workload_profile_name = "gludd-gpu-t4"' in tfvars
    assert 'workload_profile_type = "Consumption-GPU-NC8as-T4"' in tfvars
    assert f'model_revision = "{MODEL_REVISION}"' in tfvars
    assert f'container_image = "{IMAGE}"' in tfvars
    assert 'owner_token = "gludd-proof-a1b2c3"' in tfvars
    assert re.search(r'trace_id = "[0-9a-f]{32}"', tfvars)
    assert re.search(
        r'expires_at_utc = "[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z"',
        tfvars,
    )


@pytest.mark.parametrize(
    "field",
    [
        "model_revision",
        "container_image",
        "azure_subscription_id",
        "azure_resource_group",
        "azure_containerapp_environment",
        "azure_workload_profile_name",
    ],
)
def test_missing_live_binding_fails_before_terraform(field: str) -> None:
    config = _config(**{field: None})

    with pytest.raises(ValueError, match=field):
        TerraformGenerator().build_azure_containerapp_tfvars(
            config,
            deployment_name="gludd-proof-a1b2c3",
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"container_image": "vllm/vllm-openai:v0.10.2"}, "digest-pinned"),
        ({"model_revision": "main"}, "model_revision"),
        ({"allowed_cidr": "0.0.0.0/0"}, "IPv4 /32"),
        ({"max_cost_usd": 5.01}, "max_cost_usd"),
        ({"timeout_minutes": 61}, "timeout_minutes"),
    ],
)
def test_unsafe_live_value_fails_before_terraform(
    overrides: dict[str, object],
    message: str,
) -> None:
    config = _config(**overrides)

    with pytest.raises(ValueError, match=message):
        TerraformGenerator().build_azure_containerapp_tfvars(
            config,
            deployment_name="gludd-proof-a1b2c3",
        )


def test_a100_80_maps_to_the_only_a100_serverless_profile() -> None:
    tfvars = TerraformGenerator().build_azure_containerapp_tfvars(
        _config(gpu_type=GPUType.A100_80),
        deployment_name="gludd-proof-a100",
    )

    assert 'workload_profile_type = "Consumption-GPU-NC24-A100"' in tfvars


def test_a100_40_requirement_upscales_to_the_available_a100_80_profile() -> None:
    tfvars = TerraformGenerator().build_azure_containerapp_tfvars(
        _config(gpu_type=GPUType.A100_40),
        deployment_name="gludd-proof-a100",
    )

    assert 'workload_profile_type = "Consumption-GPU-NC24-A100"' in tfvars
    assert 'gpu_type = "a100_80"' in tfvars
    assert 'gpu_type = "a100_40"' not in tfvars
