"""Own assembled Azure Container App clients and attested inference backends."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import Any, Protocol, cast

from general_ludd.infra.azure_containerapp_environment_lifecycle import (
    AzureContainerAppEnvironmentRuntime,
)
from general_ludd.infra.azure_containerapp_gpu_backend import (
    _ClosableBackend,
    _GPUAttestedBackend,
    _GPUAttestor,
)
from general_ludd.infra.azure_containerapp_live_proof import (
    AzureContainerAppLiveProofPolicy,
    AzureContainerAppProofRuntime,
)
from general_ludd.infra.azure_containerapp_sdk import (
    AzureContainerAppGPUUtilizationAttestor,
    build_monitor_sdk_client,
)
from general_ludd.self_improve.azure_backend import (
    AzureApprovedPrompt,
    AzureCandidateResponse,
)
from general_ludd.self_improve.azure_containerapp_backend import (
    ContainerAppBackendTrace,
    build_azure_containerapp_candidate_backend,
)
from general_ludd.self_improve.model_candidates import (
    AzureContainerAppCandidateIdentity,
    BackendFailure,
    BackendInfrastructureError,
    CandidateBackend,
)

_Backend = CandidateBackend[AzureApprovedPrompt, AzureCandidateResponse]


class _ClosableCredential(Protocol):
    def get_token(self, *scopes: str) -> Any: ...

    def close(self) -> None: ...


class _ClosableClient(Protocol):
    def close(self) -> None: ...


def _discard_backend(_trace: ContainerAppBackendTrace) -> None:
    return None


def _discard_progress(_message: str) -> None:
    return None


class AzureContainerAppRuntimeResources:
    """Own all clients used by a direct app/environment Terraform lifecycle."""

    def __init__(
        self,
        *,
        runtime: AzureContainerAppProofRuntime,
        environment_runtime: AzureContainerAppEnvironmentRuntime,
        credential: _ClosableCredential,
        environment_transport: _ClosableClient,
        lifecycle_transport: _ClosableClient,
        app_transport: _ClosableClient,
        credential_release: Callable[[], None] | None,
        backend_trace_sink: Callable[
            [ContainerAppBackendTrace], None
        ] = _discard_backend,
        backend_factory_builder: Callable[..., _Backend] = (
            build_azure_containerapp_candidate_backend
        ),
        policy: AzureContainerAppLiveProofPolicy | None = None,
        monitor_client_factory: Callable[..., Any] = build_monitor_sdk_client,
        gpu_attestor_factory: Callable[..., Any] = (
            AzureContainerAppGPUUtilizationAttestor
        ),
        progress_sink: Callable[[str], None] = _discard_progress,
    ) -> None:
        """Initialize every client and release hook under one owner."""
        self.runtime = runtime
        self.environment_runtime = environment_runtime
        self._credential = credential
        self._environment_transport = environment_transport
        self._lifecycle_transport = lifecycle_transport
        self._app_transport = app_transport
        self._credential_release = credential_release
        self._backend_trace_sink = backend_trace_sink
        self._backend_factory_builder = backend_factory_builder
        self._policy = policy
        self._monitor_client_factory = monitor_client_factory
        self._gpu_attestor_factory = gpu_attestor_factory
        self._progress_sink = progress_sink
        self._backends: list[_GPUAttestedBackend] = []
        self._gpu_attestors: list[_GPUAttestor] = []
        self._closed = False

    def backend_factory(
        self,
        identity: AzureContainerAppCandidateIdentity,
    ) -> _Backend:
        """Bind inference and lazy exact-revision GPU attestation as one backend."""
        policy = self._policy
        if self._closed:
            raise BackendInfrastructureError(BackendFailure.UNAVAILABLE)
        if policy is None or (
            identity.resource_id.casefold() != policy.expected_resource_id.casefold()
        ):
            raise BackendInfrastructureError(BackendFailure.INVALID_RESPONSE)
        delegate: _ClosableBackend | None = None
        monitor: _ClosableClient | None = None
        attestor: _GPUAttestor | None = None
        try:
            candidate_delegate = self._backend_factory_builder(
                identity,
                discovery_timeout_seconds=120.0,
                trace_sink=self._backend_trace_sink,
            )
            if not callable(getattr(candidate_delegate, "close", None)):
                raise TypeError
            delegate = cast(_ClosableBackend, candidate_delegate)
            candidate_monitor = self._monitor_client_factory(
                self._credential,
                policy.subscription_id,
            )
            if not callable(getattr(candidate_monitor, "close", None)):
                raise TypeError
            monitor = cast(_ClosableClient, candidate_monitor)
            candidate_attestor = self._gpu_attestor_factory(
                client=monitor,
                expected_resource_id=policy.expected_resource_id,
                progress_sink=self._progress_sink,
            )
            if not all(
                callable(getattr(candidate_attestor, member, None))
                for member in ("attest", "close")
            ):
                raise TypeError
            attestor = cast(_GPUAttestor, candidate_attestor)
            backend = _GPUAttestedBackend(
                delegate,
                attestor,
                self._backend_trace_sink,
            )
        except Exception as error:
            if attestor is not None:
                with suppress(Exception):
                    attestor.close()
            elif monitor is not None:
                with suppress(Exception):
                    monitor.close()
            if delegate is not None:
                with suppress(Exception):
                    delegate.close()
            if isinstance(error, BackendInfrastructureError):
                raise
            raise BackendInfrastructureError(BackendFailure.INTERNAL) from None
        self._backends.append(backend)
        self._gpu_attestors.append(attestor)
        return cast(_Backend, backend)

    def close(self) -> None:
        """Close transports and identity before revoking the optional lease."""
        if self._closed:
            return
        self._closed = True
        failed = False
        for client in (
            *reversed(self._backends),
            *reversed(self._gpu_attestors),
            self._app_transport,
            self._lifecycle_transport,
            self._environment_transport,
            self._credential,
        ):
            try:
                cast(_ClosableClient, client).close()
            except Exception:
                failed = True
        if self._credential_release is not None:
            try:
                self._credential_release()
            except Exception:
                failed = True
        if failed:
            raise RuntimeError("Azure live resource cleanup failed")


__all__ = (
    "AzureContainerAppRuntimeResources",
    "_ClosableCredential",
    "_discard_backend",
    "_discard_progress",
)
