"""Runtime contracts for the Make-mediated self-improvement runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

import general_ludd.self_improve.codex_comparison as comparison_module
import general_ludd.self_improve.managed_runner as managed_runner_module
import general_ludd.self_improve.runtime as runner_module
from general_ludd.local_model import get_model
from general_ludd.self_improve.codex_comparison import (
    CandidateEvidence,
    CodexReference,
    ComparisonResult,
    PlannerFeedbackExchange,
    ProposalManifest,
    build_retry_prompt,
)
from general_ludd.self_improve.managed_runner import ManagedRunResult
from general_ludd.self_improve.model_lifecycle import AcquiredModel, ModelArtifactIdentity
from general_ludd.self_improve.result_artifact import ManagedSelfImproveResultArtifact
from general_ludd.self_improve.runtime import (
    MakeResult,
    MakeRunner,
    TaskSpec,
    apply_proposal,
    build_prompt,
    canonical_test_paths,
    estimate_required_output_tokens,
    generate_mechanical_proposal,
    mechanical_make_route,
    parse_coverage_evidence,
    parse_reference_files,
    proposal_from_mechanical_changes,
    proposal_scope_matches,
    quality_defaults_for_paths,
)


def _manifest() -> ProposalManifest:
    return ProposalManifest.from_json(
        json.dumps(
            {
                "schema_version": 1,
                "baseline_sha": "a" * 40,
                "task_id": "S83.133",
                "edits": [
                    {
                        "operation": "replace",
                        "path": "src/general_ludd/example.py",
                        "old_text": "return 0",
                        "new_text": "return 42",
                    },
                    {
                        "operation": "replace",
                        "path": "tests/unit/test_example.py",
                        "old_text": "assert False",
                        "new_text": "assert True",
                    },
                ],
                "tests": ["tests/unit/test_example.py"],
                "make_commands": [
                    "make test-files TESTFILES=tests/unit/test_example.py PYTEST_ARGS=-q",
                    "make coverage-files COVERAGE_TESTFILES=tests/unit/test_example.py",
                    "make lint-files FILES=src/general_ludd/example.py",
                    "make typecheck-scope FILES=src/general_ludd/example.py",
                    "make lint-docstrings DOCSTRING_FILES=src/general_ludd/example.py",
                    "make check-resource-ownership RESOURCE_OWNERSHIP_SCOPE=src/general_ludd/example.py",
                ],
                "commit_message": "fix: validate local proposal",
            }
        )
    )


def _edit_manifest(edits: list[dict[str, str]]) -> ProposalManifest:
    return ProposalManifest.from_json(
        json.dumps(
            {
                "schema_version": 1,
                "baseline_sha": "a" * 40,
                "task_id": "S83.133",
                "edits": edits,
                "tests": ["tests/unit/test_example.py"],
                "make_commands": [
                    "make test-files TESTFILES=tests/unit/test_example.py"
                ],
                "commit_message": "fix: exact patch",
            }
        )
    )


def test_make_runner_executes_only_make_without_shell_and_caches_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "ok\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = MakeRunner(tmp_path)
    first = runner.run("git-show-name-only", {"SHA": "b" * 40}, read_only=True)
    second = runner.run("git-show-name-only", {"SHA": "b" * 40}, read_only=True)

    assert first.stdout == second.stdout == "ok\n"
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv[0] == "make"
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["shell"] is False
    assert "VIRTUAL_ENV" not in kwargs["env"]
    assert kwargs["timeout"] == 120


def test_make_runner_never_caches_mutations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = MakeRunner(tmp_path)
    runner.run("repo-status")
    runner.run("repo-status")
    assert calls == 2


def test_make_runner_rejects_non_make_command_before_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject an unsafe command and accept one exact Make command."""
    runner = MakeRunner(tmp_path)
    with pytest.raises(ValueError, match="bounded make command"):
        runner.run_command("python -m pytest")

    def observe(argv: list[str], *, timeout: int) -> MakeResult:
        return MakeResult(tuple(argv), 0, str(timeout), "", 0.1)

    monkeypatch.setattr(runner, "_run_observable_argv", observe)
    result = runner.run_command("make ps", timeout=7)
    assert result.argv == ("make", "ps")
    assert result.stdout == "7"


@pytest.mark.parametrize(
    "payload, match",
    [
        ({"task_id": "S83.133", "objective": "x"}, "canonical_make_commands"),
        (
            {
                "task_id": "S83.133",
                "objective": "x",
                "canonical_make_commands": ["python -m pytest"],
            },
            "make command",
        ),
        (
            {
                "task_id": "S83.133",
                "objective": "x",
                "canonical_make_commands": ["make test-files"],
                "unexpected": True,
            },
            "unknown",
        ),
    ],
)
def test_task_spec_fails_closed(
    tmp_path: Path, payload: dict[str, object], match: str
) -> None:
    task = tmp_path / "task.json"
    task.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        TaskSpec.from_path(task)


def test_reference_file_parser_is_exact_and_bounded() -> None:
    output = """git show --name-only bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
commit bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
Author: Example <example.invalid>
Date: Thu Jan 1 00:00:00 1970 +0000

    fix: example

src/general_ludd/example.py
tests/unit/test_example.py
"""
    assert parse_reference_files(output) == frozenset(
        {"src/general_ludd/example.py", "tests/unit/test_example.py"}
    )
    with pytest.raises(ValueError, match="no repository files"):
        parse_reference_files("commit " + "b" * 40)


@pytest.mark.parametrize(
    "unsafe_path",
    ("/absolute.py", "src/../escape.py", ".git/config"),
)
def test_reference_file_parser_rejects_each_unsafe_path_component(
    unsafe_path: str,
) -> None:
    """Fail closed for each absolute, traversal, and Git-internal path class."""
    with pytest.raises(ValueError, match="unsafe reference path"):
        parse_reference_files(unsafe_path)


