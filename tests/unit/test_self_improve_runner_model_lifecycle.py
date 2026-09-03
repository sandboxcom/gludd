"""Runner contracts for automatic self-improvement model ownership."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import pytest

import general_ludd.self_improve.codex_comparison as comparison_module
import general_ludd.self_improve.runtime as runtime_module
from general_ludd.local_model import LocalModelConfig, get_model
from general_ludd.self_improve.codex_comparison import (
    CandidateEvidence,
    CodexReference,
    ComparisonResult,
    ProposalManifest,
)
from general_ludd.self_improve.managed_runner import PromptPlan, PromptShard
from general_ludd.self_improve.model_candidate_planner import PlannedModelCandidate
from general_ludd.self_improve.model_lifecycle import (
    ModelAcquisitionEvent,
    ModelAcquisitionPhase,
    ModelArtifactIdentity,
)
from general_ludd.self_improve.runtime import AttemptResult, MakeResult, PlanBoundProposal

runner = cast(Any, runtime_module)


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
        attempt_identity_digest=runner._attempt_identity_digest("bounded prompt"),
    )


def test_approved_result_plan_identity_drift_cannot_reach_outcome_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale approval must fail closed before capability evidence is recorded."""
    _wire_common(tmp_path, monkeypatch)
    recorded: list[str] = []
    monkeypatch.setattr(runner, "generate_local_proposal", lambda *_args: _proposal())
    monkeypatch.setattr(
        runner,
        "evaluate_attempt",
        lambda *_args, **_kwargs: replace(
            _result(),
            attempt_identity_digest="f" * 64,
        ),
    )
    monkeypatch.setattr(
        runner,
        "record_self_improve_outcome",
        lambda *_args, **kwargs: recorded.append(
            str(kwargs["attempt_identity_digest"])
        ),
    )

    with pytest.raises(ValueError, match="approved result plan identity drifted"):
        runner.run_benchmark(_args(_task_file(tmp_path)))

    assert recorded == []


def test_plan_identity_is_single_source_through_execution_and_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proposal, execution, approval, and outcome share the exact plan digest."""
    _wire_common(tmp_path, monkeypatch)
    observed: list[tuple[str, str]] = []
    monkeypatch.setattr(runner, "generate_local_proposal", lambda *_args: _proposal())

    def evaluate(
        _root: object,
        _task: object,
        _reference: object,
        bound: PlanBoundProposal,
        _attempt: int,
        *,
        expected_attempt_identity_digest: str,
        merge: bool,
        make_runner_factory: object | None = None,
        progress_sink: Callable[[str], None] | None = None,
    ) -> AttemptResult:
        del make_runner_factory
        assert merge is False
        assert progress_sink is not None
        observed.append(("proposal", bound.attempt_identity_digest))
        observed.append(("execution", expected_attempt_identity_digest))
        return replace(
            _result(),
            proposal=bound.proposal,
            attempt_identity_digest=expected_attempt_identity_digest,
        )

    def record(
        _store: object,
        *,
        task_text: str,
        candidate: PlannedModelCandidate,
        attempt_identity_digest: str,
        succeeded: bool,
    ) -> int:
        assert task_text == "Repair Python code safely."
        assert candidate.config.name == "qwen2.5-coder-0.5b"
        assert succeeded is True
        observed.append(("outcome", attempt_identity_digest))
        return 1

    monkeypatch.setattr(runner, "evaluate_attempt", evaluate)
    monkeypatch.setattr(runner, "record_self_improve_outcome", record)

    result = runner.run_benchmark(_args(_task_file(tmp_path)))

    expected = runner._attempt_identity_digest("bounded prompt")
    assert result.attempt_identity_digest == expected
    assert observed == [
        ("proposal", expected),
        ("execution", expected),
        ("outcome", expected),
    ]


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


class _ReservationHandle:
    def __init__(
        self,
        path: Path,
        transitions: list[str] | None = None,
    ) -> None:
        self.path = path
        self._transitions = transitions

    def mark_eligible(self, identity: ModelArtifactIdentity) -> None:
        if self._transitions is not None:
            self._transitions.append(f"eligible:{identity.model_id}")

    def mark_failed(self, identity: ModelArtifactIdentity) -> None:
        if self._transitions is not None:
            self._transitions.append(f"failed:{identity.model_id}")


class _LeaseManager:
    acquired: ClassVar[list[tuple[str, Path | None]]] = []
    released: ClassVar[int] = 0
    model_path: ClassVar[Path]
    cache_root: ClassVar[Path]

    def __init__(
        self,
        *,
        event_sink: Callable[[ModelAcquisitionEvent], None] | None = None,
    ) -> None:
        assert callable(event_sink)

    def resolve_revision(self, _repo_id: str) -> str:
        return "a" * 40

    def owned_identities_for_model_ids(
        self,
        _model_ids: tuple[str, ...],
    ) -> tuple[ModelArtifactIdentity, ...]:
        return ()

    @contextmanager
    def reserve_plan(
        self,
        _identities: tuple[ModelArtifactIdentity, ...],
        *,
        failure_hints: tuple[ModelArtifactIdentity, ...] = (),
    ) -> Iterator[_ReservationHandle]:
        del failure_hints
        yield _ReservationHandle(type(self).cache_root / "reservation.json")

    @contextmanager
    def acquire(
        self,
        task_description: str,
        *,
        explicit_path: Path | None = None,
        model_config: object | None = None,
        resolved_revision: str | None = None,
    ) -> Iterator[SimpleNamespace]:
        type(self).acquired.append((task_description, explicit_path))
        model_id = getattr(model_config, "name", "test-model")
        lease_path = type(self).cache_root / f"{model_id}.lease"
        lease_path.touch()
        try:
            yield SimpleNamespace(
                path=type(self).model_path,
                model_id=model_id,
                resolved_revision=resolved_revision or "a" * 40,
                artifact_sha256="b" * 64,
                source="managed" if explicit_path is None else "explicit",
                lease_path=lease_path,
            )
        finally:
            lease_path.unlink(missing_ok=True)
            type(self).released += 1


def _wire_common(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _LeaseManager.acquired = []
    _LeaseManager.released = 0
    _LeaseManager.model_path = tmp_path / "model.gguf"
    _LeaseManager.model_path.write_bytes(b"GGUF model")
    _LeaseManager.cache_root = tmp_path / "cache"
    _LeaseManager.cache_root.mkdir(exist_ok=True)
    config = get_model("qwen2.5-coder-0.5b")
    assert config is not None
    candidate = PlannedModelCandidate(config, "a" * 40, 0.0, 0)
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
    monkeypatch.setattr(runner, "unified_probe", lambda: object())
    monkeypatch.setattr(runner, "CapabilityEvidenceStore", lambda _path: object())
    monkeypatch.setattr(
        runner,
        "load_latest_failed_model_ids",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        runner,
        "record_self_improve_outcome",
        lambda *_args, **_kwargs: 1,
    )
    monkeypatch.setattr(
        runner,
        "plan_model_candidates",
        lambda *_args, **_kwargs: (candidate,),
    )


def test_explicit_model_retries_without_silent_model_switch_and_releases_each_lease(
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
    monkeypatch.setattr(
        runner,
        "evaluate_attempt",
        lambda *_args, **kwargs: replace(
            _result(),
            attempt_identity_digest=kwargs["expected_attempt_identity_digest"],
        ),
    )
    monkeypatch.setattr(
        runner,
        "plan_model_candidates",
        lambda *_args, **_kwargs: pytest.fail(
            "explicit model override must not invoke automatic planning"
        ),
    )

    result = runner.run_benchmark(
        _args(_task_file(tmp_path), str(_LeaseManager.model_path))
    )

    assert result.comparison.accepted
    assert _LeaseManager.acquired == [
        ("Repair Python code safely.", _LeaseManager.model_path),
        ("Repair Python code safely.", _LeaseManager.model_path),
    ]
    assert _LeaseManager.released == 2
    assert attempts == 2


def test_terminal_proposal_rejection_publishes_only_typed_safe_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Do not copy native logs, model paths, or model text into public failures."""
    _wire_common(tmp_path, monkeypatch)
    raw_failure = (
        "ggml_metal_init model=/Users/operator/models/private.gguf TOKEN=top-secret\n"
        "SELF_IMPROVE_LOCAL_PROPOSAL_ERROR "
        "replace requires distinct non-empty old_text\n"
        '{"e":[{"p":"src/private.py","a":"raw child text","z":"PASSWORD=hunter2"}]}'
    )

    def reject(*_args: object) -> ProposalManifest:
        raise ValueError(raw_failure)

    monkeypatch.setattr(runner, "generate_local_proposal", reject)
    args = _args(_task_file(tmp_path))
    args.max_attempts = 1
    expected = (
        "protocol=self-improve-validation-retry-v3 "
        "type=edit_replace_contract source=proposal_error "
        "detail=replace requires distinct non-empty old_text"
    )

    with pytest.raises(ValueError):
        runner.run_benchmark(args)

    rejected = next(
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("SELF_IMPROVE_PROPOSAL_REJECTED")
    )
    assert rejected == f"SELF_IMPROVE_PROPOSAL_REJECTED attempt=1 {expected}"
    assert all(
        secret not in rejected
        for secret in (
            "/Users/operator",
            "private.gguf",
            "top-secret",
            "src/private.py",
            "raw child text",
            "hunter2",
        )
    )


