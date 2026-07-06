"""Tests for AgentDispatcher permission gate: enabled check and can_invoke check."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from general_ludd.agents.dispatcher import AgentDispatcher, AgentTaskResult
from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentTask, AgentType


def _make_registry() -> AgentRegistry:
    registry = AgentRegistry()

    # A primary agent that can only dispatch "allowed-sub"
    registry.register(AgentConfig(
        name="restricted-invoker",
        description="Can only dispatch allowed-sub",
        type=AgentType.PRIMARY,
        permissions=AgentPermission(
            can_dispatch_subagents=True,
            allowed_subagents=["allowed-sub"],
        ),
        enabled=True,
    ))

    # An allowed subagent target
    registry.register(AgentConfig(
        name="allowed-sub",
        description="Allowed subagent",
        type=AgentType.SUBAGENT,
        permissions=AgentPermission(),
        enabled=True,
    ))

    # A forbidden subagent target (not in restricted-invoker's allowed list)
    registry.register(AgentConfig(
        name="forbidden-sub",
        description="Forbidden subagent",
        type=AgentType.SUBAGENT,
        permissions=AgentPermission(),
        enabled=True,
    ))

    # A disabled agent
    registry.register(AgentConfig(
        name="disabled-agent",
        description="Disabled agent",
        type=AgentType.SUBAGENT,
        permissions=AgentPermission(),
        enabled=False,
    ))

    return registry


def _run(coro: object) -> object:
    return cast(Any, asyncio.run)(coro)


def test_disabled_agent_returns_failed() -> None:
    registry = _make_registry()
    dispatcher = AgentDispatcher(registry=registry)
    task = AgentTask(
        task_id="t1",
        agent_name="disabled-agent",
        description="test",
        prompt="do stuff",
    )
    result: AgentTaskResult = _run(dispatcher.dispatch_one(task))
    assert result.status == "failed"
    assert "disabled" in result.output.lower()


def test_restricted_invoker_denied_forbidden_sub() -> None:
    registry = _make_registry()
    dispatcher = AgentDispatcher(registry=registry)
    task = AgentTask(
        task_id="t2",
        agent_name="forbidden-sub",
        description="test",
        prompt="do stuff",
        invoker_name="restricted-invoker",
    )
    result: AgentTaskResult = _run(dispatcher.dispatch_one(task))
    assert result.status == "failed"
    assert "Permission denied" in result.output
    assert "restricted-invoker" in result.output
    assert "forbidden-sub" in result.output


def test_restricted_invoker_allowed_sub_proceeds() -> None:
    async def _echo(task: AgentTask) -> str:
        return "hello"

    registry = _make_registry()
    dispatcher = AgentDispatcher(registry=registry, executor=_echo)
    task = AgentTask(
        task_id="t3",
        agent_name="allowed-sub",
        description="test",
        prompt="do stuff",
        invoker_name="restricted-invoker",
    )
    result: AgentTaskResult = _run(dispatcher.dispatch_one(task))
    assert result.status == "completed"
    assert result.output == "hello"


@pytest.mark.parametrize("empty_invoker", ["", "   ", "\t", None])
def test_empty_invoker_name_is_denied_fail_closed(empty_invoker: str | None) -> None:
    """SECURITY (task #50): an empty / None / whitespace-only invoker_name is an
    UNTRUSTED (un-named) invoker and must be DENIED — it must NOT bypass the
    can_invoke matrix (fail-CLOSED). The transport/executor must NOT run."""
    executor_ran = {"called": False}

    async def _echo(task: AgentTask) -> str:
        executor_ran["called"] = True
        return "ok"

    registry = _make_registry()
    dispatcher = AgentDispatcher(registry=registry, executor=_echo)
    task = AgentTask(
        task_id="t4",
        agent_name="forbidden-sub",
        description="test",
        prompt="do stuff",
    )
    # Assign directly so we can also exercise the None case (dataclass default "").
    cast(Any, task).invoker_name = empty_invoker
    result: AgentTaskResult = _run(dispatcher.dispatch_one(task))
    assert result.status == "failed"
    assert "permission denied" in result.output.lower()
    assert "forbidden-sub" in result.output
    # The executor (transport / target) must never have been reached.
    assert executor_ran["called"] is False


def test_unknown_agent_returns_failed() -> None:
    registry = _make_registry()
    dispatcher = AgentDispatcher(registry=registry)
    task = AgentTask(
        task_id="t5",
        agent_name="nonexistent",
        description="test",
        prompt="do stuff",
    )
    result: AgentTaskResult = _run(dispatcher.dispatch_one(task))
    assert result.status == "failed"
    assert "not found" in result.output.lower()
