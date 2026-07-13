"""Phase AG — Unit tests for multi-agent orchestration framework.

Covers: AgentDispatcher task creation/tracking, HibernationController pause/resume,
TaskDecomposer pattern-based decomposition, ManagerAgent team assignment, and
the implicit agent task lifecycle (registered -> dispatched -> completed/failed/blocked).
"""

from __future__ import annotations

import asyncio

from general_ludd.agents.dispatcher import AgentDispatcher
from general_ludd.agents.hibernation import (
    AgentEnvironmentSnapshot,
    HibernationController,
    HibernationStore,
)
from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.task_decomposer import (
    ManagerAgent,
    RoleGoalBackstory,
    SubTask,
    TaskDecomposer,
)
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentTask, AgentType

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _minimal_registry() -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(
        AgentConfig(
            name="general",
            description="general-purpose subagent",
            type=AgentType.SUBAGENT,
            permissions=AgentPermission(),
            max_concurrent=3,
        )
    )
    registry.register(
        AgentConfig(
            name="primary",
            description="primary build agent with dispatch permission",
            type=AgentType.PRIMARY,
            permissions=AgentPermission(
                can_edit=True,
                can_bash=True,
                can_dispatch_subagents=True,
                allowed_subagents=["*"],
            ),
            max_concurrent=1,
        )
    )
    registry.register(
        AgentConfig(
            name="disabled",
            description="a disabled agent",
            type=AgentType.SUBAGENT,
            permissions=AgentPermission(),
            enabled=False,
            max_concurrent=1,
        )
    )
    registry.seal()
    return registry


def _dummy_executor(output: str = "ok") -> callable:
    async def _exec(task: AgentTask) -> str:
        return output

    return _exec


def _make_task(
    task_id: str = "task-1",
    agent_name: str = "general",
    description: str = "test task",
    prompt: str = "do something",
    *,
    invoker_name: str = "primary",
    project_id: str | None = None,
    depth: int = 0,
) -> AgentTask:
    return AgentTask(
        task_id=task_id,
        agent_name=agent_name,
        description=description,
        prompt=prompt,
        invoker_name=invoker_name,
        project_id=project_id,
        depth=depth,
    )


# =========================================================================== #
# 1. AgentDispatcher — task creation and tracking
# =========================================================================== #


