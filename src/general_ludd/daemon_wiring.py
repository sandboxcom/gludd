"""Daemon wiring helpers — build real handler callables for DynamicDispatcher.

These factories are extracted from daemon.py so they are independently testable
without standing up a full FastAPI app.  daemon.py calls them during lifespan
startup (after subsystems are initialised) to wire the dispatch router with real
entry points instead of the original ``None`` stubs.

Wired entry points (W: event-loop-wiring)
------------------------------------------
mcp_handler     → MCPClient.call_tool(server_id, tool_name, arguments)
skill_handler   → SkillRegistry.get(name).body  (sync, cheap)
role_handler    → AgentDispatcher.dispatch_one(AgentTask)  (async)
collection_handler → None (no collection loader exists; kept as documented stub)

SpendLimiter gate
-----------------
make_spend_guarded_executor wraps any ExecutorFn with a pre-call
SpendLimiter.would_exceed() check.  When the projected cost would exceed the
rolling window budget the call is skipped and a "deferred" sentinel is returned.
When spend_limiter is None the executor runs unconditionally.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MCP handler
# ---------------------------------------------------------------------------


def make_mcp_handler(
    mcp_client: Any | None,
) -> Callable[[str, dict[str, Any]], Any] | None:
    """Return an async mcp_handler callable or None if no client is available.

    Handler signature: ``async (name: str, args: dict) -> Any``

    ``name`` format: ``"<server_id>/<tool_name>"`` — the slash-delimited prefix
    is the MCP server identifier.  A name without a slash raises ValueError so
    the DynamicDispatcher returns a fail-closed error rather than silently
    miscalling an unknown server.

    Args:
        mcp_client: A connected MCPClient instance, or None.

    Returns:
        An async callable, or None when mcp_client is None.
    """
    if mcp_client is None:
        return None

    async def _mcp_handler(name: str, args: dict[str, Any]) -> Any:
        if "/" not in name:
            raise ValueError(
                f"MCP tool name must be 'server_id/tool_name', got {name!r}. "
                "Cannot determine server_id without a '/' separator."
            )
        server_id, tool_name = name.split("/", 1)
        return await mcp_client.call_tool(server_id, tool_name, args)

    return _mcp_handler


# ---------------------------------------------------------------------------
# Skill handler
# ---------------------------------------------------------------------------


def make_skill_handler(
    skill_registry: Any | None,
) -> Callable[[str, dict[str, Any]], Any] | None:
    """Return a skill_handler callable or None if no registry is available.

    Handler signature: ``(name: str, args: dict) -> str | None``

    The handler looks up the skill by name and returns its body text.  When the
    skill is not found it returns an explicit error string so the DynamicDispatcher
    wraps it in a successful-but-empty DispatchResult rather than raising.

    Args:
        skill_registry: A SkillRegistry instance, or None.

    Returns:
        A sync callable, or None when skill_registry is None.
    """
    if skill_registry is None:
        return None

    def _skill_handler(name: str, args: dict[str, Any]) -> str | None:
        skill = skill_registry.get(name)
        if skill is None:
            logger.warning("skill_handler: skill %r not found in registry", name)
            return f"skill not found: {name!r}"
        body: str | None = skill.body
        return body

    return _skill_handler


# ---------------------------------------------------------------------------
# Role handler (agent runner via AgentDispatcher)
# ---------------------------------------------------------------------------


def make_role_handler(
    agent_dispatcher: Any | None,
) -> Callable[[str, dict[str, Any]], Any] | None:
    """Return an async role_handler callable or None if no dispatcher is available.

    Handler signature: ``async (name: str, args: dict) -> str``

    Dispatches a named agent role via AgentDispatcher.dispatch_one.  The
    ``prompt`` key in args (defaulting to the role name itself) is forwarded as
    the AgentTask prompt so the downstream executor has context.

    Args:
        agent_dispatcher: An AgentDispatcher instance, or None.

    Returns:
        An async callable, or None when agent_dispatcher is None.
    """
    if agent_dispatcher is None:
        return None

    async def _role_handler(name: str, args: dict[str, Any]) -> str:
        import time as _time

        from general_ludd.agents.dispatcher import AgentTask

        task = AgentTask(
            task_id=f"role-{name}-{int(_time.monotonic() * 1000)}",
            agent_name=name,
            description=args.get("description", f"Role dispatch: {name}"),
            prompt=args.get("prompt", name),
        )
        result = await agent_dispatcher.dispatch_one(task)
        return result.output or ""

    return _role_handler


# ---------------------------------------------------------------------------
# Convenience: build all handlers at once
# ---------------------------------------------------------------------------


def build_dispatch_handlers(
    *,
    mcp_client: Any | None,
    skill_registry: Any | None,
    agent_dispatcher: Any | None,
) -> dict[str, Callable[[str, dict[str, Any]], Any] | None]:
    """Build all dispatch handler callables from the daemon's live subsystems.

    Returns a dict with keys matching dispatch_router.register's kwargs:
        - mcp_handler
        - skill_handler
        - role_handler
        - collection_handler  (always None — no collection loader implemented)

    Any handler whose subsystem is None is also returned as None, meaning the
    DynamicDispatcher will fail-closed for that kind.

    Args:
        mcp_client:       Live MCPClient (or None).
        skill_registry:   Live SkillRegistry (or None).
        agent_dispatcher: Live AgentDispatcher (or None).

    Returns:
        Dict of handler kind → callable | None.
    """
    return {
        "mcp_handler": make_mcp_handler(mcp_client),
        "skill_handler": make_skill_handler(skill_registry),
        "role_handler": make_role_handler(agent_dispatcher),
        # TODO(integration): collection_handler — no collection loader exists;
        # document as explicit stub so the dispatcher fails-closed cleanly.
        "collection_handler": None,
    }


# ---------------------------------------------------------------------------
# SpendLimiter gate wrapper
# ---------------------------------------------------------------------------

ExecutorFn = Callable[..., Coroutine[None, None, str]]


def make_spend_guarded_executor(
    *,
    executor: ExecutorFn,
    spend_limiter: Any | None,
    projected_cost_usd: float | None = 0.0,
) -> ExecutorFn:
    """Wrap an executor with an atomic SpendLimiter check-and-record gate.

    When ``spend_limiter`` is None, the original executor is returned unchanged
    (no-op gate).

    When a limiter is configured, the cost is charged ATOMICALLY *before* the
    call via ``spend_limiter.try_charge(projected_cost_usd, ...)``:

      * If the charge is accepted (it fits the remaining budget) it is also
        RECORDED against the rolling window in the same critical section, so
        the limiter is no longer inert — repeated calls accumulate spend and
        the cap actually trips (#1).  Recording before the call also makes the
        check-and-record atomic, so concurrent dispatches cannot collectively
        overshoot the cap (#3).
      * If the charge is refused — because it would exceed the cap, or because
        ``projected_cost_usd`` is ``None`` (unknown cost) while a cap is
        configured (fail CLOSED, #4) — the executor is NOT called and the
        sentinel string ``"deferred:spend_limit_exceeded"`` is returned.

    Charging the projection up front (rather than the measured cost afterward)
    is the conservative choice for a soft cap: a dispatch that is admitted has
    already consumed its budgeted headroom, so a concurrent dispatch sees the
    reduced remaining budget immediately.

    Args:
        executor:           The real async executor coroutine function.
        spend_limiter:      SpendLimiter instance, or None to disable the gate.
        projected_cost_usd: Estimated cost of one call (USD), or ``None`` when
                            the cost is unknown.  ``None`` fails CLOSED when a
                            cap is configured.  ``0.0`` never triggers deferral.

    Returns:
        A wrapped async executor with the same signature.
    """
    if spend_limiter is None:
        return executor

    async def _guarded(*args: Any, **kwargs: Any) -> str:
        # Atomic check-and-record: charges the projected cost iff it fits, and
        # fails closed on unknown cost under a configured cap.
        admitted = spend_limiter.try_charge(projected_cost_usd, kind="token")
        if not admitted:
            remaining = spend_limiter.remaining()
            logger.warning(
                "SpendLimiter: deferring dispatch — projected=%s USD remaining=%.6f USD",
                projected_cost_usd,
                remaining,
            )
            return "deferred:spend_limit_exceeded"
        return await executor(*args, **kwargs)

    return _guarded
