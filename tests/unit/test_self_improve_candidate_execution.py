"""Bounded execution tests for explicit local and Azure candidate trial plans."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, cast

import pytest

from general_ludd.schemas.benchmark import TaskType
from general_ludd.self_improve.candidate_execution import (
    CandidateEvaluation,
    CandidateExecutionBoundary,
    CandidateExecutionError,
    CandidateExecutionEvent,
    CandidateExecutionScopeFailure,
    CandidateExecutionTrace,
    CandidateTrialCall,
    execute_candidate_trial_plan,
)
from general_ludd.self_improve.candidate_routing import (
    CalibrationSkipReason,
    CandidateAttemptOutcome,
    CandidatePrediction,
    CandidateTrialPlan,
    plan_bounded_candidate_trials,
)
from general_ludd.self_improve.model_candidates import (
    AzureFoundryAPIFamily,
    AzureFoundryCandidateIdentity,
    BackendCallBudget,
    BackendFailure,
    BackendInfrastructureError,
    BackendPolicyError,
    BackendPolicyFailure,
    BoundedCandidateSession,
    LocalGGUFCandidateIdentity,
    ModelCandidateIdentity,
    ModelCandidateProvider,
)
from general_ludd.self_improve.private_policy import SelfImproveRuntimePolicyGuard
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

_PRIVATE_REQUEST = "PRIVATE-REQUEST-CANARY"
_PRIVATE_RESPONSE = "PRIVATE-RESPONSE-CANARY"
_PRIVATE_ERROR = "PRIVATE-PROVIDER-ERROR-CANARY"


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _local_identity(label: str = "local") -> LocalGGUFCandidateIdentity:
    return LocalGGUFCandidateIdentity(
        model_id=label,
        filename=f"{label}.gguf",
        artifact_sha256=_digest(f"artifact:{label}"),
    )


def _azure_identity(label: str = "azure") -> AzureFoundryCandidateIdentity:
    return AzureFoundryCandidateIdentity(
        endpoint="https://project.openai.azure.com",
        api_family=AzureFoundryAPIFamily.AZURE_OPENAI,
        deployment=label,
        api_version="v1",
        model_version="2026-09-04",
        etag=f'W/"{label}-revision"',
    )


def _prediction(
    identity: ModelCandidateIdentity,
    *,
    probability: float,
    privacy_digest: str,
) -> CandidatePrediction:
    return CandidatePrediction(
        candidate_identity_digest=identity.identity_digest,
        provider=identity.provider,
        task_type=TaskType.FEATURE,
        task_kind="code_generation",
        evaluation_stratum_digest=_digest("feature-python-v2"),
        prompt_protocol_digest=_digest("prompt-v4"),
        evaluator_digest=_digest("evaluator-v6"),
        sampling_digest=_digest("temperature-0"),
        privacy_policy_digest=privacy_digest,
        predicted_acceptance=probability,
        predicted_latency_ms=50,
        predicted_input_tokens=12,
        predicted_output_tokens=20,
        predicted_cost_microusd=30,
    )


def _budget(**overrides: object) -> BackendCallBudget:
    values: dict[str, object] = {
        "max_calls": 1,
        "max_input_tokens": 100,
        "max_output_tokens": 100,
        "max_total_tokens": 200,
        "max_cost_microusd": 1_000,
        "timeout_seconds": 2.0,
    }
    values.update(overrides)
    return BackendCallBudget(**cast(Any, values))


class _Backend:
    def __init__(
        self,
        identity: ModelCandidateIdentity,
        label: str,
        calls: list[str],
        *,
        failure: Exception | None = None,
        barrier: threading.Barrier | None = None,
        on_generate: Callable[[], None] | None = None,
    ) -> None:
        self.candidate_identity = identity
        self.label = label
        self.calls = calls
        self.failure = failure
        self.barrier = barrier
        self.on_generate = on_generate

    def generate(
        self,
        request: object,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> str:
        del request, max_output_tokens, timeout_seconds
        self.calls.append(self.label)
        if self.on_generate is not None:
            self.on_generate()
        if self.barrier is not None:
            self.barrier.wait(timeout=2.0)
        if self.failure is not None:
            raise self.failure
        return f"{_PRIVATE_RESPONSE}:{self.label}"


def _evaluation(_response: object) -> CandidateEvaluation:
    return CandidateEvaluation(
        accepted=True,
        evaluation_score=0.9,
        blocker_count=0,
        observed_input_tokens=11,
        observed_output_tokens=17,
        observed_cost_microusd=25,
    )


def _boundary(
    root: Path,
    *,
    project_probe: Callable[[], str] | None = None,
) -> CandidateExecutionBoundary:
    source = root / "src" / "approved.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("PUBLIC = True\n", encoding="utf-8")
    guard = SelfImproveRuntimePolicyGuard.load(root, lambda _event: None, RuntimeError)
    return CandidateExecutionBoundary(
        policy_guard=guard,
        source_paths=("src/approved.py",),
        expected_project_identity_digest=_digest("project"),
        project_identity_probe=project_probe or (lambda: _digest("project")),
    )


def _plan(
    boundary: CandidateExecutionBoundary,
    identities: tuple[ModelCandidateIdentity, ...],
    *,
    concurrent: bool,
) -> CandidateTrialPlan:
    predictions = tuple(
        _prediction(
            identity,
            probability=0.9 - index / 10,
            privacy_digest=boundary.policy_guard.expected_digest,
        )
        for index, identity in enumerate(identities)
    )
    return plan_bounded_candidate_trials(
        predictions,
        (),
        max_trials=len(predictions),
        challenge_trials=0,
        concurrent=concurrent,
    )


def _call(
    ordinal: int,
    backend: _Backend,
    *,
    azure_enabled: bool | None = None,
    budget: BackendCallBudget | None = None,
    evaluator: Callable[[object], CandidateEvaluation] = _evaluation,
) -> CandidateTrialCall:
    enabled = (
        backend.candidate_identity.provider is ModelCandidateProvider.AZURE_FOUNDRY
        if azure_enabled is None
        else azure_enabled
    )
    return CandidateTrialCall(
        ordinal=ordinal,
        session=BoundedCandidateSession(
            backend,
            budget or _budget(),
            azure_enabled=enabled,
        ),
        request=_PRIVATE_REQUEST,
        evaluator=evaluator,
    )


@pytest.mark.parametrize("identity", [_local_identity(), _azure_identity()])
def test_single_provider_plan_executes_one_explicit_call_and_updates_calibration(
    tmp_path: Path,
    identity: ModelCandidateIdentity,
) -> None:
    boundary = _boundary(tmp_path)
    plan = _plan(boundary, (identity,), concurrent=False)
    calls: list[str] = []
    backend = _Backend(identity, identity.provider.value, calls)
    store = CapabilityEvidenceStore(str(tmp_path / "evidence.json"))

    result = execute_candidate_trial_plan(
        plan,
        (_call(0, backend),),
        approved_plan_digest=plan.plan_digest,
        boundary=boundary,
        evidence_store=store,
    )

    assert calls == [identity.provider.value]
    assert len(result.trials) == 1
    assert result.trials[0].response == f"{_PRIVATE_RESPONSE}:{identity.provider.value}"
    assert result.attempts[0].outcome is CandidateAttemptOutcome.ACCEPTED
    assert result.trials[0].calibration.persisted is True
    assert len(store.list_all()) == 1


def test_mixed_serial_plan_runs_each_preapproved_call_once_without_retry_or_fallback(
    tmp_path: Path,
) -> None:
    boundary = _boundary(tmp_path)
    local = _local_identity()
    azure = _azure_identity()
    plan = _plan(boundary, (local, azure), concurrent=False)
    calls: list[str] = []
    local_backend = _Backend(
        local,
        "local",
        calls,
        failure=BackendInfrastructureError(BackendFailure.TIMEOUT),
    )
    azure_backend = _Backend(azure, "azure", calls)
    store = CapabilityEvidenceStore(str(tmp_path / "evidence.json"))

    result = execute_candidate_trial_plan(
        plan,
        (_call(0, local_backend), _call(1, azure_backend)),
        approved_plan_digest=plan.plan_digest,
        boundary=boundary,
        evidence_store=store,
    )

    assert calls == ["local", "azure"]
    assert [attempt.outcome for attempt in result.attempts] == [
        CandidateAttemptOutcome.INFRASTRUCTURE_FAILURE,
        CandidateAttemptOutcome.ACCEPTED,
    ]
    assert result.trials[0].calibration.skip_reason is CalibrationSkipReason.INFRASTRUCTURE_FAILURE
    assert result.trials[1].calibration.persisted is True
    assert len(store.list_all()) == 1


def test_mixed_concurrent_plan_overlaps_calls_but_returns_plan_order(tmp_path: Path) -> None:
    boundary = _boundary(tmp_path)
    identities = (_local_identity(), _azure_identity())
    plan = _plan(boundary, identities, concurrent=True)
    barrier = threading.Barrier(2)
    calls: list[str] = []
    backends = (
        _Backend(identities[0], "local", calls, barrier=barrier),
        _Backend(identities[1], "azure", calls, barrier=barrier),
    )

    result = execute_candidate_trial_plan(
        plan,
        tuple(_call(index, backend) for index, backend in enumerate(backends)),
        approved_plan_digest=plan.plan_digest,
        boundary=boundary,
        evidence_store=CapabilityEvidenceStore(str(tmp_path / "evidence.json")),
    )

    assert set(calls) == {"local", "azure"}
    assert [trial.ordinal for trial in result.trials] == [0, 1]
    assert [trial.attempt.prediction.provider for trial in result.trials] == [
        ModelCandidateProvider.LOCAL_GGUF,
        ModelCandidateProvider.AZURE_FOUNDRY,
    ]


@pytest.mark.parametrize("malformation", ["missing", "wrong_identity", "budget"])
def test_complete_plan_is_preflighted_before_any_backend_executes(
    tmp_path: Path,
    malformation: str,
) -> None:
    boundary = _boundary(tmp_path)
    local = _local_identity()
    azure = _azure_identity()
    plan = _plan(boundary, (local, azure), concurrent=False)
    calls: list[str] = []
    local_call = _call(0, _Backend(local, "local", calls))
    azure_call = _call(1, _Backend(azure, "azure", calls))
    invocations: tuple[CandidateTrialCall, ...]
    if malformation == "missing":
        invocations = (local_call,)
    elif malformation == "wrong_identity":
        invocations = (
            _call(0, _Backend(azure, "wrong", calls)),
            azure_call,
        )
    else:
        invocations = (
            local_call,
            _call(
                1,
                _Backend(azure, "azure", calls),
                budget=_budget(max_output_tokens=10),
            ),
        )

    with pytest.raises((ValueError, BackendPolicyError)):
        execute_candidate_trial_plan(
            plan,
            invocations,
            approved_plan_digest=plan.plan_digest,
            boundary=boundary,
            evidence_store=CapabilityEvidenceStore(str(tmp_path / "evidence.json")),
        )

    assert calls == []


def test_plan_approval_digest_and_azure_opt_in_are_checked_before_all_calls(
    tmp_path: Path,
) -> None:
    boundary = _boundary(tmp_path)
    local = _local_identity()
    azure = _azure_identity()
    plan = _plan(boundary, (local, azure), concurrent=False)
    calls: list[str] = []
    invocations = (
        _call(0, _Backend(local, "local", calls)),
        _call(1, _Backend(azure, "azure", calls), azure_enabled=False),
    )
    store = CapabilityEvidenceStore(str(tmp_path / "evidence.json"))

    with pytest.raises(ValueError, match="approved plan"):
        execute_candidate_trial_plan(
            plan,
            invocations,
            approved_plan_digest=_digest("other-plan"),
            boundary=boundary,
            evidence_store=store,
        )
    with pytest.raises(BackendPolicyError) as captured:
        execute_candidate_trial_plan(
            plan,
            invocations,
            approved_plan_digest=plan.plan_digest,
            boundary=boundary,
            evidence_store=store,
        )

    assert captured.value.failure is BackendPolicyFailure.AZURE_OPT_IN_REQUIRED
    assert calls == []


def test_project_identity_is_rechecked_after_provider_before_evaluation(
    tmp_path: Path,
) -> None:
    current = [_digest("project")]
    boundary = _boundary(tmp_path, project_probe=lambda: current[0])
    identity = _local_identity()
    plan = _plan(boundary, (identity,), concurrent=False)
    calls: list[str] = []
    evaluated: list[object] = []

    def drift() -> None:
        current[0] = _digest(_PRIVATE_ERROR)

    def evaluate(response: object) -> CandidateEvaluation:
        evaluated.append(response)
        return _evaluation(response)

    with pytest.raises(CandidateExecutionError) as captured:
        execute_candidate_trial_plan(
            plan,
            (_call(0, _Backend(identity, "local", calls, on_generate=drift), evaluator=evaluate),),
            approved_plan_digest=plan.plan_digest,
            boundary=boundary,
            evidence_store=CapabilityEvidenceStore(str(tmp_path / "evidence.json")),
        )

    assert captured.value.failure is CandidateExecutionScopeFailure.PROJECT_IDENTITY_DRIFT
    assert _PRIVATE_ERROR not in str(captured.value)
    assert calls == ["local"]
    assert evaluated == []


def test_privacy_policy_is_rechecked_after_provider_before_evaluation(
    tmp_path: Path,
) -> None:
    boundary = _boundary(tmp_path)
    identity = _local_identity()
    plan = _plan(boundary, (identity,), concurrent=False)
    evaluated: list[object] = []

    def make_private() -> None:
        policy_dir = tmp_path / ".gludd"
        policy_dir.mkdir(exist_ok=True)
        (policy_dir / "self-improve-policy.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "default_access": "public",
                    "private_paths": ["src/approved.py"],
                    "public_paths": [],
                }
            ),
            encoding="utf-8",
        )

    def evaluate(response: object) -> CandidateEvaluation:
        evaluated.append(response)
        return _evaluation(response)

    with pytest.raises(CandidateExecutionError) as captured:
        execute_candidate_trial_plan(
            plan,
            (
                _call(
                    0,
                    _Backend(identity, "local", [], on_generate=make_private),
                    evaluator=evaluate,
                ),
            ),
            approved_plan_digest=plan.plan_digest,
            boundary=boundary,
            evidence_store=CapabilityEvidenceStore(str(tmp_path / "evidence.json")),
        )

    assert captured.value.failure is CandidateExecutionScopeFailure.PRIVATE_SCOPE
    assert evaluated == []


def test_identity_drift_at_learning_boundary_censors_quality_evidence(
    tmp_path: Path,
) -> None:
    current = [_digest("project")]
    boundary = _boundary(tmp_path, project_probe=lambda: current[0])
    identity = _local_identity()
    plan = _plan(boundary, (identity,), concurrent=False)
    store = CapabilityEvidenceStore(str(tmp_path / "evidence.json"))
    traces: list[CandidateExecutionTrace] = []

    def evaluate(response: object) -> CandidateEvaluation:
        current[0] = _digest("replacement-project")
        return _evaluation(response)

    result = execute_candidate_trial_plan(
        plan,
        (_call(0, _Backend(identity, "local", []), evaluator=evaluate),),
        approved_plan_digest=plan.plan_digest,
        boundary=boundary,
        evidence_store=store,
        trace_sink=traces.append,
    )

    assert result.trials[0].calibration.persisted is False
    assert result.trials[0].calibration.skip_reason is CalibrationSkipReason.PRIVATE_SCOPE
    assert store.list_all() == []
    assert any(
        trace.event is CandidateExecutionEvent.SCOPE_BLOCKED
        and trace.scope_failure is CandidateExecutionScopeFailure.PROJECT_IDENTITY_DRIFT
        for trace in traces
    )


def test_untyped_backend_and_evaluator_failures_are_censored_from_learning(
    tmp_path: Path,
) -> None:
    boundary = _boundary(tmp_path)
    identities = (_local_identity("backend"), _local_identity("evaluator"))
    plan = _plan(boundary, identities, concurrent=False)
    traces: list[CandidateExecutionTrace] = []
    calls: list[str] = []

    def broken_evaluator(_response: object) -> CandidateEvaluation:
        raise RuntimeError(_PRIVATE_ERROR)

    result = execute_candidate_trial_plan(
        plan,
        (
            _call(
                0,
                _Backend(identities[0], "backend", calls, failure=RuntimeError(_PRIVATE_ERROR)),
            ),
            _call(
                1,
                _Backend(identities[1], "evaluator", calls),
                evaluator=broken_evaluator,
            ),
        ),
        approved_plan_digest=plan.plan_digest,
        boundary=boundary,
        evidence_store=CapabilityEvidenceStore(str(tmp_path / "evidence.json")),
        trace_sink=traces.append,
    )

    assert calls == ["backend", "evaluator"]
    assert all(
        attempt.outcome is CandidateAttemptOutcome.INFRASTRUCTURE_FAILURE
        and attempt.failure is BackendFailure.INTERNAL
        for attempt in result.attempts
    )
    rendered = json.dumps([asdict(trace) for trace in traces], default=str)
    combined = repr((result, traces)) + rendered
    assert _PRIVATE_REQUEST not in combined
    assert _PRIVATE_RESPONSE not in combined
    assert _PRIVATE_ERROR not in combined


def test_scope_probe_and_trace_failures_are_fixed_message_and_fail_closed(
    tmp_path: Path,
) -> None:
    def broken_probe() -> str:
        raise RuntimeError(_PRIVATE_ERROR)

    boundary = _boundary(tmp_path, project_probe=broken_probe)
    identity = _local_identity()
    plan = _plan(boundary, (identity,), concurrent=False)
    backend = _Backend(identity, "local", [])

    with pytest.raises(CandidateExecutionError) as scope_error:
        execute_candidate_trial_plan(
            plan,
            (_call(0, backend),),
            approved_plan_digest=plan.plan_digest,
            boundary=boundary,
            evidence_store=CapabilityEvidenceStore(str(tmp_path / "evidence.json")),
        )
    assert scope_error.value.failure is CandidateExecutionScopeFailure.PROJECT_IDENTITY_DRIFT
    assert scope_error.value.__cause__ is None

    good_boundary = _boundary(tmp_path)
    good_plan = _plan(good_boundary, (identity,), concurrent=False)

    def broken_sink(_trace: CandidateExecutionTrace) -> None:
        raise RuntimeError(_PRIVATE_ERROR)

    with pytest.raises(CandidateExecutionError) as trace_error:
        execute_candidate_trial_plan(
            good_plan,
            (_call(0, backend),),
            approved_plan_digest=good_plan.plan_digest,
            boundary=good_boundary,
            evidence_store=CapabilityEvidenceStore(str(tmp_path / "other-evidence.json")),
            trace_sink=broken_sink,
        )
    assert trace_error.value.failure is CandidateExecutionScopeFailure.TRACE_FAILURE
    assert _PRIVATE_ERROR not in repr(trace_error.value)


def test_directly_malformed_plan_and_invocations_fail_before_execution(tmp_path: Path) -> None:
    boundary = _boundary(tmp_path)
    identity = _local_identity()
    valid = _plan(boundary, (identity,), concurrent=False)
    backend = _Backend(identity, "local", [])
    call = _call(0, backend)
    store = CapabilityEvidenceStore(str(tmp_path / "evidence.json"))
    malformed_plans = (
        replace(valid, concurrent=cast(Any, 1)),
        CandidateTrialPlan(trials=(), concurrent=False),
        replace(valid, trials=(replace(valid.trials[0], ordinal=1),)),
    )

    for plan in malformed_plans:
        with pytest.raises(ValueError):
            execute_candidate_trial_plan(
                plan,
                (call,),
                approved_plan_digest=plan.plan_digest,
                boundary=boundary,
                evidence_store=store,
            )
    with pytest.raises(ValueError):
        execute_candidate_trial_plan(
            valid,
            (replace(call, ordinal=True),),
            approved_plan_digest=valid.plan_digest,
            boundary=boundary,
            evidence_store=store,
        )

    assert backend.calls == []


def test_rejected_evaluation_and_single_concurrent_trial_use_explicit_plan_mode(
    tmp_path: Path,
) -> None:
    boundary = _boundary(tmp_path)
    identity = _local_identity()
    plan = _plan(boundary, (identity,), concurrent=True)

    def rejected(_response: object) -> CandidateEvaluation:
        return CandidateEvaluation(
            accepted=False,
            evaluation_score=0.2,
            blocker_count=2,
            observed_input_tokens=10,
            observed_output_tokens=8,
            observed_cost_microusd=0,
        )

    result = execute_candidate_trial_plan(
        plan,
        (_call(0, _Backend(identity, "local", []), evaluator=rejected),),
        approved_plan_digest=plan.plan_digest,
        boundary=boundary,
        evidence_store=CapabilityEvidenceStore(str(tmp_path / "evidence.json")),
    )

    assert result.concurrent is True
    assert result.attempts[0].outcome is CandidateAttemptOutcome.REJECTED
    assert result.attempts[0].blocker_count == 2
    assert result.trials[0].calibration.persisted is True


def test_public_contracts_reject_untyped_authority_and_call_shapes(tmp_path: Path) -> None:
    boundary = _boundary(tmp_path)
    identity = _local_identity()
    plan = _plan(boundary, (identity,), concurrent=False)
    call = _call(0, _Backend(identity, "local", []))
    store = CapabilityEvidenceStore(str(tmp_path / "evidence.json"))

    with pytest.raises(ValueError, match="CandidateExecutionScopeFailure"):
        CandidateExecutionError(cast(Any, "private_scope"))
    with pytest.raises(ValueError, match="accepted"):
        replace(_evaluation("response"), accepted=cast(Any, 1))
    for overrides in (
        {"ordinal": 16},
        {"session": cast(Any, object())},
        {"evaluator": cast(Any, object())},
    ):
        with pytest.raises(ValueError):
            replace(call, **overrides)
    with pytest.raises(ValueError, match="approved_plan_digest"):
        execute_candidate_trial_plan(
            plan,
            (call,),
            approved_plan_digest="not-a-digest",
            boundary=boundary,
            evidence_store=store,
        )
    with pytest.raises(ValueError, match="CandidateTrialPlan"):
        execute_candidate_trial_plan(
            cast(Any, object()),
            (call,),
            approved_plan_digest=plan.plan_digest,
            boundary=boundary,
            evidence_store=store,
        )
    with pytest.raises(ValueError, match="CandidateExecutionBoundary"):
        execute_candidate_trial_plan(
            plan,
            (call,),
            approved_plan_digest=plan.plan_digest,
            boundary=cast(Any, object()),
            evidence_store=store,
        )
    with pytest.raises(ValueError, match="CapabilityEvidenceStore"):
        execute_candidate_trial_plan(
            plan,
            (call,),
            approved_plan_digest=plan.plan_digest,
            boundary=boundary,
            evidence_store=cast(Any, object()),
        )
    with pytest.raises(ValueError, match="calls"):
        execute_candidate_trial_plan(
            plan,
            cast(Any, (object(),)),
            approved_plan_digest=plan.plan_digest,
            boundary=boundary,
            evidence_store=store,
        )


def test_boundary_constructor_rejects_ambiguous_authority(tmp_path: Path) -> None:
    valid = _boundary(tmp_path)
    invalid_values = (
        {"policy_guard": cast(Any, object())},
        {"source_paths": ()},
        {"source_paths": ("src/approved.py", "src/approved.py")},
        {"expected_project_identity_digest": "not-a-digest"},
        {"project_identity_probe": cast(Any, object())},
    )

    for values in invalid_values:
        with pytest.raises(ValueError):
            replace(valid, **values)


def test_privacy_identity_mismatch_and_evidence_failure_never_call_or_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = _boundary(tmp_path)
    identity = _local_identity()
    plan = _plan(boundary, (identity,), concurrent=False)
    trial = plan.trials[0]
    other_prediction = replace(
        trial.prediction,
        privacy_policy_digest=_digest("other-policy"),
    )
    mismatched = CandidateTrialPlan(
        trials=(
            replace(
                trial,
                prediction=other_prediction,
                ranking=replace(trial.ranking, prediction=other_prediction),
            ),
        ),
        concurrent=False,
    )
    calls: list[str] = []

    with pytest.raises(ValueError, match="privacy identity"):
        execute_candidate_trial_plan(
            mismatched,
            (_call(0, _Backend(identity, "local", calls)),),
            approved_plan_digest=mismatched.plan_digest,
            boundary=boundary,
            evidence_store=CapabilityEvidenceStore(str(tmp_path / "evidence.json")),
        )
    assert calls == []

    store = CapabilityEvidenceStore(str(tmp_path / "broken-evidence.json"))

    def fail_registration(_record: dict[str, object]) -> int:
        raise RuntimeError(_PRIVATE_ERROR)

    monkeypatch.setattr(store, "register_evidence", fail_registration)
    with pytest.raises(CandidateExecutionError) as captured:
        execute_candidate_trial_plan(
            plan,
            (_call(0, _Backend(identity, "local", calls)),),
            approved_plan_digest=plan.plan_digest,
            boundary=boundary,
            evidence_store=store,
        )

    assert captured.value.failure is CandidateExecutionScopeFailure.EVIDENCE_FAILURE
    assert _PRIVATE_ERROR not in repr(captured.value)