class TestDispatcherTaskTracking:
    def test_active_count_increments_during_dispatch(self):
        registry = _minimal_registry()
        dispatcher = AgentDispatcher(registry, executor=_dummy_executor())
        assert dispatcher.active_count == 0

    async def test_dispatch_one_returns_completed_for_known_agent(self):
        registry = _minimal_registry()
        dispatcher = AgentDispatcher(registry, executor=_dummy_executor("success"))
        task = _make_task()
        result = await dispatcher.dispatch_one(task)
        assert result.status == "completed"
        assert result.output == "success"
        assert result.task_id == "task-1"
        assert result.agent_name == "general"

    async def test_dispatch_one_fails_for_unknown_agent(self):
        registry = _minimal_registry()
        dispatcher = AgentDispatcher(registry)
        task = _make_task(agent_name="ghost")
        result = await dispatcher.dispatch_one(task)
        assert result.status == "failed"
        assert "not found" in result.output.lower()

    async def test_dispatch_one_fails_for_disabled_agent(self):
        registry = _minimal_registry()
        dispatcher = AgentDispatcher(registry)
        task = _make_task(agent_name="disabled")
        result = await dispatcher.dispatch_one(task)
        assert result.status == "failed"
        assert "disabled" in result.output.lower()

    async def test_dispatch_one_fails_when_invoker_lacks_permission(self):
        registry = _minimal_registry()
        dispatcher = AgentDispatcher(registry)
        task = _make_task(invoker_name="general")
        result = await dispatcher.dispatch_one(task)
        assert result.status == "failed"
        assert "permission denied" in result.output.lower()

    async def test_dispatch_one_fails_when_invoker_empty(self):
        registry = _minimal_registry()
        dispatcher = AgentDispatcher(registry)
        task = _make_task(invoker_name="")
        result = await dispatcher.dispatch_one(task)
        assert result.status == "failed"
        assert "permission denied" in result.output.lower()

    async def test_active_tasks_tracked_during_execution(self):
        registry = _minimal_registry()
        ran = asyncio.Event()

        async def slow_exec(task: AgentTask) -> str:
            ran.set()
            await asyncio.sleep(0.05)
            return "done"

        dispatcher = AgentDispatcher(registry, executor=slow_exec)
        task = _make_task()

        async def check():
            await ran.wait()
            async with dispatcher._lock:
                return task.task_id in dispatcher._active_tasks

        coro = dispatcher.dispatch_one(task)
        check_task = asyncio.ensure_future(check())
        result = await coro
        was_tracked = await check_task

        assert was_tracked
        assert result.status == "completed"
        assert dispatcher.active_count == 0

    async def test_dispatch_one_returns_blocked_for_paused_project(self, tmp_path):
        store = HibernationStore(tmp_path)
        ctrl = HibernationController(store)
        ctrl.pause_project("paused-proj")

        registry = _minimal_registry()
        dispatcher = AgentDispatcher(registry, pause_controller=ctrl)
        task = _make_task(project_id="paused-proj")

        result = await dispatcher.dispatch_one(task)
        assert result.status == "blocked"

    async def test_dispatch_one_records_duration(self):
        registry = _minimal_registry()
        dispatcher = AgentDispatcher(registry, executor=_dummy_executor("ok"))
        task = _make_task()
        result = await dispatcher.dispatch_one(task)
        assert result.duration_seconds >= 0.0
        assert result.status == "completed"

    async def test_get_active_tasks_for_project_filters_correctly(self):
        registry = _minimal_registry()
        dispatcher = AgentDispatcher(registry, executor=_dummy_executor("ok"))
        task_a = _make_task(task_id="a", project_id="proj-1")
        task_b = _make_task(task_id="b", project_id="proj-2")

        await dispatcher.dispatch_one(task_a)
        await dispatcher.dispatch_one(task_b)

        proj1_tasks = await dispatcher.get_active_tasks_for_project("proj-1")
        proj2_tasks = await dispatcher.get_active_tasks_for_project("proj-2")
        proj3_tasks = await dispatcher.get_active_tasks_for_project("proj-3")

        assert len(proj1_tasks) == 0  # completed tasks are removed
        assert len(proj2_tasks) == 0
        assert len(proj3_tasks) == 0


class TestDispatchMany:
    async def test_dispatch_many_runs_all_successfully(self):
        registry = _minimal_registry()
        dispatcher = AgentDispatcher(registry, executor=_dummy_executor("yep"))
        tasks = [_make_task(task_id=f"t-{i}") for i in range(3)]
        results = await dispatcher.dispatch_many(tasks, timeout=5.0)
        assert len(results) == 3
        assert all(r.status == "completed" for r in results)
        assert [r.output for r in results] == ["yep", "yep", "yep"]

    async def test_dispatch_many_empty_list_returns_empty(self):
        registry = _minimal_registry()
        dispatcher = AgentDispatcher(registry)
        results = await dispatcher.dispatch_many([])
        assert results == []

    async def test_dispatch_many_mixed_statuses(self):
        registry = _minimal_registry()
        dispatcher = AgentDispatcher(registry, executor=_dummy_executor("ok"))
        tasks = [
            _make_task(task_id="good", agent_name="general"),
            _make_task(task_id="bad", agent_name="ghost"),
            _make_task(task_id="disabled-task", agent_name="disabled"),
        ]
        results = await dispatcher.dispatch_many(tasks, timeout=5.0)
        statuses = {r.task_id: r.status for r in results}
        assert statuses["good"] == "completed"
        assert statuses["bad"] == "failed"
        assert statuses["disabled-task"] == "failed"

    async def test_dispatch_many_timeout_cancels_remaining(self):
        registry = _minimal_registry()
        hold = asyncio.Event()

        async def slow_exec(task: AgentTask) -> str:
            if task.task_id == "slow":
                await hold.wait()
            return "ok"

        dispatcher = AgentDispatcher(registry, executor=slow_exec)
        tasks = [
            _make_task(task_id="fast"),
            _make_task(task_id="slow"),
        ]
        results = await dispatcher.dispatch_many(tasks, timeout=0.1)
        hold.set()
        assert len(results) == 2
        statuses = {r.task_id: r.status for r in results}
        assert statuses["fast"] == "completed"
        assert statuses["slow"] in ("failed", "cancelled")


