"""Concrete Azure clients and Terraform runtimes for one owned model candidate."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol, cast

from general_ludd.azure.accelerator_credentials import AzureAcceleratorCredentials
from general_ludd.infra.azure_containerapp_arm import (
    HttpxARMJSONTransport,
    HttpxContainerAppARMTransport,
    HttpxContainerAppEnvironmentLifecycleTransport,
)
from general_ludd.infra.azure_containerapp_environment_lifecycle import (
    AzureContainerAppEnvironmentRuntime,
    AzureEnvironmentLifecyclePolicy,
)
from general_ludd.infra.azure_containerapp_environment_make_runtime import (
    AzureContainerAppEnvironmentTerraformRuntime,
)
from general_ludd.infra.azure_containerapp_gpu import ModelServingRequirement
from general_ludd.infra.azure_containerapp_live_proof import (
    AzureContainerAppLiveProofPolicy,
    AzureContainerAppProofRuntime,
)
from general_ludd.infra.azure_containerapp_make_runtime import (
    AzureContainerAppTerraformRuntime,
    MakeRuntimeEvent,
)
from general_ludd.infra.azure_containerapp_preflight import (
    ARM_SCOPE,
    AzureContainerAppReadOnlyPreflight,
    PreflightTrace,
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

_AZURE_AUTHORITY = "login.microsoftonline.com"
_Backend = CandidateBackend[AzureApprovedPrompt, AzureCandidateResponse]


class _ClosableCredential(Protocol):
    def get_token(self, *scopes: str) -> Any: ...

    def close(self) -> None: ...


def _discard_preflight(_trace: PreflightTrace) -> None:
    return None


def _discard_terraform(_trace: MakeRuntimeEvent) -> None:
    return None


def _discard_backend(_trace: ContainerAppBackendTrace) -> None:
    return None


def _discard_progress(_message: str) -> None:
    return None


def _credential_client(credentials: AzureAcceleratorCredentials) -> _ClosableCredential:
    try:
        from azure.identity import ClientSecretCredential
    except ImportError:
        raise RuntimeError("Azure Identity dependency is unavailable") from None
    return cast(
        _ClosableCredential,
        ClientSecretCredential(
            tenant_id=credentials.tenant_id,
            client_id=credentials.client_id,
            client_secret=credentials.client_secret,
            authority=_AZURE_AUTHORITY,
            disable_instance_discovery=True,
            retry_total=0,
        ),
    )


def _ready(document: object | None) -> bool:
    if not isinstance(document, Mapping):
        return False
    properties = document.get("properties")
    return bool(
        isinstance(properties, Mapping)
        and properties.get("provisioningState") == "Succeeded"
        and isinstance(properties.get("latestReadyRevisionName"), str)
        and properties.get("latestReadyRevisionName")
    )


def _environment_ready(document: object | None) -> bool:
    if not isinstance(document, Mapping):
        return False
    properties = document.get("properties")
    return bool(
        isinstance(properties, Mapping)
        and properties.get("provisioningState") == "Succeeded"
    )


class AzureContainerAppRuntimeResources:
    """Own all clients used by a direct app/environment Terraform lifecycle."""

    def __init__(
        self,
        *,
        runtime: AzureContainerAppProofRuntime,
        environment_runtime: AzureContainerAppEnvironmentRuntime,
        credential: _ClosableCredential,
        environment_transport: HttpxARMJSONTransport,
        lifecycle_transport: HttpxContainerAppEnvironmentLifecycleTransport,
        app_transport: HttpxContainerAppARMTransport,
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


def build_azure_containerapp_runtime_resources(
    *,
    credentials: AzureAcceleratorCredentials,
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
    _credential_factory: Callable[[AzureAcceleratorCredentials], Any] | None = None,
    _environment_transport_factory: Callable[..., Any] | None = None,
    _lifecycle_transport_factory: Callable[..., Any] | None = None,
    _app_transport_factory: Callable[..., Any] | None = None,
    _preflight_factory: Callable[..., Any] | None = None,
    _app_runtime_factory: Callable[..., Any] | None = None,
    _environment_runtime_factory: Callable[..., Any] | None = None,
    _backend_factory: Callable[..., Any] | None = None,
) -> AzureContainerAppRuntimeResources:
    """Build an exact, secret-safe resource bundle for one approved deployment."""
    if not isinstance(credentials, AzureAcceleratorCredentials):
        raise ValueError("credentials must be AzureAcceleratorCredentials")
    if not isinstance(policy, AzureContainerAppLiveProofPolicy):
        raise ValueError("policy must be AzureContainerAppLiveProofPolicy")
    if credentials.subscription_id != policy.subscription_id:
        raise ValueError("credentials do not match the approved subscription")
    if not isinstance(requirement, ModelServingRequirement):
        raise ValueError("requirement must be ModelServingRequirement")
    callbacks = (
        preflight_trace_sink,
        terraform_trace_sink,
        environment_terraform_trace_sink,
        backend_trace_sink,
        progress_sink,
        monotonic,
        sleep,
    )
    if not all(callable(callback) for callback in callbacks) or (
        credential_release is not None and not callable(credential_release)
    ):
        raise ValueError("runtime resource callbacks must be callable")

    credential: _ClosableCredential | None = None
    environment_transport: HttpxARMJSONTransport | None = None
    lifecycle_transport: HttpxContainerAppEnvironmentLifecycleTransport | None = None
    app_transport: HttpxContainerAppARMTransport | None = None
    credential_factory = _credential_client if _credential_factory is None else _credential_factory
    environment_transport_factory = (
        HttpxARMJSONTransport
        if _environment_transport_factory is None
        else _environment_transport_factory
    )
    lifecycle_transport_factory = (
        HttpxContainerAppEnvironmentLifecycleTransport
        if _lifecycle_transport_factory is None
        else _lifecycle_transport_factory
    )
    app_transport_factory = (
        HttpxContainerAppARMTransport
        if _app_transport_factory is None
        else _app_transport_factory
    )
    preflight_factory = (
        AzureContainerAppReadOnlyPreflight
        if _preflight_factory is None
        else _preflight_factory
    )
    app_runtime_factory = (
        AzureContainerAppTerraformRuntime
        if _app_runtime_factory is None
        else _app_runtime_factory
    )
    environment_runtime_factory = (
        AzureContainerAppEnvironmentTerraformRuntime
        if _environment_runtime_factory is None
        else _environment_runtime_factory
    )
    backend_factory = (
        build_azure_containerapp_candidate_backend
        if _backend_factory is None
        else _backend_factory
    )
    try:
        credential = credential_factory(credentials)
        environment_transport = environment_transport_factory(
            subscription_id=policy.subscription_id,
            resource_group=policy.resource_group,
            environment_name=policy.environment_name,
        )
        lifecycle_transport = lifecycle_transport_factory(
            subscription_id=policy.subscription_id,
            resource_group=policy.resource_group,
            environment_name=policy.environment_name,
        )
        app_transport = app_transport_factory(
            subscription_id=policy.subscription_id,
            resource_group=policy.resource_group,
            app_name=policy.app_name,
        )

        def preflight(
            active_policy: AzureContainerAppLiveProofPolicy,
            active_requirement: ModelServingRequirement,
        ) -> None:
            preflight_factory(
                cast(Any, credential),
                cast(Any, environment_transport),
                trace_sink=preflight_trace_sink,
            ).check(
                subscription_id=active_policy.subscription_id,
                resource_group=active_policy.resource_group,
                environment_name=active_policy.environment_name,
                workload_profile_name=active_policy.workload_profile_name,
                location=active_policy.location,
                requirement=active_requirement,
            )

        def read_app(
            _active_policy: AzureContainerAppLiveProofPolicy,
            expect_absent: bool,
        ) -> object | None:
            deadline = monotonic() + (600.0 if expect_absent else 900.0)
            last_document: object | None = None
            while True:
                token = credential.get_token(ARM_SCOPE).token
                last_document = app_transport.get_json(token)
                if (expect_absent and last_document is None) or (
                    not expect_absent and _ready(last_document)
                ):
                    return last_document
                if monotonic() >= deadline:
                    return last_document
                progress_sink(
                    "azure_containerapp_poll phase="
                    f"{'absence' if expect_absent else 'readiness'} state=heartbeat"
                )
                sleep(10.0)

        def read_environment(
            active_policy: AzureEnvironmentLifecyclePolicy,
            expect_absent: bool,
        ) -> object | None:
            if active_policy.environment_id.casefold() != policy.environment_id.casefold():
                raise RuntimeError("environment policy escaped the bound resource")
            deadline = monotonic() + (900.0 if expect_absent else 1_200.0)
            last_document: object | None = None
            while True:
                token = credential.get_token(ARM_SCOPE).token
                last_document = lifecycle_transport.get_environment(token)
                if (expect_absent and last_document is None) or (
                    not expect_absent
                    and (last_document is None or _environment_ready(last_document))
                ):
                    return last_document
                if monotonic() >= deadline:
                    return last_document
                progress_sink(
                    "azure_containerapp_environment_poll phase="
                    f"{'absence' if expect_absent else 'readiness'} state=heartbeat"
                )
                sleep(10.0)

        def list_environment_apps(
            active_policy: AzureEnvironmentLifecyclePolicy,
        ) -> tuple[str, ...]:
            if active_policy.environment_id.casefold() != policy.environment_id.casefold():
                raise RuntimeError("environment policy escaped the bound resource")
            expected_app_id = policy.expected_resource_id.casefold()
            deadline = monotonic() + 300.0
            while True:
                token = credential.get_token(ARM_SCOPE).token
                app_ids = lifecycle_transport.list_environment_app_ids(token)
                if not app_ids or any(
                    app_id.casefold() != expected_app_id for app_id in app_ids
                ):
                    return app_ids
                if monotonic() >= deadline:
                    return app_ids
                progress_sink(
                    "azure_containerapp_environment_poll "
                    "phase=inventory state=heartbeat"
                )
                sleep(10.0)

        runtime = app_runtime_factory(
            work_root=work_root,
            credentials=credentials,
            requirement=requirement,
            preflight_check=preflight,
            read_app=read_app,
            trace_sink=terraform_trace_sink,
        )
        environment_runtime = environment_runtime_factory(
            work_root=environment_work_root,
            credentials=credentials,
            read_environment=read_environment,
            list_environment_apps=list_environment_apps,
            trace_sink=environment_terraform_trace_sink,
        )
        resources = AzureContainerAppRuntimeResources(
            runtime=runtime,
            environment_runtime=environment_runtime,
            credential=credential,
            environment_transport=environment_transport,
            lifecycle_transport=lifecycle_transport,
            app_transport=app_transport,
            credential_release=credential_release,
            backend_trace_sink=backend_trace_sink,
            backend_factory_builder=backend_factory,
        )
        return resources
    except BaseException:
        for client in (
            app_transport,
            lifecycle_transport,
            environment_transport,
            credential,
        ):
            if client is not None:
                with suppress(Exception):
                    client.close()
        if credential_release is not None:
            with suppress(Exception):
                credential_release()
        raise


__all__ = (
    "ARM_SCOPE",
    "AzureContainerAppRuntimeResources",
    "build_azure_containerapp_runtime_resources",
)
