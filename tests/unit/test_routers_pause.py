"""Deep behavioral tests for routers/pause.py — Pause/resume endpoints for tasks, agents, infra, projects, models."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


@dataclass
class _PauseRecord:
    kind: str
    target_id: str
    paused_at: float = 0.0
    reason: str = ""
    resources: dict[str, object] | None = None
    last_state: dict[str, object] | None = None
    agent_handles: list[object] | None = None


def _make_controller() -> MagicMock:
    ctrl = MagicMock()
    record = _PauseRecord(kind="task", target_id="t1", paused_at=1234567890.0, reason="test")
    ctrl.pause.return_value = record
    ctrl.resume.return_value = record
    ctrl.list_paused.return_value = []
    ctrl.quiesce_project = AsyncMock()
    ctrl.quiesce_project.return_value = ([], "clean", [])
    ctrl.resume_rehydrate = AsyncMock()
    ctrl.resume_rehydrate.return_value = ([], "clean", [])
    return ctrl


class TestPauseRegister:
    def test_register_is_callable(self) -> None:
        from general_ludd.routers.pause import register

        assert callable(register)

    def test_register_adds_all_expected_paths(self) -> None:
        from general_ludd.routers.pause import register

        expected = {
            "/api/pause",
            "/api/pause/status",
            "/api/tasks/{task_id}/pause",
            "/api/tasks/{task_id}/resume",
            "/api/agents/{agent_id}/pause",
            "/api/agents/{agent_id}/resume",
            "/api/infra/{deployment_id}/pause",
            "/api/infra/{deployment_id}/resume",
            "/api/pause/project",
            "/api/pause/model",
            "/api/resume/project",
            "/api/resume/model",
        }
        app = FastAPI()
        register(app, {})
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        for ep in expected:
            assert ep in paths, f"Missing path: {ep}"

    def test_register_returns_none(self) -> None:
        from general_ludd.routers.pause import register

        result = register(FastAPI(), {})
        assert result is None


class TestPauseList:
    def test_no_controller_returns_empty_list(self) -> None:
        from general_ludd.routers.pause import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/pause")
        assert resp.status_code == 200
        data = resp.json()
        assert data["paused"] == []
        assert data["count"] == 0

    def test_with_controller_returns_records(self) -> None:
        from general_ludd.routers.pause import register

        ctrl = _make_controller()
        ctrl.list_paused.return_value = [
            _PauseRecord(kind="project", target_id="p1", paused_at=1.0, reason="maint"),
            _PauseRecord(kind="model", target_id="m1", paused_at=2.0, reason="deprecated"),
        ]
        app = FastAPI()
        app.state._pause_controller = ctrl
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/pause")
        data = resp.json()
        assert data["count"] == 2
        assert len(data["paused"]) == 2
        assert data["paused"][0]["kind"] == "project"
        assert data["paused"][0]["target_id"] == "p1"


class TestPauseStatus:
    def test_status_groups_by_kind(self) -> None:
        from general_ludd.routers.pause import register

        ctrl = _make_controller()
        ctrl.list_paused.return_value = [
            _PauseRecord(kind="project", target_id="p1", paused_at=1.0),
            _PauseRecord(kind="project", target_id="p2", paused_at=2.0),
            _PauseRecord(kind="model", target_id="m1", paused_at=3.0),
        ]
        app = FastAPI()
        app.state._pause_controller = ctrl
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/pause/status")
        data = resp.json()
        assert data["count"] == 3
        assert "by_type" in data
        assert len(data["by_type"]["project"]) == 2
        assert len(data["by_type"]["model"]) == 1

    def test_status_no_controller_returns_empty(self) -> None:
        from general_ludd.routers.pause import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/pause/status")
        data = resp.json()
        assert data["paused"] == []
        assert data["by_type"] == {}


class TestTaskPause:
    def test_pause_task_success(self) -> None:
        from general_ludd.routers.pause import register

        ctrl = _make_controller()
        ctrl.pause.return_value = _PauseRecord(kind="task", target_id="t42", paused_at=9.0, reason="urgent")
        app = FastAPI()
        app.state._pause_controller = ctrl
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/tasks/t42/pause", json={"reason": "urgent"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["paused"] is True
        assert data["kind"] == "task"
        assert data["target_id"] == "t42"
        assert data["reason"] == "urgent"
        ctrl.pause.assert_called_once_with("task", "t42", reason="urgent")

    def test_pause_task_no_controller(self) -> None:
        from general_ludd.routers.pause import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/tasks/t1/pause", json={})
        data = resp.json()
        assert data["paused"] is False
        assert "error" in data


class TestTaskResume:
    def test_resume_task_success(self) -> None:
        from general_ludd.routers.pause import register

        ctrl = _make_controller()
        ctrl.resume.return_value = _PauseRecord(kind="task", target_id="t42", paused_at=9.0)
        app = FastAPI()
        app.state._pause_controller = ctrl
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/tasks/t42/resume", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["resumed"] is True
        assert data["target_id"] == "t42"

    def test_resume_task_not_paused(self) -> None:
        from general_ludd.routers.pause import register

        ctrl = _make_controller()
        ctrl.resume.return_value = None
        app = FastAPI()
        app.state._pause_controller = ctrl
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/tasks/t42/resume")
        data = resp.json()
        assert data["resumed"] is False
        assert data["message"] == "was not paused"

    def test_resume_task_no_controller(self) -> None:
        from general_ludd.routers.pause import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/tasks/t1/resume")
        data = resp.json()
        assert data["resumed"] is False


class TestAgentPause:
    def test_pause_agent(self) -> None:
        from general_ludd.routers.pause import register

        ctrl = _make_controller()
        ctrl.pause.return_value = _PauseRecord(kind="agent", target_id="a7", paused_at=5.0, reason="idle")
        app = FastAPI()
        app.state._pause_controller = ctrl
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/agents/a7/pause", json={"reason": "idle"})
        data = resp.json()
        assert data["paused"] is True
        assert data["kind"] == "agent"
        ctrl.pause.assert_called_once_with("agent", "a7", reason="idle")


class TestAgentResume:
    def test_resume_agent_not_paused(self) -> None:
        from general_ludd.routers.pause import register

        ctrl = _make_controller()
        ctrl.resume.return_value = None
        app = FastAPI()
        app.state._pause_controller = ctrl
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/agents/a1/resume")
        data = resp.json()
        assert data["resumed"] is False


class TestInfraPauseResume:
    def test_pause_infra(self) -> None:
        from general_ludd.routers.pause import register

        ctrl = _make_controller()
        ctrl.pause.return_value = _PauseRecord(kind="infra", target_id="dep1", paused_at=1.0, reason="scale down")
        app = FastAPI()
        app.state._pause_controller = ctrl
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/infra/dep1/pause", json={"reason": "scale down"})
        data = resp.json()
        assert data["kind"] == "infra"
        assert data["paused"] is True

    def test_resume_infra_not_paused(self) -> None:
        from general_ludd.routers.pause import register

        ctrl = _make_controller()
        ctrl.resume.return_value = None
        app = FastAPI()
        app.state._pause_controller = ctrl
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/infra/dep1/resume")
        data = resp.json()
        assert data["resumed"] is False
        assert data["message"] == "was not paused"


class TestProjectPause:
    def test_pause_project_with_quiesce(self) -> None:
        from general_ludd.routers.pause import register

        ctrl = _make_controller()
        ctrl.pause.return_value = _PauseRecord(
            kind="project",
            target_id="p1",
            paused_at=3.0,
            reason="maint",
            resources={"spend": 10},
            last_state={"phase": "running"},
        )
        app = FastAPI()
        app.state._pause_controller = ctrl
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/pause/project", json={"target_id": "p1", "reason": "maint"})
        data = resp.json()
        assert data["paused"] is True
        assert data["kind"] == "project"
        assert data["target_id"] == "p1"
        ctrl.quiesce_project.assert_called_once()

    def test_pause_project_no_controller(self) -> None:
        from general_ludd.routers.pause import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/pause/project", json={"target_id": "p1"})
        data = resp.json()
        assert data["paused"] is False

    def test_pause_project_with_dispatcher_and_hibernation(self) -> None:
        from general_ludd.routers.pause import register

        ctrl = _make_controller()
        ctrl.pause.return_value = _PauseRecord(
            kind="project",
            target_id="p2",
            paused_at=4.0,
            reason="backup",
        )
        ctrl.quiesce_project.return_value = (["h1", "h2"], "clean", [])
        dispatcher = MagicMock()
        hibernation = MagicMock()

        app = FastAPI()
        app.state._pause_controller = ctrl
        app.state._agent_dispatcher = dispatcher
        app.state._hibernation_controller = hibernation
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/pause/project", json={"target_id": "p2", "reason": "backup"})
        data = resp.json()
        assert data["paused"] is True
        ctrl.quiesce_project.assert_called_once_with(
            "p2",
            dispatcher=dispatcher,
            hibernation=hibernation,
        )


class TestProjectResume:
    def test_resume_project_no_controller(self) -> None:
        from general_ludd.routers.pause import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/resume/project", json={"target_id": "p1"})
        data = resp.json()
        assert data["resumed"] is False

    def test_resume_project_not_paused(self) -> None:
        from general_ludd.routers.pause import register

        ctrl = _make_controller()
        ctrl.resume.return_value = None
        app = FastAPI()
        app.state._pause_controller = ctrl
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/resume/project", json={"target_id": "p1"})
        data = resp.json()
        assert data["resumed"] is False
        assert data["message"] == "was not paused"

    def test_resume_project_without_handles_no_rehydrate(self) -> None:
        from general_ludd.routers.pause import register

        ctrl = _make_controller()
        ctrl.resume.return_value = _PauseRecord(
            kind="project",
            target_id="p1",
            paused_at=1.0,
            agent_handles=None,
        )
        app = FastAPI()
        app.state._pause_controller = ctrl
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/resume/project", json={"target_id": "p1"})
        data = resp.json()
        assert data["resumed"] is True
        assert data["rehydrated_count"] == 0

    def test_resume_project_with_handles_rehydrates(self) -> None:
        from general_ludd.routers.pause import register

        ctrl = _make_controller()
        ctrl.resume.return_value = _PauseRecord(
            kind="project",
            target_id="p1",
            paused_at=1.0,
            agent_handles=["h1"],
        )
        ctrl.resume_rehydrate.return_value = ([{"id": "snap"}], "clean", [])
        dispatcher = MagicMock()
        hibernation = MagicMock()

        app = FastAPI()
        app.state._pause_controller = ctrl
        app.state._agent_dispatcher = dispatcher
        app.state._hibernation_controller = hibernation
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/resume/project", json={"target_id": "p1"})
        data = resp.json()
        assert data["resumed"] is True
        assert data["rehydrated_count"] == 1
        ctrl.resume_rehydrate.assert_called_once_with(
            "project",
            "p1",
            dispatcher=dispatcher,
            hibernation=hibernation,
        )


class TestModelPauseResume:
    def test_pause_model(self) -> None:
        from general_ludd.routers.pause import register

        ctrl = _make_controller()
        ctrl.pause.return_value = _PauseRecord(
            kind="model",
            target_id="m1",
            paused_at=5.0,
            reason="deprecated",
            resources={"input_cost": 0.01},
        )
        app = FastAPI()
        app.state._pause_controller = ctrl
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/pause/model", json={"target_id": "m1", "reason": "deprecated"})
        data = resp.json()
        assert data["paused"] is True
        assert data["kind"] == "model"

    def test_resume_model_not_paused(self) -> None:
        from general_ludd.routers.pause import register

        ctrl = _make_controller()
        ctrl.resume.return_value = None
        app = FastAPI()
        app.state._pause_controller = ctrl
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/resume/model", json={"target_id": "m1"})
        data = resp.json()
        assert data["resumed"] is False