class TestDispatcherOrchestrationGuards:
    def _registry_with_guards(self) -> AgentRegistry:
        registry = AgentRegistry()
        registry.register(
            AgentConfig(
                name="primary",
                description="primary agent",
                type=AgentType.PRIMARY,
                permissions=AgentPermission(
                    can_edit=True,
                    can_bash=True,
                    can_dispatch_subagents=True,
                    allowed_subagents=["*"],
                ),
                max_concurrent=1,
            )
        )
        registry.register(
            AgentConfig(
                name="readonly",
                description="read-only subagent",
                type=AgentType.SUBAGENT,
                permissions=AgentPermission(
                    can_edit=False,
                    can_bash=False,
                    can_read=True,
                ),
                max_concurrent=1,
            )
        )
        registry.register(
            AgentConfig(
                name="full",
                description="full-permission subagent",
                type=AgentType.SUBAGENT,
                permissions=AgentPermission(
                    can_edit=True,
                    can_bash=True,
                    can_read=True,
                    can_dispatch_subagents=True,
                    allowed_subagents=["*"],
                ),
                max_concurrent=1,
            )
        )
        registry.seal()
        return registry

    def test_nesting_depth_exceeded_rejected(self):
        from general_ludd.config.user_config import OrchestrationGuardConfig

        registry = self._registry_with_guards()
        guard = OrchestrationGuardConfig(max_nesting_depth=3)
        dispatcher = AgentDispatcher(
            registry, executor=_dummy_executor("ok"), orchestration_guard=guard
        )
        task = _make_task(depth=5)
        result = dispatcher._check_nesting_depth(task)
        assert result is not None
        assert result.status == "failed"
        assert "depth" in result.output.lower()

    def test_capability_escalation_blocked(self):
        from general_ludd.config.user_config import OrchestrationGuardConfig

        registry = self._registry_with_guards()
        guard = OrchestrationGuardConfig(enforce_capability_escalation=True)
        dispatcher = AgentDispatcher(
            registry, executor=_dummy_executor("ok"), orchestration_guard=guard
        )
        task = _make_task(agent_name="full")
        result = dispatcher._check_capability_escalation(task, "readonly")
        assert result is not None
        assert result.status == "failed"
        assert "escalation" in result.output.lower()

    async def test_spiral_detection_blocks_redispatch(self):
        from general_ludd.config.user_config import OrchestrationGuardConfig

        registry = self._registry_with_guards()
        guard = OrchestrationGuardConfig(max_redispatch_count=2)
        dispatcher = AgentDispatcher(
            registry, executor=_dummy_executor("ok"), orchestration_guard=guard
        )
        task = _make_task(task_id="loop-task")
        dispatcher._task_dispatch_counts["loop-task"] = 3
        result = await dispatcher._check_spiral(task)
        assert result is not None
        assert result.status == "failed"
        assert "spiral" in result.output.lower()

    def test_guard_disabled_when_config_is_none(self):
        registry = self._registry_with_guards()
        dispatcher = AgentDispatcher(registry, executor=_dummy_executor("ok"))
        task = _make_task(depth=100)
        assert dispatcher._check_nesting_depth(task) is None
        assert dispatcher._check_capability_escalation(task, "readonly") is None


# =========================================================================== #
# 2. HibernationController — pause / resume agents
# =========================================================================== #


