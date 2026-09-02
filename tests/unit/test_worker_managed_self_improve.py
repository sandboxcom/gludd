"""Worker endpoint contracts for approval-bound managed self-improvement."""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

import general_ludd.projects.workspace as workspace_module
import general_ludd.self_improve as self_improve_package
import general_ludd.worker.app as worker_app
from general_ludd.models.gateway import ModelProfile
from general_ludd.self_improve.codex_comparison import CodexReference
from general_ludd.self_improve.managed_runner import ApprovedSelfImprovePlan, TaskSpec


@dataclass(frozen=True)
class _ManagedResult:
    accepted: bool
    attempts: int
    plan_identity_digest: str
    attempt_identity_digest: str
    attempted_model_ids: tuple[str, ...] = ("qwen-test",)
    outcome_record_ids: tuple[str, ...] = ("outcome-1",)


class _Runner:
    def __init__(self, result: _ManagedResult, *, delay: float = 0.0) -> None:
        self.result = result
        self.delay = delay
        self.plans: list[ApprovedSelfImprovePlan] = []
        self._counter_lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def run(self, plan: ApprovedSelfImprovePlan) -> _ManagedResult:
        with self._counter_lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.plans.append(plan)
            if self.delay:
                time.sleep(self.delay)
            return self.result
        finally:
            with self._counter_lock:
                self.active -= 1


class _Factory:
    def __init__(self, runner: Any) -> None:
        self.runner = runner
        self.roots: list[Path] = []

    def __call__(self, repo_root: Path) -> Any:
        self.roots.append(repo_root)
        return self.runner


class _FailingRunner:
    def run(self, _plan: ApprovedSelfImprovePlan) -> _ManagedResult:
        raise RuntimeError("secret-token")


def _plan(repo_root: Path, *, project_id: str = "project-worker") -> ApprovedSelfImprovePlan:
    return ApprovedSelfImprovePlan.approve(
        approval_id="approval-worker",
        todo_id="TODO-WORKER-SI",
        project_id=project_id,
        repo_root=repo_root,
        task=TaskSpec(
            task_id="S83.301",
            objective="Exercise the worker-managed runtime boundary.",
            canonical_make_commands=(
                "make test-files TESTFILES=tests/unit/test_worker_managed_self_improve.py",
            ),
        ),
        reference=CodexReference(
            baseline_sha="a" * 40,
            reference_sha="b" * 40,
            changed_files=frozenset({"src/general_ludd/worker/app.py"}),
            test_files=frozenset({"tests/unit/test_worker_managed_self_improve.py"}),
            changed_lines=1,
            elapsed_seconds=0.1,
        ),
        prompt="Return one bounded worker endpoint improvement.",
        required_output_tokens=512,
        max_attempts=1,
    )


def _payload(plan: ApprovedSelfImprovePlan, *, job_id: str = "JOB-WORKER-SI") -> dict[str, object]:
    return {
        "job_id": job_id,
        "todo_id": plan.todo_id,
        "project_id": plan.project_id,
        "playbook": "not-registered.yml",
        "queue": "self_update",
        "work_type": "self_improve",
        "plan_artifact": plan.to_json(),
    }


def _build_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    repo_root: Path,
    factory: Any,
    resolver: Any | None = None,
) -> Any:
    monkeypatch.setenv("GLUDD_PSK_DISABLE", "1")
    monkeypatch.setattr(
        worker_app,
        "get_playbook_registry",
        lambda: (_ for _ in ()).throw(AssertionError("generic playbook path was used")),
    )
    return worker_app.create_app(
        gateway=None,
        dispatcher=None,
        self_improve_runner_factory=factory,
        self_improve_repo_resolver=resolver or (lambda _project_id: repo_root),
    )


def test_default_repository_resolver_uses_canonical_project_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Workspace:
        def __init__(self, project_id: str) -> None:
            assert project_id == "project-worker"
            self.repo_dir = tmp_path

    monkeypatch.setattr(workspace_module, "ProjectWorkspace", _Workspace)

    assert worker_app.resolve_worker_self_improve_repo_root("project-worker") == (
        tmp_path.resolve()
    )
    with pytest.raises(ValueError, match="non-empty"):
        worker_app.resolve_worker_self_improve_repo_root("")

    invalid_project_id: Any = 1
    with pytest.raises(ValueError, match="non-empty"):
        worker_app.resolve_worker_self_improve_repo_root(invalid_project_id)


def test_default_repository_resolver_rejects_non_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_file = tmp_path / "repo-file"
    repo_file.write_text("not a repository", encoding="utf-8")

    class _Workspace:
        def __init__(self, project_id: str) -> None:
            assert project_id == "project-worker"
            self.repo_dir = repo_file

    monkeypatch.setattr(workspace_module, "ProjectWorkspace", _Workspace)

    with pytest.raises(ValueError, match="not a directory"):
        worker_app.resolve_worker_self_improve_repo_root("project-worker")