def test_model_release_report_survives_unreadable_lease_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Publish conservative release evidence when lease inspection fails."""
    model = AcquiredModel(
        path=tmp_path / "model.gguf",
        model_id="bounded-model",
        repo_id="example/model",
        filename="model.gguf",
        resolved_revision="a" * 40,
        artifact_sha256="b" * 64,
        source="managed",
        manifest_path=tmp_path / "manifest.json",
        lease_path=tmp_path / "lease",
    )

    def fail_exists(_path: Path) -> bool:
        raise OSError("bounded denial")

    with monkeypatch.context() as context:
        context.setattr(Path, "exists", fail_exists)
        runner_module._report_model_release(model)

    assert "lease_released=false" in capsys.readouterr().out


def test_reference_file_parser_rejects_oversized_scope() -> None:
    """Reject a reference whose unique file count exceeds the hard boundary."""
    output = "\n".join(
        f"src/file-{index}.py"
        for index in range(runner_module._MAX_REFERENCE_FILES + 1)
    )
    with pytest.raises(ValueError, match="reference exceeds"):
        parse_reference_files(output)


def test_apply_proposal_atomically_replaces_all_bounded_files(tmp_path: Path) -> None:
    source = tmp_path / "src/general_ludd/example.py"
    test = tmp_path / "tests/unit/test_example.py"
    source.parent.mkdir(parents=True)
    test.parent.mkdir(parents=True)
    source.write_text("def answer() -> int:\n    return 0\n", encoding="utf-8")
    test.write_text("def test_answer() -> None:\n    assert False\n", encoding="utf-8")

    changed_lines = apply_proposal(tmp_path, _manifest())

    assert source.read_text(encoding="utf-8").endswith("return 42\n")
    assert test.read_text(encoding="utf-8").endswith("assert True\n")
    assert changed_lines == 4
    assert not list(tmp_path.rglob("*.self-improve-tmp"))


@pytest.mark.parametrize(
    "output, expected",
    [
        (
            "TOTAL 100 5 20 2 93%\nAll files meet threshold; minimum file coverage: 81%\n",
            (93.0, 81.0),
        ),
        (
            "COVERAGE_FILES_PASS aggregate=88.5 min_file=76.25\n",
            (88.5, 76.25),
        ),
    ],
)
def test_coverage_parser_requires_aggregate_and_per_file(
    output: str, expected: tuple[float, float]
) -> None:
    assert parse_coverage_evidence(output) == expected
    with pytest.raises(ValueError, match="coverage evidence"):
        parse_coverage_evidence("17 tests passed")


def test_output_capacity_estimate_rejects_codex_patch_that_cannot_fit_decode() -> None:
    assert estimate_required_output_tokens(changed_lines=932, changed_files=13) > 4096
    assert estimate_required_output_tokens(changed_lines=10, changed_files=2) < 4096


@pytest.mark.parametrize(
    ("changed_lines", "changed_files"),
    ((-1, 0), (0, -1)),
)
def test_output_capacity_estimate_rejects_each_negative_metric(
    changed_lines: int,
    changed_files: int,
) -> None:
    """Reject either invalid reference metric before estimating capacity."""
    with pytest.raises(ValueError, match="non-negative"):
        estimate_required_output_tokens(changed_lines, changed_files)


def test_docs_only_patch_marks_python_quality_gates_not_applicable() -> None:
    assert quality_defaults_for_paths(
        ["docs/features/example.md"],
        aggregate=0.0,
        minimum=0.0,
        targets={"lint-markdown"},
    ) == (100.0, 100.0, True, True, True)
    assert quality_defaults_for_paths(
        ["src/general_ludd/example.py"],
        aggregate=91.0,
        minimum=81.0,
        targets={"lint-files", "typecheck-scope", "lint-docstrings"},
    ) == (91.0, 81.0, True, True, True)


def test_canonical_test_paths_cover_docs_only_reference() -> None:
    assert canonical_test_paths(
        (
            "make test-files TESTFILES=tests/unit/test_markdown_docs_deep.py PYTEST_ARGS=-q",
            "make lint-markdown MARKDOWN_FILES=docs/features/example.md",
        )
    ) == ("tests/unit/test_markdown_docs_deep.py",)


def test_required_prompt_tests_fail_closed_without_test_identity() -> None:
    """Reject prompt construction when neither reference nor command names a test."""
    task = TaskSpec(
        task_id="S83.133",
        objective="Edit one file.",
        canonical_make_commands=("make lint-files FILES=src/example.py",),
    )
    reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({"src/example.py"}),
        test_files=frozenset(),
        changed_lines=1,
        elapsed_seconds=1.0,
    )

    with pytest.raises(ValueError, match="no canonical test path"):
        runner_module._required_prompt_tests(task, reference)


def test_prompt_requires_at_least_one_exact_focus_path(tmp_path: Path) -> None:
    """Reject an empty Codex file identity before reading baseline context."""
    task = TaskSpec(
        task_id="S83.133",
        objective="Edit one file.",
        canonical_make_commands=(
            "make test-files TESTFILES=tests/unit/test_example.py",
        ),
    )
    reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset(),
        test_files=frozenset({"tests/unit/test_example.py"}),
        changed_lines=0,
        elapsed_seconds=1.0,
    )

    with pytest.raises(ValueError, match="no prompt paths"):
        build_prompt(task, reference, tmp_path)


def test_prompt_uses_exact_codex_paths_instead_of_placeholder(tmp_path: Path) -> None:
    relative = "docs/features/example.md"
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_text("# Example\n\ntext  \n", encoding="utf-8")
    task = TaskSpec(
        task_id="S83.133",
        objective="Remove the trailing whitespace.",
        canonical_make_commands=(
            "make test-files TESTFILES=tests/unit/test_markdown_docs_deep.py PYTEST_ARGS=-q",
        ),
    )
    reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({relative}),
        test_files=frozenset(),
        changed_lines=2,
        elapsed_seconds=10.0,
    )

    prompt = build_prompt(task, reference, tmp_path)

    assert relative in prompt
    assert "repository/path" not in prompt


def test_exact_patch_rejects_ambiguous_match_without_partial_write(
    tmp_path: Path,
) -> None:
    first = tmp_path / "src/first.py"
    second = tmp_path / "src/second.py"
    first.parent.mkdir(parents=True)
    first.write_text("before\n", encoding="utf-8")
    second.write_text("repeat\nrepeat\n", encoding="utf-8")
    proposal = _edit_manifest(
        [
            {
                "operation": "replace",
                "path": "src/first.py",
                "old_text": "before",
                "new_text": "after",
            },
            {
                "operation": "replace",
                "path": "src/second.py",
                "old_text": "repeat",
                "new_text": "changed",
            },
        ]
    )

    with pytest.raises(ValueError, match="exactly once"):
        apply_proposal(tmp_path, proposal)

    assert first.read_text(encoding="utf-8") == "before\n"
    assert second.read_text(encoding="utf-8") == "repeat\nrepeat\n"


def test_exact_patch_supports_confined_create_and_delete(tmp_path: Path) -> None:
    obsolete = tmp_path / "src/obsolete.py"
    obsolete.parent.mkdir(parents=True)
    obsolete.write_text("obsolete = True\n", encoding="utf-8")
    proposal = _edit_manifest(
        [
            {
                "operation": "create",
                "path": "src/created.py",
                "old_text": "",
                "new_text": "created = True\n",
            },
            {
                "operation": "delete",
                "path": "src/obsolete.py",
                "old_text": "obsolete = True\n",
                "new_text": "",
            },
        ]
    )

    assert apply_proposal(tmp_path, proposal) == 2
    assert (tmp_path / "src/created.py").read_text(encoding="utf-8") == "created = True\n"
    assert not obsolete.exists()


@pytest.mark.parametrize(
    ("operation", "old_text", "new_text", "current", "match"),
    (
        ("create", "", "created = True\n", "already = True\n", "already exists"),
        ("delete", "expected = True\n", "", "different = True\n", "complete file"),
    ),
)
def test_exact_patch_rejects_existing_create_and_stale_delete(
    tmp_path: Path,
    operation: str,
    old_text: str,
    new_text: str,
    current: str,
    match: str,
) -> None:
    """Reject conflicting create/delete state without mutating the target."""
    target = tmp_path / "src/example.py"
    target.parent.mkdir(parents=True)
    target.write_text(current, encoding="utf-8")
    proposal = _edit_manifest(
        [
            {
                "operation": operation,
                "path": "src/example.py",
                "old_text": old_text,
                "new_text": new_text,
            }
        ]
    )

    with pytest.raises(ValueError, match=match):
        apply_proposal(tmp_path, proposal)

    assert target.read_text(encoding="utf-8") == current


def test_scope_mismatch_is_known_before_worktree_or_tests() -> None:
    proposal = _manifest()
    assert proposal_scope_matches(
        proposal,
        frozenset(
            {"src/general_ludd/example.py", "tests/unit/test_example.py"}
        ),
    )
    assert not proposal_scope_matches(proposal, frozenset({"docs/unrelated.md"}))


def test_installed_failure_diagnosis_compacts_captured_trace_without_raw_secrets() -> None:
    """Preserve exact failure facts without forwarding an unbounded raw trace."""
    secret = "gludd_debug_token_never_emit_123456789"
    captured_trace = (
        "SELF_IMPROVE_COMMAND_START command=\"make test-files\"\n"
        f"AUTH_TOKEN={secret}\n"
        + ("unbounded stack frame with model-controlled detail\n" * 4_000)
        + "SELF_IMPROVE_LOCAL_DECODE phase=proposal_decode finish=length "
        "prompt_tokens=88 completion_tokens=256 total_tokens=344 budget=256\n"
        "SELF_IMPROVE_FAILURE phase=proposal_decode "
        "failure=decode_budget_exhausted\n"
        "SELF_IMPROVE_COMMAND_END rc=1 elapsed=2.00s\n"
    )

    artifact = runner_module.compact_failure_diagnosis(
        captured_trace,
        hypothesis="proposal output exhausted the configured decode budget",
        max_bytes=256,
        max_tokens=256,
    )

    assert json.loads(artifact) == {
        "exit_code": 1,
        "failure_class": "decode_budget_exhausted",
        "finish_reason": "length",
        "finished": True,
        "hypothesis": "proposal output exhausted the configured decode budget",
        "phase": "proposal_decode",
        "schema_version": 1,
    }
    assert artifact == json.dumps(
        json.loads(artifact), ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    assert len(artifact.encode("utf-8")) <= 256
    assert secret not in artifact
    assert "unbounded stack frame" not in artifact
    with pytest.raises(ValueError, match="token budget"):
        runner_module.compact_failure_diagnosis(
            captured_trace,
            hypothesis="proposal output exhausted the configured decode budget",
            max_bytes=256,
            max_tokens=len(artifact.encode("utf-8")) - 1,
        )
    with pytest.raises(ValueError, match="secret-like material"):
        runner_module.compact_failure_diagnosis(
            captured_trace,
            hypothesis="token=do-not-forward",
        )


def test_installed_failure_diagnosis_fails_closed_on_incomplete_facts() -> None:
    """Reject malformed inputs rather than inventing diagnostic state."""
    valid_trace = (
        "SELF_IMPROVE_LOCAL_DECODE phase=proposal_decode finish=length\n"
        "SELF_IMPROVE_FAILURE phase=proposal_decode failure=decode_failed\n"
        "SELF_IMPROVE_COMMAND_END rc=1 elapsed=2.00s\n"
    )
    invalid_cases: tuple[tuple[str, str, int, int, str], ...] = (
        ("", "bounded hypothesis", 512, 512, "non-empty string"),
        (valid_trace, "", 512, 512, "non-empty string"),
        (valid_trace, "bounded hypothesis", 0, 512, "positive integers"),
        (valid_trace, "x" * 161, 512, 512, "hypothesis exceeds"),
        (
            valid_trace.replace("phase=proposal_decode", "stage=proposal_decode"),
            "bounded hypothesis",
            512,
            512,
            "missing phase",
        ),
        (
            valid_trace.replace("failure=decode_failed", "cause=decode_failed"),
            "bounded hypothesis",
            512,
            512,
            "missing failure class",
        ),
        (
            valid_trace.replace("finish=length", "completion=length"),
            "bounded hypothesis",
            512,
            512,
            "missing finish reason",
        ),
        (
            valid_trace.replace("SELF_IMPROVE_COMMAND_END", "SELF_IMPROVE_COMMAND_STOP"),
            "bounded hypothesis",
            512,
            512,
            "missing exit code",
        ),
        (
            valid_trace.replace("rc=1", "rc=999"),
            "bounded hypothesis",
            512,
            512,
            "outside the bounded range",
        ),
        (valid_trace, "bounded hypothesis", 1, 512, "byte budget"),
    )

    for trace, hypothesis, max_bytes, max_tokens, match in invalid_cases:
        with pytest.raises(ValueError, match=match):
            runner_module.compact_failure_diagnosis(
                trace,
                hypothesis=hypothesis,
                max_bytes=max_bytes,
                max_tokens=max_tokens,
            )


def test_mechanical_docs_route_uses_existing_make_tool_and_minimal_patch() -> None:
    task = TaskSpec(
        task_id="S83.133",
        objective="Remove the trailing whitespace from line 3 without other changes.",
        canonical_make_commands=(
            "make test-files TESTFILES=tests/unit/test_markdown_docs_deep.py",
            "make lint-markdown MARKDOWN_FILES=docs/example.md",
        ),
    )
    reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({"docs/example.md"}),
        test_files=frozenset(),
        changed_lines=2,
        elapsed_seconds=300.0,
    )
    before = {"docs/example.md": "# Title\n\nBody text  \n"}
    after = {"docs/example.md": "# Title\n\nBody text\n"}

    assert mechanical_make_route(task, reference) == "make fix-docs-drift"
    proposal = proposal_from_mechanical_changes(task, reference, before, after)

    assert proposal.edits[0].old_text == "Body text  \n"
    assert proposal.edits[0].new_text == "Body text\n"
    assert proposal.make_commands == task.canonical_make_commands
    assert proposal.tests == ("tests/unit/test_markdown_docs_deep.py",)


def test_mechanical_proposal_requires_canonical_test_path() -> None:
    """A mature-tool patch still fails closed without an exact test path."""
    relative = "docs/example.md"
    task = TaskSpec(
        task_id="S83.133",
        objective="Remove trailing whitespace.",
        canonical_make_commands=(
            "make lint-markdown MARKDOWN_FILES=docs/example.md",
        ),
    )
    reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({relative}),
        test_files=frozenset(),
        changed_lines=2,
        elapsed_seconds=1.0,
    )

    with pytest.raises(ValueError, match="no canonical test path"):
        proposal_from_mechanical_changes(
            task,
            reference,
            {relative: "before\n"},
            {relative: "after\n"},
        )


def test_mechanical_router_does_not_guess_for_python_change() -> None:
    task = TaskSpec(
        task_id="S83.133",
        objective="Repair a parser bug.",
        canonical_make_commands=(
            "make test-files TESTFILES=tests/unit/test_parser.py",
        ),
    )
    reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({"src/general_ludd/parser.py"}),
        test_files=frozenset({"tests/unit/test_parser.py"}),
        changed_lines=4,
        elapsed_seconds=300.0,
    )

    assert mechanical_make_route(task, reference) is None


def test_mechanical_generator_runs_owned_make_tool_before_building_patch(
    tmp_path: Path,
) -> None:
    relative = "docs/example.md"
    document = tmp_path / relative
    document.parent.mkdir(parents=True)
    document.write_text("# Title\n\nBody text  \n", encoding="utf-8")
    task = TaskSpec(
        task_id="S83.133",
        objective="Remove the trailing whitespace from line 3.",
        canonical_make_commands=(
            "make test-files TESTFILES=tests/unit/test_markdown_docs_deep.py",
        ),
    )
    reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({relative}),
        test_files=frozenset(),
        changed_lines=2,
        elapsed_seconds=300.0,
    )

    class MechanicalRunner:
        def run_command(self, command: str, *, timeout: int = 900) -> MakeResult:
            assert command == "make fix-docs-drift"
            assert timeout == 120
            document.write_text("# Title\n\nBody text\n", encoding="utf-8")
            return MakeResult(
                argv=("make", "fix-docs-drift"),
                returncode=0,
                stdout="fixed",
                stderr="",
                elapsed_seconds=0.1,
            )

    proposal = generate_mechanical_proposal(
        MechanicalRunner(),
        task,
        reference,
        tmp_path,
    )

    assert proposal is not None
    assert proposal.edits[0].path == relative


def test_observable_make_target_uses_owned_real_process_boundary() -> None:
    runner = MakeRunner(Path.cwd())

    result = runner.run_observable(
        "self-improve-local-proposal",
        {
            "SELF_IMPROVE_MODEL_PATH": "/tmp/example.gguf",
            "SELF_IMPROVE_PROMPT_FILE": "/tmp/gludd-self-improve-test/prompt.txt",
            "SELF_IMPROVE_PROPOSAL_FILE": "/tmp/gludd-self-improve-test/proposal.json",
            "SELF_IMPROVE_WORKER_VALIDATE_ONLY": "1",
        },
        timeout=10,
    )

    assert result.returncode == 0
    assert "SELF_IMPROVE_LOCAL_PROPOSAL_PLAN" in result.stdout
    assert result.argv[0:2] == ("make", "self-improve-local-proposal")


def test_run_benchmark_validate_only_uses_exact_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_file = tmp_path / "task.json"
    task_file.write_text(
        json.dumps(
            {
                "task_id": "S83.133",
                "objective": "Validate the exact plan.",
                "canonical_make_commands": [
                    "make test-files TESTFILES=tests/unit/test_example.py"
                ],
            }
        ),
        encoding="utf-8",
    )

    class ReferenceRunner:
        def __init__(self, _root: Path) -> None:
            pass

        def run(
            self,
            target: str,
            variables: dict[str, str] | None = None,
            *,
            timeout: int = 120,
            read_only: bool = False,
        ) -> MakeResult:
            del variables, timeout, read_only
            if target == "git-show-name-only":
                output = "commit " + ("b" * 40) + "\ntests/unit/test_example.py\n"
            else:
                output = "--- a/test\n+++ b/test\n-old\n+new\n"
            return MakeResult(("make", target), 0, output, "", 0.1)

    monkeypatch.setattr(runner_module, "MakeRunner", ReferenceRunner)
    args = argparse.Namespace(
        target="validate",
        local_model_path="/tmp/model.gguf",
        baseline_ref="a" * 40,
        reference_ref="b" * 40,
        task_file=str(task_file),
        max_attempts=1,
        merge=False,
        validate_only=True,
    )

    result = runner_module.run_benchmark(args)

    assert result.patch_equivalence == "validate-only"
    assert result.diagnostics == "validate-only"
    assert result.proposal.tests == ("tests/unit/test_example.py",)


def test_evaluate_attempt_covers_green_commit_and_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative = "docs/example.md"
    document = tmp_path / relative
    document.parent.mkdir(parents=True)
    document.write_text("# Title\n\nBody  \n", encoding="utf-8")
    task = TaskSpec(
        task_id="S83.133",
        objective="Remove trailing whitespace.",
        canonical_make_commands=(
            "make test-files TESTFILES=tests/unit/test_markdown_docs_deep.py",
            "make lint-markdown MARKDOWN_FILES=docs/example.md",
        ),
    )
    reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({relative}),
        test_files=frozenset(),
        changed_lines=2,
        elapsed_seconds=300.0,
    )
    proposal = ProposalManifest.from_json(
        json.dumps(
            {
                "schema_version": 1,
                "baseline_sha": "a" * 40,
                "task_id": "S83.133",
                "edits": [
                    {
                        "operation": "replace",
                        "path": relative,
                        "old_text": "Body  \n",
                        "new_text": "Body\n",
                    }
                ],
                "tests": ["tests/unit/test_markdown_docs_deep.py"],
                "make_commands": list(task.canonical_make_commands),
                "commit_message": "fix: exact docs repair",
            }
        )
    )

    class CandidateRunner:
        def __init__(self) -> None:
            self.commands: list[str] = []

        def run_command(self, command: str, *, timeout: int = 900) -> MakeResult:
            del timeout
            self.commands.append(command)
            return MakeResult(tuple(command.split()), 0, "green\n", "", 0.1)

        def run(
            self,
            target: str,
            variables: dict[str, str] | None = None,
            *,
            timeout: int = 120,
            read_only: bool = False,
        ) -> MakeResult:
            del variables, timeout, read_only
            output = "patch-equivalent=1" if target == "git-patch-equivalence" else ""
            return MakeResult(("make", target), 0, output, "", 0.1)

    candidate = CandidateRunner()
    monkeypatch.setattr(runner_module, "create_worktree", lambda *_args: (tmp_path, "candidate"))
    monkeypatch.setattr(runner_module, "MakeRunner", lambda _root: candidate)

    class RootRunner:
        def run(
            self,
            target: str,
            variables: dict[str, str] | None = None,
            *,
            timeout: int = 120,
            read_only: bool = False,
        ) -> MakeResult:
            del variables, timeout, read_only
            return MakeResult(("make", target), 0, "", "", 0.1)

    progress: list[str] = []
    result = runner_module.evaluate_attempt(
        RootRunner(),
        task,
        reference,
        runner_module.PlanBoundProposal(proposal, "c" * 64),
        1,
        expected_attempt_identity_digest="c" * 64,
        merge=False,
        progress_sink=progress.append,
    )

    assert result.comparison.accepted is True
    assert result.evidence.cleanup_passed is True
    assert result.evidence.commit_count == 1
    assert "make test-count" in candidate.commands
    assert document.read_text(encoding="utf-8").endswith("Body\n")
    phases = [event.split(" phase=", 1)[1].split()[0] for event in progress]
    assert phases == [
        "apply",
        "syntax_preflight",
        "approved_make",
        "approved_make",
        "test_count",
        "stage",
        "commit",
        "clean",
        "patch_equivalence",
        "cleanup",
    ]
    assert all(event.startswith("SELF_IMPROVE_EVALUATION_EVENT ") for event in progress)
    assert all(" failure=none" in event and " rc=0 " in event for event in progress)
    assert all(len(event.encode("ascii")) <= 256 for event in progress)
    for event in progress:
        command_digest = event.split(" command_sha256=", 1)[1].split()[0]
        assert len(command_digest) == 64
        int(command_digest, 16)
    assert relative not in "\n".join(progress)


def test_evaluate_attempt_rejects_scope_before_worktree() -> None:
    reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({"docs/example.md"}),
        test_files=frozenset(),
        changed_lines=2,
        elapsed_seconds=1.0,
    )

    result = runner_module.evaluate_attempt(
        MakeRunner(Path.cwd()),
        TaskSpec(
            task_id="S83.133",
            objective="Repair exactly.",
            canonical_make_commands=(
                "make test-files TESTFILES=tests/unit/test_example.py",
            ),
        ),
        reference,
        runner_module.PlanBoundProposal(_manifest(), "c" * 64),
        1,
        expected_attempt_identity_digest="c" * 64,
        merge=False,
    )

    assert result.patch_equivalence == "scope-preflight-rejected"
    assert "outside the exact Codex reference" in result.diagnostics


def test_evaluate_attempt_rejects_plan_identity_drift_before_worktree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A proposal approved under another plan must never reach execution."""
    expected_identity = "a" * 64
    bound = runner_module.PlanBoundProposal(_manifest(), "b" * 64)
    monkeypatch.setattr(
        runner_module,
        "create_worktree",
        lambda *_args: pytest.fail("identity drift must fail before execution"),
    )

    with pytest.raises(ValueError, match="proposal plan identity drifted"):
        runner_module.evaluate_attempt(
            MakeRunner(Path.cwd()),
            TaskSpec(
                task_id="S83.133",
                objective="Repair exact Python code.",
                canonical_make_commands=(
                    "make test-files TESTFILES=tests/unit/test_example.py",
                ),
            ),
            CodexReference(
                baseline_sha="a" * 40,
                reference_sha="b" * 40,
                changed_files=frozenset(
                    {
                        "src/general_ludd/example.py",
                        "tests/unit/test_example.py",
                    }
                ),
                test_files=frozenset({"tests/unit/test_example.py"}),
                changed_lines=4,
                elapsed_seconds=1.0,
            ),
            bound,
            1,
            expected_attempt_identity_digest=expected_identity,
            merge=False,
        )


