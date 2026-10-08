"""Observable, read-only Azure Container Apps GPU capacity preflight."""

from __future__ import annotations

from collections.abc import Callable
from typing import NoReturn

from general_ludd.infra.azure_containerapp_arm import (
    ENVIRONMENT_PREFLIGHT_API_VERSION,
    AzureContainerAppARMError,
)
from general_ludd.infra.azure_containerapp_gpu import (
    AzureContainerAppGPUProfile,
    AzureContainerAppGPUUnavailable,
    GPUProfileSelection,
    ModelServingRequirement,
    select_smallest_sufficient_profile,
)
from general_ludd.infra.azure_containerapp_preflight_parsing import (
    count,
    parse_environment,
    parse_workload_profile_state,
    provider_name,
    quota_for_profile,
    quota_number,
    validate_path_inputs,
)
from general_ludd.infra.azure_containerapp_preflight_types import (
    ARM_SCOPE,
    AccessToken,
    ARMJSONTransport,
    AzureContainerAppEvidenceError,
    AzureContainerAppPreflightError,
    AzureContainerAppPreflightResult,
    ContainerAppUsage,
    PreflightTrace,
    TokenCredential,
    _EnvironmentEvidence,
    _WorkloadProfileState,
)
from general_ludd.infra.azure_containerapp_preflight_usage import (
    read_supplementary_usages,
)

_count = count
_parse_environment = parse_environment
_parse_workload_profile_state = parse_workload_profile_state
_provider_name = provider_name
_quota_for_profile = quota_for_profile
_quota_number = quota_number
_validate_path_inputs = validate_path_inputs


def _discard_trace(_trace: PreflightTrace) -> None:
    return None


def _emit(sink: Callable[[PreflightTrace], None], trace: PreflightTrace) -> None:
    try:
        sink(trace)
    except Exception:
        raise RuntimeError("preflight trace publication failed") from None


