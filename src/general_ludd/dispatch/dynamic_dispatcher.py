"""Dynamic dispatcher: route model tool-calls to role/collection/mcp/skill handlers.

When a model turn returns a tool_call request the DynamicDispatcher routes the
call to the correct handler by ``kind``, captures the result, and returns a
``DispatchResult`` that can be written into a ``VariableStore`` so the next
prompt turn reflects reality.

# TODO(integration): call DynamicDispatcher from the event-loop turn handler
# when a model returns tool_calls, then re-render the next prompt from the
# VariableStore.  See event_loop/loop.py _dispatch_execute_job for the
# integration point.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from general_ludd.security.capability_lattice import role_may_dispatch

logger = logging.getLogger(__name__)

# Supported tool-call kinds.
ToolCallKind = Literal["role", "collection", "mcp", "skill"]

# Privileged tool-call kinds: an unbound (None) role is denied these fail-closed.
# These kinds can drive self-modification / privileged handlers, so a dispatcher
# constructed without an explicit role must NOT be able to reach them.
PRIVILEGED_KINDS = frozenset({"role", "collection", "mcp", "skill"})

# Explicit opt-in sentinel: the ONLY role value that bypasses the capability
# gate entirely (e.g. trusted in-process call sites). A None role is no longer
# unrestricted — it denies every privileged kind.
UNRESTRICTED_ROLE = "__unrestricted__"

# Typed handler signature: takes (name, args) and returns any JSON-serialisable
# value.  Handlers may be coroutines; DynamicDispatcher.dispatch is sync-only
# but callers may wrap in asyncio if needed.  For unit tests inject plain
# callables.
Handler = Callable[[str, dict[str, Any]], Any]


@dataclass
class ToolCall:
    """A single tool-call request from a model turn."""

    kind: ToolCallKind
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class DispatchResult:
    """The outcome of dispatching one ToolCall."""

    ok: bool
    kind: str = ""
    name: str = ""
    output: Any = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "kind": self.kind,
            "name": self.name,
            "output": self.output,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# parse_tool_calls
# ---------------------------------------------------------------------------

def parse_tool_calls(model_output: str | dict[str, Any]) -> list[ToolCall]:
    """Parse a model output into a list of ToolCall objects.

    Accepted shapes:
      • A dict with a top-level ``tool_calls`` list — each item must have
        ``kind``, ``name``, and optional ``args`` keys.
      • A dict that is itself a single tool-call (has ``kind`` and ``name``).
      • A JSON-encoded string of either of the above.
      • Anything else returns an empty list (parse errors are non-fatal).

    Unknown or missing ``kind`` values produce a ToolCall whose ``kind`` is set
    to the raw string; dispatch will then fail-closed via the unknown-kind path.
    """
    raw: Any = model_output

    if isinstance(raw, str):
        raw = raw.strip()
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.debug("parse_tool_calls: not valid JSON, returning []")
            return []

    if not isinstance(raw, dict):
        logger.debug("parse_tool_calls: expected dict, got %s", type(raw).__name__)
        return []

    # Shape 1: {"tool_calls": [...]}
    if "tool_calls" in raw:
        calls_raw = raw["tool_calls"]
        if not isinstance(calls_raw, list):
            return []
        return _parse_call_list(calls_raw)

    # Shape 2: single tool-call dict {"kind": ..., "name": ..., "args": {...}}
    if "kind" in raw and "name" in raw:
        call = _parse_single(raw)
        return [call] if call is not None else []

    return []


def _parse_call_list(items: list[Any]) -> list[ToolCall]:
    results: list[ToolCall] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        call = _parse_single(item)
        if call is not None:
            results.append(call)
    return results


def _parse_single(item: dict[str, Any]) -> ToolCall | None:
    kind_raw = item.get("kind")
    name_raw = item.get("name")
    if not isinstance(name_raw, str) or not name_raw:
        logger.debug("parse_tool_calls: skipping item without name: %s", item)
        return None
    # kind is required; if missing or wrong type treat as unknown so
    # dispatch fails-closed.
    kind_str: str = str(kind_raw) if kind_raw is not None else "unknown"
    args_raw = item.get("args", {})
    args: dict[str, Any] = args_raw if isinstance(args_raw, dict) else {}
    # We deliberately allow kind values not in the Literal union here so that
    # the dispatcher can handle them at dispatch time (fail-closed).
    return ToolCall(kind=kind_str, name=name_raw, args=args)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# DynamicDispatcher
# ---------------------------------------------------------------------------

class DynamicDispatcher:
    """Routes ToolCalls to injected handler callables.

    Handlers are injected at construction time so the dispatcher is fully
    unit-testable without a live daemon.  Each handler is a callable that
    accepts ``(name: str, args: dict) -> Any``.

    Unknown kinds produce a fail-closed ``DispatchResult(ok=False, error=...)``.
    """

    def __init__(
        self,
        *,
        role_handler: Handler | None = None,
        mcp_handler: Handler | None = None,
        skill_handler: Handler | None = None,
        collection_handler: Handler | None = None,
        role: str | None = None,
    ) -> None:
        self._handlers: dict[str, Handler] = {}
        if role_handler is not None:
            self._handlers["role"] = role_handler
        if mcp_handler is not None:
            self._handlers["mcp"] = mcp_handler
        if skill_handler is not None:
            self._handlers["skill"] = skill_handler
        if collection_handler is not None:
            self._handlers["collection"] = collection_handler
        # The acting role whose capability lattice gates every dispatch
        # (issue #58). A None role now FAILS CLOSED on privileged kinds (it is
        # NOT unrestricted); only the explicit ``UNRESTRICTED_ROLE`` sentinel
        # bypasses the gate. When set to a real role, a tool-call kind the role
        # lacks is denied BEFORE its handler is invoked.
        self._role = role

    def dispatch(self, call: ToolCall) -> DispatchResult:
        """Dispatch a single ToolCall.  Returns DispatchResult — never raises."""
        kind = call.kind

        # Per-role capability lattice (issue #58): the gate fails closed unless
        # the role is the explicit UNRESTRICTED_ROLE sentinel. A None (unbound)
        # role denies every PRIVILEGED_KIND; a real role must hold the capability
        # the tool-call kind requires. The check runs BEFORE the handler.
        if self._role != UNRESTRICTED_ROLE and (
            (self._role is None and kind in PRIVILEGED_KINDS)
            or (self._role is not None and not role_may_dispatch(self._role, kind))
        ):
            logger.warning(
                "DynamicDispatcher: role %r lacks capability for kind %r "
                "(tool %r) — fail-closed",
                self._role,
                kind,
                call.name,
            )
            return DispatchResult(
                ok=False,
                kind=kind,
                name=call.name,
                error=(
                    f"capability_denied: role {self._role!r} may not dispatch "
                    f"kind {kind!r}"
                ),
            )

        handler = self._handlers.get(kind)
        if handler is None:
            logger.warning(
                "DynamicDispatcher: unknown kind %r for tool %r — fail-closed",
                kind,
                call.name,
            )
            return DispatchResult(
                ok=False,
                kind=kind,
                name=call.name,
                error=f"unknown_kind:{kind}",
            )
        try:
            output = handler(call.name, call.args)
            return DispatchResult(ok=True, kind=kind, name=call.name, output=output)
        except Exception as exc:
            logger.error(
                "DynamicDispatcher: handler %r raised for %r: %s",
                kind,
                call.name,
                exc,
            )
            return DispatchResult(
                ok=False,
                kind=kind,
                name=call.name,
                error=str(exc),
            )

    def dispatch_all(self, calls: list[ToolCall]) -> list[DispatchResult]:
        """Dispatch every ToolCall in order, accumulating results."""
        return [self.dispatch(call) for call in calls]

    def list_available(self) -> dict[str, list[str]]:
        """Return the set of registered handler kinds (no scanning)."""
        return {"registered_kinds": list(self._handlers.keys())}
