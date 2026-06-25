"""Unit tests for AgentDispatcher F3 security fixes:
- disabled agent rejection
- invoker permission enforcement (returns status='failed' result, does NOT raise)
- back-compat when invoker=None
- permitted invoker succeeds

NOTE: the can_invoke denial flows through the same AgentTaskResult contract as the
not-found/disabled branches (dispatch_one returns a failed result rather than raising)
so that dispatch_many's gather and every caller receives a result, not an exception.
"""

from __future__ import annotations

import asyncio

from general_ludd.agents.behavior import BehaviorRenderer, default_subagent_behavior
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
        """Invoker with can_dispatch_subagents=False is denied: status='failed',
        'permission denied' in output (no exception raised)."""
        registry = _make_registry()
        registry.register(_invoker_config("caller", can_dispatch=False, allowed=[]))
        registry.register(_subagent_config("target"))
        dispatcher = AgentDispatcher(registry, executor=_async_executor)

        task = AgentTask(
            task_id="t2",
            agent_name="target",
            description="do work",
            prompt="run it",
            invoker_name="caller",
        )
        result = _run(dispatcher.dispatch_one(task))

        assert result.status == "failed", f"Expected failed, got {result.status!r}"
        assert "permission denied" in result.output.lower(), (
            f"Expected 'permission denied' in output, got: {result.output!r}"
        )

    def test_dispatch_unpermitted_invoker_wrong_target(self) -> None:
        """Invoker with can_dispatch=True but target not in allowed_subagents is
        denied: status='failed', 'permission denied' in output (no raise)."""
        registry = _make_registry()
        registry.register(_invoker_config("caller", can_dispatch=True, allowed=["other_sub"]))
        registry.register(_subagent_config("target"))
        dispatcher = AgentDispatcher(registry, executor=_async_executor)

        task = AgentTask(
            task_id="t3",
            agent_name="target",
            description="do work",
            prompt="run it",
            invoker_name="caller",
        )
        result = _run(dispatcher.dispatch_one(task))

        assert result.status == "failed", f"Expected failed, got {result.status!r}"
        assert "permission denied" in result.output.lower(), (
            f"Expected 'permission denied' in output, got: {result.output!r}"
        )

    def test_permission_denied_message_names_invoker_and_target(self) -> None:
        """The denial message (in result.output) must name both invoker and target."""
        registry = _make_registry()
        registry.register(_invoker_config("caller", can_dispatch=False, allowed=[]))
        registry.register(_subagent_config("target"))
        dispatcher = AgentDispatcher(registry, executor=_async_executor)

        task = AgentTask(
            task_id="t-msg",
            agent_name="target",
            description="do work",
            prompt="run it",
            invoker_name="caller",
        )
        result = _run(dispatcher.dispatch_one(task))

        assert result.status == "failed", f"Expected failed, got {result.status!r}"
        assert "caller" in result.output, f"invoker not named: {result.output!r}"
        assert "target" in result.output, f"target not named: {result.output!r}"


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
            invoker_name="caller",
        )
        result = _run(dispatcher.dispatch_one(task))

        assert result.status == "completed", f"Expected completed, got {result.status!r}"
        assert "executed:target" in result.output


async def _cancelled_executor(task: AgentTask) -> str:
    """Executor that raises CancelledError, simulating cooperative cancellation
    (e.g. dispatch_many's timeout drain cancelling the underlying future)."""
    raise asyncio.CancelledError


async def _raising_executor(task: AgentTask) -> str:
    """Executor that raises an ordinary exception (a genuine task failure)."""
    raise RuntimeError("boom")


