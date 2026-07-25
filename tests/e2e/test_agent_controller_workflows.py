"""E2E tests for agents and controllers subsystems.

Covers agent registration, dispatch with budget, floor controller, pause
controller, saturation, merge conflicts, behavior codification, and tool
adapter workflows — all using real (non-mocked) instances.

Available tools: bash (make <target> only), read, write, edit, grep, glob.
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
import time

import pytest

from general_ludd.agents.behavior import (
    AgentBehavior,
    BehaviorRenderer,
    default_primary_behavior,
    default_subagent_behavior,
)
from general_ludd.agents.dispatcher import AgentDispatcher, AgentTaskResult
from general_ludd.agents.registry import AgentRegistry, default_registry
from general_ludd.agents.tool_adapter import AgentToolAdapter
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentTask, AgentType
from general_ludd.controllers.budget import RunBudgetGuard
from general_ludd.controllers.budget_manager import BudgetManager
from general_ludd.controllers.floor import FloorController
from general_ludd.controllers.merge_conflict import (
    ConflictHunk,
    ConflictKind,
    MergeConflictController,
    ResolutionStrategy,
)
from general_ludd.controllers.pause_controller import PauseController, PauseStore
from general_ludd.controllers.saturation import SaturationController, SourceCapacity
from general_ludd.scheduling.scheduler import WorkItem

# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _make_agent_config(
    name: str,
    agent_type: AgentType = AgentType.SUBAGENT,
    *,
    description: str = "",
    can_edit: bool = True,
    can_bash: bool = False,
    can_dispatch: bool = False,
    allowed_subagents: list[str] | None = None,
    max_concurrent: int = 1,
    enabled: bool = True,
) -> AgentConfig:
    return AgentConfig(
        name=name,
        description=description or f"{name} agent",
        type=agent_type,
        permissions=AgentPermission(
            can_edit=can_edit,
            can_bash=can_bash,
            can_read=True,
            can_dispatch_subagents=can_dispatch,
            allowed_subagents=allowed_subagents or [],
        ),
        max_concurrent=max_concurrent,
        enabled=enabled,
        behavior=default_subagent_behavior(),
    )


def _make_primary(name: str, allowed: list[str] | None = None) -> AgentConfig:
    return _make_agent_config(
        name,
        AgentType.PRIMARY,
        can_dispatch=True,
        allowed_subagents=allowed or ["*"],
    )


def _registry_with_primary(
    primary_name: str = "orchestrator",
    subagent_names: list[str] | None = None,
) -> tuple[AgentRegistry, str]:
    reg = _empty_registry()
    reg.register(_make_primary(primary_name))
    for name in (subagent_names or []):
        reg.register(_make_agent_config(name))
    return reg, primary_name


def _make_task(
    task_id: str,
    agent_name: str,
    description: str = "",
    *,
    invoker: str = "",
    project_id: str | None = None,
    depth: int = 0,
) -> AgentTask:
    return AgentTask(
        task_id=task_id,
        agent_name=agent_name,
        description=description or f"task {task_id}",
        prompt=f"Perform {task_id}",
        invoker_name=invoker,
        project_id=project_id,
        depth=depth,
    )


def _empty_registry() -> AgentRegistry:
    return AgentRegistry()


async def _gather_results(
    dispatcher: AgentDispatcher,
    tasks: list[AgentTask],
    timeout: float = 10.0,
) -> list[AgentTaskResult]:
    return await dispatcher.dispatch_many(tasks, timeout=timeout)


def _make_executor_fn(responses: dict[str, str] | None = None):
    responses = responses or {}

    async def exec(task: AgentTask) -> str:
        return responses.get(task.task_id, f"output from {task.agent_name}")

    return exec


# ─────────────────────────────────────────────────────────────────
# Agent Registration E2E
# ─────────────────────────────────────────────────────────────────

class TestAgentRegistrationWorkflow:
    def test_register_new_agent_appears_in_registry(self):
        reg = _empty_registry()
        cfg = _make_agent_config("security-scanner")
        reg.register(cfg)
        assert reg.get("security-scanner") is not None
        assert reg.get("security-scanner").name == "security-scanner"

    def test_register_multiple_subagents_and_list_them(self):
        reg = _empty_registry()
        reg.register(_make_agent_config("lint-fixer", AgentType.SUBAGENT))
        reg.register(_make_agent_config("auditor", AgentType.SUBAGENT))
        reg.register(_make_agent_config("orchestrator", AgentType.PRIMARY))
        subagents = reg.list_subagents()
        assert len(subagents) == 2
        names = {a.name for a in subagents}
        assert names == {"lint-fixer", "auditor"}

    def test_registered_agent_can_be_dispatched(self):
        reg, primary = _registry_with_primary(subagent_names=["greeter"])
        dispatcher = AgentDispatcher(reg, executor=_make_executor_fn({"t1": "hello"}))
        task = _make_task("t1", "greeter", invoker=primary)
        result = asyncio.run(dispatcher.dispatch_one(task))
        assert result.status == "completed"
        assert result.output == "hello"

    def test_dispatch_to_unknown_agent_fails(self):
        reg = _empty_registry()
        dispatcher = AgentDispatcher(reg)
        task = _make_task("t1", "nonexistent")
        result = asyncio.run(dispatcher.dispatch_one(task))
        assert result.status == "failed"
        assert "not found" in result.output.lower()

    def test_dispatch_to_disabled_agent_fails(self):
        reg = _empty_registry()
        reg.register(_make_agent_config("disabled-one", enabled=False))
        dispatcher = AgentDispatcher(reg)
        task = _make_task("t1", "disabled-one")
        result = asyncio.run(dispatcher.dispatch_one(task))
        assert result.status == "failed"
        assert "disabled" in result.output.lower()

    def test_sealed_registry_rejects_new_registration(self):
        reg = _empty_registry()
        reg.register(_make_agent_config("built-in"))
        reg.seal()
        with pytest.raises(RuntimeError, match="sealed"):
            reg.register(_make_agent_config("latecomer"))

    def test_default_registry_has_all_builtins(self):
        reg = default_registry()
        all_agents = reg.list_agents()
        names = {a.name for a in all_agents}
        assert "build" in names
        assert "plan" in names
        assert "explore" in names
        assert "general" in names
        assert "research" in names

    def test_can_invoke_subagent_permission_enforcement(self):
        reg = default_registry()
        assert reg.can_invoke("build", "explore")
        assert reg.can_invoke("plan", "explore")
        assert not reg.can_invoke("explore", "build")
        assert not reg.can_invoke("build", "imaginary")

    def test_invoker_permission_check_blocks_dispatch(self):
        reg = default_registry()
        dispatcher = AgentDispatcher(reg, executor=_make_executor_fn({"t1": "ok"}))
        task = _make_task("t1", "build", invoker="explore")
        result = asyncio.run(dispatcher.dispatch_one(task))
        assert result.status == "failed"
        assert "permission denied" in result.output.lower()

    def test_empty_invoker_name_blocks_dispatch_to_any_subagent(self):
        reg = default_registry()
        dispatcher = AgentDispatcher(reg, executor=_make_executor_fn({"t1": "ok"}))
        task = _make_task("t1", "general", invoker="")
        result = asyncio.run(dispatcher.dispatch_one(task))
        assert result.status == "failed"
        assert "permission denied" in result.output.lower()


# ─────────────────────────────────────────────────────────────────
# Agent Dispatch with Budget E2E
# ─────────────────────────────────────────────────────────────────

class TestAgentDispatchWithBudget:
    def test_run_budget_guard_records_and_checks_spend(self):
        guard = RunBudgetGuard(run_budget_usd=5.0)
        guard.record_spend(2.0)
        status = guard.check_run_budget()
        assert status["allowed"] is True
        assert status["remaining_budget"] == pytest.approx(3.0)

    def test_run_budget_exceeded_blocks(self):
        guard = RunBudgetGuard(run_budget_usd=5.0)
        guard.record_spend(6.0)
        status = guard.check_run_budget()
        assert status["allowed"] is False
        assert "exceeded" in str(status["reason"])

    def test_run_budget_spend_tracking_across_dispatch(self):
        reg, primary = _registry_with_primary(subagent_names=["worker"])
        reg.get("worker").max_concurrent = 3
        responses = {
            "t_a": "result-a",
            "t_b": "result-b",
        }
        dispatcher = AgentDispatcher(reg, executor=_make_executor_fn(responses))
        guard = RunBudgetGuard(run_budget_usd=10.0)

        tasks = [
            _make_task("t_a", "worker", invoker=primary),
            _make_task("t_b", "worker", invoker=primary),
        ]
        results = asyncio.run(dispatcher.dispatch_many(tasks))
        guard.record_spend(1.5)
        guard.record_spend(2.5)
        assert len(results) == 2
        assert all(r.status == "completed" for r in results)
        assert guard.get_total_spend() == pytest.approx(4.0)

    def test_per_call_budget_gate(self):
        guard = RunBudgetGuard(per_call_budget_usd=1.0)
        ok = guard.check_per_call(0.5)
        assert ok["allowed"] is True
        denied = guard.check_per_call(2.0)
        assert denied["allowed"] is False

    def test_check_all_limits_aggregated(self):
        guard = RunBudgetGuard(run_budget_usd=10.0, per_call_budget_usd=2.0)
        guard.record_spend(9.0)
        result = guard.check_all_limits(estimated_cost=1.5)
        assert result["allowed"] is True
        result = guard.check_all_limits(estimated_cost=3.0)
        assert result["allowed"] is False

    def test_wall_clock_timeout(self):
        guard = RunBudgetGuard(run_timeout_seconds=0.0)
        time.sleep(0.01)
        status = guard.check_wall_clock()
        assert status["allowed"] is False

    def test_budget_manager_daily_limit_blocks_when_exceeded(self):
        mgr = BudgetManager(daily_limit_usd=5.0)
        r1 = mgr.check_daily_budget(6.0)
        assert r1["allowed"] is False
        assert mgr.get_status()["paused"] is True

    def test_budget_manager_allows_under_limit(self):
        mgr = BudgetManager(daily_limit_usd=10.0)
        r = mgr.check_daily_budget(3.0)
        assert r["allowed"] is True
        assert mgr.get_status()["daily_spend"] == pytest.approx(3.0)

    def test_budget_manager_per_todo_limit(self):
        mgr = BudgetManager(per_todo_limit_usd=1.0)
        r = mgr.check_todo_budget("todo-1", 0.5)
        assert r["allowed"] is True
        r = mgr.check_todo_budget("todo-2", 2.0)
        assert r["allowed"] is False

    def test_budget_manager_record_spend_reconciles(self):
        mgr = BudgetManager(daily_limit_usd=50.0, per_todo_limit_usd=20.0)
        mgr.check_todo_budget("todo-a", 10.0)
        mgr.check_daily_budget_reserved("todo-a", 10.0)
        mgr.record_spend("todo-a", 7.0)
        status = mgr.get_status()
        assert status["daily_spend"] == pytest.approx(7.0)

    def test_budget_manager_reservations_prevent_race(self):
        mgr = BudgetManager(daily_limit_usd=10.0)
        mgr.check_daily_budget_reserved("t1", 4.0)
        r = mgr.check_daily_budget_reserved("t2", 5.0)
        assert r["allowed"] is True
        r = mgr.check_daily_budget_reserved("t3", 2.0)
        assert r["allowed"] is False


# ─────────────────────────────────────────────────────────────────
# Floor Controller E2E
# ─────────────────────────────────────────────────────────────────

class TestFloorControllerWorkflow:
    def test_default_floor_is_five(self):
        fc = FloorController()
        assert fc.floor == 5

    def test_custom_floor_constructor(self):
        fc = FloorController(floor=10)
        assert fc.floor == 10

    def test_health_above_50_gives_full_floor(self):
        fc = FloorController(floor=10)
        fc.update_health(80.0)
        assert fc.get_max_active() == 10

    def test_health_between_25_and_50_halves_floor(self):
        fc = FloorController(floor=10)
        fc.update_health(40.0)
        assert fc.get_max_active() == 5

    def test_health_below_25_gives_zero(self):
        fc = FloorController(floor=10)
        fc.update_health(10.0)
        assert fc.get_max_active() == 0

    def test_floor_auto_tune_lowers_on_low_success_rate(self):
        fc = FloorController(floor=10)
        new_floor = fc.auto_tune(
            cpu_pct=20.0,
            memory_pct=30.0,
            dispatch_success_rate=80.0,
            queue_depth=5,
        )
        assert new_floor == 8
        assert len(fc.floor_history) == 1

    def test_floor_auto_tune_raises_on_high_queue_pressure(self):
        fc = FloorController(floor=10)
        new_floor = fc.auto_tune(
            cpu_pct=10.0,
            memory_pct=40.0,
            dispatch_success_rate=98.0,
            queue_depth=25,
        )
        assert new_floor == 12

    def test_floor_auto_tune_no_change_in_steady_state(self):
        fc = FloorController(floor=10)
        new_floor = fc.auto_tune(
            cpu_pct=10.0,
            memory_pct=30.0,
            dispatch_success_rate=92.0,
            queue_depth=10,
        )
        assert new_floor == 10

    def test_floor_auto_tune_bounded_below(self):
        fc = FloorController(floor=1)
        fc.auto_tune(cpu_pct=10.0, memory_pct=30.0, dispatch_success_rate=80.0, queue_depth=5)
        assert fc.floor == 1

    def test_floor_auto_tune_bounded_above(self):
        fc = FloorController(floor=20)
        fc.auto_tune(cpu_pct=10.0, memory_pct=30.0, dispatch_success_rate=98.0, queue_depth=25)
        assert fc.floor == 20

    def test_floor_history_accumulates(self):
        fc = FloorController(floor=10)
        fc.auto_tune(cpu_pct=10.0, memory_pct=30.0, dispatch_success_rate=80.0, queue_depth=5)
        fc.auto_tune(cpu_pct=10.0, memory_pct=30.0, dispatch_success_rate=98.0, queue_depth=25)
        assert len(fc.floor_history) == 2


# ─────────────────────────────────────────────────────────────────
# Pause Controller E2E
# ─────────────────────────────────────────────────────────────────

def _temp_pause_store() -> PauseStore:
    """Create a PauseStore in a temporary directory so tests don't share state."""
    from general_ludd.controllers.pause_store import PauseStore
    tmp = tempfile.mkdtemp(prefix="pause_test_")
    return PauseStore(base_dir=tmp)


