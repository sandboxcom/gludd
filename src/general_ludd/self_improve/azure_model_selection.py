"""Discover and empirically select immutable models for self-hosted Azure work."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol

from general_ludd.infra.azure_containerapp_gpu import (
    AzureContainerAppGPUUnavailable,
    select_smallest_sufficient_profile,
)
from general_ludd.models.model_registry import (
    ModelDeploymentMetadata,
    ModelSearchResult,
)
from general_ludd.self_improve._candidate_attempt import CandidateAttempt
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
from general_ludd.self_improve.model_candidates import ModelCandidateProvider


class _ModelRegistry(Protocol):
    def search(self, **kwargs: object) -> list[ModelSearchResult]: ...

    def get_deployment_metadata(self, model_id: str) -> ModelDeploymentMetadata: ...


def _admit(
    model: ModelDeploymentMetadata,
    policy: AzureModelSelectionPolicy,
) -> EligibleAzureModel | None:
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
            model_requirement(model, policy),
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
    return EligibleAzureModel(
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
) -> tuple[EligibleAzureModel, ...]:
    results = registry.search(
        query=policy.search_query,
        tags=list(policy.required_tags),
        sort="downloads",
        limit=policy.search_limit,
    )
    unique_ids = tuple(dict.fromkeys(result.model_id for result in results))
    admitted: list[EligibleAzureModel] = []
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
    candidate: EligibleAzureModel,
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
    candidates: tuple[EligibleAzureModel, ...],
    classification: CandidateTaskClassification,
    attempts: Sequence[CandidateAttempt],
) -> tuple[EligibleAzureModel, AzureModelSelectionReason, int, int, float]:
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
