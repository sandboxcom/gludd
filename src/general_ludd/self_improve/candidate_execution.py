"""Execute explicit candidate trial plans without routing, retry, or fallback."""

from __future__ import annotations

import hmac
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor

from general_ludd.self_improve._candidate_execution_runtime import (
    InvocationOutcome,
    invoke_candidate_trial,
    record_candidate_trial,
)
from general_ludd.self_improve._candidate_execution_types import (
    CandidateEvaluation,
    CandidateExecutionBoundary,
    CandidateExecutionError,
    CandidateExecutionEvent,
    CandidateExecutionResult,
    CandidateExecutionScopeFailure,
    CandidateExecutionTrace,
    CandidateTrialCall,
    CandidateTrialExecution,
    require_execution_digest,
)
from general_ludd.self_improve._candidate_execution_validation import (
    emit_execution_trace,
    validated_execution_plan,
    validated_trial_calls,
)
from general_ludd.self_improve._candidate_trials import CandidateTrial, CandidateTrialPlan
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore


def _authorize_calls(
    trials: tuple[CandidateTrial, ...],
    calls: tuple[CandidateTrialCall, ...],
    boundary: CandidateExecutionBoundary,
) -> None:
    for trial, call in zip(trials, calls, strict=True):
        boundary.require()
        call.session.authorize(
            input_tokens=trial.prediction.predicted_input_tokens,
            max_output_tokens=trial.prediction.predicted_output_tokens,
            estimated_cost_microusd=trial.prediction.predicted_cost_microusd,
        )


def _invoke_serially(
    trials: tuple[CandidateTrial, ...],
    calls: tuple[CandidateTrialCall, ...],
    *,
    plan_digest: str,
    concurrent: bool,
    boundary: CandidateExecutionBoundary,
    trace_sink: Callable[[CandidateExecutionTrace], None],
    trace_lock: threading.Lock,
    clock_ns: Callable[[], int],
) -> tuple[InvocationOutcome, ...]:
    return tuple(
        invoke_candidate_trial(
            trial,
            call,
            plan_digest=plan_digest,
            concurrent=concurrent,
            boundary=boundary,
            trace_sink=trace_sink,
            trace_lock=trace_lock,
            clock_ns=clock_ns,
        )
        for trial, call in zip(trials, calls, strict=True)
    )


def _invoke_concurrently(
    trials: tuple[CandidateTrial, ...],
    calls: tuple[CandidateTrialCall, ...],
    *,
    plan_digest: str,
    boundary: CandidateExecutionBoundary,
    trace_sink: Callable[[CandidateExecutionTrace], None],
    trace_lock: threading.Lock,
    clock_ns: Callable[[], int],
) -> tuple[InvocationOutcome, ...]:
    with ThreadPoolExecutor(
        max_workers=len(trials),
        thread_name_prefix="gludd-candidate-trial",
    ) as executor:
        futures = tuple(
            executor.submit(
                invoke_candidate_trial,
                trial,
                call,
                plan_digest=plan_digest,
                concurrent=True,
                boundary=boundary,
                trace_sink=trace_sink,
                trace_lock=trace_lock,
                clock_ns=clock_ns,
            )
            for trial, call in zip(trials, calls, strict=True)
        )
        return tuple(future.result() for future in futures)


def _record_trials(
    trials: tuple[CandidateTrial, ...],
    invoked: tuple[InvocationOutcome, ...],
    *,
    plan_digest: str,
    concurrent: bool,
    boundary: CandidateExecutionBoundary,
    evidence_store: CapabilityEvidenceStore,
    trace_sink: Callable[[CandidateExecutionTrace], None],
    trace_lock: threading.Lock,
) -> tuple[CandidateTrialExecution, ...]:
    return tuple(
        record_candidate_trial(
            trial,
            outcome,
            plan_digest=plan_digest,
            concurrent=concurrent,
            boundary=boundary,
            evidence_store=evidence_store,
            trace_sink=trace_sink,
            trace_lock=trace_lock,
        )
        for trial, outcome in zip(trials, invoked, strict=True)
    )


def execute_candidate_trial_plan(
    plan: CandidateTrialPlan,
    calls: Sequence[CandidateTrialCall],
    *,
    approved_plan_digest: str,
    boundary: CandidateExecutionBoundary,
    evidence_store: CapabilityEvidenceStore,
    trace_sink: Callable[[CandidateExecutionTrace], None] | None = None,
    clock_ns: Callable[[], int] = time.monotonic_ns,
) -> CandidateExecutionResult:
    """Execute every preauthorized trial once, in serial or concurrently."""
    trials = validated_execution_plan(plan)
    approved = require_execution_digest(approved_plan_digest, "approved_plan_digest")
    if not hmac.compare_digest(approved, plan.plan_digest):
        raise ValueError("approved plan digest does not match the executable plan")
    if not isinstance(boundary, CandidateExecutionBoundary):
        raise ValueError("boundary must be a CandidateExecutionBoundary")
    if not isinstance(evidence_store, CapabilityEvidenceStore):
        raise ValueError("evidence_store must be a CapabilityEvidenceStore")
    sink = trace_sink if trace_sink is not None else (lambda _trace: None)
    if not callable(sink) or not callable(clock_ns):
        raise ValueError("trace_sink and clock_ns must be callable")
    if any(
        trial.prediction.privacy_policy_digest
        != boundary.expected_privacy_policy_digest
        for trial in trials
    ):
        raise ValueError("approved plan privacy identity does not match execution")
    ordered_calls = validated_trial_calls(trials, calls)
    _authorize_calls(trials, ordered_calls, boundary)

    trace_lock = threading.Lock()
    emit_execution_trace(
        sink,
        trace_lock,
        CandidateExecutionTrace(
            CandidateExecutionEvent.PLAN_AUTHORIZED,
            approved,
            concurrent=plan.concurrent,
            trial_count=len(trials),
        ),
    )
    invoked = (
        _invoke_concurrently(
            trials,
            ordered_calls,
            plan_digest=approved,
            boundary=boundary,
            trace_sink=sink,
            trace_lock=trace_lock,
            clock_ns=clock_ns,
        )
        if plan.concurrent and len(trials) > 1
        else _invoke_serially(
            trials,
            ordered_calls,
            plan_digest=approved,
            concurrent=plan.concurrent,
            boundary=boundary,
            trace_sink=sink,
            trace_lock=trace_lock,
            clock_ns=clock_ns,
        )
    )
    completed = _record_trials(
        trials,
        invoked,
        plan_digest=approved,
        concurrent=plan.concurrent,
        boundary=boundary,
        evidence_store=evidence_store,
        trace_sink=sink,
        trace_lock=trace_lock,
    )
    emit_execution_trace(
        sink,
        trace_lock,
        CandidateExecutionTrace(
            CandidateExecutionEvent.PLAN_COMPLETED,
            approved,
            concurrent=plan.concurrent,
            trial_count=len(completed),
        ),
    )
    return CandidateExecutionResult(approved, plan.concurrent, completed)


__all__ = (
    "CandidateEvaluation",
    "CandidateExecutionBoundary",
    "CandidateExecutionError",
    "CandidateExecutionEvent",
    "CandidateExecutionResult",
    "CandidateExecutionScopeFailure",
    "CandidateExecutionTrace",
    "CandidateTrialCall",
    "CandidateTrialExecution",
    "execute_candidate_trial_plan",
)
