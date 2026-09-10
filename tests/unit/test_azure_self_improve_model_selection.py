"""Dynamic Azure model discovery, admission, and empirical selection tests."""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from general_ludd.infra.azure_containerapp_gpu import (
    A100_PROFILE,
    T4_PROFILE,
)
from general_ludd.infra.azure_containerapp_topology import AzureProfileCapacity
from general_ludd.models.model_registry import (
    ModelDeploymentMetadata,
    ModelSearchResult,
)
from general_ludd.schemas.benchmark import TaskType
from general_ludd.self_improve._candidate_attempt import (
    CandidateAttempt,
    CandidateAttemptOutcome,
)
from general_ludd.self_improve._candidate_prediction import CandidatePrediction
from general_ludd.self_improve.azure_model_selection import (
    AzureModelSelectionPolicy,
    AzureModelSelectionReason,
    discover_and_select_azure_model,
    model_selection_identity_digest,
    write_azure_model_selection,
)
from general_ludd.self_improve.candidate_classification import (
    classify_candidate_task,
)
from general_ludd.self_improve.model_candidates import ModelCandidateProvider

IMAGE = "registry.example/vllm@sha256:" + "9" * 64


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _metadata(
    model_id: str,
    *,
    revision: str,
    parameters: int,
    context: int = 32_768,
    license_id: str = "apache-2.0",
    tags: tuple[str, ...] = ("code", "text-generation", "vllm"),
    downloads: int = 100,
) -> ModelDeploymentMetadata:
    return ModelDeploymentMetadata(
        model_id=model_id,
        revision=revision,
        parameter_count=parameters,
        context_tokens=context,
        storage_bytes=parameters * 2,
        weight_bits=16,
        license_id=license_id,
        tags=tuple(sorted(tags)),
        pipeline_tag="text-generation",
        library_name="transformers",
        downloads=downloads,
    )


def _policy() -> AzureModelSelectionPolicy:
    return AzureModelSelectionPolicy(
        search_query="code",
        search_limit=20,
        allowed_publishers=("trusted",),
        allowed_licenses=("apache-2.0",),
        required_tags=("code", "vllm"),
        blocked_tags=("custom_code",),
        minimum_context_tokens=28_672,
        container_image=IMAGE,
        profile_capacities=(
            AzureProfileCapacity(T4_PROFILE, 1, 900_000),
            AzureProfileCapacity(A100_PROFILE, 1, 3_500_000),
        ),
        max_hourly_cost_microusd=4_000_000,
        kv_cache_mib=2_048,
        runtime_overhead_mib=3_072,
    )


class _Registry:
    def __init__(self, models: tuple[ModelDeploymentMetadata, ...]) -> None:
        self.models = {model.model_id: model for model in models}
        self.searches: list[dict[str, object]] = []
        self.hydrated: list[str] = []

    def search(self, **kwargs: object) -> list[ModelSearchResult]:
        self.searches.append(dict(kwargs))
        return [
            ModelSearchResult(model_id=model.model_id, downloads=model.downloads)
            for model in reversed(tuple(self.models.values()))
        ]

    def get_deployment_metadata(self, model_id: str) -> ModelDeploymentMetadata:
        self.hydrated.append(model_id)
        return self.models[model_id]


def _attempt(
    model: ModelDeploymentMetadata,
    policy: AzureModelSelectionPolicy,
    *,
    accepted: bool,
) -> CandidateAttempt:
    classification = classify_candidate_task("Implement a public Python feature.")
    prediction = CandidatePrediction(
        candidate_identity_digest=model_selection_identity_digest(model, policy),
        provider=ModelCandidateProvider.AZURE_CONTAINER_APP,
        task_type=classification.task_type,
        task_kind=classification.task_kind,
        evaluation_stratum_digest=_digest("older-compatible-stratum"),
        prompt_protocol_digest=_digest("prompt"),
        evaluator_digest=_digest("evaluator"),
        sampling_digest=_digest("sampling"),
        privacy_policy_digest=_digest("privacy"),
        predicted_acceptance=0.5,
        predicted_latency_ms=10_000,
        predicted_input_tokens=100,
        predicted_output_tokens=100,
        predicted_cost_microusd=1_000,
    )
    return CandidateAttempt(
        prediction=prediction,
        outcome=(
            CandidateAttemptOutcome.ACCEPTED
            if accepted
            else CandidateAttemptOutcome.REJECTED
        ),
        evaluation_score=1.0 if accepted else 0.0,
        blocker_count=0 if accepted else 1,
        observed_latency_ms=10_000,
        observed_input_tokens=100,
        observed_output_tokens=100,
        observed_cost_microusd=1_000,
    )


