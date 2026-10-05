"""Single-attempt execution and calibration for approved candidate trials."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from general_ludd.self_improve._candidate_attempt import CandidateAttempt
from general_ludd.self_improve._candidate_calibration import record_calibration_attempt
from general_ludd.self_improve._candidate_execution_types import (
    CandidateEvaluation,
    CandidateExecutionBoundary,
    CandidateExecutionError,
    CandidateExecutionEvent,
    CandidateExecutionScopeFailure,
    CandidateExecutionTrace,
    CandidateTrialCall,
    CandidateTrialExecution,
)
from general_ludd.self_improve._candidate_execution_validation import (
    elapsed_milliseconds,
    emit_execution_trace,
    evaluated_attempt,
    infrastructure_attempt,
)
from general_ludd.self_improve._candidate_trials import CandidateTrial
from general_ludd.self_improve.model_candidates import (
    BackendFailure,
    BackendInfrastructureError,
)
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore


@dataclass(frozen=True, slots=True)
class InvocationOutcome:
    """Opaque response paired with the content-free attempt facts."""

    response: object | None
    attempt: CandidateAttempt


def _infrastructure_trace(
    trial: CandidateTrial,
    attempt: CandidateAttempt,
    failure: BackendFailure,
    *,
    plan_digest: str,
    concurrent: bool,
) -> CandidateExecutionTrace:
    return CandidateExecutionTrace(
        CandidateExecutionEvent.TRIAL_INFRASTRUCTURE_FAILED,
        plan_digest,
        ordinal=trial.ordinal,
        candidate_identity_digest=trial.prediction.candidate_identity_digest,
        provider=trial.prediction.provider,
        outcome=attempt.outcome,
        failure=failure,
        concurrent=concurrent,
    )


def _evaluate_response(
    trial: CandidateTrial,
    call: CandidateTrialCall,
    response: object,
    latency_ms: int,
) -> tuple[CandidateAttempt, BackendFailure | None]:
    try:
        evaluation = call.evaluator(response)
        if not isinstance(evaluation, CandidateEvaluation):
            raise TypeError
        return evaluated_attempt(trial.prediction, evaluation, latency_ms), None
    except Exception:
        return (
            infrastructure_attempt(
                trial.prediction,
                BackendFailure.INTERNAL,
                latency_ms,
            ),
            BackendFailure.INTERNAL,
        )


def invoke_candidate_trial(
    trial: CandidateTrial,
    call: CandidateTrialCall,
    *,
    plan_digest: str,
    concurrent: bool,
    boundary: CandidateExecutionBoundary,
    trace_sink: Callable[[CandidateExecutionTrace], None],
    trace_lock: threading.Lock,
    clock_ns: Callable[[], int],
) -> InvocationOutcome:
    """Invoke and evaluate one exact trial without retrying or rerouting."""
    boundary.require()
    emit_execution_trace(
        trace_sink,
        trace_lock,
        CandidateExecutionTrace(
            CandidateExecutionEvent.TRIAL_STARTED,
            plan_digest,
            ordinal=trial.ordinal,
            candidate_identity_digest=trial.prediction.candidate_identity_digest,
            provider=trial.prediction.provider,
            concurrent=concurrent,
        ),
    )
    try:
        started_ns = clock_ns()
    except Exception:
        started_ns = time.monotonic_ns()
    try:
        response = call.session.generate(
            call.request,
            input_tokens=trial.prediction.predicted_input_tokens,
            max_output_tokens=trial.prediction.predicted_output_tokens,
            estimated_cost_microusd=trial.prediction.predicted_cost_microusd,
        )
    except BackendInfrastructureError as error:
        attempt = infrastructure_attempt(
            trial.prediction,
            error.failure,
            elapsed_milliseconds(clock_ns, started_ns),
        )
        emit_execution_trace(
            trace_sink,
            trace_lock,
            _infrastructure_trace(
                trial,
                attempt,
                error.failure,
                plan_digest=plan_digest,
                concurrent=concurrent,
            ),
        )
        return InvocationOutcome(None, attempt)

    latency_ms = elapsed_milliseconds(clock_ns, started_ns)
    boundary.require()
    attempt, failure = _evaluate_response(trial, call, response, latency_ms)
    if failure is not None:
        emit_execution_trace(
            trace_sink,
            trace_lock,
            _infrastructure_trace(
                trial,
                attempt,
                failure,
                plan_digest=plan_digest,
                concurrent=concurrent,
            ),
        )
        return InvocationOutcome(None, attempt)
    emit_execution_trace(
        trace_sink,
        trace_lock,
        CandidateExecutionTrace(
            CandidateExecutionEvent.TRIAL_EVALUATED,
            plan_digest,
            ordinal=trial.ordinal,
            candidate_identity_digest=trial.prediction.candidate_identity_digest,
            provider=trial.prediction.provider,
            outcome=attempt.outcome,
            concurrent=concurrent,
        ),
    )
    return InvocationOutcome(response, attempt)


def record_candidate_trial(
    trial: CandidateTrial,
    invoked: InvocationOutcome,
    *,
    plan_digest: str,
    concurrent: bool,
    boundary: CandidateExecutionBoundary,
    evidence_store: CapabilityEvidenceStore,
    trace_sink: Callable[[CandidateExecutionTrace], None],
    trace_lock: threading.Lock,
) -> CandidateTrialExecution:
    """Persist public calibration or emit an observable censored skip."""
    scope_failure = boundary.status()
    if scope_failure is not None:
        emit_execution_trace(
            trace_sink,
            trace_lock,
            CandidateExecutionTrace(
                CandidateExecutionEvent.SCOPE_BLOCKED,
                plan_digest,
                ordinal=trial.ordinal,
                candidate_identity_digest=trial.prediction.candidate_identity_digest,
                provider=trial.prediction.provider,
                outcome=invoked.attempt.outcome,
                scope_failure=scope_failure,
                concurrent=concurrent,
            ),
        )
    try:
        calibration = record_calibration_attempt(
            evidence_store,
            invoked.attempt,
            privacy_approved=scope_failure is None,
        )
    except Exception:
        raise CandidateExecutionError(
            CandidateExecutionScopeFailure.EVIDENCE_FAILURE
        ) from None
    event = (
        CandidateExecutionEvent.CALIBRATION_UPDATED
        if calibration.persisted
        else CandidateExecutionEvent.CALIBRATION_SKIPPED
    )
    emit_execution_trace(
        trace_sink,
        trace_lock,
        CandidateExecutionTrace(
            event,
            plan_digest,
            ordinal=trial.ordinal,
            candidate_identity_digest=trial.prediction.candidate_identity_digest,
            provider=trial.prediction.provider,
            outcome=invoked.attempt.outcome,
            calibration_skip_reason=calibration.skip_reason,
            calibration_persisted=calibration.persisted,
            concurrent=concurrent,
        ),
    )
    return CandidateTrialExecution(
        ordinal=trial.ordinal,
        candidate_identity_digest=trial.prediction.candidate_identity_digest,
        response=invoked.response,
        attempt=invoked.attempt,
        calibration=calibration,
    )


__all__ = ("InvocationOutcome", "invoke_candidate_trial", "record_candidate_trial")
