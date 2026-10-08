"""Compatibility tests for split Azure GPU worker runtime support."""

from __future__ import annotations

import general_ludd.infra.azure_gpu_worker_runtime as runtime
import general_ludd.infra.azure_gpu_worker_runtime_support as support


def test_runtime_support_is_owned_once_and_reexported() -> None:
    assert runtime.AzureGpuWorkerRuntimeError is support.AzureGpuWorkerRuntimeError
    assert runtime.AzureGpuWorkerRuntimeTrace is support.AzureGpuWorkerRuntimeTrace


def test_runtime_support_retains_exact_public_surface() -> None:
    assert set(support.__all__) == {
        "AzureGpuWorkerRuntimeError",
        "AzureGpuWorkerRuntimeTrace",
    }
