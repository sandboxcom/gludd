"""Concrete Azure clients and Terraform runtimes for one owned model candidate."""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from general_ludd.azure.accelerator_credentials import (
    AzureAcceleratorAuthentication,
    AzureAcceleratorCredentials,
    AzureAcceleratorWorkloadIdentity,
)
from general_ludd.infra.azure_containerapp_environment_lifecycle import (
    AzureContainerAppEnvironmentRuntime,
)
from general_ludd.infra.azure_containerapp_gpu import ModelServingRequirement
from general_ludd.infra.azure_containerapp_live_proof import (
    AzureContainerAppLiveProofPolicy,
    AzureContainerAppProofRuntime,
)
from general_ludd.infra.azure_containerapp_make_runtime import (
    MakeRuntimeEvent,
)
from general_ludd.infra.azure_containerapp_preflight import (
    ARM_SCOPE,
    PreflightTrace,
)
from general_ludd.infra.azure_containerapp_runtime_factories import (
    AzureContainerAppRuntimeFactories,
    resolve_azure_containerapp_runtime_factories,
)
from general_ludd.infra.azure_containerapp_runtime_factories import (
    _credential_client as _credential_client,
)
from general_ludd.infra.azure_containerapp_runtime_readers import (
    AzureContainerAppRuntimeReaders,
)
from general_ludd.infra.azure_containerapp_runtime_state import (
    _app_provisioning_state as _app_provisioning_state,
)
from general_ludd.infra.azure_containerapp_runtime_state import (
    _environment_ready as _environment_ready,
)
from general_ludd.infra.azure_containerapp_runtime_state import _ready as _ready
from general_ludd.infra.azure_containerapp_runtime_state import (
    _revision_state as _revision_state,
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
    CandidateBackend,
)

_Backend = CandidateBackend[AzureApprovedPrompt, AzureCandidateResponse]


class _ClosableCredential(Protocol):
    def get_token(self, *scopes: str) -> Any: ...

    def close(self) -> None: ...


class _ClosableClient(Protocol):
    def close(self) -> None: ...


def _discard_preflight(_trace: PreflightTrace) -> None:
    return None


def _discard_terraform(_trace: MakeRuntimeEvent) -> None:
    return None


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
        self._closed = False

    def backend_factory(
        self,
        identity: AzureContainerAppCandidateIdentity,
    ) -> _Backend:
        """Bind only the exact deployed revision through the bounded backend."""
        return self._backend_factory_builder(
            identity,
            discovery_timeout_seconds=120.0,
            trace_sink=self._backend_trace_sink,
        )

    def close(self) -> None:
        """Close transports and identity before revoking the optional lease."""
        if self._closed:
            return
        self._closed = True
        failed = False
        for client in (
            self._app_transport,
            self._lifecycle_transport,
            self._environment_transport,
            self._credential,
        ):
            try:
                client.close()
            except Exception:
                failed = True
        if self._credential_release is not None:
            try:
                self._credential_release()
            except Exception:
                failed = True
        if failed:
            raise RuntimeError("Azure live resource cleanup failed")


@dataclass(frozen=True, slots=True)
class _OpenReadTransports:
    environment: Any
    lifecycle: Any
    app: Any

    def close_quietly(self) -> None:
        for client in (self.app, self.lifecycle, self.environment):
            with suppress(Exception):
                client.close()


