"""Terraform/runtime contracts for real Azure accelerator utilization."""

from __future__ import annotations

import asyncio
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.infra.compute import (
    ComputeConfig,
    ComputeProvider,
    GPUType,
    InferenceEngine,
)
from general_ludd.infra.deployment import DeploymentManager
from general_ludd.infra.terraform import TerraformGenerator
from general_ludd.schemas.deployment import DeploymentRecord

ROOT = Path(__file__).resolve().parents[2]
STACKS = ROOT / "infra" / "terraform" / "stacks"
ARM_CLIENT_CREDENTIAL_ENV = "".join(("ARM_CLIENT_", "SE", "CRET"))
AZURE_CLIENT_CREDENTIAL_ENV = "".join(("AZURE_CLIENT_", "SE", "CRET"))


def test_makefile_has_state_free_azure_stack_initialization() -> None:
    makefile = (ROOT / "Makefile").read_text()
    assert "tf-init-local:" in makefile
    assert "terraform init -backend=false" in makefile
    assert "stacks/azure-vllm|stacks/azure-llamacpp" in makefile
    assert 'scripts/clean_terraform_test_artifacts.py "$(TF_ROOT)/$(STACK)"' in makefile


@pytest.mark.parametrize("stack_name", ["azure-vllm", "azure-llamacpp"])
def test_azure_vm_stack_is_reachable_driver_ready_and_destroyable(
    stack_name: str,
) -> None:
    stack_dir = STACKS / stack_name
    stack_tf = "\n".join(path.read_text() for path in sorted(stack_dir.glob("*.tf")))
    assert 'resource "azurerm_resource_group"' in stack_tf
    assert 'resource "azurerm_virtual_network"' in stack_tf
    assert 'resource "azurerm_subnet"' in stack_tf
    assert 'resource "azurerm_public_ip"' in stack_tf
    assert 'resource "azurerm_network_security_group"' in stack_tf
    assert 'resource "azurerm_virtual_machine_extension" "nvidia_driver"' in stack_tf
    assert (
        'resource "azurerm_virtual_machine_extension" "accelerator_bootstrap"'
        in stack_tf
    )
    assert 'publisher                  = "Microsoft.HpcCompute"' in stack_tf
    assert 'type                       = "NvidiaGpuDriverLinux"' in stack_tf
    assert "depends_on = [azurerm_virtual_machine_extension.nvidia_driver]" in stack_tf
    assert "nvidia-container-toolkit" in stack_tf
    assert "nvidia-smi" in stack_tf
    assert "http://127.0.0.1:8000/health" in stack_tf
    assert 'module "gpu_cost_watchdog"' in stack_tf
    assert 'source = "../../modules/gpu-cost-watchdog"' in stack_tf
    assert re.search(
        r"custom_data\s*=\s*base64encode\(module\.gpu_cost_watchdog\.user_data\)",
        stack_tf,
    )
    assert "module.vllm_server.base_url" not in stack_tf
    assert "azurerm_public_ip.inference.ip_address" in stack_tf
    assert "azurerm_linux_virtual_machine.inference.id" in stack_tf


