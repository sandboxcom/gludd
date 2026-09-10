"""Discover and empirically select immutable models for self-hosted Azure work."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from general_ludd.infra.azure_containerapp_gpu import (
    AzureContainerAppGPUUnavailable,
    ModelServingRequirement,
    select_smallest_sufficient_profile,
)
from general_ludd.infra.azure_containerapp_topology import AzureProfileCapacity
from general_ludd.models.model_registry import (
    ModelDeploymentMetadata,
    ModelSearchResult,
)
from general_ludd.self_improve._candidate_attempt import CandidateAttempt
from general_ludd.self_improve._candidate_prediction import stable_digest
from general_ludd.self_improve.candidate_classification import (
    CandidateTaskClassification,
)
from general_ludd.self_improve.model_candidates import ModelCandidateProvider

_IMAGE_RE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
_QUERY_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
_MAX_SEARCH_RESULTS = 100
_MAX_COST_MICROUSD = 1_000_000_000_000


class _ModelRegistry(Protocol):
    def search(self, **kwargs: object) -> list[ModelSearchResult]: ...

    def get_deployment_metadata(self, model_id: str) -> ModelDeploymentMetadata: ...


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
class _EligibleModel:
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


def _requirement(
    model: ModelDeploymentMetadata,
    policy: AzureModelSelectionPolicy,
) -> ModelServingRequirement:
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
        _requirement(model, policy),
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


def _admit(
    model: ModelDeploymentMetadata,
    policy: AzureModelSelectionPolicy,
) -> _EligibleModel | None:
    publisher = model.model_id.split("/", 1)[0].casefold()
    if publisher not in {item.casefold() for item in policy.allowed_publishers}:
        return None
    if model.license_id.casefold() not in {
        item.casefold() for item in policy.allowed_licenses
    }:
        return None
    tags = {tag.casefold() for tag in model.tags}
    if not {tag.casefold() for tag in policy.required_tags} <= tags:
        return None
    if {tag.casefold() for tag in policy.blocked_tags} & tags:
        return None
    if model.pipeline_tag.casefold() != "text-generation":
        return None
    if model.context_tokens < policy.minimum_context_tokens:
        return None
    try:
        selected = select_smallest_sufficient_profile(
            _requirement(model, policy),
            hardware_profiles=tuple(
                capacity.profile for capacity in policy.profile_capacities
            ),
        )
    except AzureContainerAppGPUUnavailable:
        return None
    hourly = next(
        capacity.hourly_cost_microusd_per_replica
        for capacity in policy.profile_capacities
        if capacity.workload_profile_type
        == selected.profile.workload_profile_type
    )
    if hourly > policy.max_hourly_cost_microusd:
        return None
    return _EligibleModel(
        model=model,
        workload_profile_type=selected.profile.workload_profile_type,
        required_vram_mib=selected.required_vram_mib,
        hourly_cost_microusd=hourly,
        identity_digest=model_selection_identity_digest(model, policy),
    )


def _emit(
    sink: Callable[[Mapping[str, object]], None],
    event: Mapping[str, object],
) -> None:
    try:
        sink(dict(event))
    except Exception:
        raise RuntimeError("Azure model selection trace publication failed") from None


def _discover(
    registry: _ModelRegistry,
    policy: AzureModelSelectionPolicy,
    sink: Callable[[Mapping[str, object]], None],
) -> tuple[_EligibleModel, ...]:
    results = registry.search(
        query=policy.search_query,
        tags=list(policy.required_tags),
        sort="downloads",
        limit=policy.search_limit,
    )
    unique_ids = tuple(dict.fromkeys(result.model_id for result in results))
    admitted: list[_EligibleModel] = []
    for model_id in unique_ids:
        try:
            candidate = _admit(registry.get_deployment_metadata(model_id), policy)
        except (OSError, RuntimeError, ValueError):
            candidate = None
        if candidate is not None:
            admitted.append(candidate)
    _emit(
        sink,
        {
            "event": "SELF_IMPROVE_AZURE_MODEL_DISCOVERY_COMPLETED",
            "discovered_count": len(unique_ids),
            "eligible_count": len(admitted),
            "schema_version": 1,
        },
    )
    return tuple(admitted)


def _observations(
    candidate: _EligibleModel,
    classification: CandidateTaskClassification,
    attempts: Sequence[CandidateAttempt],
) -> tuple[int, int]:
    matched = tuple(
        attempt
        for attempt in attempts
        if attempt.is_evaluated
        and attempt.prediction.provider is ModelCandidateProvider.AZURE_CONTAINER_APP
        and attempt.prediction.task_type is classification.task_type
        and attempt.prediction.task_kind == classification.task_kind
        and attempt.prediction.candidate_identity_digest == candidate.identity_digest
    )
    return len(matched), sum(attempt.accepted for attempt in matched)


def _choose(
    candidates: tuple[_EligibleModel, ...],
    classification: CandidateTaskClassification,
    attempts: Sequence[CandidateAttempt],
) -> tuple[_EligibleModel, AzureModelSelectionReason, int, int, float]:
    scored = tuple(
        (candidate, *_observations(candidate, classification, attempts))
        for candidate in candidates
    )
    untested = tuple(item for item in scored if item[1] == 0)
    if untested:
        candidate, trials, accepted = min(
            untested,
            key=lambda item: (
                item[0].hourly_cost_microusd,
                item[0].model.storage_bytes,
                -item[0].model.downloads,
                item[0].identity_digest,
            ),
        )
        return (
            candidate,
            AzureModelSelectionReason.LEAST_TESTED_CHALLENGER,
            trials,
            accepted,
            0.5,
        )
    ranked = tuple(
        (
            candidate,
            trials,
            accepted,
            (1.0 + accepted) / (2.0 + trials),
        )
        for candidate, trials, accepted in scored
    )
    candidate, trials, accepted, posterior = min(
        ranked,
        key=lambda item: (
            -item[3],
            item[0].hourly_cost_microusd,
            item[0].model.storage_bytes,
            item[0].identity_digest,
        ),
    )
    return (
        candidate,
        AzureModelSelectionReason.EMPIRICAL_QUALITY,
        trials,
        accepted,
        posterior,
    )


def discover_and_select_azure_model(
    registry: _ModelRegistry,
    classification: CandidateTaskClassification,
    policy: AzureModelSelectionPolicy,
    *,
    attempts: Sequence[CandidateAttempt],
    trace_sink: Callable[[Mapping[str, object]], None] | None = None,
) -> SelectedAzureModel:
    """Discover eligible models and choose an empirical challenger or winner."""
    if not isinstance(classification, CandidateTaskClassification):
        raise ValueError("classification must be a CandidateTaskClassification")
    if not isinstance(policy, AzureModelSelectionPolicy):
        raise ValueError("policy must be an AzureModelSelectionPolicy")
    observations = tuple(attempts)
    if not all(isinstance(attempt, CandidateAttempt) for attempt in observations):
        raise ValueError("attempts must contain CandidateAttempt values")
    sink = trace_sink or (lambda _event: None)
    if not callable(sink):
        raise ValueError("trace_sink must be callable")
    eligible = _discover(registry, policy, sink)
    if not eligible:
        raise ValueError("no deployable Azure model satisfies the active policy")
    candidate, reason, trials, accepted, posterior = _choose(
        eligible,
        classification,
        observations,
    )
    selected = SelectedAzureModel(
        model=candidate.model,
        container_image=policy.container_image,
        workload_profile_type=candidate.workload_profile_type,
        required_vram_mib=candidate.required_vram_mib,
        hourly_cost_microusd=candidate.hourly_cost_microusd,
        identity_digest=candidate.identity_digest,
        reason=reason,
        evaluated_trials=trials,
        accepted_trials=accepted,
        posterior_acceptance=posterior,
        kv_cache_mib=policy.kv_cache_mib,
        runtime_overhead_mib=policy.runtime_overhead_mib,
        profile_capacities=tuple(
            sorted(
                policy.profile_capacities,
                key=lambda capacity: capacity.workload_profile_type,
            )
        ),
        max_hourly_cost_microusd=policy.max_hourly_cost_microusd,
    )
    _emit(
        sink,
        {
            "candidate_identity_digest": selected.identity_digest,
            "event": "SELF_IMPROVE_AZURE_MODEL_SELECTED",
            "evaluated_trials": selected.evaluated_trials,
            "reason": selected.reason.value,
            "schema_version": 1,
            "workload_profile_type": selected.workload_profile_type,
        },
    )
    return selected


def write_azure_model_selection(output: Path, selection: SelectedAzureModel) -> None:
    """Create one mode-0600 selection artifact without replacing any path."""
    if not isinstance(selection, SelectedAzureModel):
        raise ValueError("selection must be a SelectedAzureModel")
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        raise ValueError("model selection needs a new private output path") from None
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(
                selection.payload(),
                stream,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            stream.write("\n")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


__all__ = (
    "AzureModelSelectionPolicy",
    "AzureModelSelectionReason",
    "SelectedAzureModel",
    "azure_model_deployment_identity_digest",
    "discover_and_select_azure_model",
    "model_selection_identity_digest",
    "write_azure_model_selection",
)
