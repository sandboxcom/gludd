"""Execute explicit candidate trial plans without routing, retry, or fallback."""

from __future__ import annotations

import hmac
import re
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from general_ludd.self_improve._candidate_attempt import (
    CandidateAttempt,
    CandidateAttemptOutcome,
)
from general_ludd.self_improve._candidate_calibration import (
    CalibrationSkipReason,
    CalibrationUpdate,
    record_calibration_attempt,
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
    BackendInfrastructureError,
    BoundedCandidateSession,
    ModelCandidateProvider,
)
from general_ludd.self_improve.private_policy import SelfImproveRuntimePolicyGuard
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_TRIALS = 16


class CandidateExecutionEvent(StrEnum):
    """Content-free transitions emitted by one approved plan execution."""

    PLAN_AUTHORIZED = "candidate_plan_authorized"
    TRIAL_STARTED = "candidate_trial_started"
    TRIAL_EVALUATED = "candidate_trial_evaluated"
    TRIAL_INFRASTRUCTURE_FAILED = "candidate_trial_infrastructure_failed"
    CALIBRATION_UPDATED = "candidate_calibration_updated"
    CALIBRATION_SKIPPED = "candidate_calibration_skipped"
    SCOPE_BLOCKED = "candidate_scope_blocked"
    PLAN_COMPLETED = "candidate_plan_completed"


class CandidateExecutionScopeFailure(StrEnum):
    """Fixed categories that cannot expose project or provider details."""

    PROJECT_IDENTITY_DRIFT = "project_identity_drift"
    PRIVATE_SCOPE = "private_scope"
    TRACE_FAILURE = "trace_failure"
    EVIDENCE_FAILURE = "evidence_failure"


class CandidateExecutionError(RuntimeError):
    """Censored execution-boundary refusal with no underlying exception text."""

    def __init__(self, failure: CandidateExecutionScopeFailure) -> None:
        """Retain only one fixed failure category."""
        if not isinstance(failure, CandidateExecutionScopeFailure):
            raise ValueError("failure must be a CandidateExecutionScopeFailure")
        super().__init__(f"candidate execution blocked: {failure.value}")
        self.failure = failure


@dataclass(frozen=True, slots=True)
class CandidateExecutionBoundary:
    """Recheck one approved project binding and privacy scope before effects."""

    policy_guard: SelfImproveRuntimePolicyGuard = field(repr=False, compare=False)
    source_paths: tuple[str, ...] = field(repr=False)
    expected_project_identity_digest: str
    project_identity_probe: Callable[[], str] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        """Reject ambiguous scope authority before any candidate is inspected."""
        if not isinstance(self.policy_guard, SelfImproveRuntimePolicyGuard):
            raise ValueError("policy_guard must be a SelfImproveRuntimePolicyGuard")
        if (
            not isinstance(self.source_paths, tuple)
            or not self.source_paths
            or len(set(self.source_paths)) != len(self.source_paths)
            or not all(type(path) is str and path for path in self.source_paths)
        ):
            raise ValueError("source_paths must be a non-empty unique tuple")
        _require_digest(
            self.expected_project_identity_digest,
            "expected_project_identity_digest",
        )
        _require_digest(self.policy_guard.expected_digest, "privacy policy digest")
        if not callable(self.project_identity_probe):
            raise ValueError("project_identity_probe must be callable")

    @property
    def expected_privacy_policy_digest(self) -> str:
        """Return the policy identity bound into every approved prediction."""
        return self.policy_guard.expected_digest

    def status(self) -> CandidateExecutionScopeFailure | None:
        """Return a censored drift reason, or ``None`` for the approved scope."""
        current_identity: object = None
        try:
            current_identity = self.project_identity_probe()
        except Exception:
            current_identity = None
        if (
            type(current_identity) is not str
            or _DIGEST_RE.fullmatch(current_identity) is None
            or not hmac.compare_digest(
                current_identity,
                self.expected_project_identity_digest,
            )
        ):
            return CandidateExecutionScopeFailure.PROJECT_IDENTITY_DRIFT
        try:
            decision = self.policy_guard.decision(self.source_paths)
        except Exception:
            return CandidateExecutionScopeFailure.PRIVATE_SCOPE
        if (
            not decision.allowed
            or not hmac.compare_digest(
                decision.policy_digest,
                self.expected_privacy_policy_digest,
            )
        ):
            return CandidateExecutionScopeFailure.PRIVATE_SCOPE
        return None

    def require(self) -> None:
        """Fail closed with a fixed message unless both identities still match."""
        failure = self.status()
        if failure is not None:
            raise CandidateExecutionError(failure) from None


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    """Content-free deterministic quality and usage observation."""

    accepted: bool
    evaluation_score: float
    blocker_count: int
    observed_input_tokens: int
    observed_output_tokens: int
    observed_cost_microusd: int

    def __post_init__(self) -> None:
        """Require an explicit binary outcome before constructing an attempt."""
        if not isinstance(self.accepted, bool):
            raise ValueError("accepted must be an explicit boolean")


