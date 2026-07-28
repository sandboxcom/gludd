"""Daemon wiring helpers — build real handler callables for DynamicDispatcher.

These factories are extracted from daemon.py so they are independently testable
without standing up a full FastAPI app.  daemon.py calls them during lifespan
startup (after subsystems are initialised) to wire the dispatch router with real
entry points instead of the original ``None`` stubs.

Wired entry points (W: event-loop-wiring)
------------------------------------------
mcp_handler        → MCPClient.call_tool(server_id, tool_name, arguments)
skill_handler      → SkillRegistry.get(name).body  (sync, cheap)
role_handler       → AgentDispatcher.dispatch_one(AgentTask)  (async)
collection_handler → AnsibleRunnerAdapter.run_playbook(transient_one_task_pb)
                      Builds a transient playbook that invokes the named
                      Ansible collection module, registers it for the run,
                      then unregisters. None when no adapter is supplied.

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

    The dispatched task carries ``invoker_name="build"`` so the dispatcher's
    ``can_invoke`` permission gate is ACTIVE for daemon-driven role dispatch.
    An empty invoker bypasses the gate entirely (the agent-permission matrix
    stays inert); ``"build"`` is the registered primary agent that
    ``default_registry()`` grants ``can_dispatch_subagents=True`` with
    ``allowed_subagents=["*"]``, so it truthfully covers every registered role
    target while a model-supplied ``name`` that is NOT a registered agent is
    fail-closed (the dispatcher's not-found / can_invoke guards both reject it).

    Args:
        agent_dispatcher: An AgentDispatcher instance, or None.

    Returns:
        An async callable, or None when agent_dispatcher is None.
    """
    if agent_dispatcher is None:
        return None

    async def _role_handler(name: str, args: dict[str, Any]) -> str:
        import time as _time

        from general_ludd.agents.types import AgentTask

        task = AgentTask(
            task_id=f"role-{name}-{int(_time.monotonic() * 1000)}",
            agent_name=name,
            description=args.get("description", f"Role dispatch: {name}"),
            prompt=args.get("prompt", name),
            invoker_name="build",
        )
        result = await agent_dispatcher.dispatch_one(task)
        return result.output or ""

    return _role_handler


# ---------------------------------------------------------------------------
# Collection handler (Ansible module dispatch via AnsibleRunnerAdapter)
# ---------------------------------------------------------------------------

# A module name must be a dotted Ansible FQCN (``namespace.collection.module``
# or ``namespace.module``). It becomes a YAML task key, so a malformed name
# must be rejected before it is embedded in the transient playbook.
_FQCN_RE = r"^[a-z0-9_]+(\.[a-z0-9_]+)+$"


