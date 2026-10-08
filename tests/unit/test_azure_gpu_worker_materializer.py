"""Tests for owned Azure GPU VM and VMSS OpenTofu materialization."""

from __future__ import annotations

import json
import stat
from dataclasses import replace
from pathlib import Path

import pytest

import general_ludd.infra.azure_gpu_worker_materializer as worker_materializer
from general_ludd.infra.azure_gpu_vm_strategy import AzureExecutionStrategy
from general_ludd.infra.azure_gpu_worker_materializer import (
    AzureGpuWorkerMaterializerError,
    AzureGpuWorkerProvisioningSpec,
    AzureGpuWorkerTerraformMaterializer,
)

_SUBSCRIPTION = "00000000-0000-4000-8000-000000000001"
_RESOURCE_GROUP = "gludd-accelerators"
_RESOURCE_GROUP_ID = f"/subscriptions/{_SUBSCRIPTION}/resourceGroups/{_RESOURCE_GROUP}"
_IDENTITY_ID = (
    f"{_RESOURCE_GROUP_ID}/providers/Microsoft.ManagedIdentity/"
    "userAssignedIdentities/gludd-worker"
)


def _spec(
    strategy: AzureExecutionStrategy = AzureExecutionStrategy.SINGLE_VM,
) -> AzureGpuWorkerProvisioningSpec:
    return AzureGpuWorkerProvisioningSpec(
        strategy=strategy,
        deployment_name="gludd-worker-001",
        subscription_id=_SUBSCRIPTION,
        resource_group_id=_RESOURCE_GROUP_ID,
        resource_group_name=_RESOURCE_GROUP,
        location="eastus",
        vm_size="Standard_NC24ads_A100_v4",
        instance_count=1 if strategy is AzureExecutionStrategy.SINGLE_VM else 2,
        accelerated_networking_enabled=True,
        admin_username="gludd",
        ssh_public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest gludd@test",
        ssh_private_key_path="/run/gludd/keys/azure-worker",
        controller_cidr="203.0.113.4/32",
        inference_port=8000,
        image_publisher="Canonical",
        image_offer="0001-com-ubuntu-server-jammy",
        image_sku="22_04-lts-gen2",
        image_version="22.04.202409020",
        owner_token="gludd-owner-001",
        trace_id="a" * 32,
        expires_at_utc="2026-09-20T12:00:00Z",
        availability_zone=(
            "1" if strategy is AzureExecutionStrategy.SINGLE_VM else None
        ),
        availability_zones=(
            () if strategy is AzureExecutionStrategy.SINGLE_VM else ("1",)
        ),
        user_assigned_identity_id=(
            None if strategy is AzureExecutionStrategy.SINGLE_VM else _IDENTITY_ID
        ),
        rdma_enabled=strategy is AzureExecutionStrategy.VMSS,
    )


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_materializes_single_vm_root_with_private_public_only_inputs(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "worker"
    materializer = AzureGpuWorkerTerraformMaterializer()

    result = materializer.materialize(_spec(), destination, operation_digest="b" * 64)

    assert result == destination.resolve()
    main = (destination / "main.tf").read_text(encoding="utf-8")
    provider = (destination / "provider.tf").read_text(encoding="utf-8")
    variables = (destination / "variables.tf").read_text(encoding="utf-8")
    outputs = (destination / "outputs.tf").read_text(encoding="utf-8")
    assert 'resource "azurerm_linux_virtual_machine" "worker"' in main
    assert "virtual_machine_scale_set" not in main
    assert 'provider "azurerm"' in provider
    assert 'resource_provider_registrations = "none"' in provider
    assert 'variable "ssh_public_key"' in variables
    assert 'output "owned_resource_ids"' in outputs
    tfvars_path = destination / "terraform.tfvars.json"
    tfvars = _read_json(tfvars_path)
    assert tfvars["deployment_name"] == "gludd-worker-001"
    assert tfvars["vm_size"] == "Standard_NC24ads_A100_v4"
    assert tfvars["availability_zone"] == "1"
    assert "ssh_private_key_path" not in tfvars
    assert "/run/gludd/keys/azure-worker" not in tfvars_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(tfvars_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700
    marker = _read_json(destination / ".gludd-azure-gpu-worker-owner.json")
    assert marker == {
        "operation_digest": "b" * 64,
        "protocol": "gludd-azure-gpu-worker-v1",
    }


def test_materializes_vmss_root_with_exact_scale_and_topology_inputs(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "worker"
    spec = replace(
        _spec(AzureExecutionStrategy.VMSS),
        cache_disk_enabled=True,
        cache_price_attested=True,
        cache_disk_size_gb=512,
    )

    AzureGpuWorkerTerraformMaterializer().materialize(
        spec,
        destination,
        operation_digest="c" * 64,
    )

    main = (destination / "main.tf").read_text(encoding="utf-8")
    assert 'resource "azurerm_linux_virtual_machine_scale_set" "worker"' in main
    tfvars = _read_json(destination / "terraform.tfvars.json")
    assert tfvars["instance_count"] == 2
    assert tfvars["availability_zones"] == ["1"]
    assert tfvars["rdma_enabled"] is True
    assert tfvars["user_assigned_identity_id"] == _IDENTITY_ID
    assert tfvars["cache_disk_enabled"] is True
    assert tfvars["cache_price_attested"] is True
    assert tfvars["cache_disk_size_gb"] == 512
    assert "availability_zone" not in tfvars


def test_exact_marker_allows_idempotent_refresh_but_rejects_adoption(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "worker"
    materializer = AzureGpuWorkerTerraformMaterializer()
    materializer.materialize(_spec(), destination, operation_digest="d" * 64)

    materializer.materialize(_spec(), destination, operation_digest="d" * 64)

    with pytest.raises(AzureGpuWorkerMaterializerError) as caught:
        materializer.materialize(_spec(), destination, operation_digest="e" * 64)

    assert caught.value.phase == "state-ownership"


def test_refuses_nonempty_unowned_destination(tmp_path: Path) -> None:
    destination = tmp_path / "worker"
    destination.mkdir()
    (destination / "foreign.tfstate").write_text("{}", encoding="utf-8")

    with pytest.raises(AzureGpuWorkerMaterializerError) as caught:
        AzureGpuWorkerTerraformMaterializer().materialize(
            _spec(),
            destination,
            operation_digest="f" * 64,
        )

    assert caught.value.phase == "state-ownership"


@pytest.mark.parametrize(
    "changes",
    [
        {"strategy": AzureExecutionStrategy.CONTAINER_APPS},
        {"deployment_name": "foreign-worker"},
        {"subscription_id": "not-a-uuid"},
        {"subscription_id": _SUBSCRIPTION.replace("-", "")},
        {"resource_group_name": "bad/group"},
        {"resource_group_id": "/subscriptions/wrong/resourceGroups/rg"},
        {"resource_group_name": "another"},
        {"location": "East US"},
        {"vm_size": "NC24ads_A100_v4"},
        {"accelerated_networking_enabled": 1},
        {"admin_username": "Root"},
        {"instance_count": 2},
        {"availability_zone": "4"},
        {"availability_zones": ("1",)},
        {"rdma_enabled": True},
        {"image_version": "latest"},
        {"image_publisher": ""},
        {"controller_cidr": "0.0.0.0/0"},
        {"controller_cidr": "not-a-cidr"},
        {"controller_cidr": "2001:db8::1/128"},
        {"ssh_public_key": "not-a-key"},
        {"ssh_private_key_path": "relative/key"},  # pragma: allowlist secret
        {"inference_port": True},
        {"owner_token": "OWNER"},
        {"trace_id": "not-a-trace"},
        {"expires_at_utc": "tomorrow"},
        {"use_spot": 1},
        {"max_spot_price": True},
        {"max_spot_price": 0},
        {"os_disk_size_gb": 1},
        {"os_disk_storage_account_type": "UltraSSD_LRS"},
    ],
)
def test_single_vm_spec_rejects_unowned_or_unbounded_inputs(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        replace(_spec(), **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"instance_count": 1},
        {"availability_zone": "1"},
        {"user_assigned_identity_id": None},
        {
            "user_assigned_identity_id": (
                "/subscriptions/00000000-0000-4000-8000-000000000002/"
                "resourceGroups/another/providers/Microsoft.ManagedIdentity/"
                "userAssignedIdentities/worker"
            )
        },
        {"availability_zones": ("1", "1")},
        {"availability_zones": ("4",)},
        {"availability_zones": ("1", "2"), "rdma_enabled": True},
        {"cache_disk_enabled": True, "cache_price_attested": False},
        {"cache_disk_size_gb": 1},
        {"cache_disk_storage_account_type": "Standard_LRS"},
    ],
)
def test_vmss_spec_rejects_unattested_scale_or_storage_inputs(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        replace(_spec(AzureExecutionStrategy.VMSS), **changes)


def test_materializer_rejects_invalid_contract_arguments(tmp_path: Path) -> None:
    materializer = AzureGpuWorkerTerraformMaterializer()

    with pytest.raises(AzureGpuWorkerMaterializerError) as bad_spec:
        materializer.materialize(
            object(),  # type: ignore[arg-type]
            tmp_path / "bad-spec",
            operation_digest="a" * 64,
        )
    with pytest.raises(AzureGpuWorkerMaterializerError) as bad_digest:
        materializer.materialize(
            _spec(),
            tmp_path / "bad-digest",
            operation_digest="not-a-digest",
        )

    assert bad_spec.value.phase == "spec"
    assert bad_digest.value.phase == "operation-digest"


def test_materializer_rejects_symlink_destination(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    destination = tmp_path / "worker"
    destination.symlink_to(target, target_is_directory=True)

    with pytest.raises(AzureGpuWorkerMaterializerError) as caught:
        AzureGpuWorkerTerraformMaterializer().materialize(
            _spec(),
            destination,
            operation_digest="a" * 64,
        )

    assert caught.value.phase == "state-ownership"


def test_materializer_accepts_an_existing_empty_owned_root(tmp_path: Path) -> None:
    destination = tmp_path / "worker"
    destination.mkdir()

    result = AzureGpuWorkerTerraformMaterializer().materialize(
        _spec(),
        destination,
        operation_digest="a" * 64,
    )

    assert result == destination.resolve()


def test_materializer_rejects_missing_or_linked_reviewed_assets(tmp_path: Path) -> None:
    empty_assets = tmp_path / "empty-assets"
    empty_assets.mkdir()
    with pytest.raises(AzureGpuWorkerMaterializerError) as missing:
        AzureGpuWorkerTerraformMaterializer(empty_assets).materialize(
            _spec(),
            tmp_path / "missing-worker",
            operation_digest="a" * 64,
        )

    assets = tmp_path / "linked-assets"
    module = assets / "modules" / "azure-gpu-worker"
    module.mkdir(parents=True)
    target = tmp_path / "foreign-main.tf"
    target.write_text("terraform {}", encoding="utf-8")
    (module / "main.tf").symlink_to(target)
    with pytest.raises(AzureGpuWorkerMaterializerError) as linked:
        AzureGpuWorkerTerraformMaterializer(assets).materialize(
            _spec(),
            tmp_path / "linked-worker",
            operation_digest="a" * 64,
        )

    assert missing.value.phase == "assets"
    assert linked.value.phase == "assets"


def test_materializer_rejects_public_or_malformed_ownership_marker(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "worker"
    materializer = AzureGpuWorkerTerraformMaterializer()
    materializer.materialize(_spec(), destination, operation_digest="a" * 64)
    marker = destination / ".gludd-azure-gpu-worker-owner.json"
    marker.chmod(0o644)

    with pytest.raises(AzureGpuWorkerMaterializerError) as public:
        materializer.materialize(_spec(), destination, operation_digest="a" * 64)

    marker.chmod(0o600)
    marker.write_text("not-json", encoding="utf-8")
    with pytest.raises(AzureGpuWorkerMaterializerError) as malformed:
        materializer.materialize(_spec(), destination, operation_digest="a" * 64)

    assert public.value.phase == "state-ownership"
    assert malformed.value.phase == "state-ownership"


def test_materializer_censors_filesystem_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_copy(_source: Path, _destination: Path) -> None:
        raise OSError("private filesystem detail")

    monkeypatch.setattr(worker_materializer.shutil, "copy2", fail_copy)

    with pytest.raises(AzureGpuWorkerMaterializerError) as caught:
        AzureGpuWorkerTerraformMaterializer().materialize(
            _spec(),
            tmp_path / "worker",
            operation_digest="a" * 64,
        )

    assert caught.value.phase == "materialize"
    assert "private filesystem detail" not in str(caught.value)
