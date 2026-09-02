"""Daemon dispatch contracts for approval-bound managed self-improvement."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.event_loop.loop import EventLoop
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
) -> ApprovedSelfImprovePlan:
    return ApprovedSelfImprovePlan.approve(
        approval_id="approval-managed-daemon",
        todo_id=todo_id,
        project_id=project_id,
        repo_root=repo_root,
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
