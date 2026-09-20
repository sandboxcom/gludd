"""Compatibility tests for split model-worker lifecycle contracts."""

from __future__ import annotations

import general_ludd.infra.model_worker_lifecycle as lifecycle
import general_ludd.infra.model_worker_lifecycle_contracts as contracts


def test_lifecycle_contracts_are_owned_once_and_reexported() -> None:
    exported_names = (
        "ModelWorkerConfigurationRuntime",
        "ModelWorkerDispatchRuntime",
        "ModelWorkerEndpoint",
        "ModelWorkerHost",
        "ModelWorkerInfrastructureRuntime",
        "ModelWorkerLifecycleError",
        "ModelWorkerLifecycleEvent",
        "ModelWorkerLifecyclePolicy",
        "ModelWorkerLifecycleTrace",
        "ProvisionedModelWorkerPool",
    )

    for name in exported_names:
        assert getattr(lifecycle, name) is getattr(contracts, name)


def test_lifecycle_contract_module_retains_exact_public_surface() -> None:
    assert set(contracts.__all__) == {
        "ModelWorkerConfigurationRuntime",
        "ModelWorkerDispatchRuntime",
        "ModelWorkerEndpoint",
        "ModelWorkerHost",
        "ModelWorkerInfrastructureRuntime",
        "ModelWorkerLifecycleError",
        "ModelWorkerLifecycleEvent",
        "ModelWorkerLifecyclePolicy",
        "ModelWorkerLifecycleTrace",
        "ProvisionedModelWorkerPool",
    }
