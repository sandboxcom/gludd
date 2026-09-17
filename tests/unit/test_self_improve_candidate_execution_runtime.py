"""Direct contracts for one-attempt candidate execution and calibration."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from general_ludd.schemas.benchmark import TaskType
from general_ludd.self_improve import _candidate_execution_runtime as runtime
from general_ludd.self_improve._candidate_attempt import CandidateAttemptOutcome
from general_ludd.self_improve._candidate_execution_types import (
    CandidateEvaluation,
    CandidateExecutionBoundary,
    CandidateExecutionError,
    CandidateExecutionEvent,
    CandidateExecutionScopeFailure,
    CandidateExecutionTrace,
    CandidateTrialCall,
)
from general_ludd.self_improve._candidate_prediction import CandidatePrediction
from general_ludd.self_improve._candidate_trials import (
    CandidateRanking,
    CandidateTrial,
    CandidateTrialPurpose,
)
from general_ludd.self_improve.model_candidates import (
    BackendCallBudget,
    BackendFailure,
    BackendInfrastructureError,
    BoundedCandidateSession,
    LocalGGUFCandidateIdentity,
    ModelCandidateProvider,
)
from general_ludd.self_improve.private_policy import SelfImproveRuntimePolicyGuard
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _trial(privacy_digest: str) -> tuple[CandidateTrial, LocalGGUFCandidateIdentity]:
    identity = LocalGGUFCandidateIdentity(
        model_id="fixture",
        filename="fixture.gguf",
        artifact_sha256=_digest("artifact"),
    )
    prediction = CandidatePrediction(
        candidate_identity_digest=identity.identity_digest,
        provider=ModelCandidateProvider.LOCAL_GGUF,
        task_type=TaskType.FEATURE,
        task_kind="code_generation",
        evaluation_stratum_digest=_digest("stratum"),
        prompt_protocol_digest=_digest("prompt"),
        evaluator_digest=_digest("evaluator"),
        sampling_digest=_digest("sampling"),
        privacy_policy_digest=privacy_digest,
        predicted_acceptance=0.8,
        predicted_latency_ms=25,
        predicted_input_tokens=8,
        predicted_output_tokens=13,
        predicted_cost_microusd=4,
    )
    ranking = CandidateRanking(prediction, 0, 0, 0.8, 0.5)
    return (
        CandidateTrial(0, CandidateTrialPurpose.PREFERRED, prediction, ranking),
        identity,
    )


class _Backend:
    def __init__(
        self,
        identity: LocalGGUFCandidateIdentity,
        failure: BackendInfrastructureError | None = None,
    ) -> None:
        self.candidate_identity = identity
        self.failure = failure

    def generate(
        self,
        request: object,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> object:
        del request, max_output_tokens, timeout_seconds
        if self.failure is not None:
            raise self.failure
        return object()


def _boundary(root: Path) -> CandidateExecutionBoundary:
    source = root / "src" / "approved.py"
    source.parent.mkdir(parents=True)
    source.write_text("PUBLIC = True\n", encoding="utf-8")
    guard = SelfImproveRuntimePolicyGuard.load(root, lambda _event: None, RuntimeError)
    return CandidateExecutionBoundary(
        guard,
        ("src/approved.py",),
        _digest("project"),
        lambda: _digest("project"),
    )


def _call(
    identity: LocalGGUFCandidateIdentity,
    evaluator: Callable[[object], CandidateEvaluation],
    *,
    failure: BackendInfrastructureError | None = None,
) -> CandidateTrialCall:
    session = BoundedCandidateSession(
        _Backend(identity, failure),
        BackendCallBudget(1, 50, 50, 100, 100, 2.0),
        azure_enabled=False,
    )
    return CandidateTrialCall(0, session, object(), evaluator)


def _accepted(_response: object) -> CandidateEvaluation:
    return CandidateEvaluation(True, 0.9, 0, 7, 10, 3)


def test_invoke_candidate_trial_emits_success_without_content(tmp_path: Path) -> None:
    boundary = _boundary(tmp_path)
    trial, identity = _trial(boundary.expected_privacy_policy_digest)
    traces: list[CandidateExecutionTrace] = []
    ticks = iter((1_000_000, 5_000_000))

    outcome = runtime.invoke_candidate_trial(
        trial,
        _call(identity, _accepted),
        plan_digest=_digest("plan"),
        concurrent=False,
        boundary=boundary,
        trace_sink=traces.append,
        trace_lock=threading.Lock(),
        clock_ns=lambda: next(ticks),
    )

    assert outcome.response is not None
    assert outcome.attempt.outcome is CandidateAttemptOutcome.ACCEPTED
    assert outcome.attempt.observed_latency_ms == 4
    assert [trace.event for trace in traces] == [
        CandidateExecutionEvent.TRIAL_STARTED,
        CandidateExecutionEvent.TRIAL_EVALUATED,
    ]


def test_invoke_candidate_trial_censors_typed_backend_failure(tmp_path: Path) -> None:
    boundary = _boundary(tmp_path)
    trial, identity = _trial(boundary.expected_privacy_policy_digest)
    traces: list[CandidateExecutionTrace] = []

    outcome = runtime.invoke_candidate_trial(
        trial,
        _call(
            identity,
            _accepted,
            failure=BackendInfrastructureError(BackendFailure.TIMEOUT),
        ),
        plan_digest=_digest("plan"),
        concurrent=True,
        boundary=boundary,
        trace_sink=traces.append,
        trace_lock=threading.Lock(),
        clock_ns=lambda: 1_000_000,
    )

    assert outcome.response is None
    assert outcome.attempt.failure is BackendFailure.TIMEOUT
    assert traces[-1].event is CandidateExecutionEvent.TRIAL_INFRASTRUCTURE_FAILED
    assert traces[-1].concurrent is True


def test_record_candidate_trial_censors_evidence_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = _boundary(tmp_path)
    trial, identity = _trial(boundary.expected_privacy_policy_digest)
    invoked = runtime.invoke_candidate_trial(
        trial,
        _call(identity, _accepted),
        plan_digest=_digest("plan"),
        concurrent=False,
        boundary=boundary,
        trace_sink=lambda _trace: None,
        trace_lock=threading.Lock(),
        clock_ns=lambda: 1_000_000,
    )

    def broken_record(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("private-evidence-detail")

    monkeypatch.setattr(runtime, "record_calibration_attempt", broken_record)
    with pytest.raises(CandidateExecutionError) as captured:
        runtime.record_candidate_trial(
            trial,
            invoked,
            plan_digest=_digest("plan"),
            concurrent=False,
            boundary=boundary,
            evidence_store=CapabilityEvidenceStore(str(tmp_path / "evidence.json")),
            trace_sink=lambda _trace: None,
            trace_lock=threading.Lock(),
        )

    assert captured.value.failure is CandidateExecutionScopeFailure.EVIDENCE_FAILURE
    assert "private-evidence-detail" not in repr(captured.value)