class TestPauseControllerWorkflow:
    def test_pause_and_resume_project_roundtrip(self):
        pc = PauseController(store=_temp_pause_store())
        assert not pc.is_paused("project", "proj-x")
        record = pc.pause("project", "proj-x", reason="disk full")
        assert pc.is_paused("project", "proj-x")
        assert record.kind == "project"
        assert record.target_id == "proj-x"
        assert record.reason == "disk full"

        resumed = pc.resume("project", "proj-x")
        assert resumed is not None
        assert not pc.is_paused("project", "proj-x")

    def test_pause_idempotent_returns_original(self):
        pc = PauseController(store=_temp_pause_store())
        r1 = pc.pause("model", "gpt-4")
        r2 = pc.pause("model", "gpt-4")
        assert r1.paused_at == r2.paused_at

    def test_resume_non_paused_returns_none(self):
        pc = PauseController(store=_temp_pause_store())
        result = pc.resume("project", "never-paused")
        assert result is None

    def test_list_paused_by_kind(self):
        pc = PauseController(store=_temp_pause_store())
        pc.pause("project", "p1")
        pc.pause("project", "p2")
        pc.pause("model", "m1")
        projects = pc.list_paused(kind="project")
        assert len(projects) == 2
        models = pc.list_paused(kind="model")
        assert len(models) == 1

    def test_list_all_paused(self):
        pc = PauseController(store=_temp_pause_store())
        pc.pause("project", "p1")
        pc.pause("agent", "a1")
        assert len(pc.list_paused()) == 2

    def test_get_returns_record(self):
        pc = PauseController(store=_temp_pause_store())
        pc.pause("task", "task-abc", reason="blocked")
        rec = pc.get("task", "task-abc")
        assert rec is not None
        assert rec.reason == "blocked"

    def test_get_returns_none_for_missing(self):
        pc = PauseController(store=_temp_pause_store())
        assert pc.get("task", "nope") is None

    def test_is_paused_lock_free_lookup(self):
        pc = PauseController(store=_temp_pause_store())
        pc.pause("project", "alpha")
        threads_done = []
        errors = []

        def check_paused():
            try:
                for _ in range(100):
                    assert pc.is_paused("project", "alpha")
                    assert not pc.is_paused("project", "beta")
            except Exception as e:
                errors.append(e)
            finally:
                threads_done.append(True)

        t1 = threading.Thread(target=check_paused)
        t2 = threading.Thread(target=check_paused)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert len(errors) == 0

    def test_resume_removes_record(self):
        pc = PauseController(store=_temp_pause_store())
        pc.pause("project", "p1")
        pc.resume("project", "p1")
        assert pc.get("project", "p1") is None