@dataclass(frozen=True, slots=True)
class CandidateTrialCall:
    """Opaque request and evaluator for one exact plan ordinal and session."""

    ordinal: int
    session: BoundedCandidateSession[Any, Any] = field(repr=False, compare=False)
    request: object = field(repr=False, compare=False)
    evaluator: Callable[[object], CandidateEvaluation] = field(
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        """Validate only call structure; plan authorization is checked as a set."""
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int):
            raise ValueError("ordinal must be a non-boolean integer")
        if self.ordinal < 0 or self.ordinal >= _MAX_TRIALS:
            raise ValueError("ordinal is outside the bounded trial range")
        if not isinstance(self.session, BoundedCandidateSession):
            raise ValueError("session must be a BoundedCandidateSession")
        if not callable(self.evaluator):
            raise ValueError("evaluator must be callable")


@dataclass(frozen=True, slots=True)
class CandidateExecutionTrace:
    """Request- and response-free evidence for one orchestration transition."""

    event: CandidateExecutionEvent
    plan_digest: str
    ordinal: int | None = None
    candidate_identity_digest: str | None = None
    provider: ModelCandidateProvider | None = None
    outcome: CandidateAttemptOutcome | None = None
    failure: BackendFailure | None = None
    scope_failure: CandidateExecutionScopeFailure | None = None
    calibration_skip_reason: CalibrationSkipReason | None = None
    calibration_persisted: bool | None = None
    concurrent: bool = False
    trial_count: int = 0


@dataclass(frozen=True, slots=True)
class CandidateTrialExecution:
    """One completed explicit trial, retaining its response outside trace reprs."""

    ordinal: int
    candidate_identity_digest: str
    response: object | None = field(repr=False, compare=False)
    attempt: CandidateAttempt
    calibration: CalibrationUpdate


@dataclass(frozen=True, slots=True)
class CandidateExecutionResult:
    """Stable plan-order outcomes for a serial or concurrent execution."""

    plan_digest: str
    concurrent: bool
    trials: tuple[CandidateTrialExecution, ...]

    @property
    def attempts(self) -> tuple[CandidateAttempt, ...]:
        """Return candidate attempts in approved plan order."""
        return tuple(trial.attempt for trial in self.trials)


@dataclass(frozen=True, slots=True)
class _InvocationOutcome:
    response: object | None
    attempt: CandidateAttempt


def _require_digest(value: object, field_name: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _validated_plan(plan: CandidateTrialPlan) -> tuple[CandidateTrial, ...]:
    if not isinstance(plan, CandidateTrialPlan):
        raise ValueError("plan must be a CandidateTrialPlan")
    if not isinstance(plan.concurrent, bool):
        raise ValueError("plan concurrency must be an explicit boolean")
    if not isinstance(plan.trials, tuple) or not 1 <= len(plan.trials) <= _MAX_TRIALS:
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


def _validated_calls(
    trials: tuple[CandidateTrial, ...],
    calls: Sequence[CandidateTrialCall],
) -> tuple[CandidateTrialCall, ...]:
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
            identity.identity_digest
            != trial.prediction.candidate_identity_digest
            or identity.provider is not trial.prediction.provider
        ):
            raise ValueError("call session identity does not match its approved trial")
    return ordered


def _emit(
    sink: Callable[[CandidateExecutionTrace], None],
    lock: threading.Lock,
    trace: CandidateExecutionTrace,
) -> None:
    try:
        with lock:
            sink(trace)
    except Exception:
        raise CandidateExecutionError(
            CandidateExecutionScopeFailure.TRACE_FAILURE
        ) from None


