"""Runner contracts for automatic self-improvement model ownership."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
import scripts.run_self_improve_e2e as runner
from scripts.run_self_improve_e2e import AttemptResult, MakeResult

from general_ludd.self_improve.codex_comparison import (
    CandidateEvidence,
    CodexReference,
    ComparisonResult,
    ProposalManifest,
)


def _task_file(tmp_path: Path) -> Path:
    path = tmp_path / "task.json"
    path.write_text(
        json.dumps(
            {
                "task_id": "S83.133",
                "objective": "Repair Python code safely.",
                "canonical_make_commands": [
                    "make test-files TESTFILES=tests/unit/test_example.py"
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _args(task_file: Path, model_path: str = "") -> argparse.Namespace:
    return argparse.Namespace(
        target="unit",
        local_model_path=model_path,
        baseline_ref="a" * 40,
        reference_ref="b" * 40,
        task_file=str(task_file),
        max_attempts=2,
        merge=False,
        validate_only=False,
    )


def _reference() -> CodexReference:
    return CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset(
            {"src/general_ludd/example.py", "tests/unit/test_example.py"}
        ),
        test_files=frozenset({"tests/unit/test_example.py"}),
        changed_lines=4,
        elapsed_seconds=10.0,
    )


def _proposal() -> ProposalManifest:
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
                        "new_text": "return 1",
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
                    "make test-files TESTFILES=tests/unit/test_example.py"
                ],
                "commit_message": "fix: local proposal",
            }
        )
    )


def _result() -> AttemptResult:
    proposal = _proposal()
    return AttemptResult(
        comparison=ComparisonResult(
            accepted=True,
            score=100.0,
            blockers=(),
            changed_file_precision=1.0,
            changed_file_recall=1.0,
        ),
        evidence=CandidateEvidence(
            changed_files=frozenset(
                {"src/general_ludd/example.py", "tests/unit/test_example.py"}
            ),
            tests_passed=True,
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
        patch_equivalence="equivalent",
        proposal=proposal,
        diagnostics="",
    )


class _RootRunner:
    def __init__(self, _root: Path) -> None:
        self.targets: list[str] = []

    def run(
        self,
        target: str,
        variables: dict[str, str] | None = None,
        *,
        timeout: int = 120,
        read_only: bool = False,
    ) -> MakeResult:
        del variables, timeout, read_only
        self.targets.append(target)
        return MakeResult(("make", target), 0, "", "", 0.1)


class _LeaseManager:
    acquired: ClassVar[list[tuple[str, Path | None]]] = []
    released: ClassVar[int] = 0
    model_path: ClassVar[Path]

    def __init__(self) -> None:
        pass

    @contextmanager
    def acquire(
        self,
        task_description: str,
        *,
        explicit_path: Path | None = None,
    ) -> Iterator[SimpleNamespace]:
        type(self).acquired.append((task_description, explicit_path))
        try:
            yield SimpleNamespace(
                path=type(self).model_path,
                model_id="test-model",
                resolved_revision="a" * 40,
                artifact_sha256="b" * 64,
                source="managed" if explicit_path is None else "explicit",
            )
        finally:
            type(self).released += 1


def _wire_common(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _LeaseManager.acquired = []
    _LeaseManager.released = 0
    _LeaseManager.model_path = tmp_path / "model.gguf"
    _LeaseManager.model_path.write_bytes(b"GGUF model")
    monkeypatch.setattr(runner, "MakeRunner", _RootRunner)
    monkeypatch.setattr(runner, "build_reference", lambda *_args: _reference())
    monkeypatch.setattr(
        runner,
        "create_worktree",
        lambda *_args: (tmp_path, "context"),
    )
    monkeypatch.setattr(runner, "build_prompt", lambda *_args: "bounded prompt")
    monkeypatch.setattr(runner, "generate_mechanical_proposal", lambda *_args: None)
    monkeypatch.setattr(runner, "ModelLeaseManager", _LeaseManager)


def test_runner_acquires_one_managed_model_lazily_across_retries_and_releases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_common(tmp_path, monkeypatch)
    attempts = 0

    def propose(_root: object, path: Path, _prompt: str) -> ProposalManifest:
        nonlocal attempts
        attempts += 1
        assert path == _LeaseManager.model_path
        if attempts == 1:
            raise ValueError("invalid JSON")
        return _proposal()

    monkeypatch.setattr(runner, "generate_local_proposal", propose)
    monkeypatch.setattr(runner, "evaluate_attempt", lambda *_args, **_kwargs: _result())

    result = runner.run_benchmark(_args(_task_file(tmp_path)))

    assert result.comparison.accepted
    assert _LeaseManager.acquired == [("Repair Python code safely.", None)]
    assert _LeaseManager.released == 1
    assert attempts == 2


def test_explicit_model_path_remains_an_operator_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_common(tmp_path, monkeypatch)
    explicit = tmp_path / "operator.gguf"
    explicit.write_bytes(b"GGUF override")
    monkeypatch.setattr(runner, "generate_local_proposal", lambda *_args: _proposal())
    monkeypatch.setattr(runner, "evaluate_attempt", lambda *_args, **_kwargs: _result())

    runner.run_benchmark(_args(_task_file(tmp_path), str(explicit)))

    assert _LeaseManager.acquired == [
        ("Repair Python code safely.", explicit)
    ]
    assert _LeaseManager.released == 1


def test_model_lease_releases_when_proposal_worker_is_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_common(tmp_path, monkeypatch)

    def cancelled(*_args: object) -> ProposalManifest:
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "generate_local_proposal", cancelled)

    with pytest.raises(KeyboardInterrupt):
        runner.run_benchmark(_args(_task_file(tmp_path)))

    assert _LeaseManager.released == 1


def test_mechanical_route_never_acquires_or_downloads_a_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_common(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runner,
        "generate_mechanical_proposal",
        lambda *_args: _proposal(),
    )
    monkeypatch.setattr(runner, "evaluate_attempt", lambda *_args, **_kwargs: _result())
    monkeypatch.setattr(
        runner,
        "ModelLeaseManager",
        lambda: pytest.fail("mechanical route must not acquire a model"),
    )

    result = runner.run_benchmark(_args(_task_file(tmp_path)))

    assert result.comparison.accepted


def test_cli_model_path_is_optional_and_validate_only_reports_auto() -> None:
    parsed = runner._parser().parse_args(
        [
            "--target",
            "unit",
            "--baseline-ref",
            "a" * 40,
            "--reference-ref",
            "b" * 40,
            "--task-file",
            "task.json",
            "--validate-only",
        ]
    )

    assert parsed.local_model_path == ""
