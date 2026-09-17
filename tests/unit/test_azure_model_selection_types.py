"""Tests for Azure model-selection policy and immutable identity types."""

from general_ludd.infra.azure_containerapp_gpu import T4_PROFILE
from general_ludd.infra.azure_containerapp_topology import AzureProfileCapacity
from general_ludd.models.model_deployment_metadata import ModelDeploymentMetadata
from general_ludd.self_improve.azure_model_selection_types import (
    AzureModelSelectionPolicy,
    azure_model_deployment_identity_digest,
    model_selection_identity_digest,
)
from general_ludd.self_improve.model_candidates import (
    AzureContainerAppCandidateIdentity,
)


def test_selection_identity_binds_catalog_runtime_and_observed_profile() -> None:
    """A selected identity changes when any deployment-defining fact changes."""
    image = "registry.example/vllm@sha256:" + "9" * 64
    model = ModelDeploymentMetadata(
        model_id="trusted/code-model",
        revision="a" * 40,
        parameter_count=1_000_000_000,
        context_tokens=32_768,
        storage_bytes=2_000_000_000,
        weight_bits=16,
        license_id="apache-2.0",
        tags=("code", "vllm"),
        pipeline_tag="text-generation",
        library_name="transformers",
        downloads=1,
    )
    policy = AzureModelSelectionPolicy(
        search_query="code",
        search_limit=10,
        allowed_publishers=("trusted",),
        allowed_licenses=("apache-2.0",),
        required_tags=("code",),
        blocked_tags=(),
        minimum_context_tokens=8_192,
        container_image=image,
        profile_capacities=(AzureProfileCapacity(T4_PROFILE, 1, 900_000),),
        max_hourly_cost_microusd=1_000_000,
        kv_cache_mib=1_024,
        runtime_overhead_mib=2_048,
    )

    selected = model_selection_identity_digest(model, policy)
    direct = azure_model_deployment_identity_digest(
        model_id=model.model_id,
        model_revision=model.revision,
        weight_bits=model.weight_bits,
        container_image=image,
        workload_profile_type=T4_PROFILE.workload_profile_type,
    )

    assert selected == direct
    assert len(selected) == 64


def test_selection_identity_matches_runtime_containerapp_evidence_identity() -> None:
    """Selection outcomes can be learned from the exact deployed candidate."""
    image = "registry.example/vllm@sha256:" + "9" * 64
    model = ModelDeploymentMetadata(
        model_id="trusted/code-model",
        revision="a" * 40,
        parameter_count=1_000_000_000,
        context_tokens=32_768,
        storage_bytes=2_000_000_000,
        weight_bits=16,
        license_id="apache-2.0",
        tags=("code", "vllm"),
        pipeline_tag="text-generation",
        library_name="transformers",
        downloads=1,
    )
    policy = AzureModelSelectionPolicy(
        search_query="code",
        search_limit=10,
        allowed_publishers=("trusted",),
        allowed_licenses=("apache-2.0",),
        required_tags=("code",),
        blocked_tags=(),
        minimum_context_tokens=8_192,
        container_image=image,
        profile_capacities=(AzureProfileCapacity(T4_PROFILE, 1, 900_000),),
        max_hourly_cost_microusd=1_000_000,
        kv_cache_mib=1_024,
        runtime_overhead_mib=2_048,
    )
    deployed = AzureContainerAppCandidateIdentity(
        endpoint="https://gludd-candidate.example.eastus.azurecontainerapps.io",
        resource_id=(
            "/subscriptions/11111111-2222-3333-4444-555555555555/"
            "resourceGroups/gludd-models-eastus/providers/Microsoft.App/"
            "containerApps/gludd-candidate"
        ),
        revision_name="gludd-candidate--immutable",
        image_digest="sha256:" + "9" * 64,
        model_name=model.model_id,
        model_revision=model.revision,
        workload_profile_type=T4_PROFILE.workload_profile_type,
    )

    assert model_selection_identity_digest(model, policy) == (
        deployed.evidence_identity_digest
    )