def test_live_deepseek_v4_rejection_releases_lease_and_reports_safe_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep v4 identity, diagnostics, and cleanup on the exact live JSON class."""
    _wire_common(tmp_path, monkeypatch)
    plan = PromptPlan(
        shards=(
            PromptShard(
                ("src/general_ludd/example.py",),
                "LINES 1-1\nL1|before\n",
                editable_ranges=((1, 2),),
            ),
        ),
        source_bytes=7,
        baseline_files=(("src/general_ludd/example.py", "before\n"),),
        proposal_protocol="self-improve-compact-proposal-v4",
    )
    monkeypatch.setattr(runner, "build_prompt", lambda *_args: plan)
    raw_failure = (
        "llama loader /Users/operator/private.gguf TOKEN=top-secret\n"
        "SELF_IMPROVE_LOCAL_PROPOSAL_ERROR "
        "compact-v4 proposal is not one complete JSON object; output_bytes=2308\n"
        "PASSWORD=hunter2 raw-model-fragment"
    )

    def reject(*_args: object) -> ProposalManifest:
        raise ValueError(raw_failure)

    monkeypatch.setattr(runner, "generate_local_proposal_plan", reject)
    args = _args(_task_file(tmp_path))
    args.max_attempts = 1

    with pytest.raises(ValueError):
        runner.run_benchmark(args)

    rejected = next(
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("SELF_IMPROVE_PROPOSAL_REJECTED")
    )
    assert rejected == (
        "SELF_IMPROVE_PROPOSAL_REJECTED attempt=1 "
        "protocol=self-improve-validation-retry-v4 type=proposal_json_contract "
        "source=proposal_error detail=compact-v4 proposal is not one complete JSON object"
    )
    assert all(
        secret not in rejected
        for secret in (
            "/Users/operator",
            "private.gguf",
            "top-secret",
            "hunter2",
            "raw-model-fragment",
            "output_bytes",
        )
    )
    assert _LeaseManager.released == 1


def test_v4_scope_rejection_event_emits_bounded_coordinate_telemetry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Publish only parent-derived scope evidence and still release the lease."""
    _wire_common(tmp_path, monkeypatch)
    path = "src/private/TOKEN=path-secret.py"
    baseline = "shown-a\nshown-b\nhidden-c\nhidden-d\nshown-e\nshown-f\n"
    plan = PromptPlan(
        shards=(PromptShard((path,), "bounded prompt", ((1, 3), (5, 7))),),
        source_bytes=len(baseline.encode()),
        baseline_files=((path, baseline),),
        proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
    )
    monkeypatch.setattr(runner, "build_prompt", lambda *_args: plan)
    proposal = comparison_module._decode_compact_span_proposal(
        '{"e":[{"s":4,"n":0,"z":"PASSWORD=hunter2\\n"}]}',
        focus_path=path,
    )

    def reject(*_args: object) -> ProposalManifest:
        return comparison_module.expand_compact_span_proposals(
            (proposal,),
            contract=comparison_module.ProposalContract(
                baseline_sha="a" * 40,
                task_id="S83.133",
                tests=("tests/unit/test_example.py",),
                make_commands=(
                    "make test-files TESTFILES=tests/unit/test_example.py",
                ),
                proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
            ),
            expected_path_groups=((path,),),
            expected_baseline_files={path: baseline},
            expected_editable_ranges=(((1, 3), (5, 7)),),
        )

    monkeypatch.setattr(runner, "generate_local_proposal_plan", reject)
    args = _args(_task_file(tmp_path))
    args.max_attempts = 1

    with pytest.raises(ValueError):
        runner.run_benchmark(args)

    rejected = next(
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("SELF_IMPROVE_PROPOSAL_REJECTED")
    )
    assert "type=edit_span_scope source=parent_validation" in rejected
    assert (
        f"telemetry=path_sha256={hashlib.sha256(path.encode()).hexdigest()} "
        "received_s=4 received_n=0 sections=[1,3),[5,7) "
        "boundaries=[1,3],[5,7]"
    ) in rejected
    assert all(
        secret not in rejected
        for secret in (path, "TOKEN", "path-secret", "PASSWORD", "hunter2", "shown-a")
    )
    assert len(rejected.encode("utf-8")) <= 640
    assert _LeaseManager.released == 1


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


