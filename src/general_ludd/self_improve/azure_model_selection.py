"""Discover and empirically select immutable models for self-hosted Azure work."""

from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from general_ludd.infra.azure_containerapp_gpu import (
    AzureContainerAppGPUUnavailable,
    select_smallest_sufficient_profile,
)
from general_ludd.models.model_deployment_metadata import (
    ModelDeploymentMetadataUnavailable,
)
from general_ludd.models.model_registry import (
    ModelDeploymentMetadata,
    ModelSearchResult,
)
from general_ludd.self_improve._candidate_attempt import CandidateAttempt
from general_ludd.self_improve.azure_model_empirical_choice import choose_azure_model
from general_ludd.self_improve.azure_model_rank_sampling import bounded_rank_sample
from general_ludd.self_improve.azure_model_selection_types import (
    AzureModelSelectionPolicy,
    AzureModelSelectionReason,
    EligibleAzureModel,
    SelectedAzureModel,
    azure_model_deployment_identity_digest,
    model_requirement,
    model_selection_identity_digest,
)
from general_ludd.self_improve.candidate_classification import (
    CandidateTaskClassification,
)


class _ModelRegistry(Protocol):
    def search(
        self,
        query: str = "",
        tags: list[str] | None = None,
        sort: str = "downloads",
        limit: int = 20,
        author: str | None = None,
    ) -> list[ModelSearchResult]: ...

    def get_deployment_metadata(self, model_id: str) -> ModelDeploymentMetadata: ...


class _AzureModelRejection(StrEnum):
    """Secret-free reason one discovered model was not deployable."""

    PUBLISHER_NOT_ALLOWED = "publisher_not_allowed"
    LICENSE_NOT_ALLOWED = "license_not_allowed"
    REQUIRED_TAG_MISSING = "required_tag_missing"
    BLOCKED_TAG = "blocked_tag"
    PIPELINE_UNSUPPORTED = "pipeline_unsupported"
    CONTEXT_TOO_SHORT = "context_too_short"
    HARDWARE_UNAVAILABLE = "hardware_unavailable"
    HOURLY_COST_EXCEEDED = "hourly_cost_exceeded"
    METADATA_UNAVAILABLE = "metadata_unavailable"


def _admit(
    model: ModelDeploymentMetadata,
    policy: AzureModelSelectionPolicy,
    unavailable_profile_types: frozenset[str],
) -> tuple[EligibleAzureModel | None, _AzureModelRejection | None]:
    publisher = model.model_id.split("/", 1)[0].casefold()
    if publisher not in {item.casefold() for item in policy.allowed_publishers}:
        return None, _AzureModelRejection.PUBLISHER_NOT_ALLOWED
    if model.license_id.casefold() not in {
        item.casefold() for item in policy.allowed_licenses
    }:
        return None, _AzureModelRejection.LICENSE_NOT_ALLOWED
    tags = {tag.casefold() for tag in model.tags}
    if not {tag.casefold() for tag in policy.required_tags} <= tags:
        return None, _AzureModelRejection.REQUIRED_TAG_MISSING
    if {tag.casefold() for tag in policy.blocked_tags} & tags:
        return None, _AzureModelRejection.BLOCKED_TAG
    if model.pipeline_tag.casefold() != "text-generation":
        return None, _AzureModelRejection.PIPELINE_UNSUPPORTED
    if model.context_tokens < policy.minimum_context_tokens:
        return None, _AzureModelRejection.CONTEXT_TOO_SHORT
    try:
        default_selection = select_smallest_sufficient_profile(
            model_requirement(model, policy),
            hardware_profiles=tuple(
                capacity.profile for capacity in policy.profile_capacities
            ),
        )
        selected = select_smallest_sufficient_profile(
            model_requirement(model, policy),
            hardware_profiles=tuple(
                capacity.profile
                for capacity in policy.profile_capacities
                if capacity.workload_profile_type not in unavailable_profile_types
            ),
        )
    except AzureContainerAppGPUUnavailable:
        return None, _AzureModelRejection.HARDWARE_UNAVAILABLE
    hourly = next(
        capacity.hourly_cost_microusd_per_replica
        for capacity in policy.profile_capacities
        if capacity.workload_profile_type
        == selected.profile.workload_profile_type
    )
    if hourly > policy.max_hourly_cost_microusd:
        return None, _AzureModelRejection.HOURLY_COST_EXCEEDED
    return (
        EligibleAzureModel(
            model=model,
            workload_profile_type=selected.profile.workload_profile_type,
            required_vram_mib=selected.required_vram_mib,
            hourly_cost_microusd=hourly,
            identity_digest=azure_model_deployment_identity_digest(
                model_id=model.model_id,
                model_revision=model.revision,
                weight_bits=model.weight_bits,
                container_image=policy.container_image,
                workload_profile_type=selected.profile.workload_profile_type,
            ),
            operational_failover=(
                selected.profile.workload_profile_type
                != default_selection.profile.workload_profile_type
            ),
        ),
        None,
    )


