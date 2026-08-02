"""POST /api/dispatch — thin HTTP shim over DynamicDispatcher.

PSK auth is applied by the daemon middleware (path is not in _PUBLIC_PATHS).
Handlers injected at registration time from daemon.py; unknown kinds are
fail-closed by the dispatcher itself.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException

from general_ludd.dispatch.capabilities import CapabilityRegistry
from general_ludd.dispatch.dynamic_dispatcher import (
    DispatchResult,
    DynamicDispatcher,
    parse_tool_calls,
)
from general_ludd.dispatch.router import CapabilityRouter

logger = logging.getLogger(__name__)

# Per-request tool_calls cap (D-16): unbounded model tool calls = unbounded
# cost.  Any request carrying more than this many calls is rejected with 422.
MAX_CALLS_PER_REQUEST = 20

# Bounded ring-buffer for recent dispatch history (facts facet).
_MAX_RECENT_DISPATCHES = 50

Handler = Callable[[str, dict[str, object]], object]


def register(
    app: FastAPI,
    _daemon_state: dict[str, object],
    *,
    role_handler: Handler | None = None,
    mcp_handler: Handler | None = None,
    skill_handler: Handler | None = None,
    collection_handler: Handler | None = None,
    role: str | None = None,
    capability_registry: CapabilityRegistry | None = None,
) -> None:
    """Register /api/dispatch routes on ``app``.

    Handlers are injected from daemon.py.  When a handler is ``None`` the
    dispatcher will return a fail-closed error for that kind rather than
    crashing.

    ``role`` is the acting role whose capability lattice gates every dispatch.
    It defaults to ``None`` so the live HTTP endpoint is DENY-BY-DEFAULT for the
    privileged kinds: an unbound dispatcher fails closed rather than routing
    privileged tool-calls. Pass an explicit role (or ``UNRESTRICTED_ROLE``) to
    widen this deliberately.
    """

    dispatcher = DynamicDispatcher(
        role_handler=role_handler,
        mcp_handler=mcp_handler,
        skill_handler=skill_handler,
        collection_handler=collection_handler,
        role=role,
    )

    # Per-process ring buffer for recent dispatch history.
    _recent_dispatches: list[dict[str, object]] = []

    def _record(results: list[DispatchResult]) -> None:
        for r in results:
            entry: dict[str, object] = {
                "ts": datetime.now(UTC).isoformat(),
                **r.to_dict(),
            }
            _recent_dispatches.append(entry)
        # Trim to bound
        while len(_recent_dispatches) > _MAX_RECENT_DISPATCHES:
            _recent_dispatches.pop(0)

    @app.post(
        "/api/dispatch",
        summary="Dispatch tool calls (MCP/skill/role/collection)",
        description=(
            "HTTP shim over DynamicDispatcher: supply tool_calls "
            "[{kind,name,args}]. Capability-gated; capped at 20 calls/request "
            "(D-16). PSK-authenticated."
        ),
    )
    async def api_dispatch(body: dict[str, object]) -> dict[str, object]:
        """Dispatch one or more tool-call requests from a model turn.

        Body shape (single call)::

            {"kind": "role", "name": "planner", "args": {}}

        Or with a list::

            {"tool_calls": [{"kind": "mcp", "name": "fs", "args": {}}]}

        Returns a list of DispatchResult dicts.
        """
        calls = parse_tool_calls(body)
        if not calls:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Could not parse any tool calls from request body. "
                    "Expected {kind, name[, args]} or {tool_calls: [...]}"
                ),
            )
        # D-16: cap per-request tool_calls to bound model cost.
        if len(calls) > MAX_CALLS_PER_REQUEST:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Request contains {len(calls)} tool calls which exceeds the "
                    f"per-request cap of {MAX_CALLS_PER_REQUEST}. "
                    "Split into smaller batches."
                ),
            )
        results = await dispatcher.dispatch_all(calls)
        _record(results)
        return {
            "results": [r.to_dict() for r in results],
            "count": len(results),
            "ok_count": sum(1 for r in results if r.ok),
            "error_count": sum(1 for r in results if not r.ok),
        }

    @app.get("/api/dispatch/recent")
    async def api_dispatch_recent(limit: int = 20) -> dict[str, object]:
        """Return the most recent dispatch events (bounded, newest last)."""
        bounded = max(1, min(limit, _MAX_RECENT_DISPATCHES))
        return {"recent": _recent_dispatches[-bounded:], "total": len(_recent_dispatches)}

    @app.get("/api/dispatch/available")
    async def api_dispatch_available() -> dict[str, list[str]]:
        """Return which handler kinds are registered on this daemon instance."""
        return dispatcher.list_available()

    if capability_registry is not None:
        _cap_router = CapabilityRouter(capability_registry)

        @app.post(
            "/api/dispatch/capability",
            summary="Route a capability request to matching Ansible collections",
            description=(
                "Query the capability registry for collections that declare a "
                "given capability (tag). Body: {capability: str[, payload: dict]} "
                "or {collection: str[, payload: dict]} for direct lookup. "
                "Returns RouteResult with matching collections."
            ),
        )
        async def api_dispatch_capability(body: dict[str, object]) -> dict[str, object]:
            capability = body.get("capability")
            collection = body.get("collection")
            has_capability = "capability" in body and isinstance(capability, str)
            has_collection = "collection" in body and isinstance(collection, str)
            raw_payload = body.get("payload")
            payload: dict[str, Any] = {}
            if isinstance(raw_payload, dict):
                for k, v in raw_payload.items():
                    payload[str(k)] = v

            if has_capability:
                result = _cap_router.route(str(capability), payload)
            elif has_collection:
                result = _cap_router.route_by_collection(str(collection), payload)
            else:
                from fastapi import HTTPException

                raise HTTPException(
                    status_code=422,
                    detail="Provide 'capability' (tag) or 'collection' (namespace.name)",
                )

            return {
                "ok": result.ok,
                "capability": result.capability,
                "matches": [
                    {"collection": m.name, "namespace": m.collection.namespace, "score": m.score}
                    for m in result.matches
                ],
                "payload": result.payload,
                "error": result.error,
            }

        @app.get("/api/dispatch/capabilities")
        async def api_dispatch_capabilities() -> dict[str, list[str]]:
            """List all known capability tags in the registry."""
            return {"capabilities": _cap_router.list_capabilities()}

        @app.get("/api/dispatch/capability/registry")
        async def api_dispatch_capability_registry() -> dict[str, object]:
            """Return the full capability registry as a dict."""
            return capability_registry.to_dict()

    # Expose a _dispatch_facet callable for facts.py (registered below).
    def _dispatch_facet() -> dict[str, object]:
        """Snapshot for inclusion in /api/facts under key ``"dispatch"``."""
        recent = _recent_dispatches[-10:]
        return {
            "recent_count": len(recent),
            "total_dispatched": len(_recent_dispatches),
            "recent": recent,
            "registered_kinds": dispatcher.list_available().get("registered_kinds", []),
        }

    # Store on app.state so facts.py can reach it without a circular import.
    app.state._dispatch_facet = _dispatch_facet