def make_collection_handler(
    runner_adapter: Any | None,
) -> Callable[[str, dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]] | None:
    """Return an async collection_handler callable or None if no adapter is available.

    Handler signature: ``async (name: str, args: dict) -> dict[str, Any]``

    Dispatches a named Ansible collection module (e.g.
    ``ansible.builtin.command``) by building a transient one-task playbook
    that invokes it on localhost, registering that playbook under the adapter
    for the duration of the run, invoking ``AnsibleRunnerAdapter.run_playbook``
    and returning its result dict. The transient playbook is unregistered in
    a ``finally`` block so the adapter's registry is never left polluted.

    Dispatch metadata is read from ``args`` under underscore-prefixed keys and
    stripped before the rest is forwarded as module arguments:

      * ``_timeout`` — forwarded to ``run_playbook(timeout=...)`` (default None
        → the adapter's env-driven bound, ``GLUDD_PLAYBOOK_TIMEOUT``).
      * ``_hosts`` — playbook ``hosts:`` target (default ``"localhost"``).

    Args:
        runner_adapter: An ``AnsibleRunnerAdapter`` (or duck-typed stand-in
            exposing ``private_data_dir``, ``register_playbook``,
            ``run_playbook`` and ``unregister_playbook``), or None.

    Returns:
        An async callable, or None when ``runner_adapter`` is None (the
        DynamicDispatcher then fails-closed for the ``collection`` kind).
    """
    if runner_adapter is None:
        return None

    async def _collection_handler(name: str, args: dict[str, Any]) -> dict[str, Any]:
        import asyncio
        import os
        import re
        import uuid

        import yaml

        # The module FQCN becomes a YAML task key. A malformed name could
        # either corrupt the playbook or smuggle an arbitrary task directive
        # — reject it before embedding.
        if not re.match(_FQCN_RE, name):
            raise ValueError(
                f"collection module name must be a dotted FQCN (e.g. 'ansible.builtin.command'), got {name!r}"
            )

        # Copy so dispatch metadata can be peeled without mutating caller state.
        task_args = dict(args)
        timeout = task_args.pop("_timeout", None)
        hosts = task_args.pop("_hosts", "localhost")

        # A-COLLECTION-HANDLER-UNWRAP: wrap every task-arg value Ansible-unsafe
        # BEFORE it is embedded in the transient playbook YAML so Jinja carried
        # in a caller-supplied argument (e.g.  {{ lookup('pipe','id') }}) is
        # never re-templated by the ansible executor.
        from general_ludd.ansible.unsafe import wrap_unsafe

        task_args = {k: wrap_unsafe(v) for k, v in task_args.items()}

        # When ansible is available use AnsibleDumper so AnsibleUnsafeText
        # values are serialised with the !unsafe YAML tag.  Fall back to
        # standard safe_dump when ansible is not installed (the values are
        # plain strings and SSTI is not a threat).
        try:
            from ansible.parsing.yaml.dumper import AnsibleDumper  # pragma: no cover

            _dumper = AnsibleDumper  # pragma: no cover
        except ImportError:  # pragma: no cover
            _dumper = yaml.Dumper  # pragma: no cover

        playbook = [
            {
                "hosts": hosts,
                "connection": "local",
                "gather_facts": False,
                "tasks": [
                    {"name": f"gludd_dispatch:{name}", name: task_args},
                ],
            }
        ]

        dispatch_dir = os.path.join(runner_adapter.private_data_dir, "dispatch_playbooks")
        os.makedirs(dispatch_dir, exist_ok=True)
        playbook_path = os.path.join(dispatch_dir, f"{uuid.uuid4().hex}.yml")

        # AB-1: serialize+write off the event loop so the async daemon handler
        # does not block on disk I/O under concurrent dispatch.
        def _write_playbook() -> None:
            with open(playbook_path, "w") as f:
                # A-COLLECTION-HANDLER-UNWRAP: yaml.dump (not safe_dump) so the
                # custom Dumper class is honoured.  AnsibleDumper outputs the
                # !unsafe tag for wrapped values; the plain Dumper fallback
                # serialises plain-str task_args identically to safe_dump.
                yaml.dump(playbook, f, Dumper=_dumper, default_flow_style=False)

        await asyncio.to_thread(_write_playbook)

        transient_name = f"_dispatch_{uuid.uuid4().hex}"
        runner_adapter.register_playbook(transient_name, playbook_path)
        try:
            result: dict[str, Any] = runner_adapter.run_playbook(transient_name, timeout=timeout)
            return result
        finally:
            runner_adapter.unregister_playbook(transient_name)

    return _collection_handler


# ---------------------------------------------------------------------------
# Convenience: build all handlers at once
# ---------------------------------------------------------------------------


def build_dispatch_handlers(
    *,
    mcp_client: Any | None,
    skill_registry: Any | None,
    agent_dispatcher: Any | None,
    runner_adapter: Any | None = None,
) -> dict[str, Callable[[str, dict[str, Any]], Any] | None]:
    """Build all dispatch handler callables from the daemon's live subsystems.

    Returns a dict with keys matching dispatch_router.register's kwargs:
        - mcp_handler
        - skill_handler
        - role_handler
        - collection_handler

    Any handler whose subsystem is None is also returned as None, meaning the
    DynamicDispatcher will fail-closed for that kind.

    Args:
        mcp_client:       Live MCPClient (or None).
        skill_registry:   Live SkillRegistry (or None).
        agent_dispatcher: Live AgentDispatcher (or None).
        runner_adapter:   Live AnsibleRunnerAdapter (or None). When supplied,
                          the ``collection`` kind is wired to dispatch Ansible
                          modules via a transient one-task playbook.

    Returns:
        Dict of handler kind → callable | None.
    """
    return {
        "mcp_handler": make_mcp_handler(mcp_client),
        "skill_handler": make_skill_handler(skill_registry),
        "role_handler": make_role_handler(agent_dispatcher),
        "collection_handler": make_collection_handler(runner_adapter),
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
