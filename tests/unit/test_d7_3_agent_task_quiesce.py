"""Test depth persistence across agent/task pause/resume (D.7.3, D11).

Verifies that resumed agents retain their prior depth so the recursion
guard (_check_nesting_depth) cannot be bypassed by pausing at depth N
and resuming at depth 0.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from general_ludd.agents.hibernation import (
    AgentEnvironmentSnapshot,
    HibernationController,
    HibernationStore,
    _load_hibernate_mac_key,
)
from general_ludd.controllers.pause_controller import PauseController
from general_ludd.controllers.pause_store import PauseStore

if TYPE_CHECKING:
    pass


class MockTask:
    def __init__(
        self,
        task_id,
        agent_name,
        project_id="proj-1",
        description="",
        prompt="",
        depth=0,
        messages=None,
        parent_task_id=None,
        invoker_name="",
    ):
        self.task_id = task_id
        self.agent_name = agent_name
        self.project_id = project_id
        self.description = description
        self.prompt = prompt
        self.depth = depth
        self.messages = messages or []
        self.parent_task_id = parent_task_id
        self.invoker_name = invoker_name


class MockDispatcher:
    def __init__(self, tasks):
        self._tasks = list(tasks)
        self.quiesce_project_calls: list[str] = []
        self.resume_project_calls: list[tuple[str, list]] = []
        self.dispatch_one_calls: list[MockTask] = []
        self.dispatched_tasks: list[MockTask] = []

    async def get_active_tasks_for_project(self, project_id):
        return [t for t in self._tasks if t.project_id == project_id]

    async def get_active_tasks_by_agent_name(self, agent_name):
        return [t for t in self._tasks if t.agent_name == agent_name]

    async def get_active_tasks_by_task_id(self, task_id):
        return [t for t in self._tasks if t.task_id == task_id]

    async def quiesce_project(self, project_id, timeout=30.0):
        self.quiesce_project_calls.append(project_id)
        return []

    async def resume_project(self, project_id, snapshots):
        self.resume_project_calls.append((project_id, list(snapshots)))
        from general_ludd.agents.types import AgentTask

        tasks = []
        for snap in snapshots:
            if isinstance(snap, AgentEnvironmentSnapshot):
                task = AgentTask(
                    task_id=snap.task_id,
                    agent_name=snap.agent_name,
                    description=snap.scratch.get("description", ""),
                    prompt=snap.scratch.get("prompt", ""),
                    parent_task_id=snap.parent_task_id,
                    invoker_name=snap.invoker_name,
                    project_id=project_id,
                    depth=snap.depth,
                    messages=list(snap.messages),
                )
                tasks.append(task)
                self.dispatched_tasks.append(task)
                self.dispatch_one_calls.append(task)
        return tasks

    async def dispatch_one(self, task):
        self.dispatch_one_calls.append(task)


# ── Depth persistence across agent pause/resume ──


class TestResumedAgentRetainsDepth:
    @pytest.mark.asyncio
    async def test_agent_quiesce_captures_depth(self, tmp_path):
        base_dir = str(tmp_path / "hibernate")
        key = _load_hibernate_mac_key(base_dir)
        store = HibernationStore(base_dir=base_dir, mac_key=key)
        controller = HibernationController(store=store)

        task = MockTask(
            task_id="deep-agent-1",
            agent_name="deep-agent-1",
            project_id="proj-qa",
            depth=7,
            messages=[{"role": "user", "content": "hello"}] * 5,
            description="deep subagent at depth 7",
            prompt="continue work",
        )
        dispatcher = MockDispatcher([task])

        pc = PauseController(PauseStore(tmp_path / "pause"))
        handles, status, _errors = await pc.quiesce_entity(
            "agent",
            "deep-agent-1",
            dispatcher=dispatcher,
            hibernation=controller,
        )
        assert status == "clean"
        assert _errors == []
        assert len(handles) == 1

        restored = store.hydrate(handles[0])
        assert restored.depth == 7
        assert restored.task_id == "deep-agent-1"
        assert restored.agent_name == "deep-agent-1"

    @pytest.mark.asyncio
    async def test_agent_resume_restores_depth_via_rehydrate(self, tmp_path):
        base_dir = str(tmp_path / "hibernate")
        key = _load_hibernate_mac_key(base_dir)
        store = HibernationStore(base_dir=base_dir, mac_key=key)
        hc = HibernationController(store=store)

        task = MockTask(
            task_id="deep-agent-2",
            agent_name="deep-agent-2",
            project_id="proj-qa",
            depth=5,
            messages=[{"role": "user", "content": "hello"}] * 8,
            description="deeper agent",
            prompt="keep going",
        )
        dispatcher = MockDispatcher([task])

        pc = PauseController(PauseStore(tmp_path / "pause"))

        # 1) Pause with depth capture
        handles, status, _errors = await pc.quiesce_entity(
            "agent",
            "deep-agent-2",
            dispatcher=dispatcher,
            hibernation=hc,
        )
        assert len(handles) == 1
        assert status == "clean"

        # Store handles and mark paused
        pc.pause("agent", "deep-agent-2", reason="test", agent_handles=[h.model_dump() for h in handles])

        # 2) Resume — depth should be restored
        assert pc.is_paused("agent", "deep-agent-2") is True
        pc.resume("agent", "deep-agent-2")

        # 3) Rehydrate
        snapshots, status, _errors = await pc.resume_rehydrate(
            "agent",
            "deep-agent-2",
            dispatcher=dispatcher,
            hibernation=hc,
        )
        assert len(snapshots) == 1
        assert status == "clean"

        # 4) Verify the dispatched task has depth=5
        assert len(dispatcher.dispatched_tasks) == 1
        assert dispatcher.dispatched_tasks[0].depth == 5

    @pytest.mark.asyncio
    async def test_task_pause_resume_preserves_depth(self, tmp_path):
        base_dir = str(tmp_path / "hibernate")
        key = _load_hibernate_mac_key(base_dir)
        store = HibernationStore(base_dir=base_dir, mac_key=key)
        hc = HibernationController(store=store)

        task = MockTask(
            task_id="task-42",
            agent_name="worker-bee",
            project_id="proj-qa",
            depth=3,
            messages=[{"role": "user", "content": "work"}] * 4,
            description="worker task",
            prompt="execute",
        )
        dispatcher = MockDispatcher([task])

        pc = PauseController(PauseStore(tmp_path / "pause"))

        handles, status, _errors = await pc.quiesce_entity(
            "task",
            "task-42",
            dispatcher=dispatcher,
            hibernation=hc,
        )
        assert len(handles) == 1
        assert status == "clean"

        restored = store.hydrate(handles[0])
        assert restored.depth == 3

        pc.pause("task", "task-42", reason="test", agent_handles=[h.model_dump() for h in handles])
        pc.resume("task", "task-42")

        snapshots, status, _ = await pc.resume_rehydrate(
            "task",
            "task-42",
            dispatcher=dispatcher,
            hibernation=hc,
        )
        assert len(snapshots) == 1
        assert dispatcher.dispatched_tasks[0].depth == 3

    @pytest.mark.asyncio
    async def test_quiesce_entity_no_matching_task_returns_empty(self, tmp_path):
        base_dir = str(tmp_path / "hibernate")
        key = _load_hibernate_mac_key(base_dir)
        store = HibernationStore(base_dir=base_dir, mac_key=key)
        hc = HibernationController(store=store)

        task = MockTask(task_id="other-task", agent_name="other-agent", project_id="p")
        dispatcher = MockDispatcher([task])

        pc = PauseController(PauseStore(tmp_path / "pause"))
        handles, status, _errors = await pc.quiesce_entity(
            "agent",
            "nonexistent",
            dispatcher=dispatcher,
            hibernation=hc,
        )
        assert handles == []
        assert status == "clean"

    @pytest.mark.asyncio
    async def test_quiesce_entity_no_dispatcher_returns_empty(self, tmp_path):
        pc = PauseController(PauseStore(tmp_path / "pause"))
        handles, status, _errors = await pc.quiesce_entity(
            "agent",
            "agent-1",
            dispatcher=None,
            hibernation=None,
        )
        assert handles == []
        assert status == "clean"