class TestDispatchOneCancellation:
    def test_cancelled_error_is_reraised_not_swallowed(self) -> None:
        """If the executed coroutine raises CancelledError, dispatch_one must
        re-raise it (propagate cancellation) rather than convert it into a
        status='failed' AgentTaskResult — otherwise graceful shutdown /
        dispatch_many timeout cancellation is masked as a genuine failure."""
        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        dispatcher = AgentDispatcher(registry, executor=_cancelled_executor)

        task = AgentTask(
            task_id="t-cancel",
            agent_name="worker",
            description="do work",
            prompt="run it",
        )

        raised = False
        try:
            _run(dispatcher.dispatch_one(task))
        except asyncio.CancelledError:
            raised = True

        assert raised, "CancelledError must propagate, not become a failed result"
        # The active-count finally-block must still have decremented.
        assert dispatcher.active_count == 0, (
            f"active_count leaked on cancellation: {dispatcher.active_count}"
        )

    def test_ordinary_exception_becomes_failed_result(self) -> None:
        """A non-cancellation exception must still be caught and converted into a
        status='failed' AgentTaskResult carrying the error message (the broad
        `except Exception` path is unchanged)."""
        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        dispatcher = AgentDispatcher(registry, executor=_raising_executor)

        task = AgentTask(
            task_id="t-fail",
            agent_name="worker",
            description="do work",
            prompt="run it",
        )
        result = _run(dispatcher.dispatch_one(task))

        assert result.status == "failed", f"Expected failed, got {result.status!r}"
        assert "boom" in result.output, f"Expected error text in output, got: {result.output!r}"
        assert dispatcher.active_count == 0, (
            f"active_count leaked on failure: {dispatcher.active_count}"
        )


class TestPromptTemplateParsedOnce:
    """P4 perf: the behavior 'prompt template' must be parsed/built once and
    reused across repeated dispatches of the same agent, not re-built per call.

    The per-dispatch prompt path is
    ``AgentRegistry.render_behavior_prompt`` -> ``BehaviorRenderer.render_as_prompt``
    -> ``BehaviorRenderer.render`` (the full multi-section string build). We spy
    on the actual build step (``_render_uncached``) and assert it fires exactly
    once across many renders of the same behavior, while the rendered output is
    byte-for-byte identical to the un-cached build."""

    def test_render_parsed_once_across_repeated_dispatches(self, monkeypatch) -> None:
        renderer = BehaviorRenderer()
        behavior = default_subagent_behavior()

        calls = {"n": 0}
        original = renderer._render_uncached

        def _counting_render(b):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            return original(b)

        monkeypatch.setattr(renderer, "_render_uncached", _counting_render)

        # Simulate many dispatches rendering the same agent's behavior.
        outputs = [renderer.render(behavior) for _ in range(5)]

        assert calls["n"] == 1, (
            f"behavior template re-parsed per call: built {calls['n']} times for "
            "5 renders of the same behavior (expected exactly 1)"
        )
        # Every render returns the same, unchanged body.
        assert len(set(outputs)) == 1, "cached renders diverged"

    def test_render_output_matches_uncached_build(self) -> None:
        """Caching must not alter output: cached render == direct build."""
        renderer = BehaviorRenderer()
        behavior = default_subagent_behavior()

        expected = renderer._render_uncached(behavior)
        # First call populates cache, second hits it — both must equal expected.
        assert renderer.render(behavior) == expected
        assert renderer.render(behavior) == expected

    def test_distinct_behaviors_not_conflated(self) -> None:
        """Different behaviors must render differently (cache keyed by content,
        not object identity) so two templates are never conflated."""
        renderer = BehaviorRenderer()
        primary = default_subagent_behavior()
        variant = default_subagent_behavior()
        variant.tdd_enforced = False  # changes the rendered body

        out_primary = renderer.render(primary)
        out_variant = renderer.render(variant)

        assert out_primary != out_variant, "distinct behaviors collided in cache"
        assert "TDD Policy" in out_primary
        assert "TDD Policy" not in out_variant

    def test_render_as_prompt_uses_cached_body(self, monkeypatch) -> None:
        """render_as_prompt (the dispatch-path entry) must reuse the cached body:
        the build step fires once even across differing agent_name/task headers."""
        renderer = BehaviorRenderer()
        behavior = default_subagent_behavior()

        calls = {"n": 0}
        original = renderer._render_uncached

        def _counting_render(b):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            return original(b)

        monkeypatch.setattr(renderer, "_render_uncached", _counting_render)

        p1 = renderer.render_as_prompt(behavior, agent_name="worker", task="task-a")
        p2 = renderer.render_as_prompt(behavior, agent_name="worker", task="task-b")

        assert calls["n"] == 1, (
            f"render_as_prompt re-built the body per call ({calls['n']}x); "
            "the invariant behavior body must be parsed once"
        )
        # Headers differ (different task) but the cached body tail is identical.
        body = original(behavior)
        assert p1.endswith(body) and p2.endswith(body)
        assert p1 != p2  # headers differ
