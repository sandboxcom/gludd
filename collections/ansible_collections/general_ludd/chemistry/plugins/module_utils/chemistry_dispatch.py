"""Compatibility CLI for the collection's authenticated chemistry seam."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from typing import Any

from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import GluddClient

_OPERATIONS = frozenset(
    {
        "route",
        "identity",
        "reaction",
        "molar_mass",
        "moles",
        "dilution",
        "yield",
        "hazard",
    }
)


def _payload(raw: str) -> dict[str, Any]:
    decoded = json.loads(raw) if raw.strip() else {}
    if not isinstance(decoded, dict):
        raise ValueError("CHEMISTRY_INPUT must be a JSON object")
    return decoded


def _key(operation: str, request: dict[str, Any]) -> str:
    encoded = json.dumps(
        {"operation": operation, "request": request},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return f"chemistry:{hashlib.sha256(encoded).hexdigest()}"


def main() -> int:
    """Execute the legacy environment-based contract over HTTP."""
    operation = os.environ.get("CHEMISTRY_ACTION", "route")
    if operation not in _OPERATIONS:
        print(
            json.dumps(
                {
                    "status": "refused",
                    "errors": [
                        {
                            "code": "chem.unknown_action",
                            "message": f"unknown action {operation!r}",
                        }
                    ],
                }
            )
        )
        return 2
    try:
        request = _payload(os.environ.get("CHEMISTRY_INPUT", "{}"))
    except (json.JSONDecodeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "errors": [{"code": "chem.bad_json", "message": str(exc)}],
                }
            )
        )
        return 2

    timeout = min(max(int(os.environ.get("GLUDD_TIMEOUT", "30")), 1), 30)
    client = GluddClient(
        base_url=os.environ.get("GLUDD_DAEMON_URL", "http://localhost:8000"),
        psk=os.environ.get("GLUDD_PSK", ""),
        timeout=timeout,
    )
    response = client.post(
        "/api/chemistry/resolve",
        {
            "operation": operation,
            "request": request,
            "timeout_seconds": float(timeout),
            "idempotency_key": _key(operation, request),
        },
    )
    status = response.get("_status", 0)
    if response.get("_error") or status not in (200, 201):
        print(
            json.dumps(
                {
                    "status": "failed",
                    "errors": [
                        {
                            "code": "chem.daemon_error",
                            "message": str(
                                response.get("detail")
                                or response.get("_error")
                                or f"HTTP {status}"
                            ),
                        }
                    ],
                }
            )
        )
        return 1
    print(
        json.dumps(
            {
                key: value
                for key, value in response.items()
                if not key.startswith("_")
            },
            default=str,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
