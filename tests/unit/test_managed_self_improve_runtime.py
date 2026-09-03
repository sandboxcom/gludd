"""Installed-package composition contracts for managed self-improvement."""

from __future__ import annotations

import argparse
import ast
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
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
    PromptPlan,
    TaskSpec,
)
from general_ludd.self_improve.runtime import (
    MakeResult,
    build_managed_self_improve_runner,
    prepare_managed_self_improve_plan,
)


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


class _PreparationRunner:
    """Make-only double for reference inspection and context ownership."""

    def __init__(self, context_root: Path, calls: list[str]) -> None:
        self.context_root = context_root
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
        output = ""
        if target == "git-show-name-only":
            output = "\n".join(
                (
                    "src/general_ludd/example.py",
                    "tests/unit/test_example.py",
                )
            )
        elif target == "git-show-full":
            output = "--- a/example.py\n+++ b/example.py\n-old\n+new\n"
        elif target == "agent-worktree-base":
            source = self.context_root / "src/general_ludd/example.py"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("VALUE = 0\n", encoding="utf-8")
            test = self.context_root / "tests/unit/test_example.py"
            test.parent.mkdir(parents=True, exist_ok=True)
            test.write_text("assert True\n", encoding="utf-8")
            output = f"WORKTREE_PATH={self.context_root}\n"
        elif target == "agent-merge-dev":
            raise AssertionError("plan preparation must never merge")
        return MakeResult(("make", target), 0, output, "", 0.01)

    def run_command(self, command: str, *, timeout: int = 900) -> MakeResult:
        del command, timeout
        raise AssertionError("non-mechanical preparation must not run a command")

    def run_observable(
        self,
        target: str,
        variables: dict[str, str],
        *,
        timeout: int,
    ) -> MakeResult:
        del target, variables, timeout
        raise AssertionError("plan preparation must never run inference")


def test_prepare_plan_binds_identity_round_trips_and_cleans_context(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    context_root = tmp_path / "context"
    calls: list[str] = []
    runner = _PreparationRunner(context_root, calls)
    explicit_model = tmp_path / "model.gguf"

    plan = prepare_managed_self_improve_plan(
        repo_root,
        approval_id="approval-prepared",
        todo_id="todo-prepared",
        project_id="project-prepared",
        baseline_ref="a" * 40,
        reference_ref="b" * 40,
        task=_task(),
        max_attempts=2,
        explicit_model_path=explicit_model,
        root_runner=runner,
        make_runner_factory=lambda _path: runner,
    )

    assert plan.approval_id == "approval-prepared"
    assert plan.todo_id == "todo-prepared"
    assert plan.project_id == "project-prepared"
    assert plan.repo_root == repo_root.resolve()
    assert plan.reference.baseline_sha == "a" * 40
    assert plan.reference.reference_sha == "b" * 40
    assert plan.task == _task()
    assert plan.max_attempts == 2
    assert plan.explicit_model_path == explicit_model.resolve()
    assert isinstance(plan.prompt, PromptPlan)
    payload = json.loads(plan.to_json())
    assert payload["schema_version"] == 3
    assert payload["repo_root"] == str(repo_root.resolve())
    assert "repository_binding_digest" not in payload
    assert payload["prompt"]["value"]["proposal_protocol"] == (
        "self-improve-compact-proposal-v4"
    )
    assert plan.required_output_tokens > 0
    assert ApprovedSelfImprovePlan.from_json(plan.to_json()) == plan
    assert calls == [
        "git-show-name-only",
        "git-show-full",
        "agent-worktree-base",
        "agent-cleanup",
    ]


def test_prepare_plan_cleans_context_when_prompt_construction_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    calls: list[str] = []
    runner = _PreparationRunner(tmp_path / "context", calls)

    def fail_prompt(*_args: object) -> NoReturn:
        raise RuntimeError("prompt failed")

    monkeypatch.setattr(runtime_module, "build_prompt", fail_prompt)

    with pytest.raises(RuntimeError, match="prompt failed"):
        prepare_managed_self_improve_plan(
            repo_root,
            approval_id="approval-prepared",
            todo_id="todo-prepared",
            project_id="project-prepared",
            baseline_ref="a" * 40,
            reference_ref="b" * 40,
            task=_task(),
            max_attempts=2,
            root_runner=runner,
            make_runner_factory=lambda _path: runner,
        )

    assert calls[-2:] == ["agent-worktree-base", "agent-cleanup"]


def test_cli_benchmark_delegates_approved_plan_preparation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_file = tmp_path / "task.json"
    task_file.write_text(
        '{"task_id":"S83.201","objective":"Create one exact package-runtime '
        'fixture.","canonical_make_commands":["make test-files '
        'TESTFILES=tests/unit/test_example.py"]}',
        encoding="utf-8",
    )
    repo_root = Path(runtime_module.__file__).resolve().parents[3]
    plan = _approved_plan(repo_root)
    expected = object()
    prepared: list[dict[str, object]] = []

    def prepare(path: Path, **kwargs: object) -> ApprovedSelfImprovePlan:
        prepared.append({"repo_root": path, **kwargs})
        return plan

    monkeypatch.setattr(runtime_module, "prepare_managed_self_improve_plan", prepare)
    monkeypatch.setattr(
        runtime_module,
        "build_managed_self_improve_runner",
        lambda *_args, **_kwargs: SimpleNamespace(
            run=lambda approved: SimpleNamespace(
                final_result=expected if approved is plan else None
            )
        ),
    )
    args = argparse.Namespace(
        target="unit",
        local_model_path="/tmp/model.gguf",
        baseline_ref="a" * 40,
        reference_ref="b" * 40,
        task_file=str(task_file),
        max_attempts=2,
        merge=False,
        validate_only=False,
    )

    assert runtime_module.run_benchmark(args) is expected
    assert prepared == [
        {
            "repo_root": repo_root,
            "approval_id": "cli:unit:" + ("b" * 40),
            "todo_id": "S83.201",
            "project_id": "cli-self-improve",
            "baseline_ref": "a" * 40,
            "reference_ref": "b" * 40,
            "task": TaskSpec.from_path(task_file),
            "max_attempts": 2,
            "explicit_model_path": Path("/tmp/model.gguf"),
            "output_token_limit": 4096,
            "root_runner": prepared[0]["root_runner"],
            "make_runner_factory": runtime_module.MakeRunner,
        }
    ]


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
    evaluation_events = [
        event
        for event in events
        if event.startswith("SELF_IMPROVE_EVALUATION_EVENT ")
    ]
    assert [
        event.split(" phase=", 1)[1].split()[0] for event in evaluation_events
    ] == [
        "apply",
        "syntax_preflight",
        "approved_make",
        "test_count",
        "stage",
        "commit",
        "clean",
        "patch_equivalence",
        "cleanup",
        "comparison",
    ]
    assert all("command_sha256=" in event for event in evaluation_events)
    assert str(repo_root) not in "\n".join(events)
    assert str(attempt_root) not in "\n".join(events)


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
    assert self_improve_package.prepare_managed_self_improve_plan is (
        prepare_managed_self_improve_plan
    )
    tree = ast.parse(inspect.getsource(runtime_module))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(name == "scripts" or name.startswith("scripts.") for name in imported)
