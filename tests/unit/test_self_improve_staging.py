"""Managed self-improvement staging and human-release contracts."""

from __future__ import annotations

import hmac
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self, cast
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from general_ludd.projects.repository_binding import ProjectRepositoryBinding
from general_ludd.schemas.todo import Todo, TodoStatus
from general_ludd.self_improve.approval import (
    ApprovalError,
    SelfImproveApprovalManager,
)
from general_ludd.self_improve.codex_comparison import CodexReference
from general_ludd.self_improve.managed_runner import ApprovedSelfImprovePlan, TaskSpec
from general_ludd.self_improve.staging import (
    MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
    ManagedSelfImproveArtifactKind,
    ManagedSelfImprovePlanRequest,
    build_managed_plan_request_payload,
    classify_self_improve_artifact,
    validate_bound_managed_plan,
)

_BASELINE = "a" * 40
_REFERENCE = "b" * 40
_PSK = "managed-staging-test-psk"


def _source_todo() -> dict[str, object]:
    return {
        "title": "Add tests for runtime.py",
        "description": "runtime.py has no independent branch coverage",
        "work_type": "test",
        "source": "self_improve_harness",
        "gap_type": "missing_tests",
        "source_file": "src/general_ludd/runtime.py",
        "task_type": "test_write",
        "blocker_kind": "coverage",
        "incident_count": 3,
        "recent_todo_ids": ["TODO-A", "TODO-B"],
        "test_commands": ["make test-files TESTFILES=tests/unit/test_runtime.py"],
    }


def _request(project_id: str = "proj-managed") -> ManagedSelfImprovePlanRequest:
    payload = build_managed_plan_request_payload(_source_todo(), project_id=project_id)
    return ManagedSelfImprovePlanRequest.from_json(str(payload["plan_artifact"]))


def _approved_plan(
    repo_root: Path,
    *,
    todo_id: str = "TODO-SI-1",
    project_id: str = "proj-managed",
    repository_binding_digest: str = "",
) -> ApprovedSelfImprovePlan:
    request = _request(project_id)
    return ApprovedSelfImprovePlan.approve(
        approval_id=todo_id,
        todo_id=todo_id,
        project_id=project_id,
        repo_root=repo_root,
        repository_binding_digest=repository_binding_digest,
        task=request.task,
        reference=CodexReference(
            baseline_sha=_BASELINE,
            reference_sha=_REFERENCE,
            changed_files=frozenset({"src/general_ludd/runtime.py"}),
            test_files=frozenset({"tests/unit/test_runtime.py"}),
            changed_lines=12,
            elapsed_seconds=1.0,
        ),
        prompt="Produce the exact bounded proposal.",
        required_output_tokens=256,
        max_attempts=2,
    )


def test_bound_plan_validation_rebinds_same_identity_to_host_local_repository(
    tmp_path: Path,
) -> None:
    binding = ProjectRepositoryBinding.for_project(
        project_id="proj-managed",
        workspace_path="org/proj-managed",
        repo_url="https://example.com/org/proj-managed.git",
    )
    controller_root = tmp_path / "controller"
    worker_root = tmp_path / "worker"
    controller_root.mkdir()
    worker_root.mkdir()
    plan = _approved_plan(
        controller_root,
        repository_binding_digest=binding.digest,
    )

    rebound = validate_bound_managed_plan(
        plan.to_json(),
        todo_id=plan.todo_id,
        project_id=plan.project_id,
        repo_root=worker_root,
        repository_binding_digest=binding.digest,
    )

    assert rebound.repo_root == worker_root.resolve()
    assert rebound.identity_digest == plan.identity_digest
    with pytest.raises(ValueError, match="binding"):
        validate_bound_managed_plan(
            plan.to_json(),
            todo_id=plan.todo_id,
            project_id=plan.project_id,
            repo_root=worker_root,
            repository_binding_digest="0" * 64,
        )


