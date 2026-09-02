"""Installed-package composition contracts for managed self-improvement."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import NoReturn, cast

import pytest

import general_ludd.self_improve as self_improve_package
import general_ludd.self_improve.runtime as runtime_module
from general_ludd.self_improve.codex_comparison import CodexReference, ProposalManifest
from general_ludd.self_improve.managed_runner import (
    ApprovedSelfImprovePlan,
    ManagedOutcomeAdapter,
    ManagedSelfImproveRunner,
    PlanBoundProposal,
    TaskSpec,
)
from general_ludd.self_improve.runtime import MakeResult, build_managed_self_improve_runner


def _task() -> TaskSpec:
    return TaskSpec(
        task_id="S83.201",
        objective="Create one exact package-runtime fixture.",
        canonical_make_commands=(
            "make test-files TESTFILES=tests/unit/test_example.py",
        ),
    )


def _reference() -> CodexReference:
    return CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({"src/general_ludd/example.py"}),
        test_files=frozenset({"tests/unit/test_example.py"}),
        changed_lines=1,
        elapsed_seconds=0.1,
    )


def _proposal() -> ProposalManifest:
    return ProposalManifest.from_json(
        """{
          "schema_version": 1,
          "baseline_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "task_id": "S83.201",
          "edits": [{
            "operation": "create",
            "path": "src/general_ludd/example.py",
            "old_text": "",
            "new_text": "VALUE = 1\\n"
          }],
          "tests": ["tests/unit/test_example.py"],
          "make_commands": [
            "make test-files TESTFILES=tests/unit/test_example.py"
          ],
          "commit_message": "feat: add package runtime fixture"
        }"""
    )


def _approved_plan(repo_root: Path) -> ApprovedSelfImprovePlan:
    return ApprovedSelfImprovePlan.approve(
        approval_id="approval-package-runtime",
        todo_id="todo-package-runtime",
        project_id="project-package-runtime",
        repo_root=repo_root,
        task=_task(),
        reference=_reference(),
        prompt="bounded package prompt",
        required_output_tokens=512,
        max_attempts=1,
        mechanical_proposal=_proposal(),
    )


class _FakeMakeRunner:
    """Make protocol double proving the composition root needs no direct shell."""

    def __init__(self, repo_root: Path, attempt_root: Path, calls: list[str]) -> None:
        self.repo_root = repo_root
        self.attempt_root = attempt_root
        self.calls = calls

    def run(
        self,
        target: str,
        variables: dict[str, str] | None = None,
        *,
        timeout: int = 120,
        read_only: bool = False,
    ) -> MakeResult:
        del variables, timeout, read_only
        self.calls.append(target)
        if target == "agent-merge-dev":
            raise AssertionError("managed runtime must never merge a live branch")
        if target == "agent-worktree-base":
            self.attempt_root.mkdir(parents=True, exist_ok=True)
            output = f"WORKTREE_PATH={self.attempt_root}\n"
        elif target == "git-patch-equivalence":
            output = "package-runtime-patch"
        else:
            output = ""
        return MakeResult(("make", target), 0, output, "", 0.01)

    def run_command(self, command: str, *, timeout: int = 900) -> MakeResult:
        del timeout
        target = command.split()[1]
        self.calls.append(target)
        return MakeResult(tuple(command.split()), 0, "", "", 0.01)

    def run_observable(
        self,
        target: str,
        variables: dict[str, str],
        *,
        timeout: int,
    ) -> MakeResult:
        del variables, timeout
        raise AssertionError(f"mechanical plan unexpectedly requested inference: {target}")


def test_serialized_plan_runs_through_package_factory_without_shell_or_merge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    attempt_root = tmp_path / "attempt"
    calls: list[str] = []
    events: list[str] = []

    def no_shell(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("package factory attempted direct shell access")

    monkeypatch.setattr(runtime_module, "MakeRunner", no_shell)
    plan = _approved_plan(repo_root)
    hydrated = ApprovedSelfImprovePlan.from_json(plan.to_json())
    runner = build_managed_self_improve_runner(
        repo_root,
        make_runner_factory=lambda path: _FakeMakeRunner(path, attempt_root, calls),
        progress_sink=events.append,
    )

    result = runner.run(hydrated)

    assert isinstance(runner, ManagedSelfImproveRunner)
    assert result.attempts == 1
    assert result.plan_identity_digest == hydrated.identity_digest
    assert "agent-cleanup" in calls
    assert "agent-merge-dev" not in calls
    assert events[0].startswith("SELF_IMPROVE_ATTEMPT_START")


def test_package_factory_fails_closed_for_repository_or_merge_authority(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    other_root = tmp_path / "other"
    other_root.mkdir()
    attempt_root = tmp_path / "attempt"
    calls: list[str] = []
    operation_runner = _FakeMakeRunner(repo_root, attempt_root, calls)
    def outcome_factory(cache_root: Path) -> ManagedOutcomeAdapter:
        del cache_root
        return cast(ManagedOutcomeAdapter, object())

    runner = build_managed_self_improve_runner(
        repo_root,
        root_runner=operation_runner,
        outcome_adapter_factory=outcome_factory,
    )

    with pytest.raises(ValueError, match="different repository"):
        runner.run(_approved_plan(other_root))
    with pytest.raises(ValueError, match="ApprovedSelfImprovePlan"):
        runner.run(cast(ApprovedSelfImprovePlan, object()))
    with pytest.raises(ValueError, match="cannot merge"):
        runner.attempt_evaluator(
            _task(),
            _reference(),
            PlanBoundProposal(
                _proposal(),
                _approved_plan(repo_root).attempt_identity_digest,
            ),
            1,
            expected_attempt_identity_digest=(
                _approved_plan(repo_root).attempt_identity_digest
            ),
            merge=True,
        )
    assert runner.outcome_adapter_factory is outcome_factory
    assert calls == []


def test_package_factory_rejects_non_path_repository() -> None:
    with pytest.raises(ValueError, match=r"pathlib\.Path"):
        build_managed_self_improve_runner(cast(Path, "not-a-path"))


def test_package_composition_root_is_exported_without_script_dependency() -> None:
    assert self_improve_package.build_managed_self_improve_runner is (
        build_managed_self_improve_runner
    )
    tree = ast.parse(inspect.getsource(runtime_module))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(name == "scripts" or name.startswith("scripts.") for name in imported)