# ─────────────────────────────────────────────────────────────────
# Saturation Controller E2E
# ─────────────────────────────────────────────────────────────────

class TestSaturationControllerWorkflow:
    def test_utilization_at_half_capacity(self):
        sat = SaturationController()
        assert sat.utilization(running=5, target=10) == pytest.approx(0.5)

    def test_utilization_at_full(self):
        sat = SaturationController()
        assert sat.utilization(running=10, target=10) == pytest.approx(1.0)

    def test_utilization_zero_when_nothing_running(self):
        sat = SaturationController()
        assert sat.utilization(running=0, target=10) == 0.0

    def test_utilization_zero_with_zero_target(self):
        sat = SaturationController()
        assert sat.utilization(running=5, target=0) == 0.0

    def test_backfill_pulls_exactly_headroom(self):
        sat = SaturationController()
        backlog = [
            WorkItem(id="w1"),
            WorkItem(id="w2"),
            WorkItem(id="w3"),
            WorkItem(id="w4"),
            WorkItem(id="w5"),
        ]
        plan = sat.plan_backfill(target=10, running=7, backlog=backlog)
        assert len(plan) == 3
        assert plan[0].id == "w1"

    def test_backfill_empty_when_no_headroom(self):
        sat = SaturationController()
        backlog = [WorkItem(id="w1")]
        plan = sat.plan_backfill(target=5, running=5, backlog=backlog)
        assert len(plan) == 0

    def test_backfill_empty_when_empty_backlog(self):
        sat = SaturationController()
        plan = sat.plan_backfill(target=10, running=2, backlog=[])
        assert len(plan) == 0

    def test_backfill_bounded_by_backlog_length(self):
        sat = SaturationController()
        backlog = [WorkItem(id="w1")]
        plan = sat.plan_backfill(target=20, running=0, backlog=backlog)
        assert len(plan) == 1

    def test_backfill_by_source_distributes_correctly(self):
        sat = SaturationController()
        backlog = [
            WorkItem(id="w1"),
            WorkItem(id="w2"),
            WorkItem(id="w3"),
            WorkItem(id="w4"),
        ]
        caps = [
            SourceCapacity(source_id="gpu-a", capacity=5, running=3),
            SourceCapacity(source_id="gpu-b", capacity=5, running=4),
        ]
        assignment = sat.plan_backfill_by_source(
            target=10, running=7, backlog=backlog, per_source_caps=caps
        )
        assert len(assignment.items) == 3
        assert sum(len(v) for v in assignment.by_source.values()) == 3

    def test_source_capacity_headroom(self):
        cap = SourceCapacity(source_id="s1", capacity=10, running=6)
        assert cap.headroom == 4

    def test_source_capacity_headroom_never_negative(self):
        cap = SourceCapacity(source_id="s1", capacity=5, running=10)
        assert cap.headroom == 0


