"""Hosted branch regressions for the self-improvement router."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Self

import pytest
from fastapi import FastAPI, HTTPException
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


def test_non_config_project_and_worktree_helpers_fail_closed(tmp_path: Path) -> None:
    """Stored project and path resolution rejects ambiguous boundary inputs."""
    app = FastAPI()
    with pytest.raises(HTTPException, match="invalid project identity"):
        self_improve_router._resolve_non_config_project_repo(app, "")
    with pytest.raises(HTTPException, match="workspace is unavailable"):
        self_improve_router._resolve_non_config_project_repo(app, "project-1")

    app.state._project_manager = SimpleNamespace(
        get_project=lambda _project_id: None
    )
    with pytest.raises(HTTPException, match="workspace is unavailable"):
        self_improve_router._resolve_non_config_project_repo(app, "project-1")

    broken_workspace = tmp_path / "broken-workspace"
    broken_workspace.mkdir()
    (broken_workspace / "repo").write_text("not a repository directory")
    app.state._project_manager = SimpleNamespace(
        get_project=lambda _project_id: SimpleNamespace(
            workspace_path=str(broken_workspace)
        )
    )
    with pytest.raises(HTTPException, match="workspace is unavailable"):
        self_improve_router._resolve_non_config_project_repo(app, "project-1")

    repo_root = tmp_path / "repo"
    worktree = repo_root / "worktrees" / "approved"
    worktree.mkdir(parents=True)
    assert self_improve_router._confine_non_config_worktree(
        "worktrees/approved",
        repo_root,
    ) == str(worktree.resolve())
    with pytest.raises(ValueError, match="missing or malformed"):
        self_improve_router._confine_non_config_worktree("", repo_root)

    regular_file = repo_root / "not-a-worktree"
    regular_file.write_text("not a directory")
    with pytest.raises(ValueError, match="not a directory"):
        self_improve_router._confine_non_config_worktree(
            str(regular_file),
            repo_root,
        )


def test_released_non_config_change_executes_and_is_consumed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    worktree = project_root / "repo" / "worktrees" / "approved"
    worktree.mkdir(parents=True)
    todo = SimpleNamespace(
        todo_id="approval-1",
        work_type=SELF_IMPROVE_WORK_TYPE,
        status=TodoStatus.QUEUED.value,
        version=1,
        project_id="project-1",
        plan_artifact=json.dumps(
            {
                "description": "approved",
                "kind": "code",
                "project_id": "project-1",
                "schema_version": 1,
                "title": "approved",
                "worktree_path": str(worktree.resolve()),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )

    class Repository(_Repository):
        def __init__(self, _session: object) -> None:
            self.todo = todo

        @classmethod
        def scoped(cls, session: object, project_id: str) -> Repository:
            assert project_id == "project-1"
            return cls(session)

    app = FastAPI()
    app.state._session_factory = _Factory()
    app.state._project_manager = SimpleNamespace(
        get_project=lambda _project_id: SimpleNamespace(
            workspace_path=str(project_root)
        )
    )
    monkeypatch.setattr(self_improve_router, "TodoRepository", Repository)
    monkeypatch.setattr(
        reload_self_improve, "SelfImprovementWorkflow", _Workflow
    )
    self_improve_router.register(app, {"todos": []})

    response = TestClient(app).post(
        "/admin/self-improve/apply",
        json={
            "kind": "code",
            "approval_id": "approval-1",
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


def test_released_non_config_change_uses_only_approved_project_and_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved_root = tmp_path / "approved-project"
    approved_worktree = approved_root / "repo" / "worktrees" / "approved"
    approved_worktree.mkdir(parents=True)
    attacker_root = tmp_path / "attacker-project"
    attacker_worktree = attacker_root / "repo" / "worktrees" / "attacker"
    attacker_worktree.mkdir(parents=True)
    artifact = json.dumps(
        {
            "description": "approved description",
            "kind": "code",
            "project_id": "approved-project",
            "schema_version": 1,
            "title": "approved title",
            "worktree_path": str(approved_worktree.resolve()),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    todo = SimpleNamespace(
        todo_id="approval-1",
        work_type=SELF_IMPROVE_WORK_TYPE,
        status=TodoStatus.QUEUED.value,
        version=1,
        project_id="approved-project",
        plan_artifact=artifact,
    )

    class Repository:
        def __init__(self, _session: object) -> None:
            pass

        @classmethod
        def scoped(cls, session: object, project_id: str) -> Repository:
            assert project_id == "approved-project"
            return cls(session)

        async def get_by_id(self, _todo_id: str) -> SimpleNamespace:
            return todo

        async def transition(
            self,
            _todo_id: str,
            status: TodoStatus,
            *,
            expected_version: int,
            project_id: str | None = None,
        ) -> SimpleNamespace:
            assert project_id in (None, "approved-project")
            return SimpleNamespace(status=status.value, version=expected_version + 1)

    validated_paths: list[str] = []

    class Workflow(_Workflow):
        def validate_improvement(self, worktree_path: str) -> SimpleNamespace:
            validated_paths.append(worktree_path)
            return super().validate_improvement(worktree_path)

    project_lookups: list[str] = []

    def get_project(project_id: str) -> SimpleNamespace:
        project_lookups.append(project_id)
        roots = {
            "approved-project": approved_root,
            "attacker-project": attacker_root,
        }
        return SimpleNamespace(workspace_path=str(roots[project_id]))

    app = FastAPI()
    app.state._session_factory = _Factory()
    app.state._project_manager = SimpleNamespace(get_project=get_project)
    monkeypatch.setattr(self_improve_router, "TodoRepository", Repository)
    monkeypatch.setattr(reload_self_improve, "SelfImprovementWorkflow", Workflow)
    self_improve_router.register(app, {"todos": []})

    response = TestClient(app).post(
        "/admin/self-improve/apply",
        json={
            "kind": "code",
            "approval_id": "approval-1",
            "project_id": "attacker-project",
            "worktree_path": str(attacker_worktree),
        },
    )

    assert response.status_code == 200
    assert validated_paths == [str(approved_worktree.resolve())]
    assert project_lookups == ["approved-project"]


@pytest.mark.parametrize("artifact_case", ["missing", "malformed", "outside", "absent"])
def test_invalid_approved_non_config_artifact_fails_before_workflow(
    artifact_case: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "approved-project"
    repo_root = project_root / "repo"
    repo_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    missing = repo_root / "missing"
    stored_path = outside if artifact_case == "outside" else missing
    artifact: str | None = json.dumps(
        {
            "description": "approved description",
            "kind": "code",
            "project_id": "approved-project",
            "schema_version": 1,
            "title": "approved title",
            "worktree_path": str(stored_path.resolve(strict=False)),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if artifact_case == "malformed":
        artifact = "not-json"
    elif artifact_case == "absent":
        artifact = None
    todo = SimpleNamespace(
        todo_id="approval-1",
        work_type=SELF_IMPROVE_WORK_TYPE,
        status=TodoStatus.QUEUED.value,
        version=1,
        project_id="approved-project",
        plan_artifact=artifact,
    )

    class Repository:
        def __init__(self, _session: object) -> None:
            pass

        async def get_by_id(self, _todo_id: str) -> SimpleNamespace:
            return todo

    workflow_calls: list[str] = []

    class Workflow:
        def __init__(self) -> None:
            workflow_calls.append("constructed")

    app = FastAPI()
    app.state._session_factory = _Factory()
    app.state._project_manager = SimpleNamespace(
        get_project=lambda _project_id: SimpleNamespace(
            workspace_path=str(project_root)
        )
    )
    monkeypatch.setattr(self_improve_router, "TodoRepository", Repository)
    monkeypatch.setattr(reload_self_improve, "SelfImprovementWorkflow", Workflow)
    self_improve_router.register(app, {"todos": []})

    response = TestClient(app, raise_server_exceptions=False).post(
        "/admin/self-improve/apply",
        json={
            "kind": "code",
            "approval_id": "approval-1",
            "project_id": "attacker-project",
            "worktree_path": str(outside),
        },
    )

    assert response.status_code == 422
    assert workflow_calls == []


def test_priority_coercion_covers_bool_and_cap() -> None:
    assert self_improve_router._coerce_priority(True) == 5
    assert self_improve_router._coerce_priority(5001) == 1000