class AzureContainerAppReadOnlyPreflight:
    """Read one named environment and quota before any paid Azure mutation."""

    def __init__(
        self,
        credential: TokenCredential,
        transport: ARMJSONTransport,
        *,
        trace_sink: Callable[[PreflightTrace], None] | None = None,
    ) -> None:
        """Bind read-only credential, ARM transport, and trace boundaries."""
        if not callable(getattr(credential, "get_token", None)):
            raise ValueError("credential must provide get_token")
        if not callable(getattr(transport, "get_json", None)):
            raise ValueError("transport must provide get_json")
        self._credential = credential
        self._transport = transport
        self._trace_sink = _discard_trace if trace_sink is None else trace_sink
        if not callable(self._trace_sink):
            raise ValueError("trace_sink must be callable")

    def _refuse(self, location: str, reason: str, message: str) -> NoReturn:
        _emit(
            self._trace_sink,
            PreflightTrace("preflight_refused", location, reason=reason),
        )
        raise AzureContainerAppPreflightError(message)

    def _read(
        self,
        *,
        path: str,
        token: str,
        phase: str,
        location: str,
    ) -> object:
        try:
            return self._transport.get_json(path, token)
        except AzureContainerAppARMError as exc:
            if exc.status_code in {401, 403}:
                self._refuse(
                    location,
                    f"{phase}_unauthorized",
                    f"Azure Container Apps {phase} read is not authorized",
                )
            if exc.status_code == 404:
                self._refuse(
                    location,
                    f"{phase}_not_found",
                    f"Azure Container Apps {phase} does not exist",
                )
            if (
                isinstance(exc.status_code, int)
                and 100 <= exc.status_code <= 599
            ):
                self._refuse(
                    location,
                    f"{phase}_http_{exc.status_code}",
                    f"Azure Container Apps {phase} read failed",
                )
            self._refuse(
                location,
                f"{phase}_read_failed",
                f"Azure Container Apps {phase} read failed",
            )
        except Exception:
            self._refuse(
                location,
                f"{phase}_read_failed",
                f"Azure Container Apps {phase} read failed",
            )
        raise AssertionError("unreachable")

    def _parse_or_refuse(
        self,
        location: str,
        parser: Callable[[], _EnvironmentEvidence | tuple[_WorkloadProfileState, int]],
    ) -> _EnvironmentEvidence | tuple[_WorkloadProfileState, int]:
        try:
            return parser()
        except AzureContainerAppEvidenceError as exc:
            self._refuse(location, exc.reason, str(exc))

    def _authenticate(self, location: str) -> str:
        """Acquire one ARM token while emitting only content-free state."""
        _emit(self._trace_sink, PreflightTrace("authentication_started", location))
        try:
            token = self._credential.get_token(ARM_SCOPE).token
        except Exception:
            self._refuse(
                location,
                "authentication_failed",
                "Azure Container Apps authentication failed",
            )
        _emit(self._trace_sink, PreflightTrace("authentication_succeeded", location))
        return token

    def _environment_evidence(
        self,
        root: str,
        token: str,
        location: str,
        environment_name: str,
        workload_profile_name: str,
        selection: GPUProfileSelection,
    ) -> _EnvironmentEvidence:
        """Read and bind the exact named environment and configured profile."""
        payload = self._read(
            path=f"{root}?api-version={ENVIRONMENT_PREFLIGHT_API_VERSION}",
            token=token,
            phase="environment",
            location=location,
        )
        parsed = self._parse_or_refuse(
            location,
            lambda: _parse_environment(
                payload,
                expected_resource_id=root,
                expected_environment_name=environment_name,
                expected_location=location,
                expected_profile_name=workload_profile_name,
                expected_profile_type=selection.profile.workload_profile_type,
            ),
        )
        assert isinstance(parsed, _EnvironmentEvidence)
        _emit(
            self._trace_sink,
            PreflightTrace(
                "environment_discovered",
                location,
                profile_name=selection.profile.name,
                record_count=len(parsed.available_profile_types),
                record_names=parsed.available_profile_types,
            ),
        )
        return parsed

    def _usage_evidence(
        self,
        root: str,
        token: str,
        location: str,
        selection: GPUProfileSelection,
    ) -> tuple[ContainerAppUsage, ...]:
        """Read the bounded environment-level usage evidence."""
        return read_supplementary_usages(
            transport=self._transport,
            root=root,
            token=token,
            location=location,
            selection=selection,
            emit=lambda trace: _emit(self._trace_sink, trace),
            refuse=self._refuse,
        )

    def _state_evidence(
        self,
        root: str,
        token: str,
        location: str,
        workload_profile_name: str,
        selection: GPUProfileSelection,
    ) -> _WorkloadProfileState | None:
        """Read the exact workload-profile state and publish its count."""
        payload = self._read(
            path=(
                f"{root}/workloadProfileStates"
                f"?api-version={ENVIRONMENT_PREFLIGHT_API_VERSION}"
            ),
            token=token,
            phase="workload_profile_states",
            location=location,
        )
        try:
            parsed = _parse_workload_profile_state(payload, workload_profile_name)
        except AzureContainerAppEvidenceError as exc:
            if exc.reason not in {
                "workload_profile_state_incomplete",
                "workload_profile_state_missing",
            }:
                self._refuse(location, exc.reason, str(exc))
            _emit(
                self._trace_sink,
                PreflightTrace(
                    "supplementary_profile_state_unavailable",
                    location,
                    profile_name=selection.profile.name,
                    reason=exc.reason,
                ),
            )
            return None
        state, state_count = parsed
        _emit(
            self._trace_sink,
            PreflightTrace(
                "workload_profile_state_discovered",
                location,
                profile_name=selection.profile.name,
                record_count=state_count,
                record_names=(selection.profile.workload_profile_type,),
            ),
        )
        return state

    def _remaining_quota(
        self,
        location: str,
        selection: GPUProfileSelection,
        usages: tuple[ContainerAppUsage, ...],
        state: _WorkloadProfileState | None,
    ) -> tuple[float | None, str | None]:
        """Intersect available quota evidence without inventing absent values."""
        environment_quota = _quota_for_profile(selection.profile, usages)
        remaining_values: list[float] = []
        quota_name: str | None = None
        if state is not None:
            remaining_values.append(float(state.remaining))
            quota_name = state.name
        if environment_quota is not None:
            remaining_values.append(environment_quota.remaining)
            if quota_name is None:
                quota_name = environment_quota.name
        if not remaining_values:
            _emit(
                self._trace_sink,
                PreflightTrace(
                    "quota_verification_deferred",
                    location,
                    profile_name=selection.profile.name,
                    reason="provider_evidence_unavailable",
                ),
            )
            return None, None
        remaining = min(remaining_values)
        if remaining < 1:
            self._refuse(
                location,
                "gpu_quota_exhausted",
                "Azure Container Apps GPU quota is exhausted",
            )
        return remaining, quota_name

    def check(
        self,
        *,
        subscription_id: str,
        resource_group: str,
        environment_name: str,
        workload_profile_name: str,
        location: str,
        requirement: ModelServingRequirement,
        hardware_profiles: tuple[AzureContainerAppGPUProfile, ...] | None = None,
    ) -> AzureContainerAppPreflightResult:
        """Return immutable readiness evidence or fail closed before deployment."""
        _validate_path_inputs(
            subscription_id,
            resource_group,
            environment_name,
            workload_profile_name,
            location,
        )
        try:
            selection = select_smallest_sufficient_profile(
                requirement,
                hardware_profiles=hardware_profiles,
            )
        except AzureContainerAppGPUUnavailable as exc:
            self._refuse(location, "profile_unavailable", str(exc))

        token = self._authenticate(location)
        root = (
            f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}/"
            f"providers/Microsoft.App/managedEnvironments/{environment_name}"
        )
        environment = self._environment_evidence(
            root,
            token,
            location,
            environment_name,
            workload_profile_name,
            selection,
        )
        usages = self._usage_evidence(root, token, location, selection)
        state = self._state_evidence(
            root,
            token,
            location,
            workload_profile_name,
            selection,
        )
        quota_remaining, quota_name = self._remaining_quota(
            location,
            selection,
            usages,
            state,
        )

        _emit(
            self._trace_sink,
            PreflightTrace(
                "preflight_ready",
                location,
                profile_name=selection.profile.name,
            ),
        )
        return AzureContainerAppPreflightResult(
            ready=True,
            location=location,
            profile=selection.profile,
            required_vram_mib=selection.required_vram_mib,
            available_profile_types=environment.available_profile_types,
            quota_scope="environment" if quota_remaining is not None else "deployment",
            quota_verified=quota_remaining is not None,
            quota_name=quota_name,
            quota_remaining=quota_remaining,
        )


__all__ = [
    "ARM_SCOPE",
    "ARMJSONTransport",
    "AccessToken",
    "AzureContainerAppPreflightError",
    "AzureContainerAppPreflightResult",
    "AzureContainerAppReadOnlyPreflight",
    "ContainerAppUsage",
    "PreflightTrace",
    "TokenCredential",
]