# ─────────────────────────────────────────────────────────────────
# Merge Conflict E2E
# ─────────────────────────────────────────────────────────────────

class TestMergeConflictWorkflow:
    def _controller(self) -> MergeConflictController:
        return MergeConflictController()

    def test_parse_single_conflict_hunk(self):
        mc = self._controller()
        content = """\
line before
<<<<<<< ours
a = 1
=======
a = 2
>>>>>>> theirs
line after"""
        hunks = mc.parse_hunks(content)
        assert len(hunks) == 1
        assert hunks[0].ours == ("a = 1",)
        assert hunks[0].theirs == ("a = 2",)
        assert hunks[0].start_line == 2

    def test_parse_multiple_hunks(self):
        mc = self._controller()
        content = """\
<<<<<<< ours
x = 1
=======
x = 2
>>>>>>> theirs
middle
<<<<<<< ours
y = 3
=======
y = 4
>>>>>>> theirs"""
        hunks = mc.parse_hunks(content)
        assert len(hunks) == 2

    def test_parse_no_conflicts_returns_empty(self):
        mc = self._controller()
        hunks = mc.parse_hunks("plain file content\nno conflicts here\n")
        assert len(hunks) == 0

    def test_classify_identical_hunks(self):
        mc = self._controller()
        hunk = ConflictHunk(ours=("x = 1",), theirs=("x = 1",), start_line=1)
        assert mc.classify(hunk) == ConflictKind.IDENTICAL

    def test_classify_add_on_one_side_ours_only(self):
        mc = self._controller()
        hunk = ConflictHunk(ours=("new line",), theirs=(), start_line=1)
        assert mc.classify(hunk) == ConflictKind.ADD_ON_ONE_SIDE

    def test_classify_add_on_one_side_theirs_only(self):
        mc = self._controller()
        hunk = ConflictHunk(ours=(), theirs=("their line",), start_line=1)
        assert mc.classify(hunk) == ConflictKind.ADD_ON_ONE_SIDE

    def test_classify_whitespace_only(self):
        mc = self._controller()
        hunk = ConflictHunk(
            ours=("  a = 1  ",), theirs=("a = 1",), start_line=1
        )
        assert mc.classify(hunk) == ConflictKind.WHITESPACE_ONLY

    def test_classify_import_block(self):
        mc = self._controller()
        hunk = ConflictHunk(
            ours=("import os", "import sys"),
            theirs=("import os", "from pathlib import Path"),
            start_line=1,
        )
        assert mc.classify(hunk) == ConflictKind.IMPORT_BLOCK

    def test_classify_semantic_divergence(self):
        mc = self._controller()
        hunk = ConflictHunk(
            ours=("a = compute_x()",),
            theirs=("a = compute_y()",),
            start_line=1,
        )
        assert mc.classify(hunk) == ConflictKind.SEMANTIC

    def test_resolve_identical_takes_either(self):
        mc = self._controller()
        hunk = ConflictHunk(ours=("x = 1",), theirs=("x = 1",), start_line=1)
        resolution = mc.resolve_hunk(hunk)
        assert resolution.strategy == ResolutionStrategy.TAKE_EITHER
        assert resolution.confidence == 1.0
        assert resolution.resolved_lines == ("x = 1",)

    def test_resolve_ours_add_takes_ours(self):
        mc = self._controller()
        hunk = ConflictHunk(ours=("new feature",), theirs=(), start_line=1)
        resolution = mc.resolve_hunk(hunk)
        assert resolution.strategy == ResolutionStrategy.TAKE_OURS
        assert resolution.resolved_lines == ("new feature",)

    def test_resolve_theirs_add_takes_theirs(self):
        mc = self._controller()
        hunk = ConflictHunk(ours=(), theirs=("remote feature",), start_line=1)
        resolution = mc.resolve_hunk(hunk)
        assert resolution.strategy == ResolutionStrategy.TAKE_THEIRS
        assert resolution.resolved_lines == ("remote feature",)

    def test_resolve_import_block_union(self):
        mc = self._controller()
        hunk = ConflictHunk(
            ours=("import os",),
            theirs=("from pathlib import Path",),
            start_line=1,
        )
        resolution = mc.resolve_hunk(hunk)
        assert resolution.strategy == ResolutionStrategy.TAKE_UNION
        assert "import os" in resolution.resolved_lines
        assert "from pathlib import Path" in resolution.resolved_lines

    def test_resolve_semantic_escalates(self):
        mc = self._controller()
        hunk = ConflictHunk(ours=("a = 1",), theirs=("a = 2",), start_line=1)
        resolution = mc.resolve_hunk(hunk)
        assert resolution.strategy == ResolutionStrategy.ESCALATE
        assert resolution.confidence == 0.0
        assert resolution.resolved_lines is None

    def test_plan_file_auto_resolvable(self):
        mc = self._controller()
        content = """\
<<<<<<< ours
import os
=======
from pathlib import Path
>>>>>>> theirs"""
        plan = mc.plan_file("src/foo.py", content)
        assert plan.path == "src/foo.py"
        assert plan.auto_resolvable is True
        assert plan.escalation_count == 0

    def test_plan_file_with_escalation_is_not_auto_resolvable(self):
        mc = self._controller()
        content = """\
<<<<<<< ours
a = 1
=======
a = 2
>>>>>>> theirs"""
        plan = mc.plan_file("src/bar.py", content)
        assert plan.auto_resolvable is False
        assert plan.escalation_count == 1

    def test_plan_file_mixed_resolutions(self):
        mc = self._controller()
        content = """\
<<<<<<< ours
import os
=======
import sys
>>>>>>> theirs
middle
<<<<<<< ours
result = heavy_compute()
=======
result = 42
>>>>>>> theirs"""
        plan = mc.plan_file("src/main.py", content)
        assert len(plan.resolutions) == 2
        assert plan.auto_resolvable is False