def test_managed_retries_escalate_across_distinct_planned_model_leases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_common(tmp_path, monkeypatch)
    small = get_model("qwen2.5-coder-0.5b")
    larger = get_model("deepseek-coder-1.3b")
    assert small is not None
    assert larger is not None
    candidates = (
        PlannedModelCandidate(small, "a" * 40, 0.4, 0),
        PlannedModelCandidate(larger, "b" * 40, 0.6, 1),
    )
    acquired: list[tuple[str, str]] = []
    released: list[str] = []
    proposal_paths: list[Path] = []
    evidence_paths: list[str] = []

    class CandidateLeaseManager:
        def __init__(
            self,
            *,
            event_sink: Callable[[ModelAcquisitionEvent], None] | None = None,
        ) -> None:
            assert callable(event_sink)
            self.cache_root = tmp_path / "cache"
            self.cache_root.mkdir(exist_ok=True)

        def resolve_revision(self, _repo_id: str) -> str:
            raise AssertionError("the injected planner owns revision resolution")

        def owned_identities_for_model_ids(
            self,
            _model_ids: tuple[str, ...],
        ) -> tuple[ModelArtifactIdentity, ...]:
            return ()

        @contextmanager
        def reserve_plan(
            self,
            _identities: tuple[ModelArtifactIdentity, ...],
            *,
            failure_hints: tuple[ModelArtifactIdentity, ...] = (),
        ) -> Iterator[_ReservationHandle]:
            del failure_hints
            yield _ReservationHandle(self.cache_root / "reservation.json")

        @contextmanager
        def acquire(
            self,
            task_description: str,
            *,
            explicit_path: Path | None = None,
            model_config: LocalModelConfig | None = None,
            resolved_revision: str | None = None,
        ) -> Iterator[SimpleNamespace]:
            assert task_description == "Repair Python code safely."
            assert explicit_path is None
            assert isinstance(model_config, LocalModelConfig)
            assert model_config in (small, larger)
            assert resolved_revision is not None
            model_id = model_config.name
            path = tmp_path / f"{model_id}.gguf"
            path.write_bytes(b"GGUF")
            acquired.append((model_id, resolved_revision))
            lease_path = self.cache_root / f"{model_id}.lease"
            lease_path.touch()
            try:
                yield SimpleNamespace(
                    path=path,
                    model_id=model_id,
                    resolved_revision=resolved_revision,
                    artifact_sha256="c" * 64,
                    source="managed",
                    lease_path=lease_path,
                )
            finally:
                lease_path.unlink(missing_ok=True)
                released.append(model_id)

    def plan(
        task_text: str,
        output_tokens: int,
        prior_failed_model_ids: tuple[str, ...],
        hardware: object,
        evidence_store: object,
        revision_resolver: object,
        *,
        input_tokens: int,
        max_candidates: int,
        on_resolution_failure: Callable[[LocalModelConfig, str], None],
    ) -> tuple[PlannedModelCandidate, ...]:
        assert task_text == "Repair Python code safely."
        assert output_tokens > 0
        assert input_tokens == 4
        assert prior_failed_model_ids == ()
        assert hardware == "hardware"
        assert evidence_store == "evidence"
        assert callable(revision_resolver)
        assert max_candidates == 2
        assert callable(on_resolution_failure)
        return candidates

    def propose(_root: object, path: Path, _prompt: str) -> ProposalManifest:
        proposal_paths.append(path)
        if len(proposal_paths) == 1:
            raise ValueError("strict schema failure")
        return _proposal()

    def build_evidence_store(path: str) -> str:
        evidence_paths.append(path)
        return "evidence"

    monkeypatch.setattr(runner, "ModelLeaseManager", CandidateLeaseManager)
    monkeypatch.setattr(runner, "unified_probe", lambda: "hardware", raising=False)
    monkeypatch.setattr(
        runner,
        "CapabilityEvidenceStore",
        build_evidence_store,
        raising=False,
    )
    monkeypatch.setattr(runner, "plan_model_candidates", plan, raising=False)
    monkeypatch.setattr(runner, "generate_local_proposal", propose)
    monkeypatch.setattr(runner, "evaluate_attempt", lambda *_args, **_kwargs: _result())

    result = runner.run_benchmark(_args(_task_file(tmp_path)))

    assert result.comparison.accepted
    assert acquired == [
        ("qwen2.5-coder-0.5b", "a" * 40),
        ("deepseek-coder-1.3b", "b" * 40),
    ]
    assert released == [
        "qwen2.5-coder-0.5b",
        "deepseek-coder-1.3b",
    ]
    assert proposal_paths == [
        tmp_path / "qwen2.5-coder-0.5b.gguf",
        tmp_path / "deepseek-coder-1.3b.gguf",
    ]
    assert evidence_paths == [str(tmp_path / "cache" / ".gludd" / "capability-evidence.json")]


