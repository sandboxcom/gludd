"""D.7.3: Quiesce at dispatcher seam + rehydrating resume.

Covers:
  1. Dispatcher.quiesce_project drains in-flight tasks
  2. PauseController.quiesce_project captures snapshots via hibernation
  3. Quiesce status tracking on pause records
  4. Rehydrating resume re-enqueues tasks
  5. Graceful degradation when subsystems absent
  6. Error handling during quiesce and resume
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.agents.dispatcher import AgentDispatcher
from general_ludd.agents.hibernation import (
    AgentEnvironmentSnapshot,
    HibernationController,
    HibernationHandle,
    HibernationStore,
)
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentTask


class FakeRegistry:
    def get(self, name):
        return AgentConfig(
            name=name, description="fake", type="subagent",  # pyright: ignore[reportArgumentType]
            permissions=AgentPermission(),
        )

    def can_invoke(self, invoker, name):
        return True


def _make_task(task_id, project_id, **kw):
    return AgentTask(
        task_id=task_id,
        agent_name="test-agent",
        description=f"desc {task_id}",
        prompt=f"prompt {task_id}",
        project_id=project_id,
        **kw,
    )


# ---------------------------------------------------------------------------
# 1. Dispatcher.quiesce_project drains
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_quiesce_returns_cancelled_results():
    disp = AgentDispatcher(FakeRegistry())  # pyright: ignore[reportArgumentType]
    disp._active_tasks["t1"] = _make_task("t1", "proj-a")
    disp._active_tasks["t2"] = _make_task("t2", "proj-a")
    disp._active_tasks["t3"] = _make_task("t3", "proj-b")
    disp._active_count = 3

    results = await disp.quiesce_project("proj-a")
    assert len(results) == 2
    statuses = {r.status for r in results}
    assert statuses == {"cancelled"}
    ids = {r.task_id for r in results}
    assert ids == {"t1", "t2"}


@pytest.mark.asyncio
async def test_dispatcher_quiesce_no_tasks():
    disp = AgentDispatcher(FakeRegistry())  # pyright: ignore[reportArgumentType]
    disp._active_tasks["t1"] = _make_task("t1", "proj-b")
    results = await disp.quiesce_project("proj-a")
    assert results == []


@pytest.mark.asyncio
async def test_dispatcher_quiesce_empty_active():
    disp = AgentDispatcher(FakeRegistry())  # pyright: ignore[reportArgumentType]
    results = await disp.quiesce_project("proj-a")
    assert results == []


# ---------------------------------------------------------------------------
# 2. PauseController.quiesce_project captures snapshots
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_hibernation_store(tmp_path):
    store = HibernationStore(base_dir=str(tmp_path / "hib"))
    controller = HibernationController(store, min_depth=0, min_context_messages=0)
    return controller


@pytest.fixture
def fake_dispatcher():
    disp = MagicMock(spec=AgentDispatcher)
    disp.quiesce_project = AsyncMock(return_value=[])
    disp.get_active_tasks_for_project = AsyncMock(return_value=[])
    disp.resume_project = AsyncMock(return_value=[])
    return disp


@pytest.mark.asyncio
async def test_pause_controller_quiesce_no_subsystems():
    from general_ludd.controllers.pause_controller import PauseController

    pc = PauseController()
    handles, status, errors = await pc.quiesce_project("proj-x", None, None)
    assert handles == []
    assert status == "clean"
    assert errors == []


@pytest.mark.asyncio
async def test_pause_controller_quiesce_drains_then_snapshots(fake_dispatcher, fake_hibernation_store):
    from general_ludd.controllers.pause_controller import PauseController

    task = _make_task("t-quiesce", "proj-x")
    fake_dispatcher.get_active_tasks_for_project.return_value = [task]

    pc = PauseController()
    handles, status, errors = await pc.quiesce_project(
        "proj-x", dispatcher=fake_dispatcher, hibernation=fake_hibernation_store
    )

    fake_dispatcher.quiesce_project.assert_awaited_once_with("proj-x")
    assert len(handles) == 1
    assert status == "clean"
    assert errors == []


@pytest.mark.asyncio
async def test_pause_controller_quiesce_degraded_on_hydration_failure(fake_dispatcher):
    from general_ludd.controllers.pause_controller import PauseController

    task = _make_task("t-fail", "proj-x")
    fake_dispatcher.get_active_tasks_for_project.return_value = [task]

    bad_store = MagicMock()
    bad_store.dehydrate_async = AsyncMock(side_effect=RuntimeError("disk full"))
    bad_hibernation = MagicMock()
    bad_hibernation._store = bad_store

    pc = PauseController()
    handles, status, errors = await pc.quiesce_project(
        "proj-x", dispatcher=fake_dispatcher, hibernation=bad_hibernation
    )

    assert status == "degraded"
    assert len(errors) == 1
    assert "disk full" in errors[0]


@pytest.mark.asyncio
async def test_pause_controller_quiesce_no_active_tasks(fake_dispatcher, fake_hibernation_store):
    from general_ludd.controllers.pause_controller import PauseController

    pc = PauseController()
    handles, status, errors = await pc.quiesce_project(
        "proj-x", dispatcher=fake_dispatcher, hibernation=fake_hibernation_store
    )

    assert handles == []
    assert status == "clean"


# ---------------------------------------------------------------------------
# 3. Quiesce status tracking
# ---------------------------------------------------------------------------


def test_pause_record_quiesce_fields():
    from general_ludd.controllers.pause_controller import PauseRecord

    r = PauseRecord(kind="project", target_id="p1", paused_at=0.0)
    assert r.quiesce_status == "none"
    assert r.quiesce_errors == []

    r.quiesce_status = "clean"
    r.quiesce_errors.append("oops")
    assert r.quiesce_status == "clean"
    assert r.quiesce_errors == ["oops"]


def test_controller_record_quiesce_status(tmp_path):
    from general_ludd.controllers.pause_controller import PauseController
    from general_ludd.controllers.pause_store import PauseStore

    store = PauseStore(base_dir=str(tmp_path / "ps"))
    pc = PauseController(store=store)

    pc.pause("project", "p-q", quiesce_status="clean", quiesce_errors=["err1"])
    record = pc.get("project", "p-q")
    assert record is not None
    assert record.quiesce_status == "clean"
    assert record.quiesce_errors == ["err1"]


# ---------------------------------------------------------------------------
# 4. Rehydrating resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_rehydrate_no_record():
    from general_ludd.controllers.pause_controller import PauseController

    pc = PauseController()
    snaps, status, errors = await pc.resume_rehydrate("project", "nope", None, None)
    assert snaps == []
    assert status == "clean"


@pytest.mark.asyncio
async def test_resume_rehydrate_no_subsystems():
    from general_ludd.controllers.pause_controller import PauseController
    from general_ludd.controllers.pause_store import PauseStore

    pc = PauseController(store=PauseStore())
    pc.pause("project", "proj-x")
    snaps, status, errors = await pc.resume_rehydrate("project", "proj-x", None, None)
    assert snaps == []
    assert status == "clean"


@pytest.mark.asyncio
async def test_resume_rehydrate_restores_and_re_enqueues(fake_dispatcher, fake_hibernation_store, tmp_path):
    from general_ludd.controllers.pause_controller import PauseController
    from general_ludd.controllers.pause_store import PauseStore

    snap = AgentEnvironmentSnapshot(
        task_id="t-resume",
        agent_name="test-agent",
        scratch={"description": "a test", "prompt": "do it", "project_id": "proj-x"},
    )
    handle = fake_hibernation_store._store.dehydrate(snap)

    store = PauseStore(base_dir=str(tmp_path / "ps"))
    pc = PauseController(store=store)
    pc.pause(
        "project",
        "proj-x",
        agent_handles=[handle.model_dump()],
        quiesce_status="clean",
    )

    snaps, status, errors = await pc.resume_rehydrate(
        "project",
        "proj-x",
        dispatcher=fake_dispatcher,
        hibernation=fake_hibernation_store,
    )

    assert status == "clean"
    assert len(snaps) == 1
    assert snaps[0].task_id == "t-resume"

    fake_dispatcher.resume_project.assert_awaited_once()
    re_enqueued = fake_dispatcher.resume_project.call_args[0][1]
    assert len(re_enqueued) == 1


@pytest.mark.asyncio
async def test_resume_rehydrate_multiple_handles(fake_dispatcher, fake_hibernation_store, tmp_path):
    from general_ludd.controllers.pause_controller import PauseController
    from general_ludd.controllers.pause_store import PauseStore

    snap_a = AgentEnvironmentSnapshot(
        task_id="t-a",
        agent_name="agent-a",
        scratch={"description": "A", "prompt": "do A", "project_id": "proj-x"},
    )
    snap_b = AgentEnvironmentSnapshot(
        task_id="t-b",
        agent_name="agent-b",
        scratch={"description": "B", "prompt": "do B", "project_id": "proj-x"},
    )
    handle_a = fake_hibernation_store._store.dehydrate(snap_a)
    handle_b = fake_hibernation_store._store.dehydrate(snap_b)

    store = PauseStore(base_dir=str(tmp_path / "ps"))
    pc = PauseController(store=store)
    pc.pause(
        "project",
        "proj-x",
        agent_handles=[handle_a.model_dump(), handle_b.model_dump()],
        quiesce_status="clean",
    )

    snaps, status, errors = await pc.resume_rehydrate(
        "project", "proj-x",
        dispatcher=fake_dispatcher,
        hibernation=fake_hibernation_store,
    )

    assert status == "clean"
    assert len(snaps) == 2


@pytest.mark.asyncio
async def test_resume_rehydrate_degraded_on_hydration_failure(fake_dispatcher, tmp_path):
    from general_ludd.controllers.pause_controller import PauseController
    from general_ludd.controllers.pause_store import PauseStore

    bad_handle = HibernationHandle(
        task_id="t-bad",
        path="/nonexistent.snapshot.json",
        checksum="deadbeef",
        size_bytes=0,
    )

    store = PauseStore(base_dir=str(tmp_path / "ps"))
    pc = PauseController(store=store)
    pc.pause(
        "project", "proj-x",
        agent_handles=[bad_handle.model_dump()],
        quiesce_status="clean",
    )
    del pc

    from general_ludd.controllers.pause_store import PauseStore

    pc2 = PauseController(store=PauseStore(base_dir=str(tmp_path / "ps")))

    snaps, status, errors = await pc2.resume_rehydrate(
        "project", "proj-x",
        dispatcher=fake_dispatcher,
        hibernation=fake_hibernation_store,
    )

    assert status == "degraded"
    assert len(errors) >= 1


# ---------------------------------------------------------------------------
# 5. Dispatcher.resume_project re-enqueues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_resume_project_creates_tasks():
    disp = AgentDispatcher(FakeRegistry())  # pyright: ignore[reportArgumentType]
    snaps = [
        AgentEnvironmentSnapshot(
            task_id="t-renq",
            agent_name="test-agent",
            scratch={"description": "resumed desc", "prompt": "resumed prompt", "project_id": "proj-x"},
        )
    ]
    tasks = await disp.resume_project("proj-x", list(snaps))
    assert len(tasks) == 1
    assert tasks[0].task_id == "t-renq"
    assert tasks[0].description == "resumed desc"
    assert tasks[0].prompt == "resumed prompt"
    assert tasks[0].project_id == "proj-x"


@pytest.mark.asyncio
async def test_dispatcher_resume_project_skips_non_snapshots():
    disp = AgentDispatcher(FakeRegistry())  # pyright: ignore[reportArgumentType]
    tasks = await disp.resume_project("proj-x", [42, "string"])
    assert tasks == []


# ---------------------------------------------------------------------------
# 6. End-to-end: pause + quiesce → resume + rehydrate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_quiesce_resume_lifecycle(fake_dispatcher, fake_hibernation_store, tmp_path):
    from general_ludd.controllers.pause_controller import PauseController
    from general_ludd.controllers.pause_store import PauseStore

    task = _make_task("t-lifecycle", "proj-e2e")
    fake_dispatcher.get_active_tasks_for_project.return_value = [task]

    store = PauseStore(base_dir=str(tmp_path / "ps"))
    pc = PauseController(store=store)

    handles, qstatus, qerrors = await pc.quiesce_project(
        "proj-e2e",
        dispatcher=fake_dispatcher,
        hibernation=fake_hibernation_store,
    )
    assert qstatus == "clean"
    assert len(handles) == 1

    pc.pause(
        "project",
        "proj-e2e",
        agent_handles=[h.model_dump() for h in handles],
        quiesce_status=qstatus,
        quiesce_errors=qerrors,
    )
    assert pc.is_paused("project", "proj-e2e")

    pc.resume("project", "proj-e2e")
    assert not pc.is_paused("project", "proj-e2e")


# ---------------------------------------------------------------------------
# 7. Quiesce survives persist roundtrip
# ---------------------------------------------------------------------------


def test_quiesce_handles_survive_persist(tmp_path):
    from general_ludd.controllers.pause_controller import PauseController
    from general_ludd.controllers.pause_store import PauseStore

    store = PauseStore(base_dir=str(tmp_path / "ps"))
    pc = PauseController(store=store)

    handle_dict = {
        "task_id": "t-survivor",
        "path": "/tmp/hib/t-survivor.snapshot.json",
        "checksum": "abc123",
        "size_bytes": 1024,
        "depth": 0,
    }

    pc.pause(
        "project",
        "proj-survive",
        agent_handles=[handle_dict],
        quiesce_status="clean",
        quiesce_errors=[],
    )
    del pc

    pc2 = PauseController(store=PauseStore(base_dir=str(tmp_path / "ps")))
    record = pc2.get("project", "proj-survive")
    assert record is not None
    assert record.quiesce_status == "clean"
    assert len(record.agent_handles) == 1
    assert record.agent_handles[0]["task_id"] == "t-survivor"


# ---------------------------------------------------------------------------
# 8. Quiesce with no hibernation forces status to none
# ---------------------------------------------------------------------------


def test_record_quiesce_persists_through_pause():
    from general_ludd.controllers.pause_controller import PauseController
    from general_ludd.controllers.pause_store import PauseStore

    pc = PauseController(store=PauseStore())
    pc.pause("project", "p-nohib", quiesce_status="none")
    rec = pc.get("project", "p-nohib")
    assert rec is not None
    assert rec.quiesce_status == "none"
    assert rec.agent_handles == []
