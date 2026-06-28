"""Activation tests for the can_invoke agent-permission matrix at the daemon
dispatch sites.

Two daemon dispatch sites build ``AgentTask``s that flow into
``AgentDispatcher.dispatch_one`` (which gates on
``registry.can_invoke(invoker, target)`` when ``task.invoker_name`` is set):

  * pipeline DispatchLane via ``pipeline.daemon_adapters.make_dispatch_fn``
  * daemon role dispatch via ``daemon_wiring.make_role_handler``

Before this fix BOTH sites left ``invoker_name`` empty, so the gate's
``if task.invoker_name and ...`` guard short-circuited and the matrix was inert
in the daemon — any role could dispatch any agent. These tests pin that:

  1. Each site now stamps the trusted ``"build"`` invoker onto the task.
  2. Driven through a REAL ``AgentDispatcher`` built on the REAL
     ``default_registry()``, a legitimate dispatch (registered target) is
     ALLOWED — the gate does not deny a valid pipeline/role dispatch.
  3. An UNAUTHORIZED invoker (one without dispatch permission) is DENIED, and an
     UNREGISTERED target is fail-closed — proving the gate actually enforces and
     is not merely bypassed.
"""

from __future__ import annotations

import pytest

from general_ludd.agents.dispatcher import AgentDispatcher
from general_ludd.agents.registry import default_registry
from general_ludd.agents.types import AgentTask
from general_ludd.daemon_wiring import make_role_handler
from general_ludd.pipeline.daemon_adapters import make_dispatch_fn


async def _echo_executor(task: AgentTask) -> str:
    return f"ran:{task.agent_name}"


class _CapturingDispatcher:
    """Records the AgentTask passed to dispatch_one without running the gate."""

    def __init__(self) -> None:
        self.last_task: AgentTask | None = None

    async def dispatch_one(self, task: AgentTask) -> object:
        self.last_task = task

        class _R:
            output = ""
            status = "completed"

        return _R()


class TestInvokerIsStamped:
    """Both daemon dispatch sites stamp the trusted ``build`` invoker."""

    @pytest.mark.asyncio
    async def test_pipeline_dispatch_stamps_build_invoker(self) -> None:
        cap = _CapturingDispatcher()
        fn = make_dispatch_fn(cap)
        await fn("unit-1")
        assert cap.last_task is not None
        assert cap.last_task.invoker_name == "build"
        # Target must be a REGISTERED agent name (not the old, unregistered
        # "general-purpose" which the not-found guard would have rejected).
        assert cap.last_task.agent_name == "general"
        assert default_registry().get(cap.last_task.agent_name) is not None

    @pytest.mark.asyncio
    async def test_role_handler_stamps_build_invoker(self) -> None:
        cap = _CapturingDispatcher()
        handler = make_role_handler(cap)
        assert handler is not None
        await handler("explore", {"prompt": "look around"})
        assert cap.last_task is not None
        assert cap.last_task.invoker_name == "build"
        assert cap.last_task.agent_name == "explore"


class TestGateAllowsLegitimateDispatch:
    """Through the REAL registry-backed dispatcher, legit dispatch is ALLOWED."""

    @pytest.mark.asyncio
    async def test_pipeline_legit_dispatch_allowed(self) -> None:
        dispatcher = AgentDispatcher(default_registry(), executor=_echo_executor)
        fn = make_dispatch_fn(dispatcher)
        result = await fn("unit-42")
        # build -> general is permitted (build.allowed_subagents == ["*"]).
        assert result.status == "completed", result.output
        assert result.output == "ran:general"

    @pytest.mark.asyncio
    async def test_role_legit_dispatch_allowed(self) -> None:
        dispatcher = AgentDispatcher(default_registry(), executor=_echo_executor)
        handler = make_role_handler(dispatcher)
        assert handler is not None
        # build -> explore is permitted (explore is registered; build allows "*").
        out = await handler("explore", {"prompt": "x"})
        assert out == "ran:explore"


class TestGateEnforces:
    """The gate is no longer inert: unauthorized invoker / unregistered target
    are DENIED through the real registry."""

    @pytest.mark.asyncio
    async def test_unauthorized_invoker_is_denied(self) -> None:
        # "explore" is a registered subagent with can_dispatch_subagents=False.
        # A task claiming "explore" as invoker must be DENIED dispatching another
        # agent — proving the matrix enforces, not bypasses.
        dispatcher = AgentDispatcher(default_registry(), executor=_echo_executor)
        task = AgentTask(
            task_id="t-unauth",
            agent_name="general",
            description="d",
            prompt="p",
            invoker_name="explore",
        )
        result = await dispatcher.dispatch_one(task)
        assert result.status == "failed"
        assert "permission denied" in result.output.lower()

    @pytest.mark.asyncio
    async def test_unknown_invoker_is_denied(self) -> None:
        dispatcher = AgentDispatcher(default_registry(), executor=_echo_executor)
        task = AgentTask(
            task_id="t-unknown-invoker",
            agent_name="general",
            description="d",
            prompt="p",
            invoker_name="ghost",  # not registered
        )
        result = await dispatcher.dispatch_one(task)
        assert result.status == "failed"
        assert "permission denied" in result.output.lower()

    @pytest.mark.asyncio
    async def test_unregistered_target_is_fail_closed(self) -> None:
        # A model-supplied role name that is not a registered agent is rejected
        # by the dispatcher's not-found guard (the can_invoke gate would also
        # deny it since the target is unregistered).
        dispatcher = AgentDispatcher(default_registry(), executor=_echo_executor)
        handler = make_role_handler(dispatcher)
        assert handler is not None
        out = await handler("not-a-real-agent", {"prompt": "x"})
        assert "not found in registry" in out.lower()
