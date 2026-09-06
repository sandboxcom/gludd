"""Empirically rank, execute, and select explicit managed model candidates.

The router owns no provider client and never sees repository paths or proposal
text in its trace records.  Callers provide already bounded sessions plus exact
decode and evaluation adapters.  Every candidate in the plan is invoked once;
there is no implicit retry or fallback.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from typing import TypeVar

from general_ludd.self_improve._candidate_calibration import (
    load_calibration_attempts,
)
from general_ludd.self_improve._candidate_execution_types import (
    MAX_CANDIDATE_TRIALS,
    CandidateEvaluation,
    CandidateExecutionBoundary,
    CandidateExecutionResult,
    CandidateTrialCall,
)
from general_ludd.self_improve._candidate_prediction import (
    CandidatePrediction,
    require_digest,
    stable_digest,
)
from general_ludd.self_improve._candidate_trials import (
    CandidateTrialPlan,
    plan_bounded_candidate_trials,
)
from general_ludd.self_improve.candidate_classification import (
    CandidateTaskClassification,
)
from general_ludd.self_improve.candidate_execution import (
    execute_candidate_trial_plan,
)
from general_ludd.self_improve.managed_candidate_routing_types import (
    CandidateObservedUsage,
    CandidateProposalAssessment,
    CandidateProposalDecodeRejected,
    ManagedCandidateProposalCodec,
    ManagedCandidateRouteFailure,
    ManagedCandidateRoutingError,
    ManagedCandidateRoutingEvent,
    ManagedCandidateRoutingResult,
    ManagedCandidateRoutingTrace,
    ManagedCandidateTrialSpec,
)
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

_ProposalT = TypeVar("_ProposalT")


def _validated_specs(
    specs: Sequence[ManagedCandidateTrialSpec[_ProposalT]],
) -> tuple[ManagedCandidateTrialSpec[_ProposalT], ...]:
    items = tuple(specs)
    if not 1 <= len(items) <= MAX_CANDIDATE_TRIALS:
        raise ValueError("candidate specs must contain between one and sixteen items")
    if not all(isinstance(item, ManagedCandidateTrialSpec) for item in items):
        raise ValueError("candidate specs must contain ManagedCandidateTrialSpec values")
    identities = tuple(item.session.candidate_identity.identity_digest for item in items)
    if len(identities) != len(set(identities)):
        raise ValueError("candidate specs must not repeat an immutable identity")
    return items


def _evaluation_stratum_digest(
    classification: CandidateTaskClassification,
    *,
    prompt_protocol_digest: str,
    evaluator_digest: str,
    sampling_digest: str,
    privacy_policy_digest: str,
) -> str:
    return stable_digest(
        {
            "evaluator_digest": evaluator_digest,
            "matched_capabilities": [
                capability.payload()
                for capability in classification.matched_capabilities
            ],
            "precedence_version": classification.precedence_version,
            "privacy_policy_digest": privacy_policy_digest,
            "prompt_protocol_digest": prompt_protocol_digest,
            "protocol": "gludd-managed-candidate-routing-stratum-v1",
            "sampling_digest": sampling_digest,
            "task_kind": classification.task_kind,
            "task_role": classification.task_role.value,
            "task_type": classification.task_type.value,
        }
    )


def _predictions(
    classification: CandidateTaskClassification,
    specs: tuple[ManagedCandidateTrialSpec[_ProposalT], ...],
    *,
    prompt_protocol_digest: str,
    evaluator_digest: str,
    sampling_digest: str,
    privacy_policy_digest: str,
) -> tuple[CandidatePrediction, ...]:
    stratum = _evaluation_stratum_digest(
        classification,
        prompt_protocol_digest=prompt_protocol_digest,
        evaluator_digest=evaluator_digest,
        sampling_digest=sampling_digest,
        privacy_policy_digest=privacy_policy_digest,
    )
    return tuple(
        CandidatePrediction(
            candidate_identity_digest=spec.session.candidate_identity.identity_digest,
            provider=spec.session.candidate_identity.provider,
            task_type=classification.task_type,
            task_kind=classification.task_kind,
            evaluation_stratum_digest=stratum,
            prompt_protocol_digest=prompt_protocol_digest,
            evaluator_digest=evaluator_digest,
            sampling_digest=sampling_digest,
            privacy_policy_digest=privacy_policy_digest,
            predicted_acceptance=spec.prior_acceptance,
            predicted_latency_ms=spec.predicted_latency_ms,
            predicted_input_tokens=spec.predicted_input_tokens,
            predicted_output_tokens=spec.predicted_output_tokens,
            predicted_cost_microusd=spec.predicted_cost_microusd,
        )
        for spec in specs
    )


def _emit_routing_trace(
    sink: Callable[[object], None],
    trace: ManagedCandidateRoutingTrace,
) -> None:
    try:
        sink(trace)
    except Exception:
        raise ManagedCandidateRoutingError(
            ManagedCandidateRouteFailure.TRACE_FAILURE
        ) from None


def _candidate_evaluator(
    spec: ManagedCandidateTrialSpec[_ProposalT],
    identity_digest: str,
    decoded: dict[str, _ProposalT],
    assessments: dict[str, CandidateProposalAssessment],
    lock: threading.Lock,
) -> Callable[[object], CandidateEvaluation]:
    def evaluate(response: object) -> CandidateEvaluation:
        usage = spec.usage_reader(response)
        if not isinstance(usage, CandidateObservedUsage):
            raise TypeError("usage reader returned an invalid observation")
        try:
            proposal = spec.decoder(response)
        except CandidateProposalDecodeRejected:
            return CandidateEvaluation(
                accepted=False,
                evaluation_score=0.0,
                blocker_count=1,
                observed_input_tokens=usage.input_tokens,
                observed_output_tokens=usage.output_tokens,
                observed_cost_microusd=usage.cost_microusd,
            )
        assessment = spec.assessor(proposal)
        if not isinstance(assessment, CandidateProposalAssessment):
            raise TypeError("assessor returned an invalid quality result")
        with lock:
            decoded[identity_digest] = proposal
            assessments[identity_digest] = assessment
        return CandidateEvaluation(
            accepted=assessment.accepted,
            evaluation_score=assessment.score,
            blocker_count=assessment.blocker_count,
            observed_input_tokens=usage.input_tokens,
            observed_output_tokens=usage.output_tokens,
            observed_cost_microusd=usage.cost_microusd,
        )

    return evaluate


def _trial_calls(
    plan: CandidateTrialPlan,
    specs: tuple[ManagedCandidateTrialSpec[_ProposalT], ...],
    decoded: dict[str, _ProposalT],
    assessments: dict[str, CandidateProposalAssessment],
    lock: threading.Lock,
) -> tuple[CandidateTrialCall, ...]:
    by_identity = {
        spec.session.candidate_identity.identity_digest: spec for spec in specs
    }
    return tuple(
        CandidateTrialCall(
            ordinal=trial.ordinal,
            session=by_identity[trial.prediction.candidate_identity_digest].session,
            request=by_identity[trial.prediction.candidate_identity_digest].request,
            evaluator=_candidate_evaluator(
                by_identity[trial.prediction.candidate_identity_digest],
                trial.prediction.candidate_identity_digest,
                decoded,
                assessments,
                lock,
            ),
        )
        for trial in plan.trials
    )


def _selected_identity(
    execution: CandidateExecutionResult,
    decoded: dict[str, _ProposalT],
) -> str | None:
    evaluated = tuple(
        trial
        for trial in execution.trials
        if trial.candidate_identity_digest in decoded and trial.attempt.is_evaluated
    )
    accepted = tuple(trial for trial in evaluated if trial.attempt.accepted)
    selected = accepted[0] if accepted else (evaluated[0] if evaluated else None)
    return None if selected is None else selected.candidate_identity_digest


def _validated_route_configuration(
    classification: CandidateTaskClassification,
    specs: Sequence[ManagedCandidateTrialSpec[_ProposalT]],
    boundary: CandidateExecutionBoundary,
    evidence_store: CapabilityEvidenceStore,
    prompt_protocol_digest: str,
    evaluator_digest: str,
    sampling_digest: str,
    concurrent: bool,
    trace_sink: Callable[[object], None] | None,
) -> tuple[
    tuple[ManagedCandidateTrialSpec[_ProposalT], ...],
    str,
    str,
    str,
    Callable[[object], None],
]:
    """Validate every route input before model, trace, or storage effects."""
    if not isinstance(classification, CandidateTaskClassification):
        raise ValueError("classification must be a CandidateTaskClassification")
    candidates = _validated_specs(specs)
    if not isinstance(boundary, CandidateExecutionBoundary):
        raise ValueError("boundary must be a CandidateExecutionBoundary")
    if not isinstance(evidence_store, CapabilityEvidenceStore):
        raise ValueError("evidence_store must be a CapabilityEvidenceStore")
    prompt_digest = require_digest(prompt_protocol_digest, "prompt_protocol_digest")
    quality_digest = require_digest(evaluator_digest, "evaluator_digest")
    sample_digest = require_digest(sampling_digest, "sampling_digest")
    if not isinstance(concurrent, bool):
        raise ValueError("concurrent must be an explicit boolean")
    sink = trace_sink if trace_sink is not None else (lambda _trace: None)
    if not callable(sink):
        raise ValueError("trace_sink must be callable")
    return candidates, prompt_digest, quality_digest, sample_digest, sink


def _finalize_routing_result(
    execution: CandidateExecutionResult,
    decoded: dict[str, _ProposalT],
    assessments: dict[str, CandidateProposalAssessment],
    plan: CandidateTrialPlan,
    sink: Callable[[object], None],
) -> ManagedCandidateRoutingResult[_ProposalT]:
    """Select the first calibrated result or emit one censored terminal event."""
    identity_digest = _selected_identity(execution, decoded)
    if identity_digest is None:
        _emit_routing_trace(
            sink,
            ManagedCandidateRoutingTrace(
                ManagedCandidateRoutingEvent.NO_EVALUATED_PROPOSAL,
                plan.plan_digest,
                len(plan.trials),
            ),
        )
        raise ManagedCandidateRoutingError(
            ManagedCandidateRouteFailure.NO_EVALUATED_PROPOSAL
        )
    prediction = next(
        trial.prediction
        for trial in plan.trials
        if trial.prediction.candidate_identity_digest == identity_digest
    )
    assessment = assessments[identity_digest]
    _emit_routing_trace(
        sink,
        ManagedCandidateRoutingTrace(
            ManagedCandidateRoutingEvent.CANDIDATE_SELECTED,
            plan.plan_digest,
            len(plan.trials),
            candidate_identity_digest=identity_digest,
            provider=prediction.provider.value,
            accepted=assessment.accepted,
        ),
    )
    return ManagedCandidateRoutingResult(
        selected=decoded[identity_digest],
        selected_prediction=prediction,
        selected_assessment=assessment,
        execution=execution,
    )


def route_managed_candidate_proposals(
    classification: CandidateTaskClassification,
    specs: Sequence[ManagedCandidateTrialSpec[_ProposalT]],
    *,
    boundary: CandidateExecutionBoundary,
    evidence_store: CapabilityEvidenceStore,
    prompt_protocol_digest: str,
    evaluator_digest: str,
    sampling_digest: str,
    concurrent: bool,
    trace_sink: Callable[[object], None] | None = None,
) -> ManagedCandidateRoutingResult[_ProposalT]:
    """Execute every explicitly planned candidate once and select by evidence."""
    candidates, prompt_digest, quality_digest, sample_digest, sink = (
        _validated_route_configuration(
            classification,
            specs,
            boundary,
            evidence_store,
            prompt_protocol_digest,
            evaluator_digest,
            sampling_digest,
            concurrent,
            trace_sink,
        )
    )

    predictions = _predictions(
        classification,
        candidates,
        prompt_protocol_digest=prompt_digest,
        evaluator_digest=quality_digest,
        sampling_digest=sample_digest,
        privacy_policy_digest=boundary.expected_privacy_policy_digest,
    )
    attempts = load_calibration_attempts(
        evidence_store,
        evaluation_stratum_digest=predictions[0].evaluation_stratum_digest,
    )
    plan = plan_bounded_candidate_trials(
        predictions,
        attempts,
        max_trials=len(predictions),
        challenge_trials=max(0, len(predictions) - 1),
        concurrent=concurrent,
    )
    _emit_routing_trace(
        sink,
        ManagedCandidateRoutingTrace(
            ManagedCandidateRoutingEvent.PLAN_CREATED,
            plan.plan_digest,
            len(plan.trials),
        ),
    )
    decoded: dict[str, _ProposalT] = {}
    assessments: dict[str, CandidateProposalAssessment] = {}
    decoded_lock = threading.Lock()
    calls = _trial_calls(plan, candidates, decoded, assessments, decoded_lock)
    execution = execute_candidate_trial_plan(
        plan,
        calls,
        approved_plan_digest=plan.plan_digest,
        boundary=boundary,
        evidence_store=evidence_store,
        trace_sink=lambda trace: sink(trace),
    )
    return _finalize_routing_result(execution, decoded, assessments, plan, sink)


__all__ = (
    "CandidateObservedUsage",
    "CandidateProposalAssessment",
    "CandidateProposalDecodeRejected",
    "ManagedCandidateProposalCodec",
    "ManagedCandidateRouteFailure",
    "ManagedCandidateRoutingError",
    "ManagedCandidateRoutingEvent",
    "ManagedCandidateRoutingResult",
    "ManagedCandidateRoutingTrace",
    "ManagedCandidateTrialSpec",
    "route_managed_candidate_proposals",
)
