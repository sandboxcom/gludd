"""Fail-closed validation and evidence helpers for candidate execution."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence

from general_ludd.self_improve._candidate_attempt import (
    CandidateAttempt,
    CandidateAttemptOutcome,
)
from general_ludd.self_improve._candidate_execution_types import (
    MAX_CANDIDATE_TRIALS,
    CandidateEvaluation,
    CandidateExecutionError,
    CandidateExecutionScopeFailure,
    CandidateExecutionTrace,
    CandidateTrialCall,
)
from general_ludd.self_improve._candidate_prediction import CandidatePrediction
from general_ludd.self_improve._candidate_trials import (
    CandidateRanking,
    CandidateTrial,
    CandidateTrialPlan,
    CandidateTrialPurpose,
)
from general_ludd.self_improve.model_candidates import BackendFailure


def validated_execution_plan(plan: CandidateTrialPlan) -> tuple[CandidateTrial, ...]:
    """Validate one immutable, bounded, single-policy trial plan."""
    if not isinstance(plan, CandidateTrialPlan):
        raise ValueError("plan must be a CandidateTrialPlan")
    if not isinstance(plan.concurrent, bool):
        raise ValueError("plan concurrency must be an explicit boolean")
    if (
        not isinstance(plan.trials, tuple)
        or not 1 <= len(plan.trials) <= MAX_CANDIDATE_TRIALS
    ):
        raise ValueError("plan must contain between one and sixteen trials")
    identities: list[str] = []
    for ordinal, trial in enumerate(plan.trials):
        if (
            not isinstance(trial, CandidateTrial)
            or isinstance(trial.ordinal, bool)
            or trial.ordinal != ordinal
            or not isinstance(trial.purpose, CandidateTrialPurpose)
            or not isinstance(trial.prediction, CandidatePrediction)
            or not isinstance(trial.ranking, CandidateRanking)
            or trial.ranking.prediction != trial.prediction
            or trial.prediction.predicted_output_tokens < 1
        ):
            raise ValueError("plan contains a malformed candidate trial")
        identities.append(trial.prediction.candidate_identity_digest)
    if len(identities) != len(set(identities)):
        raise ValueError("plan candidate identities must be unique")
    privacy_digests = {
        trial.prediction.privacy_policy_digest for trial in plan.trials
    }
    if len(privacy_digests) != 1:
        raise ValueError("plan must bind one privacy policy identity")
    return plan.trials


def validated_trial_calls(
    trials: tuple[CandidateTrial, ...],
    calls: Sequence[CandidateTrialCall],
) -> tuple[CandidateTrialCall, ...]:
    """Require exactly one identity-matched call for every plan ordinal."""
    invocations = tuple(calls)
    if not all(isinstance(call, CandidateTrialCall) for call in invocations):
        raise ValueError("calls must contain CandidateTrialCall values")
    by_ordinal = {call.ordinal: call for call in invocations}
    if len(by_ordinal) != len(invocations) or set(by_ordinal) != set(range(len(trials))):
        raise ValueError("calls must cover every plan ordinal exactly once")
    ordered = tuple(by_ordinal[ordinal] for ordinal in range(len(trials)))
    for trial, call in zip(trials, ordered, strict=True):
        identity = call.session.candidate_identity
        if (
            identity.identity_digest != trial.prediction.candidate_identity_digest
            or identity.provider is not trial.prediction.provider
        ):
            raise ValueError("call session identity does not match its approved trial")
    return ordered


def emit_execution_trace(
    sink: Callable[[CandidateExecutionTrace], None],
    lock: threading.Lock,
    trace: CandidateExecutionTrace,
) -> None:
    """Serialize trace delivery and translate sink failures without leaking text."""
    try:
        with lock:
            sink(trace)
    except Exception:
        raise CandidateExecutionError(
            CandidateExecutionScopeFailure.TRACE_FAILURE
        ) from None


def elapsed_milliseconds(clock_ns: Callable[[], int], started_ns: int) -> int:
    """Return a bounded duration even when an injected clock is malformed."""
    try:
        elapsed = clock_ns() - started_ns
    except Exception:
        return 0
    if isinstance(elapsed, bool) or not isinstance(elapsed, int):
        return 0
    return max(0, min(elapsed // 1_000_000, 86_400_000))


def infrastructure_attempt(
    prediction: CandidatePrediction,
    failure: BackendFailure,
    latency_ms: int,
) -> CandidateAttempt:
    """Create a censored attempt without model-quality evidence."""
    return CandidateAttempt(
        prediction=prediction,
        outcome=CandidateAttemptOutcome.INFRASTRUCTURE_FAILURE,
        evaluation_score=None,
        blocker_count=0,
        observed_latency_ms=latency_ms,
        observed_input_tokens=0,
        observed_output_tokens=0,
        observed_cost_microusd=0,
        failure=failure,
    )


def evaluated_attempt(
    prediction: CandidatePrediction,
    evaluation: CandidateEvaluation,
    latency_ms: int,
) -> CandidateAttempt:
    """Create one accepted or deterministically rejected quality attempt."""
    return CandidateAttempt(
        prediction=prediction,
        outcome=(
            CandidateAttemptOutcome.ACCEPTED
            if evaluation.accepted
            else CandidateAttemptOutcome.REJECTED
        ),
        evaluation_score=evaluation.evaluation_score,
        blocker_count=evaluation.blocker_count,
        observed_latency_ms=latency_ms,
        observed_input_tokens=evaluation.observed_input_tokens,
        observed_output_tokens=evaluation.observed_output_tokens,
        observed_cost_microusd=evaluation.observed_cost_microusd,
    )


__all__ = (
    "elapsed_milliseconds",
    "emit_execution_trace",
    "evaluated_attempt",
    "infrastructure_attempt",
    "validated_execution_plan",
    "validated_trial_calls",
)
