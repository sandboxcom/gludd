"""Compose owned environment lifecycle around one bounded Container App proof."""

from __future__ import annotations

from collections.abc import Callable

from general_ludd.infra.azure_containerapp_environment_lifecycle import (
    AzureContainerAppEnvironmentRuntime,
    AzureEnvironmentLifecyclePolicy,
    AzureEnvironmentProfile,
    EnvironmentLifecycleDisposition,
    EnvironmentLifecycleTrace,
    ensure_azure_containerapp_environment,
    release_azure_containerapp_environment,
)
from general_ludd.infra.azure_containerapp_live_proof import (
    AzureContainerAppLiveProofError,
    AzureContainerAppLiveProofFailure,
    AzureContainerAppLiveProofPolicy,
    AzureContainerAppLiveProofResult,
    AzureContainerAppProofRuntime,
    LiveProofTrace,
    run_azure_containerapp_live_proof,
)
from general_ludd.self_improve.azure_backend import (
    AzureApprovedPrompt,
    AzureCandidateResponse,
)
from general_ludd.self_improve.model_candidates import (
    AzureContainerAppCandidateIdentity,
    CandidateBackend,
)

_Backend = CandidateBackend[AzureApprovedPrompt, AzureCandidateResponse]
_BackendFactory = Callable[[AzureContainerAppCandidateIdentity], _Backend]


def _discard_environment_trace(_trace: EnvironmentLifecycleTrace) -> None:
    return None


def _discard_app_trace(_trace: LiveProofTrace) -> None:
    return None


def _validate_boundaries(
    app_policy: AzureContainerAppLiveProofPolicy,
    environment_policy: AzureEnvironmentLifecyclePolicy,
    environment_runtime: AzureContainerAppEnvironmentRuntime,
    app_runtime: AzureContainerAppProofRuntime,
    environment_trace_sink: Callable[[EnvironmentLifecycleTrace], None],
    app_trace_sink: Callable[[LiveProofTrace], None],
) -> None:
    if not isinstance(app_policy, AzureContainerAppLiveProofPolicy):
        raise ValueError("app_policy must be AzureContainerAppLiveProofPolicy")
    if not isinstance(environment_policy, AzureEnvironmentLifecyclePolicy):
        raise ValueError(
            "environment_policy must be AzureEnvironmentLifecyclePolicy"
        )
    if not isinstance(environment_runtime, AzureContainerAppEnvironmentRuntime):
        raise ValueError("environment_runtime must implement its runtime protocol")
    if not isinstance(app_runtime, AzureContainerAppProofRuntime):
        raise ValueError("app_runtime must implement its runtime protocol")
    if not callable(environment_trace_sink) or not callable(app_trace_sink):
        raise ValueError("trace sinks must be callable")


def validate_owned_azure_containerapp_authority(
    app_policy: AzureContainerAppLiveProofPolicy,
    environment_policy: AzureEnvironmentLifecyclePolicy,
) -> None:
    """Require app and environment policies to share one teardown authority."""
    try:
        required_profile = AzureEnvironmentProfile(
            app_policy.workload_profile_name,
            app_policy.workload_profile_type,
        )
        if (
            app_policy.subscription_id != environment_policy.subscription_id
            or app_policy.resource_group != environment_policy.resource_group
            or app_policy.environment_name != environment_policy.environment_name
            or app_policy.location != environment_policy.location
            or required_profile not in environment_policy.profiles
        ):
            raise ValueError
    except Exception:
        raise AzureContainerAppLiveProofError(
            AzureContainerAppLiveProofFailure.POLICY
        ) from None


def _release_owned_environment(
    environment_policy: AzureEnvironmentLifecyclePolicy,
    environment_runtime: AzureContainerAppEnvironmentRuntime,
    environment_trace_sink: Callable[[EnvironmentLifecycleTrace], None],
) -> bool:
    try:
        release = release_azure_containerapp_environment(
            environment_policy,
            runtime=environment_runtime,
            trace_sink=environment_trace_sink,
        )
        return release.disposition in {
            EnvironmentLifecycleDisposition.ABSENT,
            EnvironmentLifecycleDisposition.DESTROYED,
        }
    except Exception:
        return False


def run_owned_azure_containerapp_live_proof(
    app_policy: AzureContainerAppLiveProofPolicy,
    *,
    environment_policy: AzureEnvironmentLifecyclePolicy,
    environment_runtime: AzureContainerAppEnvironmentRuntime,
    app_runtime: AzureContainerAppProofRuntime,
    approved_prompt: AzureApprovedPrompt,
    backend_factory: _BackendFactory,
    environment_trace_sink: Callable[
        [EnvironmentLifecycleTrace], None
    ] = _discard_environment_trace,
    app_trace_sink: Callable[[LiveProofTrace], None] = _discard_app_trace,
) -> AzureContainerAppLiveProofResult:
    """Create/reconcile, use, and finally tear down one owned environment."""
    _validate_boundaries(
        app_policy,
        environment_policy,
        environment_runtime,
        app_runtime,
        environment_trace_sink,
        app_trace_sink,
    )
    validate_owned_azure_containerapp_authority(app_policy, environment_policy)
    if not app_policy.live:
        return run_azure_containerapp_live_proof(
            app_policy,
            runtime=app_runtime,
            approved_prompt=approved_prompt,
            backend_factory=backend_factory,
            trace_sink=app_trace_sink,
        )

    try:
        ensure_azure_containerapp_environment(
            environment_policy,
            runtime=environment_runtime,
            trace_sink=environment_trace_sink,
        )
    except Exception:
        raise AzureContainerAppLiveProofError(
            AzureContainerAppLiveProofFailure.ENVIRONMENT
        ) from None

    proof: AzureContainerAppLiveProofResult | None = None
    proof_failure: AzureContainerAppLiveProofError | None = None
    try:
        proof = run_azure_containerapp_live_proof(
            app_policy,
            runtime=app_runtime,
            approved_prompt=approved_prompt,
            backend_factory=backend_factory,
            trace_sink=app_trace_sink,
        )
    except AzureContainerAppLiveProofError as error:
        proof_failure = error
    except Exception:
        proof_failure = AzureContainerAppLiveProofError(
            AzureContainerAppLiveProofFailure.POLICY
        )

    if not _release_owned_environment(
        environment_policy,
        environment_runtime,
        environment_trace_sink,
    ):
        raise AzureContainerAppLiveProofError(
            AzureContainerAppLiveProofFailure.CLEANUP
        ) from None
    if proof_failure is not None:
        raise proof_failure from None
    if proof is None:
        raise AzureContainerAppLiveProofError(
            AzureContainerAppLiveProofFailure.POLICY
        )
    return proof


__all__ = (
    "run_owned_azure_containerapp_live_proof",
    "validate_owned_azure_containerapp_authority",
)
