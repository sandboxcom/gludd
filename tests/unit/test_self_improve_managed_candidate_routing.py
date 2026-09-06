"""Behavioral tests for empirically calibrated managed-candidate routing."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from general_ludd.schemas.benchmark import TaskType
from general_ludd.self_improve._candidate_attempt import CandidateAttemptOutcome
from general_ludd.self_improve._candidate_execution_types import (
    CandidateExecutionBoundary,
    CandidateExecutionError,
)
from general_ludd.self_improve.azure_backend import AzureCandidateResponse
from general_ludd.self_improve.candidate_classification import (
    CandidateTaskClassification,
    classify_candidate_task,
)
from general_ludd.self_improve.managed_candidate_routing import (
    CandidateObservedUsage,
    CandidateProposalAssessment,
    CandidateProposalDecodeRejected,
    ManagedCandidateProposalCodec,
    ManagedCandidateRouteFailure,
    ManagedCandidateRoutingError,
    ManagedCandidateRoutingEvent,
    ManagedCandidateRoutingTrace,
    ManagedCandidateTrialSpec,
    route_managed_candidate_proposals,
)
from general_ludd.self_improve.model_candidates import (
    AzureFoundryAPIFamily,
    AzureFoundryCandidateIdentity,
    BackendCallBudget,
    BackendFailure,
    BackendInfrastructureError,
    BoundedCandidateSession,
    LocalGGUFCandidateIdentity,
    ModelCandidateIdentity,
    ModelCandidateProvider,
)
from general_ludd.self_improve.private_policy import SelfImproveRuntimePolicyGuard
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _local_identity() -> LocalGGUFCandidateIdentity:
    return LocalGGUFCandidateIdentity(
        model_id="local-coder",
        repo_id="example/local-coder",
        revision="a" * 40,
        filename="local-coder.Q4_K_M.gguf",
        artifact_sha256="b" * 64,
    )


def _azure_identity() -> AzureFoundryCandidateIdentity:
    return AzureFoundryCandidateIdentity(
        endpoint="https://unit-test.openai.azure.com",
        api_family=AzureFoundryAPIFamily.AZURE_OPENAI,
        deployment="coder-deployment",
        api_version="v1",
        model_version="2026-09-01",
        etag='"immutable-etag"',
    )


def _budget() -> BackendCallBudget:
    return BackendCallBudget(
        max_calls=1,
        max_input_tokens=128,
        max_output_tokens=128,
        max_total_tokens=256,
        max_cost_microusd=10_000,
        timeout_seconds=5.0,
    )


class _Backend:
    def __init__(
        self,
        identity: ModelCandidateIdentity,
        response: object,
        calls: list[ModelCandidateProvider],
        *,
        failure: BackendInfrastructureError | None = None,
        barrier: threading.Barrier | None = None,
    ) -> None:
        self._identity = identity
        self._response = response
        self._calls = calls
        self._failure = failure
        self._barrier = barrier

    @property
    def candidate_identity(self) -> ModelCandidateIdentity:
        return self._identity

    def generate(
        self,
        _request: object,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> object:
        assert max_output_tokens == 32
        assert timeout_seconds == 5.0
        self._calls.append(self._identity.provider)
        if self._barrier is not None:
            self._barrier.wait(timeout=2.0)
        if self._failure is not None:
            raise self._failure
        return self._response


def _boundary(root: Path, *, project_digest: str | None = None) -> CandidateExecutionBoundary:
    source = root / "src" / "approved.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("PUBLIC = True\n", encoding="utf-8")
    guard = SelfImproveRuntimePolicyGuard.load(root, lambda _event: None, RuntimeError)
    expected = _digest("project")
    return CandidateExecutionBoundary(
        policy_guard=guard,
        source_paths=("src/approved.py",),
        expected_project_identity_digest=expected,
        project_identity_probe=lambda: project_digest or expected,
    )


def _local_usage(_response: object) -> CandidateObservedUsage:
    return CandidateObservedUsage(
        input_tokens=12,
        output_tokens=8,
        cost_microusd=0,
    )


def _azure_usage(response: object) -> CandidateObservedUsage:
    assert isinstance(response, AzureCandidateResponse)
    return CandidateObservedUsage(
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cost_microusd=1_000,
    )


def _assessment(accepted: bool) -> Callable[[str], CandidateProposalAssessment]:
    return lambda _proposal: CandidateProposalAssessment(
        accepted=accepted,
        score=1.0 if accepted else 0.0,
        blocker_count=0 if accepted else 1,
    )


def _spec(
    identity: ModelCandidateIdentity,
    response: object,
    calls: list[ModelCandidateProvider],
    *,
    accepted: bool,
    decode: Callable[[object], str] | None = None,
    failure: BackendInfrastructureError | None = None,
    barrier: threading.Barrier | None = None,
) -> ManagedCandidateTrialSpec[str]:
    remote = identity.provider is ModelCandidateProvider.AZURE_FOUNDRY
    backend = _Backend(identity, response, calls, failure=failure, barrier=barrier)
    selected_decoder = decode or (
        (lambda value: value.text)
        if remote
        else (lambda value: str(value))
    )
    return ManagedCandidateTrialSpec(
        session=BoundedCandidateSession(backend, _budget(), azure_enabled=remote),
        request="private-request-never-in-trace",
        decoder=selected_decoder,
        usage_reader=_azure_usage if remote else _local_usage,
        assessor=_assessment(accepted),
        predicted_latency_ms=2_000 if remote else 1_000,
        predicted_input_tokens=12,
        predicted_output_tokens=32,
        predicted_cost_microusd=1_000 if remote else 0,
    )


def _route(
    root: Path,
    store: CapabilityEvidenceStore,
    specs: tuple[ManagedCandidateTrialSpec[str], ...],
    *,
    concurrent: bool = False,
    boundary: CandidateExecutionBoundary | None = None,
    traces: list[object] | None = None,
    task_text: str = "Implement one bounded public Python feature.",
):
    return route_managed_candidate_proposals(
        classify_candidate_task(task_text),
        specs,
        boundary=boundary or _boundary(root),
        evidence_store=store,
        prompt_protocol_digest=_digest("prompt-protocol"),
        evaluator_digest=_digest("full-evaluator"),
        sampling_digest=_digest("sampling"),
        concurrent=concurrent,
        trace_sink=None if traces is None else traces.append,
    )


def test_mixed_trial_executes_each_candidate_once_and_selects_cheapest_equal_prior(
    tmp_path: Path,
) -> None:
    calls: list[ModelCandidateProvider] = []
    store = CapabilityEvidenceStore(str(tmp_path / "evidence.json"))
    traces: list[object] = []
    azure_response = AzureCandidateResponse("azure-proposal", 13, 7, 20)

    result = _route(
        tmp_path,
        store,
        (
            _spec(_local_identity(), "local-proposal", calls, accepted=True),
            _spec(_azure_identity(), azure_response, calls, accepted=True),
        ),
        traces=traces,
    )

    assert calls == [
        ModelCandidateProvider.LOCAL_GGUF,
        ModelCandidateProvider.AZURE_FOUNDRY,
    ]
    assert result.selected == "local-proposal"
    assert result.selected_prediction.provider is ModelCandidateProvider.LOCAL_GGUF
    assert len(store.list_all()) == 2
    assert [trial.attempt.outcome for trial in result.execution.trials] == [
        CandidateAttemptOutcome.ACCEPTED,
        CandidateAttemptOutcome.ACCEPTED,
    ]
    assert isinstance(traces[0], ManagedCandidateRoutingTrace)
    assert traces[0].event is ManagedCandidateRoutingEvent.PLAN_CREATED
    assert isinstance(traces[-1], ManagedCandidateRoutingTrace)
    assert traces[-1].event is ManagedCandidateRoutingEvent.CANDIDATE_SELECTED


def test_historical_rejection_and_acceptance_promote_azure_on_next_task_trial(
    tmp_path: Path,
) -> None:
    store = CapabilityEvidenceStore(str(tmp_path / "evidence.json"))
    first_calls: list[ModelCandidateProvider] = []
    azure_response = AzureCandidateResponse("azure-proposal", 13, 7, 20)
    first = _route(
        tmp_path,
        store,
        (
            _spec(_local_identity(), "local-proposal", first_calls, accepted=False),
            _spec(_azure_identity(), azure_response, first_calls, accepted=True),
        ),
    )
    assert first.selected == "azure-proposal"

    second_calls: list[ModelCandidateProvider] = []
    second = _route(
        tmp_path,
        store,
        (
            _spec(_local_identity(), "local-better", second_calls, accepted=True),
            _spec(
                _azure_identity(),
                AzureCandidateResponse("azure-better", 13, 7, 20),
                second_calls,
                accepted=True,
            ),
        ),
        task_text="Add another bounded public Python capability.",
    )

    assert second_calls[0] is ModelCandidateProvider.AZURE_FOUNDRY
    assert second.selected == "azure-better"
    assert second.selected_prediction.provider is ModelCandidateProvider.AZURE_FOUNDRY
    assert len(store.list_all()) == 4


def test_infrastructure_failure_is_censored_and_excluded_from_calibration(
    tmp_path: Path,
) -> None:
    calls: list[ModelCandidateProvider] = []
    store = CapabilityEvidenceStore(str(tmp_path / "evidence.json"))
    result = _route(
        tmp_path,
        store,
        (
            _spec(_local_identity(), "local-proposal", calls, accepted=True),
            _spec(
                _azure_identity(),
                AzureCandidateResponse("unused", 1, 1, 2),
                calls,
                accepted=True,
                failure=BackendInfrastructureError(BackendFailure.TIMEOUT),
            ),
        ),
    )

    assert result.selected == "local-proposal"
    assert result.execution.trials[1].attempt.outcome is CandidateAttemptOutcome.INFRASTRUCTURE_FAILURE
    assert result.execution.trials[1].attempt.failure is BackendFailure.TIMEOUT
    assert len(store.list_all()) == 1


def test_project_identity_drift_blocks_every_candidate_before_effects(tmp_path: Path) -> None:
    calls: list[ModelCandidateProvider] = []
    store = CapabilityEvidenceStore(str(tmp_path / "evidence.json"))
    specs = (
        _spec(_local_identity(), "local-proposal", calls, accepted=True),
        _spec(
            _azure_identity(),
            AzureCandidateResponse("azure-proposal", 1, 1, 2),
            calls,
            accepted=True,
        ),
    )

    with pytest.raises(CandidateExecutionError, match="project_identity_drift"):
        _route(
            tmp_path,
            store,
            specs,
            boundary=_boundary(tmp_path, project_digest=_digest("drifted")),
        )

    assert calls == []
    assert store.list_all() == []


def test_all_protocol_rejections_calibrate_false_and_raise_fixed_failure(
    tmp_path: Path,
) -> None:
    calls: list[ModelCandidateProvider] = []
    store = CapabilityEvidenceStore(str(tmp_path / "evidence.json"))
    traces: list[object] = []

    def reject(_response: object) -> str:
        raise CandidateProposalDecodeRejected

    with pytest.raises(ManagedCandidateRoutingError) as raised:
        _route(
            tmp_path,
            store,
            (
                _spec(
                    _local_identity(),
                    "sensitive-invalid-local-output",
                    calls,
                    accepted=True,
                    decode=reject,
                ),
                _spec(
                    _azure_identity(),
                    AzureCandidateResponse("sensitive-invalid-azure-output", 2, 3, 5),
                    calls,
                    accepted=True,
                    decode=reject,
                ),
            ),
            traces=traces,
        )

    assert raised.value.failure is ManagedCandidateRouteFailure.NO_EVALUATED_PROPOSAL
    assert str(raised.value) == "managed candidate routing failed: no_evaluated_proposal"
    assert len(store.list_all()) == 2
    assert all(record["accepted"] is False for record in store.list_all())
    assert traces[-1].event is ManagedCandidateRoutingEvent.NO_EVALUATED_PROPOSAL
    rendered = repr(traces)
    assert "sensitive-invalid" not in rendered
    assert "private-request" not in rendered


def test_concurrent_execution_retains_deterministic_plan_order(tmp_path: Path) -> None:
    calls: list[ModelCandidateProvider] = []
    store = CapabilityEvidenceStore(str(tmp_path / "evidence.json"))
    barrier = threading.Barrier(2)
    result = _route(
        tmp_path,
        store,
        (
            _spec(
                _local_identity(),
                "local-proposal",
                calls,
                accepted=True,
                barrier=barrier,
            ),
            _spec(
                _azure_identity(),
                AzureCandidateResponse("azure-proposal", 1, 1, 2),
                calls,
                accepted=True,
                barrier=barrier,
            ),
        ),
        concurrent=True,
    )

    assert result.selected == "local-proposal"
    assert tuple(
        trial.attempt.prediction.provider for trial in result.execution.trials
    ) == (
        ModelCandidateProvider.LOCAL_GGUF,
        ModelCandidateProvider.AZURE_FOUNDRY,
    )
    assert {record["task_type"] for record in store.list_all()} == {
        TaskType.FEATURE.value
    }


def test_public_routing_value_objects_reject_ambiguous_or_unsafe_shapes() -> None:
    digest = _digest("contract")

    with pytest.raises(ValueError, match="ManagedCandidateRouteFailure"):
        ManagedCandidateRoutingError(cast(ManagedCandidateRouteFailure, "unknown"))
    with pytest.raises(ValueError, match="ManagedCandidateRoutingEvent"):
        ManagedCandidateRoutingTrace(
            cast(ManagedCandidateRoutingEvent, "unknown"), digest, 1
        )
    with pytest.raises(ValueError, match="provider"):
        ManagedCandidateRoutingTrace(
            ManagedCandidateRoutingEvent.PLAN_CREATED,
            digest,
            1,
            provider="",
        )
    with pytest.raises(ValueError, match="accepted"):
        ManagedCandidateRoutingTrace(
            ManagedCandidateRoutingEvent.PLAN_CREATED,
            digest,
            1,
            accepted=cast(bool, 1),
        )
    with pytest.raises(ValueError, match="accepted"):
        CandidateProposalAssessment(cast(bool, 1), 1.0, 0)
    with pytest.raises(ValueError, match="must not contain blockers"):
        CandidateProposalAssessment(True, 1.0, 1)
    with pytest.raises(ValueError, match="request_text"):
        ManagedCandidateProposalCodec(" ", str, digest, digest)
    with pytest.raises(ValueError, match="decoder"):
        ManagedCandidateProposalCodec(
            "bounded request",
            cast(Callable[[str], str], None),
            digest,
            digest,
        )


def test_trial_and_route_validation_refuses_effects_before_model_calls(
    tmp_path: Path,
) -> None:
    calls: list[ModelCandidateProvider] = []
    store = CapabilityEvidenceStore(str(tmp_path / "evidence.json"))
    classification = classify_candidate_task("Implement one public feature.")
    boundary = _boundary(tmp_path)
    spec = _spec(_local_identity(), "proposal", calls, accepted=True)
    route_kwargs = {
        "boundary": boundary,
        "evidence_store": store,
        "prompt_protocol_digest": _digest("prompt-protocol"),
        "evaluator_digest": _digest("full-evaluator"),
        "sampling_digest": _digest("sampling"),
        "concurrent": False,
    }

    with pytest.raises(ValueError, match="BoundedCandidateSession"):
        ManagedCandidateTrialSpec(
            session=cast(BoundedCandidateSession[object, object], object()),
            request="request",
            decoder=str,
            usage_reader=_local_usage,
            assessor=_assessment(True),
            predicted_latency_ms=1,
            predicted_input_tokens=1,
            predicted_output_tokens=1,
            predicted_cost_microusd=0,
        )
    with pytest.raises(ValueError, match="must be callable"):
        ManagedCandidateTrialSpec(
            session=spec.session,
            request="request",
            decoder=cast(Callable[[object], str], None),
            usage_reader=_local_usage,
            assessor=_assessment(True),
            predicted_latency_ms=1,
            predicted_input_tokens=1,
            predicted_output_tokens=1,
            predicted_cost_microusd=0,
        )
    with pytest.raises(ValueError, match="between one and sixteen"):
        route_managed_candidate_proposals(classification, (), **route_kwargs)
    with pytest.raises(ValueError, match="ManagedCandidateTrialSpec"):
        route_managed_candidate_proposals(
            classification,
            (cast(ManagedCandidateTrialSpec[str], object()),),
            **route_kwargs,
        )
    with pytest.raises(ValueError, match="must not repeat"):
        route_managed_candidate_proposals(classification, (spec, spec), **route_kwargs)
    with pytest.raises(ValueError, match="classification"):
        route_managed_candidate_proposals(
            cast(CandidateTaskClassification, object()), (spec,), **route_kwargs
        )
    with pytest.raises(ValueError, match="boundary"):
        route_managed_candidate_proposals(
            classification,
            (spec,),
            **{**route_kwargs, "boundary": cast(CandidateExecutionBoundary, object())},
        )
    with pytest.raises(ValueError, match="evidence_store"):
        route_managed_candidate_proposals(
            classification,
            (spec,),
            **{**route_kwargs, "evidence_store": cast(CapabilityEvidenceStore, object())},
        )
    with pytest.raises(ValueError, match="concurrent"):
        route_managed_candidate_proposals(
            classification,
            (spec,),
            **{**route_kwargs, "concurrent": cast(bool, 1)},
        )
    with pytest.raises(ValueError, match="trace_sink"):
        route_managed_candidate_proposals(
            classification,
            (spec,),
            **route_kwargs,
            trace_sink=cast(Callable[[object], None], object()),
        )

    assert calls == []
    assert store.list_all() == []


def test_trace_sink_failure_is_typed_and_blocks_model_effects(tmp_path: Path) -> None:
    calls: list[ModelCandidateProvider] = []
    store = CapabilityEvidenceStore(str(tmp_path / "evidence.json"))

    def fail_trace(_trace: object) -> None:
        raise RuntimeError("sensitive trace sink detail")

    with pytest.raises(ManagedCandidateRoutingError) as raised:
        route_managed_candidate_proposals(
            classify_candidate_task("Implement one bounded public feature."),
            (_spec(_local_identity(), "private response", calls, accepted=True),),
            boundary=_boundary(tmp_path),
            evidence_store=store,
            prompt_protocol_digest=_digest("prompt-protocol"),
            evaluator_digest=_digest("full-evaluator"),
            sampling_digest=_digest("sampling"),
            concurrent=False,
            trace_sink=fail_trace,
        )

    assert raised.value.failure is ManagedCandidateRouteFailure.TRACE_FAILURE
    assert "sensitive" not in str(raised.value)
    assert calls == []
    assert store.list_all() == []


def test_invalid_adapter_results_are_censored_as_internal_failures(
    tmp_path: Path,
) -> None:
    calls: list[ModelCandidateProvider] = []
    store = CapabilityEvidenceStore(str(tmp_path / "evidence.json"))

    def invalid_usage(_response: object) -> CandidateObservedUsage:
        return cast(CandidateObservedUsage, object())

    def invalid_assessment(_proposal: str) -> CandidateProposalAssessment:
        return cast(CandidateProposalAssessment, object())

    local = _spec(_local_identity(), "sensitive-local-response", calls, accepted=True)
    azure = _spec(
        _azure_identity(),
        AzureCandidateResponse("sensitive-azure-response", 2, 3, 5),
        calls,
        accepted=True,
    )
    invalid_local = ManagedCandidateTrialSpec(
        session=local.session,
        request=local.request,
        decoder=local.decoder,
        usage_reader=invalid_usage,
        assessor=local.assessor,
        predicted_latency_ms=local.predicted_latency_ms,
        predicted_input_tokens=local.predicted_input_tokens,
        predicted_output_tokens=local.predicted_output_tokens,
        predicted_cost_microusd=local.predicted_cost_microusd,
    )
    invalid_azure = ManagedCandidateTrialSpec(
        session=azure.session,
        request=azure.request,
        decoder=azure.decoder,
        usage_reader=azure.usage_reader,
        assessor=invalid_assessment,
        predicted_latency_ms=azure.predicted_latency_ms,
        predicted_input_tokens=azure.predicted_input_tokens,
        predicted_output_tokens=azure.predicted_output_tokens,
        predicted_cost_microusd=azure.predicted_cost_microusd,
    )

    with pytest.raises(ManagedCandidateRoutingError) as raised:
        _route(tmp_path, store, (invalid_local, invalid_azure))

    assert raised.value.failure is ManagedCandidateRouteFailure.NO_EVALUATED_PROPOSAL
    assert calls == [
        ModelCandidateProvider.LOCAL_GGUF,
        ModelCandidateProvider.AZURE_FOUNDRY,
    ]
    assert store.list_all() == []
