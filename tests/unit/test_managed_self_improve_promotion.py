"""Retry-safe promotion contracts for accepted managed self-improvement."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from general_ludd.db.promotion_repository import (
    CompletedManagedPromotion,
    ManagedPromotionIdentity,
    ManagedPromotionLease,
)
from general_ludd.self_improve.codex_comparison import (
    CandidateEvidence,
    CodexReference,
    ProposalManifest,
    compare_with_codex,
)
from general_ludd.self_improve.managed_runner import ApprovedSelfImprovePlan, TaskSpec
from general_ludd.self_improve.promotion import (
    ManagedPromotionReceipt,
    ManagedSelfImprovePromotionCoordinator,
    build_managed_self_improve_promotion_coordinator,
    validate_managed_promotion_inputs,
)
from general_ludd.self_improve.result_artifact import ManagedSelfImproveResultArtifact
from general_ludd.self_improve.runtime import MakeResult
from tests.self_improve_native_fixtures import controlled_revision_result


def _plan(
    repo_root: Path,
    *,
    repository_binding_digest: str = "",
) -> ApprovedSelfImprovePlan:
    return ApprovedSelfImprovePlan.approve(
        approval_id="approval-promotion",
        todo_id="TODO-PROMOTION",
        project_id="project-promotion",
        repo_root=repo_root,
        repository_binding_digest=repository_binding_digest,
        task=TaskSpec(
            task_id="S83.204",
            objective="Promote one accepted proposal.",
            canonical_make_commands=(
                "make test-files TESTFILES=tests/unit/test_example.py PYTEST_ARGS=-q",
            ),
        ),
        reference=CodexReference(
            baseline_sha="a" * 40,
            reference_sha="b" * 40,
            changed_files=frozenset({"src/general_ludd/example.py"}),
            test_files=frozenset({"tests/unit/test_example.py"}),
            changed_lines=2,
            elapsed_seconds=2.0,
        ),
        prompt="bounded promotion prompt",
        required_output_tokens=512,
        max_attempts=1,
    )


def _artifact(plan: ApprovedSelfImprovePlan) -> ManagedSelfImproveResultArtifact:
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
                    "make lint-files FILES=src/general_ludd/example.py"
                ],
                "commit_message": "feat: improve example",
            }
        )
    )
    evidence = CandidateEvidence(
        changed_files=frozenset({"src/general_ludd/example.py"}),
        tests_passed=True,
        warnings=0,
        coverage_aggregate=91.0,
        coverage_min_file=80.0,
        ruff_passed=True,
        mypy_passed=True,
        docstrings_passed=True,
        markdown_passed=True,
        cleanup_passed=True,
        commit_count=1,
        worktree_clean=True,
        elapsed_seconds=1.0,
        changed_lines=2,
    )
    comparison = compare_with_codex(proposal, evidence, plan.reference)
    assert comparison.accepted
    return ManagedSelfImproveResultArtifact(
        accepted=True,
        attempts=1,
        plan_identity_digest=plan.approved_plan_digest,
        attempt_identity_digest=plan.attempt_identity_digest,
        attempted_model_ids=("qwen2.5-coder-1.5b",),
        outcome_record_ids=("outcome-1",),
        proposal=proposal,
        evidence=evidence,
        comparison=comparison,
        patch_equivalence="patch-equivalent=0 unique=1",
        diagnostics="",
    )


def _result(returncode: int, stdout: str = "", stderr: str = "") -> MakeResult:
    return MakeResult(
        argv=("make", "fake"),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        elapsed_seconds=0.01,
    )


class _Repository:
    def __init__(self, acquisition: ManagedPromotionLease | CompletedManagedPromotion) -> None:
        self.acquisition = acquisition
        self.identities: list[ManagedPromotionIdentity] = []
        self.bound: list[tuple[ManagedPromotionLease, str]] = []
        self.completed: list[tuple[ManagedPromotionLease, str]] = []
        self.abandoned: list[ManagedPromotionLease] = []

    async def acquire(
        self,
        identity: ManagedPromotionIdentity,
        *,
        owner: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> ManagedPromotionLease | CompletedManagedPromotion:
        self.identities.append(identity)
        assert owner == "event-loop-promotion"
        assert now.tzinfo is not None
        assert lease_duration == timedelta(hours=2)
        return self.acquisition

    async def bind_worktree(
        self,
        claim: ManagedPromotionLease,
        branch: str,
        *,
        now: datetime,
    ) -> None:
        self.bound.append((claim, branch))

    async def complete(
        self,
        claim: ManagedPromotionLease,
        *,
        development_commit: str,
        marker: str,
        now: datetime,
    ) -> CompletedManagedPromotion:
        self.completed.append((claim, development_commit))
        identity = self.identities[-1]
        return CompletedManagedPromotion(
            identity=identity,
            development_commit=development_commit,
            marker=marker,
            fencing_token=claim.fencing_token,
            completed_at=now,
        )

    async def abandon(
        self,
        claim: ManagedPromotionLease,
        *,
        error: str,
        now: datetime,
    ) -> None:
        assert len(error.encode()) <= 4096
        self.abandoned.append(claim)


class _RootRunner:
    """Controlled native-shaped owners; actual native proof lives in the safety cohort."""

    def __init__(self, worktree: Path, *, marker_present: bool = False) -> None:
        self.worktree = worktree
        self.marker_present = marker_present
        self.branch = ""
        self.development_head = ("c" if marker_present else "a") * 40
        self.calls: list[tuple[str, dict[str, str]]] = []

    def run(
        self, target: str, variables: dict[str, str] | None = None, *,
        timeout: int = 120, read_only: bool = False,
    ) -> MakeResult:
        del timeout, read_only
        values = dict(variables or {})
        self.calls.append((target, values))
        if target == "self-improve-promotion-marker":
            if self.marker_present:
                return _result(0, "PROMOTION_COMMIT=" + self.development_head + "\n")
            return _result(2, "PROMOTION_ABSENT\n")
        if target == "worktree-state":
            return _result(0, json.dumps({
                "branch": "development", "head": self.development_head,
                "path": str(self.worktree.parent), "is_clean": True,
            }))
        if target == "_gate-fresh-check":
            return _result(0)
        if target == "development-fast-forward":
            assert set(values) == {"SOURCE", "EXPECTED_BEFORE_SHA", "EXPECTED_AFTER_SHA", "APPLY"}
            head, before = values["SOURCE"], self.development_head
            assert values["EXPECTED_BEFORE_SHA"] == before
            assert values["EXPECTED_AFTER_SHA"] == head
            if values["APPLY"] == "0":
                return _result(0, f"DEVELOPMENT-FAST-FORWARD-VALIDATED source={head} "
                               f"before={before} after={head} apply=0\n")
            assert values["APPLY"] == "1"
            self.development_head, self.marker_present = head, True
            return _result(0, f"DEVELOPMENT-FAST-FORWARDED source={head} before={before} after={head} mode=ff-only\n")
        if target == "agent-worktree-base":
            self.branch = values["BRANCH"]
            return _result(0, f"WORKTREE_PATH={self.worktree}\n")
        if target == "git-show-commit":
            return controlled_revision_result(values["C"], self.development_head)
        if target in {"agent-cleanup", "workstream-unregister"}:
            assert values["BRANCH"] == self.branch
            assert values["WORKTREE"] == str(self.worktree)
            expected = ("WORKSTREAM_EXPECTED_HEAD" if target == "workstream-unregister"
                        else "AGENT_CLEANUP_EXPECTED_HEAD")
            assert values[expected] == "c" * 40
            payload: dict[str, object] = {
                "branch": self.branch, "worktree": str(self.worktree), "head": "c" * 40,
                "native_ownership_verified": 1,
            }
            if target == "workstream-unregister":
                return _result(0, json.dumps({**payload, "status": "released"}))
            assert values["AGENT_CLEANUP_PURPOSE"] == "merged"
            plan = values["AGENT_CLEANUP_VALIDATE_ONLY"] == "1"
            if not plan:
                assert values["AGENT_CLEANUP_VALIDATE_ONLY"] == "0"
                self.worktree.rename(self.worktree.with_name(self.worktree.name + "-retired"))
            progress = "" if plan else "owned native archive progress\n"
            return _result(0, progress + json.dumps({
                **payload, "status": "planned" if plan else "removed", "validate_only": plan,
            }))
        raise AssertionError(f"unexpected root target {target}")


class _WorktreeRunner:
    """Controlled accepted candidate; never supplies actual gate/release evidence."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.branch = ""
        self.commands: list[str] = []
        self.targets: list[tuple[str, dict[str, str]]] = []

    def run_command(self, command: str, *, timeout: int = 900) -> MakeResult:
        self.commands.append(command)
        return _result(0, "TOTAL 10 0 100%\n")

    def run(
        self, target: str, variables: dict[str, str] | None = None, *,
        timeout: int = 120, read_only: bool = False,
    ) -> MakeResult:
        self.targets.append((target, dict(variables or {})))
        if target == "repo-status":
            return _result(0, "")
        if target == "git-show-commit":
            return controlled_revision_result((variables or {})["C"], "c" * 40)
        if target == "worktree-state":
            return _result(0, json.dumps({
                "branch": self.branch, "path": str(self.repo_root), "head": "c" * 40, "is_clean": True,
            }))
        return _result(0)