def test_default_runner_factory_delegates_to_installed_composition_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel: Any = _Runner(_ManagedResult(True, 1, "x", "y"))
    roots: list[Path] = []

    def build(repo_root: Path) -> Any:
        roots.append(repo_root)
        return sentinel

    monkeypatch.setattr(
        self_improve_package,
        "build_managed_self_improve_runner",
        build,
    )

    assert worker_app.build_worker_self_improve_runner(tmp_path) is sentinel
    assert roots == [tmp_path]


def test_gateway_adds_distinct_auto_profiles_and_scopes_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_ludd.config import loader as config_loader
    from general_ludd.models import auto_configurator, provider_registry
    from general_ludd.secrets import config as secrets_config
    from general_ludd.secrets import env as secrets_env
    from general_ludd.secrets import manager as secrets_manager

    explicit = ModelProfile(model_profile_id="explicit", model_name="model-a")
    duplicate = ModelProfile(model_profile_id="explicit", model_name="model-b")
    discovered = ModelProfile(model_profile_id="discovered", model_name="model-c")
    permission = object()
    scoped_secrets = MagicMock(name="scoped-secrets")
    registry = MagicMock(name="provider-registry")

    monkeypatch.setattr(
        config_loader,
        "load_user_config",
        lambda: SimpleNamespace(model_profiles={"explicit": explicit}),
    )
    monkeypatch.setattr(
        auto_configurator.AutoConfigurator,
        "auto_configure_profiles",
        lambda _self: [duplicate, discovered],
    )
    monkeypatch.setattr(
        provider_registry.ProviderRegistry,
        "from_profiles",
        staticmethod(lambda _profiles: registry),
    )
    monkeypatch.setattr(secrets_env, "EnvSecretsManager", MagicMock)
    monkeypatch.setattr(secrets_config, "OpenBaoConfig", MagicMock)
    monkeypatch.setattr(
        secrets_manager,
        "SecretsManager",
        lambda *, config, permission_spec: scoped_secrets,
    )

    gateway = worker_app.build_gateway_from_config(permission_spec=permission)

    assert gateway is not None
    assert set(gateway._profiles) == {"explicit", "discovered"}
    assert gateway._secrets is scoped_secrets


def test_gateway_scoped_secret_failure_falls_back_to_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_ludd.config import loader as config_loader
    from general_ludd.models import auto_configurator, provider_registry
    from general_ludd.secrets import env as secrets_env
    from general_ludd.secrets import manager as secrets_manager

    profile = ModelProfile(model_profile_id="explicit", model_name="model-a")
    environment_secrets = MagicMock(name="environment-secrets")

    monkeypatch.setattr(
        config_loader,
        "load_user_config",
        lambda: SimpleNamespace(model_profiles={"explicit": profile}),
    )
    monkeypatch.setattr(
        auto_configurator.AutoConfigurator,
        "auto_configure_profiles",
        lambda _self: [],
    )
    monkeypatch.setattr(
        provider_registry.ProviderRegistry,
        "from_profiles",
        staticmethod(lambda _profiles: MagicMock()),
    )
    monkeypatch.setattr(
        secrets_env,
        "EnvSecretsManager",
        lambda: environment_secrets,
    )
    monkeypatch.setattr(
        secrets_manager,
        "SecretsManager",
        MagicMock(side_effect=RuntimeError("unavailable")),
    )

    gateway = worker_app.build_gateway_from_config(permission_spec=object())

    assert gateway is not None
    assert gateway._secrets is environment_secrets


def test_compaction_config_enabled_uses_configured_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_ludd.compaction import aggressive
    from general_ludd.config import loader as config_loader

    level = object()
    monkeypatch.setattr(
        config_loader,
        "load_user_config",
        lambda: SimpleNamespace(compaction=SimpleNamespace(enabled=True, level=2)),
    )
    monkeypatch.setattr(aggressive, "level_at", lambda index: level if index == 2 else None)

    assert worker_app._resolve_compaction_config() == (True, level)


@pytest.mark.asyncio
async def test_self_improve_executes_approved_plan_without_playbook_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    managed = _ManagedResult(
        accepted=True,
        attempts=1,
        plan_identity_digest=plan.identity_digest,
        attempt_identity_digest=plan.attempt_identity_digest,
    )
    runner = _Runner(managed)
    factory = _Factory(runner)
    app = _build_app(monkeypatch, repo_root=tmp_path, factory=factory)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/jobs/execute", json=_payload(plan))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "created"
    assert body["exit_code"] == 0
    assert body["events"] == [{
        "event": "self_improve_completed",
        "accepted": True,
        "attempts": 1,
        "plan_identity_digest": plan.identity_digest,
        "attempt_identity_digest": plan.attempt_identity_digest,
        "attempted_model_ids": ["qwen-test"],
        "outcome_record_ids": ["outcome-1"],
    }]
    assert factory.roots == [tmp_path.resolve()]
    assert runner.plans == [ApprovedSelfImprovePlan.from_json(plan.to_json())]