def test_plan_request_payload_preserves_exact_gap_source_and_task_identity() -> None:
    payload = build_managed_plan_request_payload(
        _source_todo(), project_id="proj-managed"
    )

    assert set(payload) == {"approval_policy", "plan_artifact"}
    assert payload["approval_policy"] == MANAGED_SELF_IMPROVE_APPROVAL_POLICY
    request = ManagedSelfImprovePlanRequest.from_json(str(payload["plan_artifact"]))
    assert request.project_id == "proj-managed"
    assert request.source == "self_improve_harness"
    assert request.gap_type == "missing_tests"
    assert request.source_file == "src/general_ludd/runtime.py"
    assert request.task_type == "test_write"
    assert request.blocker_kind == "coverage"
    assert request.incident_count == 3
    assert request.recent_todo_ids == ("TODO-A", "TODO-B")
    assert request.task.objective == "runtime.py has no independent branch coverage"
    assert request.task.canonical_make_commands == (
        "make test-files TESTFILES=tests/unit/test_runtime.py",
    )
    assert request.task.task_id.startswith("S")
    assert request.to_json() == payload["plan_artifact"]


def test_plan_request_is_canonical_bounded_and_duplicate_field_safe() -> None:
    raw = _request().to_json()
    with pytest.raises(ValueError, match="canonical"):
        ManagedSelfImprovePlanRequest.from_json(" " + raw)
    with pytest.raises(ValueError, match="duplicate"):
        ManagedSelfImprovePlanRequest.from_json(
            raw.replace('{"artifact_type":', '{"artifact_type":"duplicate","artifact_type":', 1)
        )
    with pytest.raises(ValueError, match="bounded"):
        ManagedSelfImprovePlanRequest.from_json("x" * 262_145)