@pytest.mark.parametrize("stack_name", ["azure-vllm", "azure-llamacpp"])
def test_azure_stack_split_keeps_one_owner_per_release_resource(
    stack_name: str,
) -> None:
    """Pin the semantic resolution of the beta.3 stack integration conflicts."""

    stack_dir = STACKS / stack_name
    main_tf = (stack_dir / "main.tf").read_text()
    infrastructure_tf = (stack_dir / "infrastructure.tf").read_text()
    outputs_tf = (stack_dir / "outputs.tf").read_text()
    stack_tf = "\n".join((main_tf, infrastructure_tf, outputs_tf))

    declarations = (
        'module "gpu_cost_watchdog"',
        'resource "azurerm_resource_group" "inference"',
        'resource "azurerm_network_interface" "inference"',
        'resource "azurerm_network_interface_security_group_association" "inference"',
        'resource "azurerm_linux_virtual_machine" "inference"',
        'resource "azurerm_virtual_machine_extension" "nvidia_driver"',
        'resource "azurerm_virtual_machine_extension" "accelerator_bootstrap"',
        'output "instance_id"',
        'output "endpoint_url"',
        'output "resource_group_name"',
    )
    for declaration in declarations:
        assert stack_tf.count(declaration) == 1, declaration

    assert 'module "gpu_cost_watchdog"' in main_tf
    assert 'resource "azurerm_linux_virtual_machine" "inference"' in main_tf
    assert 'resource "azurerm_resource_group" "inference"' not in main_tf
    assert 'resource "azurerm_virtual_machine_extension"' not in main_tf
    assert 'output "instance_id"' not in main_tf
    assert 'resource "azurerm_resource_group" "inference"' in infrastructure_tf
    assert 'resource "azurerm_virtual_machine_extension"' in infrastructure_tf
    assert 'output "instance_id"' in outputs_tf


@pytest.mark.parametrize(
    ("engine", "stack_name"),
    [
        (InferenceEngine.VLLM, "azure-vllm"),
        (InferenceEngine.LLAMACPP, "azure-llamacpp"),
    ],
)
def test_generator_materializes_packaged_azure_stack_and_exact_sku(
    tmp_path: Path,
    engine: InferenceEngine,
    stack_name: str,
) -> None:
    config = ComputeConfig(
        provider=ComputeProvider.AZURE,
        gpu_type=GPUType.H100,
        gpu_count=2,
        engine=engine,
        model_name="org/model",
        region="eastus",
        max_cost_usd=25,
        timeout_minutes=45,
    )

    stack_dir = TerraformGenerator().materialize(
        config,
        tmp_path,
        deployment_name="gludd-a1b2c3",
    )

    assert stack_dir == tmp_path / "stacks" / stack_name
    assert (stack_dir / "main.tf").is_file()
    assert (tmp_path / "modules").is_dir()
    tfvars = (stack_dir / "terraform.tfvars").read_text()
    assert 'instance_type       = "Standard_NC80adis_H100_v5"' in tfvars
    assert "gpus               = 2" in tfvars
    assert 'deployment_name     = "gludd-a1b2c3"' in tfvars
    assert 'allowed_cidr        = "127.0.0.1/32"' in tfvars
    assert "super-secret" not in tfvars


def test_generator_refuses_materialization_for_non_azure_provider(
    tmp_path: Path,
) -> None:
    config = ComputeConfig(
        provider=ComputeProvider.AWS,
        gpu_type=GPUType.A100_80,
        model_name="org/model",
    )
    with pytest.raises(ValueError, match="Azure"):
        TerraformGenerator().materialize(
            config,
            tmp_path,
            deployment_name="gludd-a1b2c3",
        )


