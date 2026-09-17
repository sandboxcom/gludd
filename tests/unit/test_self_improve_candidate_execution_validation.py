"""Direct contracts for candidate execution validation helpers."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import replace
from typing import Any, cast

import pytest

from general_ludd.schemas.benchmark import TaskType
from general_ludd.self_improve._candidate_attempt import CandidateAttemptOutcome
from general_ludd.self_improve._candidate_execution_types import (
    CandidateEvaluation,
    CandidateExecutionError,
    CandidateExecutionEvent,
    CandidateExecutionScopeFailure,
    CandidateExecutionTrace,
)
from general_ludd.self_improve._candidate_execution_validation import (
    elapsed_milliseconds,
    emit_execution_trace,
    evaluated_attempt,
    infrastructure_attempt,
    validated_execution_plan,
    validated_trial_calls,
)
from general_ludd.self_improve._candidate_prediction import CandidatePrediction
from general_ludd.self_improve._candidate_trials import (
    CandidateRanking,
    CandidateTrial,
    CandidateTrialPlan,
    CandidateTrialPurpose,
)
from general_ludd.self_improve.model_candidates import (
    BackendFailure,
    ModelCandidateProvider,
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _prediction() -> CandidatePrediction:
    return CandidatePrediction(
        candidate_identity_digest=_digest("candidate"),
        provider=ModelCandidateProvider.LOCAL_GGUF,
        task_type=TaskType.FEATURE,
        task_kind="code_generation",
        evaluation_stratum_digest=_digest("stratum"),
        prompt_protocol_digest=_digest("prompt"),
        evaluator_digest=_digest("evaluator"),
        sampling_digest=_digest("sampling"),
        privacy_policy_digest=_digest("privacy"),
        predicted_acceptance=0.75,
        predicted_latency_ms=20,
        predicted_input_tokens=10,
        predicted_output_tokens=12,
        predicted_cost_microusd=5,
    )


def _plan() -> CandidateTrialPlan:
    prediction = _prediction()
    ranking = CandidateRanking(prediction, 0, 0, 0.75, 0.5)
    return CandidateTrialPlan(
        trials=(CandidateTrial(0, CandidateTrialPurpose.PREFERRED, prediction, ranking),),
        concurrent=False,
    )


@pytest.mark.parametrize(
    ("finished_ns", "started_ns", "expected"),
    [
        (5_000_000, 1_000_000, 4),
        (-1, 0, 0),
        (86_400_001_000_000, 0, 86_400_000),
        (True, 0, 0),
    ],
)
def test_elapsed_milliseconds_is_non_negative_bounded_and_typed(
    finished_ns: object,
    started_ns: int,
    expected: int,
) -> None:
    assert elapsed_milliseconds(lambda: cast(Any, finished_ns), started_ns) == expected


def test_elapsed_milliseconds_censors_clock_failure() -> None:
    def broken_clock() -> int:
        raise RuntimeError("private-clock-detail")

    assert elapsed_milliseconds(broken_clock, 0) == 0


def test_trace_failure_exposes_only_fixed_scope_category() -> None:
    trace = CandidateExecutionTrace(
        CandidateExecutionEvent.PLAN_AUTHORIZED,
        _digest("plan"),
    )

    def broken_sink(_trace: CandidateExecutionTrace) -> None:
        raise RuntimeError("private-trace-detail")

    with pytest.raises(CandidateExecutionError) as captured:
        emit_execution_trace(broken_sink, threading.Lock(), trace)

    assert captured.value.failure is CandidateExecutionScopeFailure.TRACE_FAILURE
    assert "private-trace-detail" not in repr(captured.value)


def test_attempt_factories_separate_quality_from_infrastructure() -> None:
    prediction = _prediction()
    accepted = evaluated_attempt(
        prediction,
        CandidateEvaluation(True, 0.9, 0, 9, 11, 4),
        7,
    )
    rejected = evaluated_attempt(
        prediction,
        CandidateEvaluation(False, 0.2, 2, 9, 11, 4),
        8,
    )
    failed = infrastructure_attempt(prediction, BackendFailure.TIMEOUT, 9)

    assert accepted.outcome is CandidateAttemptOutcome.ACCEPTED
    assert rejected.outcome is CandidateAttemptOutcome.REJECTED
    assert rejected.blocker_count == 2
    assert failed.outcome is CandidateAttemptOutcome.INFRASTRUCTURE_FAILURE
    assert failed.failure is BackendFailure.TIMEOUT
    assert failed.evaluation_score is None


def test_plan_validation_rejects_duplicate_identity_and_untyped_plan() -> None:
    plan = _plan()
    assert validated_execution_plan(plan) == plan.trials
    duplicate = replace(plan.trials[0], ordinal=1)

    with pytest.raises(ValueError, match="unique"):
        validated_execution_plan(replace(plan, trials=(*plan.trials, duplicate)))
    with pytest.raises(ValueError, match="CandidateTrialPlan"):
        validated_execution_plan(cast(Any, object()))
    assert validated_trial_calls((), ()) == ()
