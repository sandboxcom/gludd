"""Tests for D7.3 — durable MAC key, unconditional quiesce, rehydrate-on-resume."""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from general_ludd.agents.hibernation import (
    AgentEnvironmentSnapshot,
    HibernationController,
    HibernationStore,
    _load_hibernate_mac_key,
)
from general_ludd.controllers.pause_controller import PauseController, PauseRecord
from general_ludd.controllers.pause_store import PauseStore
from general_ludd.routers.pause import register

# ═══════════════════════════════════════════════════════════════════════════
# Durable MAC key — restart survival
# ═══════════════════════════════════════════════════════════════════════════


class TestDurableMacKey:
    def test_two_stores_same_durable_key_verify(self, tmp_path):
        base_dir = str(tmp_path / "hibernate")
        key = _load_hibernate_mac_key(base_dir)
        assert key is not None

        store_a = HibernationStore(base_dir=base_dir, mac_key=key)
        snap = AgentEnvironmentSnapshot(
            task_id="task-1",
            agent_name="test-agent",
            scratch={"description": "test"},
        )
        handle_a = store_a.dehydrate(snap)

        store_b = HibernationStore(base_dir=base_dir, mac_key=key)
        restored = store_b.hydrate(handle_a)
        assert restored.task_id == "task-1"
        assert restored.agent_name == "test-agent"
        assert restored.scratch == {"description": "test"}

    def test_two_stores_different_keys_fail(self, tmp_path):
        base_dir = str(tmp_path / "hibernate")
        key = _load_hibernate_mac_key(base_dir)
        assert key is not None

        store_a = HibernationStore(base_dir=base_dir, mac_key=key)
        snap = AgentEnvironmentSnapshot(task_id="task-1", agent_name="test")
        handle_a = store_a.dehydrate(snap)

        bad_store = HibernationStore(base_dir=base_dir)  # random ephemeral key
        from general_ludd.agents.hibernation import IntegrityError

        with pytest.raises(IntegrityError):
            bad_store.hydrate(handle_a)


# ═══════════════════════════════════════════════════════════════════════════
# Unconditional quiesce — bypasses should_dehydrate
# ═══════════════════════════════════════════════════════════════════════════


class MockDispatcher:
    def __init__(self, tasks):
        self._tasks = tasks

    async def get_active_tasks_for_project(self, project_id):
        return [t for t in self._tasks if t.project_id == project_id]

    async def quiesce_project(self, project_id):
        return []


class MockTask:
    def __init__(
        self, task_id, agent_name, project_id, description="", prompt="", parent_task_id=None, invoker_name=""
    ):
        self.task_id = task_id
        self.agent_name = agent_name
        self.project_id = project_id
        self.description = description
        self.prompt = prompt
        self.parent_task_id = parent_task_id
        self.invoker_name = invoker_name


