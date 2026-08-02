"""
Capability dispatch module_utils for general_ludd.agent.

Thin wrapper delegating to general_ludd.dispatch modules:
- CapabilityRegistry, discover_capabilities  (capabilities.py)
- CapabilityRouter, RouteResult              (router.py)
- DynamicDispatcher                          (dynamic_dispatcher.py)

Stdlib-only so it runs inside Ansible module execution without
third-party deps (the delegated dispatch modules handle their own imports).

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

import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Ensure general_ludd package is importable when running inside Ansible
# ---------------------------------------------------------------------------
_SRC = Path(__file__).resolve().parents[6] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

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
# Lazy router singleton — built once per process
# ---------------------------------------------------------------------------

_router: Any = None
_router_init: bool = False


def _build() -> Any:
    """Create a CapabilityRouter backed by an empty CapabilityRegistry.

    Returns the router on success, or None if general_ludd.dispatch is not
    importable (no router → graceful fallback in list/register).
    """
    global _router, _router_init
    if _router_init:
        return _router
    _router_init = True
    try:
        from general_ludd.dispatch.capabilities import CapabilityRegistry
        from general_ludd.dispatch.router import CapabilityRouter

        registry = CapabilityRegistry()
        _router = CapabilityRouter(registry)
    except Exception:
        _router = None
    return _router


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
    """Route a capability through CapabilityRouter.route().

    The ``daemon_url``, ``psk``, ``timeout``, ``model_profile``, and ``role``
    keyword-only arguments are retained for backward compatibility but are no
    longer consumed — routing is now in-process via ``CapabilityRouter``.
    """
    if not capability or not isinstance(capability, str):
        raise CapabilityDispatchError("capability must be a non-empty string")

    if payload is None:
        payload = {}

    router = _build()
    if router is None:
        return {"results": [], "count": 0, "ok_count": 0, "error_count": 1}

    result = router.route(capability, payload)
    results: list[dict[str, Any]] = []
    ok_count = 0
    error_count = 0

    if result.ok:
        for m in result.matches:
            results.append(
                {
                    "ok": True,
                    "capability": result.capability,
                    "collection": m.name,
                    "score": m.score,
                }
            )
            ok_count += 1
    else:
        error_count = 1

    return {
        "results": results,
        "count": len(results),
        "ok_count": ok_count,
        "error_count": error_count,
    }


def list_capabilities(
    daemon_url: str = DEFAULT_DAEMON_URL,
    psk: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> list[str]:
    """List known capabilities from the registry and router.

    Merges process-local entries (via ``register_capability``) with
    capabilities discovered by the router.
    """
    capabilities: set[str] = set(_registry.keys())

    router = _build()
    if router is not None:
        try:
            for cap in router.list_capabilities():
                capabilities.add(cap)
        except Exception:
            pass

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
