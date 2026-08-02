"""
Capability dispatch module_utils for general_ludd.agent.

Routes capability requests through the daemon HTTP API, finds and invokes
the best matching collection/role handler, and manages capability registration.

Stdlib-only so it runs inside Ansible module execution without third-party deps.

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

import json
import urllib.error
import urllib.request
from typing import Any

DEFAULT_DAEMON_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 30
DISPATCH_ENDPOINT = "/api/dispatch"
DISPATCH_AVAILABLE_ENDPOINT = "/api/dispatch/available"


class CapabilityDispatchError(Exception):
    """Raised when a capability dispatch fails (routing, auth, timeout)."""


def _headers(psk: str) -> dict[str, str]:
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if psk:
        headers["Authorization"] = "Bearer " + psk
        headers["X-PSK"] = psk
    return headers


def _send(
    url: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    psk: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_headers(psk), method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        status = exc.code
    except urllib.error.URLError as exc:
        return {"_error": str(exc.reason), "_status": 0, "_raw": ""}
    except OSError as exc:
        return {"_error": str(exc), "_status": 0, "_raw": ""}

    try:
        parsed: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"_raw": raw}
    parsed["_status"] = status
    return parsed


def _url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


_registry: dict[str, dict[str, Any]] = {}


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
    """Route a capability request through the daemon to the best matching handler.

    Sends ``capability`` as the tool-call name to ``POST /api/dispatch`` with
    ``kind`` set to ``"collection"``. The daemon's ``DynamicDispatcher`` routes
    it to the collection handler registered for that capability tag.

    Parameters
    ----------
    capability:
        Capability tag to dispatch (must match a ``galaxy.yml`` tag).
    payload:
        Arbitrary payload forwarded to the matching collection's handler.
    daemon_url:
        Base URL of the daemon (default ``http://localhost:8000``).
    psk:
        Pre-shared key for daemon auth.  Treated as a secret.
    timeout:
        Per-request timeout in seconds.
    model_profile:
        Optional model profile hint for model-backed capabilities.
    role:
        Acting role for capability lattice gating.

    Returns
    -------
    dict
        Daemon dispatch response with keys ``results``, ``count``,
        ``ok_count``, ``error_count``.

    Raises
    ------
    CapabilityDispatchError
        If the daemon is unreachable, authentication fails, or the
        dispatch request is rejected.
    """
    if not capability or not isinstance(capability, str):
        raise CapabilityDispatchError("capability must be a non-empty string")

    if payload is None:
        payload = {}

    body: dict[str, Any] = {
        "kind": "collection",
        "name": capability,
        "args": {"payload": payload},
    }
    if model_profile:
        body["model_profile"] = model_profile
    if role:
        body["role"] = role

    endpoint = _url(daemon_url, DISPATCH_ENDPOINT)
    resp = _send(endpoint, method="POST", body=body, psk=psk, timeout=timeout)

    status = resp.get("_status", 0)
    if resp.get("_error"):
        raise CapabilityDispatchError(f"daemon unreachable for capability dispatch {capability!r}: {resp['_error']}")
    if status == 401:
        raise CapabilityDispatchError(f"unauthorized (bad or missing PSK) for capability dispatch {capability!r}")
    if status not in (200, 201):
        detail = resp.get("detail", f"HTTP {status}")
        raise CapabilityDispatchError(f"dispatch of {capability!r} failed: {detail}")

    return resp


def list_capabilities(
    daemon_url: str = DEFAULT_DAEMON_URL,
    psk: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> list[str]:
    """List all registered capability tags known to the daemon.

    Calls ``GET /api/dispatch/available`` and extracts the ``registered_kinds``
    plus the local process registry.

    Parameters
    ----------
    daemon_url:
        Base URL of the daemon.
    psk:
        Pre-shared key for daemon auth.
    timeout:
        Per-request timeout in seconds.

    Returns
    -------
    list[str]
        Sorted list of known capability tags.

    Raises
    ------
    CapabilityDispatchError
        If the daemon is unreachable or authentication fails.
    """
    capabilities: set[str] = set(_registry.keys())

    endpoint = _url(daemon_url, DISPATCH_AVAILABLE_ENDPOINT)
    resp = _send(endpoint, method="GET", psk=psk, timeout=timeout)

    status = resp.get("_status", 0)
    if resp.get("_error"):
        raise CapabilityDispatchError(f"daemon unreachable for list_capabilities: {resp['_error']}")
    if status == 401:
        raise CapabilityDispatchError("unauthorized (bad or missing PSK) for list_capabilities")
    if status != 200:
        detail = resp.get("detail", f"HTTP {status}")
        raise CapabilityDispatchError(f"list_capabilities failed: {detail}")

    registered_kinds = resp.get("registered_kinds", [])
    if isinstance(registered_kinds, list):
        for kind in registered_kinds:
            if isinstance(kind, dict) and "name" in kind:
                capabilities.add(str(kind["name"]))

    raw_handlers = resp.get("handlers")
    if isinstance(raw_handlers, list):
        for handler in raw_handlers:
            if isinstance(handler, dict) and "name" in handler:
                capabilities.add(str(handler["name"]))
            elif isinstance(handler, str):
                capabilities.add(handler)

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
    """Register a capability from a collection with the daemon.

    Stores the registration in the process-local registry and issues a
    dispatch registration request to the daemon via ``POST /api/dispatch``
    with kind ``"collection"`` and name ``"register_capability"``.

    Parameters
    ----------
    name:
        Capability tag to register.
    roles:
        Roles that may exercise this capability (default empty).
    model_needs:
        Model requirements dict (default empty).
    daemon_url:
        Base URL of the daemon.
    psk:
        Pre-shared key for daemon auth.
    timeout:
        Per-request timeout in seconds.

    Returns
    -------
    dict
        Registration result with ``name``, ``roles``, ``model_needs``,
        and ``registered`` fields.

    Raises
    ------
    CapabilityDispatchError
        If registration fails.
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

    body: dict[str, Any] = {
        "kind": "collection",
        "name": "register_capability",
        "args": {
            "capability_name": name,
            "roles": list(roles),
            "model_needs": dict(model_needs),
        },
    }

    endpoint = _url(daemon_url, DISPATCH_ENDPOINT)
    resp = _send(endpoint, method="POST", body=body, psk=psk, timeout=timeout)

    status = resp.get("_status", 0)
    if resp.get("_error"):
        raise CapabilityDispatchError(f"daemon unreachable for register_capability {name!r}: {resp['_error']}")
    if status == 401:
        raise CapabilityDispatchError(f"unauthorized (bad or missing PSK) for register_capability {name!r}")
    if status not in (200, 201):
        detail = resp.get("detail", f"HTTP {status}")
        raise CapabilityDispatchError(f"register_capability {name!r} failed: {detail}")

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