@pytest.mark.parametrize(
    ("proposal", "identity", "message"),
    [
        (object(), "a" * 64, "proposal manifest"),
        (_manifest(), "A" * 64, "attempt identity"),
    ],
)
def test_plan_bound_proposal_rejects_untrusted_identity_shapes(
    proposal: object,
    identity: str,
    message: str,
) -> None:
    """Only a validated manifest and canonical digest can cross the boundary."""
    with pytest.raises(ValueError, match=message):
        runner_module.PlanBoundProposal(cast(ProposalManifest, proposal), identity)


def test_approval_rejects_bound_proposal_or_manifest_drift() -> None:
    """Approval validation binds both the plan digest and exact proposal value."""
    original = _manifest()
    bound = runner_module.PlanBoundProposal(original, "a" * 64)
    result = replace(
        _attempt_result(accepted=True),
        proposal=original,
        attempt_identity_digest="a" * 64,
    )

    with pytest.raises(ValueError, match="proposal plan identity drifted"):
        runner_module._validate_approved_result_identity(result, bound, "b" * 64)

    with pytest.raises(ValueError, match="approved proposal drifted"):
        runner_module._validate_approved_result_identity(
            replace(result, proposal=replace(original, commit_message="fix: drifted")),
            bound,
            "a" * 64,
        )