def test_no_fitting_managed_candidate_emits_typed_plan_exhaustion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _wire_common(tmp_path, monkeypatch)

    class NoAcquireManager:
        def __init__(
            self,
            *,
            event_sink: Callable[[ModelAcquisitionEvent], None] | None = None,
        ) -> None:
            assert callable(event_sink)
            self.cache_root = tmp_path / "cache"
            self.cache_root.mkdir(exist_ok=True)

        def resolve_revision(self, _repo_id: str) -> str:
            return "a" * 40

        def acquire(self, *_args: object, **_kwargs: object) -> object:
            pytest.fail("model acquisition must not run without a fitting candidate")

    monkeypatch.setattr(runner, "ModelLeaseManager", NoAcquireManager)
    monkeypatch.setattr(runner, "unified_probe", lambda: object(), raising=False)
    monkeypatch.setattr(
        runner,
        "CapabilityEvidenceStore",
        lambda _path: object(),
        raising=False,
    )
    monkeypatch.setattr(
        runner,
        "plan_model_candidates",
        lambda *_args, **_kwargs: (),
        raising=False,
    )

    with pytest.raises(runner.ModelPlanError) as raised:
        runner.run_benchmark(_args(_task_file(tmp_path)))

    assert raised.value.failure is runner.ModelPlanFailure.EXHAUSTED
    feedback = runner._public_failure_feedback(raised.value)
    assert "type=model_plan_exhausted" in feedback
    assert "source=runner" in feedback
    assert "detail=<redacted>" in feedback
    assert "no fitting local coding model candidates" not in feedback
    output = capsys.readouterr().out
    assert "SELF_IMPROVE_MODEL_PLAN candidates=[]" in output
    assert "SELF_IMPROVE_ATTEMPT_START" not in output
    assert "SELF_IMPROVE_MODEL_OUTCOME" not in output
    assert "SELF_IMPROVE_PROPOSAL_REJECTED" not in output


def test_managed_attempts_load_prior_failures_and_persist_each_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _wire_common(tmp_path, monkeypatch)
    first = get_model("deepseek-coder-1.3b")
    second = get_model("qwen2.5-coder-1.5b")
    failed = get_model("qwen2.5-coder-0.5b")
    assert first is not None
    assert second is not None
    assert failed is not None
    candidates = (
        PlannedModelCandidate(first, "a" * 40, 0.4, 0),
        PlannedModelCandidate(second, "b" * 40, 0.5, 1),
    )
    evidence_store = object()
    events: list[str] = []
    attempt_identities: list[str] = []

    class EvidenceLeaseManager:
        def __init__(
            self,
            *,
            event_sink: Callable[[ModelAcquisitionEvent], None] | None = None,
        ) -> None:
            assert callable(event_sink)
            self.cache_root = tmp_path / "cache"
            self.cache_root.mkdir(exist_ok=True)

        def resolve_revision(self, _repo_id: str) -> str:
            raise AssertionError("the injected planner owns revision resolution")

        def owned_identities_for_model_ids(
            self,
            _model_ids: tuple[str, ...],
        ) -> tuple[ModelArtifactIdentity, ...]:
            return ()

        @contextmanager
        def reserve_plan(
            self,
            _identities: tuple[ModelArtifactIdentity, ...],
            *,
            failure_hints: tuple[ModelArtifactIdentity, ...] = (),
        ) -> Iterator[_ReservationHandle]:
            del failure_hints
            yield _ReservationHandle(self.cache_root / "reservation.json", events)

        @contextmanager
        def acquire(
            self,
            task_description: str,
            *,
            explicit_path: Path | None = None,
            model_config: LocalModelConfig | None = None,
            resolved_revision: str | None = None,
        ) -> Iterator[SimpleNamespace]:
            assert task_description == "Repair Python code safely."
            assert explicit_path is None
            assert model_config is not None
            assert resolved_revision is not None
            events.append(f"acquire:{model_config.name}")
            lease_path = self.cache_root / f"{model_config.name}.lease"
            lease_path.touch()
            try:
                path = tmp_path / f"{model_config.name}.gguf"
                path.write_bytes(b"GGUF")
                yield SimpleNamespace(
                    path=path,
                    model_id=model_config.name,
                    resolved_revision=resolved_revision,
                    artifact_sha256="c" * 64,
                    source="managed",
                    lease_path=lease_path,
                )
            finally:
                lease_path.unlink(missing_ok=True)
                events.append(f"release:{model_config.name}")

    def load_failures(
        store: object,
        *,
        task_text: str,
        attempt_identity_digest: str,
    ) -> tuple[str, ...]:
        assert store is evidence_store
        assert task_text == "Repair Python code safely."
        assert len(attempt_identity_digest) == 64
        int(attempt_identity_digest, 16)
        attempt_identities.append(attempt_identity_digest)
        events.append("load")
        return (failed.name,)

    def plan(
        task_text: str,
        output_tokens: int,
        prior_failed_model_ids: tuple[str, ...],
        hardware: object,
        store: object,
        revision_resolver: object,
        *,
        input_tokens: int,
        max_candidates: int,
        on_resolution_failure: Callable[[LocalModelConfig, str], None],
    ) -> tuple[PlannedModelCandidate, ...]:
        assert task_text == "Repair Python code safely."
        assert output_tokens > 0
        assert input_tokens == 4
        assert prior_failed_model_ids == (failed.name,)
        assert hardware == "hardware"
        assert store is evidence_store
        assert callable(revision_resolver)
        assert max_candidates == 2
        on_resolution_failure(failed, "network unavailable")
        events.append("plan")
        return candidates

    proposal_calls = 0

    def propose(_root: object, _path: Path, _prompt: str) -> ProposalManifest:
        nonlocal proposal_calls
        proposal_calls += 1
        if proposal_calls == 1:
            raise ValueError("strict schema failure")
        return _proposal()

    def record(
        store: object,
        *,
        task_text: str,
        candidate: PlannedModelCandidate,
        attempt_identity_digest: str,
        succeeded: bool,
    ) -> int:
        assert store is evidence_store
        assert task_text == "Repair Python code safely."
        assert attempt_identities == [attempt_identity_digest]
        events.append(f"outcome:{candidate.config.name}:{succeeded}")
        return len(events)

    monkeypatch.setattr(runner, "ModelLeaseManager", EvidenceLeaseManager)
    monkeypatch.setattr(runner, "unified_probe", lambda: "hardware")
    monkeypatch.setattr(runner, "CapabilityEvidenceStore", lambda _path: evidence_store)
    monkeypatch.setattr(runner, "load_latest_failed_model_ids", load_failures, raising=False)
    monkeypatch.setattr(runner, "plan_model_candidates", plan)
    monkeypatch.setattr(runner, "record_self_improve_outcome", record, raising=False)
    monkeypatch.setattr(runner, "generate_local_proposal", propose)
    monkeypatch.setattr(runner, "evaluate_attempt", lambda *_args, **_kwargs: _result())

    result = runner.run_benchmark(_args(_task_file(tmp_path)))

    assert result.comparison.accepted
    assert attempt_identities == [runner._attempt_identity_digest("bounded prompt")]
    output = capsys.readouterr().out
    assert (
        "SELF_IMPROVE_MODEL_UNAVAILABLE "
        f"model={failed.name} error=\"network unavailable\""
    ) in output
    assert (
        f"SELF_IMPROVE_MODEL_RELEASED model={first.name} lease_released=true"
    ) in output
    assert (
        f"SELF_IMPROVE_MODEL_OUTCOME model={first.name} succeeded=false"
    ) in output
    assert (
        f"SELF_IMPROVE_MODEL_RELEASED model={second.name} lease_released=true"
    ) in output
    assert (
        f"SELF_IMPROVE_MODEL_OUTCOME model={second.name} succeeded=true"
    ) in output
    assert events == [
        "load",
        "plan",
        f"acquire:{first.name}",
        f"release:{first.name}",
        f"failed:{first.name}",
        f"outcome:{first.name}:False",
        f"acquire:{second.name}",
        f"eligible:{second.name}",
        f"release:{second.name}",
        f"outcome:{second.name}:True",
    ]