def _request_value() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_request().to_json()))


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda value: value.pop("source"), "fields"),
        (lambda value: value.update(artifact_type="wrong"), "artifact type"),
        (lambda value: value.update(schema_version=2), "schema version"),
        (lambda value: value.update(recent_todo_ids="TODO-A"), "recent todo ids"),
        (
            lambda value: value["task"].update(canonical_make_commands="make gate"),
            "task commands",
        ),
        (
            lambda value: value["task"].update(reference_elapsed_seconds=True),
            "elapsed time",
        ),
    ],
)
def test_plan_request_parser_rejects_every_schema_ambiguity(
    mutator: object,
    message: str,
) -> None:
    value = _request_value()
    mutator(value)  # type: ignore[operator]
    with pytest.raises(ValueError, match=message):
        ManagedSelfImprovePlanRequest.from_json(_canonical_json(value))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"project_id": "p" * 33}, "project_id"),
        ({"source": "s" * 129}, "source"),
        ({"source_file": "/absolute.py"}, "source_file"),
        ({"source_file": "../escape.py"}, "source_file"),
        ({"source_file": "src\\escape.py"}, "source_file"),
        ({"source_file": "src//module.py"}, "canonical"),
        ({"title": "t" * 513}, "title"),
        ({"incident_count": True}, "incident_count"),
        ({"incident_count": -1}, "incident_count"),
        ({"recent_todo_ids": tuple(f"TODO-{i}" for i in range(33))}, "bounded"),
        ({"recent_todo_ids": ("T" * 33,)}, "recent_todo_id"),
        ({"recent_todo_ids": cast(Any, ["TODO-A"])}, "immutable tuple"),
        ({"task": cast(Any, object())}, "immutable TaskSpec"),
    ],
)
def test_plan_request_constructor_rejects_unbounded_or_ambiguous_identity(
    changes: Any,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(_request(), **changes)


@pytest.mark.parametrize(
    ("todo", "message"),
    [
        (cast(Any, []), "todo mapping"),
        ({**_source_todo(), "title": 7}, "title"),
        ({**_source_todo(), "description": " d "}, "description"),
        ({**_source_todo(), "recent_todo_ids": "TODO-A"}, "recent_todo_ids"),
        ({**_source_todo(), "test_commands": "make gate"}, "test_commands"),
        ({**_source_todo(), "test_commands": ["pytest"]}, "make command"),
    ],
)
def test_plan_request_payload_rejects_untyped_or_unsafe_source_values(
    todo: Any,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        build_managed_plan_request_payload(todo, project_id="proj-managed")


def test_plan_request_defaults_are_explicit_and_stable() -> None:
    payload = build_managed_plan_request_payload(
        {"title": "Repair the bounded gap"}, project_id="proj-managed"
    )
    request = ManagedSelfImprovePlanRequest.from_json(str(payload["plan_artifact"]))
    assert request.source == "self_improve_harness"
    assert request.gap_type == "unspecified"
    assert request.source_file == ""
    assert request.work_type == "code"
    assert request.task.objective == "Repair the bounded gap"
    assert request.task.canonical_make_commands == ("make gate",)


def test_artifact_classifier_explicitly_separates_managed_and_legacy_types(
    tmp_path: Path,
) -> None:
    assert classify_self_improve_artifact(
        _request().to_json(), MANAGED_SELF_IMPROVE_APPROVAL_POLICY
    ) is ManagedSelfImproveArtifactKind.MANAGED_PLAN_REQUEST
    assert classify_self_improve_artifact(
        _approved_plan(tmp_path).to_json(), MANAGED_SELF_IMPROVE_APPROVAL_POLICY
    ) is ManagedSelfImproveArtifactKind.MANAGED_APPROVED_PLAN
    assert classify_self_improve_artifact(
        '{"capability_required":"config_write","change_content":"x",'
        '"kind":"config","reason":"approved","target_paths":["a.yml"]}',
        "none",
    ) is ManagedSelfImproveArtifactKind.LEGACY_CONFIG
    assert classify_self_improve_artifact(
        '{"description":"d","kind":"code","project_id":"p",'
        '"schema_version":1,"title":"t","worktree_path":"/tmp/w"}',
        "none",
    ) is ManagedSelfImproveArtifactKind.LEGACY_NON_CONFIG


@pytest.mark.parametrize(
    "raw",
    [None, "{malformed", "[]", '{"kind":"unknown"}'],
)
def test_artifact_classifier_keeps_unknown_legacy_data_non_executable(raw: object) -> None:
    assert classify_self_improve_artifact(
        raw, "none"
    ) is ManagedSelfImproveArtifactKind.LEGACY_UNKNOWN


def test_artifact_classifier_rejects_noncanonical_managed_plan(tmp_path: Path) -> None:
    raw = _approved_plan(tmp_path).to_json()
    assert classify_self_improve_artifact(
        " " + raw, MANAGED_SELF_IMPROVE_APPROVAL_POLICY
    ) is ManagedSelfImproveArtifactKind.MALFORMED_MANAGED


@pytest.mark.parametrize("drift", ["task", "baseline", "reference", "noncanonical"])
def test_bound_plan_validator_rejects_staging_identity_drift(
    tmp_path: Path,
    drift: str,
) -> None:
    from general_ludd.self_improve.staging import validate_bound_managed_plan

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    plan = _approved_plan(repo_root)
    raw = plan.to_json()
    expected_task = plan.task
    baseline = _BASELINE
    reference = _REFERENCE
    if drift == "task":
        expected_task = TaskSpec(
            task_id="S99",
            objective="Different task",
            canonical_make_commands=("make gate",),
        )
    elif drift == "baseline":
        baseline = "c" * 40
    elif drift == "reference":
        reference = "d" * 40
    else:
        raw = " " + raw

    with pytest.raises(ValueError, match="managed self-improve"):
        validate_bound_managed_plan(
            raw,
            todo_id="TODO-SI-1",
            project_id="proj-managed",
            repo_root=repo_root,
            expected_task=expected_task,
            baseline_ref=baseline,
            reference_ref=reference,
        )


def test_bound_plan_validator_rejects_missing_artifact_and_repository(
    tmp_path: Path,
) -> None:
    from general_ludd.self_improve.staging import validate_bound_managed_plan

    with pytest.raises(ValueError, match="missing"):
        validate_bound_managed_plan(
            None,
            todo_id="TODO-SI-1",
            project_id="proj-managed",
            repo_root=tmp_path,
        )
    missing_root = tmp_path / "missing"
    plan = _approved_plan(missing_root)
    with pytest.raises(ValueError, match="repository"):
        validate_bound_managed_plan(
            plan.to_json(),
            todo_id="TODO-SI-1",
            project_id="proj-managed",
            repo_root=missing_root,
        )


@pytest.mark.asyncio
async def test_managed_request_cannot_queue_before_exact_plan_preparation() -> None:
    row = SimpleNamespace(
        todo_id="TODO-SI-1",
        project_id="proj-managed",
        status=TodoStatus.APPROVAL_REQUIRED.value,
        work_type="self_improve",
        approval_policy=MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
        plan_artifact=_request().to_json(),
        version=1,
    )
    store = AsyncMock()
    store.get_by_id.return_value = row
    manager = SelfImproveApprovalManager(managed_repo_resolver=lambda _project_id: Path("/tmp"))

    with pytest.raises(ApprovalError, match="must be prepared"):
        await manager.approve_by_id(store, row.todo_id)

    store.transition.assert_not_awaited()


def test_in_process_approval_cannot_bypass_managed_plan_preparation() -> None:
    todo = Todo(
        todo_id="TODO-SI-1",
        title="Managed gap",
        project_id="proj-managed",
        status=TodoStatus.APPROVAL_REQUIRED,
        approval_policy=MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
        plan_artifact=_request().to_json(),
    )

    with pytest.raises(ApprovalError, match="must be prepared"):
        SelfImproveApprovalManager().approve(todo)

    assert todo.status is TodoStatus.APPROVAL_REQUIRED


@pytest.mark.asyncio
async def test_only_bound_canonical_managed_plan_can_queue(tmp_path: Path) -> None:
    repo_root = tmp_path / "project" / "repo"
    repo_root.mkdir(parents=True)
    binding = ProjectRepositoryBinding.for_project(
        project_id="proj-managed",
        workspace_path="org/proj-managed",
        repo_url="https://example.com/org/proj-managed.git",
    )
    row = SimpleNamespace(
        todo_id="TODO-SI-1",
        project_id="proj-managed",
        status=TodoStatus.APPROVAL_REQUIRED.value,
        work_type="self_improve",
        approval_policy=MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
        plan_artifact=_approved_plan(
            repo_root,
            repository_binding_digest=binding.digest,
        ).to_json(),
        version=4,
    )
    digested_values = vars(row).copy()
    digested_values.update(version=5)
    digested = SimpleNamespace(**digested_values)
    released_values = vars(digested).copy()
    released_values.update(status=TodoStatus.QUEUED.value, version=6)
    released = SimpleNamespace(**released_values)
    store = AsyncMock()
    store.get_by_id.return_value = row
    store.update.return_value = digested
    store.transition.return_value = released
    manager = SelfImproveApprovalManager(
        managed_repo_resolver=lambda project_id: (
            repo_root if project_id == "proj-managed" else tmp_path / "wrong"
        ),
        managed_binding_resolver=lambda project_id: (
            binding.digest if project_id == "proj-managed" else "0" * 64
        ),
    )

    assert await manager.approve_by_id(store, row.todo_id) is released
    store.transition.assert_awaited_once_with(
        row.todo_id,
        TodoStatus.QUEUED,
        expected_version=5,
        project_id=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["todo", "project", "repo", "malformed", "missing"])
async def test_managed_approval_fails_closed_on_every_identity_drift(
    tmp_path: Path, drift: str
) -> None:
    repo_root = tmp_path / "project" / "repo"
    repo_root.mkdir(parents=True)
    plan_root = tmp_path / "other" / "repo" if drift == "repo" else repo_root
    plan_root.mkdir(parents=True, exist_ok=True)
    plan = _approved_plan(
        plan_root,
        todo_id="TODO-OTHER" if drift == "todo" else "TODO-SI-1",
        project_id="proj-other" if drift == "project" else "proj-managed",
    )
    artifact: str | None = plan.to_json()
    if drift == "malformed":
        artifact = "{not-json"
    elif drift == "missing":
        artifact = None
    row = SimpleNamespace(
        todo_id="TODO-SI-1",
        project_id="proj-managed",
        status=TodoStatus.APPROVAL_REQUIRED.value,
        work_type="self_improve",
        approval_policy=MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
        plan_artifact=artifact,
        version=1,
    )
    store = AsyncMock()
    store.get_by_id.return_value = row
    manager = SelfImproveApprovalManager(
        managed_repo_resolver=lambda _project_id: repo_root
    )

    with pytest.raises(ApprovalError, match="managed self-improve"):
        await manager.approve_by_id(store, row.todo_id)

    store.transition.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_config_and_non_config_approvals_remain_releasable() -> None:
    for artifact in (
        json.dumps(
            {
                "capability_required": "config_write",
                "change_content": "enabled: true\n",
                "kind": "config",
                "reason": "legacy config",
                "target_paths": ["config/test.yml"],
            }
        ),
        json.dumps(
            {
                "description": "legacy code",
                "kind": "code",
                "project_id": "legacy-project",
                "schema_version": 1,
                "title": "legacy code",
                "worktree_path": "/tmp/gludd-legacy-worktree",
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    ):
        row = SimpleNamespace(
            todo_id="legacy",
            project_id=None,
            status=TodoStatus.APPROVAL_REQUIRED.value,
            work_type="self_improve",
            approval_policy="none",
            plan_artifact=artifact,
            version=1,
        )
        store = AsyncMock()
        store.get_by_id.return_value = row
        store.update.return_value = SimpleNamespace(**{**vars(row), "version": 2})
        store.transition.return_value = row
        manager = SelfImproveApprovalManager()

        assert await manager.approve_by_id(store, row.todo_id) is row
        store.transition.assert_awaited_once_with(
            row.todo_id,
            TodoStatus.APPROVED,
            expected_version=2,
            project_id=None,
        )


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


def _authenticated_app(register: object, *, setup: object) -> FastAPI:
    app = FastAPI()
    setup(app)  # type: ignore[operator]
    register(app, {})  # type: ignore[operator]

    @app.middleware("http")
    async def _auth(request: object, call_next: object) -> object:
        authorization = request.headers.get("Authorization", "")  # type: ignore[attr-defined]
        supplied = authorization.removeprefix("Bearer ").strip()
        if not supplied or not hmac.compare_digest(supplied, _PSK):
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
        return await call_next(request)  # type: ignore[operator]

    return app


def test_authenticated_prepare_seam_stores_exact_plan_and_keeps_human_gate(
    tmp_path: Path,
) -> None:
    import general_ludd.routers.self_improve as router

    project_root = tmp_path / "project"
    repo_root = project_root / "repo"
    repo_root.mkdir(parents=True)
    request = _request()
    row = SimpleNamespace(
        todo_id="TODO-SI-1",
        project_id="proj-managed",
        status=TodoStatus.APPROVAL_REQUIRED.value,
        work_type="self_improve",
        approval_policy=MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
        plan_artifact=request.to_json(),
        version=7,
    )
    stored_updates: list[dict[str, object]] = []
    repository = AsyncMock()
    repository.get_by_id.return_value = row

    async def update(
        _todo_id: str,
        updates: dict[str, object],
        expected_version: int,
        project_id: str | None = None,
    ) -> object:
        assert expected_version == 7
        assert project_id == "proj-managed"
        stored_updates.append(updates)
        row.plan_artifact = updates["plan_artifact"]
        row.version += 1
        return row

    repository.update.side_effect = update
    binding = ProjectRepositoryBinding.for_project(
        project_id="proj-managed",
        workspace_path="org/proj-managed",
        repo_url="https://example.com/org/proj-managed.git",
    )
    plan = _approved_plan(repo_root, repository_binding_digest=binding.digest)

    def setup(app: FastAPI) -> None:
        app.state._session_factory = _Factory()
        app.state._project_manager = SimpleNamespace(
            get_project=lambda _project_id: SimpleNamespace(
                workspace_path=str(project_root)
            )
        )

    app = _authenticated_app(router.register, setup=setup)
    with (
        patch.object(router, "TodoRepository", return_value=repository),
        patch.object(router, "prepare_managed_self_improve_plan", return_value=plan) as prepare,
        patch.object(router, "_resolve_non_config_project_repo", return_value=repo_root),
        patch.object(
            router,
            "_resolve_project_repository_binding",
            return_value=binding,
        ),
    ):
        unauthenticated = TestClient(app).post(
            "/admin/self-improve/approvals/TODO-SI-1/prepare",
            json={"baseline_ref": _BASELINE, "reference_ref": _REFERENCE},
        )
        response = TestClient(app).post(
            "/admin/self-improve/approvals/TODO-SI-1/prepare",
            json={"baseline_ref": _BASELINE, "reference_ref": _REFERENCE},
            headers={"Authorization": f"Bearer {_PSK}"},
        )

    assert unauthenticated.status_code == 401
    assert response.status_code == 200
    assert response.json()["status"] == TodoStatus.APPROVAL_REQUIRED.value
    assert row.status == TodoStatus.APPROVAL_REQUIRED.value
    assert stored_updates == [{"plan_artifact": plan.to_json()}]
    hydrated = ApprovedSelfImprovePlan.from_json(str(row.plan_artifact))
    assert hydrated.repo_root is None
    assert hydrated.identity_digest == plan.identity_digest
    assert hydrated.repository_binding_digest == binding.digest
    prepare.assert_called_once_with(
        repo_root.resolve(),
        approval_id="TODO-SI-1",
        todo_id="TODO-SI-1",
        project_id="proj-managed",
        repository_binding_digest=binding.digest,
        baseline_ref=_BASELINE,
        reference_ref=_REFERENCE,
        task=request.task,
        max_attempts=3,
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"baseline_ref": "main", "reference_ref": _REFERENCE},
        {"baseline_ref": _BASELINE, "reference_ref": "development"},
        {"baseline_ref": _BASELINE, "reference_ref": _REFERENCE, "extra": True},
        {"baseline_ref": _BASELINE, "reference_ref": _REFERENCE, "max_attempts": 0},
    ],
)
def test_prepare_seam_rejects_mutable_or_ambiguous_inputs(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    import general_ludd.routers.self_improve as router

    project_root = tmp_path / "project"
    (project_root / "repo").mkdir(parents=True)
    row = SimpleNamespace(
        todo_id="TODO-SI-1",
        project_id="proj-managed",
        status=TodoStatus.APPROVAL_REQUIRED.value,
        work_type="self_improve",
        approval_policy=MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
        plan_artifact=_request().to_json(),
        version=1,
    )
    repository = AsyncMock()
    repository.get_by_id.return_value = row

    app = FastAPI()
    app.state._session_factory = _Factory()
    app.state._project_manager = SimpleNamespace(
        get_project=lambda _project_id: SimpleNamespace(workspace_path=str(project_root))
    )
    router.register(app, {})
    with patch.object(router, "TodoRepository", return_value=repository):
        response = TestClient(app).post(
            "/admin/self-improve/approvals/TODO-SI-1/prepare", json=payload
        )

    assert response.status_code == 422
    repository.update.assert_not_awaited()
