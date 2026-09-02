"""Todo completion gate for managed self-improvement promotion receipts."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from general_ludd.review.decision_applier import apply_decision
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.todo import TodoStatus
from general_ludd.self_improve.promotion import ManagedPromotionReceipt
from general_ludd.self_improve.staging import MANAGED_SELF_IMPROVE_APPROVAL_POLICY


@contextmanager
def _verified_completion() -> Any:
    async def inline(fn: Any, *args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    with (
        patch.object(asyncio, "to_thread", inline),
        patch(
            "general_ludd.review.completion_verifier.verify_completion",
            new=lambda decision, _return, _root: decision,
        ),
    ):
        yield


def _decision() -> TaskDecision:
    return TaskDecision(
        return_id="RETURN-PROMOTION",
        matched_todo_id="TODO-PROMOTION",
        decision="complete",
        confidence=1.0,
    )


def _receipt(repo_root: Path) -> ManagedPromotionReceipt:
    return ManagedPromotionReceipt(
        artifact_digest="a" * 64,
        plan_identity_digest="b" * 64,
        attempt_identity_digest="c" * 64,
        todo_id="TODO-PROMOTION",
        project_id="project-promotion",
        repo_root=repo_root,
        return_id="RETURN-PROMOTION",
        development_commit="d" * 40,
        marker="Gludd-Self-Improve-Artifact=" + "a" * 64,
        fencing_token=1,
        marker_verified=True,
    )


async def test_managed_complete_without_receipt_is_rejected(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    todo = SimpleNamespace(
        todo_id="TODO-PROMOTION",
        project_id="project-promotion",
        work_type="self_improve",
        approval_policy=MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
        version=2,
    )
    todo_repo = AsyncMock()
    todo_repo.get_by_id.return_value = todo

    with _verified_completion(), pytest.raises(ValueError, match="promotion receipt"):
        await apply_decision(
            _decision(),
            todo_repo,
            AsyncMock(),
            repo_root=str(repo_root),
        )

    todo_repo.transition.assert_not_awaited()


async def test_managed_complete_accepts_exact_verified_receipt(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    todo = SimpleNamespace(
        todo_id="TODO-PROMOTION",
        project_id="project-promotion",
        work_type="self_improve",
        approval_policy=MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
        version=2,
    )
    todo_repo = AsyncMock()
    todo_repo.get_by_id.return_value = todo

    with _verified_completion():
        await apply_decision(
            _decision(),
            todo_repo,
            AsyncMock(),
            repo_root=str(repo_root),
            managed_promotion_receipt=_receipt(repo_root),
        )

    todo_repo.transition.assert_awaited_once_with(
        "TODO-PROMOTION",
        TodoStatus.COMPLETE,
        2,
        project_id="project-promotion",
    )


async def test_receipt_for_another_return_is_rejected(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    todo_repo = AsyncMock()
    todo_repo.get_by_id.return_value = SimpleNamespace(
        todo_id="TODO-PROMOTION",
        project_id="project-promotion",
        work_type="self_improve",
        approval_policy=MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
        version=2,
    )
    receipt = _receipt(repo_root)
    receipt = ManagedPromotionReceipt(
        artifact_digest=receipt.artifact_digest,
        plan_identity_digest=receipt.plan_identity_digest,
        attempt_identity_digest=receipt.attempt_identity_digest,
        todo_id=receipt.todo_id,
        project_id=receipt.project_id,
        repo_root=receipt.repo_root,
        return_id="RETURN-OTHER",
        development_commit=receipt.development_commit,
        marker=receipt.marker,
        fencing_token=receipt.fencing_token,
        marker_verified=True,
    )

    with _verified_completion(), pytest.raises(ValueError, match="return identity"):
        await apply_decision(
            _decision(),
            todo_repo,
            AsyncMock(),
            repo_root=str(repo_root),
            managed_promotion_receipt=receipt,
        )


async def test_managed_complete_rejects_todo_without_project_identity(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    todo_repo = AsyncMock()
    todo_repo.get_by_id.return_value = SimpleNamespace(
        todo_id="TODO-PROMOTION",
        project_id=None,
        work_type="self_improve",
        approval_policy=MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
        version=2,
    )

    with _verified_completion(), pytest.raises(ValueError, match="project identity"):
        await apply_decision(
            _decision(),
            todo_repo,
            AsyncMock(),
            repo_root=str(repo_root),
            managed_promotion_receipt=_receipt(repo_root),
        )

    todo_repo.transition.assert_not_awaited()


async def test_verifier_downgrade_does_not_require_or_accept_promotion_receipt(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    todo_repo = AsyncMock()
    todo_repo.get_by_id.return_value = SimpleNamespace(
        todo_id="TODO-PROMOTION",
        project_id="project-promotion",
        work_type="self_improve",
        approval_policy=MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
        version=2,
    )

    async def inline(fn: Any, *args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    def downgrade(
        decision: TaskDecision,
        _task_return: object,
        _root: object,
    ) -> TaskDecision:
        return decision.model_copy(update={"decision": "needs_more_work"})

    with (
        patch.object(asyncio, "to_thread", inline),
        patch(
            "general_ludd.review.completion_verifier.verify_completion",
            new=downgrade,
        ),
    ):
        await apply_decision(
            _decision(),
            todo_repo,
            AsyncMock(),
            repo_root=str(repo_root),
        )

    todo_repo.transition.assert_awaited_once_with(
        "TODO-PROMOTION",
        TodoStatus.NEEDS_MORE_WORK,
        2,
        project_id="project-promotion",
    )


async def test_legacy_self_improve_complete_does_not_require_managed_receipt(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    todo_repo = AsyncMock()
    todo_repo.get_by_id.return_value = SimpleNamespace(
        todo_id="TODO-LEGACY",
        project_id="project-promotion",
        work_type="self_improve",
        approval_policy="none",
        version=2,
    )
    decision = _decision().model_copy(update={"matched_todo_id": "TODO-LEGACY"})

    with _verified_completion():
        await apply_decision(
            decision,
            todo_repo,
            AsyncMock(),
            repo_root=str(repo_root),
        )

    todo_repo.transition.assert_awaited_once_with(
        "TODO-LEGACY",
        TodoStatus.COMPLETE,
        2,
        project_id="project-promotion",
    )


@pytest.mark.parametrize(
    ("checks", "expected_summary"),
    [
        (
            [
                {"passed": False, "summary": "lint failed"},
                {"passed": False, "name": "types"},
                {"passed": True, "summary": "ignored"},
                "not-a-check",
            ],
            "lint failed; types: FAIL",
        ),
        ([{"passed": True, "summary": "ignored"}], "project gate FAILED"),
    ],
)
async def test_failing_project_gate_downgrades_before_managed_receipt_gate(
    tmp_path: Path,
    checks: list[object],
    expected_summary: str,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "project.yml").write_text("gate: true\n", encoding="utf-8")
    todo_repo = AsyncMock()
    todo_repo.get_by_id.return_value = SimpleNamespace(
        todo_id="TODO-PROMOTION",
        project_id="project-promotion",
        work_type="self_improve",
        approval_policy=MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
        version=2,
    )

    with (
        _verified_completion(),
        patch(
            "general_ludd.quality.project_gate.run_project_gate",
            return_value={"passed": False, "checks": checks},
        ),
    ):
        await apply_decision(
            _decision(),
            todo_repo,
            AsyncMock(),
            repo_root=str(repo_root),
        )

    todo_repo.transition.assert_awaited_once_with(
        "TODO-PROMOTION",
        TodoStatus.NEEDS_MORE_WORK,
        2,
        project_id="project-promotion",
    )
    created_payloads = [call.args[0] for call in todo_repo.create.await_args_list]
    assert len(created_payloads) == 2
    assert expected_summary in created_payloads[0]["description"]
    assert created_payloads[1]["work_type"] == "review"


async def test_project_gate_error_fails_safe_then_requires_managed_receipt(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "project.yml").write_text("gate: true\n", encoding="utf-8")
    todo_repo = AsyncMock()
    todo_repo.get_by_id.return_value = SimpleNamespace(
        todo_id="TODO-PROMOTION",
        project_id="project-promotion",
        work_type="self_improve",
        approval_policy=MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
        version=2,
    )

    with (
        _verified_completion(),
        patch(
            "general_ludd.quality.project_gate.run_project_gate",
            side_effect=RuntimeError("gate unavailable"),
        ),
        pytest.raises(ValueError, match="promotion receipt"),
    ):
        await apply_decision(
            _decision(),
            todo_repo,
            AsyncMock(),
            repo_root=str(repo_root),
        )

    todo_repo.transition.assert_not_awaited()


async def test_passing_project_gate_preserves_managed_receipt_requirement(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "project.yml").write_text("gate: true\n", encoding="utf-8")
    todo_repo = AsyncMock()
    todo_repo.get_by_id.return_value = SimpleNamespace(
        todo_id="TODO-PROMOTION",
        project_id="project-promotion",
        work_type="self_improve",
        approval_policy=MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
        version=2,
    )

    with (
        _verified_completion(),
        patch(
            "general_ludd.quality.project_gate.run_project_gate",
            return_value={"passed": True, "checks": []},
        ),
    ):
        await apply_decision(
            _decision(),
            todo_repo,
            AsyncMock(),
            repo_root=str(repo_root),
            managed_promotion_receipt=_receipt(repo_root),
        )

    todo_repo.transition.assert_awaited_once_with(
        "TODO-PROMOTION",
        TodoStatus.COMPLETE,
        2,
        project_id="project-promotion",
    )