def test_managed_runner_wires_bounded_acquisition_event_sink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _wire_common(tmp_path, monkeypatch)
    captured: dict[str, Callable[[ModelAcquisitionEvent], None]] = {}

    class ObservableLeaseManager(_LeaseManager):
        def __init__(
            self,
            *,
            event_sink: Callable[[ModelAcquisitionEvent], None] | None = None,
        ) -> None:
            assert event_sink is not None
            captured["sink"] = event_sink

    monkeypatch.setattr(runner, "ModelLeaseManager", ObservableLeaseManager)
    monkeypatch.setattr(runner, "generate_local_proposal", lambda *_args: _proposal())
    monkeypatch.setattr(runner, "evaluate_attempt", lambda *_args, **_kwargs: _result())

    result = runner.run_benchmark(_args(_task_file(tmp_path)))
    captured["sink"](
        ModelAcquisitionEvent(
            phase=ModelAcquisitionPhase.DOWNLOAD_PROGRESS,
            operation_id="0123456789abcdef",
            repository_key="fedcba9876543210",
            model_key="0011223344556677",
            revision="a" * 40,
            elapsed_seconds=15.25,
        )
    )

    assert result.comparison.accepted
    output = capsys.readouterr().out
    assert (
        "SELF_IMPROVE_MODEL_ACQUISITION phase=download_progress "
        "operation=0123456789abcdef repository=fedcba9876543210 "
        "model=0011223344556677 revision=" + "a" * 40
        + " elapsed_seconds=15.25 failure=none"
    ) in output


def test_validate_only_rejects_unmapped_automatic_model_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_common(tmp_path, monkeypatch)
    task_file = tmp_path / "unmapped-task.json"
    task_file.write_text(
        json.dumps(
            {
                "task_id": "S83.133",
                "objective": "Improve the local workflow.",
                "canonical_make_commands": [
                    "make test-files TESTFILES=tests/unit/test_example.py"
                ],
            }
        ),
        encoding="utf-8",
    )
    args = _args(task_file)
    args.validate_only = True

    with pytest.raises(ValueError, match="mapped coding capability"):
        runner.run_benchmark(args)


def test_managed_runner_reserves_every_candidate_with_exact_failure_hints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_common(tmp_path, monkeypatch)
    first = get_model("qwen2.5-coder-0.5b")
    second = get_model("deepseek-coder-1.3b")
    failed = get_model("qwen2.5-coder-1.5b")
    assert first is not None and second is not None and failed is not None
    first_model: LocalModelConfig = first
    second_model: LocalModelConfig = second
    failed_model: LocalModelConfig = failed
    candidates = (
        PlannedModelCandidate(first_model, "a" * 40, 0.4, 0),
        PlannedModelCandidate(second_model, "b" * 40, 0.6, 1),
    )
    failure_identity = ModelArtifactIdentity(
        failed_model.name,
        failed_model.repo,
        failed_model.filename,
        "f" * 40,
    )
    reservations: list[
        tuple[
            tuple[ModelArtifactIdentity, ...],
            tuple[ModelArtifactIdentity, ...],
        ]
    ] = []
    events: list[str] = []
    active = False

    class ReservationManager(_LeaseManager):
        def owned_identities_for_model_ids(
            self,
            model_ids: tuple[str, ...],
        ) -> tuple[ModelArtifactIdentity, ...]:
            assert model_ids == (failed_model.name,)
            return (failure_identity,)

        @contextmanager
        def reserve_plan(
            self,
            identities: tuple[ModelArtifactIdentity, ...],
            *,
            failure_hints: tuple[ModelArtifactIdentity, ...] = (),
        ) -> Iterator[_ReservationHandle]:
            nonlocal active
            reservations.append((identities, failure_hints))
            active = True
            events.append("reservation-enter")
            handle = _ReservationHandle(
                self.cache_root / "reservation.json",
                events,
            )
            try:
                yield handle
            finally:
                active = False
                events.append("reservation-exit")

    evaluation_calls = 0

    def propose(*_args: object) -> ProposalManifest:
        assert active
        return _proposal()

    def evaluate(*_args: object, **_kwargs: object) -> AttemptResult:
        nonlocal evaluation_calls
        evaluation_calls += 1
        result = _result()
        if evaluation_calls == 1:
            return replace(
                result,
                comparison=replace(
                    result.comparison,
                    accepted=False,
                    score=0.0,
                    blockers=("quality threshold",),
                ),
            )
        return result

    monkeypatch.setattr(runner, "ModelLeaseManager", ReservationManager)
    monkeypatch.setattr(
        runner,
        "load_latest_failed_model_ids",
        lambda *_args, **_kwargs: (failed_model.name,),
    )
    monkeypatch.setattr(
        runner,
        "plan_model_candidates",
        lambda *_args, **_kwargs: candidates,
    )
    monkeypatch.setattr(runner, "generate_local_proposal", propose)
    monkeypatch.setattr(runner, "evaluate_attempt", evaluate)

    assert runner.run_benchmark(_args(_task_file(tmp_path))).comparison.accepted

    assert reservations == [
        (
            (
                ModelArtifactIdentity(
                    first_model.name,
                    first_model.repo,
                    first_model.filename,
                    "a" * 40,
                ),
                ModelArtifactIdentity(
                    second_model.name,
                    second_model.repo,
                    second_model.filename,
                    "b" * 40,
                ),
            ),
            (failure_identity,),
        )
    ]
    assert events == [
        "reservation-enter",
        f"eligible:{first_model.name}",
        f"failed:{first_model.name}",
        f"eligible:{second_model.name}",
        "reservation-exit",
    ]
    assert not active


