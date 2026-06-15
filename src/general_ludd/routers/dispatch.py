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

from general_ludd.dispatch.dynamic_dispatcher import (
    DispatchResult,
    DynamicDispatcher,
    parse_tool_calls,
)

logger = logging.getLogger(__name__)

# Bounded ring-buffer for recent dispatch history (facts facet).
_MAX_RECENT_DISPATCHES = 50

Handler = Callable[[str, dict[str, Any]], Any]


def register(
    app: FastAPI,
    _daemon_state: dict[str, Any],
    *,
    role_handler: Handler | None = None,
    mcp_handler: Handler | None = None,
    skill_handler: Handler | None = None,
    collection_handler: Handler | None = None,
) -> None:
    """Register /api/dispatch routes on ``app``.

    Handlers are injected from daemon.py.  When a handler is ``None`` the
    dispatcher will return a fail-closed error for that kind rather than
    crashing.
    """

    dispatcher = DynamicDispatcher(
        role_handler=role_handler,
        mcp_handler=mcp_handler,
        skill_handler=skill_handler,
        collection_handler=collection_handler,
    )

    # Per-process ring buffer for recent dispatch history.
    _recent_dispatches: list[dict[str, Any]] = []

    def _record(results: list[DispatchResult]) -> None:
        for r in results:
            entry: dict[str, Any] = {
                "ts": datetime.now(UTC).isoformat(),
                **r.to_dict(),
            }
            _recent_dispatches.append(entry)
        # Trim to bound
        while len(_recent_dispatches) > _MAX_RECENT_DISPATCHES:
            _recent_dispatches.pop(0)

    @app.post("/api/dispatch")
    async def api_dispatch(body: dict[str, Any]) -> dict[str, Any]:
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
        results = dispatcher.dispatch_all(calls)
        _record(results)
        return {
            "results": [r.to_dict() for r in results],
            "count": len(results),
            "ok_count": sum(1 for r in results if r.ok),
            "error_count": sum(1 for r in results if not r.ok),
        }

    @app.get("/api/dispatch/recent")
    async def api_dispatch_recent(limit: int = 20) -> dict[str, Any]:
        """Return the most recent dispatch events (bounded, newest last)."""
        bounded = max(1, min(limit, _MAX_RECENT_DISPATCHES))
        return {"recent": _recent_dispatches[-bounded:], "total": len(_recent_dispatches)}

    @app.get("/api/dispatch/available")
    async def api_dispatch_available() -> dict[str, Any]:
        """Return which handler kinds are registered on this daemon instance."""
        return dispatcher.list_available()

    # Expose a _dispatch_facet callable for facts.py (registered below).
    def _dispatch_facet() -> dict[str, Any]:
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
