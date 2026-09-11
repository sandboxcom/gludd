"""Dependency factories for Azure Container Apps managed runtimes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from general_ludd.azure.accelerator_credentials import (
    AzureAcceleratorAuthentication,
    build_azure_management_credential,
)
from general_ludd.infra.azure_containerapp_environment_make_runtime import (
    AzureContainerAppEnvironmentTerraformRuntime,
)
from general_ludd.infra.azure_containerapp_make_runtime import (
    AzureContainerAppTerraformRuntime,
)
from general_ludd.infra.azure_containerapp_preflight import (
    AzureContainerAppReadOnlyPreflight,
)
from general_ludd.infra.azure_containerapp_sdk import (
    AzureContainerAppsSDKReadTransports,
    build_container_apps_sdk_client,
)
from general_ludd.self_improve.azure_containerapp_backend import (
    build_azure_containerapp_candidate_backend,
)


def _credential_client(credentials: AzureAcceleratorAuthentication) -> Any:
    return build_azure_management_credential(credentials)


@dataclass(frozen=True, slots=True)
class AzureContainerAppRuntimeFactories:
    """Resolved production or injected constructors for one resource bundle."""

    credential: Callable[[AzureAcceleratorAuthentication], Any]
    preflight: Callable[..., Any]
    app_runtime: Callable[..., Any]
    environment_runtime: Callable[..., Any]
    backend: Callable[..., Any]
    sdk_client: Callable[..., Any]
    sdk_transports: Callable[..., Any]


def resolve_azure_containerapp_runtime_factories(
    *,
    credential: Callable[[AzureAcceleratorAuthentication], Any] | None,
    preflight: Callable[..., Any] | None,
    app_runtime: Callable[..., Any] | None,
    environment_runtime: Callable[..., Any] | None,
    backend: Callable[..., Any] | None,
    sdk_client: Callable[..., Any] | None,
    sdk_transports: Callable[..., Any] | None,
) -> AzureContainerAppRuntimeFactories:
    """Choose explicit test seams or the official production SDK adapters."""
    return AzureContainerAppRuntimeFactories(
        credential=_credential_client if credential is None else credential,
        preflight=AzureContainerAppReadOnlyPreflight if preflight is None else preflight,
        app_runtime=AzureContainerAppTerraformRuntime if app_runtime is None else app_runtime,
        environment_runtime=(
            AzureContainerAppEnvironmentTerraformRuntime
            if environment_runtime is None
            else environment_runtime
        ),
        backend=(
            build_azure_containerapp_candidate_backend if backend is None else backend
        ),
        sdk_client=(
            build_container_apps_sdk_client if sdk_client is None else sdk_client
        ),
        sdk_transports=(
            AzureContainerAppsSDKReadTransports
            if sdk_transports is None
            else sdk_transports
        ),
    )


__all__ = (
    "AzureContainerAppRuntimeFactories",
    "resolve_azure_containerapp_runtime_factories",
)