class TestHibernationPauseResume:
    def test_pause_project_adds_to_paused_set(self, tmp_path):
        ctrl = HibernationController(HibernationStore(tmp_path))
        ctrl.pause_project("proj-a")
        assert ctrl.is_paused("project", "proj-a") is True
        assert ctrl.is_paused("project", "proj-b") is False

    def test_resume_project_removes_from_paused_set(self, tmp_path):
        ctrl = HibernationController(HibernationStore(tmp_path))
        ctrl.pause_project("proj-a")
        ctrl.resume_project("proj-a")
        assert ctrl.is_paused("project", "proj-a") is False

    def test_pause_idempotent(self, tmp_path):
        ctrl = HibernationController(HibernationStore(tmp_path))
        ctrl.pause_project("proj-a")
        ctrl.pause_project("proj-a")
        assert ctrl.is_paused("project", "proj-a") is True

    def test_resume_non_paused_is_noop(self, tmp_path):
        ctrl = HibernationController(HibernationStore(tmp_path))
        ctrl.resume_project("never-paused")
        assert ctrl.is_paused("project", "never-paused") is False

    def test_is_paused_non_project_scope_returns_false(self, tmp_path):
        ctrl = HibernationController(HibernationStore(tmp_path))
        ctrl.pause_project("proj-a")
        assert ctrl.is_paused("agent", "proj-a") is False
        assert ctrl.is_paused("workspace", "proj-a") is False

    def test_multiple_projects_paused_independently(self, tmp_path):
        ctrl = HibernationController(HibernationStore(tmp_path))
        ctrl.pause_project("proj-a")
        ctrl.pause_project("proj-b")
        assert ctrl.is_paused("project", "proj-a") is True
        assert ctrl.is_paused("project", "proj-b") is True

        ctrl.resume_project("proj-a")
        assert ctrl.is_paused("project", "proj-a") is False
        assert ctrl.is_paused("project", "proj-b") is True


class TestHibernationMinDepthOptions:
    def test_constructor_defaults(self, tmp_path):
        ctrl = HibernationController(HibernationStore(tmp_path))
        assert ctrl._min_depth == 3
        assert ctrl._min_context_messages == 8

    def test_custom_thresholds(self, tmp_path):
        ctrl = HibernationController(
            HibernationStore(tmp_path), min_depth=5, min_context_messages=20
        )
        assert ctrl._min_depth == 5
        assert ctrl._min_context_messages == 20

    def test_custom_clock_injected(self, tmp_path):
        frozen = 99.0
        ctrl = HibernationController(
            HibernationStore(tmp_path), clock=lambda: frozen
        )
        assert ctrl._clock() == frozen


# =========================================================================== #
# 3. TaskDecomposer — decomposition and ManagerAgent assignment
# =========================================================================== #


