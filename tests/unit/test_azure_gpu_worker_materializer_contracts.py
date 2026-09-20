"""Compatibility tests for split Azure GPU worker materializer contracts."""

from __future__ import annotations

import general_ludd.infra.azure_gpu_worker_materializer as materializer
import general_ludd.infra.azure_gpu_worker_materializer_contracts as contracts


def test_materializer_spec_is_owned_once_and_reexported() -> None:
    assert (
        materializer.AzureGpuWorkerProvisioningSpec
        is contracts.AzureGpuWorkerProvisioningSpec
    )


def test_materializer_contract_module_retains_exact_public_surface() -> None:
    assert contracts.__all__ == ("AzureGpuWorkerProvisioningSpec",)