def _emit(
    sink: Callable[[dict[str, object]], None],
    event: Mapping[str, object],
) -> None:
    try:
        sink(dict(event))
    except Exception:
        raise RuntimeError("Azure model selection trace publication failed") from None


def _discover(
    registry: _ModelRegistry,
    policy: AzureModelSelectionPolicy,
    sink: Callable[[dict[str, object]], None],
    unavailable_profile_types: frozenset[str],
) -> tuple[EligibleAzureModel, ...]:
    publisher_results: list[tuple[ModelSearchResult, ...]] = []
    publisher_query_count = 0
    for publisher in policy.allowed_publishers[: policy.search_limit]:
        publisher_query_count += 1
        ranked = registry.search(
            query=policy.search_query,
            tags=list(policy.required_tags),
            sort="downloads",
            limit=policy.search_limit,
            author=publisher,
        )
        publisher_results.append(
            tuple(
                sorted(
                    {
                        result.model_id: result
                        for result in ranked
                    }.values(),
                    key=lambda result: (-result.downloads, result.model_id),
                )
            )
        )
    results = bounded_rank_sample(publisher_results, policy.search_limit)
    unique_ids = tuple(dict.fromkeys(result.model_id for result in results))
    admitted: list[EligibleAzureModel] = []
    rejection_counts: Counter[str] = Counter()
    for model_id in unique_ids:
        try:
            candidate, rejection = _admit(
                registry.get_deployment_metadata(model_id),
                policy,
                unavailable_profile_types,
            )
        except ModelDeploymentMetadataUnavailable as error:
            candidate = None
            rejection = None
            rejection_counts[f"metadata_{error.failure.value}_unavailable"] += 1
        except (OSError, RuntimeError, ValueError):
            candidate = None
            rejection = _AzureModelRejection.METADATA_UNAVAILABLE
        if candidate is not None:
            admitted.append(candidate)
        elif rejection is not None:
            rejection_counts[rejection.value] += 1
    _emit(
        sink,
        {
            "event": "SELF_IMPROVE_AZURE_MODEL_DISCOVERY_COMPLETED",
            "discovered_count": len(unique_ids),
            "eligible_count": len(admitted),
            "hydrated_count": len(unique_ids),
            "publisher_query_count": publisher_query_count,
            "rank_sampling": "publisher_spread",
            "rejection_counts": dict(sorted(rejection_counts.items())),
            "schema_version": 4,
        },
    )
    return tuple(admitted)


def discover_and_select_azure_model(
    registry: _ModelRegistry,
    classification: CandidateTaskClassification,
    policy: AzureModelSelectionPolicy,
    *,
    attempts: Sequence[CandidateAttempt],
    unavailable_profile_types: frozenset[str] = frozenset(),
    trace_sink: Callable[[dict[str, object]], None] | None = None,
) -> SelectedAzureModel:
    """Discover eligible models and choose an empirical challenger or winner."""
    if not isinstance(classification, CandidateTaskClassification):
        raise ValueError("classification must be a CandidateTaskClassification")
    if not isinstance(policy, AzureModelSelectionPolicy):
        raise ValueError("policy must be an AzureModelSelectionPolicy")
    observations = tuple(attempts)
    if not all(isinstance(attempt, CandidateAttempt) for attempt in observations):
        raise ValueError("attempts must contain CandidateAttempt values")
    if not isinstance(unavailable_profile_types, frozenset) or any(
        not isinstance(profile, str) or not profile
        for profile in unavailable_profile_types
    ):
        raise ValueError("unavailable_profile_types must be a frozenset of identifiers")
    sink = trace_sink or (lambda _event: None)
    if not callable(sink):
        raise ValueError("trace_sink must be callable")
    eligible = _discover(registry, policy, sink, unavailable_profile_types)
    if not eligible:
        raise ValueError("no deployable Azure model satisfies the active policy")
    candidate, reason, trials, accepted, posterior = choose_azure_model(
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