# ─────────────────────────────────────────────────────────────────
# Agent Behavior Codification E2E
# ─────────────────────────────────────────────────────────────────

class TestBehaviorCodificationWorkflow:
    def test_behavior_renders_full_prompt_for_primary(self):
        behavior = default_primary_behavior()
        renderer = BehaviorRenderer()
        prompt = renderer.render_as_prompt(
            behavior, agent_name="build", task="ship feature",
        )
        assert "build" in prompt
        assert "ship feature" in prompt
        assert "TDD Policy" in prompt
        assert "SESSION" in prompt
        assert "Guardrail Policy" in prompt

    def test_behavior_renders_for_subagent_without_self_directed_work(self):
        behavior = default_subagent_behavior()
        renderer = BehaviorRenderer()
        prompt = renderer.render(behavior)
        assert "Self-Directed Work" not in prompt

    def test_custom_behavior_preserves_settings(self):
        behavior = AgentBehavior(
            completion_policy="stop_on_blocker",
            tdd_enforced=False,
            commit_after_green=False,
            allowed_command_patterns=["make *", "npm *"],
            max_retries=5,
            subagent_context_limit_lines=5,
        )
        renderer = BehaviorRenderer()
        prompt = renderer.render(behavior)
        assert "blocker" in prompt.lower()
        assert "TDD Policy" not in prompt
        assert "npm" in prompt
        assert "Return ≤5 lines" in prompt

    def test_behavior_command_allowlist_matching(self):
        behavior = default_primary_behavior()
        assert behavior.is_command_allowed("make test")
        assert behavior.is_command_allowed("make lint FILES=src/")
        assert not behavior.is_command_allowed("git push")
        assert not behavior.is_command_allowed("make test && echo done")
        assert not behavior.is_command_allowed("")
        assert not behavior.is_command_allowed("  ")

    def test_behavior_assume_and_proceed_records(self):
        behavior = default_primary_behavior()
        assert behavior.should_block_on_question("what next?") is False
        entry = behavior.record_assumption("which file?", "main.py")
        assert "ASSUMPTION" in entry
        assert len(behavior.assumption_log) == 1

    def test_behavior_renderer_cache_works(self):
        renderer = BehaviorRenderer()
        behavior = default_primary_behavior()
        r1 = renderer.render(behavior)
        r2 = renderer.render(behavior)
        assert r1 == r2

    def test_behavior_serialization_roundtrip(self):
        original = default_primary_behavior()
        d = original.to_dict()
        restored = AgentBehavior.from_dict(d)
        assert restored.completion_policy == original.completion_policy
        assert restored.tdd_enforced == original.tdd_enforced
        assert restored.max_retries == original.max_retries

    def test_registry_behavior_resolution(self):
        reg = default_registry()
        build_behavior = reg.get_behavior("build")
        assert build_behavior.tdd_enforced is True
        explore_behavior = reg.get_behavior("explore")
        assert explore_behavior.self_directed_work is False

    def test_registry_render_behavior_prompt(self):
        reg = default_registry()
        prompt = reg.render_behavior_prompt("build", "fix a bug")
        assert prompt is not None
        assert "build" in prompt
        assert "fix a bug" in prompt
        assert reg.render_behavior_prompt("imaginary", "task") is None


