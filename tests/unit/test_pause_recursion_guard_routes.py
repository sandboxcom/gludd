"""API regressions for recursion-depth continuity across pause and resume."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.agents.dispatcher import AgentDispatcher
from general_ludd.agents.hibernation import (
    AgentEnvironmentSnapshot,
    HibernationController,
    HibernationStore,
    _load_hibernate_mac_key,
)
from general_ludd.agents.types import AgentTask
from general_ludd.controllers.pause_controller import PauseController
from general_ludd.controllers.pause_store import PauseStore
from general_ludd.routers.pause import register


class _RecordingDispatcher:
    """Minimal dispatcher seam that records rehydrated control state."""

    def __init__(self, task: AgentTask) -> None:
        self.task = task
        self.resume_calls: list[tuple[str, list[AgentEnvironmentSnapshot]]] = []

    async def get_active_tasks_by_agent_name(self, agent_name: str) -> list[AgentTask]:
        return [self.task] if self.task.agent_name == agent_name else []

    async def get_active_tasks_by_task_id(self, task_id: str) -> list[AgentTask]:
        return [self.task] if self.task.task_id == task_id else []

    async def resume_project(
        self,
        project_id: str,
        snapshots: Sequence[object],
    ) -> list[AgentTask]:
        typed = [snapshot for snapshot in snapshots if isinstance(snapshot, AgentEnvironmentSnapshot)]
        self.resume_calls.append((project_id, typed))
        return []


def _make_task(*, project_id: str = "project-original") -> AgentTask:
    """Build the active task used by route and controller regressions."""
    return AgentTask(
        task_id="task-deep",
        agent_name="agent-deep",
        description="continue nested work",
        prompt="resume",
        project_id=project_id,
        depth=4,
    )


def _make_hibernation(tmp_path: Path, name: str = "hibernation") -> HibernationController:
    """Build an authenticated, worktree-local hibernation controller."""
    hibernation_dir = str(tmp_path / name)
    mac_key = _load_hibernate_mac_key(hibernation_dir)
    assert mac_key is not None
    return HibernationController(
        HibernationStore(base_dir=hibernation_dir, mac_key=mac_key)
    )


def _make_app(tmp_path: Path) -> tuple[FastAPI, _RecordingDispatcher]:
    task = _make_task()
    dispatcher = _RecordingDispatcher(task)
    app = FastAPI()
    app.state._pause_controller = PauseController(PauseStore(tmp_path / "pause"))
    app.state._agent_dispatcher = dispatcher
    app.state._hibernation_controller = _make_hibernation(tmp_path)
    register(app, {})
    return app, dispatcher


@pytest.mark.parametrize(
    ("kind", "target_id"),
    [("agent", "agent-deep"), ("task", "task-deep")],
)
def test_entity_routes_preserve_depth_and_original_project(
    tmp_path: Path,
    kind: str,
    target_id: str,
) -> None:
    """Public pause/resume routes must use the canonical snapshot path."""
    app, dispatcher = _make_app(tmp_path)
    client = TestClient(app)

    pause = client.post(f"/api/{kind}s/{target_id}/pause", json={"reason": "hold"})
    resume = client.post(f"/api/{kind}s/{target_id}/resume", json={})

    assert pause.status_code == 200
    assert pause.json()["quiesce_status"] == "clean"
    assert resume.status_code == 200
    assert resume.json()["rehydrated_count"] == 1
    assert resume.json()["rehydrate_errors"] == []
    assert len(dispatcher.resume_calls) == 1
    project_id, snapshots = dispatcher.resume_calls[0]
    assert project_id == "project-original"
    assert len(snapshots) == 1
    assert snapshots[0].depth == 4


@pytest.mark.asyncio
async def test_resume_legacy_snapshot_falls_back_to_entity_id(tmp_path: Path) -> None:
    """Legacy snapshots without project metadata retain a safe resume target."""
    dispatcher = _RecordingDispatcher(_make_task(project_id="legacy-task"))
    hibernation = _make_hibernation(tmp_path, "legacy-hibernation")
    snapshot = AgentEnvironmentSnapshot(
        task_id="legacy-task",
        agent_name="agent-deep",
        depth=4,
        scratch={"description": "legacy", "prompt": "resume"},
    )
    handle = hibernation._store.dehydrate(snapshot)
    controller = PauseController(PauseStore(tmp_path / "legacy-pause"))
    controller.pause("task", "legacy-task", agent_handles=[handle])
    controller.resume("task", "legacy-task")

    snapshots, status, errors = await controller.resume_rehydrate(
        "task", "legacy-task", cast(AgentDispatcher, dispatcher), hibernation
    )

    assert status == "clean"
    assert errors == []
    assert len(snapshots) == 1
    restored = snapshots[0]
    assert isinstance(restored, AgentEnvironmentSnapshot)
    assert restored.depth == 4
    assert dispatcher.resume_calls[0][0] == "legacy-task"


@pytest.mark.asyncio
async def test_resume_skips_unsupported_handle_shape(tmp_path: Path) -> None:
    """Unknown in-memory handle shapes cannot escape into the dispatcher."""
    dispatcher = _RecordingDispatcher(_make_task())
    hibernation = _make_hibernation(tmp_path, "unsupported-hibernation")
    controller = PauseController(PauseStore(tmp_path / "unsupported-pause"))
    record = controller.pause("task", "task-deep")
    record.agent_handles.append("unsupported")
    controller.resume("task", "task-deep")

    snapshots, status, errors = await controller.resume_rehydrate(
        "task", "task-deep", cast(AgentDispatcher, dispatcher), hibernation
    )

    assert snapshots == []
    assert status == "clean"
    assert errors == []
    assert dispatcher.resume_calls == []


@pytest.mark.asyncio
async def test_entity_quiesce_non_entity_kind_is_noop(tmp_path: Path) -> None:
    """The entity helper leaves project-level orchestration to its own path."""
    dispatcher = _RecordingDispatcher(_make_task())
    hibernation = _make_hibernation(tmp_path, "project-hibernation")

    handles, status, errors = await PauseController(
        PauseStore(tmp_path / "project-pause")
    ).quiesce_entity(
        "project",
        "project-original",
        cast(AgentDispatcher, dispatcher),
        hibernation,
    )

    assert handles == []
    assert status == "clean"
    assert errors == []
    assert dispatcher.resume_calls == []


@pytest.mark.asyncio
async def test_entity_quiesce_reports_snapshot_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entity pause reports failed durable capture instead of claiming clean."""
    dispatcher = _RecordingDispatcher(_make_task())
    hibernation = _make_hibernation(tmp_path, "failing-hibernation")

    async def fail_snapshot(_snapshot: AgentEnvironmentSnapshot) -> None:
        raise OSError("snapshot unavailable")

    monkeypatch.setattr(hibernation._store, "dehydrate_async", fail_snapshot)
    handles, status, errors = await PauseController(
        PauseStore(tmp_path / "failing-pause")
    ).quiesce_entity(
        "task", "task-deep", cast(AgentDispatcher, dispatcher), hibernation
    )

    assert handles == []
    assert status == "degraded"
    assert errors == ["task-deep: snapshot unavailable"]
