#!/usr/bin/python
"""Run a typed chemistry operation through the authenticated Gludd daemon."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    GluddClient,
    error_result,
)

_OPERATIONS = (
    "route",
    "identity",
    "reaction",
    "molar_mass",
    "moles",
    "dilution",
    "yield",
    "hazard",
)


def _stable_key(operation: str, request: dict[str, Any]) -> str:
    encoded = json.dumps(
        {"operation": operation, "request": request},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return f"chemistry:{hashlib.sha256(encoded).hexdigest()}"


def run(module: Any) -> None:
    """Execute the module contract against one authenticated daemon client."""
    operation: str = module.params["operation"]
    request: dict[str, Any] = module.params["request"]
    timeout: int = module.params["timeout"]
    if timeout < 1 or timeout > 30:
        module.fail_json(**error_result("timeout must be between 1 and 30 seconds"))
        return
    if module.check_mode:
        module.exit_json(changed=False, result={}, operation=operation)
        return

    client = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=timeout,
    )
    response = client.post(
        "/api/chemistry/resolve",
        {
            "operation": operation,
            "request": request,
            "timeout_seconds": float(min(timeout, 30)),
            "idempotency_key": module.params["idempotency_key"]
            or _stable_key(operation, request),
        },
    )
    status = response.get("_status", 0)
    if response.get("_error") or status not in (200, 201):
        detail = response.get("detail") or response.get("_error") or f"HTTP {status}"
        module.fail_json(
            **error_result(
                f"chemistry operation failed: {detail}",
                status=status,
            )
        )
        return
    result = {key: value for key, value in response.items() if not key.startswith("_")}
    module.exit_json(changed=False, result=result, operation=operation)


def main() -> None:
    """Create the Ansible argument contract and execute it."""
    module = AnsibleModule(
        argument_spec={
            "operation": {
                "type": "str",
                "required": True,
                "choices": list(_OPERATIONS),
            },
            "request": {"type": "dict", "required": True},
            "daemon_url": {"type": "str", "default": "http://localhost:8000"},
            "psk": {"type": "str", "default": "", "no_log": True},
            "timeout": {"type": "int", "default": 30},
            "idempotency_key": {"type": "str", "default": ""},
        },
        supports_check_mode=True,
    )
    run(module)


if __name__ == "__main__":
    main()
