"""Direct contracts for the provider-neutral Azure availability boundary."""

from __future__ import annotations

import pytest

from general_ludd.infra.azure_containerapp_gpu import ModelServingRequirement
from general_ludd.infra.azure_operational_availability import (
    AzureAvailabilityIndex as PublicAvailabilityIndex,
)
from general_ludd.infra.azure_operational_availability import (
    AzureAvailabilityScope as PublicAvailabilityScope,
)
from general_ludd.infra.azure_operational_availability import (
    build_azure_availability_scope as public_build_scope,
)
from general_ludd.infra.azure_operational_availability_contracts import (
    AzureAvailabilityIndex,
    AzureAvailabilityScope,
    AzureInfrastructurePhase,
    build_azure_availability_scope,
)


def _requirement() -> ModelServingRequirement:
    return ModelServingRequirement(
        model_id="vendor/private-model",
        revision="b" * 40,
        parameter_count=3_000_000_000,
        weight_bits=16,
        kv_cache_mib=2_048,
        runtime_overhead_mib=3_072,
    )


def test_public_module_reexports_contract_objects_by_identity() -> None:
    assert PublicAvailabilityIndex is AzureAvailabilityIndex
    assert PublicAvailabilityScope is AzureAvailabilityScope
    assert public_build_scope is build_azure_availability_scope


def test_contract_builder_produces_content_free_immutable_scope() -> None:
    scope = build_azure_availability_scope(
        location="westus3",
        resource_sku="provider/accelerator-small",
        container_image="registry.example/vllm@sha256:" + "9" * 64,
        requirement=_requirement(),
    )

    assert scope.location == "westus3"
    assert scope.runtime_version_digest == "sha256:" + "9" * 64
    assert len(scope.scope_digest) == 64
    assert "private-model" not in str(scope.payload())


def test_contracts_fail_closed_and_offer_only_a_neutral_unseen_prior() -> None:
    scope = AzureAvailabilityScope(
        location="eastus",
        resource_sku="Standard_NC24ads_A100_v4",
        runtime_version_digest="sha256:" + "a" * 64,
        topology_digest="b" * 64,
    )

    assessment = AzureAvailabilityIndex(()).assess(scope)

    assert assessment.availability_score == 0.5
    assert assessment.observed_outcomes == 0
    assert assessment.feasible is True
    assert AzureInfrastructurePhase.REQUEST.value == "request"
    with pytest.raises(ValueError, match="immutable"):
        build_azure_availability_scope(
            location="eastus",
            resource_sku="Standard_NC24ads_A100_v4",
            container_image="registry.example/vllm:latest",
            requirement=_requirement(),
        )
