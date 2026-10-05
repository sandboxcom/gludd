"""Router coverage for managed self-improvement repository binding changes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Self, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import general_ludd.routers.self_improve as self_improve_router
from general_ludd.projects.repository_binding import ProjectRepositoryBinding
from general_ludd.schemas.todo import TodoStatus
from general_ludd.self_improve.codex_comparison import CodexReference
from general_ludd.self_improve.managed_runner import ApprovedSelfImprovePlan
from general_ludd.self_improve.staging import (
    MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
    ManagedSelfImprovePlanRequest,
    build_managed_plan_request_payload,
)

_BASELINE = "a" * 40
_REFERENCE = "b" * 40


class _Session:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: object | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        return None


class _Factory:
    def __call__(self) -> _Session:
        return _Session()


def _request(project_id: str = "proj-managed") -> ManagedSelfImprovePlanRequest:
    payload = build_managed_plan_request_payload(
        {
            "title": "Exercise managed router binding",
            "description": "Reject stale or unavailable project bindings.",
            "work_type": "test",
            "source": "self_improve_harness",
            "gap_type": "missing_tests",
            "source_file": "src/general_ludd/routers/self_improve.py",
            "task_type": "test_write",
            "blocker_kind": "coverage",
            "incident_count": 1,
            "recent_todo_ids": [],
            "test_commands": [
                "make test-files "
                "TESTFILES=tests/unit/test_self_improve_router_binding_coverage.py"
            ],
        },
        project_id=project_id,
    )
    return ManagedSelfImprovePlanRequest.from_json(str(payload["plan_artifact"]))


def _held_todo(request: ManagedSelfImprovePlanRequest) -> SimpleNamespace:
    return SimpleNamespace(
        todo_id="TODO-SI-BINDING",
        project_id="proj-managed",
        status=TodoStatus.APPROVAL_REQUIRED.value,
        work_type="self_improve",
        approval_policy=MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
        plan_artifact=request.to_json(),
        version=4,
    )


def _factory() -> async_sessionmaker[AsyncSession]:
    return cast(async_sessionmaker[AsyncSession], _Factory())


def _raise_lookup(_project_id: str) -> None:
    raise LookupError("private project lookup detail")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "manager",
    [
        None,
        SimpleNamespace(get_project=lambda _project_id: None),
        SimpleNamespace(
            get_project=lambda _project_id: SimpleNamespace(
                active=False,
                workspace_path="tenant/proj-managed",
                repo_url="https://example.com/org/managed.git",
            )
        ),
        SimpleNamespace(
            get_project=lambda _project_id: SimpleNamespace(
                active=True,
                workspace_path="../escape",
                repo_url="https://example.com/org/managed.git",
            )
        ),
        SimpleNamespace(get_project=_raise_lookup),
    ],
)
async def test_prepare_rejects_unknown_or_malformed_current_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager: object,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    request = _request()
    repository = AsyncMock()
    repository.get_by_id.return_value = _held_todo(request)
    prepare = MagicMock()
    app = FastAPI()
    app.state._project_manager = manager
    monkeypatch.setattr(
        self_improve_router,
        "_resolve_non_config_project_repo",
        lambda _app, _project_id: repo_root,
    )
    monkeypatch.setattr(
        self_improve_router,
        "TodoRepository",
        lambda _session: repository,
    )
    monkeypatch.setattr(
        self_improve_router,
        "prepare_managed_self_improve_plan",
        prepare,
    )

    with pytest.raises(HTTPException) as exc_info:
        await self_improve_router._prepare_managed_approval(
            app,
            _factory(),
            "TODO-SI-BINDING",
            {"baseline_ref": _BASELINE, "reference_ref": _REFERENCE},
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == (
        "managed self-improve project binding is unavailable"
    )
    prepare.assert_not_called()
    repository.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_prepare_rejects_plan_built_for_replaced_repository_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    request = _request()
    repository = AsyncMock()
    repository.get_by_id.return_value = _held_todo(request)
    stale_binding = ProjectRepositoryBinding.for_project(
        project_id="proj-managed",
        workspace_path="tenant/proj-managed",
        repo_url="https://example.com/org/replaced.git",
    )
    stale_plan = ApprovedSelfImprovePlan.approve(
        approval_id="TODO-SI-BINDING",
        todo_id="TODO-SI-BINDING",
        project_id="proj-managed",
        repo_root=repo_root,
        repository_binding_digest=stale_binding.digest,
        task=request.task,
        reference=CodexReference(
            baseline_sha=_BASELINE,
            reference_sha=_REFERENCE,
            changed_files=frozenset({"src/general_ludd/routers/self_improve.py"}),
            test_files=frozenset(
                {"tests/unit/test_self_improve_router_binding_coverage.py"}
            ),
            changed_lines=1,
            elapsed_seconds=0.1,
        ),
        prompt="bounded router binding prompt",
        required_output_tokens=256,
        max_attempts=1,
    )
    app = FastAPI()
    app.state._project_manager = SimpleNamespace(
        get_project=lambda _project_id: SimpleNamespace(
            active=True,
            workspace_path="tenant/proj-managed",
            repo_url="https://example.com/org/current.git",
        )
    )
    monkeypatch.setattr(
        self_improve_router,
        "_resolve_non_config_project_repo",
        lambda _app, _project_id: repo_root,
    )
    monkeypatch.setattr(
        self_improve_router,
        "TodoRepository",
        lambda _session: repository,
    )
    monkeypatch.setattr(
        self_improve_router,
        "prepare_managed_self_improve_plan",
        MagicMock(return_value=stale_plan),
    )

    with pytest.raises(HTTPException) as exc_info:
        await self_improve_router._prepare_managed_approval(
            app,
            _factory(),
            "TODO-SI-BINDING",
            {"baseline_ref": _BASELINE, "reference_ref": _REFERENCE},
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == (
        "approval TODO-SI-BINDING managed plan preparation failed"
    )
    repository.update.assert_not_awaited()


def test_binding_rebind_tracks_the_current_repository_identity() -> None:
    project = SimpleNamespace(
        active=True,
        workspace_path="tenant/proj-managed",
        repo_url="https://example.com/org/original.git",
    )
    app = FastAPI()
    app.state._project_manager = SimpleNamespace(
        get_project=lambda _project_id: project
    )

    original = self_improve_router._resolve_project_repository_binding(
        app,
        "proj-managed",
    )
    project.repo_url = "https://example.com/org/rebound.git"
    rebound = self_improve_router._resolve_project_repository_binding(
        app,
        "proj-managed",
    )

    assert rebound.project_id == original.project_id == "proj-managed"
    assert rebound.workspace_key == original.workspace_key == "tenant/proj-managed"
    assert rebound.repository_fingerprint != original.repository_fingerprint
    assert rebound.digest != original.digest


def test_non_config_repository_resolution_fails_closed_and_uses_state_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise every trust-boundary outcome in repository resolution."""
    app = FastAPI()

    with pytest.raises(HTTPException, match="invalid project identity"):
        self_improve_router._resolve_non_config_project_repo(app, " proj-managed ")

    with pytest.raises(HTTPException, match="workspace is unavailable"):
        self_improve_router._resolve_non_config_project_repo(app, "proj-managed")

    app.state._project_manager = SimpleNamespace(
        get_project=lambda _project_id: None
    )
    with pytest.raises(HTTPException, match="workspace is unavailable"):
        self_improve_router._resolve_non_config_project_repo(app, "proj-managed")

    workspace = tmp_path / "regular-file-workspace"
    workspace.mkdir()
    (workspace / "repo").write_text("not a directory")
    app.state._project_manager = SimpleNamespace(
        get_project=lambda _project_id: SimpleNamespace(
            workspace_path=str(workspace),
        )
    )
    with pytest.raises(HTTPException, match="workspace is unavailable"):
        self_improve_router._resolve_non_config_project_repo(app, "proj-managed")

    workspace_base = tmp_path / "state" / "workspaces"
    relative_repo = workspace_base / "tenant" / "proj-managed" / "repo"
    relative_repo.mkdir(parents=True)
    monkeypatch.setattr(
        "general_ludd.projects.workspace.default_workspace_base",
        lambda: str(workspace_base),
    )
    app.state._project_manager = SimpleNamespace(
        get_project=lambda _project_id: SimpleNamespace(
            workspace_path="tenant/proj-managed",
            repo_url="https://example.com/org/managed.git",
        )
    )

    assert (
        self_improve_router._resolve_non_config_project_repo(app, "proj-managed")
        == relative_repo.resolve()
    )


