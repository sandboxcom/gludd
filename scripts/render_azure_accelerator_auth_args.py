#!/usr/bin/env python3
"""Render safe Azure CLI arguments for Gludd accelerator deployment access."""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from general_ludd.azure.accelerator_role import (
    ROLE_NAME,
    ROLE_TEMPLATE_PATH,
    materialize_accelerator_role,
)
from general_ludd.azure.accelerator_role import (
    validate_resource_group as _validate_resource_group,
)
from general_ludd.azure.accelerator_role import (
    validate_subscription_id as _validate_subscription,
)

ENVIRONMENT_TEMPLATE_CLI_PATH: Final = "config/infra/azure-containerapp-environment.json"
_PRINCIPAL_NAME_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()\-]{0,119}$")
_RESOURCE_NAME_RE: Final = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,62}[A-Za-z0-9_]?$"
)
_LOCATION_RE: Final = re.compile(r"^[a-z][a-z0-9]{1,31}$")
_GPU_PROFILE_TYPES: Final = frozenset(
    {"Consumption-GPU-NC8as-T4", "Consumption-GPU-NC24-A100"}
)


def _validate_principal_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or _PRINCIPAL_NAME_RE.fullmatch(value) is None
        or value != value.strip()
    ):
        raise ValueError("invalid service principal name")
    return value


def _validate_resource_name(value: object, label: str) -> str:
    if not isinstance(value, str) or _RESOURCE_NAME_RE.fullmatch(value) is None:
        raise ValueError(f"invalid {label}")
    return value


def _validate_location(value: object) -> str:
    if not isinstance(value, str) or _LOCATION_RE.fullmatch(value) is None:
        raise ValueError("invalid location")
    return value


def _validate_workload_profile_type(value: object) -> str:
    if not isinstance(value, str) or value not in _GPU_PROFILE_TYPES:
        raise ValueError("invalid workload profile type")
    return value


def _encode(arguments: tuple[str, ...]) -> bytes:
    return b"\0".join(argument.encode("utf-8") for argument in arguments) + b"\0"


def build_role_arguments(
    *,
    subscription_id: str,
    resource_group: str,
    template_path: Path = ROLE_TEMPLATE_PATH,
) -> tuple[str, ...]:
    """Build one Azure CLI argv that creates the accelerator custom role."""
    subscription_id = _validate_subscription(subscription_id)
    resource_group = _validate_resource_group(resource_group)
    role_definition = json.dumps(
        materialize_accelerator_role(
            template_path=template_path,
            subscription_id=subscription_id,
            resource_group=resource_group,
        ),
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        "role",
        "definition",
        "create",
        "--role-definition",
        role_definition,
        "--subscription",
        subscription_id,
        "--only-show-errors",
        "--output",
        "json",
    )


def build_role_update_arguments(
    *,
    subscription_id: str,
    resource_group: str,
    template_path: Path = ROLE_TEMPLATE_PATH,
) -> tuple[str, ...]:
    """Build one Azure CLI argv that narrows an existing accelerator role."""
    create_arguments = build_role_arguments(
        subscription_id=subscription_id,
        resource_group=resource_group,
        template_path=template_path,
    )
    return (*create_arguments[:2], "update", *create_arguments[3:])


def build_auth_arguments(
    *,
    subscription_id: str,
    resource_group: str,
    service_principal_name: str,
) -> tuple[str, ...]:
    """Build one Azure CLI argv that creates and assigns a deployer identity."""
    subscription_id = _validate_subscription(subscription_id)
    resource_group = _validate_resource_group(resource_group)
    service_principal_name = _validate_principal_name(service_principal_name)
    scope = f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
    return (
        "ad",
        "sp",
        "create-for-rbac",
        "--name",
        service_principal_name,
        "--role",
        ROLE_NAME,
        "--scopes",
        scope,
        "--json-auth",
        "true",
        "--only-show-errors",
        "--output",
        "json",
    )


def build_environment_bootstrap_arguments(
    *,
    subscription_id: str,
    resource_group: str,
    environment_name: str,
    workload_profile_name: str,
    workload_profile_type: str,
    location: str,
) -> tuple[str, ...]:
    """Build one operator-owned incremental deployment for the shared environment."""
    subscription_id = _validate_subscription(subscription_id)
    resource_group = _validate_resource_group(resource_group)
    environment_name = _validate_resource_name(environment_name, "environment name")
    workload_profile_name = _validate_resource_name(
        workload_profile_name,
        "workload profile name",
    )
    workload_profile_type = _validate_workload_profile_type(workload_profile_type)
    location = _validate_location(location)
    return (
        "deployment",
        "group",
        "create",
        "--subscription",
        subscription_id,
        "--resource-group",
        resource_group,
        "--name",
        "gludd-containerapp-environment-bootstrap",
        "--mode",
        "Incremental",
        "--template-file",
        ENVIRONMENT_TEMPLATE_CLI_PATH,
        "--parameters",
        f"location={location}",
        f"environmentName={environment_name}",
        f"workloadProfileName={workload_profile_name}",
        f"workloadProfileType={workload_profile_type}",
        "--output",
        "json",
    )


