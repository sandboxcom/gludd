"""
Capability dispatch module_utils for general_ludd.agent.

Thin stdlib-only wrapper over the daemon's authenticated capability routes.
Collection processes never import the core router or scan the source checkout.

Usage in a module::

    from ansible_collections.general_ludd.agent.plugins.module_utils.capability_router import (
        dispatch,
        list_capabilities,
        register_capability,
        CapabilityDispatchError,
    )

    result = dispatch("agentic", {"task": "build"})
"""

from __future__ import annotations

from typing import Any

from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import GluddClient

# ---------------------------------------------------------------------------
# Public constants (kept for backward compatibility)
# ---------------------------------------------------------------------------
DEFAULT_DAEMON_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 30
DISPATCH_ENDPOINT = "/api/dispatch"
DISPATCH_AVAILABLE_ENDPOINT = "/api/dispatch/available"

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class CapabilityDispatchError(Exception):
    """Raised when a capability dispatch fails (routing, auth, timeout)."""


# ---------------------------------------------------------------------------
# Process-local capability registry (register_capability consumers)
# ---------------------------------------------------------------------------

_registry: dict[str, dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def dispatch(
    capability: str,
    payload: dict[str, Any] | None = None,
    *,
    daemon_url: str = DEFAULT_DAEMON_URL,
    psk: str = "",
    timeout: int = DEFAULT_TIMEOUT,
    model_profile: str | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    """Route a capability through the daemon's shared registry."""
    if not capability or not isinstance(capability, str):
        raise CapabilityDispatchError("capability must be a non-empty string")

    if payload is None:
        payload = {}

    client = GluddClient(base_url=daemon_url, psk=psk, timeout=timeout)
    response = client.post(
        "/api/dispatch/capability",
        {"capability": capability, "payload": payload},
    )
    if response.get("_error") or response.get("_status") != 200:
        return {"results": [], "count": 0, "ok_count": 0, "error_count": 1}
    matches = response.get("matches")
    raw_matches = matches if isinstance(matches, list) else []
    results = [
        {
            "ok": True,
            "capability": response.get("capability", capability),
            "collection": match.get("collection", ""),
            "score": match.get("score", 0.0),
        }
        for match in raw_matches
        if isinstance(match, dict)
    ]
    return {
        "results": results,
        "count": len(results),
        "ok_count": len(results),
        "error_count": 0 if response.get("ok") else 1,
    }


def list_capabilities(
    daemon_url: str = DEFAULT_DAEMON_URL,
    psk: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> list[str]:
    """List daemon capabilities plus compatibility-local registrations."""
    capabilities: set[str] = set(_registry.keys())
    response = GluddClient(base_url=daemon_url, psk=psk, timeout=timeout).get(
        "/api/dispatch/capabilities"
    )
    remote = response.get("capabilities")
    if isinstance(remote, list):
        capabilities.update(str(item) for item in remote)

    return sorted(capabilities)


def register_capability(
    name: str,
    roles: list[str] | None = None,
    model_needs: dict[str, Any] | None = None,
    *,
    daemon_url: str = DEFAULT_DAEMON_URL,
    psk: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Register a capability in the process-local registry.

    ``daemon_url``, ``psk``, and ``timeout`` are retained for backward
    compatibility but are no longer consumed — registration is now local.
    """
    if not name or not isinstance(name, str):
        raise CapabilityDispatchError("capability name must be a non-empty string")

    if roles is None:
        roles = []
    if model_needs is None:
        model_needs = {}

    _registry[name] = {
        "name": name,
        "roles": list(roles),
        "model_needs": dict(model_needs),
    }

    return {
        "name": name,
        "roles": list(roles),
        "model_needs": dict(model_needs),
        "registered": True,
    }


def get_registry() -> dict[str, dict[str, Any]]:
    """Return a copy of the process-local capability registry."""
    return dict(_registry)


def clear_registry() -> None:
    """Clear the process-local capability registry (test helper)."""
    _registry.clear()