def test_non_config_worktree_confinement_covers_all_path_shapes(
    tmp_path: Path,
) -> None:
    """Accept a relative directory and reject malformed, file, and escaped paths."""
    repo_root = tmp_path / "repo"
    worktree = repo_root / "worktrees" / "candidate"
    worktree.mkdir(parents=True)

    assert self_improve_router._confine_non_config_worktree(
        "worktrees/candidate",
        repo_root,
    ) == str(worktree.resolve())

    with pytest.raises(ValueError, match="missing or malformed"):
        self_improve_router._confine_non_config_worktree(None, repo_root)

    regular_file = repo_root / "proposal.patch"
    regular_file.write_text("patch")
    with pytest.raises(ValueError, match="not a directory"):
        self_improve_router._confine_non_config_worktree(
            str(regular_file),
            repo_root,
        )

    escaped = tmp_path / "outside"
    escaped.mkdir()
    with pytest.raises(ValueError, match="escapes"):
        self_improve_router._confine_non_config_worktree(
            str(escaped),
            repo_root,
        )


@pytest.mark.asyncio
async def test_prepare_rejects_each_stale_approval_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject missing, retyped, legacy, and project-drifted approval rows."""
    request = _request()
    repository = AsyncMock()
    monkeypatch.setattr(
        self_improve_router,
        "TodoRepository",
        lambda _session: repository,
    )
    app = FastAPI()

    def approval(**updates: object) -> SimpleNamespace:
        values = vars(_held_todo(request)).copy()
        values.update(updates)
        return SimpleNamespace(**values)

    cases = (
        (None, 404, "not found"),
        (approval(status=TodoStatus.QUEUED.value), 409, "not awaiting preparation"),
        (approval(work_type="code"), 409, "not a self-improve record"),
        (approval(approval_policy="legacy"), 409, "legacy self-improve artifact"),
        (approval(project_id=7), 422, "invalid project identity"),
        (approval(project_id="proj-replaced"), 422, "project identity drifted"),
    )
    for row, expected_status, expected_detail in cases:
        repository.get_by_id.return_value = row
        with pytest.raises(HTTPException) as exc_info:
            await self_improve_router._prepare_managed_approval(
                app,
                _factory(),
                "TODO-SI-BINDING",
                {"baseline_ref": _BASELINE, "reference_ref": _REFERENCE},
            )
        assert exc_info.value.status_code == expected_status
        assert expected_detail in str(exc_info.value.detail)


def test_prepare_route_fails_without_database_and_on_lost_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the public preparation route closed without its DB or human hold."""
    app = FastAPI()
    self_improve_router.register(app, {"todos": []})
    client = TestClient(app)
    payload = {"baseline_ref": _BASELINE, "reference_ref": _REFERENCE}

    unavailable = client.post(
        "/admin/self-improve/approvals/TODO-SI-BINDING/prepare",
        json=payload,
    )
    assert unavailable.status_code == 503

    app.state._session_factory = _factory()
    prepare = AsyncMock(
        return_value=(
            SimpleNamespace(status=TodoStatus.QUEUED.value),
            SimpleNamespace(approved_plan_digest="f" * 64),
        )
    )
    monkeypatch.setattr(
        self_improve_router,
        "_prepare_managed_approval",
        prepare,
    )
    lost_hold = client.post(
        "/admin/self-improve/approvals/TODO-SI-BINDING/prepare",
        json=payload,
    )
    assert lost_hold.status_code == 409
    assert lost_hold.json()["detail"].endswith("left the human approval gate")