def test_runner_validation_helpers_fail_closed() -> None:
    assert runner_module._is_safe_make_command("make test-files") is True
    assert runner_module._is_safe_make_command("python -m pytest") is False
    assert runner_module._is_safe_make_command("make 'unterminated") is False
    assert runner_module._warning_count("WARNING one\n0 warnings\nwarning two") == 2
    assert runner_module._line_count_from_patch("--- a\n+++ b\n-old\n+new\n") == 2
    with pytest.raises(ValueError, match="unsafe Make target"):
        runner_module._validate_target_and_variables("bad target", {})
    with pytest.raises(ValueError, match="unsafe Make variable"):
        runner_module._validate_target_and_variables("good", {"bad": "x"})
    with pytest.raises(ValueError, match="unsafe value"):
        runner_module._validate_target_and_variables("good", {"VALUE": "x\n"})
    with pytest.raises(ValueError, match="exactly 40"):
        runner_module._validate_sha("sha", "bad")


def _attempt_result(*, accepted: bool) -> runner_module.AttemptResult:
    proposal = _manifest()
    return runner_module.AttemptResult(
        comparison=ComparisonResult(
            accepted=accepted,
            score=100.0 if accepted else 70.0,
            blockers=() if accepted else ("tests",),
            changed_file_precision=1.0,
            changed_file_recall=1.0,
        ),
        evidence=CandidateEvidence(
            changed_files=frozenset(edit.path for edit in proposal.edits),
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
            elapsed_seconds=1.0,
            changed_lines=4,
        ),
        patch_equivalence="patch-equivalent=1" if accepted else "patch-equivalent=0",
        proposal=proposal,
        diagnostics="" if accepted else "E assert false",
        attempt_identity_digest=runner_module._attempt_identity_digest(
            "bounded prompt"
        ),
    )


def _planner_feedback_exchange() -> PlannerFeedbackExchange:
    config = get_model("qwen2.5-coder-0.5b")
    assert config is not None
    return PlannerFeedbackExchange(
        plan_identity_digest="a" * 64,
        attempt_identity_digest="b" * 64,
        attempt_number=1,
        model_identity=ModelArtifactIdentity(
            model_id=config.name,
            repo_id=config.repo,
            filename=config.filename,
            revision="c" * 40,
        ),
        task_id="S83.209",
        task_objective="Integrate exact schema feedback.",
        outcome=ComparisonResult(
            accepted=False,
            score=70.0,
            blockers=("tests",),
            changed_file_precision=1.0,
            changed_file_recall=1.0,
        ),
        source_artifact_digest="d" * 64,
    )


@pytest.mark.parametrize(
    ("field_path", "value", "match"),
    [
        (("schema_version",), 2, "schema_version"),
        (("kind",), "other", "kind"),
        (("attempt_number",), 0, "attempt_number"),
        (("plan_identity_digest",), "bad", "SHA-256"),
        (("model_identity", "filename"), 7, "model identity fields"),
        (("task", "task_id"), "bad", "task_id"),
        (("task", "objective"), 7, "task fields"),
        (("source", "kind"), "other", "source kind"),
        (("outcome", "accepted"), "yes", "boolean"),
        (("outcome", "score"), -1, "valid range"),
        (("outcome", "blockers"), "tests", "bounded JSON list"),
        (("outcome", "blockers"), ["tests", "tests"], "unique bounded"),
        (("outcome", "accepted"), True, "contradicts"),
    ],
)
def test_planner_feedback_exchange_rejects_each_identity_or_schema_drift(
    field_path: tuple[str, ...],
    value: object,
    match: str,
) -> None:
    """Reject malformed identities and outcomes before planner persistence."""
    payload = json.loads(_planner_feedback_exchange().to_json())
    target = payload
    for field in field_path[:-1]:
        target = cast(dict[str, Any], target[field])
    target[field_path[-1]] = value

    with pytest.raises(ValueError, match=match):
        PlannerFeedbackExchange.from_json(json.dumps(payload))


def test_planner_feedback_exchange_rejects_ambiguous_json() -> None:
    """Reject non-objects, invalid JSON, and duplicate fields."""
    for raw, match in (
        ("[]", "root fields"),
        ("{", "valid JSON"),
        (
            _planner_feedback_exchange()
            .to_json()
            .replace('"schema_version":1', '"schema_version":1,"schema_version":1'),
            "duplicate field",
        ),
    ):
        with pytest.raises(ValueError, match=match):
            PlannerFeedbackExchange.from_json(raw)


def _benchmark_args(task_file: Path, *, max_attempts: int = 1) -> argparse.Namespace:
    model_path = task_file.parent / "model.gguf"
    model_path.write_bytes(b"GGUF test model")
    return argparse.Namespace(
        target="unit",
        local_model_path=str(model_path),
        baseline_ref="a" * 40,
        reference_ref="b" * 40,
        task_file=str(task_file),
        max_attempts=max_attempts,
        merge=False,
        validate_only=False,
    )


def _benchmark_task_file(tmp_path: Path) -> Path:
    task_file = tmp_path / "task.json"
    task_file.write_text(
        json.dumps(
            {
                "task_id": "S83.133",
                "objective": "Remove trailing whitespace.",
                "canonical_make_commands": [
                    "make test-files TESTFILES=tests/unit/test_example.py"
                ],
            }
        ),
        encoding="utf-8",
    )
    return task_file


def test_run_benchmark_prefers_accepted_mechanical_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_file = _benchmark_task_file(tmp_path)
    reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({"src/general_ludd/example.py", "tests/unit/test_example.py"}),
        test_files=frozenset({"tests/unit/test_example.py"}),
        changed_lines=4,
        elapsed_seconds=10.0,
    )
    accepted = _attempt_result(accepted=True)

    class RootRunner:
        def __init__(self, _root: Path) -> None:
            pass

        def run(
            self,
            target: str,
            variables: dict[str, str] | None = None,
            *,
            timeout: int = 120,
            read_only: bool = False,
        ) -> MakeResult:
            del variables, timeout, read_only
            return MakeResult(("make", target), 0, "", "", 0.1)

    monkeypatch.setattr(runner_module, "MakeRunner", RootRunner)
    monkeypatch.setattr(runner_module, "build_reference", lambda *_args: reference)
    monkeypatch.setattr(runner_module, "create_worktree", lambda *_args: (tmp_path, "context"))
    monkeypatch.setattr(runner_module, "build_prompt", lambda *_args: "bounded prompt")
    monkeypatch.setattr(
        runner_module,
        "generate_mechanical_proposal",
        lambda *_args: _manifest(),
    )
    monkeypatch.setattr(runner_module, "evaluate_attempt", lambda *_args, **_kwargs: accepted)
    monkeypatch.setattr(
        runner_module,
        "generate_local_proposal",
        lambda *_args: pytest.fail("local model must not run for mechanical route"),
    )

    assert runner_module.run_benchmark(_benchmark_args(task_file)) is accepted


def test_run_benchmark_retries_rejected_local_output_with_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_file = _benchmark_task_file(tmp_path)
    reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({"src/general_ludd/example.py", "tests/unit/test_example.py"}),
        test_files=frozenset({"tests/unit/test_example.py"}),
        changed_lines=4,
        elapsed_seconds=10.0,
    )
    accepted = _attempt_result(accepted=True)
    prompts: list[str] = []

    class RootRunner:
        def __init__(self, _root: Path) -> None:
            pass

        def run(
            self,
            target: str,
            variables: dict[str, str] | None = None,
            *,
            timeout: int = 120,
            read_only: bool = False,
        ) -> MakeResult:
            del variables, timeout, read_only
            return MakeResult(("make", target), 0, "", "", 0.1)

    def propose(_runner: object, _model: Path, prompt: str) -> ProposalManifest:
        prompts.append(prompt)
        if len(prompts) == 1:
            raise ValueError("incomplete JSON tail")
        return _manifest()

    monkeypatch.setattr(runner_module, "MakeRunner", RootRunner)
    monkeypatch.setattr(runner_module, "build_reference", lambda *_args: reference)
    monkeypatch.setattr(runner_module, "create_worktree", lambda *_args: (tmp_path, "context"))
    monkeypatch.setattr(runner_module, "build_prompt", lambda *_args: "bounded prompt")
    monkeypatch.setattr(runner_module, "generate_mechanical_proposal", lambda *_args: None)
    monkeypatch.setattr(runner_module, "generate_local_proposal", propose)
    monkeypatch.setattr(runner_module, "evaluate_attempt", lambda *_args, **_kwargs: accepted)

    result = runner_module.run_benchmark(_benchmark_args(task_file, max_attempts=2))

    assert result is accepted
    assert len(prompts) == 2
    assert "protocol=self-improve-validation-retry-v3" in prompts[1]
    assert "type=proposal_validation" in prompts[1]
    assert "source=worker_tail" in prompts[1]
    assert "detail=<redacted>" in prompts[1]
    assert "incomplete JSON tail" not in prompts[1]


def test_run_benchmark_returns_final_rejected_comparison(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_file = _benchmark_task_file(tmp_path)
    reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({"src/general_ludd/example.py", "tests/unit/test_example.py"}),
        test_files=frozenset({"tests/unit/test_example.py"}),
        changed_lines=4,
        elapsed_seconds=10.0,
    )
    rejected = _attempt_result(accepted=False)

    class RootRunner:
        def __init__(self, _root: Path) -> None:
            pass

        def run(
            self,
            target: str,
            variables: dict[str, str] | None = None,
            *,
            timeout: int = 120,
            read_only: bool = False,
        ) -> MakeResult:
            del variables, timeout, read_only
            return MakeResult(("make", target), 0, "", "", 0.1)

    monkeypatch.setattr(runner_module, "MakeRunner", RootRunner)
    monkeypatch.setattr(runner_module, "build_reference", lambda *_args: reference)
    monkeypatch.setattr(runner_module, "create_worktree", lambda *_args: (tmp_path, "context"))
    monkeypatch.setattr(runner_module, "build_prompt", lambda *_args: "bounded prompt")
    monkeypatch.setattr(runner_module, "generate_mechanical_proposal", lambda *_args: None)
    monkeypatch.setattr(runner_module, "generate_local_proposal", lambda *_args: _manifest())
    monkeypatch.setattr(runner_module, "evaluate_attempt", lambda *_args, **_kwargs: rejected)

    assert runner_module.run_benchmark(_benchmark_args(task_file)) is rejected


