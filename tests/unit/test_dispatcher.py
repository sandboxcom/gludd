"""Unit tests for AgentDispatcher F3 security fixes:
- disabled agent rejection
- invoker permission enforcement
- back-compat when invoker=None
- permitted invoker succeeds
"""

from __future__ import annotations

import asyncio

from general_ludd.agents.dispatcher import AgentDispatcher, AgentTask
from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentType


def _make_registry() -> AgentRegistry:
    return AgentRegistry()


def _subagent_config(name: str, enabled: bool = True) -> AgentConfig:
    return AgentConfig(
        name=name,
        description=f"Test subagent {name}",
        type=AgentType.SUBAGENT,
        permissions=AgentPermission(
            can_edit=False,
            can_bash=False,
            can_read=True,
            can_dispatch_subagents=False,
            allowed_subagents=[],
        ),
        enabled=enabled,
    )


def _invoker_config(
    name: str,
    can_dispatch: bool,
    allowed: list[str],
) -> AgentConfig:
    return AgentConfig(
        name=name,
        description=f"Test invoker {name}",
        type=AgentType.PRIMARY,
        permissions=AgentPermission(
            can_edit=False,
            can_bash=False,
            can_read=True,
            can_dispatch_subagents=can_dispatch,
            allowed_subagents=allowed,
        ),
        enabled=True,
    )


async def _async_executor(task: AgentTask) -> str:
    return f"executed:{task.agent_name}"


def _run(coro):  # type: ignore[return]
    return asyncio.run(coro)


class TestDispatchDisabledAgent:
    def test_dispatch_disabled_agent(self) -> None:
        """Dispatch to a disabled agent must return status='failed' with 'disabled' in output."""
        registry = _make_registry()
        registry.register(_subagent_config("worker", enabled=False))
        dispatcher = AgentDispatcher(registry, executor=_async_executor)

        task = AgentTask(
            task_id="t1",
            agent_name="worker",
            description="do work",
            prompt="run it",
        )
        result = _run(dispatcher.dispatch_one(task))

        assert result.status == "failed", f"Expected failed, got {result.status!r}"
        assert "disabled" in result.output.lower(), (
            f"Expected 'disabled' in output, got: {result.output!r}"
        )


class TestDispatchUnpermittedInvoker:
    def test_dispatch_unpermitted_invoker_no_flag(self) -> None:
        """Invoker with can_dispatch_subagents=False must be rejected."""
        registry = _make_registry()
        registry.register(_invoker_config("caller", can_dispatch=False, allowed=[]))
        registry.register(_subagent_config("target"))
        dispatcher = AgentDispatcher(registry, executor=_async_executor)

        task = AgentTask(
            task_id="t2",
            agent_name="target",
            description="do work",
            prompt="run it",
            invoker="caller",
        )
        result = _run(dispatcher.dispatch_one(task))

        assert result.status == "failed", f"Expected failed, got {result.status!r}"
        assert "not permitted" in result.output.lower(), (
            f"Expected 'not permitted' in output, got: {result.output!r}"
        )

    def test_dispatch_unpermitted_invoker_wrong_target(self) -> None:
        """Invoker with can_dispatch=True but target not in allowed_subagents must be rejected."""
        registry = _make_registry()
        registry.register(_invoker_config("caller", can_dispatch=True, allowed=["other_sub"]))
        registry.register(_subagent_config("target"))
        dispatcher = AgentDispatcher(registry, executor=_async_executor)

        task = AgentTask(
            task_id="t3",
            agent_name="target",
            description="do work",
            prompt="run it",
            invoker="caller",
        )
        result = _run(dispatcher.dispatch_one(task))

        assert result.status == "failed"
        assert "not permitted" in result.output.lower(), (
            f"Expected 'not permitted' in output, got: {result.output!r}"
        )


class TestDispatchBackCompatNoInvoker:
    def test_dispatch_back_compat_no_invoker(self) -> None:
        """Existing calls without invoker field complete normally (back-compat)."""
        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        dispatcher = AgentDispatcher(registry, executor=_async_executor)

        # No invoker keyword — old call pattern
        task = AgentTask(
            task_id="t4",
            agent_name="worker",
            description="do work",
            prompt="run it",
        )
        result = _run(dispatcher.dispatch_one(task))

        assert result.status == "completed", f"Expected completed, got {result.status!r}"
        assert "executed:worker" in result.output


class TestDispatchPermittedInvoker:
    def test_dispatch_permitted_invoker(self) -> None:
        """Invoker with can_dispatch_subagents=True and target in allowed_subagents succeeds."""
        registry = _make_registry()
        registry.register(_invoker_config("caller", can_dispatch=True, allowed=["target"]))
        registry.register(_subagent_config("target"))
        dispatcher = AgentDispatcher(registry, executor=_async_executor)

        task = AgentTask(
            task_id="t5",
            agent_name="target",
            description="do work",
            prompt="run it",
            invoker="caller",
        )
        result = _run(dispatcher.dispatch_one(task))

        assert result.status == "completed", f"Expected completed, got {result.status!r}"
        assert "executed:target" in result.output
