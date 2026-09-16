"""Owned Azure runtime construction and secret-free lifecycle tracing."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol, cast

from general_ludd.azure.accelerator_credentials import (
    AzureAcceleratorAuthentication,
)
from general_ludd.azure.resource_group_bootstrap import (
    AzureResourceGroupBootstrapError,
    AzureResourceGroupBootstrapPolicy,
)
from general_ludd.infra.azure_containerapp_environment_lifecycle import (
    AzureEnvironmentLifecyclePolicy,
)
from general_ludd.infra.azure_containerapp_gpu import ModelServingRequirement
from general_ludd.infra.azure_containerapp_live_proof import (
    AzureContainerAppLiveProofPolicy,
)
from general_ludd.infra.azure_containerapp_owned_candidate import (
    OwnedCandidateLifecycleTrace,
    owned_candidate_deployment_digest,
)
from general_ludd.infra.azure_containerapp_owned_candidate_types import (
    OwnedCandidateLifecycleError,
)
from general_ludd.infra.azure_containerapp_preflight_types import PreflightTrace
from general_ludd.infra.azure_containerapp_sdk import AzureGPUMetricResponseReason
from general_ludd.infra.azure_idle_retention import AzureIdleRetentionPolicy
from general_ludd.self_improve.azure_containerapp_bootstrap_credentials import (
    AzureCredentialProvider,
    release_once,
)
from general_ludd.self_improve.azure_containerapp_transport_types import (
    ContainerAppGPUAttestationSource,
    ContainerAppResponseFailure,
)
from general_ludd.self_improve.azure_infrastructure_evidence import (
    AzureInfrastructurePhase,
    record_azure_infrastructure_failure,
)
from general_ludd.self_improve.azure_operational_availability import (
    AzureAvailabilityTerminal,
    build_azure_availability_scope,
    record_azure_availability_terminal,
)
from general_ludd.self_improve.live_candidate_wiring import (
    ContainerAppCandidateBackend,
)
from general_ludd.self_improve.model_candidates import (
    BackendFailure,
    BackendInfrastructureError,
)
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

_RESOURCE_GROUP_FAILURES = {
    "authentication": BackendFailure.AUTHENTICATION,
    "authorization": BackendFailure.AUTHORIZATION,
    "quota": BackendFailure.RATE_LIMITED,
    "ownership": BackendFailure.UNAVAILABLE,
    "conflict": BackendFailure.UNAVAILABLE,
    "dependency": BackendFailure.UNAVAILABLE,
    "cleanup": BackendFailure.INTERNAL,
    "internal": BackendFailure.INTERNAL,
}
_PROFILE_OPERATIONAL_FAILURES = frozenset(
    {
        BackendFailure.RATE_LIMITED,
        BackendFailure.TIMEOUT,
        BackendFailure.UNAVAILABLE,
    }
)
_OWNERSHIP_STATES = frozenset(
    {
        "exact_owned",
        "legacy_owned",
        "location_mismatch",
        "name_mismatch",
        "operator_staged",
        "owner_mismatch",
        "reserved_tag_mismatch",
        "untagged_handoff",
    }
)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_PREFLIGHT_HTTP_REASON = re.compile(
    r"^(?:environment|usages|workload_profile_states)_http_[1-5][0-9]{2}$"
)
_PREFLIGHT_REASONS = frozenset(
    {
        "authentication_failed",
        "environment_identity_mismatch",
        "environment_location_mismatch",
        "environment_not_found",
        "environment_not_ready",
        "environment_read_failed",
        "environment_response_invalid",
        "environment_unauthorized",
        "gpu_quota_exhausted",
        "profile_unavailable",
        "provider_evidence_unavailable",
        "usage_response_invalid",
        "usages_not_found",
        "usages_read_failed",
        "usages_unauthorized",
        "workload_profile_disabled",
        "workload_profile_missing",
        "workload_profile_state_invalid",
        "workload_profile_state_incomplete",
        "workload_profile_state_missing",
        "workload_profile_states_not_found",
        "workload_profile_states_read_failed",
        "workload_profile_states_unauthorized",
        "workload_profile_type_mismatch",
    }
)
_PREFLIGHT_INVALID_RESPONSE_REASONS = frozenset(
    {
        "environment_identity_mismatch",
        "environment_location_mismatch",
        "environment_response_invalid",
        "usage_response_invalid",
        "workload_profile_state_invalid",
        "workload_profile_type_mismatch",
    }
)
_GPU_ATTESTATION_REASONS = frozenset(
    reason.value for reason in AzureGPUMetricResponseReason
)


def _safe_preflight_reason(value: object) -> str | None:
    """Return only one locally defined, content-free preflight reason."""
    if not isinstance(value, str):
        return None
    if value in _PREFLIGHT_REASONS or _PREFLIGHT_HTTP_REASON.fullmatch(value):
        return value
    return None


def _safe_gpu_attestation_reason(value: object) -> str | None:
    """Return only one locally defined, content-free GPU refusal reason."""
    return value if isinstance(value, str) and value in _GPU_ATTESTATION_REASONS else None


def _preflight_backend_failure(reason: object) -> BackendFailure | None:
    """Map one validated preflight refusal into model-neutral infrastructure."""
    safe_reason = _safe_preflight_reason(reason)
    if safe_reason is None:
        return None
    if safe_reason == "authentication_failed":
        return BackendFailure.AUTHENTICATION
    if safe_reason.endswith("_unauthorized"):
        return BackendFailure.AUTHORIZATION
    if safe_reason.endswith("_not_found"):
        return BackendFailure.NOT_FOUND
    if safe_reason == "gpu_quota_exhausted" or safe_reason.endswith("_http_429"):
        return BackendFailure.RATE_LIMITED
    if safe_reason.endswith(("_http_408", "_http_504")):
        return BackendFailure.TIMEOUT
    if safe_reason.endswith("_read_failed") or re.search(
        r"_http_5[0-9]{2}$", safe_reason
    ):
        return BackendFailure.TRANSPORT
    if re.search(r"_http_4[0-9]{2}$", safe_reason):
        return BackendFailure.INVALID_RESPONSE
    if safe_reason in _PREFLIGHT_INVALID_RESPONSE_REASONS:
        return BackendFailure.INVALID_RESPONSE
    return BackendFailure.UNAVAILABLE


class AzureBootstrapRuntimeResources(Protocol):
    """Minimum composite resource lifecycle constructed by the Azure builder."""

    runtime: object
    environment_runtime: object
    backend_factory: object

    def close(self) -> None:
        """Release the complete composite resource lifecycle."""
        ...


def runtime_trace(
    progress_sink: Callable[[str], None],
    component: str,
    event: object,
) -> None:
    """Emit one allowlisted, secret-free Azure lifecycle event."""
    phase = getattr(event, "phase", getattr(event, "event", "unknown"))
    state = getattr(event, "state", "observed")
    digest = getattr(event, "operation_digest", None) or getattr(
        event,
        "candidate_identity_digest",
        getattr(event, "candidate_digest", None),
    )
    elapsed = getattr(event, "elapsed_seconds", 0)
    failure_class = getattr(event, "failure_class", None) or getattr(
        event, "failure", None
    )
    if isinstance(failure_class, BackendFailure):
        failure_class = failure_class.value
    elif failure_class not in {failure.value for failure in BackendFailure}:
        failure_class = None
    response_failure = getattr(event, "response_failure", None)
    if isinstance(response_failure, ContainerAppResponseFailure):
        response_failure = response_failure.value
    elif response_failure not in {
        reason.value for reason in ContainerAppResponseFailure
    }:
        response_failure = None
    http_status = getattr(event, "http_status", 0)
    if (
        isinstance(http_status, bool)
        or not isinstance(http_status, int)
        or not 100 <= http_status <= 599
    ):
        http_status = 0
    ownership_state = getattr(event, "ownership_state", None)
    observed_owner_digest = getattr(event, "observed_owner_digest", None)
    expected_owner_digest = getattr(event, "expected_owner_digest", None)
    envelope_digest = getattr(event, "envelope_digest", None)
    if not isinstance(observed_owner_digest, str) or not _DIGEST.fullmatch(
        observed_owner_digest
    ):
        observed_owner_digest = None
    if not isinstance(expected_owner_digest, str) or not _DIGEST.fullmatch(
        expected_owner_digest
    ):
        expected_owner_digest = None
    if not isinstance(envelope_digest, str) or not _DIGEST.fullmatch(envelope_digest):
        envelope_digest = None
    retention_digest = getattr(event, "retention_plan_digest", None)
    retention_seconds = getattr(event, "retention_seconds", 0)
    retention_hourly_cost = getattr(
        event,
        "retention_hourly_cost_microusd",
        0,
    )
    token_counts = tuple(
        getattr(event, name, 0)
        for name in ("input_tokens", "output_tokens", "total_tokens")
    )
    if (
        any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= 100_000_000
            for value in token_counts
        )
        or token_counts[0] + token_counts[1] != token_counts[2]
    ):
        token_counts = (0, 0, 0)
    gpu_maximum_percent = getattr(event, "gpu_maximum_percent", 0.0)
    gpu_positive_sample_count = getattr(event, "gpu_positive_sample_count", 0)
    if (
        isinstance(gpu_maximum_percent, bool)
        or not isinstance(gpu_maximum_percent, (int, float))
        or not math.isfinite(gpu_maximum_percent)
        or not 0 <= gpu_maximum_percent <= 100
        or isinstance(gpu_positive_sample_count, bool)
        or not isinstance(gpu_positive_sample_count, int)
        or not 0 <= gpu_positive_sample_count <= 10_000
        or (gpu_maximum_percent > 0) != (gpu_positive_sample_count > 0)
    ):
        gpu_maximum_percent = 0.0
        gpu_positive_sample_count = 0
    else:
        gpu_maximum_percent = float(gpu_maximum_percent)
    raw_gpu_attestation_source = getattr(event, "gpu_attestation_source", None)
    gpu_attestation_source = (
        raw_gpu_attestation_source.value
        if isinstance(raw_gpu_attestation_source, ContainerAppGPUAttestationSource)
        else None
    )
    gpu_runtime_counts = tuple(
        getattr(event, name, 0)
        for name in (
            "gpu_prompt_tokens",
            "gpu_generation_tokens",
            "gpu_successful_requests",
        )
    )
    gpu_estimated_flops_per_gpu = getattr(
        event,
        "gpu_estimated_flops_per_gpu",
        0.0,
    )
    runtime_evidence_valid = (
        gpu_attestation_source
        == ContainerAppGPUAttestationSource.STARTUP_CUDA_VLLM_METRICS.value
        and all(
            not isinstance(value, bool) and isinstance(value, int)
            for value in gpu_runtime_counts
        )
        and 1 <= gpu_runtime_counts[0] <= 100_000_000
        and 1 <= gpu_runtime_counts[1] <= 100_000_000
        and 1 <= gpu_runtime_counts[2] <= 10_000
        and not isinstance(gpu_estimated_flops_per_gpu, bool)
        and isinstance(gpu_estimated_flops_per_gpu, (int, float))
        and math.isfinite(gpu_estimated_flops_per_gpu)
        and 0 <= gpu_estimated_flops_per_gpu <= 1e30
    )
    if not runtime_evidence_valid:
        gpu_runtime_counts = (0, 0, 0)
        gpu_estimated_flops_per_gpu = 0.0
    else:
        gpu_estimated_flops_per_gpu = float(gpu_estimated_flops_per_gpu)
    event_source = getattr(event, "event_source", None)
    event_reason = getattr(event, "reason", None)
    preflight_reason = _safe_preflight_reason(event_reason)
    attestation_reason = _safe_gpu_attestation_reason(event_reason)
    allowed_fields = {
        "event_source": frozenset({"opentofu_ui", "azure_resource_manager"}),
        "resource_type": frozenset(
            {
                "azapi_resource",
                "microsoft.app/containerapps",
                "microsoft.app/managedenvironments",
            }
        ),
        "action": frozenset(
            {"noop", "create", "read", "update", "replace", "delete", "move"}
        ),
        "event_kind": frozenset(
            {
                "planned_change",
                "resource_drift",
                "apply_start",
                "apply_progress",
                "apply_complete",
                "apply_errored",
            }
        ),
        "provisioning_state": frozenset(
            {
                "succeeded",
                "failed",
                "canceled",
                "waiting",
                "in-progress",
                "provisioning",
                "deleting",
                "initialization-in-progress",
                "infrastructure-setup-in-progress",
                "infrastructure-setup-complete",
                "scheduled-for-delete",
                "upgrade-requested",
                "upgrade-failed",
            }
        ),
    }
    structured = ""
    if event_source in allowed_fields["event_source"]:
        values = {
            name: getattr(event, name, None)
            for name in (
                "event_source",
                "resource_type",
                "action",
                "event_kind",
                "provisioning_state",
            )
        }
        structured = " " + " ".join(
            f"{name}={value if value in allowed_fields[name] else 'none'}"
            for name, value in values.items()
        )
    progress_sink(
        "SELF_IMPROVE_AZURE_BOOTSTRAP "
        f"component={component} phase={phase} state={state} "
        f"operation_digest={digest or 'unbound'} elapsed_seconds={elapsed} "
        f"failure_class={failure_class or 'none'} "
        f"response_failure={response_failure or 'none'} "
        f"http_status={http_status or 'none'} "
        "ownership_state="
        f"{ownership_state if ownership_state in _OWNERSHIP_STATES else 'none'} "
        f"observed_owner_digest={observed_owner_digest or 'none'} "
        f"expected_owner_digest={expected_owner_digest or 'none'} "
        f"envelope_digest={envelope_digest or 'none'} "
        f"retention_plan_digest={retention_digest or 'none'} "
        f"retention_seconds={retention_seconds} "
        f"retention_hourly_cost_microusd={retention_hourly_cost} "
        f"input_tokens={token_counts[0]} output_tokens={token_counts[1]} "
        f"total_tokens={token_counts[2]} "
        f"gpu_maximum_percent={gpu_maximum_percent} "
        f"gpu_positive_sample_count={gpu_positive_sample_count} "
        f"gpu_attestation_source={gpu_attestation_source or 'none'} "
        f"gpu_prompt_tokens={gpu_runtime_counts[0]} "
        f"gpu_generation_tokens={gpu_runtime_counts[1]} "
        f"gpu_successful_requests={gpu_runtime_counts[2]} "
        f"gpu_estimated_flops_per_gpu={gpu_estimated_flops_per_gpu} "
        f"attestation_reason={attestation_reason or 'none'} "
        f"preflight_reason={preflight_reason or 'none'}{structured} "
        "secret_output=false"
    )


class ConfiguredAzureContainerAppBootstrapFactory:
    """Create a fresh complete owned lifecycle for every approved trial scope."""

    def __init__(
        self,
        *,
        app_policy: AzureContainerAppLiveProofPolicy,
        environment_policy: AzureEnvironmentLifecyclePolicy,
        requirement: ModelServingRequirement,
        work_root: Path,
        environment_work_root: Path,
        credential_provider: AzureCredentialProvider,
        resource_group_bootstrapper: Callable[..., object],
        resources_builder: Callable[..., AzureBootstrapRuntimeResources],
        owned_factory_type: type[Any],
        progress_sink: Callable[[str], None],
        idle_retention_policy: AzureIdleRetentionPolicy,
        expected_next_demand_seconds: int | None,
        resource_group_owner_digest: str,
        legacy_owner_digests: tuple[str, ...] = (),
        operational_evidence_store: CapabilityEvidenceStore | None = None,
    ) -> None:
        """Initialize immutable lifecycle inputs for fresh candidate sessions."""
        self._app_policy = app_policy
        self._environment_policy = environment_policy
        self._requirement = requirement
        self._work_root = work_root
        self._environment_work_root = environment_work_root
        self._credential_provider = credential_provider
        if not callable(resource_group_bootstrapper):
            raise ValueError("resource_group_bootstrapper must be callable")
        self._resource_group_bootstrapper = resource_group_bootstrapper
        self._resources_builder = resources_builder
        self._owned_factory_type = owned_factory_type
        self._progress_sink = progress_sink
        self._idle_retention_policy = idle_retention_policy
        self._expected_next_demand_seconds = expected_next_demand_seconds
        self._resource_group_owner_digest = resource_group_owner_digest
        self._legacy_owner_digests = legacy_owner_digests
        if operational_evidence_store is not None and not isinstance(
            operational_evidence_store, CapabilityEvidenceStore
        ):
            raise ValueError(
                "operational_evidence_store must be a CapabilityEvidenceStore"
            )
        self._operational_evidence_store = operational_evidence_store
        self._availability_scope = build_azure_availability_scope(
            location=app_policy.location,
            resource_sku=app_policy.workload_profile_type,
            container_image=app_policy.container_image,
            requirement=requirement,
        )
        self._deployment_digest = owned_candidate_deployment_digest(
            app_policy,
            environment_policy,
            idle_retention_policy,
            expected_next_demand_seconds,
        )

    @property
    def deployment_digest(self) -> str:
        """Return the immutable app-and-environment desired-state digest."""
        return self._deployment_digest

    def _record_operational_failure(
        self,
        failure: BackendFailure,
        phase: AzureInfrastructurePhase,
    ) -> None:
        """Persist only availability evidence that may safely change placement."""
        if (
            self._operational_evidence_store is None
            or failure not in _PROFILE_OPERATIONAL_FAILURES
        ):
            return
        try:
            record_azure_infrastructure_failure(
                self._operational_evidence_store,
                location=self._app_policy.location,
                workload_profile_type=self._app_policy.workload_profile_type,
                container_image=self._app_policy.container_image,
                deployment_identity_digest=self._deployment_digest,
                phase=phase,
                failure=failure,
            )
            record_azure_availability_terminal(
                self._operational_evidence_store,
                scope=self._availability_scope,
                deployment_identity_digest=self._deployment_digest,
                phase=phase,
                outcome=AzureAvailabilityTerminal(failure.value),
            )
        except Exception:
            self._progress_sink(
                "SELF_IMPROVE_AZURE_BOOTSTRAP phase=operational_evidence_failed "
                f"operation_digest={self._deployment_digest} secret_output=false"
            )
            raise BackendInfrastructureError(BackendFailure.INTERNAL) from None
        self._progress_sink(
            "SELF_IMPROVE_AZURE_BOOTSTRAP phase=operational_evidence_recorded "
            f"operation_digest={self._deployment_digest} "
            f"failure_class={failure.value} secret_output=false"
        )

    def _record_operational_success(self) -> None:
        """Persist a successful terminal startup so later failures can heal."""
        if self._operational_evidence_store is None:
            return
        try:
            record_azure_availability_terminal(
                self._operational_evidence_store,
                scope=self._availability_scope,
                deployment_identity_digest=self._deployment_digest,
                phase=AzureInfrastructurePhase.CANDIDATE_STARTUP,
                outcome=AzureAvailabilityTerminal.AVAILABLE,
            )
        except Exception:
            self._progress_sink(
                "SELF_IMPROVE_AZURE_BOOTSTRAP phase=operational_evidence_failed "
                f"operation_digest={self._deployment_digest} secret_output=false"
            )
            raise BackendInfrastructureError(BackendFailure.INTERNAL) from None
        self._progress_sink(
            "SELF_IMPROVE_AZURE_BOOTSTRAP phase=operational_evidence_recorded "
            f"operation_digest={self._deployment_digest} "
            "outcome=available secret_output=false"
        )

    def _ensure_resource_group(
        self,
        credentials: AzureAcceleratorAuthentication,
        release: Callable[[], None],
    ) -> None:
        """Acquire the owned resource group or release the credential lease."""
        try:
            self._resource_group_bootstrapper(
                AzureResourceGroupBootstrapPolicy(
                    subscription_id=self._app_policy.subscription_id,
                    resource_group=self._app_policy.resource_group,
                    location=self._app_policy.location,
                    owner_digest=self._resource_group_owner_digest,
                    legacy_owner_digests=self._legacy_owner_digests,
                ),
                credentials,
                trace_sink=lambda event: runtime_trace(
                    self._progress_sink,
                    "resource_group",
                    event,
                ),
            )
        except BackendInfrastructureError:
            with suppress(Exception):
                release()
            raise
        except AzureResourceGroupBootstrapError as error:
            with suppress(Exception):
                release()
            failure = _RESOURCE_GROUP_FAILURES.get(
                error.failure_class,
                BackendFailure.INTERNAL,
            )
            raise BackendInfrastructureError(failure) from None
        except BaseException:
            with suppress(Exception):
                release()
            raise BackendInfrastructureError(BackendFailure.INTERNAL) from None

    def __call__(self) -> ContainerAppCandidateBackend:
        """Acquire credentials and return one fully owned Azure backend."""
        self._progress_sink(
            "SELF_IMPROVE_AZURE_BOOTSTRAP phase=credential_acquire_started "
            f"operation_digest={self._deployment_digest} secret_output=false"
        )
        acquisition = self._credential_provider.acquire()
        if acquisition.credentials.subscription_id != self._app_policy.subscription_id:
            with suppress(Exception):
                acquisition.release()
            raise RuntimeError("Azure bootstrap credential scope mismatch")
        release = release_once(acquisition.release)
        preflight_failure: BackendFailure | None = None

        def preflight_trace(event: PreflightTrace) -> None:
            nonlocal preflight_failure
            runtime_trace(self._progress_sink, "preflight", event)
            if event.phase == "preflight_refused":
                preflight_failure = _preflight_backend_failure(event.reason)

        self._progress_sink(
            "SELF_IMPROVE_AZURE_BOOTSTRAP phase=credential_acquired "
            f"operation_digest={self._deployment_digest} secret_output=false"
        )
        self._ensure_resource_group(acquisition.credentials, release)
        try:
            resources = self._resources_builder(
                credentials=acquisition.credentials,
                policy=self._app_policy,
                requirement=self._requirement,
                work_root=self._work_root,
                environment_work_root=self._environment_work_root,
                credential_release=release,
                preflight_trace_sink=preflight_trace,
                terraform_trace_sink=lambda event: runtime_trace(
                    self._progress_sink,
                    "app_terraform",
                    event,
                ),
                environment_terraform_trace_sink=lambda event: runtime_trace(
                    self._progress_sink,
                    "environment_terraform",
                    event,
                ),
                backend_trace_sink=lambda event: runtime_trace(
                    self._progress_sink,
                    "backend",
                    event,
                ),
                progress_sink=lambda message: self._progress_sink(
                    "SELF_IMPROVE_AZURE_BOOTSTRAP component=arm "
                    f"{message} secret_output=false"
                ),
            )
        except BackendInfrastructureError as exc:
            with suppress(Exception):
                release()
            self._record_operational_failure(
                exc.failure,
                (
                    AzureInfrastructurePhase.PREFLIGHT
                    if preflight_failure is not None
                    else AzureInfrastructurePhase.CANDIDATE_STARTUP
                ),
            )
            raise
        except BaseException:
            with suppress(Exception):
                release()
            raise BackendInfrastructureError(BackendFailure.INTERNAL) from None

        def owned_trace(event: OwnedCandidateLifecycleTrace) -> None:
            runtime_trace(self._progress_sink, "owned_lifecycle", event)

        try:
            owner = self._owned_factory_type(
                app_policy=self._app_policy,
                environment_policy=self._environment_policy,
                environment_runtime=resources.environment_runtime,
                app_runtime=resources.runtime,
                backend_factory=resources.backend_factory,
                resource_release=resources.close,
                trace_sink=owned_trace,
                idle_retention_policy=self._idle_retention_policy,
                expected_next_demand_seconds=self._expected_next_demand_seconds,
            )
            if owner.deployment_digest != self._deployment_digest:
                resources.close()
                raise RuntimeError("Azure bootstrap configuration drift")
            backend = cast(ContainerAppCandidateBackend, owner())
            self._record_operational_success()
            return backend
        except BackendInfrastructureError as exc:
            with suppress(Exception):
                resources.close()
            self._record_operational_failure(
                exc.failure,
                AzureInfrastructurePhase.CANDIDATE_STARTUP,
            )
            raise
        except OwnedCandidateLifecycleError as exc:
            with suppress(Exception):
                resources.close()
            if exc.failure is not None:
                failure = exc.failure
            elif exc.operation == "preflight":
                failure = preflight_failure or BackendFailure.UNAVAILABLE
            else:
                failure = BackendFailure.INTERNAL
            self._record_operational_failure(
                failure,
                (
                    AzureInfrastructurePhase.PREFLIGHT
                    if exc.operation == "preflight"
                    else AzureInfrastructurePhase.CANDIDATE_STARTUP
                ),
            )
            raise BackendInfrastructureError(failure) from None
        except RuntimeError as exc:
            if str(exc) == "Azure bootstrap configuration drift":
                raise
            with suppress(Exception):
                resources.close()
            raise BackendInfrastructureError(BackendFailure.INTERNAL) from None
        except BaseException:
            with suppress(Exception):
                resources.close()
            raise BackendInfrastructureError(BackendFailure.INTERNAL) from None


__all__ = (
    "AzureBootstrapRuntimeResources",
    "ConfiguredAzureContainerAppBootstrapFactory",
    "runtime_trace",
)
