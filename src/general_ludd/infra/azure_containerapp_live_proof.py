"""Bounded deploy, inference, and app-only cleanup proof for Azure GPUs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from general_ludd.infra.azure_containerapp_live_trace import (
    build_live_proof_trace as _trace,
)
from general_ludd.infra.azure_containerapp_live_trace import (
    cleanup_emit_live_proof_trace as _cleanup_emit,
)
from general_ludd.infra.azure_containerapp_live_trace import (
    discard_live_proof_trace as _discard_trace,
)
from general_ludd.infra.azure_containerapp_live_trace import (
    emit_live_proof_trace as _emit,
)
from general_ludd.infra.azure_containerapp_live_types import (
    LIVE_PROOF_ACKNOWLEDGEMENT,
    AzureContainerAppDeploymentEvidence,
    AzureContainerAppLiveProofError,
    AzureContainerAppLiveProofFailure,
    AzureContainerAppLiveProofPolicy,
    AzureContainerAppLiveProofResult,
    AzureContainerAppProofRuntime,
    LiveProofEvent,
    LiveProofTrace,
)
from general_ludd.infra.azure_containerapp_plan_audit import audit_containerapp_plan
from general_ludd.self_improve.azure_backend import (
    AzureApprovedPrompt,
    AzureCandidateResponse,
)
from general_ludd.self_improve.model_candidates import (
    AzureContainerAppCandidateIdentity,
    BoundedCandidateSession,
    CandidateBackend,
)

_ModelBackend = CandidateBackend[AzureApprovedPrompt, AzureCandidateResponse]
_BackendFactory = Callable[[AzureContainerAppCandidateIdentity], _ModelBackend]


@dataclass(slots=True)
class _LiveProofAttempt:
    """Mutable ownership ledger for the one live deployment attempt."""

    backend: _ModelBackend | None = None
    response: AzureCandidateResponse | None = None
    identity: AzureContainerAppCandidateIdentity | None = None
    failure: AzureContainerAppLiveProofError | None = None
    apply_started: bool = False


def _validated_input_tokens(
    policy: AzureContainerAppLiveProofPolicy,
    runtime: AzureContainerAppProofRuntime,
    approved_prompt: AzureApprovedPrompt,
    backend_factory: _BackendFactory,
    trace_sink: Callable[[LiveProofTrace], None],
) -> int:
    """Validate every boundary and recheck prompt authority before effects."""
    if not isinstance(policy, AzureContainerAppLiveProofPolicy):
        raise ValueError("policy must be an AzureContainerAppLiveProofPolicy")
    if not isinstance(runtime, AzureContainerAppProofRuntime):
        raise ValueError("runtime must implement AzureContainerAppProofRuntime")
    if not isinstance(approved_prompt, AzureApprovedPrompt):
        raise ValueError("approved_prompt must be an AzureApprovedPrompt")
    if not callable(backend_factory):
        raise ValueError("backend_factory must be callable")
    if not callable(trace_sink):
        raise ValueError("trace_sink must be callable")
    try:
        prompt = approved_prompt._reveal_after_recheck()
        input_tokens = max(1, len(prompt.encode("utf-8")))
        if input_tokens > policy.call_budget.max_input_tokens:
            raise ValueError
        return input_tokens
    except Exception:
        raise AzureContainerAppLiveProofError(
            AzureContainerAppLiveProofFailure.POLICY
        ) from None


def _audit_requested_plan(
    policy: AzureContainerAppLiveProofPolicy,
    runtime: AzureContainerAppProofRuntime,
    trace_sink: Callable[[LiveProofTrace], None],
) -> None:
    """Produce and audit one exact single-create Terraform plan."""
    _emit(trace_sink, _trace(LiveProofEvent.POLICY_VALIDATED, policy))
    _emit(trace_sink, _trace(LiveProofEvent.PLAN_STARTED, policy))
    try:
        plan = runtime.plan(policy)
    except Exception:
        error = AzureContainerAppLiveProofError(
            AzureContainerAppLiveProofFailure.PLAN_SCOPE,
            detail="runtime_plan",
        )
        _emit(
            trace_sink,
            _trace(
                LiveProofEvent.FAILED,
                policy,
                failure=error.failure,
                failure_detail=error.detail,
            ),
        )
        raise error from None
    try:
        audit_containerapp_plan(plan, policy)
    except AzureContainerAppLiveProofError as error:
        _emit(
            trace_sink,
            _trace(
                LiveProofEvent.FAILED,
                policy,
                failure=error.failure,
                failure_detail=error.detail,
            ),
        )
        raise
    _emit(
        trace_sink,
        _trace(LiveProofEvent.PLAN_AUDITED, policy, resource_change_count=1),
    )


def _run_preflight(
    policy: AzureContainerAppLiveProofPolicy,
    runtime: AzureContainerAppProofRuntime,
    trace_sink: Callable[[LiveProofTrace], None],
) -> None:
    """Require named-environment GPU headroom before paid mutation."""
    _emit(trace_sink, _trace(LiveProofEvent.PREFLIGHT_STARTED, policy))
    try:
        runtime.preflight(policy)
    except Exception:
        raise AzureContainerAppLiveProofError(
            AzureContainerAppLiveProofFailure.PREFLIGHT
        ) from None
    _emit(trace_sink, _trace(LiveProofEvent.PREFLIGHT_SUCCEEDED, policy))


def _execute_live_work(
    attempt: _LiveProofAttempt,
    policy: AzureContainerAppLiveProofPolicy,
    runtime: AzureContainerAppProofRuntime,
    approved_prompt: AzureApprovedPrompt,
    backend_factory: _BackendFactory,
    trace_sink: Callable[[LiveProofTrace], None],
    input_tokens: int,
) -> None:
    """Deploy, bind, discover, and invoke one candidate exactly once."""
    _emit(trace_sink, _trace(LiveProofEvent.APPLY_STARTED, policy))
    attempt.apply_started = True
    try:
        evidence = runtime.apply(policy)
    except Exception:
        raise AzureContainerAppLiveProofError(
            AzureContainerAppLiveProofFailure.APPLY
        ) from None
    _emit(trace_sink, _trace(LiveProofEvent.APPLY_SUCCEEDED, policy))
    try:
        attempt.identity = evidence.candidate_identity(policy)
    except Exception:
        raise AzureContainerAppLiveProofError(
            AzureContainerAppLiveProofFailure.DEPLOYMENT_EVIDENCE
        ) from None
    identity = attempt.identity
    _emit(
        trace_sink,
        _trace(
            LiveProofEvent.DEPLOYMENT_VERIFIED,
            policy,
            candidate_digest=identity.identity_digest,
        ),
    )
    _emit(
        trace_sink,
        _trace(
            LiveProofEvent.DISCOVERY_STARTED,
            policy,
            candidate_digest=identity.identity_digest,
        ),
    )
    try:
        attempt.backend = backend_factory(identity)
        if (
            not isinstance(attempt.backend, CandidateBackend)
            or attempt.backend.candidate_identity.identity_digest
            != identity.identity_digest
        ):
            raise ValueError
    except Exception:
        raise AzureContainerAppLiveProofError(
            AzureContainerAppLiveProofFailure.DISCOVERY
        ) from None
    _emit(
        trace_sink,
        _trace(
            LiveProofEvent.DISCOVERY_SUCCEEDED,
            policy,
            candidate_digest=identity.identity_digest,
        ),
    )
    session = BoundedCandidateSession(attempt.backend, policy.call_budget, azure_enabled=True)
    _emit(
        trace_sink,
        _trace(
            LiveProofEvent.WORK_REQUEST_STARTED,
            policy,
            candidate_digest=identity.identity_digest,
        ),
    )
    try:
        attempt.response = session.generate(
            approved_prompt,
            input_tokens=input_tokens,
            max_output_tokens=policy.call_budget.max_output_tokens,
            estimated_cost_microusd=policy.estimated_request_cost_microusd,
        )
    except Exception:
        raise AzureContainerAppLiveProofError(
            AzureContainerAppLiveProofFailure.WORK_REQUEST
        ) from None
    _emit(
        trace_sink,
        _trace(
            LiveProofEvent.WORK_REQUEST_SUCCEEDED,
            policy,
            candidate_digest=identity.identity_digest,
            response=attempt.response,
        ),
    )


def _cleanup_live_attempt(
    attempt: _LiveProofAttempt,
    policy: AzureContainerAppLiveProofPolicy,
    runtime: AzureContainerAppProofRuntime,
    trace_sink: Callable[[LiveProofTrace], None],
) -> bool:
    """Close the backend and prove app absence without masking cleanup failure."""
    cleanup_failed = False
    if attempt.backend is not None:
        try:
            close = getattr(attempt.backend, "close", None)
            if callable(close):
                close()
            else:
                cleanup_failed = True
        except Exception:
            cleanup_failed = True
        cleanup_failed = not _cleanup_emit(
            trace_sink,
            _trace(
                LiveProofEvent.BACKEND_CLOSED,
                policy,
                candidate_digest=(
                    None
                    if attempt.identity is None
                    else attempt.identity.identity_digest
                ),
            ),
        ) or cleanup_failed
    if not attempt.apply_started:
        return cleanup_failed
    cleanup_failed = not _cleanup_emit(
        trace_sink,
        _trace(LiveProofEvent.DESTROY_STARTED, policy),
    ) or cleanup_failed
    try:
        runtime.destroy(policy)
    except Exception:
        return True
    cleanup_failed = not _cleanup_emit(
        trace_sink,
        _trace(LiveProofEvent.DESTROY_SUCCEEDED, policy),
    ) or cleanup_failed
    try:
        remains = runtime.exists(policy)
    except Exception:
        return True
    if remains:
        return True
    return not _cleanup_emit(
        trace_sink,
        _trace(LiveProofEvent.ABSENCE_VERIFIED, policy),
    ) or cleanup_failed


def _completed_result(
    attempt: _LiveProofAttempt,
    policy: AzureContainerAppLiveProofPolicy,
    trace_sink: Callable[[LiveProofTrace], None],
) -> AzureContainerAppLiveProofResult:
    """Validate terminal state, publish completion, and build public evidence."""
    if attempt.failure is not None:
        raise attempt.failure from None
    if attempt.response is None or attempt.identity is None:
        raise AzureContainerAppLiveProofError(
            AzureContainerAppLiveProofFailure.WORK_REQUEST
        ) from None
    response = attempt.response
    identity = attempt.identity
    _emit(
        trace_sink,
        _trace(
            LiveProofEvent.COMPLETED,
            policy,
            candidate_digest=identity.identity_digest,
            response=response,
        ),
    )
    return AzureContainerAppLiveProofResult(
        plan_audited=True,
        deployment_created=True,
        work_completed=True,
        cleanup_verified=True,
        operation_digest=policy.operation_digest,
        candidate_identity_digest=identity.identity_digest,
        response_text=response.text,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        total_tokens=response.total_tokens,
    )


def run_azure_containerapp_live_proof(
    policy: AzureContainerAppLiveProofPolicy,
    *,
    runtime: AzureContainerAppProofRuntime,
    approved_prompt: AzureApprovedPrompt,
    backend_factory: _BackendFactory,
    trace_sink: Callable[[LiveProofTrace], None] = _discard_trace,
) -> AzureContainerAppLiveProofResult:
    """Audit, optionally deploy, invoke once, and verify app-only cleanup."""
    input_tokens = _validated_input_tokens(
        policy, runtime, approved_prompt, backend_factory, trace_sink
    )
    _audit_requested_plan(policy, runtime, trace_sink)
    if not policy.live:
        _emit(trace_sink, _trace(LiveProofEvent.DRY_RUN_COMPLETED, policy))
        return AzureContainerAppLiveProofResult(
            plan_audited=True,
            deployment_created=False,
            work_completed=False,
            cleanup_verified=False,
            operation_digest=policy.operation_digest,
        )
    _run_preflight(policy, runtime, trace_sink)
    attempt = _LiveProofAttempt()
    try:
        _execute_live_work(
            attempt,
            policy,
            runtime,
            approved_prompt,
            backend_factory,
            trace_sink,
            input_tokens,
        )
    except AzureContainerAppLiveProofError as error:
        attempt.failure = error
    if _cleanup_live_attempt(attempt, policy, runtime, trace_sink):
        raise AzureContainerAppLiveProofError(
            AzureContainerAppLiveProofFailure.CLEANUP
        ) from None
    return _completed_result(attempt, policy, trace_sink)


__all__ = (
    "LIVE_PROOF_ACKNOWLEDGEMENT",
    "AzureContainerAppDeploymentEvidence",
    "AzureContainerAppLiveProofError",
    "AzureContainerAppLiveProofFailure",
    "AzureContainerAppLiveProofPolicy",
    "AzureContainerAppLiveProofResult",
    "AzureContainerAppProofRuntime",
    "LiveProofEvent",
    "LiveProofTrace",
    "audit_containerapp_plan",
    "run_azure_containerapp_live_proof",
)
