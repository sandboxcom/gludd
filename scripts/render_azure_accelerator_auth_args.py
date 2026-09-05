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

ROLE_NAME: Final = "General Ludd Accelerator Deployer"
ROLE_TEMPLATE_PATH: Final = (
    Path(__file__).resolve().parents[1] / "config" / "infra" / "azure-iam-policy.json"
)
_ROLE_SCOPE_TEMPLATE: Final = "/subscriptions/{subscription_id}"
_UUID_RE: Final = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_PRINCIPAL_NAME_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()\-]{0,119}$")


def _validate_subscription(value: object) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError("invalid subscription ID")
    return value


def _validate_principal_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or _PRINCIPAL_NAME_RE.fullmatch(value) is None
        or value != value.strip()
    ):
        raise ValueError("invalid service principal name")
    return value


def _materialize_role(template_path: Path, subscription_id: str) -> str:
    try:
        raw = json.loads(template_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid accelerator role template") from exc
    if not isinstance(raw, dict):
        raise ValueError("invalid accelerator role template")
    actions = raw.get("Actions")
    if (
        raw.get("Name") != ROLE_NAME
        or raw.get("AssignableScopes") != [_ROLE_SCOPE_TEMPLATE]
        or raw.get("DataActions") != []
        or not isinstance(actions, list)
        or not all(isinstance(action, str) for action in actions)
        or any("Microsoft.CognitiveServices/" in action for action in actions)
    ):
        raise ValueError("invalid accelerator role template")
    role = dict(raw)
    role["AssignableScopes"] = [f"/subscriptions/{subscription_id}"]
    return json.dumps(role, sort_keys=True, separators=(",", ":"))


def _encode(arguments: tuple[str, ...]) -> bytes:
    return b"\0".join(argument.encode("utf-8") for argument in arguments) + b"\0"


def build_role_arguments(
    *,
    subscription_id: str,
    template_path: Path = ROLE_TEMPLATE_PATH,
) -> tuple[str, ...]:
    """Build one Azure CLI argv that creates the accelerator custom role."""
    subscription_id = _validate_subscription(subscription_id)
    role_definition = _materialize_role(template_path, subscription_id)
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


def build_auth_arguments(
    *,
    subscription_id: str,
    service_principal_name: str,
) -> tuple[str, ...]:
    """Build one Azure CLI argv that creates and assigns a deployer identity."""
    subscription_id = _validate_subscription(subscription_id)
    service_principal_name = _validate_principal_name(service_principal_name)
    scope = f"/subscriptions/{subscription_id}"
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
        "--subscription",
        subscription_id,
        "--only-show-errors",
        "--output",
        "json",
    )


def render_role_arguments(
    *,
    subscription_id: str,
    template_path: Path = ROLE_TEMPLATE_PATH,
) -> bytes:
    """Encode the role-creation argv for one ``xargs -0 az`` invocation."""
    return _encode(
        build_role_arguments(
            subscription_id=subscription_id,
            template_path=template_path,
        )
    )


def render_auth_arguments(
    *,
    subscription_id: str,
    service_principal_name: str,
) -> bytes:
    """Encode the identity-creation argv for one ``xargs -0 az`` invocation."""
    return _encode(
        build_auth_arguments(
            subscription_id=subscription_id,
            service_principal_name=service_principal_name,
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
                )
            )
        elif raw == ["auth"]:
            payload = render_auth_arguments(
                subscription_id=_required_environment(
                    "_GLUDD_AZURE_ACCELERATOR_SUBSCRIPTION_ID_RAW",
                    "AZURE_ACCELERATOR_SUBSCRIPTION_ID",
                ),
                service_principal_name=_required_environment(
                    "_GLUDD_AZURE_ACCELERATOR_SP_NAME_RAW",
                    "AZURE_ACCELERATOR_SP_NAME",
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