def test_managed_runner_releases_plan_reservation_on_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_common(tmp_path, monkeypatch)
    candidate = get_model("qwen2.5-coder-0.5b")
    assert candidate is not None
    candidate_model: LocalModelConfig = candidate
    active = False
    exited = False
    transitions: list[str] = []

    class ReservationManager(_LeaseManager):
        def owned_identities_for_model_ids(
            self,
            _model_ids: tuple[str, ...],
        ) -> tuple[ModelArtifactIdentity, ...]:
            return ()

        @contextmanager
        def reserve_plan(
            self,
            identities: tuple[ModelArtifactIdentity, ...],
            *,
            failure_hints: tuple[ModelArtifactIdentity, ...] = (),
        ) -> Iterator[_ReservationHandle]:
            nonlocal active, exited
            assert identities == (
                ModelArtifactIdentity(
                    candidate_model.name,
                    candidate_model.repo,
                    candidate_model.filename,
                    "a" * 40,
                ),
            )
            assert failure_hints == ()
            active = True
            handle = _ReservationHandle(
                self.cache_root / "reservation.json",
                transitions,
            )
            try:
                yield handle
            except BaseException as exc:
                exc.add_note("reservation observed primary cancellation")
                raise
            finally:
                active = False
                exited = True

    def cancelled(*_args: object) -> ProposalManifest:
        assert active
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "ModelLeaseManager", ReservationManager)
    monkeypatch.setattr(runner, "generate_local_proposal", cancelled)

    with pytest.raises(KeyboardInterrupt) as raised:
        runner.run_benchmark(_args(_task_file(tmp_path)))

    assert exited
    assert not active
    assert transitions == [f"failed:{candidate_model.name}"]
    assert "reservation observed primary cancellation" in raised.value.__notes__


def test_managed_runner_marks_evaluation_cancellation_failed_before_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_common(tmp_path, monkeypatch)
    candidate = get_model("qwen2.5-coder-0.5b")
    assert candidate is not None
    candidate_model: LocalModelConfig = candidate
    transitions: list[str] = []
    exited = False

    class EvaluationManager(_LeaseManager):
        @contextmanager
        def reserve_plan(
            self,
            identities: tuple[ModelArtifactIdentity, ...],
            *,
            failure_hints: tuple[ModelArtifactIdentity, ...] = (),
        ) -> Iterator[_ReservationHandle]:
            nonlocal exited
            assert identities == (
                ModelArtifactIdentity(
                    candidate_model.name,
                    candidate_model.repo,
                    candidate_model.filename,
                    "a" * 40,
                ),
            )
            assert failure_hints == ()
            try:
                yield _ReservationHandle(
                    self.cache_root / "reservation.json",
                    transitions,
                )
            finally:
                exited = True

    def cancel_evaluation(*_args: object, **_kwargs: object) -> AttemptResult:
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "ModelLeaseManager", EvaluationManager)
    monkeypatch.setattr(runner, "generate_local_proposal", lambda *_args: _proposal())
    monkeypatch.setattr(runner, "evaluate_attempt", cancel_evaluation)

    with pytest.raises(KeyboardInterrupt):
        runner.run_benchmark(_args(_task_file(tmp_path)))

    assert transitions == [
        f"eligible:{candidate_model.name}",
        f"failed:{candidate_model.name}",
    ]
    assert exited
    assert EvaluationManager.released == 1


def test_explicit_model_evaluation_exception_releases_without_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_common(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "generate_local_proposal", lambda *_args: _proposal())

    def fail_evaluation(*_args: object, **_kwargs: object) -> AttemptResult:
        raise RuntimeError("evaluation failed")

    monkeypatch.setattr(runner, "evaluate_attempt", fail_evaluation)

    with pytest.raises(RuntimeError, match="evaluation failed"):
        runner.run_benchmark(
            _args(_task_file(tmp_path), model_path=str(_LeaseManager.model_path))
        )

    assert _LeaseManager.acquired == [
        ("Repair Python code safely.", _LeaseManager.model_path)
    ]
    assert _LeaseManager.released == 1


