"""Direct empirical-choice contracts for eligible Azure model candidates."""

from __future__ import annotations

from general_ludd.models.model_registry import ModelDeploymentMetadata
from general_ludd.self_improve._candidate_attempt import (
    CandidateAttempt,
    CandidateAttemptOutcome,
)
from general_ludd.self_improve._candidate_prediction import CandidatePrediction
from general_ludd.self_improve.azure_model_empirical_choice import choose_azure_model
from general_ludd.self_improve.azure_model_selection_types import (
    AzureModelSelectionReason,
    EligibleAzureModel,
)
from general_ludd.self_improve.candidate_classification import (
    CandidateTaskClassification,
    classify_candidate_task,
)
from general_ludd.self_improve.model_candidates import ModelCandidateProvider


def _candidate(
    name: str,
    digest_character: str,
    *,
    hourly_cost: int,
    operational_failover: bool = False,
) -> EligibleAzureModel:
    return EligibleAzureModel(
        model=ModelDeploymentMetadata(
            model_id=f"trusted/{name}",
            revision=digest_character * 40,
            parameter_count=1_000_000_000,
            context_tokens=32_768,
            storage_bytes=2_000_000_000,
            weight_bits=16,
            license_id="apache-2.0",
            tags=("code", "vllm"),
            pipeline_tag="text-generation",
            library_name="transformers",
            downloads=100,
        ),
        workload_profile_type="Consumption-GPU-NC8as-T4",
        required_vram_mib=8_192,
        hourly_cost_microusd=hourly_cost,
        identity_digest=digest_character * 64,
        operational_failover=operational_failover,
    )


def _attempt(
    candidate: EligibleAzureModel,
    classification: CandidateTaskClassification,
    *,
    accepted: bool,
) -> CandidateAttempt:
    prediction = CandidatePrediction(
        candidate_identity_digest=candidate.identity_digest,
        provider=ModelCandidateProvider.AZURE_CONTAINER_APP,
        task_type=classification.task_type,
        task_kind=classification.task_kind,
        evaluation_stratum_digest="c" * 64,
        prompt_protocol_digest="d" * 64,
        evaluator_digest="e" * 64,
        sampling_digest="f" * 64,
        privacy_policy_digest="1" * 64,
        predicted_acceptance=0.5,
        predicted_latency_ms=1_000,
        predicted_input_tokens=100,
        predicted_output_tokens=50,
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
        observed_latency_ms=1_000,
        observed_input_tokens=100,
        observed_output_tokens=50,
        observed_cost_microusd=1_000,
    )


def test_untested_choice_prefers_cheapest_candidate_and_marks_failover() -> None:
    classification = classify_candidate_task("Implement a public Python feature.")
    expensive = _candidate("expensive", "a", hourly_cost=900_000)
    failover = _candidate(
        "failover",
        "b",
        hourly_cost=500_000,
        operational_failover=True,
    )

    selected, reason, trials, accepted, posterior = choose_azure_model(
        (expensive, failover),
        classification,
        (),
    )

    assert selected is failover
    assert reason is AzureModelSelectionReason.OPERATIONAL_FAILOVER
    assert (trials, accepted, posterior) == (0, 0, 0.5)


def test_empirical_choice_prefers_higher_posterior_acceptance() -> None:
    classification = classify_candidate_task("Implement a public Python feature.")
    rejected = _candidate("rejected", "a", hourly_cost=400_000)
    accepted = _candidate("accepted", "b", hourly_cost=500_000)

    selected, reason, trials, accepted_count, posterior = choose_azure_model(
        (rejected, accepted),
        classification,
        (
            _attempt(rejected, classification, accepted=False),
            _attempt(accepted, classification, accepted=True),
        ),
    )

    assert selected is accepted
    assert reason is AzureModelSelectionReason.EMPIRICAL_QUALITY
    assert (trials, accepted_count, posterior) == (1, 1, 2 / 3)
