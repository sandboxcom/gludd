"""Public value contracts for bounded candidate-plan execution."""

from __future__ import annotations

import hmac
import re
from collections.abc import Callable
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
)
from general_ludd.self_improve.model_candidates import (
    BackendFailure,
    BoundedCandidateSession,
    ModelCandidateProvider,
)
from general_ludd.self_improve.private_policy import SelfImproveRuntimePolicyGuard

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_CANDIDATE_TRIALS = 16


def require_execution_digest(value: object, field_name: str) -> str:
    """Return a canonical digest or reject ambiguous execution authority."""
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


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
        require_execution_digest(
            self.expected_project_identity_digest,
            "expected_project_identity_digest",
        )
        require_execution_digest(
            self.policy_guard.expected_digest,
            "privacy policy digest",
        )
        if not callable(self.project_identity_probe):
            raise ValueError("project_identity_probe must be callable")

    @property
    def expected_privacy_policy_digest(self) -> str:
        """Return the policy identity bound into every approved prediction."""
        return self.policy_guard.expected_digest

    def status(self) -> CandidateExecutionScopeFailure | None:
        """Return a censored drift reason, or ``None`` for the approved scope."""
        try:
            current_identity: object = self.project_identity_probe()
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
        if self.ordinal < 0 or self.ordinal >= MAX_CANDIDATE_TRIALS:
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


__all__ = (
    "MAX_CANDIDATE_TRIALS",
    "CandidateEvaluation",
    "CandidateExecutionBoundary",
    "CandidateExecutionError",
    "CandidateExecutionEvent",
    "CandidateExecutionResult",
    "CandidateExecutionScopeFailure",
    "CandidateExecutionTrace",
    "CandidateTrialCall",
    "CandidateTrialExecution",
    "require_execution_digest",
)
