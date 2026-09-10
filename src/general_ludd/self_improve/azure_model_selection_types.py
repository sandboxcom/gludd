"""Validated policy, result, and identity types for Azure model selection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from general_ludd.infra.azure_containerapp_gpu import (
    ModelServingRequirement,
    select_smallest_sufficient_profile,
)
from general_ludd.infra.azure_containerapp_topology import AzureProfileCapacity
from general_ludd.models.model_deployment_metadata import ModelDeploymentMetadata
from general_ludd.self_improve._candidate_prediction import stable_digest

_IMAGE_RE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
_QUERY_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
_MAX_SEARCH_RESULTS = 100
_MAX_COST_MICROUSD = 1_000_000_000_000


class AzureModelSelectionReason(StrEnum):
    """Why one dynamically discovered model won this selection cycle."""

    LEAST_TESTED_CHALLENGER = "least_tested_challenger"
    EMPIRICAL_QUALITY = "empirical_quality"


@dataclass(frozen=True, slots=True)
class AzureModelSelectionPolicy:
    """User-owned trust, compatibility, hardware, and cost constraints."""

    search_query: str
    search_limit: int
    allowed_publishers: tuple[str, ...]
    allowed_licenses: tuple[str, ...]
    required_tags: tuple[str, ...]
    blocked_tags: tuple[str, ...]
    minimum_context_tokens: int
    container_image: str
    profile_capacities: tuple[AzureProfileCapacity, ...]
    max_hourly_cost_microusd: int
    kv_cache_mib: int
    runtime_overhead_mib: int

    def __post_init__(self) -> None:
        """Reject ambiguous discovery or effectively unbounded spend policy."""
        if _QUERY_RE.fullmatch(self.search_query) is None:
            raise ValueError("search_query must be one categorical term")
        if (
            isinstance(self.search_limit, bool)
            or not 1 <= self.search_limit <= _MAX_SEARCH_RESULTS
        ):
            raise ValueError("search_limit is outside its hard bound")
        for name, values in (
            ("allowed_publishers", self.allowed_publishers),
            ("allowed_licenses", self.allowed_licenses),
            ("required_tags", self.required_tags),
            ("blocked_tags", self.blocked_tags),
        ):
            if (
                type(values) is not tuple
                or (name != "blocked_tags" and not values)
                or len(values) != len(set(values))
                or any(not isinstance(value, str) or not value for value in values)
            ):
                raise ValueError(f"{name} must be one unique bounded tuple")
        if set(self.required_tags) & set(self.blocked_tags):
            raise ValueError("required_tags and blocked_tags must not overlap")
        if _IMAGE_RE.fullmatch(self.container_image) is None:
            raise ValueError("container_image must use one immutable SHA-256 digest")
        if (
            type(self.profile_capacities) is not tuple
            or not self.profile_capacities
            or not all(
                isinstance(capacity, AzureProfileCapacity)
                for capacity in self.profile_capacities
            )
            or len(
                {
                    capacity.workload_profile_type
                    for capacity in self.profile_capacities
                }
            )
            != len(self.profile_capacities)
        ):
            raise ValueError("profile_capacities must be one unique non-empty tuple")
        for name, value in (
            ("minimum_context_tokens", self.minimum_context_tokens),
            ("max_hourly_cost_microusd", self.max_hourly_cost_microusd),
            ("kv_cache_mib", self.kv_cache_mib),
            ("runtime_overhead_mib", self.runtime_overhead_mib),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < (1 if name == "minimum_context_tokens" else 0)
                or value > _MAX_COST_MICROUSD
            ):
                raise ValueError(f"{name} is outside its hard bound")


@dataclass(frozen=True, slots=True)
class EligibleAzureModel:
    """One catalog model admitted by policy and sized to observed hardware."""

    model: ModelDeploymentMetadata
    workload_profile_type: str
    required_vram_mib: int
    hourly_cost_microusd: int
    identity_digest: str


@dataclass(frozen=True, slots=True)
class SelectedAzureModel:
    """Exact immutable selection passed to the deployment compiler."""

    model: ModelDeploymentMetadata
    container_image: str
    workload_profile_type: str
    required_vram_mib: int
    hourly_cost_microusd: int
    identity_digest: str
    reason: AzureModelSelectionReason
    evaluated_trials: int
    accepted_trials: int
    posterior_acceptance: float
    kv_cache_mib: int
    runtime_overhead_mib: int
    profile_capacities: tuple[AzureProfileCapacity, ...]
    max_hourly_cost_microusd: int

    def payload(self) -> dict[str, object]:
        """Return the exact secret-free deployment selection artifact."""
        return {
            "schema_version": 1,
            "model_id": self.model.model_id,
            "revision": self.model.revision,
            "parameter_count": self.model.parameter_count,
            "weight_bits": self.model.weight_bits,
            "kv_cache_mib": self.kv_cache_mib,
            "runtime_overhead_mib": self.runtime_overhead_mib,
            "profile_capacities": [
                capacity.payload()
                for capacity in self.profile_capacities
            ],
            "max_hourly_cost_microusd": self.max_hourly_cost_microusd,
            "context_tokens": self.model.context_tokens,
            "container_image": self.container_image,
            "workload_profile_type": self.workload_profile_type,
            "selection_identity_digest": self.identity_digest,
            "selection_reason": self.reason.value,
        }


def model_requirement(
    model: ModelDeploymentMetadata,
    policy: AzureModelSelectionPolicy,
) -> ModelServingRequirement:
    """Compile one catalog model into the generic model-serving requirement."""
    return ModelServingRequirement(
        model_id=model.model_id,
        revision=model.revision,
        parameter_count=model.parameter_count,
        weight_bits=model.weight_bits,
        kv_cache_mib=policy.kv_cache_mib,
        runtime_overhead_mib=policy.runtime_overhead_mib,
    )


def model_selection_identity_digest(
    model: ModelDeploymentMetadata,
    policy: AzureModelSelectionPolicy,
) -> str:
    """Bind model, runtime image, and selected GPU shape across redeployments."""
    selection = select_smallest_sufficient_profile(
        model_requirement(model, policy),
        hardware_profiles=tuple(
            capacity.profile for capacity in policy.profile_capacities
        ),
    )
    return azure_model_deployment_identity_digest(
        model_id=model.model_id,
        model_revision=model.revision,
        weight_bits=model.weight_bits,
        container_image=policy.container_image,
        workload_profile_type=selection.profile.workload_profile_type,
    )


def azure_model_deployment_identity_digest(
    *,
    model_id: str,
    model_revision: str,
    weight_bits: int,
    container_image: str,
    workload_profile_type: str,
) -> str:
    """Bind immutable model and runtime facts independent of an app instance."""
    if (
        not isinstance(model_id, str)
        or not model_id
        or len(model_id) > 200
        or any(character.isspace() or ord(character) < 32 for character in model_id)
    ):
        raise ValueError("model_id must be a bounded non-whitespace identifier")
    if not isinstance(model_revision, str) or re.fullmatch(
        r"[0-9a-f]{40}", model_revision
    ) is None:
        raise ValueError("model_revision must be one immutable commit")
    if weight_bits not in {4, 8, 16}:
        raise ValueError("weight_bits must be 4, 8, or 16")
    if _IMAGE_RE.fullmatch(container_image) is None:
        raise ValueError("container_image must use one immutable SHA-256 digest")
    if (
        not isinstance(workload_profile_type, str)
        or not workload_profile_type
        or len(workload_profile_type) > 200
        or any(
            character.isspace() or ord(character) < 32
            for character in workload_profile_type
        )
    ):
        raise ValueError("workload_profile_type must be one bounded identifier")
    return stable_digest(
        {
            "container_image": container_image,
            "model_id": model_id,
            "model_revision": model_revision,
            "protocol": "gludd-azure-model-selection-v1",
            "weight_bits": weight_bits,
            "workload_profile_type": workload_profile_type,
        }
    )


__all__ = (
    "AzureModelSelectionPolicy",
    "AzureModelSelectionReason",
    "EligibleAzureModel",
    "SelectedAzureModel",
    "azure_model_deployment_identity_digest",
    "model_requirement",
    "model_selection_identity_digest",
)