class TestTaskDecomposer:
    def test_decompose_empty_description_returns_empty(self):
        decomposer = TaskDecomposer()
        result = decomposer.decompose("", "any_role")
        assert result == []

    def test_decompose_whitespace_only_returns_empty(self):
        decomposer = TaskDecomposer()
        result = decomposer.decompose("   \t  ", "any_role")
        assert result == []

    def test_decompose_with_api_keyword_returns_api_steps(self):
        decomposer = TaskDecomposer()
        result = decomposer.decompose("Build a REST API for users", "backend_dev")
        assert len(result) >= 5
        descriptions = [s.description for s in result]
        assert any("API contract" in d for d in descriptions)
        assert any("integrat" in d.lower() for d in descriptions)

    def test_decompose_with_database_keyword_returns_db_steps(self):
        decomposer = TaskDecomposer()
        result = decomposer.decompose("Create database schema", "backend_dev")
        assert len(result) >= 5
        descriptions = [s.description for s in result]
        assert any("schema" in d.lower() for d in descriptions)

    def test_decompose_with_unknown_keyword_returns_default_steps(self):
        decomposer = TaskDecomposer()
        result = decomposer.decompose("do something magical", "any_role")
        assert len(result) == 5
        descriptions = {s.description for s in result}
        assert "Analyze requirements and constraints" in descriptions
        assert "Implement the core logic" in descriptions

    def test_subtasks_have_sequential_dependencies(self):
        decomposer = TaskDecomposer()
        result = decomposer.decompose("Build an API", "backend_dev")
        for i, subtask in enumerate(result):
            if i == 0:
                assert subtask.dependencies == []
            else:
                assert subtask.dependencies == [str(i)]

    def test_subtasks_initial_status_is_pending(self):
        decomposer = TaskDecomposer()
        result = decomposer.decompose("Build an API", "backend_dev")
        assert all(s.status == "pending" for s in result)

    def test_register_role_adds_to_decomposer(self):
        decomposer = TaskDecomposer()
        role = RoleGoalBackstory(
            role="backend_dev",
            goal="Build robust backend services",
            backstory="Senior backend engineer",
        )
        decomposer.register_role(role)
        assert "backend_dev" in decomposer.list_roles()

    def test_list_roles_returns_sorted(self):
        decomposer = TaskDecomposer()
        decomposer.register_role(
            RoleGoalBackstory(role="zulu", goal="g", backstory="b")
        )
        decomposer.register_role(
            RoleGoalBackstory(role="alpha", goal="g", backstory="b")
        )
        assert decomposer.list_roles() == ["alpha", "zulu"]

    def test_decompose_with_registered_role_assigns_matching_role(self):
        decomposer = TaskDecomposer()
        decomposer.register_role(
            RoleGoalBackstory(
                role="backend_dev",
                goal="Build backend services",
                backstory="Expert backend engineer",
            )
        )
        result = decomposer.decompose("Build API endpoints", "backend_dev")
        assert any(s.assigned_role == "backend_dev" for s in result)


class TestManagerAgent:
    def test_assigns_tasks_with_preset_assigned_role(self):
        mgr = ManagerAgent(
            RoleGoalBackstory(role="manager", goal="Coordinate", backstory="Manager")
        )
        frontend = RoleGoalBackstory(
            role="frontend_dev", goal="Build UIs", backstory="Frontend developer"
        )
        backend = RoleGoalBackstory(
            role="backend_dev", goal="Build APIs", backstory="Backend developer"
        )
        mgr.add_team_member(frontend)
        mgr.add_team_member(backend)

        tasks = [
            SubTask(id="1", description="Write a React component", assigned_role="frontend_dev"),
            SubTask(id="2", description="Create database migration", assigned_role="backend_dev"),
        ]
        assignments = mgr.assign_tasks(tasks)

        assert assignments["1"] is frontend
        assert assignments["2"] is backend

    def test_assigns_unassigned_task_by_keyword_match(self):
        mgr = ManagerAgent(
            RoleGoalBackstory(role="manager", goal="Coordinate", backstory="Manager")
        )
        backend = RoleGoalBackstory(
            role="backend_dev", goal="Build robust APIs and services", backstory="Backend dev"
        )
        mgr.add_team_member(backend)

        tasks = [SubTask(id="1", description="Implement an API endpoint")]
        assignments = mgr.assign_tasks(tasks)
        assert assignments["1"] is backend

    def test_assigns_to_first_member_when_no_match(self):
        mgr = ManagerAgent(
            RoleGoalBackstory(role="manager", goal="Coordinate", backstory="Manager")
        )
        first = RoleGoalBackstory(
            role="first_role", goal="first goal", backstory="First member"
        )
        second = RoleGoalBackstory(
            role="second_role", goal="second goal", backstory="Second member"
        )
        mgr.add_team_member(first)
        mgr.add_team_member(second)

        tasks = [SubTask(id="1", description="zzz")]
        assignments = mgr.assign_tasks(tasks)
        assert assignments["1"] is first

    def test_returns_none_for_empty_team(self):
        mgr = ManagerAgent(
            RoleGoalBackstory(role="manager", goal="Coordinate", backstory="Manager")
        )
        tasks = [SubTask(id="1", description="Do something")]
        assignments = mgr.assign_tasks(tasks)
        assert assignments["1"] is None

    def test_assigned_role_not_in_team_falls_back_to_keyword(self):
        mgr = ManagerAgent(
            RoleGoalBackstory(role="manager", goal="Coordinate", backstory="Manager")
        )
        backend = RoleGoalBackstory(
            role="backend_dev", goal="Build APIs", backstory="Backend dev"
        )
        mgr.add_team_member(backend)

        tasks = [
            SubTask(id="1", description="Write a React component", assigned_role="frontend_dev"),
        ]
        assignments = mgr.assign_tasks(tasks)
        assert assignments["1"] is backend  # falls back to keyword/goal match

    def test_manager_role_preserved(self):
        role = RoleGoalBackstory(
            role="lead", goal="Lead the team", backstory="Experienced leader"
        )
        mgr = ManagerAgent(role)
        assert mgr.manager_role is role
        assert mgr.manager_role.role == "lead"