def _claim(
    artifact: ManagedSelfImproveResultArtifact, *, stale_worktree_branch: str | None = None,
) -> ManagedPromotionLease:
    return ManagedPromotionLease(
        artifact_digest=artifact.artifact_digest,
        owner="event-loop-promotion",
        fencing_token=3,
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        stale_worktree_branch=stale_worktree_branch,
    )


async def test_promotes_exact_proposal_once_to_hard_coded_development(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    repo_root.mkdir()
    (worktree / "src/general_ludd").mkdir(parents=True)
    (worktree / "src/general_ludd/example.py").write_text("return 0", encoding="utf-8")
    plan = _plan(repo_root)
    artifact = _artifact(plan)
    repository = _Repository(_claim(artifact))
    root_runner = _RootRunner(worktree)
    made: list[_WorktreeRunner] = []

    def runner_factory(path: Path) -> _WorktreeRunner:
        runner = _WorktreeRunner(path)
        runner.branch = root_runner.branch
        made.append(runner)
        return runner

    coordinator = ManagedSelfImprovePromotionCoordinator(
        repository,
        root_runner=root_runner,
        make_runner_factory=runner_factory,
        owner="event-loop-promotion",
        clock=lambda: datetime(2029, 12, 31, tzinfo=UTC),
    )

    receipt = await coordinator.promote(
        plan_artifact=plan.to_json(),
        result_artifact=artifact.to_json(),
        todo_id=plan.todo_id,
        project_id=plan.project_id,
        repo_root=repo_root,
        return_id="RETURN-PROMOTION",
    )

    assert receipt.development_commit == "c" * 40
    assert receipt.artifact_digest == artifact.artifact_digest
    assert [target for target, _ in root_runner.calls] == [
        "self-improve-promotion-marker", "agent-worktree-base", "worktree-state",
        "development-fast-forward", "development-fast-forward", "git-show-commit",
        "self-improve-promotion-marker", "agent-cleanup", "workstream-unregister",
        "agent-cleanup", "agent-cleanup", "git-show-commit",
    ]
    merge_values = [values for target, values in root_runner.calls if target == "development-fast-forward"]
    assert merge_values == [
        {"SOURCE": "c" * 40, "EXPECTED_BEFORE_SHA": "a" * 40, "EXPECTED_AFTER_SHA": "c" * 40, "APPLY": mode}
        for mode in ("0", "1")
    ]
    cleanup_values = [values for target, values in root_runner.calls if target == "agent-cleanup"]
    assert [values["AGENT_CLEANUP_VALIDATE_ONLY"] for values in cleanup_values] == ["1", "1", "0"]
    assert all(values["WORKTREE"] == str(worktree) for values in cleanup_values)
    assert all(values["AGENT_CLEANUP_EXPECTED_HEAD"] == "c" * 40 for values in cleanup_values)
    assert not worktree.exists()
    assert worktree.with_name("worktree-retired").is_dir()
    assert made[0].commands == [
        plan.task.canonical_make_commands[0],
        artifact.proposal.make_commands[0],
        "make test-count",
    ]
    assert [target for target, _ in made[0].targets] == [
        "git-add",
        "repo-commit",
        "repo-status",
        "git-show-commit",
    ]
    assert [target for target, _ in made[1].targets] == ["worktree-state", "_gate-fresh-check"]
    commit_message = made[0].targets[1][1]["MSG"]
    assert f"Gludd-Self-Improve-Artifact={artifact.artifact_digest}" in commit_message
    assert repository.completed


async def test_retry_after_merge_uses_verified_marker_without_second_merge(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    plan = _plan(repo_root)
    artifact = _artifact(plan)
    repository = _Repository(_claim(artifact))
    root_runner = _RootRunner(tmp_path / "unused", marker_present=True)
    coordinator = ManagedSelfImprovePromotionCoordinator(
        repository,
        root_runner=root_runner,
        make_runner_factory=lambda _path: pytest.fail("must not create a runner"),
        owner="event-loop-promotion",
        clock=lambda: datetime(2029, 12, 31, tzinfo=UTC),
    )

    receipt = await coordinator.promote(
        plan_artifact=plan.to_json(),
        result_artifact=artifact.to_json(),
        todo_id=plan.todo_id,
        project_id=plan.project_id,
        repo_root=repo_root,
        return_id="RETURN-PROMOTION",
    )

    assert receipt.development_commit == "c" * 40
    assert [target for target, _ in root_runner.calls] == [
        "self-improve-promotion-marker", "worktree-state", "development-fast-forward",
        "_gate-fresh-check", "git-show-commit",
    ]
    assert all(values["APPLY"] == "0" for target, values in root_runner.calls if target == "development-fast-forward")
    assert not any(target in {"agent-worktree-base", "agent-cleanup", "workstream-unregister"}
                   for target, _ in root_runner.calls)
    assert len(repository.completed) == 1


def test_validation_rejects_recomputed_comparison_drift(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    plan = _plan(repo_root)
    artifact = _artifact(plan)
    forged = replace(
        artifact,
        comparison=replace(artifact.comparison, score=99.0),
        artifact_digest="",
    )

    with pytest.raises(ValueError, match="recomputed comparison"):
        validate_managed_promotion_inputs(
            plan_artifact=plan.to_json(),
            result_artifact=forged.to_json(),
            todo_id=plan.todo_id,
            project_id=plan.project_id,
            repo_root=repo_root,
            return_id="RETURN-PROMOTION",
        )


def test_validation_rebinds_path_independent_plan_for_local_promotion(
    tmp_path: Path,
) -> None:
    controller_root = tmp_path / "controller"
    promotion_root = tmp_path / "promotion-host"
    controller_root.mkdir()
    promotion_root.mkdir()
    binding_digest = "d" * 64
    plan = _plan(
        controller_root,
        repository_binding_digest=binding_digest,
    )
    artifact = _artifact(plan)

    inputs = validate_managed_promotion_inputs(
        plan_artifact=plan.to_json(),
        result_artifact=artifact.to_json(),
        todo_id=plan.todo_id,
        project_id=plan.project_id,
        repo_root=promotion_root,
        repository_binding_digest=binding_digest,
        return_id="RETURN-PROMOTION",
    )

    assert inputs.plan.repo_root == promotion_root.resolve()
    assert inputs.plan.identity_digest == plan.identity_digest


@pytest.mark.parametrize("field", ["todo", "project", "repo"])
def test_validation_rejects_outer_identity_drift(tmp_path: Path, field: str) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    plan = _plan(repo_root)
    artifact = _artifact(plan)
    values: dict[str, Any] = {
        "todo_id": plan.todo_id,
        "project_id": plan.project_id,
        "repo_root": repo_root,
    }
    (tmp_path / "other").mkdir()
    values[{"todo": "todo_id", "project": "project_id", "repo": "repo_root"}[field]] = (
        tmp_path / "other" if field == "repo" else "wrong"
    )

    with pytest.raises(ValueError, match=field):
        validate_managed_promotion_inputs(
            plan_artifact=plan.to_json(),
            result_artifact=artifact.to_json(),
            return_id="RETURN-PROMOTION",
            **values,
        )


def test_receipt_rejects_wrong_todo_project_or_repo(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    receipt = ManagedPromotionReceipt(
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

    receipt.verify_for(
        todo_id="TODO-PROMOTION",
        project_id="project-promotion",
        repo_root=repo_root,
        return_id="RETURN-PROMOTION",
    )
    with pytest.raises(ValueError, match="todo"):
        receipt.verify_for(
            todo_id="wrong",
            project_id="project-promotion",
            repo_root=repo_root,
            return_id="RETURN-PROMOTION",
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"marker_verified": False}, "marker verification"),
        ({"development_commit": "not-a-commit"}, "40-character"),
        (
            {"marker": "Gludd-Self-Improve-Artifact=" + "f" * 64},
            "marker does not match",
        ),
        ({"fencing_token": 0}, "positive integer"),
        ({"fencing_token": True}, "positive integer"),
    ],
)
def test_receipt_constructor_rejects_unverified_or_malformed_proof(
    tmp_path: Path,
    changes: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "artifact_digest": "a" * 64,
        "plan_identity_digest": "b" * 64,
        "attempt_identity_digest": "c" * 64,
        "todo_id": "TODO-PROMOTION",
        "project_id": "project-promotion",
        "repo_root": tmp_path,
        "return_id": "RETURN-PROMOTION",
        "development_commit": "d" * 40,
        "marker": "Gludd-Self-Improve-Artifact=" + "a" * 64,
        "fencing_token": 1,
        "marker_verified": True,
    }
    values.update(changes)

    with pytest.raises(ValueError, match=message):
        ManagedPromotionReceipt(**cast(Any, values))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"project_id": "project-other"}, "project identity"),
        ({"return_id": "RETURN-OTHER"}, "return identity"),
        ({"repo_root": None}, "repository root"),
        ({"repo_root": "other"}, "repository identity"),
    ],
)
def test_receipt_rejects_every_scope_identity_mismatch(
    tmp_path: Path,
    changes: dict[str, object],
    message: str,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    receipt = ManagedPromotionReceipt(
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
    values: dict[str, object] = {
        "todo_id": "TODO-PROMOTION",
        "project_id": "project-promotion",
        "repo_root": repo_root,
        "return_id": "RETURN-PROMOTION",
    }
    values.update(changes)

    with pytest.raises(ValueError, match=message):
        receipt.verify_for(**cast(Any, values))


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("plan", "plan identity"),
        ("attempt", "attempt identity"),
        ("accepted", "accepted managed result"),
        ("baseline", "baseline identity"),
        ("task", "task identity"),
        ("scope", "scope"),
    ],
)
def test_validation_rejects_every_result_authority_drift(
    tmp_path: Path,
    field: str,
    message: str,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    plan = _plan(repo_root)
    artifact = _artifact(plan)
    if field == "plan":
        forged = replace(artifact, plan_identity_digest="d" * 64, artifact_digest="")
    elif field == "attempt":
        forged = replace(
            artifact,
            attempt_identity_digest="d" * 64,
            artifact_digest="",
        )
    elif field == "accepted":
        forged = replace(
            artifact,
            accepted=False,
            comparison=replace(artifact.comparison, accepted=False),
            artifact_digest="",
        )
    elif field == "baseline":
        forged = replace(
            artifact,
            proposal=replace(artifact.proposal, baseline_sha="e" * 40),
            artifact_digest="",
        )
    elif field == "task":
        forged = replace(
            artifact,
            proposal=replace(artifact.proposal, task_id="S83.205"),
            artifact_digest="",
        )
    else:
        proposal = replace(
            artifact.proposal,
            edits=(replace(artifact.proposal.edits[0], path="src/general_ludd/other.py"),),
        )
        evidence = replace(
            artifact.evidence,
            changed_files=frozenset({"src/general_ludd/other.py"}),
        )
        forged = replace(
            artifact,
            proposal=proposal,
            evidence=evidence,
            artifact_digest="",
        )

    with pytest.raises(ValueError, match=message):
        validate_managed_promotion_inputs(
            plan_artifact=plan.to_json(),
            result_artifact=forged.to_json(),
            todo_id=plan.todo_id,
            project_id=plan.project_id,
            repo_root=repo_root,
            return_id="RETURN-PROMOTION",
        )


def _completed(
    plan: ApprovedSelfImprovePlan,
    artifact: ManagedSelfImproveResultArtifact,
    *,
    commit: str = "c" * 40,
) -> CompletedManagedPromotion:
    return CompletedManagedPromotion(
        identity=ManagedPromotionIdentity(
            artifact_digest=artifact.artifact_digest,
            plan_identity_digest=plan.identity_digest,
            attempt_identity_digest=plan.attempt_identity_digest,
            todo_id=plan.todo_id,
            project_id=plan.project_id,
            return_id="RETURN-PROMOTION",
            repo_root=str(plan.repo_root),
        ),
        development_commit=commit,
        marker="Gludd-Self-Improve-Artifact=" + artifact.artifact_digest,
        fencing_token=3,
        completed_at=datetime(2029, 12, 31, tzinfo=UTC),
    )


async def test_completed_receipt_is_reverified_without_mutating_git(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    plan = _plan(repo_root)
    artifact = _artifact(plan)
    root_runner = _RootRunner(tmp_path / "unused", marker_present=True)
    repository = _Repository(_completed(plan, artifact))
    coordinator = ManagedSelfImprovePromotionCoordinator(
        repository,
        root_runner=root_runner,
        owner="event-loop-promotion",
    )

    receipt = await coordinator.promote(
        plan_artifact=plan.to_json(),
        result_artifact=artifact.to_json(),
        todo_id=plan.todo_id,
        project_id=plan.project_id,
        repo_root=repo_root,
        return_id="RETURN-PROMOTION",
    )

    assert receipt.development_commit == "c" * 40
    assert [target for target, _ in root_runner.calls] == [
        "self-improve-promotion-marker", "worktree-state", "development-fast-forward",
        "_gate-fresh-check", "git-show-commit",
    ]
    assert not repository.completed
    assert not repository.abandoned


@pytest.mark.parametrize("marker_present", [False, True])
async def test_completed_receipt_rejects_missing_or_drifted_git_marker(
    tmp_path: Path,
    marker_present: bool,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    plan = _plan(repo_root)
    artifact = _artifact(plan)
    completed = _completed(
        plan,
        artifact,
        commit="d" * 40 if marker_present else "c" * 40,
    )
    coordinator = ManagedSelfImprovePromotionCoordinator(
        _Repository(completed),
        root_runner=_RootRunner(tmp_path / "unused", marker_present=marker_present),
        owner="event-loop-promotion",
    )

    with pytest.raises(RuntimeError, match=r"not reachable|identity drifted"):
        await coordinator.promote(
            plan_artifact=plan.to_json(),
            result_artifact=artifact.to_json(),
            todo_id=plan.todo_id,
            project_id=plan.project_id,
            repo_root=repo_root,
            return_id="RETURN-PROMOTION",
        )


class _FailingWorktreeRunner(_WorktreeRunner):
    def run_command(self, command: str, *, timeout: int = 900) -> MakeResult:
        self.commands.append(command)
        return _result(1, stderr="canonical check failed")


async def test_failed_candidate_preserves_materialization_and_durable_binding(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    repo_root.mkdir()
    (worktree / "src/general_ludd").mkdir(parents=True)
    (worktree / "src/general_ludd/example.py").write_text(
        "return 0", encoding="utf-8"
    )
    plan = _plan(repo_root)
    artifact = _artifact(plan)
    repository = _Repository(_claim(artifact))
    root_runner = _RootRunner(worktree)
    coordinator = ManagedSelfImprovePromotionCoordinator(
        repository,
        root_runner=root_runner,
        make_runner_factory=_FailingWorktreeRunner,
        owner="event-loop-promotion",
    )

    with pytest.raises(RuntimeError, match=r"canonical check.*failed"):
        await coordinator.promote(
            plan_artifact=plan.to_json(),
            result_artifact=artifact.to_json(),
            todo_id=plan.todo_id,
            project_id=plan.project_id,
            repo_root=repo_root,
            return_id="RETURN-PROMOTION",
        )

    assert not repository.abandoned
    assert not repository.completed
    assert repository.bound, "the preserved candidate must retain its durable owner binding"
    assert worktree.exists()
    assert [target for target, _ in root_runner.calls] == [
        "self-improve-promotion-marker", "agent-worktree-base",
    ]


def test_installed_coordinator_uses_database_and_make_composition_root(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    coordinator = build_managed_self_improve_promotion_coordinator(
        AsyncMock(), repo_root, "event-loop-promotion"
    )

    assert isinstance(coordinator, ManagedSelfImprovePromotionCoordinator)