class TestUnconditionalQuiesce:
    @pytest.mark.asyncio
    async def test_quiesce_dehydrates_shallow_tasks(self, tmp_path):
        """should_dehydrate requires depth>=3, messages>=8.
        D7.3 must bypass this — dehydrate ALL tasks unconditionally."""
        base_dir = str(tmp_path / "hibernate")
        key = _load_hibernate_mac_key(base_dir)
        store = HibernationStore(base_dir=base_dir, mac_key=key)
        controller = HibernationController(store=store)

        task = MockTask(
            task_id="shallow-task",
            agent_name="test-agent",
            project_id="proj-1",
            description="test description",
            prompt="test prompt",
        )
        dispatcher = MockDispatcher([task])

        pc = PauseController()
        result = await pc.quiesce_project(
            "proj-1",
            dispatcher=dispatcher,
            hibernation=controller,
        )
        handles, _status, _errors = result

        assert len(handles) == 1
        assert _status == "clean"
        assert _errors == []
        assert handles[0].task_id == "shallow-task"

    @pytest.mark.asyncio
    async def test_quiesce_captures_metadata_in_scratch(self, tmp_path):
        base_dir = str(tmp_path / "hibernate")
        key = _load_hibernate_mac_key(base_dir)
        store = HibernationStore(base_dir=base_dir, mac_key=key)
        controller = HibernationController(store=store)

        task = MockTask(
            task_id="meta-task",
            agent_name="test-agent",
            project_id="proj-1",
            description="a description",
            prompt="some prompt",
        )
        dispatcher = MockDispatcher([task])

        pc = PauseController()
        handles, _, _ = await pc.quiesce_project(
            "proj-1",
            dispatcher=dispatcher,
            hibernation=controller,
        )

        restored = store.hydrate(handles[0])
        assert restored.scratch["description"] == "a description"
        assert restored.scratch["prompt"] == "some prompt"
        assert restored.scratch["project_id"] == "proj-1"

    @pytest.mark.asyncio
    async def test_quiesce_empty_project_returns_clean(self, tmp_path):
        dispatcher = MockDispatcher([])
        base_dir = str(tmp_path / "hibernate")
        key = _load_hibernate_mac_key(base_dir)
        store = HibernationStore(base_dir=base_dir, mac_key=key)
        controller = HibernationController(store=store)

        pc = PauseController()
        handles, _status, _errors = await pc.quiesce_project(
            "proj-1",
            dispatcher=dispatcher,
            hibernation=controller,
        )

        assert handles == []
        assert _status == "clean"
        assert _errors == []


# ═══════════════════════════════════════════════════════════════════════════
# Quiesce status and errors on PauseRecord
# ═══════════════════════════════════════════════════════════════════════════


class TestQuiesceStatus:
    def test_pause_record_defaults(self):
        record = PauseRecord(kind="project", target_id="p1", paused_at=1.0)
        assert record.quiesce_status == "none"
        assert record.quiesce_errors == []

    def test_pause_record_stores_quiesce_fields(self, tmp_path):
        store = PauseStore(base_dir=str(tmp_path / "pause_store"))
        pc = PauseController(store=store)
        record = pc.pause(
            "project",
            "proj-1",
            quiesce_status="clean",
            quiesce_errors=[],
        )
        assert record.quiesce_status == "clean"
        assert record.quiesce_errors == []

    def test_pause_record_stores_degraded(self, tmp_path):
        store = PauseStore(base_dir=str(tmp_path / "pause_store"))
        pc = PauseController(store=store)
        record = pc.pause(
            "project",
            "proj-1",
            quiesce_status="degraded",
            quiesce_errors=["hydrate task-3 failed"],
        )
        assert record.quiesce_status == "degraded"
        assert record.quiesce_errors == ["hydrate task-3 failed"]

    def test_pause_record_survives_restart(self, tmp_path):
        store_a = PauseStore(base_dir=str(tmp_path / "pause_store"))
        pc_a = PauseController(store=store_a)
        pc_a.pause(
            "project",
            "proj-1",
            quiesce_status="degraded",
            quiesce_errors=["err1"],
            agent_handles=[{"task_id": "t1", "path": "/tmp/x", "checksum": "abc", "size_bytes": 100}],
        )

        store_b = PauseStore(base_dir=str(tmp_path / "pause_store"))
        pc_b = PauseController(store=store_b)
        record = pc_b.get("project", "proj-1")
        assert record is not None
        assert record.quiesce_status == "degraded"
        assert record.quiesce_errors == ["err1"]

    def test_idempotent_pause_preserves_first_quiesce_status(self, tmp_path):
        store = PauseStore(base_dir=str(tmp_path / "pause_store"))
        pc = PauseController(store=store)
        r1 = pc.pause("project", "p1", quiesce_status="clean")
        r2 = pc.pause("project", "p1", quiesce_status="degraded")
        assert r1.quiesce_status == "clean"
        assert r2.quiesce_status == "clean"


# ═══════════════════════════════════════════════════════════════════════════
# Router-level: rehydrate-on-resume
# ═══════════════════════════════════════════════════════════════════════════


