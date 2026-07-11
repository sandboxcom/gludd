"""D11 — Subagent orchestration defect guards.

Four guards tested:
- max_nesting_depth — refuse dispatch when depth exceeds config limit
- capability non-escalation — child AgentPermission must be subset of parent
- dispatch rate limiter — sliding-window throttle on dispatch frequency
- spiral detection — same-task redispatch counter with cutoff
"""

from __future__ import annotations

import asyncio
from typing import Any

from general_ludd.agents.dispatcher import AgentDispatcher, AgentTask
from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentType
from general_ludd.config.user_config import OrchestrationGuardConfig


def _make_registry() -> AgentRegistry:
    return AgentRegistry()


def _subagent_config(name: str, enabled: bool = True, **kwargs: Any) -> AgentConfig:
    defaults: dict[str, Any] = {
        "name": name,
        "description": f"Test subagent {name}",
        "type": AgentType.SUBAGENT,
        "permissions": AgentPermission(
            can_edit=False,
            can_bash=False,
            can_read=True,
            can_dispatch_subagents=False,
            allowed_subagents=[],
        ),
        "enabled": enabled,
    }
    defaults.update(kwargs)
    return AgentConfig(**defaults)


def _invoker_config(
    name: str,
    can_dispatch: bool,
    allowed: list[str],
    **kwargs: Any,
) -> AgentConfig:
    defaults: dict[str, Any] = {
        "name": name,
        "description": f"Test invoker {name}",
        "type": AgentType.PRIMARY,
        "permissions": AgentPermission(
            can_edit=False,
            can_bash=False,
            can_read=True,
            can_dispatch_subagents=can_dispatch,
            allowed_subagents=allowed,
        ),
        "enabled": True,
    }
    defaults.update(kwargs)
    return AgentConfig(**defaults)


async def _async_executor(task: AgentTask) -> str:
    return f"executed:{task.agent_name}"


_TRUSTED_INVOKER = "trusted-caller"


def _register_trusted_invoker(registry: AgentRegistry) -> str:
    registry.register(_invoker_config(_TRUSTED_INVOKER, can_dispatch=True, allowed=["*"]))
    return _TRUSTED_INVOKER


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1. Max nesting depth
# ---------------------------------------------------------------------------


