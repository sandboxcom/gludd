"""Fail-closed local state adoption for one owned Azure environment."""

from __future__ import annotations

import stat
from collections.abc import Mapping
from pathlib import Path

from general_ludd.infra.azure_containerapp_environment_plan_contract import (
    ENVIRONMENT_API_TYPE,
)
from general_ludd.infra.azure_containerapp_environment_types import (
    AzureEnvironmentLifecyclePolicy,
)
from general_ludd.infra.azure_containerapp_make_types import (
    AzureContainerAppMakeRuntimeError,
)
from general_ludd.infra.azure_containerapp_make_validation import read_bounded_json

_RESOURCE_IDENTITY = (
    "module.environment",
    "managed",
    "azapi_resource",
    "managed_environment",
)


def environment_import_id(policy: AzureEnvironmentLifecyclePolicy) -> str:
    """Return the exact AzAPI import identity pinned by Gludd's module."""
    api_version = ENVIRONMENT_API_TYPE.rsplit("@", 1)[1]
    return f"{policy.environment_id}?api-version={api_version}"


def _state_resources(state_path: Path) -> list[object] | None:
    try:
        metadata = state_path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise AzureContainerAppMakeRuntimeError("state-ownership") from None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
        raise AzureContainerAppMakeRuntimeError("state-ownership")
    document = read_bounded_json(state_path, phase="state-ownership")
    resources = document.get("resources") if isinstance(document, Mapping) else None
    if resources == []:
        return []
    if not isinstance(resources, list) or len(resources) != 1:
        raise AzureContainerAppMakeRuntimeError("state-ownership")
    return resources


def _state_resource_id(resource: object) -> str:
    if not isinstance(resource, Mapping) or (
        resource.get("module"),
        resource.get("mode"),
        resource.get("type"),
        resource.get("name"),
    ) != _RESOURCE_IDENTITY:
        raise AzureContainerAppMakeRuntimeError("state-ownership")
    instances = resource.get("instances")
    if not isinstance(instances, list) or len(instances) != 1:
        raise AzureContainerAppMakeRuntimeError("state-ownership")
    instance = instances[0]
    attributes = instance.get("attributes") if isinstance(instance, Mapping) else None
    resource_id = attributes.get("id") if isinstance(attributes, Mapping) else None
    if not isinstance(resource_id, str):
        raise AzureContainerAppMakeRuntimeError("state-ownership")
    return resource_id


def state_tracks_environment(
    state_path: Path,
    policy: AzureEnvironmentLifecyclePolicy,
) -> bool:
    """Prove state is empty/missing or tracks only the exact managed environment."""
    resources = _state_resources(state_path)
    if not resources:
        return False
    stored_id = _state_resource_id(resources[0]).casefold()
    accepted_ids = {
        policy.environment_id.casefold(),
        environment_import_id(policy).casefold(),
    }
    if stored_id not in accepted_ids:
        raise AzureContainerAppMakeRuntimeError("state-ownership")
    return True


__all__ = ("environment_import_id", "state_tracks_environment")