def test_main_publishes_json_and_fails_for_unaccepted_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = argparse.Namespace(target="unit", validate_only=False)
    rejected = _attempt_result(accepted=False)

    class Parser:
        def parse_args(self) -> argparse.Namespace:
            return args

    monkeypatch.setattr(runner_module, "_parser", lambda: Parser())
    monkeypatch.setattr(runner_module, "run_benchmark", lambda _args: rejected)

    assert runner_module.main() == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["target"] == "unit"
    assert payload["accepted"] is False


def test_main_redacts_native_terminal_failure_to_typed_safe_marker(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = argparse.Namespace(target="unit", validate_only=False)
    raw_failure = (
        "ggml_metal_init model=/Users/operator/models/private.gguf TOKEN=top-secret\n"
        "SELF_IMPROVE_LOCAL_PROPOSAL_ERROR "
        "replace requires distinct non-empty old_text\n"
        '{"e":[{"p":"src/private.py","a":"raw child text","z":"PASSWORD=hunter2"}]}'
    )

    class Parser:
        def parse_args(self) -> argparse.Namespace:
            return args

    monkeypatch.setattr(runner_module, "_parser", lambda: Parser())
    monkeypatch.setattr(
        runner_module,
        "run_benchmark",
        lambda _args: (_ for _ in ()).throw(RuntimeError(raw_failure)),
    )

    assert runner_module.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "SELF_IMPROVE_ERROR protocol=self-improve-validation-retry-v3 "
        "type=edit_replace_contract source=proposal_error "
        "detail=replace requires distinct non-empty old_text\n"
    )
    assert all(
        secret not in captured.err
        for secret in (
            "/Users/operator",
            "private.gguf",
            "top-secret",
            "src/private.py",
            "raw child text",
            "hunter2",
            "Traceback",
        )
    )


def test_main_publishes_bounded_terminal_error_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = argparse.Namespace(target="unit", validate_only=False)

    class Parser:
        def parse_args(self) -> argparse.Namespace:
            return args

    monkeypatch.setattr(runner_module, "_parser", lambda: Parser())
    monkeypatch.setattr(
        runner_module,
        "run_benchmark",
        lambda _args: (_ for _ in ()).throw(RuntimeError("candidate plan exhausted")),
    )

    assert runner_module.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "SELF_IMPROVE_ERROR protocol=self-improve-validation-retry-v3 "
        "type=proposal_validation source=worker_tail detail=<redacted>\n"
    )
    assert "Traceback" not in captured.err


def test_main_preserves_compact_v4_identity_for_live_json_terminal_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Do not relabel a terminal compact-v4 framing failure as retry-v3."""
    args = argparse.Namespace(target="unit", validate_only=False)
    raw_failure = (
        "llama loader /Users/operator/private.gguf TOKEN=top-secret\n"
        "SELF_IMPROVE_LOCAL_PROPOSAL_ERROR "
        "compact-v4 proposal is not one complete JSON object; output_bytes=2308\n"
        "PASSWORD=hunter2 raw-model-fragment"
    )

    class Parser:
        def parse_args(self) -> argparse.Namespace:
            return args

    monkeypatch.setattr(runner_module, "_parser", lambda: Parser())
    monkeypatch.setattr(
        runner_module,
        "run_benchmark",
        lambda _args: (_ for _ in ()).throw(RuntimeError(raw_failure)),
    )

    assert runner_module.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "SELF_IMPROVE_ERROR protocol=self-improve-validation-retry-v5 "
        "type=proposal_json_contract source=proposal_error "
        "detail=compact-v4 proposal is not one complete JSON object\n"
    )
    assert all(
        secret not in captured.err
        for secret in (
            "/Users/operator",
            "private.gguf",
            "top-secret",
            "hunter2",
            "raw-model-fragment",
            "output_bytes",
        )
    )


@pytest.mark.parametrize(
    "model_name",
    ("qwen2.5-coder-1.5b", "smollm2-1.7b"),
)
def test_main_preserves_structurally_bound_v4_for_live_length_failure(
    model_name: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep shared decode-budget failures on retry-v4 through outer wrappers."""
    args = argparse.Namespace(target="unit", validate_only=False)
    raw_failure = RuntimeError(
        "SELF_IMPROVE_LOCAL_PROPOSAL_ERROR "
        "local model exhausted the proposal token budget before completion; "
        f"model={model_name} finish=length completion_tokens=1024 "
        "TOKEN=do-not-publish"
    )
    bound = runner_module._bind_failure_protocol(
        raw_failure,
        "self-improve-compact-proposal-v4",
    )
    assert bound is raw_failure
    outer = RuntimeError("managed runner boundary")
    outer.__cause__ = bound

    class Parser:
        def parse_args(self) -> argparse.Namespace:
            return args

    monkeypatch.setattr(runner_module, "_parser", lambda: Parser())
    monkeypatch.setattr(
        runner_module,
        "run_benchmark",
        lambda _args: (_ for _ in ()).throw(outer),
    )

    assert runner_module.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "SELF_IMPROVE_ERROR protocol=self-improve-validation-retry-v5 "
        "type=decode_budget source=proposal_error "
        "detail=local model exhausted the proposal token budget before completion\n"
    )
    assert model_name not in captured.err
    assert "TOKEN" not in captured.err


def test_finite_live_qwen_budget_feedback_is_typed_bounded_and_secret_free() -> None:
    """Report the stop/3217 overgeneration class without the model-authored text."""
    raw = (
        "native TOKEN=do-not-publish finish=stop completion_tokens=3217\n"
        "SELF_IMPROVE_LOCAL_PROPOSAL_ERROR "
        "compact span new text exceeds 3072 bytes; "
        "received_edits=2 received_content_bytes=>3072 "
        "max_edits=4 max_content_bytes=3072\n"
        "PRIVATE_SOURCE=hunter2"
    )

    feedback = managed_runner_module._validation_retry_feedback(
        raw,
        proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
    )

    assert feedback == (
        "protocol=self-improve-validation-retry-v5 type=edit_content_budget "
        "source=proposal_error detail=compact span new text exceeds 3072 bytes "
        "telemetry=received_edits=2 received_content_bytes=>3072 "
        "max_edits=4 max_content_bytes=3072"
    )
    assert len(feedback.encode("utf-8")) <= 512
    assert all(
        secret not in feedback
        for secret in ("TOKEN", "PRIVATE_SOURCE", "hunter2", "3217")
    )


def test_finite_live_cardinality_feedback_rejects_telemetry_injection() -> None:
    """Expose only the fixed count state from an over-limit compact-v4 shard."""
    valid = (
        "SELF_IMPROVE_LOCAL_PROPOSAL_ERROR "
        "compact proposal edits must contain 1..4 entries; "
        "received_edits=>4 max_edits=4"
    )
    injected = valid + " path=src/private.py z=PASSWORD=hunter2"

    assert managed_runner_module._validation_retry_feedback(
        valid,
        proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
    ).endswith(
        "detail=compact proposal edits must contain 1..4 entries "
        "telemetry=received_edits=>4 max_edits=4"
    )
    redacted = managed_runner_module._validation_retry_feedback(
        injected,
        proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
    )
    assert redacted.endswith("detail=compact proposal edits must contain 1..4 entries")
    assert all(
        secret not in redacted
        for secret in ("src/private.py", "PASSWORD", "hunter2", "telemetry=")
    )


@pytest.mark.parametrize(
    ("detail", "telemetry"),
    (
        (
            "compact span old lines exceed 64; "
            "received_old_lines=>64 max_old_lines=64",
            "received_old_lines=>64 max_old_lines=64",
        ),
        (
            "compact span new lines exceed 64; "
            "received_new_lines=>64 max_new_lines=64",
            "received_new_lines=>64 max_new_lines=64",
        ),
        (
            "compact span changed lines exceed 96; "
            "received_changed_lines=>96 max_changed_lines=96",
            "received_changed_lines=>96 max_changed_lines=96",
        ),
    ),
)
def test_compact_v4_line_budget_feedback_exposes_only_bounded_counts(
    detail: str,
    telemetry: str,
) -> None:
    """Classify pre-apply size rejection without copying path, source, or text."""
    raw = f"SELF_IMPROVE_PARENT_PROPOSAL_ERROR {detail}"

    feedback = managed_runner_module._validation_retry_feedback(
        raw,
        proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
    )

    assert feedback == (
        "protocol=self-improve-validation-retry-v5 type=edit_line_budget "
        f"source=parent_validation detail={detail.partition(';')[0]} "
        f"telemetry={telemetry}"
    )
    assert len(feedback.encode("ascii")) <= 512

    injected = raw + " path=src/private.py z=PASSWORD=hunter2"
    redacted = managed_runner_module._validation_retry_feedback(
        injected,
        proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
    )
    assert "telemetry=" not in redacted
    assert all(
        value not in redacted
        for value in ("src/private.py", "PASSWORD", "hunter2")
    )


@pytest.mark.parametrize(
    ("detail", "feedback_type"),
    (
        ("compact spans must not overlap", "edit_span_overlap"),
        (
            "compact spans must use distinct start coordinates",
            "edit_span_duplicate",
        ),
    ),
)
def test_compact_v4_retry_feedback_distinguishes_overlap_from_duplicate(
    detail: str,
    feedback_type: str,
) -> None:
    """Do not mislabel post-sort ambiguity as model ordering failure."""
    feedback = managed_runner_module._validation_retry_feedback(
        f"SELF_IMPROVE_LOCAL_PROPOSAL_ERROR {detail}",
        proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
    )

    assert feedback == (
        "protocol=self-improve-validation-retry-v5 "
        f"type={feedback_type} source=proposal_error detail={detail}"
    )