def render_role_arguments(
    *,
    subscription_id: str,
    resource_group: str,
    template_path: Path = ROLE_TEMPLATE_PATH,
) -> bytes:
    """Encode the role-creation argv for one ``xargs -0 az`` invocation."""
    return _encode(
        build_role_arguments(
            subscription_id=subscription_id,
            resource_group=resource_group,
            template_path=template_path,
        )
    )


def render_auth_arguments(
    *,
    subscription_id: str,
    resource_group: str,
    service_principal_name: str,
) -> bytes:
    """Encode the identity-creation argv for one ``xargs -0 az`` invocation."""
    return _encode(
        build_auth_arguments(
            subscription_id=subscription_id,
            resource_group=resource_group,
            service_principal_name=service_principal_name,
        )
    )


def render_role_update_arguments(
    *,
    subscription_id: str,
    resource_group: str,
    template_path: Path = ROLE_TEMPLATE_PATH,
) -> bytes:
    """Encode the role-update argv for one ``xargs -0 az`` invocation."""
    return _encode(
        build_role_update_arguments(
            subscription_id=subscription_id,
            resource_group=resource_group,
            template_path=template_path,
        )
    )


def render_environment_bootstrap_arguments(
    *,
    subscription_id: str,
    resource_group: str,
    environment_name: str,
    workload_profile_name: str,
    workload_profile_type: str,
    location: str,
) -> bytes:
    """Encode one operator-owned shared-environment deployment argv."""
    return _encode(
        build_environment_bootstrap_arguments(
            subscription_id=subscription_id,
            resource_group=resource_group,
            environment_name=environment_name,
            workload_profile_name=workload_profile_name,
            workload_profile_type=workload_profile_type,
            location=location,
        )
    )


def _required_environment(internal_name: str, public_name: str) -> str:
    value = os.environ.get(internal_name)
    if not value:
        raise ValueError(f"{public_name} is required")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    """Write only the selected validated argument stream to stdout."""
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        if raw == ["role"]:
            payload = render_role_arguments(
                subscription_id=_required_environment(
                    "_GLUDD_AZURE_ACCELERATOR_SUBSCRIPTION_ID_RAW",
                    "AZURE_ACCELERATOR_SUBSCRIPTION_ID",
                ),
                resource_group=_required_environment(
                    "_GLUDD_AZURE_ACCELERATOR_RESOURCE_GROUP_RAW",
                    "AZURE_ACCELERATOR_RESOURCE_GROUP",
                ),
            )
        elif raw == ["role-update"]:
            payload = render_role_update_arguments(
                subscription_id=_required_environment(
                    "_GLUDD_AZURE_ACCELERATOR_SUBSCRIPTION_ID_RAW",
                    "AZURE_ACCELERATOR_SUBSCRIPTION_ID",
                ),
                resource_group=_required_environment(
                    "_GLUDD_AZURE_ACCELERATOR_RESOURCE_GROUP_RAW",
                    "AZURE_ACCELERATOR_RESOURCE_GROUP",
                ),
            )
        elif raw == ["auth"]:
            payload = render_auth_arguments(
                subscription_id=_required_environment(
                    "_GLUDD_AZURE_ACCELERATOR_SUBSCRIPTION_ID_RAW",
                    "AZURE_ACCELERATOR_SUBSCRIPTION_ID",
                ),
                resource_group=_required_environment(
                    "_GLUDD_AZURE_ACCELERATOR_RESOURCE_GROUP_RAW",
                    "AZURE_ACCELERATOR_RESOURCE_GROUP",
                ),
                service_principal_name=_required_environment(
                    "_GLUDD_AZURE_ACCELERATOR_SP_NAME_RAW",
                    "AZURE_ACCELERATOR_SP_NAME",
                ),
            )
        elif raw == ["environment-bootstrap"]:
            payload = render_environment_bootstrap_arguments(
                subscription_id=_required_environment(
                    "_GLUDD_AZURE_ACCELERATOR_SUBSCRIPTION_ID_RAW",
                    "AZURE_ACCELERATOR_SUBSCRIPTION_ID",
                ),
                resource_group=_required_environment(
                    "_GLUDD_AZURE_ACCELERATOR_RESOURCE_GROUP_RAW",
                    "AZURE_ACCELERATOR_RESOURCE_GROUP",
                ),
                environment_name=_required_environment(
                    "_GLUDD_AZURE_CONTAINERAPP_ENVIRONMENT_RAW",
                    "AZURE_CONTAINERAPP_ENVIRONMENT",
                ),
                workload_profile_name=_required_environment(
                    "_GLUDD_AZURE_CONTAINERAPP_WORKLOAD_PROFILE_NAME_RAW",
                    "AZURE_CONTAINERAPP_WORKLOAD_PROFILE_NAME",
                ),
                workload_profile_type=_required_environment(
                    "_GLUDD_AZURE_CONTAINERAPP_WORKLOAD_PROFILE_TYPE_RAW",
                    "AZURE_CONTAINERAPP_WORKLOAD_PROFILE_TYPE",
                ),
                location=_required_environment(
                    "_GLUDD_AZURE_CONTAINERAPP_LOCATION_RAW",
                    "AZURE_CONTAINERAPP_LOCATION",
                ),
            )
        else:
            raise ValueError("invalid mode")
    except ValueError as exc:
        print(f"azure-accelerator-auth-args: {exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