def test_discovery_uses_categorical_query_and_selects_cheapest_untested_fit() -> None:
    small = _metadata(
        "trusted/small-coder",
        revision="a" * 40,
        parameters=3_000_000_000,
    )
    large = _metadata(
        "trusted/large-coder",
        revision="b" * 40,
        parameters=16_000_000_000,
        downloads=10_000,
    )
    registry = _Registry((small, large))
    traces: list[dict[str, object]] = []
    task = "Implement customer-specific pricing logic that must never leave the repo."

    selected = discover_and_select_azure_model(
        registry,
        classify_candidate_task(task),
        _policy(),
        attempts=(),
        trace_sink=traces.append,
    )

    assert selected.model == small
    assert selected.workload_profile_type == T4_PROFILE.workload_profile_type
    assert selected.reason is AzureModelSelectionReason.LEAST_TESTED_CHALLENGER
    assert registry.searches == [
        {
            "query": "code",
            "tags": ["code", "vllm"],
            "sort": "downloads",
            "limit": 20,
        }
    ]
    assert task not in json.dumps(registry.searches)
    assert all("small-coder" not in json.dumps(trace) for trace in traces)
    assert traces[-1]["event"] == "SELF_IMPROVE_AZURE_MODEL_SELECTED"


def test_least_tested_challenger_rotates_then_empirical_quality_wins() -> None:
    small = _metadata(
        "trusted/small-coder",
        revision="a" * 40,
        parameters=3_000_000_000,
    )
    large = _metadata(
        "trusted/large-coder",
        revision="b" * 40,
        parameters=16_000_000_000,
    )
    policy = _policy()
    classification = classify_candidate_task("Implement a public Python feature.")

    challenger = discover_and_select_azure_model(
        _Registry((small, large)),
        classification,
        policy,
        attempts=(_attempt(small, policy, accepted=True),),
    )
    assert challenger.model == large
    assert challenger.reason is AzureModelSelectionReason.LEAST_TESTED_CHALLENGER

    learned = discover_and_select_azure_model(
        _Registry((small, large)),
        classification,
        policy,
        attempts=(
            _attempt(small, policy, accepted=False),
            _attempt(small, policy, accepted=False),
            _attempt(large, policy, accepted=True),
            _attempt(large, policy, accepted=True),
        ),
    )
    assert learned.model == large
    assert learned.reason is AzureModelSelectionReason.EMPIRICAL_QUALITY
    assert learned.evaluated_trials == 2
    assert learned.accepted_trials == 2


@pytest.mark.parametrize(
    "model",
    (
        _metadata(
            "untrusted/coder",
            revision="1" * 40,
            parameters=3_000_000_000,
        ),
        _metadata(
            "trusted/wrong-license",
            revision="2" * 40,
            parameters=3_000_000_000,
            license_id="other",
        ),
        _metadata(
            "trusted/custom-code",
            revision="3" * 40,
            parameters=3_000_000_000,
            tags=("code", "custom_code", "text-generation", "vllm"),
        ),
        _metadata(
            "trusted/short-context",
            revision="4" * 40,
            parameters=3_000_000_000,
            context=8_192,
        ),
        _metadata(
            "trusted/too-large",
            revision="5" * 40,
            parameters=70_000_000_000,
        ),
    ),
)
def test_discovery_rejects_untrusted_or_unfitted_candidates(
    model: ModelDeploymentMetadata,
) -> None:
    with pytest.raises(ValueError, match="no deployable Azure model"):
        discover_and_select_azure_model(
            _Registry((model,)),
            classify_candidate_task("Implement a public Python feature."),
            _policy(),
            attempts=(),
        )


def test_selection_writer_is_exclusive_private_and_exact(tmp_path: Path) -> None:
    model = _metadata(
        "trusted/discovered-coder",
        revision="a" * 40,
        parameters=3_000_000_000,
    )
    selection = discover_and_select_azure_model(
        _Registry((model,)),
        classify_candidate_task("Implement a public Python feature."),
        _policy(),
        attempts=(),
    )
    output = tmp_path / "selection.json"

    write_azure_model_selection(output, selection)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert payload["model_id"] == model.model_id
    assert payload["revision"] == model.revision
    assert payload["selection_identity_digest"] == selection.identity_digest
    assert payload["selection_reason"] == "least_tested_challenger"
    assert payload["container_image"] == IMAGE
    assert [
        profile["workload_profile_type"] for profile in payload["profile_capacities"]
    ] == [
        A100_PROFILE.workload_profile_type,
        T4_PROFILE.workload_profile_type,
    ]
    assert "t4_hourly_cost_microusd" not in payload
    assert "a100_hourly_cost_microusd" not in payload
    with pytest.raises(ValueError, match="new private output"):
        write_azure_model_selection(output, selection)


def test_task_type_is_part_of_empirical_selection_scope() -> None:
    model = _metadata(
        "trusted/discovered-coder",
        revision="a" * 40,
        parameters=3_000_000_000,
    )
    unrelated = replace(
        _attempt(model, _policy(), accepted=False),
        prediction=replace(
            _attempt(model, _policy(), accepted=False).prediction,
            task_type=TaskType.DOCUMENTATION,
        ),
    )

    selected = discover_and_select_azure_model(
        _Registry((model,)),
        classify_candidate_task("Implement a public Python feature."),
        _policy(),
        attempts=(unrelated,),
    )

    assert selected.evaluated_trials == 0
