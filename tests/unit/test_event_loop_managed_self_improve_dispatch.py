"""Daemon dispatch contracts for approval-bound managed self-improvement."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.event_loop.loop import EventLoop
from general_ludd.projects.repository_binding import ProjectRepositoryBinding
from general_ludd.self_improve.codex_comparison import (
    CandidateEvidence,
    CodexReference,
    ComparisonResult,
    ProposalManifest,
)
from general_ludd.self_improve.managed_runner import (
    ApprovedSelfImprovePlan,
    AttemptResult,
    ManagedRunResult,
    TaskSpec,
)
from general_ludd.self_improve.result_artifact import (
    ManagedSelfImproveResultArtifact,
)


def _approved_plan(
    repo_root: Path,
    *,
    todo_id: str = "TODO-SELF-IMPROVE",
    project_id: str = "project-managed",
    repository_binding_digest: str = "",
) -> ApprovedSelfImprovePlan:
    return ApprovedSelfImprovePlan.approve(
        approval_id="approval-managed-daemon",
        todo_id=todo_id,
        project_id=project_id,
        repo_root=repo_root,
        repository_binding_digest=repository_binding_digest,
        task=TaskSpec(
            task_id="S83.203",
            objective="Exercise the daemon managed self-improvement boundary.",
            canonical_make_commands=(
                "make test-files TESTFILES=tests/unit/test_example.py",
            ),
        ),
        reference=CodexReference(
            baseline_sha="a" * 40,
            reference_sha="b" * 40,
            changed_files=frozenset({"src/general_ludd/example.py"}),
            test_files=frozenset({"tests/unit/test_example.py"}),
            changed_lines=1,
            elapsed_seconds=0.1,
        ),
        prompt="bounded daemon prompt",
        required_output_tokens=512,
        max_attempts=1,
    )


def _todo(plan_artifact: str | None, *, project_id: str = "project-managed") -> SimpleNamespace:
    return SimpleNamespace(
        todo_id="TODO-SELF-IMPROVE",
        title="Managed self-improvement",
        description="Run only the frozen approved plan.",
        status="active",
        priority=10,
        queue="core",
        tags=[],
        work_type="self_improve",
        resource_profile="local_heavy",
        project_id=project_id,
        plan_artifact=plan_artifact,
        approved_artifact_digest=(
            hashlib.sha256(plan_artifact.encode("utf-8")).hexdigest()
            if plan_artifact
            else None
        ),
        prompt_profile=None,
        model_profile=None,
        acceptance_criteria=None,
        definition_of_done="",
        version=1,
    )


def _managed_result(
    plan: ApprovedSelfImprovePlan,
    *,
    accepted: bool = True,
) -> ManagedRunResult:
    proposal = ProposalManifest.from_json(
        json.dumps(
            {
                "schema_version": 1,
                "baseline_sha": plan.reference.baseline_sha,
                "task_id": plan.task.task_id,
                "edits": [
                    {
                        "operation": "replace",
                        "path": "src/general_ludd/example.py",
                        "old_text": "return 0",
                        "new_text": "return 1",
                    }
                ],
                "tests": ["tests/unit/test_example.py"],
                "make_commands": [
                    "make test-files TESTFILES=tests/unit/test_example.py"
                ],
                "commit_message": "feat: improve example",
            }
        )
    )
    evidence = CandidateEvidence(
        changed_files=frozenset({"src/general_ludd/example.py"}),
        tests_passed=accepted,
        warnings=0,
        coverage_aggregate=90.0,
        coverage_min_file=80.0,
        ruff_passed=True,
        mypy_passed=True,
        docstrings_passed=True,
        markdown_passed=True,
        cleanup_passed=True,
        commit_count=1,
        worktree_clean=True,
        elapsed_seconds=0.1,
        changed_lines=2,
    )
    comparison = ComparisonResult(
        accepted=accepted,
        score=100.0 if accepted else 50.0,
        blockers=() if accepted else ("tests",),
        changed_file_precision=1.0,
        changed_file_recall=1.0,
    )
    return ManagedRunResult(
        final_result=AttemptResult(
            comparison=comparison,
            evidence=evidence,
            patch_equivalence="e" * 64,
            proposal=proposal,
            diagnostics="" if accepted else "tests failed",
            attempt_identity_digest=plan.attempt_identity_digest,
        ),
        attempts=1,
        plan_identity_digest=plan.identity_digest,
        attempted_model_ids=("model-one",),
        outcome_record_ids=("17",),
    )


def _make_loop(
    repo_root: Path,
    managed_runner: MagicMock,
) -> tuple[EventLoop, dict[str, Any]]:
    session = AsyncMock()
    task_return_repo = AsyncMock()
    generic_runner = MagicMock()
    generic_runner.run_playbook.side_effect = AssertionError(
        "managed self-improvement must not use the generic playbook runner",
    )
    http_client = AsyncMock()
    http_client.post.side_effect = AssertionError(
        "managed self-improvement must not use the generic worker endpoint",
    )
    factory = MagicMock(return_value=managed_runner)
    loop = EventLoop(
        config={"repo_root": str(repo_root)},
        session=session,
        task_return_repo=task_return_repo,
        runner=generic_runner,
        http_client=http_client,
        project_workspace={
            "project-managed": SimpleNamespace(repo_dir=repo_root),
        },
        self_improve_runner_factory=factory,
    )
    return loop, {
        "factory": factory,
        "generic_runner": generic_runner,
        "http_client": http_client,
        "session": session,
        "task_return_repo": task_return_repo,
    }


def _make_bound_worker_loop(
    tmp_path: Path,
    response: object,
) -> tuple[ApprovedSelfImprovePlan, EventLoop, dict[str, Any]]:
    binding = ProjectRepositoryBinding.for_project(
        project_id="project-managed",
        workspace_path="tenant/project-managed",
        repo_url="https://example.com/org/managed.git",
    )
    controller_repo = tmp_path / "controller-host" / "repo"
    controller_repo.mkdir(parents=True)
    plan = _approved_plan(
        controller_repo,
        repository_binding_digest=binding.digest,
    )
    task_return_repo = AsyncMock()
    session = AsyncMock()
    managed_factory = MagicMock()
    http_client = AsyncMock()
    if isinstance(response, Exception):
        http_client.post.side_effect = response
    else:
        http_client.post.return_value = response
    project_manager = MagicMock()
    project_manager.get_project.return_value = SimpleNamespace(
        project_id=binding.project_id,
        workspace_path=binding.workspace_key,
        repo_url="https://example.com/org/managed.git",
    )
    loop = EventLoop(
        config={"self_improve": {"execution_mode": "worker"}},
        session=session,
        task_return_repo=task_return_repo,
        http_client=http_client,
        project_manager=project_manager,
        self_improve_runner_factory=managed_factory,
    )
    return plan, loop, {
        "http_client": http_client,
        "managed_factory": managed_factory,
        "session": session,
        "task_return_repo": task_return_repo,
    }


@pytest.mark.parametrize(
    "manager",
    [
        None,
        SimpleNamespace(),
        SimpleNamespace(get_project=lambda _project_id: None),
        SimpleNamespace(
            get_project=lambda _project_id: SimpleNamespace(active=False)
        ),
        SimpleNamespace(
            get_project=lambda _project_id: SimpleNamespace(
                active=True,
                workspace_path="../escape",
                repo_url="https://example.com/org/escape.git",
            )
        ),
    ],
)
def test_managed_binding_resolver_rejects_untrusted_manager_state(
    manager: object,
) -> None:
    loop = EventLoop(config={}, project_manager=manager)
    assert loop._resolve_managed_self_improve_binding("project-managed") is None


def test_managed_repository_resolver_rejects_every_non_directory_shape(
    tmp_path: Path,
) -> None:
    loop = EventLoop(config={})
    loop._project_workspace = None
    assert loop._resolve_managed_self_improve_repo("project-managed") is None

    loop._project_workspace = {}
    assert loop._resolve_managed_self_improve_repo("project-managed") is None

    loop._project_workspace = {"project-managed": SimpleNamespace()}
    assert loop._resolve_managed_self_improve_repo("project-managed") is None

    missing = tmp_path / "missing"
    loop._project_workspace = {
        "project-managed": SimpleNamespace(repo_dir=missing)
    }
    assert loop._resolve_managed_self_improve_repo("project-managed") is None

    regular_file = tmp_path / "regular-file"
    regular_file.write_text("not a repository")
    loop._project_workspace = {
        "project-managed": SimpleNamespace(repo_dir=regular_file)
    }
    assert loop._resolve_managed_self_improve_repo("project-managed") is None


@pytest.mark.asyncio
async def test_valid_plan_runs_managed_service_and_persists_reviewable_return(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    plan = _approved_plan(repo_root)
    managed_runner = MagicMock()
    managed_runner.run.return_value = _managed_result(plan)
    loop, collaborators = _make_loop(repo_root, managed_runner)

    await loop._dispatch_execute_job(_todo(plan.to_json()))

    collaborators["factory"].assert_called_once_with(repo_root.resolve())
    hydrated = managed_runner.run.call_args.args[0]
    assert isinstance(hydrated, ApprovedSelfImprovePlan)
    assert hydrated.identity_digest == plan.identity_digest
    collaborators["generic_runner"].run_playbook.assert_not_called()
    collaborators["http_client"].post.assert_not_awaited()
    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    assert persisted["todo_id"] == plan.todo_id
    assert persisted["job_id"] == f"EXEC-{plan.todo_id}"
    assert persisted["exit_code"] == 0
    artifact = ManagedSelfImproveResultArtifact.from_json(
        persisted["result_summary"]
    )
    assert artifact.accepted is True
    assert artifact.plan_identity_digest == plan.identity_digest
    assert artifact.attempt_identity_digest == plan.attempt_identity_digest
    assert artifact.proposal.edits[0].new_text == "return 1"
    assert artifact.evidence.tests_passed is True
    assert artifact.comparison.score == 100.0
    collaborators["session"].flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_bound_local_plan_rebinds_to_current_trusted_repository(
    tmp_path: Path,
) -> None:
    binding = ProjectRepositoryBinding.for_project(
        project_id="project-managed",
        workspace_path="tenant/project-managed",
        repo_url="https://example.com/org/managed.git",
    )
    controller_repo = tmp_path / "controller" / "repo"
    controller_repo.mkdir(parents=True)
    local_repo = tmp_path / "local" / "repo"
    local_repo.mkdir(parents=True)
    plan = _approved_plan(
        controller_repo,
        repository_binding_digest=binding.digest,
    )
    managed_runner = MagicMock()
    managed_runner.run.return_value = _managed_result(plan)
    loop, collaborators = _make_loop(local_repo, managed_runner)
    loop._project_manager = SimpleNamespace(
        get_project=lambda _project_id: SimpleNamespace(
            active=True,
            workspace_path=binding.workspace_key,
            repo_url="https://example.com/org/managed.git",
        )
    )

    await loop._dispatch_execute_job(_todo(plan.to_json()))

    collaborators["factory"].assert_called_once_with(local_repo.resolve())
    rebound = managed_runner.run.call_args.args[0]
    assert rebound.repo_root == local_repo.resolve()
    assert rebound.identity_digest == plan.identity_digest
    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    assert persisted["exit_code"] == 0


@pytest.mark.asyncio
async def test_bound_local_plan_rebind_failure_is_typed_and_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = ProjectRepositoryBinding.for_project(
        project_id="project-managed",
        workspace_path="tenant/project-managed",
        repo_url="https://example.com/org/managed.git",
    )
    controller_repo = tmp_path / "controller" / "repo"
    controller_repo.mkdir(parents=True)
    local_repo = tmp_path / "local" / "repo"
    local_repo.mkdir(parents=True)
    plan = _approved_plan(
        controller_repo,
        repository_binding_digest=binding.digest,
    )
    managed_runner = MagicMock()
    loop, collaborators = _make_loop(local_repo, managed_runner)
    loop._project_manager = SimpleNamespace(
        get_project=lambda _project_id: SimpleNamespace(
            active=True,
            workspace_path=binding.workspace_key,
            repo_url="https://example.com/org/managed.git",
        )
    )

    def reject_rebind(*_args: object, **_kwargs: object) -> None:
        raise ValueError("private path detail")

    monkeypatch.setattr(
        ApprovedSelfImprovePlan,
        "bind_execution_repository",
        reject_rebind,
    )

    await loop._dispatch_execute_job(_todo(plan.to_json()))

    collaborators["factory"].assert_not_called()
    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    assert json.loads(persisted["result_summary"])["reason"] == (
        "repository_binding_stale"
    )
    assert "private path detail" not in persisted["result_summary"]


@pytest.mark.asyncio
async def test_worker_mode_dispatches_path_independent_repository_binding(
    tmp_path: Path,
) -> None:
    binding = ProjectRepositoryBinding.for_project(
        project_id="project-managed",
        workspace_path="tenant/project-managed",
        repo_url="https://example.com/org/managed.git",
    )
    controller_repo = tmp_path / "controller-host" / "repo"
    controller_repo.mkdir(parents=True)
    plan = _approved_plan(
        controller_repo,
        repository_binding_digest=binding.digest,
    )
    task_return_repo = AsyncMock()
    session = AsyncMock()
    managed_factory = MagicMock()
    http_client = AsyncMock()
    result_artifact = ManagedSelfImproveResultArtifact.from_run_result(
        _managed_result(plan)
    )
    http_client.post.return_value = {
        "return_id": f"RET-EXEC-{plan.todo_id}",
        "exit_code": 0,
        "result_summary": result_artifact.to_json(),
    }
    project_manager = MagicMock()
    project_manager.get_project.return_value = SimpleNamespace(
        project_id=binding.project_id,
        workspace_path=binding.workspace_key,
        repo_url="https://example.com/org/managed.git",
    )
    loop = EventLoop(
        config={"self_improve": {"execution_mode": "worker"}},
        session=session,
        task_return_repo=task_return_repo,
        http_client=http_client,
        project_manager=project_manager,
        self_improve_runner_factory=managed_factory,
    )

    await loop._dispatch_execute_job(_todo(plan.to_json()))

    managed_factory.assert_not_called()
    http_client.post.assert_awaited_once()
    url = http_client.post.await_args.args[0]
    payload = http_client.post.await_args.kwargs["json"]
    assert url.endswith("/jobs/execute")
    assert payload["repository_binding_digest"] == binding.digest
    assert payload["plan_artifact"] == plan.to_json()
    assert str(controller_repo.resolve()) not in json.dumps(payload)
    persisted = task_return_repo.create.await_args.kwargs["data"]
    assert persisted["exit_code"] == 0
    assert persisted["result_summary"] == result_artifact.to_json()


@pytest.mark.asyncio
async def test_worker_mode_without_http_client_fails_closed(tmp_path: Path) -> None:
    plan, loop, collaborators = _make_bound_worker_loop(
        tmp_path,
        {"result_summary": "unused"},
    )
    loop._http_client = None

    await loop._dispatch_execute_job(_todo(plan.to_json()))

    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    assert json.loads(persisted["result_summary"])["reason"] == (
        "worker_unavailable"
    )


@pytest.mark.asyncio
async def test_worker_non_integer_status_and_non_mapping_detail_are_rejected(
    tmp_path: Path,
) -> None:
    response = SimpleNamespace(
        status_code="200",
        json=lambda: {"detail": "untrusted detail"},
    )
    plan, loop, collaborators = _make_bound_worker_loop(tmp_path, response)

    await loop._dispatch_execute_job(_todo(plan.to_json()))

    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    assert json.loads(persisted["result_summary"])["reason"] == "worker_rejected"


@pytest.mark.asyncio
async def test_worker_status_below_success_range_is_rejected(tmp_path: Path) -> None:
    response = SimpleNamespace(
        status_code=199,
        json=lambda: {"detail": {"reason": "unrecognized_worker_reason"}},
    )
    plan, loop, collaborators = _make_bound_worker_loop(tmp_path, response)

    await loop._dispatch_execute_job(_todo(plan.to_json()))

    collaborators["managed_factory"].assert_not_called()
    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    assert json.loads(persisted["result_summary"])["reason"] == "worker_rejected"


@pytest.mark.asyncio
async def test_worker_result_for_another_plan_is_rejected(tmp_path: Path) -> None:
    other_repo = tmp_path / "other"
    other_repo.mkdir()
    other_plan = _approved_plan(other_repo, todo_id="TODO-OTHER")
    response = {
        "result_summary": ManagedSelfImproveResultArtifact.from_run_result(
            _managed_result(other_plan)
        ).to_json()
    }
    plan, loop, collaborators = _make_bound_worker_loop(tmp_path, response)

    await loop._dispatch_execute_job(_todo(plan.to_json()))

    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    assert json.loads(persisted["result_summary"])["reason"] == (
        "managed_result_invalid"
    )


@pytest.mark.asyncio
async def test_changed_project_mapping_rejects_stale_bound_plan_before_dispatch(
    tmp_path: Path,
) -> None:
    approved_binding = ProjectRepositoryBinding.for_project(
        project_id="project-managed",
        workspace_path="tenant/project-managed",
        repo_url="https://example.com/org/managed.git",
    )
    plan = _approved_plan(
        tmp_path,
        repository_binding_digest=approved_binding.digest,
    )
    managed_factory = MagicMock()
    http_client = AsyncMock()
    task_return_repo = AsyncMock()
    project_manager = MagicMock()
    project_manager.get_project.return_value = SimpleNamespace(
        project_id=approved_binding.project_id,
        workspace_path="tenant/project-managed",
        repo_url="https://example.com/org/replaced.git",
    )
    loop = EventLoop(
        config={"self_improve": {"execution_mode": "worker"}},
        session=AsyncMock(),
        task_return_repo=task_return_repo,
        http_client=http_client,
        project_manager=project_manager,
        self_improve_runner_factory=managed_factory,
    )

    await loop._dispatch_execute_job(_todo(plan.to_json()))

    managed_factory.assert_not_called()
    http_client.post.assert_not_awaited()
    persisted = task_return_repo.create.await_args.kwargs["data"]
    assert json.loads(persisted["result_summary"])["reason"] == (
        "repository_binding_stale"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("remote_reason", "expected_reason"),
    [
        (
            "self_improve_repository_binding_stale",
            "repository_binding_stale",
        ),
        (
            "self_improve_repository_unavailable",
            "repository_unavailable",
        ),
        ("unrecognized_worker_reason", "worker_rejected"),
        (None, "worker_rejected"),
    ],
)
async def test_worker_rejection_is_mapped_to_bounded_local_reason(
    tmp_path: Path,
    remote_reason: str | None,
    expected_reason: str,
) -> None:
    response = SimpleNamespace(
        status_code=409,
        json=lambda: {"detail": {"reason": remote_reason}},
    )
    plan, loop, collaborators = _make_bound_worker_loop(tmp_path, response)

    await loop._dispatch_execute_job(_todo(plan.to_json()))

    collaborators["managed_factory"].assert_not_called()
    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    assert json.loads(persisted["result_summary"])["reason"] == expected_reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        RuntimeError("private transport detail"),
        SimpleNamespace(status_code=200),
        SimpleNamespace(status_code=200, json=lambda: []),
    ],
)
async def test_worker_transport_or_response_shape_failure_is_secret_safe(
    tmp_path: Path,
    response: object,
) -> None:
    plan, loop, collaborators = _make_bound_worker_loop(tmp_path, response)

    await loop._dispatch_execute_job(_todo(plan.to_json()))

    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    summary = json.loads(persisted["result_summary"])
    assert summary["reason"] == "worker_dispatch_failed"
    assert "private transport detail" not in persisted["result_summary"]


@pytest.mark.asyncio
async def test_worker_response_decoder_failure_is_secret_safe(tmp_path: Path) -> None:
    def reject_response_body() -> None:
        raise RuntimeError("private response body detail")

    response = SimpleNamespace(status_code=200, json=reject_response_body)
    plan, loop, collaborators = _make_bound_worker_loop(tmp_path, response)

    await loop._dispatch_execute_job(_todo(plan.to_json()))

    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    assert json.loads(persisted["result_summary"])["reason"] == (
        "worker_dispatch_failed"
    )
    assert "private response body detail" not in persisted["result_summary"]


@pytest.mark.asyncio
async def test_worker_async_response_body_is_accepted(tmp_path: Path) -> None:
    seed_plan = _approved_plan(tmp_path)
    artifact = ManagedSelfImproveResultArtifact.from_run_result(
        _managed_result(seed_plan)
    )

    async def response_json() -> dict[str, object]:
        return {"result_summary": artifact.to_json()}

    response = SimpleNamespace(status_code=200, json=response_json)
    plan, loop, collaborators = _make_bound_worker_loop(tmp_path, response)
    rebound_artifact = ManagedSelfImproveResultArtifact.from_run_result(
        _managed_result(plan)
    )
    response.json = AsyncMock(
        return_value={"result_summary": rebound_artifact.to_json()}
    )

    await loop._dispatch_execute_job(_todo(plan.to_json()))

    response.json.assert_awaited_once()
    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    assert persisted["result_summary"] == rebound_artifact.to_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("result_summary", [None, "not-json"])
async def test_worker_invalid_result_artifact_fails_closed(
    tmp_path: Path,
    result_summary: object,
) -> None:
    plan, loop, collaborators = _make_bound_worker_loop(
        tmp_path,
        {"result_summary": result_summary},
    )

    await loop._dispatch_execute_job(_todo(plan.to_json()))

    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    assert json.loads(persisted["result_summary"])["reason"] == (
        "managed_result_invalid"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_reason"),
    [
        ("untrusted", "invalid_execution_mode"),
        ("worker", "repository_binding_required"),
    ],
)
async def test_invalid_or_legacy_worker_mode_fails_closed_before_dispatch(
    tmp_path: Path,
    mode: str,
    expected_reason: str,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    plan = _approved_plan(repo_root)
    managed_runner = MagicMock()
    loop, collaborators = _make_loop(repo_root, managed_runner)
    loop.config = {"self_improve": {"execution_mode": mode}}

    await loop._dispatch_execute_job(_todo(plan.to_json()))

    collaborators["factory"].assert_not_called()
    collaborators["http_client"].post.assert_not_awaited()
    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    assert json.loads(persisted["result_summary"])["reason"] == expected_reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("artifact", "reason"),
    [
        (None, "missing_plan_artifact"),
        ("not-json", "invalid_plan_artifact"),
    ],
)
async def test_missing_or_malformed_plan_fails_closed_without_generic_fallback(
    tmp_path: Path,
    artifact: str | None,
    reason: str,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    managed_runner = MagicMock()
    loop, collaborators = _make_loop(repo_root, managed_runner)

    await loop._dispatch_execute_job(_todo(artifact))

    collaborators["factory"].assert_not_called()
    managed_runner.run.assert_not_called()
    collaborators["generic_runner"].run_playbook.assert_not_called()
    collaborators["http_client"].post.assert_not_awaited()
    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    assert persisted["exit_code"] == 1
    assert json.loads(persisted["result_summary"]) == {
        "accepted": False,
        "kind": "managed_self_improve",
        "reason": reason,
    }


@pytest.mark.asyncio
async def test_post_approval_plan_artifact_change_fails_closed(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    plan = _approved_plan(repo_root)
    todo = _todo(plan.to_json())
    todo.approved_artifact_digest = "0" * 64
    managed_runner = MagicMock()
    loop, collaborators = _make_loop(repo_root, managed_runner)

    await loop._dispatch_execute_job(todo)

    collaborators["factory"].assert_not_called()
    managed_runner.run.assert_not_called()
    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    assert json.loads(persisted["result_summary"])["reason"] == (
        "approval_artifact_digest_mismatch"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("approved_digest", [None, 7])
async def test_missing_or_non_text_approval_digest_fails_closed(
    tmp_path: Path,
    approved_digest: object,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    plan = _approved_plan(repo_root)
    todo = _todo(plan.to_json())
    todo.approved_artifact_digest = approved_digest
    managed_runner = MagicMock()
    loop, collaborators = _make_loop(repo_root, managed_runner)

    await loop._dispatch_execute_job(todo)

    managed_runner.run.assert_not_called()
    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    assert json.loads(persisted["result_summary"])["reason"] == (
        "approval_artifact_digest_mismatch"
    )


@pytest.mark.asyncio
async def test_oversized_approved_artifact_fails_closed_before_parsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    artifact = "x" * 1_048_577
    todo = _todo(artifact)
    managed_runner = MagicMock()
    loop, _collaborators = _make_loop(repo_root, managed_runner)
    persist = AsyncMock()
    monkeypatch.setattr(loop, "_persist_managed_self_improve_return", persist)

    await loop._dispatch_execute_job(todo)

    managed_runner.run.assert_not_called()
    persist.assert_awaited_once()
    awaited = persist.await_args
    assert awaited is not None
    assert awaited.kwargs["reason"] == (
        "approval_artifact_digest_mismatch"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("plan_todo_id", "plan_project_id", "todo_project_id", "configured_repo", "reason"),
    [
        (
            "TODO-OTHER",
            "project-managed",
            "project-managed",
            "approved",
            "todo_identity_mismatch",
        ),
        (
            "TODO-SELF-IMPROVE",
            "project-other",
            "project-managed",
            "approved",
            "project_identity_mismatch",
        ),
        (
            "TODO-SELF-IMPROVE",
            "project-managed",
            "project-managed",
            "other",
            "repository_identity_mismatch",
        ),
    ],
)
async def test_identity_mismatch_fails_closed_before_service_construction(
    tmp_path: Path,
    plan_todo_id: str,
    plan_project_id: str,
    todo_project_id: str,
    configured_repo: str,
    reason: str,
) -> None:
    approved_root = tmp_path / "approved"
    approved_root.mkdir()
    configured_root = approved_root
    if configured_repo == "other":
        configured_root = tmp_path / "other"
        configured_root.mkdir()
    plan = _approved_plan(
        approved_root,
        todo_id=plan_todo_id,
        project_id=plan_project_id,
    )
    managed_runner = MagicMock()
    loop, collaborators = _make_loop(configured_root, managed_runner)

    await loop._dispatch_execute_job(
        _todo(plan.to_json(), project_id=todo_project_id),
    )

    collaborators["factory"].assert_not_called()
    managed_runner.run.assert_not_called()
    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    assert persisted["exit_code"] == 1
    assert json.loads(persisted["result_summary"])["reason"] == reason


@pytest.mark.asyncio
async def test_managed_execution_is_serialized_per_event_loop(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    plan_one = _approved_plan(repo_root)
    plan_two = _approved_plan(repo_root, todo_id="TODO-SELF-IMPROVE-2")
    managed_runner = MagicMock()
    managed_runner.run.return_value = _managed_result(plan_one)
    loop, _collaborators = _make_loop(repo_root, managed_runner)
    active = 0
    peak_active = 0

    async def observable_offload(func: Any, *args: object, **kwargs: object) -> object:
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        try:
            await asyncio.sleep(0)
            return func(*args, **kwargs)
        finally:
            active -= 1

    loop._bounded_to_thread = observable_offload  # type: ignore[assignment]
    todo_two = _todo(plan_two.to_json())
    todo_two.todo_id = plan_two.todo_id

    await asyncio.gather(
        loop._dispatch_execute_job(_todo(plan_one.to_json())),
        loop._dispatch_execute_job(todo_two),
    )

    assert managed_runner.run.call_count == 2
    assert peak_active == 1


@pytest.mark.asyncio
async def test_managed_runtime_failure_becomes_failed_reviewable_return(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    plan = _approved_plan(repo_root)
    managed_runner = MagicMock()
    managed_runner.run.side_effect = RuntimeError("private model detail")
    loop, collaborators = _make_loop(repo_root, managed_runner)

    await loop._dispatch_execute_job(_todo(plan.to_json()))

    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    assert persisted["exit_code"] == 1
    assert json.loads(persisted["result_summary"]) == {
        "accepted": False,
        "kind": "managed_self_improve",
        "reason": "managed_execution_failed",
    }
    assert "private model detail" not in persisted["result_summary"]
    collaborators["generic_runner"].run_playbook.assert_not_called()
    collaborators["http_client"].post.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejected_managed_result_is_persisted_as_unsuccessful(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    plan = _approved_plan(repo_root)
    managed_runner = MagicMock()
    managed_runner.run.return_value = _managed_result(plan, accepted=False)
    loop, collaborators = _make_loop(repo_root, managed_runner)

    await loop._dispatch_execute_job(_todo(plan.to_json()))

    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    assert persisted["exit_code"] == 1
    assert json.loads(persisted["result_summary"])["accepted"] is False


@pytest.mark.asyncio
async def test_unresolvable_repository_fails_closed_before_factory(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    plan = _approved_plan(repo_root)
    managed_runner = MagicMock()
    loop, collaborators = _make_loop(repo_root, managed_runner)
    loop._project_workspace = {}

    await loop._dispatch_execute_job(_todo(plan.to_json()))

    collaborators["factory"].assert_not_called()
    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    assert persisted["exit_code"] == 1
    assert json.loads(persisted["result_summary"])["reason"] == (
        "repository_unavailable"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_result", [SimpleNamespace(), "not-a-result"])
async def test_unencodable_managed_result_fails_closed(
    tmp_path: Path,
    invalid_result: object,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    plan = _approved_plan(repo_root)
    managed_runner = MagicMock()
    managed_runner.run.return_value = invalid_result
    loop, collaborators = _make_loop(repo_root, managed_runner)

    await loop._dispatch_execute_job(_todo(plan.to_json()))

    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    assert persisted["exit_code"] == 1
    assert json.loads(persisted["result_summary"]) == {
        "accepted": False,
        "kind": "managed_self_improve",
        "reason": "managed_result_invalid",
    }
    collaborators["generic_runner"].run_playbook.assert_not_called()
    collaborators["http_client"].post.assert_not_awaited()


@pytest.mark.asyncio
async def test_managed_result_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    plan = _approved_plan(repo_root)
    managed_runner = MagicMock()
    managed_runner.run.return_value = replace(
        _managed_result(plan),
        plan_identity_digest="f" * 64,
    )
    loop, collaborators = _make_loop(repo_root, managed_runner)

    await loop._dispatch_execute_job(_todo(plan.to_json()))

    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    assert persisted["exit_code"] == 1
    assert json.loads(persisted["result_summary"])["reason"] == (
        "managed_result_invalid"
    )


@pytest.mark.asyncio
async def test_local_attempt_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    plan = _approved_plan(repo_root)
    result = _managed_result(plan)
    managed_runner = MagicMock()
    managed_runner.run.return_value = replace(
        result,
        final_result=replace(
            result.final_result,
            attempt_identity_digest="f" * 64,
        ),
    )
    loop, collaborators = _make_loop(repo_root, managed_runner)

    await loop._dispatch_execute_job(_todo(plan.to_json()))

    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    assert persisted["exit_code"] == 1
    assert json.loads(persisted["result_summary"])["reason"] == (
        "managed_result_invalid"
    )


@pytest.mark.asyncio
async def test_worker_attempt_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    plan, loop, collaborators = _make_bound_worker_loop(tmp_path, {})
    result = _managed_result(plan)
    mismatched = replace(
        result,
        final_result=replace(
            result.final_result,
            attempt_identity_digest="f" * 64,
        ),
    )
    collaborators["http_client"].post.return_value = {
        "result_summary": ManagedSelfImproveResultArtifact.from_run_result(
            mismatched
        ).to_json()
    }

    await loop._dispatch_execute_job(_todo(plan.to_json()))

    persisted = collaborators["task_return_repo"].create.await_args.kwargs["data"]
    assert persisted["exit_code"] == 1
    assert json.loads(persisted["result_summary"])["reason"] == (
        "managed_result_invalid"
    )