def _open_read_transports(
    *,
    credential: _ClosableCredential,
    policy: AzureContainerAppLiveProofPolicy,
    environment_factory: Callable[..., Any] | None,
    lifecycle_factory: Callable[..., Any] | None,
    app_factory: Callable[..., Any] | None,
    factories: AzureContainerAppRuntimeFactories,
) -> _OpenReadTransports:
    environment: Any | None = None
    lifecycle: Any | None = None
    app: Any | None = None
    sdk_client: Any | None = None
    legacy = (environment_factory, lifecycle_factory, app_factory)
    try:
        if any(factory is not None for factory in legacy):
            if not all(factory is not None for factory in legacy):
                raise ValueError("all legacy Azure read transports must be supplied")
            environment = cast(Callable[..., Any], environment_factory)(
                subscription_id=policy.subscription_id,
                resource_group=policy.resource_group,
                environment_name=policy.environment_name,
            )
            lifecycle = cast(Callable[..., Any], lifecycle_factory)(
                subscription_id=policy.subscription_id,
                resource_group=policy.resource_group,
                environment_name=policy.environment_name,
            )
            app = cast(Callable[..., Any], app_factory)(
                subscription_id=policy.subscription_id,
                resource_group=policy.resource_group,
                app_name=policy.app_name,
            )
        else:
            sdk_client = factories.sdk_client(credential, policy.subscription_id)
            views = factories.sdk_transports(client=sdk_client, policy=policy)
            environment, lifecycle, app = views.preflight, views.lifecycle, views.app
            sdk_client = None
        return _OpenReadTransports(environment, lifecycle, app)
    except BaseException:
        for client in (app, lifecycle, environment, sdk_client):
            if client is not None:
                with suppress(Exception):
                    client.close()
        raise


def _validate_runtime_build(
    credentials: AzureAcceleratorAuthentication,
    policy: AzureContainerAppLiveProofPolicy,
    requirement: ModelServingRequirement,
    callbacks: tuple[Callable[..., object], ...],
    credential_release: Callable[[], None] | None,
) -> None:
    if not isinstance(
        credentials,
        (AzureAcceleratorCredentials, AzureAcceleratorWorkloadIdentity),
    ):
        raise ValueError("credentials must use the Azure accelerator contract")
    if not isinstance(policy, AzureContainerAppLiveProofPolicy):
        raise ValueError("policy must be AzureContainerAppLiveProofPolicy")
    if credentials.subscription_id != policy.subscription_id:
        raise ValueError("credentials do not match the approved subscription")
    if not isinstance(requirement, ModelServingRequirement):
        raise ValueError("requirement must be ModelServingRequirement")
    if not all(callable(callback) for callback in callbacks) or (
        credential_release is not None and not callable(credential_release)
    ):
        raise ValueError("runtime resource callbacks must be callable")