def test_main_redacts_finite_live_smollm_stop_framing_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pin stop/3850 and 12,629-byte evidence without publishing raw completion data."""
    args = argparse.Namespace(target="unit", validate_only=False)
    raw = RuntimeError(
        "SELF_IMPROVE_LOCAL_DECODE phase=proposal finish=stop completion_tokens=3850\n"
        "SELF_IMPROVE_LOCAL_PROPOSAL_ERROR compact-v4 proposal is not one complete "
        "JSON object; output_bytes=12629\nPASSWORD=hunter2"
    )
    bound = runner_module._bind_failure_protocol(
        raw,
        comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
    )

    class Parser:
        def parse_args(self) -> argparse.Namespace:
            return args

    monkeypatch.setattr(runner_module, "_parser", lambda: Parser())
    monkeypatch.setattr(
        runner_module,
        "run_benchmark",
        lambda _args: (_ for _ in ()).throw(bound),
    )

    assert runner_module.main() == 2
    captured = capsys.readouterr()
    assert captured.err == (
        "SELF_IMPROVE_ERROR protocol=self-improve-validation-retry-v5 "
        "type=proposal_json_contract source=proposal_error "
        "detail=compact-v4 proposal is not one complete JSON object\n"
    )
    assert all(value not in captured.err for value in ("3850", "12629", "PASSWORD"))


def test_terminal_protocol_classification_never_guesses_from_attempt_digest() -> None:
    """Treat an unbound shared failure as legacy even when text names a v4 digest."""
    raw = RuntimeError(
        "attempt_identity_digest=24363d727bcce62f7bb19c7dad7b3a557"
        "30cbc4376813094af25aabf3e1311d0 "
        "SELF_IMPROVE_LOCAL_PROPOSAL_ERROR "
        "local model exhausted the proposal token budget before completion"
    )

    assert runner_module._public_failure_feedback(raw).startswith(
        "protocol=self-improve-validation-retry-v3 type=decode_budget "
    )


_CATALOG_BASELINE = "eac05dc88c03f14fbd7dd5f4c6d72943609d9e26"
_CATALOG_REFERENCE = "80b381bd87f32487d784964ce93566e3b016b191"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _live_self_improve_command(
    *, through_make: bool, task_file: Path, validate_only: bool
) -> list[str]:
    """Build one bounded process command for the real script or Make boundary."""
    if through_make:
        return [
            "make",
            "test-self-improve",
            "TARGET=entrypoint-contract",
            "SELF_IMPROVE_MODEL_PATH=",
            f"SELF_IMPROVE_BASELINE_REF={_CATALOG_BASELINE}",
            f"SELF_IMPROVE_REFERENCE_REF={_CATALOG_REFERENCE}",
            f"SELF_IMPROVE_TASK_FILE={task_file}",
            "SELF_IMPROVE_MAX_ATTEMPTS=1",
            f"SELF_IMPROVE_VALIDATE_ONLY={int(validate_only)}",
        ]
    command = [
        sys.executable,
        "scripts/run_self_improve_e2e.py",
        "--target",
        "entrypoint-contract",
        "--baseline-ref",
        _CATALOG_BASELINE,
        "--reference-ref",
        _CATALOG_REFERENCE,
        "--task-file",
        str(task_file),
        "--max-attempts",
        "1",
    ]
    if validate_only:
        command.append("--validate-only")
    return command


@pytest.mark.parametrize("through_make", [False, True], ids=("script", "make"))
def test_live_entrypoint_propagates_terminal_failure_exit(
    tmp_path: Path,
    through_make: bool,
) -> None:
    """A handled live failure must cross both process boundaries as exit two."""
    completed = subprocess.run(
        _live_self_improve_command(
            through_make=through_make,
            task_file=tmp_path / "missing-task.json",
            validate_only=False,
        ),
        cwd=_REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 2
    assert (
        "SELF_IMPROVE_ERROR protocol=self-improve-validation-retry-v3 "
        "type=proposal_validation source=worker_tail detail=<redacted>"
        in completed.stderr
    )
    assert str(tmp_path) not in completed.stderr
    assert "Traceback" not in completed.stderr


@pytest.mark.parametrize("through_make", [False, True], ids=("script", "make"))
def test_live_validate_only_entrypoint_stays_zero(through_make: bool) -> None:
    """The tracked synthetic catalog plan remains a successful dry boundary."""
    completed = subprocess.run(
        _live_self_improve_command(
            through_make=through_make,
            task_file=_REPOSITORY_ROOT / "config/self-improve/catalog-truth.json",
            validate_only=True,
        ),
        cwd=_REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0
    assert "SELF_IMPROVE_CODEX_PLAN" in completed.stdout
    assert "SELF_IMPROVE_ERROR" not in completed.stderr


def test_dependency_floor_validate_only_preserves_requested_plan_identity() -> None:
    """Validate-only must return the exact requested task/reference identity."""
    baseline = "df6c84a5da10b11e0e1407b0c5699073b7523b8e"
    reference = "3e8b9a275f70dcd84d75940a64042d3113f0f8fe"
    task_file = _REPOSITORY_ROOT / "config/self-improve/codex-parity-smoke.json"

    result = runner_module.run_benchmark(
        argparse.Namespace(
            target="dependency-floor",
            local_model_path="",
            baseline_ref=baseline,
            reference_ref=reference,
            task_file=str(task_file),
            max_attempts=1,
            merge=False,
            validate_only=True,
        )
    )

    assert result.proposal.baseline_sha == baseline
    assert result.proposal.task_id == "S83.79"
    assert result.proposal.make_commands == TaskSpec.from_path(
        task_file
    ).canonical_make_commands


def test_reference_and_worktree_helpers_publish_exact_identity(tmp_path: Path) -> None:
    class RootRunner:
        def run(
            self,
            target: str,
            variables: dict[str, str] | None = None,
            *,
            timeout: int = 120,
            read_only: bool = False,
        ) -> MakeResult:
            del variables, timeout, read_only
            outputs = {
                "git-show-name-only": "commit " + ("b" * 40) + "\nsrc/example.py\n",
                "git-show-full": "--- a/src/example.py\n+++ b/src/example.py\n-old\n+new\n",
                "agent-worktree-base": f"WORKTREE_PATH={tmp_path}\n",
            }
            return MakeResult(("make", target), 0, outputs[target], "", 0.1)

    root = RootRunner()
    reference = runner_module.build_reference(root, "a" * 40, "b" * 40, 2.0)
    worktree, branch = runner_module.create_worktree(root, "a" * 40, 3)

    assert reference.changed_files == frozenset({"src/example.py"})
    assert reference.changed_lines == 2
    assert worktree == tmp_path.resolve()
    assert branch.startswith("self-improve-codex-")
    assert branch.endswith("-3")


def test_reference_and_worktree_helpers_fail_without_make_evidence() -> None:
    class FailedRunner:
        def __init__(self, *, fail: bool) -> None:
            self.fail = fail

        def run(
            self,
            target: str,
            variables: dict[str, str] | None = None,
            *,
            timeout: int = 120,
            read_only: bool = False,
        ) -> MakeResult:
            del variables, timeout, read_only
            return MakeResult(
                ("make", target),
                1 if self.fail else 0,
                "",
                "failed" if self.fail else "",
                0.1,
            )

    with pytest.raises(RuntimeError, match="cannot inspect"):
        runner_module.build_reference(FailedRunner(fail=True), "a" * 40, "b" * 40, 0.0)
    with pytest.raises(RuntimeError, match="did not publish"):
        runner_module.create_worktree(FailedRunner(fail=False), "a" * 40, 1)


def test_generate_local_proposal_rejects_missing_and_invalid_worker_output(
    tmp_path: Path,
) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")

    class NoOutputRunner:
        def run_observable(
            self,
            target: str,
            variables: dict[str, str],
            *,
            timeout: int,
        ) -> MakeResult:
            del target, variables, timeout
            return MakeResult(("make", "worker"), 0, "", "", 0.1)

    with pytest.raises(FileNotFoundError, match="GGUF"):
        runner_module.generate_local_proposal(
            NoOutputRunner(),
            tmp_path / "missing.gguf",
            "prompt",
        )
    with pytest.raises(ValueError, match="proposal prompt"):
        runner_module.generate_local_proposal(NoOutputRunner(), model, "")
    with pytest.raises(RuntimeError, match="bounded regular file"):
        runner_module.generate_local_proposal(NoOutputRunner(), model, "prompt")


def test_local_exchange_cleans_after_compact_parent_aggregate_rejection(
    tmp_path: Path,
) -> None:
    """Remove the owned exchange when individually bounded strings exceed the total."""
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    exchanges: list[Path] = []

    class AggregateRejectRunner:
        def run_observable(
            self,
            target: str,
            variables: dict[str, str],
            *,
            timeout: int,
        ) -> MakeResult:
            del target, timeout
            exchange = Path(variables["SELF_IMPROVE_PROMPT_FILE"]).parent
            exchanges.append(exchange)
            assert exchange.is_dir()
            raw = json.dumps(
                {
                    "e": [
                        {"s": 2, "n": 1, "z": "PRIVATE_SOURCE=" + "😀" * 385},
                        {"s": 1, "n": 1, "z": "😀" * 385},
                    ]
                }
            )
            comparison_module._decode_compact_span_proposal(
                raw,
                focus_path="src/general_ludd/example.py",
            )
            return MakeResult(("make", "worker"), 0, "", "", 0.1)

    with pytest.raises(ValueError, match="new text exceeds 3072 bytes") as captured:
        runner_module.generate_local_proposal(AggregateRejectRunner(), model, "prompt")

    assert len(exchanges) == 1
    assert not exchanges[0].exists()
    assert "received_content_bytes=>3072" in str(captured.value)
    assert "PRIVATE_SOURCE" not in str(captured.value)


def test_evaluate_attempt_stops_on_first_failed_command_and_cleans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative = "docs/example.md"
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_text("Body  \n", encoding="utf-8")
    task = TaskSpec(
        task_id="S83.133",
        objective="Remove trailing whitespace.",
        canonical_make_commands=("make test-files TESTFILES=tests/unit/test_example.py",),
    )
    reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({relative}),
        test_files=frozenset(),
        changed_lines=2,
        elapsed_seconds=1.0,
    )
    proposal = ProposalManifest.from_json(
        json.dumps(
            {
                "schema_version": 1,
                "baseline_sha": "a" * 40,
                "task_id": "S83.133",
                "edits": [
                    {
                        "operation": "replace",
                        "path": relative,
                        "old_text": "Body  \n",
                        "new_text": "Body\n",
                    }
                ],
                "tests": ["tests/unit/test_example.py"],
                "make_commands": list(task.canonical_make_commands),
                "commit_message": "fix: failure evidence",
            }
        )
    )

    class FailedCandidate:
        def run_command(self, command: str, *, timeout: int = 900) -> MakeResult:
            del timeout
            return MakeResult(tuple(command.split()), 1, "E failure", "", 0.1)

        def run(
            self,
            target_name: str,
            variables: dict[str, str] | None = None,
            *,
            timeout: int = 120,
            read_only: bool = False,
        ) -> MakeResult:
            del variables, timeout, read_only
            return MakeResult(("make", target_name), 0, "", "", 0.1)

    class RootRunner:
        def __init__(self) -> None:
            self.targets: list[str] = []

        def run(
            self,
            target_name: str,
            variables: dict[str, str] | None = None,
            *,
            timeout: int = 120,
            read_only: bool = False,
        ) -> MakeResult:
            del variables, timeout, read_only
            self.targets.append(target_name)
            return MakeResult(("make", target_name), 0, "", "", 0.1)

    root = RootRunner()
    monkeypatch.setattr(runner_module, "create_worktree", lambda *_args: (tmp_path, "candidate"))
    monkeypatch.setattr(runner_module, "MakeRunner", lambda _root: FailedCandidate())

    result = runner_module.evaluate_attempt(
        root,
        task,
        reference,
        runner_module.PlanBoundProposal(proposal, "c" * 64),
        1,
        expected_attempt_identity_digest="c" * 64,
        merge=False,
    )

    assert result.evidence.tests_passed is False
    assert json.loads(result.diagnostics)["failure_class"] == "make_failed"
    assert "E failure" not in result.diagnostics
    assert root.targets == ["agent-cleanup"]


def test_live_qwen_three_b_score_sixty_emits_safe_typed_evaluation_diagnosis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Turn the exact two-file/five-line fast failure into actionable safe evidence."""
    source_path = "src/general_ludd/catalog_truth.py"
    test_path = "tests/unit/test_catalog_truth.py"
    source = tmp_path / source_path
    test = tmp_path / test_path
    source.parent.mkdir(parents=True)
    test.parent.mkdir(parents=True)
    source.write_text("value = 0\n", encoding="utf-8")
    test.write_text("assert False\n", encoding="utf-8")
    command = "make test-specific TESTFILE=tests/unit/test_catalog_truth.py"
    task = TaskSpec(
        task_id="S83.134",
        objective="Repair catalog truth in the exact source and focused test.",
        canonical_make_commands=(command,),
    )
    reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({source_path, test_path}),
        test_files=frozenset({test_path}),
        changed_lines=5,
        elapsed_seconds=300.0,
    )
    model_text = "MODEL_Z_NEVER_EMIT"
    proposal = ProposalManifest.from_json(
        json.dumps(
            {
                "schema_version": 1,
                "baseline_sha": "a" * 40,
                "task_id": "S83.134",
                "edits": [
                    {
                        "operation": "replace",
                        "path": source_path,
                        "old_text": "value = 0\n",
                        "new_text": f"value = 1  # {model_text}\nextra = 2\n",
                    },
                    {
                        "operation": "replace",
                        "path": test_path,
                        "old_text": "assert False\n",
                        "new_text": "assert True\n",
                    },
                ],
                "tests": [test_path],
                "make_commands": [command],
                "commit_message": "fix: repair catalog truth",
            }
        )
    )
    leaked_path = "/Users/private/catalog_truth.py"
    leaked_secret = "AUTH_TOKEN=hunter2"

    class FailedCandidate:
        def run_command(self, approved_command: str, *, timeout: int = 900) -> MakeResult:
            del timeout
            assert approved_command == command
            return MakeResult(
                tuple(approved_command.split()),
                1,
                f"failed near {leaked_path}",
                leaked_secret,
                1.0,
            )

        def run(
            self,
            target_name: str,
            variables: dict[str, str] | None = None,
            *,
            timeout: int = 120,
            read_only: bool = False,
        ) -> MakeResult:
            del variables, timeout, read_only
            return MakeResult(("make", target_name), 0, "", "", 0.1)

    class RootRunner:
        def __init__(self) -> None:
            self.targets: list[str] = []

        def run(
            self,
            target_name: str,
            variables: dict[str, str] | None = None,
            *,
            timeout: int = 120,
            read_only: bool = False,
        ) -> MakeResult:
            del variables, timeout, read_only
            self.targets.append(target_name)
            return MakeResult(
                ("make", target_name),
                0,
                leaked_path,
                leaked_secret,
                0.1,
            )

    root = RootRunner()
    progress: list[str] = []
    monkeypatch.setattr(
        runner_module,
        "create_worktree",
        lambda *_args: (tmp_path, "candidate"),
    )
    monkeypatch.setattr(runner_module, "MakeRunner", lambda _root: FailedCandidate())

    result = runner_module.evaluate_attempt(
        root,
        task,
        reference,
        runner_module.PlanBoundProposal(proposal, "c" * 64),
        1,
        expected_attempt_identity_digest="c" * 64,
        merge=False,
        progress_sink=progress.append,
    )

    diagnosis = json.loads(result.diagnostics)
    assert result.comparison.score == 60.0
    assert result.evidence.changed_lines == 5
    assert diagnosis == {
        "category": "none",
        "column": 0,
        "command_kind": "approved_make",
        "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
        "duration_ms": 1000,
        "exit_code": 1,
        "failure_class": "make_failed",
        "finish_reason": "unknown",
        "finished": True,
        "hypothesis": "approved evaluation failed; correct only the typed phase",
        "line": 0,
        "path_sha256": "",
        "phase": "approved_make",
        "protocol": "self-improve-evaluation-diagnosis-v2",
        "schema_version": 3,
    }
    assert result.diagnostics == json.dumps(
        diagnosis,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert len(result.diagnostics.encode("ascii")) <= 768
    persisted = ManagedSelfImproveResultArtifact.from_run_result(
        ManagedRunResult(
            final_result=replace(
                result,
                patch_equivalence="evaluation-not-committed",
            ),
            attempts=1,
            plan_identity_digest="d" * 64,
            attempted_model_ids=("qwen2.5-coder-3b",),
            outcome_record_ids=(),
        )
    )
    assert persisted.diagnostics == result.diagnostics
    events = "\n".join(progress)
    assert "phase=approved_make" in events
    assert "phase=cleanup" in events
    assert "duration_ms=1000" in events
    assert root.targets == ["agent-cleanup"]
    retry = build_retry_prompt(
        "Repair catalog truth.",
        result.comparison,
        diagnostics=result.diagnostics,
    )
    for forbidden in (
        model_text,
        source_path,
        test_path,
        leaked_path,
        leaked_secret,
        "hunter2",
    ):
        assert forbidden not in events
        assert forbidden not in result.diagnostics
        assert forbidden not in persisted.diagnostics
        assert forbidden not in retry


def test_evaluation_apply_exception_emits_typed_failure_and_still_cleans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An apply exception must retain cleanup and never publish exception content."""
    secret = "MODEL_Z_OR_SECRET=hunter2"
    progress: list[str] = []

    class RootRunner:
        def __init__(self) -> None:
            self.targets: list[str] = []

        def run(
            self,
            target_name: str,
            variables: dict[str, str] | None = None,
            *,
            timeout: int = 120,
            read_only: bool = False,
        ) -> MakeResult:
            del variables, timeout, read_only
            self.targets.append(target_name)
            return MakeResult(("make", target_name), 0, "", secret, 0.1)

    root = RootRunner()
    monkeypatch.setattr(
        runner_module,
        "create_worktree",
        lambda *_args: (tmp_path, "candidate"),
    )
    monkeypatch.setattr(
        runner_module,
        "apply_proposal",
        lambda *_args: (_ for _ in ()).throw(RuntimeError(secret)),
    )

    with pytest.raises(RuntimeError, match="MODEL_Z_OR_SECRET"):
        runner_module.evaluate_attempt(
            root,
            TaskSpec(
                task_id="S83.133",
                objective="Repair exact Python code.",
                canonical_make_commands=(
                    "make test-files TESTFILES=tests/unit/test_example.py",
                ),
            ),
            CodexReference(
                baseline_sha="a" * 40,
                reference_sha="b" * 40,
                changed_files=frozenset(
                    {
                        "src/general_ludd/example.py",
                        "tests/unit/test_example.py",
                    }
                ),
                test_files=frozenset({"tests/unit/test_example.py"}),
                changed_lines=4,
                elapsed_seconds=1.0,
            ),
            runner_module.PlanBoundProposal(_manifest(), "c" * 64),
            1,
            expected_attempt_identity_digest="c" * 64,
            merge=False,
            progress_sink=progress.append,
        )

    assert root.targets == ["agent-cleanup"]
    assert [event.split(" phase=", 1)[1].split()[0] for event in progress] == [
        "apply",
        "cleanup",
    ]
    assert "failure=apply_failed" in progress[0]
    assert "failure=none" in progress[1]
    assert secret not in "\n".join(progress)


def test_evaluation_runner_factory_exception_still_cleans_created_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Construction failure after worktree creation must not bypass cleanup."""
    cleanup_targets: list[str] = []
    progress: list[str] = []

    class RootRunner:
        def run(
            self,
            target_name: str,
            variables: dict[str, str] | None = None,
            *,
            timeout: int = 120,
            read_only: bool = False,
        ) -> MakeResult:
            del variables, timeout, read_only
            cleanup_targets.append(target_name)
            return MakeResult(("make", target_name), 0, "", "", 0.1)

    monkeypatch.setattr(
        runner_module,
        "create_worktree",
        lambda *_args: (tmp_path, "candidate"),
    )

    with pytest.raises(RuntimeError, match="factory failed"):
        runner_module.evaluate_attempt(
            RootRunner(),
            TaskSpec(
                task_id="S83.133",
                objective="Repair exact Python code.",
                canonical_make_commands=(
                    "make test-files TESTFILES=tests/unit/test_example.py",
                ),
            ),
            CodexReference(
                baseline_sha="a" * 40,
                reference_sha="b" * 40,
                changed_files=frozenset(
                    {
                        "src/general_ludd/example.py",
                        "tests/unit/test_example.py",
                    }
                ),
                test_files=frozenset({"tests/unit/test_example.py"}),
                changed_lines=4,
                elapsed_seconds=1.0,
            ),
            runner_module.PlanBoundProposal(_manifest(), "c" * 64),
            1,
            expected_attempt_identity_digest="c" * 64,
            merge=False,
            make_runner_factory=lambda _root: (_ for _ in ()).throw(
                RuntimeError("factory failed")
            ),
            progress_sink=progress.append,
        )

    assert cleanup_targets == ["agent-cleanup"]
    assert len(progress) == 1
    assert "phase=cleanup" in progress[0]
    assert "failure=none" in progress[0]


@pytest.mark.parametrize(
    "replacement",
    [
        "smollm2-135M)\n",
        (
            "GLUDD_SELF_IMPROVE_FOCUS_PATH="
            "tests/unit/test_e2e_model_configs.py        assert len(models) == 1\n"
        ),
    ],
)
def test_parent_syntax_preflight_rejects_exact_live_classes_with_safe_feedback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    """Reject malformed Python before pytest without echoing model-authored text."""
    relative = "tests/unit/test_e2e_model_configs.py"
    baseline = "def test_catalog() -> None:\n    assert True\n"
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_text(baseline, encoding="utf-8")
    task = TaskSpec(
        task_id="S83.134",
        objective="Repair the catalog mappings and their focused tests.",
        canonical_make_commands=(
            "make test-specific TESTFILE=tests/unit/test_e2e_model_configs.py",
        ),
    )
    reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({relative}),
        test_files=frozenset({relative}),
        changed_lines=2,
        elapsed_seconds=1.0,
    )
    proposal = ProposalManifest.from_json(
        json.dumps(
            {
                "schema_version": 1,
                "baseline_sha": "a" * 40,
                "task_id": "S83.134",
                "edits": [
                    {
                        "operation": "replace",
                        "path": relative,
                        "old_text": baseline,
                        "new_text": replacement,
                    }
                ],
                "tests": [relative],
                "make_commands": list(task.canonical_make_commands),
                "commit_message": "fix: repair catalog truth",
            }
        )
    )

    class CandidateRunner:
        def run_command(self, _command: str, *, timeout: int = 900) -> MakeResult:
            del timeout
            raise AssertionError("syntax-invalid Python must not reach a Make command")

    class RootRunner:
        def __init__(self) -> None:
            self.targets: list[str] = []

        def run(
            self,
            target_name: str,
            variables: dict[str, str] | None = None,
            *,
            timeout: int = 120,
            read_only: bool = False,
        ) -> MakeResult:
            del variables, timeout, read_only
            self.targets.append(target_name)
            return MakeResult(("make", target_name), 0, "", "", 0.1)

    root = RootRunner()
    monkeypatch.setattr(
        runner_module,
        "create_worktree",
        lambda *_args: (tmp_path, "candidate"),
    )
    monkeypatch.setattr(runner_module, "MakeRunner", lambda _root: CandidateRunner())

    result = runner_module.evaluate_attempt(
        root,
        task,
        reference,
        runner_module.PlanBoundProposal(proposal, "c" * 64),
        1,
        expected_attempt_identity_digest="c" * 64,
        merge=False,
    )

    assert result.evidence.tests_passed is False
    diagnosis = json.loads(result.diagnostics)
    assert diagnosis["phase"] == "syntax_preflight"
    assert diagnosis["failure_class"] == "python_syntax"
    assert diagnosis["category"] == "python_syntax"
    assert diagnosis["path_sha256"] == hashlib.sha256(relative.encode()).hexdigest()
    assert diagnosis["line"] == 1
    assert isinstance(diagnosis["column"], int) and diagnosis["column"] > 0
    assert len(result.diagnostics.encode("ascii")) <= 768
    assert replacement.strip() not in result.diagnostics
    assert relative not in result.diagnostics
    retry = build_retry_prompt(
        "Repair the catalog mappings.",
        result.comparison,
        diagnostics=result.diagnostics,
    )
    assert '"failure_class":"python_syntax"' in retry
    assert replacement.strip() not in retry
    assert relative not in retry
    assert root.targets == ["agent-cleanup"]