def _elapsed_ms(clock_ns: Callable[[], int], started_ns: int) -> int:
    try:
        elapsed = clock_ns() - started_ns
    except Exception:
        return 0
    if isinstance(elapsed, bool) or not isinstance(elapsed, int):
        return 0
    return max(0, min(elapsed // 1_000_000, 86_400_000))


def _infrastructure_attempt(
    prediction: CandidatePrediction,
    failure: BackendFailure,
    latency_ms: int,
) -> CandidateAttempt:
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


def _evaluated_attempt(
    prediction: CandidatePrediction,
    evaluation: CandidateEvaluation,
    latency_ms: int,
) -> CandidateAttempt:
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


def _invoke_trial(
    trial: CandidateTrial,
    call: CandidateTrialCall,
    *,
    plan_digest: str,
    concurrent: bool,
    boundary: CandidateExecutionBoundary,
    trace_sink: Callable[[CandidateExecutionTrace], None],
    trace_lock: threading.Lock,
    clock_ns: Callable[[], int],
) -> _InvocationOutcome:
    boundary.require()
    _emit(
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
        attempt = _infrastructure_attempt(
            trial.prediction,
            error.failure,
            _elapsed_ms(clock_ns, started_ns),
        )
        _emit(
            trace_sink,
            trace_lock,
            CandidateExecutionTrace(
                CandidateExecutionEvent.TRIAL_INFRASTRUCTURE_FAILED,
                plan_digest,
                ordinal=trial.ordinal,
                candidate_identity_digest=trial.prediction.candidate_identity_digest,
                provider=trial.prediction.provider,
                outcome=attempt.outcome,
                failure=error.failure,
                concurrent=concurrent,
            ),
        )
        return _InvocationOutcome(None, attempt)
    latency_ms = _elapsed_ms(clock_ns, started_ns)
    boundary.require()
    try:
        evaluation = call.evaluator(response)
        if not isinstance(evaluation, CandidateEvaluation):
            raise TypeError
        attempt = _evaluated_attempt(trial.prediction, evaluation, latency_ms)
    except Exception:
        attempt = _infrastructure_attempt(
            trial.prediction,
            BackendFailure.INTERNAL,
            latency_ms,
        )
        _emit(
            trace_sink,
            trace_lock,
            CandidateExecutionTrace(
                CandidateExecutionEvent.TRIAL_INFRASTRUCTURE_FAILED,
                plan_digest,
                ordinal=trial.ordinal,
                candidate_identity_digest=trial.prediction.candidate_identity_digest,
                provider=trial.prediction.provider,
                outcome=attempt.outcome,
                failure=BackendFailure.INTERNAL,
                concurrent=concurrent,
            ),
        )
        return _InvocationOutcome(None, attempt)
    _emit(
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
    return _InvocationOutcome(response, attempt)


def _record_trial(
    trial: CandidateTrial,
    invoked: _InvocationOutcome,
    *,
    plan_digest: str,
    concurrent: bool,
    boundary: CandidateExecutionBoundary,
    evidence_store: CapabilityEvidenceStore,
    trace_sink: Callable[[CandidateExecutionTrace], None],
    trace_lock: threading.Lock,
) -> CandidateTrialExecution:
    scope_failure = boundary.status()
    if scope_failure is not None:
        _emit(
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
    _emit(
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
    """Execute every preauthorized trial once, in serial or concurrently.

    The complete plan, call set, provider opt-in, identities, and budgets are
    checked before the first backend can observe a request.  Infrastructure
    failures become censored attempts and never trigger a retry or a new route.
    """
    trials = _validated_plan(plan)
    approved = _require_digest(approved_plan_digest, "approved_plan_digest")
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
    ordered_calls = _validated_calls(trials, calls)

    for trial, call in zip(trials, ordered_calls, strict=True):
        boundary.require()
        call.session.authorize(
            input_tokens=trial.prediction.predicted_input_tokens,
            max_output_tokens=trial.prediction.predicted_output_tokens,
            estimated_cost_microusd=trial.prediction.predicted_cost_microusd,
        )

    trace_lock = threading.Lock()
    _emit(
        sink,
        trace_lock,
        CandidateExecutionTrace(
            CandidateExecutionEvent.PLAN_AUTHORIZED,
            approved,
            concurrent=plan.concurrent,
            trial_count=len(trials),
        ),
    )
    invoked: tuple[_InvocationOutcome, ...]
    if plan.concurrent and len(trials) > 1:
        with ThreadPoolExecutor(
            max_workers=len(trials),
            thread_name_prefix="gludd-candidate-trial",
        ) as executor:
            futures = tuple(
                executor.submit(
                    _invoke_trial,
                    trial,
                    call,
                    plan_digest=approved,
                    concurrent=True,
                    boundary=boundary,
                    trace_sink=sink,
                    trace_lock=trace_lock,
                    clock_ns=clock_ns,
                )
                for trial, call in zip(trials, ordered_calls, strict=True)
            )
            invoked = tuple(future.result() for future in futures)
    else:
        invoked = tuple(
            _invoke_trial(
                trial,
                call,
                plan_digest=approved,
                concurrent=plan.concurrent,
                boundary=boundary,
                trace_sink=sink,
                trace_lock=trace_lock,
                clock_ns=clock_ns,
            )
            for trial, call in zip(trials, ordered_calls, strict=True)
        )

    completed = tuple(
        _record_trial(
            trial,
            outcome,
            plan_digest=approved,
            concurrent=plan.concurrent,
            boundary=boundary,
            evidence_store=evidence_store,
            trace_sink=sink,
            trace_lock=trace_lock,
        )
        for trial, outcome in zip(trials, invoked, strict=True)
    )
    _emit(
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