class StubDispatcher:
    """Captures dispatched tasks for assertion."""

    def __init__(self):
        self.dispatched: list = []

    async def dispatch_one(self, task):
        self.dispatched.append(task)
        from general_ludd.agents.dispatcher import AgentTaskResult

        return AgentTaskResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status="completed",
            output="resumed",
        )

    async def get_active_tasks_for_project(self, project_id):
        return []

    async def quiesce_project(self, project_id):
        return []

    async def resume_project(self, project_id, rehydrated_snapshots):
        from general_ludd.agents.dispatcher import AgentTask
        from general_ludd.agents.hibernation import AgentEnvironmentSnapshot

        re_enqueued = []
        for snap in rehydrated_snapshots:
            if not isinstance(snap, AgentEnvironmentSnapshot):
                continue
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
            await self.dispatch_one(task)
            re_enqueued.append(task)
        return re_enqueued


@pytest.fixture
def app_with_resume(tmp_path):
    app = FastAPI()

    pause_base = str(tmp_path / "pause")
    key = _load_hibernate_mac_key(pause_base)

    pc = PauseController(store=PauseStore(base_dir=pause_base))
    hs = HibernationStore(base_dir=pause_base, mac_key=key)
    hc = HibernationController(store=hs)
    stub_dispatcher = StubDispatcher()

    app.state._pause_controller = pc
    app.state._hibernation_controller = hc
    app.state._agent_dispatcher = stub_dispatcher

    register(app, {})
    return app


@pytest.fixture
def client(app_with_resume):
    from fastapi.testclient import TestClient

    return TestClient(app_with_resume)


class TestRehydrateOnResume:
    def test_resume_rehydrates_agent_from_handle(self, app_with_resume, client):
        """Pause with dehydrated agent → resume rehydrates and re-dispatches."""
        dispatcher = app_with_resume.state._agent_dispatcher
        hc = app_with_resume.state._hibernation_controller

        snap = AgentEnvironmentSnapshot(
            task_id="agent-1",
            agent_name="test-agent",
            scratch={
                "description": "test task",
                "prompt": "do something",
                "project_id": "proj-42",
            },
        )
        handle = hc._store.dehydrate(snap)

        pc = app_with_resume.state._pause_controller
        pc.pause(
            "project",
            "proj-42",
            quiesce_status="clean",
            agent_handles=[handle.model_dump()],
        )

        resp = client.post("/api/resume/project", json={"target_id": "proj-42"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["resumed"] is True
        assert body["rehydrated_count"] == 1
        assert body["rehydrate_errors"] == []

        assert len(dispatcher.dispatched) == 1
        dispatched = dispatcher.dispatched[0]
        assert dispatched.task_id == "agent-1"
        assert dispatched.agent_name == "test-agent"
        assert dispatched.description == "test task"
        assert dispatched.prompt == "do something"
        assert dispatched.project_id == "proj-42"

    def test_resume_without_agent_handles(self, client):
        """Resume when record has no agent_handles → rehydrated_count is 0."""
        client.post("/api/pause/project", json={"target_id": "proj-99"})
        resp = client.post("/api/resume/project", json={"target_id": "proj-99"})
        assert resp.status_code == 200
        assert resp.json()["resumed"] is True
        assert resp.json()["rehydrated_count"] == 0

    def test_resume_not_paused(self, client):
        resp = client.post("/api/resume/project", json={"target_id": "never-paused"})
        assert resp.status_code == 200
        assert resp.json()["resumed"] is False

    def test_resume_handles_rehydrate_error_gracefully(self, app_with_resume, client):
        """Corrupted handle → rehydrate fails, resume still succeeds degraded."""
        pc = app_with_resume.state._pause_controller
        pc.pause(
            "project",
            "proj-bad",
            quiesce_status="clean",
            agent_handles=[
                {
                    "task_id": "bad-agent",
                    "path": "/nonexistent/file.json",
                    "checksum": "deadbeef",
                    "size_bytes": 0,
                }
            ],
        )

        resp = client.post("/api/resume/project", json={"target_id": "proj-bad"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["resumed"] is True
        assert body["rehydrated_count"] == 0
        assert len(body["rehydrate_errors"]) >= 1