def test_live_v4_logical_line_materialization_prevents_syntax_concatenation(
    tmp_path: Path,
) -> None:
    """Supply an omitted interior LF before syntax validation sees the candidate."""
    relative = "src/general_ludd/example.py"
    baseline = "def enabled() -> bool:\n    value = False\n    return value\n"
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_text(baseline, encoding="utf-8")
    proposal = comparison_module.CompactSpanProposal(
        focus_path=relative,
        edits=(
            comparison_module.CompactLineSpan(
                start_line=2,
                old_line_count=1,
                new_text="    value = True",
            ),
        ),
    )
    contract = comparison_module.ProposalContract(
        baseline_sha="a" * 40,
        task_id="S83.134",
        tests=("tests/unit/test_example.py",),
        make_commands=("make test-files TESTFILES=tests/unit/test_example.py",),
        proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
    )

    manifest = comparison_module.expand_compact_span_proposals(
        (proposal,),
        contract=contract,
        expected_path_groups=((relative,),),
        expected_baseline_files={relative: baseline},
        expected_editable_ranges=(((1, 4),),),
    )
    changed_lines = apply_proposal(tmp_path, manifest)

    assert changed_lines == 2
    assert target.read_text(encoding="utf-8") == (
        "def enabled() -> bool:\n    value = True\n    return value\n"
    )
    assert runner_module._python_syntax_preflight(tmp_path, (relative,)) is None