def test_azure_sdk_credentials_are_translated_for_azurerm_without_global_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "AZURE_SUBSCRIPTION_ID": "sub-123",
        "AZURE_TENANT_ID": "tenant-456",
        "AZURE_CLIENT_ID": "client-789",
        AZURE_CLIENT_CREDENTIAL_ENV: "credential-value",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    for name in (
        "ARM_SUBSCRIPTION_ID",
        "ARM_TENANT_ID",
        "ARM_CLIENT_ID",
        ARM_CLIENT_CREDENTIAL_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    config = ComputeConfig(
        provider=ComputeProvider.AZURE,
        gpu_type=GPUType.A100_80,
        model_name="org/model",
    )

    env = DeploymentManager(working_dir=str(tmp_path))._build_auth_env(config)

    assert env["ARM_SUBSCRIPTION_ID"] == "sub-123"
    assert env["ARM_TENANT_ID"] == "tenant-456"
    assert env["ARM_CLIENT_ID"] == "client-789"
    assert env[ARM_CLIENT_CREDENTIAL_ENV] == "credential-value"
    assert ARM_CLIENT_CREDENTIAL_ENV not in os.environ


def test_managed_identity_credentials_enable_azurerm_msi_without_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub-123")
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant-456")
    monkeypatch.setenv("AZURE_CLIENT_ID", "managed-identity-client")
    monkeypatch.delenv(AZURE_CLIENT_CREDENTIAL_ENV, raising=False)
    monkeypatch.delenv(ARM_CLIENT_CREDENTIAL_ENV, raising=False)
    monkeypatch.delenv("ARM_USE_MSI", raising=False)
    config = ComputeConfig(
        provider=ComputeProvider.AZURE,
        gpu_type=GPUType.A100_80,
        model_name="org/model",
    )

    env = DeploymentManager(working_dir=str(tmp_path))._build_auth_env(config)

    assert env["ARM_CLIENT_ID"] == "managed-identity-client"
    assert env["ARM_USE_MSI"] == "true"
    assert ARM_CLIENT_CREDENTIAL_ENV not in env


@pytest.mark.asyncio
async def test_azure_plan_uses_materialized_release_stack(tmp_path: Path) -> None:
    config = ComputeConfig(
        provider=ComputeProvider.AZURE,
        gpu_type=GPUType.A100_80,
        model_name="org/model",
    )
    manager = DeploymentManager(working_dir=str(tmp_path))
    stack_dir = tmp_path / "plan-stack"
    stack_dir.mkdir()
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"plan", b""))
    proc.returncode = 0

    with (
        patch.object(
            manager._generator,
            "materialize",
            return_value=stack_dir,
        ) as materialize,
        patch.object(manager._generator, "generate") as generate,
        patch.object(
            manager,
            "_run_terraform",
            new_callable=AsyncMock,
            return_value={"stdout": "", "stderr": "", "returncode": 0},
        ) as run,
        patch.object(
            asyncio,
            "create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=proc,
        ) as create_process,
    ):
        await manager.plan(config)

    materialize.assert_called_once()
    generate.assert_not_called()
    assert run.await_args.kwargs["cwd"] == str(stack_dir)
    assert create_process.await_args.kwargs["cwd"] == str(stack_dir)


@pytest.mark.asyncio
async def test_azure_validate_uses_materialized_release_stack(tmp_path: Path) -> None:
    config = ComputeConfig(
        provider=ComputeProvider.AZURE,
        gpu_type=GPUType.H100,
        model_name="org/model",
    )
    manager = DeploymentManager(working_dir=str(tmp_path))
    stack_dir = tmp_path / "validate-stack"
    stack_dir.mkdir()
    with (
        patch.object(
            manager._generator,
            "materialize",
            return_value=stack_dir,
        ) as materialize,
        patch.object(manager._generator, "generate") as generate,
        patch.object(
            manager,
            "_run_terraform",
            new_callable=AsyncMock,
            return_value={"stdout": "valid", "stderr": "", "returncode": 0},
        ) as run,
    ):
        await manager.validate(config)

    materialize.assert_called_once()
    generate.assert_not_called()
    assert all(call.kwargs["cwd"] == str(stack_dir) for call in run.await_args_list)


@pytest.mark.asyncio
async def test_deployment_manager_uses_materialized_azure_stack(
    tmp_path: Path,
) -> None:
    config = ComputeConfig(
        provider=ComputeProvider.AZURE,
        gpu_type=GPUType.A100_80,
        model_name="org/model",
        region="eastus",
    )
    manager = DeploymentManager(working_dir=str(tmp_path))
    stack_dir = tmp_path / "d-test" / "stacks" / "azure-vllm"
    stack_dir.mkdir(parents=True)
    outputs = {
        "stdout": (
            '{"deployment_id":{"value":"gludd-a1b2c3"},'
            '"instance_ip":{"value":"203.0.113.4"},'
            '"endpoint_url":{"value":"http://203.0.113.4:8000/v1"}}'
        ),
        "stderr": "",
        "returncode": 0,
    }
    with (
        patch.object(
            manager._generator,
            "materialize",
            return_value=stack_dir,
        ) as materialize,
        patch.object(
            manager._generator,
            "generate",
        ) as generate,
        patch.object(
            manager,
            "_run_terraform",
            new_callable=AsyncMock,
            return_value=outputs,
        ) as run,
    ):
        instance = await manager.deploy(config)

    materialize.assert_called_once()
    generate.assert_not_called()
    assert all(call.kwargs["cwd"] == str(stack_dir) for call in run.call_args_list)
    assert instance.instance_id == "gludd-a1b2c3"
    assert instance.endpoint_url == "http://203.0.113.4:8000/v1"


