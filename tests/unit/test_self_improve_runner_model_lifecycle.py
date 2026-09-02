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

from general_ludd.local_model import LocalModelConfig, get_model
from general_ludd.self_improve.codex_comparison import (
    CandidateEvidence,
    CodexReference,
    ComparisonResult,
    ProposalManifest,
)
from general_ludd.self_improve.model_candidate_planner import PlannedModelCandidate


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
    cache_root: ClassVar[Path]


    def resolve_revision(self, _repo_id: str) -> str:
        return "a" * 40

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
        try:
            yield SimpleNamespace(
                path=type(self).model_path,
                model_id=model_id,
                resolved_revision=resolved_revision or "a" * 40,
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
    monkeypatch.setattr(runner, "evaluate_attempt", lambda *_args, **_kwargs: _result())
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
        def __init__(self) -> None:
            self.cache_root = tmp_path / "cache"
            self.cache_root.mkdir(exist_ok=True)

        def resolve_revision(self, _repo_id: str) -> str:
            raise AssertionError("the injected planner owns revision resolution")

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
            try:
                yield SimpleNamespace(
                    path=path,
                    model_id=model_id,
                    resolved_revision=resolved_revision,
                    artifact_sha256="c" * 64,
                    source="managed",
                )
            finally:
                released.append(model_id)

    def plan(
        task_text: str,
        output_tokens: int,
        prior_failed_model_ids: tuple[str, ...],
        hardware: object,
        evidence_store: object,
        revision_resolver: object,
        *,
        max_candidates: int,
    ) -> tuple[PlannedModelCandidate, ...]:
        assert task_text == "Repair Python code safely."
        assert output_tokens > 0
        assert prior_failed_model_ids == ()
        assert hardware == "hardware"
        assert evidence_store == "evidence"
        assert callable(revision_resolver)
        assert max_candidates == 2
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


def test_no_fitting_managed_candidate_fails_before_model_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_common(tmp_path, monkeypatch)

    class NoAcquireManager:
        def __init__(self) -> None:
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

    with pytest.raises(RuntimeError, match="no fitting local coding model candidates"):
        runner.run_benchmark(_args(_task_file(tmp_path)))


def test_managed_attempts_load_prior_failures_and_persist_each_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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

    class EvidenceLeaseManager:
        def __init__(self) -> None:
            self.cache_root = tmp_path / "cache"
            self.cache_root.mkdir(exist_ok=True)

        def resolve_revision(self, _repo_id: str) -> str:
            raise AssertionError("the injected planner owns revision resolution")

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
            try:
                path = tmp_path / f"{model_config.name}.gguf"
                path.write_bytes(b"GGUF")
                yield SimpleNamespace(
                    path=path,
                    model_id=model_config.name,
                    resolved_revision=resolved_revision,
                    artifact_sha256="c" * 64,
                    source="managed",
                )
            finally:
                events.append(f"release:{model_config.name}")

    def load_failures(store: object, *, task_text: str) -> tuple[str, ...]:
        assert store is evidence_store
        assert task_text == "Repair Python code safely."
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
        max_candidates: int,
    ) -> tuple[PlannedModelCandidate, ...]:
        assert task_text == "Repair Python code safely."
        assert output_tokens > 0
        assert prior_failed_model_ids == (failed.name,)
        assert hardware == "hardware"
        assert store is evidence_store
        assert callable(revision_resolver)
        assert max_candidates == 2
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
        succeeded: bool,
    ) -> int:
        assert store is evidence_store
        assert task_text == "Repair Python code safely."
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
    assert events == [
        "load",
        "plan",
        f"acquire:{first.name}",
        f"release:{first.name}",
        f"outcome:{first.name}:False",
        f"acquire:{second.name}",
        f"release:{second.name}",
        f"outcome:{second.name}:True",
    ]