def test_managed_acquisition_entry_failure_closes_plan_before_reraising(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_common(tmp_path, monkeypatch)
    transitions: list[str] = []
    exited = False

    class EntryFailureManager(_LeaseManager):
        @contextmanager
        def reserve_plan(
            self,
            _identities: tuple[ModelArtifactIdentity, ...],
            *,
            failure_hints: tuple[ModelArtifactIdentity, ...] = (),
        ) -> Iterator[_ReservationHandle]:
            nonlocal exited
            assert failure_hints == ()
            try:
                yield _ReservationHandle(
                    self.cache_root / "reservation.json",
                    transitions,
                )
            finally:
                exited = True

        @contextmanager
        def acquire(
            self,
            _task_description: str,
            *,
            explicit_path: Path | None = None,
            model_config: object | None = None,
            resolved_revision: str | None = None,
        ) -> Iterator[SimpleNamespace]:
            del explicit_path, model_config, resolved_revision
            if False:
                yield SimpleNamespace()
            raise RuntimeError("acquisition entry failed")

    monkeypatch.setattr(runner, "ModelLeaseManager", EntryFailureManager)
    args = _args(_task_file(tmp_path))
    args.max_attempts = 1

    with pytest.raises(RuntimeError, match="acquisition entry failed"):
        runner.run_benchmark(args)

    assert transitions == ["failed:qwen2.5-coder-0.5b"]
    assert exited
    assert EntryFailureManager.released == 0


def test_typed_acquisition_refusal_does_not_poison_evidence_or_retry_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _wire_common(tmp_path, monkeypatch)
    transitions: list[str] = []
    outcomes: list[str] = []
    acquisition_calls = 0
    reservation_exited = False

    class TypedAcquisitionFailure(RuntimeError):
        failure = SimpleNamespace(value="cache_reclaim")

    class RefusingManager(_LeaseManager):
        @contextmanager
        def reserve_plan(
            self,
            _identities: tuple[ModelArtifactIdentity, ...],
            *,
            failure_hints: tuple[ModelArtifactIdentity, ...] = (),
        ) -> Iterator[_ReservationHandle]:
            nonlocal reservation_exited
            assert failure_hints == ()
            try:
                yield _ReservationHandle(
                    self.cache_root / "reservation.json",
                    transitions,
                )
            finally:
                reservation_exited = True

        @contextmanager
        def acquire(
            self,
            _task_description: str,
            *,
            explicit_path: Path | None = None,
            model_config: object | None = None,
            resolved_revision: str | None = None,
        ) -> Iterator[SimpleNamespace]:
            nonlocal acquisition_calls
            del explicit_path, model_config, resolved_revision
            acquisition_calls += 1
            if False:
                yield SimpleNamespace()
            raise TypedAcquisitionFailure(
                "managed model acquisition failed: cache_reclaim"
            )

    monkeypatch.setattr(
        runner,
        "ModelAcquisitionError",
        TypedAcquisitionFailure,
        raising=False,
    )
    monkeypatch.setattr(runner, "ModelLeaseManager", RefusingManager)
    monkeypatch.setattr(
        runner,
        "record_self_improve_outcome",
        lambda *_args, **_kwargs: outcomes.append("recorded"),
    )
    args = _args(_task_file(tmp_path))
    args.max_attempts = 2

    with pytest.raises(TypedAcquisitionFailure, match="cache_reclaim"):
        runner.run_benchmark(args)

    output = capsys.readouterr().out
    assert acquisition_calls == 1
    assert reservation_exited
    assert transitions == []
    assert outcomes == []
    assert (
        "SELF_IMPROVE_MODEL_ACQUISITION_REJECTED "
        "attempt=1 failure=cache_reclaim"
    ) in output
    assert "SELF_IMPROVE_PROPOSAL_REJECTED" not in output
    assert "SELF_IMPROVE_MODEL_OUTCOME" not in output
    assert "candidate plan is exhausted" not in output
    feedback = runner._public_failure_feedback(
        TypedAcquisitionFailure("must not be exposed")
    )
    assert "type=cache_reclaim" in feedback
    assert "source=model_lifecycle" in feedback
    assert "detail=<redacted>" in feedback
    assert "must not be exposed" not in feedback


def test_prompt_plan_generation_failure_retries_with_next_reserved_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_common(tmp_path, monkeypatch)
    first = get_model("qwen2.5-coder-0.5b")
    second = get_model("deepseek-coder-1.3b")
    assert first is not None and second is not None
    prompt_plan = runner.PromptPlan(
        shards=(
            runner.PromptShard(
                (
                    "src/general_ludd/example.py",
                    "tests/unit/test_example.py",
                ),
                "bounded prompt",
            ),
        ),
        source_bytes=14,
    )
    candidates = (
        PlannedModelCandidate(first, "a" * 40, 0.0, 0),
        PlannedModelCandidate(second, "b" * 40, 0.0, 1),
    )
    proposal_calls = 0

    def generate_plan(*_args: object) -> ProposalManifest:
        nonlocal proposal_calls
        proposal_calls += 1
        if proposal_calls == 1:
            raise RuntimeError("first candidate generation failed")
        return _proposal()

    monkeypatch.setattr(runner, "build_prompt", lambda *_args: prompt_plan)
    monkeypatch.setattr(
        runner,
        "plan_model_candidates",
        lambda *_args, **_kwargs: candidates,
    )
    monkeypatch.setattr(runner, "generate_local_proposal_plan", generate_plan)
    monkeypatch.setattr(
        runner,
        "_build_validation_retry_prompt_plan",
        lambda base, _diagnostic: base,
    )
    monkeypatch.setattr(
        runner,
        "evaluate_attempt",
        lambda *_args, **kwargs: replace(
            _result(),
            attempt_identity_digest=kwargs["expected_attempt_identity_digest"],
        ),
    )

    result = runner.run_benchmark(_args(_task_file(tmp_path)))

    assert result.comparison.accepted
    assert proposal_calls == 2
    assert _LeaseManager.released == 2


def test_consumed_managed_plan_exhausts_without_fake_second_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _wire_common(tmp_path, monkeypatch)
    model = get_model("qwen2.5-coder-0.5b")
    assert model is not None
    candidate = PlannedModelCandidate(model, "a" * 40, 0.0, 0)
    outcomes: list[tuple[str, bool]] = []

    def reject_proposal(
        _root: object,
        _path: Path,
        _prompt: str,
    ) -> ProposalManifest:
        raise ValueError("invalid structured proposal")

    def record(
        _store: object,
        *,
        task_text: str,
        candidate: PlannedModelCandidate,
        attempt_identity_digest: str,
        succeeded: bool,
    ) -> int:
        assert task_text
        assert len(attempt_identity_digest) == 64
        outcomes.append((candidate.config.name, succeeded))
        return len(outcomes)

    monkeypatch.setattr(
        runner,
        "plan_model_candidates",
        lambda *_args, **_kwargs: (candidate,),
    )
    monkeypatch.setattr(runner, "generate_local_proposal", reject_proposal)
    monkeypatch.setattr(runner, "record_self_improve_outcome", record)

    with pytest.raises(runner.ModelPlanError) as raised:
        runner.run_benchmark(_args(_task_file(tmp_path)))

    assert raised.value.failure is runner.ModelPlanFailure.EXHAUSTED
    assert outcomes == [(model.name, False)]
    assert len(_LeaseManager.acquired) == 1
    assert _LeaseManager.released == 1
    output = capsys.readouterr().out
    assert output.count("SELF_IMPROVE_ATTEMPT_START") == 1
    assert "SELF_IMPROVE_ATTEMPT_START attempt=2" not in output
    assert output.count("SELF_IMPROVE_MODEL_OUTCOME") == 1
    assert output.count("SELF_IMPROVE_PROPOSAL_REJECTED") == 1


def test_run_benchmark_default_sink_flushes_evaluation_and_retry_diagnosis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Publish safe lifecycle evidence through the real CLI composition root."""
    _wire_common(tmp_path, monkeypatch)
    baseline_files = (
        ("src/general_ludd/example.py", "return 0\n"),
        ("tests/unit/test_example.py", "assert False\n"),
    )
    prompt = PromptPlan(
        shards=(
            PromptShard(
                focus_paths=tuple(path for path, _content in baseline_files),
                prompt="bounded compact-v4 prompt",
                editable_ranges=((1, 2), (3, 4)),
            ),
        ),
        source_bytes=sum(len(content.encode("utf-8")) for _path, content in baseline_files),
        baseline_files=baseline_files,
        proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
    )
    first = get_model("qwen2.5-coder-1.5b")
    second = get_model("qwen2.5-coder-3b")
    assert first is not None
    assert second is not None
    candidates = (
        PlannedModelCandidate(first, "a" * 40, 0.0, 0),
        PlannedModelCandidate(second, "b" * 40, 0.0, 1),
    )
    command_sha256 = hashlib.sha256(b"approved-make").hexdigest()
    diagnosis = json.dumps(
        {
            "command_kind": "approved_make",
            "command_sha256": command_sha256,
            "duration_ms": 1000,
            "exit_code": 1,
            "failure_class": "make_failed",
            "finish_reason": "unknown",
            "finished": True,
            "hypothesis": "approved evaluation failed; correct only the typed phase",
            "phase": "approved_make",
            "protocol": "self-improve-evaluation-diagnosis-v1",
            "schema_version": 2,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    attempts: list[int] = []

    def evaluate(
        _root_runner: object,
        _task: object,
        _reference: object,
        _bound: object,
        attempt: int,
        *,
        expected_attempt_identity_digest: str,
        merge: bool,
        make_runner_factory: object | None = None,
        progress_sink: Callable[[str], None] | None = None,
    ) -> AttemptResult:
        del merge, make_runner_factory
        attempts.append(attempt)
        assert progress_sink is not None
        runner._record_evaluation_event(
            [],
            progress_sink,
            phase="approved_make",
            command_kind="approved_make",
            command_identity="make hidden SECRET_TOKEN=/absolute/private/repo",
            returncode=1 if attempt == 1 else 0,
            elapsed_seconds=1.0,
            failure_class="make_failed",
        )
        result = replace(
            _result(),
            attempt_identity_digest=expected_attempt_identity_digest,
        )
        if attempt == 1:
            return replace(
                result,
                comparison=ComparisonResult(
                    accepted=False,
                    score=60.0,
                    blockers=("tests",),
                    changed_file_precision=1.0,
                    changed_file_recall=1.0,
                ),
                diagnostics=diagnosis,
            )
        return result

    monkeypatch.setattr(runner, "build_prompt", lambda *_args: prompt)
    monkeypatch.setattr(
        runner,
        "plan_model_candidates",
        lambda *_args, **_kwargs: candidates,
    )
    monkeypatch.setattr(runner, "generate_local_proposal_plan", lambda *_args: _proposal())
    monkeypatch.setattr(runner, "evaluate_attempt", evaluate)

    result = runner.run_benchmark(_args(_task_file(tmp_path)))

    assert result.comparison.accepted
    assert attempts == [1, 2]
    output_lines = capsys.readouterr().out.splitlines()
    evaluation_lines = [
        line
        for line in output_lines
        if line.startswith("SELF_IMPROVE_EVALUATION_EVENT ")
    ]
    retry_lines = [
        line
        for line in output_lines
        if line.startswith("SELF_IMPROVE_RETRY_DIAGNOSIS ")
    ]
    assert len(evaluation_lines) == 2
    assert retry_lines == [
        "SELF_IMPROVE_RETRY_DIAGNOSIS "
        "protocol=self-improve-evaluation-diagnosis-v1 "
        "phase=approved_make failure=make_failed rc=1 duration_ms=1000 "
        f"command_sha256={command_sha256}"
    ]
    first_event = output_lines.index(evaluation_lines[0])
    retry_event = output_lines.index(retry_lines[0])
    stable_attempt_identity = (
        "ee4671f30088b25acf380a6f3c53b1b518693439e99e7ed290e4986f5d85b82a"
    )
    attempt_lines = [
        line
        for line in output_lines
        if line.startswith("SELF_IMPROVE_ATTEMPT_START ")
    ]
    assert attempt_lines == [
        "SELF_IMPROVE_ATTEMPT_START "
        f"attempt=1 attempt_identity_digest={stable_attempt_identity}",
        "SELF_IMPROVE_ATTEMPT_START "
        f"attempt=2 attempt_identity_digest={stable_attempt_identity}",
    ]
    second_attempt = next(
        index
        for index, line in enumerate(output_lines)
        if line.startswith("SELF_IMPROVE_ATTEMPT_START attempt=2 ")
    )
    assert first_event < retry_event < second_attempt
    safe_telemetry = "\n".join((*evaluation_lines, *retry_lines))
    for forbidden in ("make hidden", "SECRET_TOKEN", "/absolute/private/repo"):
        assert forbidden not in safe_telemetry


def test_retry_diagnosis_event_sanitizes_invalid_injected_artifact() -> None:
    """Never reflect untrusted diagnosis fields into the parent-readable stream."""
    injected = json.dumps(
        {
            "protocol": "self-improve-evaluation-diagnosis-v1",
            "source": "MODEL_Z SECRET_TOKEN /absolute/private/repo make hidden",
        }
    )

    rendered = runner._render_retry_diagnosis_event(injected)

    assert rendered == (
        "SELF_IMPROVE_RETRY_DIAGNOSIS "
        "protocol=self-improve-evaluation-diagnosis-v1 phase=evaluation "
        "failure=diagnosis_unavailable rc=1 duration_ms=0 "
        f"command_sha256={hashlib.sha256(b'self-improve-evaluation-diagnosis-v1').hexdigest()}"
    )
    assert len(rendered.encode("ascii")) <= 256
    for forbidden in ("MODEL_Z", "SECRET_TOKEN", "/absolute/private/repo", "make hidden"):
        assert forbidden not in rendered