def _assemble_runtime_resources(
    *,
    credential: _ClosableCredential,
    transports: _OpenReadTransports,
    credentials: AzureAcceleratorAuthentication,
    policy: AzureContainerAppLiveProofPolicy,
    requirement: ModelServingRequirement,
    work_root: str | Path,
    environment_work_root: str | Path,
    credential_release: Callable[[], None] | None,
    factories: AzureContainerAppRuntimeFactories,
    preflight_trace_sink: Callable[[PreflightTrace], None],
    terraform_trace_sink: Callable[[MakeRuntimeEvent], None],
    environment_terraform_trace_sink: Callable[[MakeRuntimeEvent], None],
    backend_trace_sink: Callable[[ContainerAppBackendTrace], None],
    progress_sink: Callable[[str], None],
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> AzureContainerAppRuntimeResources:
    readers = AzureContainerAppRuntimeReaders(
        credential=credential,
        environment_transport=transports.environment,
        lifecycle_transport=transports.lifecycle,
        app_transport=transports.app,
        policy=policy,
        preflight_factory=factories.preflight,
        preflight_trace_sink=preflight_trace_sink,
        progress_sink=progress_sink,
        monotonic=monotonic,
        sleep=sleep,
    )
    runtime = factories.app_runtime(
        work_root=work_root,
        credentials=credentials,
        requirement=requirement,
        preflight_check=readers.preflight,
        read_app=readers.read_app,
        trace_sink=terraform_trace_sink,
    )
    environment_runtime = factories.environment_runtime(
        work_root=environment_work_root,
        credentials=credentials,
        read_environment=readers.read_environment,
        list_environment_apps=readers.list_environment_apps,
        trace_sink=environment_terraform_trace_sink,
    )
    return AzureContainerAppRuntimeResources(
        runtime=runtime,
        environment_runtime=environment_runtime,
        credential=credential,
        environment_transport=transports.environment,
        lifecycle_transport=transports.lifecycle,
        app_transport=transports.app,
        credential_release=credential_release,
        backend_trace_sink=backend_trace_sink,
        backend_factory_builder=factories.backend,
    )


def build_azure_containerapp_runtime_resources(
    *,
    credentials: AzureAcceleratorAuthentication,
    policy: AzureContainerAppLiveProofPolicy,
    requirement: ModelServingRequirement,
    work_root: str | Path,
    environment_work_root: str | Path,
    credential_release: Callable[[], None] | None = None,
    preflight_trace_sink: Callable[[PreflightTrace], None] = _discard_preflight,
    terraform_trace_sink: Callable[[MakeRuntimeEvent], None] = _discard_terraform,
    environment_terraform_trace_sink: Callable[
        [MakeRuntimeEvent], None
    ] = _discard_terraform,
    backend_trace_sink: Callable[[ContainerAppBackendTrace], None] = _discard_backend,
    progress_sink: Callable[[str], None] = _discard_progress,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    _credential_factory: Callable[[AzureAcceleratorAuthentication], Any] | None = None,
    _environment_transport_factory: Callable[..., Any] | None = None,
    _lifecycle_transport_factory: Callable[..., Any] | None = None,
    _app_transport_factory: Callable[..., Any] | None = None,
    _preflight_factory: Callable[..., Any] | None = None,
    _app_runtime_factory: Callable[..., Any] | None = None,
    _environment_runtime_factory: Callable[..., Any] | None = None,
    _backend_factory: Callable[..., Any] | None = None,
    _sdk_client_factory: Callable[..., Any] | None = None,
    _sdk_transports_factory: Callable[..., Any] | None = None,
) -> AzureContainerAppRuntimeResources:
    """Build an exact, secret-safe resource bundle for one approved deployment."""
    _validate_runtime_build(
        credentials,
        policy,
        requirement,
        (
            preflight_trace_sink,
            terraform_trace_sink,
            environment_terraform_trace_sink,
            backend_trace_sink,
            progress_sink,
            monotonic,
            sleep,
        ),
        credential_release,
    )
    factories = resolve_azure_containerapp_runtime_factories(
        credential=_credential_factory,
        preflight=_preflight_factory,
        app_runtime=_app_runtime_factory,
        environment_runtime=_environment_runtime_factory,
        backend=_backend_factory,
        sdk_client=_sdk_client_factory,
        sdk_transports=_sdk_transports_factory,
    )
    credential: _ClosableCredential | None = None
    transports: _OpenReadTransports | None = None
    try:
        credential = factories.credential(credentials)
        transports = _open_read_transports(
            credential=credential,
            policy=policy,
            environment_factory=_environment_transport_factory,
            lifecycle_factory=_lifecycle_transport_factory,
            app_factory=_app_transport_factory,
            factories=factories,
        )
        return _assemble_runtime_resources(
            credential=credential,
            transports=transports,
            credentials=credentials,
            policy=policy,
            requirement=requirement,
            work_root=work_root,
            environment_work_root=environment_work_root,
            credential_release=credential_release,
            factories=factories,
            preflight_trace_sink=preflight_trace_sink,
            terraform_trace_sink=terraform_trace_sink,
            environment_terraform_trace_sink=environment_terraform_trace_sink,
            backend_trace_sink=backend_trace_sink,
            progress_sink=progress_sink,
            monotonic=monotonic,
            sleep=sleep,
        )
    except BaseException:
        if transports is not None:
            transports.close_quietly()
        if credential is not None:
            with suppress(Exception):
                credential.close()
        if credential_release is not None:
            with suppress(Exception):
                credential_release()
        raise


__all__ = (
    "ARM_SCOPE",
    "AzureContainerAppRuntimeResources",
    "build_azure_containerapp_runtime_resources",
)