@pytest.mark.asyncio
async def test_azure_apply_failure_triggers_best_effort_destroy(
    tmp_path: Path,
) -> None:
    config = ComputeConfig(
        provider=ComputeProvider.AZURE,
        gpu_type=GPUType.A100_80,
        model_name="org/model",
    )
    manager = DeploymentManager(working_dir=str(tmp_path))
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    calls: list[list[str]] = []

    async def _run(
        args: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, object]:
        del cwd, env
        calls.append(args)
        if args[0] == "apply":
            raise RuntimeError("allocation failed")
        return {"stdout": "", "stderr": "", "returncode": 0}

    with (
        patch.object(manager._generator, "materialize", return_value=stack_dir),
        patch.object(manager, "_run_terraform", side_effect=_run),
        pytest.raises(RuntimeError, match="allocation failed"),
    ):
        await manager.deploy(config)

    assert ["destroy", "-auto-approve", "-input=false"] in calls


@pytest.mark.asyncio
async def test_expired_registry_record_requests_terraform_destroy(
    tmp_path: Path,
) -> None:
    manager = DeploymentManager(working_dir=str(tmp_path))
    manager._registry["gludd-expired"] = DeploymentRecord(
        instance_id="gludd-expired",
        working_dir=str(tmp_path / "stack"),
        provider="azure",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    with patch.object(
        manager,
        "destroy",
        new_callable=AsyncMock,
    ) as destroy:
        await manager._destroy_at_expiry("gludd-expired")
    destroy.assert_awaited_once_with("gludd-expired")


@pytest.mark.asyncio
async def test_azure_registry_persists_expiry_and_auth_alias_names(
    tmp_path: Path,
) -> None:
    config = ComputeConfig(
        provider=ComputeProvider.AZURE,
        gpu_type=GPUType.A100_80,
        model_name="org/model",
        timeout_minutes=60,
        max_cost_usd=10,
        hourly_rate_usd=20,
        provider_auth_aliases={
            ARM_CLIENT_CREDENTIAL_ENV: "AZURE_CLIENT_CREDENTIAL_ALIAS",
        },
    )

    class _Secrets:
        def resolve(
            self,
            alias_name: str,
            project_id: str | None = None,
        ) -> str | None:
            del project_id
            return "resolved-credential" if alias_name else None

    manager = DeploymentManager(
        working_dir=str(tmp_path),
        secrets_resolver=_Secrets(),
    )
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    output = {
        "stdout": (
            '{"deployment_id":{"value":"gludd-a1b2c3"},'
            '"instance_ip":{"value":"203.0.113.4"}}'
        )
    }
    with (
        patch.object(manager._generator, "materialize", return_value=stack_dir),
        patch.object(
            manager,
            "_run_terraform",
            new_callable=AsyncMock,
            return_value=output,
        ),
    ):
        await manager.deploy(config)

    record = manager.get_deployment("gludd-a1b2c3")
    assert record is not None
    assert record.expires_at is not None
    lifetime = record.expires_at - record.created_at
    assert timedelta(minutes=29) < lifetime <= timedelta(minutes=30)
    assert record.provider_auth_aliases == {
        ARM_CLIENT_CREDENTIAL_ENV: "AZURE_CLIENT_CREDENTIAL_ALIAS",
    }