def test_parent_syntax_preflight_is_tokenize_aware_and_skips_non_python(
    tmp_path: Path,
) -> None:
    """Honor Python coding cookies and ignore unrelated file types."""
    python_path = tmp_path / "src/example.py"
    python_path.parent.mkdir(parents=True)
    python_path.write_bytes(b"# coding: latin-1\nname = 'caf\xe9'\n")
    text_path = tmp_path / "docs/example.txt"
    text_path.parent.mkdir(parents=True)
    text_path.write_text("not Python )", encoding="utf-8")

    assert (
        runner_module._python_syntax_preflight(
            tmp_path,
            ("docs/example.txt", "src/example.py"),
        )
        is None
    )


def test_terminate_process_group_escalates_and_tolerates_gone_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[int] = []

    class Process:
        pid = 123

        def __init__(self) -> None:
            self.waits = 0

        def wait(self, timeout: float | None = None) -> int:
            assert timeout == 5
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("worker", timeout)
            return 0

    monkeypatch.setattr(
        os,
        "killpg",
        lambda _pid, sent_signal: signals.append(sent_signal),
    )
    runner_module._terminate_process_group(Process())
    assert signals == [signal.SIGTERM, signal.SIGKILL]

    def gone(_pid: int, _signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "killpg", gone)
    runner_module._terminate_process_group(Process())


def test_task_spec_file_and_value_boundaries_are_fail_closed(tmp_path: Path) -> None:
    task_file = tmp_path / "task.json"
    with pytest.raises(FileNotFoundError, match="not readable"):
        TaskSpec.from_path(task_file)

    task_file.write_bytes(b"x" * 262_145)
    with pytest.raises(ValueError, match="exceeds"):
        TaskSpec.from_path(task_file)

    task_file.write_bytes(bytes([255]))
    with pytest.raises(ValueError, match="UTF-8 JSON"):
        TaskSpec.from_path(task_file)

    task_file.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        TaskSpec.from_path(task_file)

    base: dict[str, object] = {
        "task_id": "S83.133",
        "objective": "repair",
        "canonical_make_commands": ["make test-files"],
    }
    cases: list[tuple[dict[str, object], str]] = [
        ({"task_id": "invalid"}, "task_id"),
        ({"objective": "  "}, "objective"),
        ({"objective": "x" * 65_537}, "objective exceeds"),
        ({"canonical_make_commands": []}, "1..32"),
        ({"canonical_make_commands": ["make test-files"] * 33}, "1..32"),
        ({"reference_elapsed_seconds": -1}, "non-negative"),
    ]
    for updates, match in cases:
        payload = dict(base)
        payload.update(updates)
        task_file.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError, match=match):
            TaskSpec.from_path(task_file)


def test_mechanical_change_builder_rejects_ambiguous_or_incomplete_diffs() -> None:
    task = TaskSpec(
        task_id="S83.133",
        objective="Remove trailing whitespace.",
        canonical_make_commands=(
            "make test-files TESTFILES=tests/unit/test_example.py",
        ),
    )
    reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({"docs/example.md"}),
        test_files=frozenset(),
        changed_lines=2,
        elapsed_seconds=1.0,
    )
    with pytest.raises(ValueError, match="exact Codex scope"):
        proposal_from_mechanical_changes(task, reference, {}, {})
    with pytest.raises(ValueError, match="bounded replacements"):
        proposal_from_mechanical_changes(
            task,
            reference,
            {"docs/example.md": "Body\n"},
            {"docs/example.md": "Body\nAdded\n"},
        )
    with pytest.raises(ValueError, match="did not change"):
        proposal_from_mechanical_changes(
            task,
            reference,
            {"docs/example.md": "Body\n"},
            {"docs/example.md": "Body\n"},
        )
    with pytest.raises(ValueError, match="not unique"):
        proposal_from_mechanical_changes(
            task,
            reference,
            {"docs/example.md": "Body  \nKeep\nBody  \n"},
            {"docs/example.md": "Body\nKeep\nBody  \n"},
        )


def test_generate_mechanical_proposal_handles_no_route_missing_input_and_tool_error(
    tmp_path: Path,
) -> None:
    python_task = TaskSpec(
        task_id="S83.133",
        objective="Repair parser.",
        canonical_make_commands=("make test-files TESTFILES=tests/unit/test_parser.py",),
    )
    python_reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({"src/parser.py"}),
        test_files=frozenset({"tests/unit/test_parser.py"}),
        changed_lines=2,
        elapsed_seconds=1.0,
    )

    class FailedRunner:
        def run_command(self, command: str, *, timeout: int = 900) -> MakeResult:
            del command, timeout
            return MakeResult(("make", "fix-docs-drift"), 1, "", "tool failed", 0.1)

    assert (
        generate_mechanical_proposal(
            FailedRunner(),
            python_task,
            python_reference,
            tmp_path,
        )
        is None
    )

    docs_task = TaskSpec(
        task_id="S83.133",
        objective="Remove trailing whitespace.",
        canonical_make_commands=(
            "make test-files TESTFILES=tests/unit/test_example.py",
        ),
    )
    docs_reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({"docs/example.md"}),
        test_files=frozenset(),
        changed_lines=2,
        elapsed_seconds=1.0,
    )
    with pytest.raises(ValueError, match="bounded regular file"):
        generate_mechanical_proposal(FailedRunner(), docs_task, docs_reference, tmp_path)

    document = tmp_path / "docs/example.md"
    document.parent.mkdir(parents=True)
    document.write_text("Body  \n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="tool failed"):
        generate_mechanical_proposal(FailedRunner(), docs_task, docs_reference, tmp_path)


def test_run_benchmark_rejects_reference_over_decode_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_file = _benchmark_task_file(tmp_path)
    reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({"src/example.py"}),
        test_files=frozenset({"tests/unit/test_example.py"}),
        changed_lines=1000,
        elapsed_seconds=1.0,
    )

    class RootRunner:
        def __init__(self, _root: Path) -> None:
            pass

    monkeypatch.setattr(runner_module, "MakeRunner", RootRunner)
    monkeypatch.setattr(runner_module, "build_reference", lambda *_args: reference)

    with pytest.raises(ValueError, match="exceeds the local decode budget"):
        runner_module.run_benchmark(_benchmark_args(task_file))