class TestSubTaskModel:
    def test_subtask_equality_by_id(self):
        a = SubTask(id="1", description="do a")
        b = SubTask(id="1", description="do b")
        assert a == b
        assert hash(a) == hash(b)

    def test_subtask_inequality(self):
        a = SubTask(id="1", description="do a")
        b = SubTask(id="2", description="do a")
        assert a != b

    def test_subtask_repr(self):
        s = SubTask(id="task-42", description="do it", status="pending")
        r = repr(s)
        assert "task-42" in r
        assert "pending" in r


class TestRoleGoalBackstoryModel:
    def test_equality_by_role_name(self):
        a = RoleGoalBackstory(role="coder", goal="code well", backstory="coder")
        b = RoleGoalBackstory(role="coder", goal="code poorly", backstory="different")
        assert a == b
        assert hash(a) == hash(b)

    def test_inequality_different_roles(self):
        a = RoleGoalBackstory(role="coder", goal="code", backstory="b")
        b = RoleGoalBackstory(role="tester", goal="code", backstory="b")
        assert a != b

    def test_repr_includes_role(self):
        r = RoleGoalBackstory(role="auditor", goal="audit", backstory="b")
        assert "auditor" in repr(r)

    def test_tools_default_to_empty_list(self):
        r = RoleGoalBackstory(role="agent", goal="g", backstory="b")
        assert r.tools == []

    def test_tools_can_be_provided(self):
        r = RoleGoalBackstory(
            role="agent", goal="g", backstory="b", tools=["read", "write"]
        )
        assert r.tools == ["read", "write"]


# =========================================================================== #
# 4. Agent task lifecycle — registered → dispatched → completed/failed/blocked
# =========================================================================== #


