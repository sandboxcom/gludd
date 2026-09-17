"""Read optional environment usage evidence without widening preflight authority."""

from __future__ import annotations

from collections.abc import Callable
from typing import NoReturn

from general_ludd.infra.azure_containerapp_arm import (
    ENVIRONMENT_PREFLIGHT_API_VERSION,
    AzureContainerAppARMError,
)
from general_ludd.infra.azure_containerapp_gpu import GPUProfileSelection
from general_ludd.infra.azure_containerapp_preflight_parsing import parse_usages
from general_ludd.infra.azure_containerapp_preflight_types import (
    ARMJSONTransport,
    AzureContainerAppPreflightError,
    ContainerAppUsage,
    PreflightTrace,
)


def read_supplementary_usages(
    *,
    transport: ARMJSONTransport,
    root: str,
    token: str,
    location: str,
    selection: GPUProfileSelection,
    emit: Callable[[PreflightTrace], None],
    refuse: Callable[[str, str, str], NoReturn],
) -> tuple[ContainerAppUsage, ...]:
    """Return bounded usage records, tolerating only unavailable evidence."""

    def unavailable(reason: str) -> tuple[ContainerAppUsage, ...]:
        emit(
            PreflightTrace(
                "supplementary_usage_unavailable",
                location,
                profile_name=selection.profile.name,
                reason=reason,
            )
        )
        return ()

    try:
        payload = transport.get_json(
            f"{root}/usages?api-version={ENVIRONMENT_PREFLIGHT_API_VERSION}",
            token,
        )
    except AzureContainerAppARMError as exc:
        if isinstance(exc.status_code, int) and 500 <= exc.status_code <= 599:
            return unavailable(f"usages_http_{exc.status_code}")
        if exc.status_code in {401, 403}:
            refuse(
                location,
                "usages_unauthorized",
                "Azure Container Apps usages read is not authorized",
            )
        if exc.status_code == 404:
            refuse(
                location,
                "usages_not_found",
                "Azure Container Apps usages does not exist",
            )
        if isinstance(exc.status_code, int) and 100 <= exc.status_code <= 599:
            refuse(
                location,
                f"usages_http_{exc.status_code}",
                "Azure Container Apps usages read failed",
            )
        return unavailable("usages_read_failed")
    except Exception:
        return unavailable("usages_read_failed")
    try:
        usages = parse_usages(payload)
    except AzureContainerAppPreflightError:
        refuse(
            location,
            "usage_response_invalid",
            "Azure Container Apps usage response is invalid",
        )
    emit(
        PreflightTrace(
            "quota_discovered",
            location,
            profile_name=selection.profile.name,
            record_count=len(usages),
            record_names=tuple(usage.name for usage in usages),
        )
    )
    return usages


__all__ = ("read_supplementary_usages",)
