"""Tests split from :mod:`tests.unit.test_self_improve_codex_runner` by coherent behavior."""

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

import pytest

import general_ludd.self_improve.codex_comparison as comparison_module
import general_ludd.self_improve.managed_runner as managed_runner_module
import general_ludd.self_improve.runtime as runner_module
from general_ludd.self_improve.codex_comparison import (
    CodexReference,
    ProposalManifest,
    build_retry_prompt,
)
from general_ludd.self_improve.managed_runner import ManagedRunResult
from general_ludd.self_improve.result_artifact import ManagedSelfImproveResultArtifact
from general_ludd.self_improve.runtime import (
    MakeResult,
    TaskSpec,
    apply_proposal,
    generate_mechanical_proposal,
    proposal_from_mechanical_changes,
)
from tests.unit.test_self_improve_codex_runner import (
    _benchmark_args,
    _benchmark_task_file,
    _manifest,
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


def test_local_worker_validation_failure_is_typed_without_raw_output(
    tmp_path: Path,
) -> None:
    """Treat model protocol violations as quality evidence, not infrastructure."""
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")

    class ValidationRejectRunner:
        def run_observable(
            self,
            target: str,
            variables: dict[str, str],
            *,
            timeout: int,
        ) -> MakeResult:
            del target, variables, timeout
            return MakeResult(
                ("make", "worker"),
                2,
                (
                    "PRIVATE_SOURCE=do-not-retain\n"
                    "SELF_IMPROVE_LOCAL_PROPOSAL_ERROR "
                    "compact span new lines exceed 64; received_new_lines=>64 "
                    "max_new_lines=64\n"
                    "PASSWORD=hunter2\n"
                ),
                "",
                0.1,
            )

    with pytest.raises(ValueError) as raised:
        runner_module.generate_local_proposal(
            ValidationRejectRunner(),
            model,
            "prompt",
        )

    diagnostic = str(raised.value)
    assert "type=edit_line_budget" in diagnostic
    assert "source=proposal_error" in diagnostic
    assert "detail=compact span new lines exceed 64" in diagnostic
    assert "PRIVATE_SOURCE" not in diagnostic
    assert "do-not-retain" not in diagnostic
    assert "PASSWORD" not in diagnostic
    assert "hunter2" not in diagnostic


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