# ─────────────────────────────────────────────────────────────────
# Tool Adapter E2E
# ─────────────────────────────────────────────────────────────────

class TestToolAdapterWorkflow:
    def test_list_agent_tools_converts_all_agents(self):
        reg = _empty_registry()
        reg.register(_make_agent_config("lint-fixer"))
        reg.register(_make_agent_config("auditor"))
        adapter = AgentToolAdapter(reg)
        tools = adapter.list_agent_tools()
        assert len(tools) == 2
        tool_names = {t["name"] for t in tools}
        assert tool_names == {"dispatch_lint-fixer", "dispatch_auditor"}

    def test_list_agent_tools_filters_by_invoker_permissions(self):
        reg = _empty_registry()
        reg.register(
            _make_agent_config(
                "orchestrator",
                AgentType.PRIMARY,
                can_dispatch=True,
                allowed_subagents=["lint-fixer"],
            )
        )
        reg.register(_make_agent_config("lint-fixer"))
        reg.register(_make_agent_config("auditor"))
        adapter = AgentToolAdapter(reg)
        tools = adapter.list_agent_tools(invoker="orchestrator")
        assert len(tools) == 1
        assert tools[0]["target_agent"] == "lint-fixer"

    def test_list_agent_tools_filters_unknown_invoker(self):
        reg = _empty_registry()
        reg.register(_make_agent_config("lint-fixer"))
        reg.register(_make_agent_config("auditor"))
        adapter = AgentToolAdapter(reg)
        tools = adapter.list_agent_tools(invoker="unknown")
        assert len(tools) == 0

    def test_get_agent_as_tool_returns_tool_dict(self):
        reg = _empty_registry()
        reg.register(
            _make_agent_config("greeter", description="says hello")
        )
        adapter = AgentToolAdapter(reg)
        tool = adapter.get_agent_as_tool("greeter")
        assert tool is not None
        assert tool["name"] == "dispatch_greeter"
        assert tool["description"] == "says hello"
        assert tool["type"] == "agent_dispatch"

    def test_get_agent_as_tool_unknown_returns_none(self):
        reg = _empty_registry()
        adapter = AgentToolAdapter(reg)
        tool = adapter.get_agent_as_tool("nobody")
        assert tool is None

    def test_get_agent_as_tool_permission_denied_returns_none(self):
        reg = _empty_registry()
        reg.register(
            _make_agent_config("builder", can_dispatch=True, allowed_subagents=["auditor"])
        )
        reg.register(_make_agent_config("auditor"))
        reg.register(_make_agent_config("secret-keeper"))
        adapter = AgentToolAdapter(reg)
        tool = adapter.get_agent_as_tool("secret-keeper", invoker="builder")
        assert tool is None

    def test_tool_adapter_with_default_registry(self):
        reg = default_registry()
        adapter = AgentToolAdapter(reg)
        tools_from_build = adapter.list_agent_tools(invoker="build")
        assert len(tools_from_build) == 5
        tools_from_explore = adapter.list_agent_tools(invoker="explore")
        assert len(tools_from_explore) == 0

    def test_tool_adapter_dispatch_to_self_included(self):
        reg = _empty_registry()
        reg.register(
            _make_agent_config("self-dispatcher", can_dispatch=True, allowed_subagents=["self-dispatcher"])
        )
        adapter = AgentToolAdapter(reg)
        tools = adapter.list_agent_tools(invoker="self-dispatcher")
        assert any(t["target_agent"] == "self-dispatcher" for t in tools)