@pytest.mark.asyncio
async def test_self_improve_rejection_is_a_typed_failed_task_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    runner = _Runner(
        _ManagedResult(
            accepted=False,
            attempts=1,
            plan_identity_digest=plan.identity_digest,
            attempt_identity_digest=plan.attempt_identity_digest,
        )
    )
    app = _build_app(monkeypatch, repo_root=tmp_path, factory=_Factory(runner))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/jobs/execute", json=_payload(plan))

    assert response.status_code == 200
    assert response.json()["exit_code"] == 1
    assert response.json()["events"][0]["accepted"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload_change", "reason"),
    [
        ({"plan_artifact": None}, "self_improve_plan_required"),
        ({"plan_artifact": "not-json"}, "invalid_self_improve_plan"),
        ({"project_id": None}, "self_improve_project_required"),
        ({"project_id": "wrong-project"}, "self_improve_identity_mismatch"),
        ({"todo_id": "wrong-todo"}, "self_improve_identity_mismatch"),
    ],
)
async def test_self_improve_fails_closed_for_missing_malformed_or_mismatched_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload_change: dict[str, object],
    reason: str,
) -> None:
    plan = _plan(tmp_path)
    factory = _Factory(_Runner(_ManagedResult(True, 1, "x", "y")))
    app = _build_app(monkeypatch, repo_root=tmp_path, factory=factory)
    payload = _payload(plan)
    payload.update(payload_change)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/jobs/execute", json=payload)

    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == reason
    assert factory.roots == []


@pytest.mark.asyncio
async def test_self_improve_fails_closed_when_repository_mapping_is_absent_or_mismatched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    factory = _Factory(_Runner(_ManagedResult(True, 1, "x", "y")))

    def missing(_project_id: str) -> Path:
        raise LookupError("not configured")

    missing_app = _build_app(
        monkeypatch,
        repo_root=tmp_path,
        factory=factory,
        resolver=missing,
    )
    other = tmp_path / "other"
    other.mkdir()
    repo_file = tmp_path / "repo-file"
    repo_file.write_text("not a repository", encoding="utf-8")
    mismatch_app = _build_app(
        monkeypatch,
        repo_root=tmp_path,
        factory=factory,
        resolver=lambda _project_id: other,
    )
    wrong_type_app = _build_app(
        monkeypatch,
        repo_root=tmp_path,
        factory=factory,
        resolver=lambda _project_id: "not-a-path",
    )
    file_app = _build_app(
        monkeypatch,
        repo_root=tmp_path,
        factory=factory,
        resolver=lambda _project_id: repo_file,
    )

    async with AsyncClient(transport=ASGITransport(app=missing_app), base_url="http://test") as client:
        missing_response = await client.post("/jobs/execute", json=_payload(plan))
    async with AsyncClient(transport=ASGITransport(app=mismatch_app), base_url="http://test") as client:
        mismatch_response = await client.post("/jobs/execute", json=_payload(plan))
    async with AsyncClient(transport=ASGITransport(app=wrong_type_app), base_url="http://test") as client:
        wrong_type_response = await client.post("/jobs/execute", json=_payload(plan))
    async with AsyncClient(transport=ASGITransport(app=file_app), base_url="http://test") as client:
        file_response = await client.post("/jobs/execute", json=_payload(plan))

    assert missing_response.status_code == 400
    assert missing_response.json()["detail"]["reason"] == "self_improve_repository_unavailable"
    assert mismatch_response.status_code == 400
    assert mismatch_response.json()["detail"]["reason"] == "self_improve_identity_mismatch"
    assert wrong_type_response.status_code == 400
    assert wrong_type_response.json()["detail"]["reason"] == "self_improve_repository_unavailable"
    assert file_response.status_code == 400
    assert file_response.json()["detail"]["reason"] == "self_improve_repository_unavailable"
    assert factory.roots == []


@pytest.mark.asyncio
async def test_self_improve_model_execution_is_serialized_per_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    runner = _Runner(
        _ManagedResult(
            accepted=True,
            attempts=1,
            plan_identity_digest=plan.identity_digest,
            attempt_identity_digest=plan.attempt_identity_digest,
        ),
        delay=0.05,
    )
    app = _build_app(monkeypatch, repo_root=tmp_path, factory=_Factory(runner))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first, second = await asyncio.gather(
            client.post("/jobs/execute", json=_payload(plan, job_id="JOB-WORKER-SI-1")),
            client.post("/jobs/execute", json=_payload(plan, job_id="JOB-WORKER-SI-2")),
        )

    assert first.status_code == second.status_code == 200
    assert runner.max_active == 1


@pytest.mark.asyncio
async def test_self_improve_runtime_exception_is_secret_safe_failed_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    app = _build_app(
        monkeypatch,
        repo_root=tmp_path,
        factory=_Factory(_FailingRunner()),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/jobs/execute", json=_payload(plan))

    body = response.json()
    assert response.status_code == 200
    assert body["exit_code"] == 1
    assert body["result_summary"] == "managed self-improvement failed"
    assert "secret-token" not in response.text
    assert body["events"] == [
        {"event": "self_improve_failed", "reason": "managed_execution_failed"}
    ]