class TestMaxNestingDepthEnforced:
    def test_depth_within_limit_succeeds(self) -> None:
        registry = _make_registry()
        invoker = _register_trusted_invoker(registry)
        registry.register(_subagent_config("worker"))
        guard = OrchestrationGuardConfig(max_nesting_depth=3)
        dispatcher = AgentDispatcher(
            registry, executor=_async_executor, orchestration_guard=guard
        )

        task = AgentTask(
            task_id="d1",
            agent_name="worker",
            description="do work",
            prompt="run it",
            invoker_name=invoker,
            depth=2,  # within limit of 3
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "completed", f"Expected completed, got {result.status!r}"

    def test_depth_exceeds_limit_refused(self) -> None:
        registry = _make_registry()
        invoker = _register_trusted_invoker(registry)
        registry.register(_subagent_config("worker"))
        guard = OrchestrationGuardConfig(max_nesting_depth=3)
        dispatcher = AgentDispatcher(
            registry, executor=_async_executor, orchestration_guard=guard
        )

        task = AgentTask(
            task_id="d2",
            agent_name="worker",
            description="do work",
            prompt="run it",
            invoker_name=invoker,
            depth=4,  # exceeds limit of 3
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "failed", f"Expected failed, got {result.status!r}"
        assert "nesting" in result.output.lower(), (
            f"Expected 'nesting' in output, got: {result.output!r}"
        )

    def test_depth_at_limit_refused(self) -> None:
        """Exceeding means strictly greater than the limit (limit is inclusive)."""
        registry = _make_registry()
        invoker = _register_trusted_invoker(registry)
        registry.register(_subagent_config("worker"))
        guard = OrchestrationGuardConfig(max_nesting_depth=2)
        dispatcher = AgentDispatcher(
            registry, executor=_async_executor, orchestration_guard=guard
        )

        task = AgentTask(
            task_id="d3",
            agent_name="worker",
            description="do work",
            prompt="run it",
            invoker_name=invoker,
            depth=3,  # exceeds limit of 2
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "failed", f"Expected failed, got {result.status!r}"

    def test_depth_default_zero_passes(self) -> None:
        """AgentTask with no explicit depth (defaults to 0) should pass."""
        registry = _make_registry()
        invoker = _register_trusted_invoker(registry)
        registry.register(_subagent_config("worker"))
        guard = OrchestrationGuardConfig(max_nesting_depth=3)
        dispatcher = AgentDispatcher(
            registry, executor=_async_executor, orchestration_guard=guard
        )

        task = AgentTask(
            task_id="d4",
            agent_name="worker",
            description="do work",
            prompt="run it",
            invoker_name=invoker,
            # depth not set → defaults to 0
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "completed", f"Expected completed, got {result.status!r}"


# ---------------------------------------------------------------------------
# 2. Capability non-escalation — child caps subset of parent
# ---------------------------------------------------------------------------


class TestChildCapsSubsetOfParent:
    def test_child_caps_equal_to_parent_succeeds(self) -> None:
        registry = _make_registry()
        invoker = _register_trusted_invoker(registry)
        invoker_cfg = registry.get(invoker)
        assert invoker_cfg is not None
        invoker_cfg.permissions.can_edit = True
        invoker_cfg.permissions.can_bash = True
        invoker_cfg.permissions.can_dispatch_subagents = True

        registry.register(_subagent_config("worker", permissions=AgentPermission(
            can_edit=True,
            can_bash=True,
            can_read=True,
            can_dispatch_subagents=False,
            allowed_subagents=[],
        )))
        guard = OrchestrationGuardConfig(enforce_capability_escalation=True)
        dispatcher = AgentDispatcher(
            registry, executor=_async_executor, orchestration_guard=guard
        )

        task = AgentTask(
            task_id="cc1",
            agent_name="worker",
            description="do work",
            prompt="run it",
            invoker_name=invoker,
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "completed", f"Expected completed, got {result.status!r}"

    def test_child_cap_exceeds_parent_refused(self) -> None:
        """Child has can_edit=True but parent does not → denied."""
        registry = _make_registry()
        registry.register(_invoker_config("parent", can_dispatch=True, allowed=["worker"],
                                          permissions=AgentPermission(
                                              can_edit=False,
                                              can_bash=False,
                                              can_read=True,
                                              can_dispatch_subagents=True,
                                              allowed_subagents=["worker"],
                                          )))
        registry.register(_subagent_config("worker", permissions=AgentPermission(
            can_edit=True,  # child has edit, parent does not
            can_bash=False,
            can_read=True,
            can_dispatch_subagents=False,
            allowed_subagents=[],
        )))
        guard = OrchestrationGuardConfig(enforce_capability_escalation=True)
        dispatcher = AgentDispatcher(
            registry, executor=_async_executor, orchestration_guard=guard
        )

        task = AgentTask(
            task_id="cc2",
            agent_name="worker",
            description="do work",
            prompt="run it",
            invoker_name="parent",
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "failed", f"Expected failed, got {result.status!r}"
        assert "capability" in result.output.lower() or "escalat" in result.output.lower(), (
            f"Expected 'capability'/'escalation' in output, got: {result.output!r}"
        )

    def test_child_can_dispatch_parent_cannot_refused(self) -> None:
        """Child has can_dispatch_subagents=True but parent does not → denied."""
        registry = _make_registry()
        registry.register(_invoker_config("parent", can_dispatch=True, allowed=["worker"],
                                          permissions=AgentPermission(
                                              can_edit=False,
                                              can_bash=False,
                                              can_read=True,
                                              can_dispatch_subagents=True,  # parent can dispatch
                                              allowed_subagents=["worker"],
                                          )))
        # Parent CAN dispatch subagents to invoke this target, but the parent
        # itself is the one whose caps we compare: the invoker's own
        # can_dispatch_subagents is True (to pass can_invoke), but the child
        # has it True too (which is fine since parent also has it).
        registry.register(_subagent_config("worker", permissions=AgentPermission(
            can_edit=False,
            can_bash=False,
            can_read=True,
            can_dispatch_subagents=True,  # child can also dispatch — parent has it too
            allowed_subagents=[],
        )))
        guard = OrchestrationGuardConfig(enforce_capability_escalation=True)
        dispatcher = AgentDispatcher(
            registry, executor=_async_executor, orchestration_guard=guard
        )

        task = AgentTask(
            task_id="cc3",
            agent_name="worker",
            description="do work",
            prompt="run it",
            invoker_name="parent",
        )
        result = _run(dispatcher.dispatch_one(task))
        # parent has can_dispatch_subagents=True, child has it too — allowed
        assert result.status == "completed", f"Expected completed, got {result.status!r}"

    def test_escalation_check_skipped_when_disabled(self) -> None:
        """When enforce_capability_escalation=False, the check is skipped."""
        registry = _make_registry()
        registry.register(_invoker_config("parent", can_dispatch=True, allowed=["worker"],
                                          permissions=AgentPermission(
                                              can_edit=False,
                                              can_bash=False,
                                              can_read=True,
                                              can_dispatch_subagents=True,
                                              allowed_subagents=["worker"],
                                          )))
        registry.register(_subagent_config("worker", permissions=AgentPermission(
            can_edit=True,  # exceeds parent, but check is disabled
            can_bash=False,
            can_read=True,
            can_dispatch_subagents=False,
            allowed_subagents=[],
        )))
        guard = OrchestrationGuardConfig(enforce_capability_escalation=False)
        dispatcher = AgentDispatcher(
            registry, executor=_async_executor, orchestration_guard=guard
        )

        task = AgentTask(
            task_id="cc4",
            agent_name="worker",
            description="do work",
            prompt="run it",
            invoker_name="parent",
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "completed", f"Should succeed when escalation check disabled, got {result.status!r}"

    def test_no_invoker_bypasses_escalation_check(self) -> None:
        """When there is no invoker, the permission gate already denies it.
        The escalation check is skipped since there's no parent to compare against."""
        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        guard = OrchestrationGuardConfig(enforce_capability_escalation=True)
        dispatcher = AgentDispatcher(
            registry, executor=_async_executor, orchestration_guard=guard
        )

        task = AgentTask(
            task_id="cc5",
            agent_name="worker",
            description="do work",
            prompt="run it",
            # no invoker → permission gate denies it before escalation check
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "failed", f"Expected failed, got {result.status!r}"
        assert "permission denied" in result.output.lower()


# ---------------------------------------------------------------------------
# 3. Dispatch rate limiter
# ---------------------------------------------------------------------------


class TestDispatchRateLimited:
    def test_single_dispatch_within_window_succeeds(self) -> None:
        registry = _make_registry()
        invoker = _register_trusted_invoker(registry)
        registry.register(_subagent_config("worker"))
        guard = OrchestrationGuardConfig(max_dispatches_per_window=10, dispatch_rate_window_s=60.0)
        dispatcher = AgentDispatcher(
            registry, executor=_async_executor, orchestration_guard=guard
        )

        task = AgentTask(
            task_id="r1",
            agent_name="worker",
            description="do work",
            prompt="run it",
            invoker_name=invoker,
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "completed", f"Expected completed, got {result.status!r}"

    def test_rapid_dispatch_hits_rate_limiter(self) -> None:
        """Dispatching more than max_dispatches_per_window in a short window is blocked."""
        registry = _make_registry()
        invoker = _register_trusted_invoker(registry)
        registry.register(_subagent_config("worker"))
        guard = OrchestrationGuardConfig(max_dispatches_per_window=3, dispatch_rate_window_s=60.0)
        dispatcher = AgentDispatcher(
            registry, executor=_async_executor, orchestration_guard=guard
        )

        # First 3 should succeed
        for i in range(3):
            task = AgentTask(
                task_id=f"r-fast-{i}",
                agent_name="worker",
                description="do work",
                prompt="run it",
                invoker_name=invoker,
            )
            result = _run(dispatcher.dispatch_one(task))
            assert result.status == "completed", f"Dispatch {i} failed: {result.output!r}"

        # 4th should be rate-limited
        task = AgentTask(
            task_id="r-fast-over",
            agent_name="worker",
            description="do work",
            prompt="run it",
            invoker_name=invoker,
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "failed", f"Expected rate-limited failed, got {result.status!r}"
        assert "rate" in result.output.lower(), (
            f"Expected 'rate' in output, got: {result.output!r}"
        )

    def test_rate_limiter_disabled_by_zero_max(self) -> None:
        """When max_dispatches_per_window=0, no rate limiting."""
        registry = _make_registry()
        invoker = _register_trusted_invoker(registry)
        registry.register(_subagent_config("worker"))
        guard = OrchestrationGuardConfig(max_dispatches_per_window=0, dispatch_rate_window_s=60.0)
        dispatcher = AgentDispatcher(
            registry, executor=_async_executor, orchestration_guard=guard
        )

        for i in range(20):
            task = AgentTask(
                task_id=f"r-off-{i}",
                agent_name="worker",
                description="do work",
                prompt="run it",
                invoker_name=invoker,
            )
            result = _run(dispatcher.dispatch_one(task))
            assert result.status == "completed", f"Dispatch {i} should succeed when limiter disabled"

    def test_rate_window_expires_allows_new_dispatch(self) -> None:
        """Manually clearing the rate window (simulating time passage) allows new dispatch."""
        registry = _make_registry()
        invoker = _register_trusted_invoker(registry)
        registry.register(_subagent_config("worker"))
        guard = OrchestrationGuardConfig(max_dispatches_per_window=3, dispatch_rate_window_s=60.0)
        dispatcher = AgentDispatcher(
            registry, executor=_async_executor, orchestration_guard=guard
        )

        for i in range(3):
            task = AgentTask(
                task_id=f"r-exp-{i}",
                agent_name="worker",
                description="do work",
                prompt="run it",
                invoker_name=invoker,
            )
            _run(dispatcher.dispatch_one(task))

        # Manually expire the window by clearing the timestamp list
        dispatcher._rate_limiter_timestamps.clear()

        task = AgentTask(
            task_id="r-exp-4",
            agent_name="worker",
            description="do work",
            prompt="run it",
            invoker_name=invoker,
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "completed", f"Should succeed after window clear, got {result.status!r}"


# ---------------------------------------------------------------------------
# 4. Spiral detection — same-task redispatch counter with cutoff
# ---------------------------------------------------------------------------


class TestSpiralDetectionCutoff:
    def test_first_dispatch_succeeds(self) -> None:
        registry = _make_registry()
        invoker = _register_trusted_invoker(registry)
        registry.register(_subagent_config("worker"))
        guard = OrchestrationGuardConfig(max_redispatch_count=5)
        dispatcher = AgentDispatcher(
            registry, executor=_async_executor, orchestration_guard=guard
        )

        task = AgentTask(
            task_id="sp1",
            agent_name="worker",
            description="do work",
            prompt="run it",
            invoker_name=invoker,
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "completed", f"Expected completed, got {result.status!r}"

    def test_same_task_redispatched_up_to_limit_succeeds(self) -> None:
        """Re-dispatching the same task_id up to max_redispatch is allowed."""
        registry = _make_registry()
        invoker = _register_trusted_invoker(registry)
        registry.register(_subagent_config("worker"))
        guard = OrchestrationGuardConfig(max_redispatch_count=3)
        dispatcher = AgentDispatcher(
            registry, executor=_async_executor, orchestration_guard=guard
        )

        for i in range(3):
            task = AgentTask(
                task_id="sp2",  # same task_id every time
                agent_name="worker",
                description="do work",
                prompt="run it",
                invoker_name=invoker,
            )
            result = _run(dispatcher.dispatch_one(task))
            assert result.status == "completed", f"Redispatch {i} should succeed, got {result.status!r}"

    def test_same_task_exceeds_redispatch_limit_blocked(self) -> None:
        """After max_redispatch_count dispatches of the same task_id, the next is blocked."""
        registry = _make_registry()
        invoker = _register_trusted_invoker(registry)
        registry.register(_subagent_config("worker"))
        guard = OrchestrationGuardConfig(max_redispatch_count=3)
        dispatcher = AgentDispatcher(
            registry, executor=_async_executor, orchestration_guard=guard
        )

        for _i in range(3):
            task = AgentTask(
                task_id="sp3",
                agent_name="worker",
                description="do work",
                prompt="run it",
                invoker_name=invoker,
            )
            _run(dispatcher.dispatch_one(task))

        # 4th dispatch of same task_id → spiral detected
        task = AgentTask(
            task_id="sp3",
            agent_name="worker",
            description="do work",
            prompt="run it",
            invoker_name=invoker,
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "failed", f"Expected spiral detection failed, got {result.status!r}"
        assert "spiral" in result.output.lower(), (
            f"Expected 'spiral' in output, got: {result.output!r}"
        )

    def test_different_task_ids_independent(self) -> None:
        """Spiral counter is per-task_id; different tasks don't interfere."""
        registry = _make_registry()
        invoker = _register_trusted_invoker(registry)
        registry.register(_subagent_config("worker"))
        guard = OrchestrationGuardConfig(max_redispatch_count=2)
        dispatcher = AgentDispatcher(
            registry, executor=_async_executor, orchestration_guard=guard
        )

        for _i in range(2):
            task = AgentTask(
                task_id="sp4a",
                agent_name="worker",
                description="do work",
                prompt="run it",
                invoker_name=invoker,
            )
            _run(dispatcher.dispatch_one(task))

        # sp4a now at limit, but sp4b is independent
        task = AgentTask(
            task_id="sp4b",
            agent_name="worker",
            description="do work",
            prompt="run it",
            invoker_name=invoker,
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "completed", f"Different task_id should be independent, got {result.status!r}"

        # sp4a's 3rd dispatch should still be blocked
        task = AgentTask(
            task_id="sp4a",
            agent_name="worker",
            description="do work",
            prompt="run it",
            invoker_name=invoker,
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "failed", f"sp4a should hit spiral limit, got {result.status!r}"

    def test_spiral_detection_disabled_by_zero_max(self) -> None:
        """When max_redispatch_count=0, no spiral detection."""
        registry = _make_registry()
        invoker = _register_trusted_invoker(registry)
        registry.register(_subagent_config("worker"))
        guard = OrchestrationGuardConfig(max_redispatch_count=0)
        dispatcher = AgentDispatcher(
            registry, executor=_async_executor, orchestration_guard=guard
        )

        for i in range(10):
            task = AgentTask(
                task_id="sp5",
                agent_name="worker",
                description="do work",
                prompt="run it",
                invoker_name=invoker,
            )
            result = _run(dispatcher.dispatch_one(task))
            assert result.status == "completed", (
                f"Dispatch {i} should succeed when spiral disabled, "
                f"got {result.status!r}"
            )