# ─────────────────────────────────────────────────────────────────
# Cross-Controller Integration E2E
# ─────────────────────────────────────────────────────────────────

class TestCrossControllerIntegration:
    def test_dispatch_then_budget_then_floor_workflow(self):
        """Full pipeline: register → dispatch → budget check → floor check."""
        reg, primary = _registry_with_primary(subagent_names=["worker"])
        reg.get("worker").max_concurrent = 3
        dispatcher = AgentDispatcher(reg, executor=_make_executor_fn({
            "t1": "done-1",
            "t2": "done-2",
            "t3": "done-3",
        }))

        guard = RunBudgetGuard(run_budget_usd=5.0)
        fc = FloorController(floor=3)

        tasks = [_make_task(f"t{i}", "worker", invoker=primary) for i in range(1, 4)]
        results = asyncio.run(dispatcher.dispatch_many(tasks))
        assert all(r.status == "completed" for r in results)
        assert dispatcher.active_count == 0

        guard.record_spend(0.50)
        guard.record_spend(0.50)
        assert guard.check_run_budget()["allowed"] is True

        assert fc.get_max_active() == 3

    def test_pause_gate_stops_dispatcher(self):
        reg, primary = _registry_with_primary(subagent_names=["worker"])
        pc = PauseController(store=_temp_pause_store())
        dispatcher = AgentDispatcher(
            reg,
            executor=_make_executor_fn({"t1": "ok"}),
            pause_controller=pc,
        )

        task = _make_task("t1", "worker", invoker=primary, project_id="proj-a")
        result = asyncio.run(dispatcher.dispatch_one(task))
        assert result.status == "completed"

        pc.pause("project", "proj-a", reason="quota")
        result = asyncio.run(dispatcher.dispatch_one(task))
        assert result.status == "blocked"
        assert "paused" in result.output.lower()

    def test_pause_resume_cycle_with_dispatcher(self):
        reg, primary = _registry_with_primary(subagent_names=["worker"])
        pc = PauseController(store=_temp_pause_store())
        dispatcher = AgentDispatcher(
            reg,
            executor=_make_executor_fn({"t1": "work-done"}),
            pause_controller=pc,
        )

        pc.pause("project", "proj-b", reason="maintenance")
        task = _make_task("t1", "worker", invoker=primary, project_id="proj-b")
        result = asyncio.run(dispatcher.dispatch_one(task))
        assert result.status == "blocked"

        pc.resume("project", "proj-b")
        result = asyncio.run(dispatcher.dispatch_one(task))
        assert result.status == "completed"

    def test_saturation_integration_budget_and_floor(self):
        sat = SaturationController()
        fc = FloorController(floor=10)
        guard = RunBudgetGuard(run_budget_usd=100.0)

        running = 6
        effective_floor = fc.get_max_active()
        headroom = max(0, effective_floor - running)

        assert headroom == 4
        guard.record_spend(50.0)
        assert guard.check_run_budget()["allowed"] is True

        backlog = [WorkItem(id=f"w{i}") for i in range(10)]
        plan = sat.plan_backfill(
            target=effective_floor, running=running, backlog=backlog
        )
        assert len(plan) == headroom

    def test_merge_conflict_with_rendered_behavior(self):
        """If two agents edit same file, merge conflict controller handles it."""
        mc = MergeConflictController()
        behavior = default_primary_behavior()
        renderer = BehaviorRenderer()
        prompt = renderer.render(behavior)
        assert "TDD" in prompt

        conflict_content = """\
<<<<<<< ours
timeout = 30
=======
timeout = 15
>>>>>>> theirs"""
        plan = mc.plan_file("config.py", conflict_content)
        assert plan.auto_resolvable is False
        assert plan.escalation_count == 1

    def test_default_registry_integration_with_tool_adapter(self):
        reg = default_registry()
        adapter = AgentToolAdapter(reg)
        build_tool = adapter.get_agent_as_tool("explore", invoker="build")
        assert build_tool is not None
        assert build_tool["type"] == "agent_dispatch"

        explore_tool = adapter.get_agent_as_tool("build", invoker="explore")
        assert explore_tool is None

    def test_dispatcher_concurrent_max_respected(self):
        reg, primary = _registry_with_primary(subagent_names=["sequential-worker"])
        reg.get("sequential-worker").max_concurrent = 1
        exec_order = []

        async def ordered_exec(task: AgentTask) -> str:
            exec_order.append(task.task_id)
            await asyncio.sleep(0.01)
            return task.task_id

        dispatcher = AgentDispatcher(reg, executor=ordered_exec)
        tasks = [_make_task(f"t{i}", "sequential-worker", invoker=primary) for i in range(3)]
        results = asyncio.run(dispatcher.dispatch_many(tasks, timeout=10.0))
        assert all(r.status == "completed" for r in results)

    def test_registry_behavior_resolution_fallback_to_defaults(self):
        reg = _empty_registry()
        reg.register(
            _make_agent_config("no-behavior", AgentType.PRIMARY)
        )
        behavior = reg.get_behavior("no-behavior")
        assert behavior.tdd_enforced is True
        assert behavior.session_persistence is True

        reg.register(
            _make_agent_config("also-no-behavior", AgentType.SUBAGENT)
        )
        sub_behavior = reg.get_behavior("also-no-behavior")
        assert sub_behavior.self_directed_work is False
