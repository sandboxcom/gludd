"""Compatibility tests for Azure GPU worker SDK public value types."""

from __future__ import annotations

import pytest

from general_ludd.infra.azure_gpu_worker_sdk import (
    AzureGpuWorkerInstance as ExportedInstance,
)
from general_ludd.infra.azure_gpu_worker_sdk import (
    AzureGpuWorkerSdkError as ExportedError,
)
from general_ludd.infra.azure_gpu_worker_sdk_types import (
    AzureGpuWorkerInstance,
    AzureGpuWorkerSdkError,
)


def test_reader_module_preserves_public_type_exports() -> None:
    assert ExportedInstance is AzureGpuWorkerInstance
    assert ExportedError is AzureGpuWorkerSdkError


def test_instance_requires_private_ipv4_and_bounded_arm_id() -> None:
    instance = AzureGpuWorkerInstance(
        host_id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/worker",
        address="10.43.1.4",
    )

    assert instance.address == "10.43.1.4"
    with pytest.raises(ValueError, match="private IPv4"):
        AzureGpuWorkerInstance(host_id=instance.host_id, address="8.8.8.8")


def test_sdk_error_exposes_only_stable_phase() -> None:
    error = AzureGpuWorkerSdkError("resource-inventory")

    assert error.phase == "resource-inventory"
    assert str(error) == "Azure GPU worker SDK readback failed: resource-inventory"