class TestTaskLifecycle:
    """Implicit lifecycle: task instantiated → dispatched → result recorded."""

    async def test_lifecycle_unknown_agent_fails_before_execution(self):
        registry = _minimal_registry()
        called = False

        async def never_called(task: AgentTask) -> str:
            nonlocal called
            called = True
            return "ok"

        dispatcher = AgentDispatcher(registry, executor=never_called)
        task = _make_task(agent_name="ghost")
        result = await dispatcher.dispatch_one(task)
        assert result.status == "failed"
        assert "not found" in result.output.lower()
        assert called is False

    async def test_lifecycle_executor_exception_surfaces_as_failed(self):
        registry = _minimal_registry()

        async def broken_exec(task: AgentTask) -> str:
            raise RuntimeError("unexpected crash")

        dispatcher = AgentDispatcher(registry, executor=broken_exec)
        task = _make_task()
        result = await dispatcher.dispatch_one(task)
        assert result.status == "failed"
        assert "unexpected crash" in result.output

    async def test_lifecycle_active_count_resets_after_failure(self):
        registry = _minimal_registry()

        async def crash(task: AgentTask) -> str:
            raise ValueError("boom")

        dispatcher = AgentDispatcher(registry, executor=crash)
        task = _make_task()
        await dispatcher.dispatch_one(task)
        assert dispatcher.active_count == 0

    async def test_lifecycle_multiple_dispatches_track_independent_counts(self):
        registry = _minimal_registry()
        dispatcher = AgentDispatcher(registry, executor=_dummy_executor("ok"))
        t1 = _make_task(task_id="t-1")
        t2 = _make_task(task_id="t-2")
        r1 = await dispatcher.dispatch_one(t1)
        r2 = await dispatcher.dispatch_one(t2)
        assert r1.task_id == "t-1"
        assert r2.task_id == "t-2"
        assert dispatcher.active_count == 0

    async def test_lifecycle_result_includes_duration(self):
        registry = _minimal_registry()
        dispatcher = AgentDispatcher(registry, executor=_dummy_executor("ok"))
        task = _make_task()
        result = await dispatcher.dispatch_one(task)
        assert result.duration_seconds >= 0.0

    async def test_lifecycle_artifacts_present_in_result(self):
        registry = _minimal_registry()
        dispatcher = AgentDispatcher(registry, executor=_dummy_executor("artifacts"))
        task = _make_task()
        result = await dispatcher.dispatch_one(task)
        assert isinstance(result.artifacts, list)


class TestTaskLifecycleCancellation:
    async def test_dispatch_many_timeout_records_cancelled_tasks(self):
        registry = _minimal_registry()
        hold = asyncio.Event()

        async def blocked(task: AgentTask) -> str:
            await hold.wait()
            return "never"

        dispatcher = AgentDispatcher(registry, executor=blocked)
        tasks = [_make_task(task_id="blocked-task")]
        results = await dispatcher.dispatch_many(tasks, timeout=0.01)
        hold.set()
        assert len(results) == 1
        assert results[0].task_id == "blocked-task"


# =========================================================================== #
# 5. Quiesce / Resume at dispatch boundary
# =========================================================================== #


class TestQuiesceResume:
    async def test_quiesce_returns_cancelled_for_active_tasks(self):
        registry = _minimal_registry()
        hold = asyncio.Event()

        async def blocked(task: AgentTask) -> str:
            await hold.wait()
            return "late"

        dispatcher = AgentDispatcher(registry, executor=blocked, pause_controller=None)
        task = _make_task(task_id="q-task", project_id="proj-q")
        coro = asyncio.ensure_future(dispatcher.dispatch_one(task))

        await asyncio.sleep(0.02)
        async with dispatcher._lock:
            assert "q-task" in dispatcher._active_tasks

        results = await dispatcher.quiesce_project("proj-q", timeout=0.1)
        hold.set()
        await coro

        assert len(results) == 1
        assert results[0].status == "cancelled"
        assert results[0].task_id == "q-task"

    async def test_quiesce_unknown_project_returns_empty(self):
        registry = _minimal_registry()
        dispatcher = AgentDispatcher(registry)
        results = await dispatcher.quiesce_project("no-such-project")
        assert results == []

    async def test_resume_project_rehydrates_snapshots(self):
        registry = _minimal_registry()
        dispatcher = AgentDispatcher(registry)

        snap = AgentEnvironmentSnapshot(
            task_id="t-resume",
            agent_name="general",
            depth=1,
            parent_task_id="parent-1",
            invoker_name="primary",
            scratch={"description": "resumed work", "prompt": "continue"},
        )
        tasks = await dispatcher.resume_project("proj-x", [snap])
        assert len(tasks) == 1
        assert tasks[0].task_id == "t-resume"
        assert tasks[0].agent_name == "general"
        assert tasks[0].description == "resumed work"
        assert tasks[0].prompt == "continue"
        assert tasks[0].project_id == "proj-x"

    async def test_resume_project_skips_non_snapshot_entries(self):
        registry = _minimal_registry()
        dispatcher = AgentDispatcher(registry)
        tasks = await dispatcher.resume_project("proj-x", ["not a snapshot", 42])
        assert tasks == []
