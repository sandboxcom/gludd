"""Hosted branch regressions for the self-improvement router."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Self

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import general_ludd.reload.self_improve as reload_self_improve
import general_ludd.routers.self_improve as self_improve_router
from general_ludd.schemas.todo import TodoStatus
from general_ludd.self_improve.approval import SELF_IMPROVE_WORK_TYPE


class _Session:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        return None


class _Factory:
    def __call__(self) -> _Session:
        return _Session()


class _Repository:
    def __init__(self, _session: object) -> None:
        self.todo = SimpleNamespace(
            todo_id="approval-1",
            work_type=SELF_IMPROVE_WORK_TYPE,
            status=TodoStatus.QUEUED.value,
            version=1,
        )

    async def get_by_id(self, _todo_id: str) -> SimpleNamespace:
        return self.todo

    async def transition(
        self,
        _todo_id: str,
        status: TodoStatus,
        *,
        expected_version: int,
    ) -> SimpleNamespace:
        return SimpleNamespace(status=status.value, version=expected_version + 1)


class _Workflow:
    def validate_improvement(self, worktree_path: str) -> SimpleNamespace:
        return SimpleNamespace(worktree=worktree_path, success=True)

    def apply_improvement(
        self, approval_id: str, validation: SimpleNamespace
    ) -> SimpleNamespace:
        return SimpleNamespace(
            applied=approval_id == "approval-1" and bool(validation),
            reload_needed=True,
        )

    def reload_if_needed(self, _apply_result: object) -> SimpleNamespace:
        return SimpleNamespace(status="reloaded")


def test_project_workspace_is_resolved_before_fail_closed_database_check(
    tmp_path: Path,
) -> None:
    app = FastAPI()
    app.state._project_manager = SimpleNamespace(
        get_project=lambda _project_id: SimpleNamespace(
            workspace_path=str(tmp_path)
        )
    )
    self_improve_router.register(app, {"todos": []})

    response = TestClient(app).post(
        "/admin/self-improve/apply",
        json={"kind": "config", "project_id": "project-1"},
    )

    assert response.status_code == 503
    assert "approval database" in response.json()["detail"]


@pytest.mark.parametrize(
    "project_manager",
    [None, SimpleNamespace(get_project=lambda _project_id: None)],
)
def test_optional_project_resolution_falls_through_to_database_gate(
    project_manager: object | None,
) -> None:
    app = FastAPI()
    app.state._project_manager = project_manager
    self_improve_router.register(app, {"todos": []})

    response = TestClient(app).post(
        "/admin/self-improve/apply",
        json={"kind": "config", "project_id": "missing-project"},
    )

    assert response.status_code == 503
    assert "approval database" in response.json()["detail"]


def test_released_non_config_change_executes_and_is_consumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.state._session_factory = _Factory()
    monkeypatch.setattr(self_improve_router, "TodoRepository", _Repository)
    monkeypatch.setattr(
        reload_self_improve, "SelfImprovementWorkflow", _Workflow
    )
    self_improve_router.register(app, {"todos": []})

    response = TestClient(app).post(
        "/admin/self-improve/apply",
        json={
            "kind": "code",
            "approval_id": "approval-1",
            "worktree_path": "/tmp/gludd-owned-worktree",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "todo_id": "approval-1",
        "validation_passed": True,
        "applied": True,
        "reload_needed": True,
        "reload_status": "reloaded",
    }


def test_priority_coercion_covers_bool_and_cap() -> None:
    assert self_improve_router._coerce_priority(True) == 5
    assert self_improve_router._coerce_priority(5001) == 1000
