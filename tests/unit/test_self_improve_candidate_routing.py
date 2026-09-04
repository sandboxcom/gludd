"""Provider-neutral self-improvement prediction and calibration tests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from typing import Any, cast

import pytest

from general_ludd.schemas.benchmark import TaskType
from general_ludd.self_improve.candidate_routing import (
    CalibrationSkipReason,
    CandidateAttempt,
    CandidateAttemptOutcome,
    CandidatePrediction,
    CandidateTrialPurpose,
    load_calibration_attempts,
    plan_bounded_candidate_trials,
    prequential_brier_skill,
    rank_candidate_predictions,
    record_calibration_attempt,
)
from general_ludd.self_improve.model_candidates import (
    BackendFailure,
    ModelCandidateProvider,
)
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _prediction(
    label: str,
    *,
    provider: ModelCandidateProvider = ModelCandidateProvider.LOCAL_GGUF,
    probability: float = 0.6,
    cost: int = 500,
    latency: int = 100,
    stratum: str = "feature-python-v1",
) -> CandidatePrediction:
    return CandidatePrediction(
        candidate_identity_digest=_digest(f"candidate:{label}"),
        provider=provider,
        task_type=TaskType.FEATURE,
        task_kind="code_generation",
        evaluation_stratum_digest=_digest(stratum),
        prompt_protocol_digest=_digest("prompt-v3"),
        evaluator_digest=_digest("evaluator-v5"),
        sampling_digest=_digest("temperature-0"),
        privacy_policy_digest=_digest("public-policy"),
        predicted_acceptance=probability,
        predicted_latency_ms=latency,
        predicted_input_tokens=400,
        predicted_output_tokens=200,
        predicted_cost_microusd=cost,
    )


def _attempt(
    prediction: CandidatePrediction,
    outcome: CandidateAttemptOutcome,
    *,
    score: float | None = None,
    failure: BackendFailure | None = None,
) -> CandidateAttempt:
    if score is None and outcome is not CandidateAttemptOutcome.INFRASTRUCTURE_FAILURE:
        score = 1.0 if outcome is CandidateAttemptOutcome.ACCEPTED else 0.25
    return CandidateAttempt(
        prediction=prediction,
        outcome=outcome,
        evaluation_score=score,
        blocker_count=1 if outcome is CandidateAttemptOutcome.REJECTED else 0,
        observed_latency_ms=120,
        observed_input_tokens=390,
        observed_output_tokens=180,
        observed_cost_microusd=450,
        failure=failure,
    )


def _accepted(prediction: CandidatePrediction) -> CandidateAttempt:
    return _attempt(prediction, CandidateAttemptOutcome.ACCEPTED)


def _rejected(prediction: CandidatePrediction) -> CandidateAttempt:
    return _attempt(prediction, CandidateAttemptOutcome.REJECTED)


def test_prediction_is_frozen_deterministic_and_contains_no_raw_work() -> None:
    prediction = _prediction("local-a")

    assert len(prediction.prediction_digest) == 64
    assert prediction.prediction_digest == _prediction("local-a").prediction_digest
    assert prediction.task_type is TaskType.FEATURE
    assert set(vars(prediction)) == {
        "candidate_identity_digest",
        "provider",
        "task_type",
        "task_kind",
        "evaluation_stratum_digest",
        "prompt_protocol_digest",
        "evaluator_digest",
        "sampling_digest",
        "privacy_policy_digest",
        "predicted_acceptance",
        "predicted_latency_ms",
        "predicted_input_tokens",
        "predicted_output_tokens",
        "predicted_cost_microusd",
    }
    with pytest.raises(FrozenInstanceError):
        prediction.__setattr__("predicted_acceptance", 0.9)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: replace(value, candidate_identity_digest="not-a-digest"),
        lambda value: replace(value, provider=cast(Any, "azure_foundry")),
        lambda value: replace(value, task_type=cast(Any, "feature")),
        lambda value: replace(value, task_kind="Source/Path"),
        lambda value: replace(value, evaluation_stratum_digest="A" * 64),
        lambda value: replace(value, predicted_acceptance=-0.01),
        lambda value: replace(value, predicted_acceptance=1.01),
        lambda value: replace(value, predicted_acceptance=float("nan")),
        lambda value: replace(value, predicted_latency_ms=0),
        lambda value: replace(value, predicted_input_tokens=-1),
        lambda value: replace(value, predicted_output_tokens=True),
        lambda value: replace(value, predicted_cost_microusd=-1),
    ],
)
def test_prediction_rejects_ambiguous_unbounded_or_untyped_values(mutation: Any) -> None:
    with pytest.raises(ValueError):
        mutation(_prediction("invalid"))


def test_prediction_digest_binds_every_prediction_and_evaluation_field() -> None:
    original = _prediction("bound")
    mutations = (
        replace(original, candidate_identity_digest=_digest("other")),
        replace(original, provider=ModelCandidateProvider.AZURE_FOUNDRY),
        replace(original, task_type=TaskType.BUG_FIX),
        replace(original, task_kind="bug_fixing"),
        replace(original, evaluation_stratum_digest=_digest("other-stratum")),
        replace(original, prompt_protocol_digest=_digest("other-prompt")),
        replace(original, evaluator_digest=_digest("other-evaluator")),
        replace(original, sampling_digest=_digest("other-sampling")),
        replace(original, privacy_policy_digest=_digest("other-policy")),
        replace(original, predicted_acceptance=0.7),
        replace(original, predicted_latency_ms=101),
        replace(original, predicted_input_tokens=401),
        replace(original, predicted_output_tokens=201),
        replace(original, predicted_cost_microusd=501),
    )

    assert all(item.prediction_digest != original.prediction_digest for item in mutations)


@pytest.mark.parametrize(
    ("outcome", "score", "failure", "blockers"),
    [
        (CandidateAttemptOutcome.ACCEPTED, None, None, 0),
        (CandidateAttemptOutcome.REJECTED, None, None, 1),
        (CandidateAttemptOutcome.ACCEPTED, 1.1, None, 0),
        (CandidateAttemptOutcome.REJECTED, -0.1, None, 1),
        (CandidateAttemptOutcome.ACCEPTED, 1.0, BackendFailure.TIMEOUT, 0),
        (CandidateAttemptOutcome.INFRASTRUCTURE_FAILURE, 0.0, BackendFailure.TIMEOUT, 0),
        (CandidateAttemptOutcome.INFRASTRUCTURE_FAILURE, None, None, 0),
        (CandidateAttemptOutcome.INFRASTRUCTURE_FAILURE, None, BackendFailure.TIMEOUT, 1),
    ],
)
def test_attempt_rejects_incoherent_quality_and_infrastructure_states(
    outcome: CandidateAttemptOutcome,
    score: float | None,
    failure: BackendFailure | None,
    blockers: int,
) -> None:
    with pytest.raises(ValueError):
        CandidateAttempt(
            prediction=_prediction("bad-attempt"),
            outcome=outcome,
            evaluation_score=score,
            blocker_count=blockers,
            observed_latency_ms=1,
            observed_input_tokens=0,
            observed_output_tokens=0,
            observed_cost_microusd=0,
            failure=failure,
        )


def test_private_and_infrastructure_attempts_emit_censored_traces_but_do_not_learn(
    tmp_path: Any,
) -> None:
    store = CapabilityEvidenceStore(str(tmp_path / "routing.json"))
    prediction = _prediction(
        "azure-private",
        provider=ModelCandidateProvider.AZURE_FOUNDRY,
    )
    private_attempt = _accepted(prediction)
    infrastructure_attempt = _attempt(
        prediction,
        CandidateAttemptOutcome.INFRASTRUCTURE_FAILURE,
        failure=BackendFailure.AUTHENTICATION,
    )
    events: list[dict[str, object]] = []

    private_update = record_calibration_attempt(
        store,
        private_attempt,
        privacy_approved=False,
        trace_sink=lambda event: events.append(dict(event)),
    )
    infrastructure_update = record_calibration_attempt(
        store,
        infrastructure_attempt,
        privacy_approved=True,
        trace_sink=lambda event: events.append(dict(event)),
    )

    assert private_update.persisted is False
    assert private_update.skip_reason is CalibrationSkipReason.PRIVATE_SCOPE
    assert infrastructure_update.persisted is False
    assert (
        infrastructure_update.skip_reason
        is CalibrationSkipReason.INFRASTRUCTURE_FAILURE
    )
    assert store.list_all() == []
    assert [event["event"] for event in events] == [
        "SELF_IMPROVE_MODEL_CALIBRATION_SKIPPED",
        "SELF_IMPROVE_MODEL_CALIBRATION_SKIPPED",
    ]
    rendered = json.dumps(events, sort_keys=True)
    assert "PRIVATE-CANARY" not in rendered
    assert "azure-private" not in rendered
    assert "services.ai.azure.com" not in rendered
    assert "authentication" not in rendered


def test_public_evaluated_attempt_persists_exact_secret_free_record_and_trace(
    tmp_path: Any,
) -> None:
    store = CapabilityEvidenceStore(str(tmp_path / "routing.json"))
    attempt = _accepted(_prediction("public-local"))
    events: list[dict[str, object]] = []

    update = record_calibration_attempt(
        store,
        attempt,
        privacy_approved=True,
        trace_sink=lambda event: events.append(dict(event)),
    )

    assert update.persisted is True
    assert update.record_count == 1
    assert update.skip_reason is None
    assert events == [update.trace]
    assert update.trace["event"] == "SELF_IMPROVE_MODEL_CALIBRATION_UPDATED"
    records = store.list_all()
    assert len(records) == 1
    assert records[0]["candidate_identity_digest"] == attempt.prediction.candidate_identity_digest
    assert records[0]["prediction_digest"] == attempt.prediction.prediction_digest
    assert records[0]["attempt_digest"] == attempt.attempt_digest
    assert records[0]["accepted"] is True
    assert set(records[0]) == {
        "accepted",
        "attempt_digest",
        "blocker_count",
        "candidate_identity_digest",
        "collection",
        "evaluation_score",
        "evaluation_stratum_digest",
        "evaluator_digest",
        "evidence_digest",
        "observed_cost_microusd",
        "observed_input_tokens",
        "observed_latency_ms",
        "observed_output_tokens",
        "prediction_digest",
        "predicted_acceptance",
        "predicted_cost_microusd",
        "predicted_input_tokens",
        "predicted_latency_ms",
        "predicted_output_tokens",
        "privacy_policy_digest",
        "prompt_protocol_digest",
        "provider",
        "registered_at",
        "sampling_digest",
        "schema_version",
        "task_kind",
        "task_type",
    }


def test_loader_round_trips_valid_records_and_ignores_tampering(tmp_path: Any) -> None:
    store = CapabilityEvidenceStore(str(tmp_path / "routing.json"))
    accepted = _accepted(_prediction("valid"))
    record_calibration_attempt(store, accepted, privacy_approved=True)
    tampered = dict(store.list_all()[0])
    tampered["predicted_acceptance"] = 0.99
    store.register_evidence(tampered)
    malformed = dict(store.list_all()[0])
    malformed["raw_task"] = "PRIVATE-CANARY"
    store.register_evidence(malformed)

    loaded = load_calibration_attempts(
        store,
        evaluation_stratum_digest=accepted.prediction.evaluation_stratum_digest,
    )

    assert loaded == (accepted,)


def test_ranking_uses_only_exact_identity_and_stratum_evidence() -> None:
    proven = _prediction("proven", probability=0.55, cost=900, latency=300)
    unproven = _prediction("unproven", probability=0.9, cost=100, latency=50)
    other_stratum = _prediction("proven", stratum="bug-python-v1")
    attempts = tuple(_accepted(proven) for _ in range(8)) + tuple(
        _rejected(proven) for _ in range(2)
    ) + tuple(_rejected(other_stratum) for _ in range(20))

    ranked = rank_candidate_predictions((unproven, proven), attempts)

    assert [item.prediction for item in ranked] == [proven, unproven]
    assert ranked[0].evaluated_trials == 10
    assert ranked[0].accepted_trials == 8
    assert ranked[1].evaluated_trials == 0
    assert ranked[0].conservative_acceptance > ranked[1].conservative_acceptance


def test_ranking_rejects_duplicate_candidates_and_mixed_prediction_strata() -> None:
    prediction = _prediction("duplicate")
    with pytest.raises(ValueError, match="duplicate"):
        rank_candidate_predictions((prediction, prediction), ())
    with pytest.raises(ValueError, match="stratum"):
        rank_candidate_predictions(
            (prediction, _prediction("other", stratum="other-stratum")),
            (),
        )


def test_bounded_plan_explicitly_combines_local_and_azure_with_one_challenge() -> None:
    local = _prediction("local", probability=0.7, cost=100, latency=100)
    azure = _prediction(
        "azure",
        provider=ModelCandidateProvider.AZURE_FOUNDRY,
        probability=0.8,
        cost=1_000,
        latency=200,
    )
    alternate = _prediction("alternate", probability=0.6, cost=200, latency=150)
    attempts = tuple(_accepted(local) for _ in range(5)) + tuple(
        _rejected(local) for _ in range(2)
    ) + (_accepted(azure),)

    plan = plan_bounded_candidate_trials(
        (local, azure, alternate),
        attempts,
        max_trials=2,
        challenge_trials=1,
        concurrent=True,
    )

    assert len(plan.trials) == 2
    assert plan.concurrent is True
    assert plan.trials[0].purpose is CandidateTrialPurpose.PREFERRED
    assert plan.trials[0].prediction is local
    assert plan.trials[1].purpose is CandidateTrialPurpose.CHALLENGE
    assert plan.trials[1].prediction is alternate
    assert {trial.prediction.provider for trial in plan.trials} == {
        ModelCandidateProvider.LOCAL_GGUF,
    }
    assert plan.candidate_identity_digests == tuple(
        trial.prediction.candidate_identity_digest for trial in plan.trials
    )
    assert len(plan.plan_digest) == 64
    assert "fallback" not in repr(plan).lower()


def test_mixed_plan_can_select_an_explicit_azure_challenge_without_fallback() -> None:
    local = _prediction("local", probability=0.8)
    azure = _prediction(
        "azure",
        provider=ModelCandidateProvider.AZURE_FOUNDRY,
        probability=0.7,
    )
    attempts = tuple(_accepted(local) for _ in range(4))

    plan = plan_bounded_candidate_trials(
        (local, azure),
        attempts,
        max_trials=2,
        challenge_trials=1,
        concurrent=False,
    )

    assert [trial.prediction.provider for trial in plan.trials] == [
        ModelCandidateProvider.LOCAL_GGUF,
        ModelCandidateProvider.AZURE_FOUNDRY,
    ]
    assert [trial.ordinal for trial in plan.trials] == [0, 1]


@pytest.mark.parametrize(
    ("max_trials", "challenge_trials", "concurrent"),
    [
        (0, 0, False),
        (17, 0, False),
        (1, 2, False),
        (1, 0, cast(Any, "yes")),
    ],
)
def test_trial_plan_rejects_unbounded_or_ambiguous_policy(
    max_trials: int,
    challenge_trials: int,
    concurrent: bool,
) -> None:
    with pytest.raises(ValueError):
        plan_bounded_candidate_trials(
            (_prediction("only"),),
            (),
            max_trials=max_trials,
            challenge_trials=challenge_trials,
            concurrent=concurrent,
        )


def test_prequential_brier_skill_measures_predictions_against_task_baseline() -> None:
    probabilities = (0.9, 0.1, 0.8, 0.2)
    outcomes = (
        CandidateAttemptOutcome.ACCEPTED,
        CandidateAttemptOutcome.REJECTED,
        CandidateAttemptOutcome.ACCEPTED,
        CandidateAttemptOutcome.REJECTED,
    )
    attempts = tuple(
        _attempt(_prediction(f"trial-{index}", probability=probability), outcome)
        for index, (probability, outcome) in enumerate(
            zip(probabilities, outcomes, strict=True)
        )
    )

    report = prequential_brier_skill(attempts)

    assert report.evaluated_attempts == 4
    assert report.model_brier_score == pytest.approx(0.025)
    assert report.baseline_brier_score == pytest.approx(0.3261111111)
    assert report.brier_skill_score == pytest.approx(0.9233390119)
    assert report.mean_predicted_acceptance == pytest.approx(0.5)
    assert report.observed_acceptance_rate == pytest.approx(0.5)


def test_infrastructure_failures_are_censored_from_calibration_math() -> None:
    prediction = _prediction("censored", probability=0.9)
    accepted = _accepted(prediction)
    infrastructure = _attempt(
        prediction,
        CandidateAttemptOutcome.INFRASTRUCTURE_FAILURE,
        failure=BackendFailure.RATE_LIMITED,
    )

    report = prequential_brier_skill((infrastructure, accepted, infrastructure))

    assert report.evaluated_attempts == 1
    assert report.model_brier_score == pytest.approx(0.01)
    assert report.baseline_brier_score == pytest.approx(0.25)


def test_empty_calibration_report_is_explicitly_unavailable() -> None:
    report = prequential_brier_skill(())

    assert report.evaluated_attempts == 0
    assert report.model_brier_score is None
    assert report.baseline_brier_score is None
    assert report.brier_skill_score is None
    assert report.mean_predicted_acceptance is None
    assert report.observed_acceptance_rate is None
