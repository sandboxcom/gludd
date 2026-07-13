"""D.11 subagent orchestration defect defenses — unit tests.

Four dispatch-time guardrails:
  1. max_nesting_depth — refuse dispatches deeper than the limit
  2. capability_escalation — child may not hold permissions the parent lacks
  3. rate_limiter — sliding-window dispatch-rate cap
  4. spiral_detection — per-task redispatch cutoff
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from general_ludd.agents.dispatcher import AgentDispatcher, AgentTask
from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentType
from general_ludd.config.user_config import OrchestrationGuardConfig


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_registry() -> AgentRegistry:
    return AgentRegistry()


def _subagent_config(name: str, enabled: bool = True) -> AgentConfig:
    return AgentConfig(
        name=name,
        description=f"Test subagent {name}",
        type=AgentType.SUBAGENT,
        permissions=AgentPermission(
            can_edit=False, can_bash=False, can_read=True,
            can_dispatch_subagents=False, allowed_subagents=[],
        ),
        enabled=enabled,
    )


def _invoker_config(
    name: str,
    *,
    can_edit: bool = False,
    can_bash: bool = False,
    can_read: bool = True,
    can_dispatch: bool = True,
    allowed: list[str] | None = None,
) -> AgentConfig:
    return AgentConfig(
        name=name,
        description=f"Test invoker {name}",
        type=AgentType.PRIMARY,
        permissions=AgentPermission(
            can_edit=can_edit,
            can_bash=can_bash,
            can_read=can_read,
            can_dispatch_subagents=can_dispatch,
            allowed_subagents=allowed if allowed is not None else ["*"],
        ),
        enabled=True,
    )


async def _async_executor(task: AgentTask) -> str:
    return f"executed:{task.agent_name}"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


_TRUSTED_INVOKER = "trusted-caller"


def _register_trusted_invoker(registry: AgentRegistry) -> str:
    registry.register(
        _invoker_config(_TRUSTED_INVOKER, can_dispatch=True, allowed=["*"])
    )
    return _TRUSTED_INVOKER


def _make_dispatcher(
    registry: AgentRegistry,
    guard: OrchestrationGuardConfig | None = None,
) -> AgentDispatcher:
    return AgentDispatcher(
        registry, executor=_async_executor, orchestration_guard=guard,
    )


# ── 1. max_nesting_depth ──────────────────────────────────────────────────────

class TestMaxNestingDepth:
    def test_depth_within_limit_succeeds(self) -> None:
        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)
        guard = OrchestrationGuardConfig(max_nesting_depth=3)
        dispatcher = _make_dispatcher(registry, guard=guard)

        task = AgentTask(
            task_id="nd-1", agent_name="worker", description="d", prompt="p",
            invoker_name=invoker, depth=3,
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "completed"

    def test_depth_exceeds_limit_is_denied(self) -> None:
        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)
        guard = OrchestrationGuardConfig(max_nesting_depth=3)
        dispatcher = _make_dispatcher(registry, guard=guard)

        task = AgentTask(
            task_id="nd-2", agent_name="worker", description="d", prompt="p",
            invoker_name=invoker, depth=4,
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "failed"
        assert "nesting depth" in result.output.lower()

    def test_depth_zero_limit_disabled(self) -> None:
        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)
        guard = OrchestrationGuardConfig(max_nesting_depth=0)
        dispatcher = _make_dispatcher(registry, guard=guard)

        task = AgentTask(
            task_id="nd-3", agent_name="worker", description="d", prompt="p",
            invoker_name=invoker, depth=99,
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "completed"

    def test_no_guard_config_passes_through(self) -> None:
        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)
        dispatcher = _make_dispatcher(registry, guard=None)

        task = AgentTask(
            task_id="nd-4", agent_name="worker", description="d", prompt="p",
            invoker_name=invoker, depth=99,
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "completed"


# ── 2. capability_escalation ──────────────────────────────────────────────────

class TestCapabilityEscalation:
    def test_child_has_edit_parent_does_not_is_denied(self) -> None:
        """Parent has can_edit=False; child has can_edit=True → denied."""
        registry = _make_registry()
        registry.register(_invoker_config(
            "caller", can_edit=False, can_bash=False, can_read=True,
            can_dispatch=True, allowed=["*"],
        ))
        registry.register(AgentConfig(
            name="worker", description="d", type=AgentType.SUBAGENT,
            permissions=AgentPermission(
                can_edit=True, can_bash=False, can_read=True,
                can_dispatch_subagents=False, allowed_subagents=[],
            ),
        ))
        guard = OrchestrationGuardConfig(enforce_capability_escalation=True)
        dispatcher = _make_dispatcher(registry, guard=guard)

        task = AgentTask(
            task_id="ce-1", agent_name="worker", description="d", prompt="p",
            invoker_name="caller",
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "failed"
        assert "capability escalation" in result.output.lower()
        assert "can_edit" in result.output

    def test_child_has_bash_parent_does_not_is_denied(self) -> None:
        """Parent has can_bash=False; child has can_bash=True → denied."""
        registry = _make_registry()
        registry.register(_invoker_config(
            "caller", can_edit=False, can_bash=False, can_read=True,
            can_dispatch=True, allowed=["*"],
        ))
        registry.register(AgentConfig(
            name="worker", description="d", type=AgentType.SUBAGENT,
            permissions=AgentPermission(
                can_edit=False, can_bash=True, can_read=True,
                can_dispatch_subagents=False, allowed_subagents=[],
            ),
        ))
        guard = OrchestrationGuardConfig(enforce_capability_escalation=True)
        dispatcher = _make_dispatcher(registry, guard=guard)

        task = AgentTask(
            task_id="ce-2", agent_name="worker", description="d", prompt="p",
            invoker_name="caller",
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "failed"
        assert "can_bash" in result.output

    def test_child_has_read_parent_does_not_is_denied(self) -> None:
        """Parent has can_read=False; child has can_read=True → denied."""
        registry = _make_registry()
        registry.register(_invoker_config(
            "caller", can_edit=False, can_bash=False, can_read=False,
            can_dispatch=True, allowed=["*"],
        ))
        registry.register(AgentConfig(
            name="worker", description="d", type=AgentType.SUBAGENT,
            permissions=AgentPermission(
                can_edit=False, can_bash=False, can_read=True,
                can_dispatch_subagents=False, allowed_subagents=[],
            ),
        ))
        guard = OrchestrationGuardConfig(enforce_capability_escalation=True)
        dispatcher = _make_dispatcher(registry, guard=guard)

        task = AgentTask(
            task_id="ce-3", agent_name="worker", description="d", prompt="p",
            invoker_name="caller",
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "failed"
        assert "can_read" in result.output

    def test_child_has_dispatch_parent_does_not_is_denied(self) -> None:
        """Parent has can_dispatch_subagents=False → can_invoke blocks first.
        The escalation guard covers the same field, but can_invoke (invocation
        gate) fires before escalation — both assert correct failure."""
        registry = _make_registry()
        registry.register(_invoker_config(
            "caller", can_edit=False, can_bash=False, can_read=True,
            can_dispatch=False, allowed=["*"],
        ))
        registry.register(AgentConfig(
            name="worker", description="d", type=AgentType.SUBAGENT,
            permissions=AgentPermission(
                can_edit=False, can_bash=False, can_read=True,
                can_dispatch_subagents=True, allowed_subagents=[],
            ),
        ))
        guard = OrchestrationGuardConfig(enforce_capability_escalation=True)
        dispatcher = _make_dispatcher(registry, guard=guard)

        task = AgentTask(
            task_id="ce-4", agent_name="worker", description="d", prompt="p",
            invoker_name="caller",
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "failed"
        # can_invoke denies before escalation; either message is correct
        assert "permission denied" in result.output.lower() or (
            "can_dispatch_subagents" in result.output
        )

    def test_allowed_subagent_not_in_parent_list_is_denied(self) -> None:
        """Child's allowed_subagents includes an agent parent does not allow.
        Parent can invoke "worker" but does NOT permit "c" — escalation fires."""
        registry = _make_registry()
        registry.register(_invoker_config(
            "caller", can_dispatch=True, allowed=["worker", "a", "b"],
        ))
        registry.register(AgentConfig(
            name="worker", description="d", type=AgentType.SUBAGENT,
            permissions=AgentPermission(
                can_edit=False, can_bash=False, can_read=True,
                can_dispatch_subagents=True, allowed_subagents=["c"],
            ),
        ))
        guard = OrchestrationGuardConfig(enforce_capability_escalation=True)
        dispatcher = _make_dispatcher(registry, guard=guard)

        task = AgentTask(
            task_id="ce-5", agent_name="worker", description="d", prompt="p",
            invoker_name="caller",
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "failed"
        assert "allowed_subagent:c" in result.output

    def test_parent_star_allows_all_child_subagents(self) -> None:
        """Parent with '*' in allowed_subagents permits any child subagent,
        AND parent has all caps so no escalation violations."""
        registry = _make_registry()
        registry.register(_invoker_config(
            "caller", can_dispatch=True, allowed=["*"],
            can_edit=True, can_bash=True, can_read=True,
        ))
        registry.register(AgentConfig(
            name="worker", description="d", type=AgentType.SUBAGENT,
            permissions=AgentPermission(
                can_edit=True, can_bash=True, can_read=True,
                can_dispatch_subagents=True, allowed_subagents=["x", "y", "z"],
            ),
        ))
        guard = OrchestrationGuardConfig(enforce_capability_escalation=True)
        dispatcher = _make_dispatcher(registry, guard=guard)

        task = AgentTask(
            task_id="ce-6", agent_name="worker", description="d", prompt="p",
            invoker_name="caller",
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "completed"

    def test_identical_permissions_succeeds(self) -> None:
        """Child with same permissions as parent is allowed."""
        registry = _make_registry()
        registry.register(_invoker_config(
            "caller", can_edit=True, can_bash=True, can_read=True,
            can_dispatch=True, allowed=["*"],
        ))
        registry.register(AgentConfig(
            name="worker", description="d", type=AgentType.SUBAGENT,
            permissions=AgentPermission(
                can_edit=True, can_bash=True, can_read=True,
                can_dispatch_subagents=True, allowed_subagents=[],
            ),
        ))
        guard = OrchestrationGuardConfig(enforce_capability_escalation=True)
        dispatcher = _make_dispatcher(registry, guard=guard)

        task = AgentTask(
            task_id="ce-7", agent_name="worker", description="d", prompt="p",
            invoker_name="caller",
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "completed"

    def test_escalation_disabled_via_config(self) -> None:
        """When enforce_capability_escalation=False, escalation is not checked."""
        registry = _make_registry()
        registry.register(_invoker_config(
            "caller", can_edit=False, can_dispatch=True, allowed=["*"],
        ))
        registry.register(AgentConfig(
            name="worker", description="d", type=AgentType.SUBAGENT,
            permissions=AgentPermission(
                can_edit=True, can_bash=False, can_read=True,
            ),
        ))
        guard = OrchestrationGuardConfig(enforce_capability_escalation=False)
        dispatcher = _make_dispatcher(registry, guard=guard)

        task = AgentTask(
            task_id="ce-8", agent_name="worker", description="d", prompt="p",
            invoker_name="caller",
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "completed"

    def test_no_guard_missing_invoker_skipped(self) -> None:
        """If guard is None, escalation check is skipped."""
        registry = _make_registry()
        registry.register(_invoker_config(
            "caller", can_edit=False, can_dispatch=True, allowed=["*"],
        ))
        registry.register(AgentConfig(
            name="worker", description="d", type=AgentType.SUBAGENT,
            permissions=AgentPermission(can_edit=True),
        ))
        dispatcher = _make_dispatcher(registry, guard=None)

        task = AgentTask(
            task_id="ce-9", agent_name="worker", description="d", prompt="p",
            invoker_name="caller",
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "completed"

    def test_no_invoker_skipped(self) -> None:
        """Empty invoker_name → escalation check is skipped (permission-denied
        logic handles this separately)."""
        registry = _make_registry()
        registry.register(AgentConfig(
            name="worker", description="d", type=AgentType.SUBAGENT,
            permissions=AgentPermission(
                can_edit=True, can_bash=True,
            ),
        ))
        guard = OrchestrationGuardConfig(enforce_capability_escalation=True)
        dispatcher = _make_dispatcher(registry, guard=guard)

        task = AgentTask(
            task_id="ce-10", agent_name="worker", description="d", prompt="p",
        )
        result = _run(dispatcher.dispatch_one(task))
        # permission-denied beats escalation
        assert result.status == "failed"
        assert "permission denied" in result.output.lower()

    def test_unknown_parent_registry_miss_skipped(self) -> None:
        """If invoker is unknown, can_invoke denies before escalation runs.
        The escalation guard's own parent-is-None skip is defensive code for
        internal consistency — can_invoke catches this first."""
        registry = _make_registry()
        registry.register(AgentConfig(
            name="worker", description="d", type=AgentType.SUBAGENT,
            permissions=AgentPermission(can_edit=True),
        ))
        guard = OrchestrationGuardConfig(enforce_capability_escalation=True)
        dispatcher = _make_dispatcher(registry, guard=guard)

        task = AgentTask(
            task_id="ce-11", agent_name="worker", description="d", prompt="p",
            invoker_name="unknown-parent",
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "failed"
        assert "permission denied" in result.output.lower()


# ── 3. rate_limiter ───────────────────────────────────────────────────────────

class TestRateLimiter:
    def test_dispatch_within_limit_succeeds(self) -> None:
        """Dispatches within the window limit are allowed."""
        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)
        guard = OrchestrationGuardConfig(
            max_dispatches_per_window=5, dispatch_rate_window_s=60.0,
        )
        dispatcher = _make_dispatcher(registry, guard=guard)

        for i in range(5):
            task = AgentTask(
                task_id=f"rl-{i}", agent_name="worker", description="d", prompt="p",
                invoker_name=invoker,
            )
            result = _run(dispatcher.dispatch_one(task))
            assert result.status == "completed", f"dispatch {i} failed"

    def test_dispatch_exceeds_limit_is_rate_limited(self) -> None:
        """The (N+1)th dispatch in a window is rate-limited."""
        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)
        guard = OrchestrationGuardConfig(
            max_dispatches_per_window=3, dispatch_rate_window_s=60.0,
        )
        dispatcher = _make_dispatcher(registry, guard=guard)

        # Fill the window.
        for i in range(3):
            task = AgentTask(
                task_id=f"rl-a-{i}", agent_name="worker", description="d",
                prompt="p", invoker_name=invoker,
            )
            result = _run(dispatcher.dispatch_one(task))
            assert result.status == "completed"

        # The next one is rate-limited.
        task = AgentTask(
            task_id="rl-exceed", agent_name="worker", description="d", prompt="p",
            invoker_name=invoker,
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "failed"
        assert "rate limited" in result.output.lower()

    def test_rate_limit_zero_disabled(self) -> None:
        """max_dispatches_per_window=0 disables the rate limiter."""
        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)
        guard = OrchestrationGuardConfig(
            max_dispatches_per_window=0, dispatch_rate_window_s=60.0,
        )
        dispatcher = _make_dispatcher(registry, guard=guard)

        for i in range(10):
            task = AgentTask(
                task_id=f"rl-d-{i}", agent_name="worker", description="d",
                prompt="p", invoker_name=invoker,
            )
            result = _run(dispatcher.dispatch_one(task))
            assert result.status == "completed"

    def test_rate_limit_message_includes_counts(self) -> None:
        """Rate-limit output names the current count, window, and limit."""
        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)
        guard = OrchestrationGuardConfig(
            max_dispatches_per_window=1, dispatch_rate_window_s=60.0,
        )
        dispatcher = _make_dispatcher(registry, guard=guard)

        _run(dispatcher.dispatch_one(AgentTask(
            task_id="rl-msg-1", agent_name="worker", description="d", prompt="p",
            invoker_name=invoker,
        )))
        result = _run(dispatcher.dispatch_one(AgentTask(
            task_id="rl-msg-2", agent_name="worker", description="d", prompt="p",
            invoker_name=invoker,
        )))
        assert "2" in result.output
        assert "60" in result.output or "6" in result.output
        assert "1" in result.output or "limit" in result.output.lower()

    def test_no_guard_skips_rate_limit(self) -> None:
        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)
        dispatcher = _make_dispatcher(registry, guard=None)

        for i in range(20):
            task = AgentTask(
                task_id=f"rl-ng-{i}", agent_name="worker", description="d",
                prompt="p", invoker_name=invoker,
            )
            result = _run(dispatcher.dispatch_one(task))
            assert result.status == "completed"


# ── 4. spiral_detection ──────────────────────────────────────────────────────

class TestSpiralDetection:
    def test_first_dispatch_succeeds(self) -> None:
        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)
        guard = OrchestrationGuardConfig(max_redispatch_count=5)
        dispatcher = _make_dispatcher(registry, guard=guard)

        task = AgentTask(
            task_id="sp-1", agent_name="worker", description="d", prompt="p",
            invoker_name=invoker,
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "completed"

    def test_repeated_redispatch_up_to_limit_succeeds(self) -> None:
        """Same task_id dispatched up to max_redispatch_count times succeeds."""
        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)
        guard = OrchestrationGuardConfig(max_redispatch_count=5)
        dispatcher = _make_dispatcher(registry, guard=guard)

        for i in range(1, 6):  # 5 dispatches = at the limit
            task = AgentTask(
                task_id="sp-repeat", agent_name="worker", description="d",
                prompt="p", invoker_name=invoker,
            )
            result = _run(dispatcher.dispatch_one(task))
            assert result.status == "completed", f"dispatch {i} failed"

    def test_redispatch_exceeds_limit_is_blocked(self) -> None:
        """The (N+1)th redispatch of the same task_id is blocked."""
        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)
        guard = OrchestrationGuardConfig(max_redispatch_count=3)
        dispatcher = _make_dispatcher(registry, guard=guard)

        for _ in range(3):
            _run(dispatcher.dispatch_one(AgentTask(
                task_id="sp-block", agent_name="worker", description="d",
                prompt="p", invoker_name=invoker,
            )))

        result = _run(dispatcher.dispatch_one(AgentTask(
            task_id="sp-block", agent_name="worker", description="d",
            prompt="p", invoker_name=invoker,
        )))
        assert result.status == "failed"
        assert "spiral" in result.output.lower()
        assert "sp-block" in result.output

    def test_spiral_message_includes_count_and_limit(self) -> None:
        """Spiral-block output names the task id, count, and limit."""
        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)
        guard = OrchestrationGuardConfig(max_redispatch_count=2)
        dispatcher = _make_dispatcher(registry, guard=guard)

        for _ in range(2):
            _run(dispatcher.dispatch_one(AgentTask(
                task_id="sp-msg", agent_name="worker", description="d",
                prompt="p", invoker_name=invoker,
            )))

        result = _run(dispatcher.dispatch_one(AgentTask(
            task_id="sp-msg", agent_name="worker", description="d",
            prompt="p", invoker_name=invoker,
        )))
        assert "sp-msg" in result.output
        assert "3" in result.output
        assert "2" in result.output

    def test_different_task_ids_have_independent_spiral_counters(self) -> None:
        """Each task_id has its own redispatch counter."""
        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)
        guard = OrchestrationGuardConfig(max_redispatch_count=2)
        dispatcher = _make_dispatcher(registry, guard=guard)

        # Exhaust spiral limit for id-a.
        for _ in range(3):
            _run(dispatcher.dispatch_one(AgentTask(
                task_id="id-a", agent_name="worker", description="d",
                prompt="p", invoker_name=invoker,
            )))

        # A fresh id-b is still allowed.
        result = _run(dispatcher.dispatch_one(AgentTask(
            task_id="id-b", agent_name="worker", description="d",
            prompt="p", invoker_name=invoker,
        )))
        assert result.status == "completed"

    def test_spiral_limit_zero_disabled(self) -> None:
        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)
        guard = OrchestrationGuardConfig(max_redispatch_count=0)
        dispatcher = _make_dispatcher(registry, guard=guard)

        for _ in range(20):
            result = _run(dispatcher.dispatch_one(AgentTask(
                task_id="sp-off", agent_name="worker", description="d",
                prompt="p", invoker_name=invoker,
            )))
            assert result.status == "completed"

    def test_no_guard_spiral_not_checked(self) -> None:
        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)
        dispatcher = _make_dispatcher(registry, guard=None)

        for _ in range(20):
            result = _run(dispatcher.dispatch_one(AgentTask(
                task_id="sp-no-g", agent_name="worker", description="d",
                prompt="p", invoker_name=invoker,
            )))
            assert result.status == "completed"


# ── 5. integration: guard ordering and precedence ─────────────────────────────

class TestGuardOrdering:
    def test_nesting_depth_runs_before_executor(self) -> None:
        """Nesting depth is checked before the executor runs."""
        ran = {"called": False}

        async def _tracking_executor(task: AgentTask) -> str:
            ran["called"] = True
            return "ok"

        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)
        guard = OrchestrationGuardConfig(max_nesting_depth=0)
        dispatcher = AgentDispatcher(
            registry, executor=_tracking_executor, orchestration_guard=guard,
        )
        dispatcher._orchestration_guard = OrchestrationGuardConfig(
            max_nesting_depth=3,
        )

        task = AgentTask(
            task_id="go-1", agent_name="worker", description="d", prompt="p",
            invoker_name=invoker, depth=99,
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "failed"
        assert ran["called"] is False

    def test_escalation_runs_before_executor(self) -> None:
        """Capability escalation is checked before the executor runs."""
        ran = {"called": False}

        async def _tracking_executor(task: AgentTask) -> str:
            ran["called"] = True
            return "ok"

        registry = _make_registry()
        registry.register(_invoker_config(
            "caller", can_edit=False, can_dispatch=True, allowed=["*"],
        ))
        registry.register(AgentConfig(
            name="worker", description="d", type=AgentType.SUBAGENT,
            permissions=AgentPermission(can_edit=True),
        ))
        guard = OrchestrationGuardConfig(enforce_capability_escalation=True)
        dispatcher = AgentDispatcher(
            registry, executor=_tracking_executor, orchestration_guard=guard,
        )

        task = AgentTask(
            task_id="go-2", agent_name="worker", description="d", prompt="p",
            invoker_name="caller",
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "failed"
        assert ran["called"] is False

    def test_all_guards_block_before_executor(self) -> None:
        """When multiple guards all reject, the first one (nesting depth) wins
        and the executor never runs."""
        ran = {"called": False}

        async def _tracking_executor(task: AgentTask) -> str:
            ran["called"] = True
            return "ok"

        registry = _make_registry()
        registry.register(_invoker_config(
            "caller", can_edit=False, can_dispatch=True, allowed=["*"],
        ))
        registry.register(AgentConfig(
            name="worker", description="d", type=AgentType.SUBAGENT,
            permissions=AgentPermission(can_edit=True),
        ))
        guard = OrchestrationGuardConfig(
            max_nesting_depth=3,
            enforce_capability_escalation=True,
        )
        dispatcher = AgentDispatcher(
            registry, executor=_tracking_executor, orchestration_guard=guard,
        )

        task = AgentTask(
            task_id="go-3", agent_name="worker", description="d", prompt="p",
            invoker_name="caller", depth=99,
        )
        result = _run(dispatcher.dispatch_one(task))
        assert result.status == "failed"
        assert ran["called"] is False
        assert "nesting depth" in result.output.lower()

    def test_escalation_trumps_spiral_when_both_block(self) -> None:
        """When nesting depth passes but escalation fails, escalation wins."""
        ran = {"called": False}

        async def _tracking_executor(task: AgentTask) -> str:
            ran["called"] = True
            return "ok"

        registry = _make_registry()
        registry.register(_invoker_config(
            "caller", can_edit=False, can_dispatch=True, allowed=["*"],
        ))
        registry.register(AgentConfig(
            name="worker", description="d", type=AgentType.SUBAGENT,
            permissions=AgentPermission(can_edit=True),
        ))
        guard = OrchestrationGuardConfig(
            enforce_capability_escalation=True, max_redispatch_count=1,
        )
        dispatcher = AgentDispatcher(
            registry, executor=_tracking_executor, orchestration_guard=guard,
        )

        # Dispatch once (fills spiral counter for this id).
        _run(dispatcher.dispatch_one(AgentTask(
            task_id="go-4", agent_name="worker", description="d", prompt="p",
            invoker_name="caller",
        )))
        # Second dispatch: both escalaton (can_edit) and spiral (2 > 1) block.
        # Escalation runs first in dispatch_one → wins.
        result = _run(dispatcher.dispatch_one(AgentTask(
            task_id="go-4", agent_name="worker", description="d", prompt="p",
            invoker_name="caller",
        )))
        assert result.status == "failed"
        assert ran["called"] is False
        assert "capability escalation" in result.output.lower()


# ── 6. dispatch_many with guards ──────────────────────────────────────────────

class TestDispatchManyWithGuards:
    def test_mixed_results_from_guards_and_executor(self) -> None:
        """dispatch_many returns a mix of guard-rejected and completed results."""
        registry = _make_registry()
        invoker = _register_trusted_invoker(registry)
        registry.register(_subagent_config("worker"))
        guard = OrchestrationGuardConfig(max_nesting_depth=3, max_redispatch_count=1)
        dispatcher = _make_dispatcher(registry, guard=guard)

        tasks = [
            # 0: allowed (depth=0, first dispatch of this id)
            AgentTask(task_id="dm-ok", agent_name="worker", description="d",
                       prompt="p", invoker_name=invoker, depth=0),
            # 1: denied by nesting depth (depth=5 > 3)
            AgentTask(task_id="dm-deep", agent_name="worker", description="d",
                       prompt="p", invoker_name=invoker, depth=5),
            # 2: allowed (first dispatch of a different id)
            AgentTask(task_id="dm-ok2", agent_name="worker", description="d",
                       prompt="p", invoker_name=invoker, depth=0),
        ]

        results = _run(dispatcher.dispatch_many(tasks))

        assert len(results) == 3
        ok_ids = {r.task_id for r in results if r.status == "completed"}
        fail_ids = {r.task_id for r in results if r.status == "failed"}
        assert "dm-ok" in ok_ids
        assert "dm-ok2" in ok_ids
        assert "dm-deep" in fail_ids

    def test_empty_task_list_returns_empty(self) -> None:
        registry = _make_registry()
        guard = OrchestrationGuardConfig()
        dispatcher = _make_dispatcher(registry, guard=guard)
        results = _run(dispatcher.dispatch_many([]))
        assert results == []


# ── 7. configuration defaults ─────────────────────────────────────────────────

class TestOrchestrationGuardDefaults:
    def test_default_max_nesting_depth_is_3(self) -> None:
        cfg = OrchestrationGuardConfig()
        assert cfg.max_nesting_depth == 3

    def test_default_max_redispatch_count_is_5(self) -> None:
        cfg = OrchestrationGuardConfig()
        assert cfg.max_redispatch_count == 5

    def test_default_rate_limiter_disabled(self) -> None:
        cfg = OrchestrationGuardConfig()
        assert cfg.max_dispatches_per_window == 0

    def test_default_dispatch_rate_window_is_60(self) -> None:
        cfg = OrchestrationGuardConfig()
        assert cfg.dispatch_rate_window_s == 60.0

    def test_default_enforce_capability_escalation_is_true(self) -> None:
        cfg = OrchestrationGuardConfig()
        assert cfg.enforce_capability_escalation is True

    def test_default_max_concurrent_model_calls_is_10(self) -> None:
        cfg = OrchestrationGuardConfig()
        assert cfg.max_concurrent_model_calls == 10

    def test_default_task_split_threshold_is_medium(self) -> None:
        cfg = OrchestrationGuardConfig()
        assert cfg.task_split_threshold_effort == "medium"
