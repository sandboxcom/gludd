"""WP-C1 behavioral coverage for AgentDispatcher.

Covers the dispatch permission gates and active-task tracking that the
existing ``test_dispatcher.py`` does not exercise end-to-end:

- Pause gate: a task whose ``project_id`` is paused via the injected
  ``pause_controller`` returns ``status='blocked'`` and never reaches the
  executor.
- Budget gate: budget exhaustion is expressed through the same
  ``pause_controller.is_paused`` surface the dispatcher already consults;
  the dispatcher is policy-mechanism-agnostic about *why* a project is
  silenced.
- Capability gate: an invoker lacking ``can_dispatch_subagents`` (or not
  listed in ``allowed_subagents``) is denied before the executor runs.
- Active-count tracking: ``_active_count`` increments when a task enters
  the semaphore body and decrements on completion (including the failure
  path).
- Failure recording: repeated failures are each captured by the
  ``RunRecorder`` so an escalation layer can observe the threshold.
- Project-scoped active-task query.
- Per-agent concurrency cap enforced via ``max_concurrent`` semaphore.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from general_ludd.agents.dispatcher import AgentDispatcher, AgentTask
from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentType


# ---------------------------------------------------------------------------
# Helpers (mirrors the patterns in test_dispatcher.py)
# ---------------------------------------------------------------------------


def _make_registry() -> AgentRegistry:
    return AgentRegistry()


def _subagent_config(
    name: str, *, enabled: bool = True, max_concurrent: int = 1
) -> AgentConfig:
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
        max_concurrent=max_concurrent,
    )


def _invoker_config(
    name: str, *, can_dispatch: bool, allowed: list[str]
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


_TRUSTED_INVOKER = "trusted-caller"


def _register_trusted_invoker(registry: AgentRegistry) -> str:
    registry.register(
        _invoker_config(_TRUSTED_INVOKER, can_dispatch=True, allowed=["*"])
    )
    return _TRUSTED_INVOKER


async def _async_executor(task: AgentTask) -> str:
    return f"executed:{task.agent_name}"


class _StubPauseController:
    """Minimal pause-controller stub for the dispatcher's getattr-based probe.

    ``AgentDispatcher.dispatch_one`` calls
    ``getattr(pause_controller, "is_paused", None)`` and then
    ``is_paused("project", project_id)``.  This stub lets each test
    control which (kind, target) pairs are silenced.
    """

    def __init__(self, silenced: set[tuple[str, str]] | None = None) -> None:
        self._silenced = silenced or set()

    def is_paused(self, kind: str, target_id: str) -> bool:
        return (kind, target_id) in self._silenced


# ---------------------------------------------------------------------------
# 1. Pause gate
# ---------------------------------------------------------------------------


class TestDispatchBlocksWhenProjectPaused:
    def test_dispatch_blocks_when_project_paused(self) -> None:
        """A task whose project_id is paused returns status='blocked' and
        the executor never runs."""
        ran = {"called": False}

        async def _tracking(task: AgentTask) -> str:
            ran["called"] = True
            return "should-not-run"

        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)
        ctrl = _StubPauseController(silenced={("project", "proj-1")})
        dispatcher = AgentDispatcher(registry, executor=_tracking, pause_controller=ctrl)

        task = AgentTask(
            task_id="t-paused",
            agent_name="worker",
            description="do work",
            prompt="run it",
            invoker_name=invoker,
            project_id="proj-1",
        )
        result = asyncio.run(dispatcher.dispatch_one(task))

        assert result.status == "blocked", f"got {result.status!r}"
        assert "paused" in result.output.lower()
        assert ran["called"] is False, "executor ran despite project being paused"

    def test_unpaused_project_proceeds(self) -> None:
        """Sanity: the same setup with a non-paused project completes normally."""
        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)
        ctrl = _StubPauseController(silenced=set())
        dispatcher = AgentDispatcher(
            registry, executor=_async_executor, pause_controller=ctrl
        )

        task = AgentTask(
            task_id="t-ok",
            agent_name="worker",
            description="do work",
            prompt="run it",
            invoker_name=invoker,
            project_id="proj-active",
        )
        result = asyncio.run(dispatcher.dispatch_one(task))

        assert result.status == "completed"


# ---------------------------------------------------------------------------
# 2. Budget gate (expressed via the pause_controller surface)
# ---------------------------------------------------------------------------


class TestDispatchBlocksWhenBudgetExhausted:
    def test_dispatch_blocks_when_budget_exhausted(self) -> None:
        """Budget exhaustion is surfaced through the same ``is_paused`` gate
        the dispatcher already consults.  The dispatcher is agnostic to the
        *reason* a project is silenced — it trusts the controller's verdict.

        This test models budget exhaustion as the controller returning
        ``True`` for the project, which is how a budget-aware controller
        would express 'stop spending' to the dispatcher."""
        ran = {"called": False}

        async def _tracking(task: AgentTask) -> str:
            ran["called"] = True
            return "should-not-run"

        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)

        # A budget-aware controller silences the project when its budget is
        # exhausted — the dispatcher sees this identically to a manual pause.
        ctrl = _StubPauseController(silenced={("project", "budget-proj")})
        dispatcher = AgentDispatcher(registry, executor=_tracking, pause_controller=ctrl)

        task = AgentTask(
            task_id="t-budget",
            agent_name="worker",
            description="do work",
            prompt="run it",
            invoker_name=invoker,
            project_id="budget-proj",
        )
        result = asyncio.run(dispatcher.dispatch_one(task))

        assert result.status == "blocked", (
            f"budget-exhausted project must be blocked, got {result.status!r}"
        )
        assert ran["called"] is False, "executor ran despite budget exhaustion"


# ---------------------------------------------------------------------------
# 3. Capability gate
# ---------------------------------------------------------------------------


class TestDispatchBlocksWhenCapabilityNotAllowed:
    def test_dispatch_blocks_when_capability_not_allowed(self) -> None:
        """An invoker without ``can_dispatch_subagents`` is denied before the
        executor runs (capability gate)."""
        ran = {"called": False}

        async def _tracking(task: AgentTask) -> str:
            ran["called"] = True
            return "should-not-run"

        registry = _make_registry()
        registry.register(
            _invoker_config("weak-caller", can_dispatch=False, allowed=[])
        )
        registry.register(_subagent_config("target"))
        dispatcher = AgentDispatcher(registry, executor=_tracking)

        task = AgentTask(
            task_id="t-cap",
            agent_name="target",
            description="do work",
            prompt="run it",
            invoker_name="weak-caller",
        )
        result = asyncio.run(dispatcher.dispatch_one(task))

        assert result.status == "failed"
        assert "permission denied" in result.output.lower()
        assert ran["called"] is False, "executor ran despite capability denial"

    def test_capability_gate_denies_wrong_target(self) -> None:
        """can_dispatch=True but target not in allowed_subagents → denied."""
        registry = _make_registry()
        registry.register(
            _invoker_config("scoped-caller", can_dispatch=True, allowed=["other"])
        )
        registry.register(_subagent_config("target"))
        dispatcher = AgentDispatcher(registry, executor=_async_executor)

        task = AgentTask(
            task_id="t-scope",
            agent_name="target",
            description="do work",
            prompt="run it",
            invoker_name="scoped-caller",
        )
        result = asyncio.run(dispatcher.dispatch_one(task))

        assert result.status == "failed"
        assert "permission denied" in result.output.lower()


# ---------------------------------------------------------------------------
# 4 & 5. Active-count tracking
# ---------------------------------------------------------------------------


class TestActiveCountTracking:
    def test_dispatch_increments_active_count(self) -> None:
        """While a task is executing, ``active_count`` must be 1 and the task
        must appear in ``_active_tasks``."""
        seen: dict[str, int] = {}

        async def _observing(task: AgentTask) -> str:
            # Observe the count mid-flight via the dispatcher instance captured
            # in the closure. The executor runs inside the semaphore+lock block
            # where _active_count has already been incremented.
            seen["count"] = dispatcher.active_count
            seen["in_active"] = task.task_id in dispatcher._active_tasks
            return "done"

        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)
        dispatcher = AgentDispatcher(registry, executor=_observing)

        task = AgentTask(
            task_id="t-inc",
            agent_name="worker",
            description="do work",
            prompt="run it",
            invoker_name=invoker,
        )
        result = asyncio.run(dispatcher.dispatch_one(task))

        assert result.status == "completed"
        assert seen.get("count") == 1, (
            f"active_count was {seen.get('count')} during execution, expected 1"
        )
        assert seen.get("in_active") is True

    def test_dispatch_decrements_active_count_on_completion(self) -> None:
        """After a task completes, ``active_count`` returns to 0."""
        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)
        dispatcher = AgentDispatcher(registry, executor=_async_executor)

        task = AgentTask(
            task_id="t-dec",
            agent_name="worker",
            description="do work",
            prompt="run it",
            invoker_name=invoker,
        )
        result = asyncio.run(dispatcher.dispatch_one(task))

        assert result.status == "completed"
        assert dispatcher.active_count == 0
        assert task.task_id not in dispatcher._active_tasks

    def test_active_count_decrements_on_failure(self) -> None:
        """The finally-block must decrement even when the executor raises."""

        async def _boom(task: AgentTask) -> str:
            raise RuntimeError("kaboom")

        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)
        dispatcher = AgentDispatcher(registry, executor=_boom)

        task = AgentTask(
            task_id="t-fail-dec",
            agent_name="worker",
            description="do work",
            prompt="run it",
            invoker_name=invoker,
        )
        result = asyncio.run(dispatcher.dispatch_one(task))

        assert result.status == "failed"
        assert dispatcher.active_count == 0, (
            f"active_count leaked on failure: {dispatcher.active_count}"
        )


# ---------------------------------------------------------------------------
# 6. Failure escalation (via RunRecorder capture)
# ---------------------------------------------------------------------------


class TestDispatchFailuresEscalateAfterThreshold:
    def test_dispatch_failures_escalate_after_threshold(self) -> None:
        """The dispatcher records every failure event into the RunRecorder.
        After N consecutive failures for the same agent, the recorder holds
        N ``task_failed`` events — the observable substrate an escalation
        layer (alerting, circuit-breaker, backoff) would consume.

        The dispatcher itself does not implement escalation policy; it
        provides the signal.  This test pins that the signal is complete
        and correctly typed so downstream escalation is not starved."""

        class _CapturingRecorder:
            def __init__(self) -> None:
                self.events: list[dict[str, Any]] = []

            def record(self, run_id: str, event: dict[str, Any]) -> None:
                event["_run_id"] = run_id
                self.events.append(event)

        async def _always_fails(task: AgentTask) -> str:
            raise RuntimeError(f"failure-{task.task_id}")

        registry = _make_registry()
        registry.register(_subagent_config("worker"))
        invoker = _register_trusted_invoker(registry)
        recorder = _CapturingRecorder()
        dispatcher = AgentDispatcher(
            registry, executor=_always_fails, run_recorder=recorder
        )

        threshold = 3
        for i in range(threshold):
            task = AgentTask(
                task_id=f"escalate-{i}",
                agent_name="worker",
                description="do work",
                prompt="run it",
                invoker_name=invoker,
            )
            result = asyncio.run(dispatcher.dispatch_one(task))
            assert result.status == "failed"

        failed_events = [e for e in recorder.events if e.get("type") == "task_failed"]
        assert len(failed_events) == threshold, (
            f"expected {threshold} task_failed events (escalation substrate), "
            f"got {len(failed_events)}"
        )
        # Every failure event carries the agent name and error text.
        for ev in failed_events:
            assert ev["agent_name"] == "worker"
            assert "error" in ev or "reason" in ev


# ---------------------------------------------------------------------------
# 7. Project-scoped active-task query
# ---------------------------------------------------------------------------


class TestGetActiveTasksForProject:
    def test_get_active_tasks_for_project_returns_correct_set(self) -> None:
        """``get_active_tasks_for_project`` returns only the in-flight tasks
        whose ``project_id`` matches, filtering out other projects."""
        seen: dict[str, list[str]] = {}

        async def _observe_proj_a(task: AgentTask) -> str:
            # While this task is running, query active tasks for proj-a.
            active = await dispatcher.get_active_tasks_for_project("proj-a")
            seen["proj_a"] = [t.task_id for t in active]
            return "ok"

        registry = _make_registry()
        registry.register(_subagent_config("worker", max_concurrent=5))
        invoker = _register_trusted_invoker(registry)
        dispatcher = AgentDispatcher(registry, executor=_observe_proj_a)

        # Dispatch three tasks concurrently: two for proj-a, one for proj-b.
        tasks = [
            AgentTask(
                task_id="a-1",
                agent_name="worker",
                description="d",
                prompt="p",
                invoker_name=invoker,
                project_id="proj-a",
            ),
            AgentTask(
                task_id="a-2",
                agent_name="worker",
                description="d",
                prompt="p",
                invoker_name=invoker,
                project_id="proj-a",
            ),
            AgentTask(
                task_id="b-1",
                agent_name="worker",
                description="d",
                prompt="p",
                invoker_name=invoker,
                project_id="proj-b",
            ),
        ]
        results = asyncio.run(dispatcher.dispatch_many(tasks))

        assert all(r.status == "completed" for r in results)
        # The observing executor ran inside one of the proj-a tasks; at that
        # moment at least the calling task was active for proj-a.  proj-b's
        # task must NOT appear in the proj-a query.
        proj_a_seen = seen.get("proj_a", [])
        assert "b-1" not in proj_a_seen, (
            f"proj-b task leaked into proj-a query: {proj_a_seen}"
        )


# ---------------------------------------------------------------------------
# 8. Per-agent concurrency cap (max_concurrent semaphore)
# ---------------------------------------------------------------------------


class TestConcurrentDispatchRespectsConcurrencyCap:
    @pytest.mark.timeout(30)
    def test_concurrent_dispatch_respects_max_concurrent_cap(self) -> None:
        """``max_concurrent`` on the agent config bounds how many tasks for the
        same agent run simultaneously.  Dispatching more tasks than the cap
        must serialize them through the semaphore — the peak in-flight count
        never exceeds ``max_concurrent``."""
        max_concurrent = 2
        peak = {"value": 0}
        in_flight = {"value": 0}

        async def _counting(task: AgentTask) -> str:
            in_flight["value"] += 1
            if in_flight["value"] > peak["value"]:
                peak["value"] = in_flight["value"]
            # Yield to let other coroutines enter the semaphore body.
            await asyncio.sleep(0.05)
            in_flight["value"] -= 1
            return "ok"

        registry = _make_registry()
        registry.register(
            _subagent_config("capped", max_concurrent=max_concurrent)
        )
        invoker = _register_trusted_invoker(registry)
        dispatcher = AgentDispatcher(registry, executor=_counting)

        n_tasks = max_concurrent * 3  # over-subscribe to force contention
        tasks = [
            AgentTask(
                task_id=f"cap-{i}",
                agent_name="capped",
                description="d",
                prompt="p",
                invoker_name=invoker,
            )
            for i in range(n_tasks)
        ]
        results = asyncio.run(dispatcher.dispatch_many(tasks))

        assert all(r.status == "completed" for r in results), (
            f"unexpected failures: {[r for r in results if r.status != 'completed']}"
        )
        assert peak["value"] <= max_concurrent, (
            f"concurrency cap violated: peak in-flight was {peak['value']}, "
            f"cap is {max_concurrent}"
        )
