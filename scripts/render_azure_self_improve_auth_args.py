#!/usr/bin/env python3
"""Render validated NUL-delimited arguments for one Azure CLI invocation."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Sequence
from typing import Final

_UUID_RE: Final = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_RESOURCE_GROUP_RE: Final = re.compile(r"^[A-Za-z0-9_.()\-]{1,90}$")
_ACCOUNT_RE: Final = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
_PRINCIPAL_NAME_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()\-]{0,119}$")


def _validate_subscription(value: object) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError("invalid subscription ID")
    return value


def _validate_resource_group(value: object) -> str:
    if (
        not isinstance(value, str)
        or _RESOURCE_GROUP_RE.fullmatch(value) is None
        or value.endswith(".")
    ):
        raise ValueError("invalid resource group")
    return value


def _validate_account(value: object) -> str:
    if not isinstance(value, str) or _ACCOUNT_RE.fullmatch(value) is None:
        raise ValueError("invalid account")
    return value


def _validate_principal_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or _PRINCIPAL_NAME_RE.fullmatch(value) is None
        or value != value.strip()
    ):
        raise ValueError("invalid service principal name")
    return value


def build_arguments(
    *,
    subscription_id: str,
    resource_group: str,
    account: str,
    service_principal_name: str,
) -> tuple[str, ...]:
    """Build the only supported Azure CLI argument vector."""
    subscription_id = _validate_subscription(subscription_id)
    resource_group = _validate_resource_group(resource_group)
    account = _validate_account(account)
    service_principal_name = _validate_principal_name(service_principal_name)
    scope = (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account}"
    )
    role_name = f"Gludd Azure OpenAI Self Improvement - {account}"
    return (
        "ad",
        "sp",
        "create-for-rbac",
        "--name",
        service_principal_name,
        "--role",
        role_name,
        "--scopes",
        scope,
        "--json-auth",
        "true",
        "--subscription",
        subscription_id,
        "--only-show-errors",
        "--output",
        "json",
    )


def render_arguments(
    *,
    subscription_id: str,
    resource_group: str,
    account: str,
    service_principal_name: str,
) -> bytes:
    """Encode arguments for ``xargs -0`` without shell interpretation."""
    arguments = build_arguments(
        subscription_id=subscription_id,
        resource_group=resource_group,
        account=account,
        service_principal_name=service_principal_name,
    )
    return b"\0".join(argument.encode("utf-8") for argument in arguments) + b"\0"


def _parse(argv: Sequence[str] | None) -> dict[str, str]:
    raw = list(sys.argv[1:] if argv is None else argv)
    fields = {
        "--subscription-id": "subscription_id",
        "--resource-group": "resource_group",
        "--account": "account",
        "--service-principal-name": "service_principal_name",
    }
    if not raw:
        environment_fields = {
            "_GLUDD_AZURE_SELF_IMPROVE_SUBSCRIPTION_ID_RAW": (
                "subscription_id",
                "AZURE_SELF_IMPROVE_SUBSCRIPTION_ID",
            ),
            "_GLUDD_AZURE_SELF_IMPROVE_RESOURCE_GROUP_RAW": (
                "resource_group",
                "AZURE_SELF_IMPROVE_RESOURCE_GROUP",
            ),
            "_GLUDD_AZURE_SELF_IMPROVE_ACCOUNT_RAW": (
                "account",
                "AZURE_SELF_IMPROVE_ACCOUNT",
            ),
            "_GLUDD_AZURE_SELF_IMPROVE_SP_NAME_RAW": (
                "service_principal_name",
                "AZURE_SELF_IMPROVE_SP_NAME",
            ),
        }
        parsed_from_environment: dict[str, str] = {}
        for environment_name, (field, public_name) in environment_fields.items():
            value = os.environ.get(environment_name)
            if not value:
                raise ValueError(f"{public_name} is required")
            parsed_from_environment[field] = value
        return parsed_from_environment
    if len(raw) != len(fields) * 2:
        raise ValueError("invalid arguments")
    parsed: dict[str, str] = {}
    for index in range(0, len(raw), 2):
        parsed_field = fields.get(raw[index])
        if parsed_field is None or parsed_field in parsed:
            raise ValueError("invalid arguments")
        parsed[parsed_field] = raw[index + 1]
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    """Write only the validated argument stream to stdout."""
    try:
        payload = render_arguments(**_parse(argv))
    except ValueError as exc:
        print(f"azure-self-improve-auth-args: {exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
