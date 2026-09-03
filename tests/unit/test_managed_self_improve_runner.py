"""Contracts for the reusable managed self-improvement runner."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import scripts.run_self_improve_e2e as cli_runner

import general_ludd.self_improve as self_improve_package
import general_ludd.self_improve.managed_runner as managed_runner_module
import general_ludd.self_improve.model_candidate_planner as planner_module
from general_ludd.local_model import LocalModelConfig, get_model
from general_ludd.self_improve.codex_comparison import (
    CandidateEvidence,
    CodexReference,
    ComparisonResult,
    ProposalManifest,
    local_proposal_attempt_identity_digest,
)
from general_ludd.self_improve.managed_runner import (
    ApprovedSelfImprovePlan,
    AttemptResult,
    ManagedRunResult,
    ManagedSelfImproveRunner,
    PlanBoundProposal,
    PromptPlan,
    PromptShard,
    TaskSpec,
    apply_proposal,
)
from general_ludd.self_improve.model_candidate_planner import PlannedModelCandidate
from general_ludd.self_improve.model_lifecycle import ModelArtifactIdentity
from general_ludd.self_improve.runtime import (
    MakeResult,
    build_managed_self_improve_runner,
    prepare_managed_self_improve_plan,
)

_REPOSITORY_BINDING_DIGEST = "e" * 64


def _task() -> TaskSpec:
    return TaskSpec(
        task_id="S83.200",
        objective="Implement a focused Python feature.",
        canonical_make_commands=(
            "make test-files TESTFILES=tests/unit/test_example.py",
        ),
    )


def test_managed_runner_is_available_from_the_self_improve_package() -> None:
    assert self_improve_package.ApprovedSelfImprovePlan is ApprovedSelfImprovePlan
    assert self_improve_package.ManagedRunResult is ManagedRunResult
    assert self_improve_package.ManagedSelfImproveRunner is ManagedSelfImproveRunner
    assert self_improve_package.CodeTaskShape is planner_module.CodeTaskShape
    assert "CodeTaskShape" in self_improve_package.__all__


def _reference() -> CodexReference:
    return CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({"src/general_ludd/example.py"}),
        test_files=frozenset({"tests/unit/test_example.py"}),
        changed_lines=2,
        elapsed_seconds=1.0,
    )


def _proposal() -> ProposalManifest:
    return ProposalManifest.from_json(
        """{
          "schema_version": 1,
          "baseline_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "task_id": "S83.200",
          "edits": [{
            "operation": "replace",
            "path": "src/general_ludd/example.py",
            "old_text": "return 0",
            "new_text": "return 1"
          }],
          "tests": ["tests/unit/test_example.py"],
          "make_commands": [
            "make test-files TESTFILES=tests/unit/test_example.py"
          ],
          "commit_message": "feat: improve example"
        }"""
    )


def _evidence() -> CandidateEvidence:
    return CandidateEvidence(
        changed_files=frozenset({"src/general_ludd/example.py"}),
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
        elapsed_seconds=0.5,
        changed_lines=2,
    )


def _candidate(name: str = "qwen2.5-coder-0.5b", level: int = 0) -> PlannedModelCandidate:
    model = get_model(name)
    assert model is not None
    return PlannedModelCandidate(model, chr(ord("c") + level) * 40, 0.5, level)


def _plan(tmp_path: Path, *, max_attempts: int = 2) -> ApprovedSelfImprovePlan:
    return ApprovedSelfImprovePlan.approve(
        approval_id="approval-1",
        todo_id="todo-1",
        project_id="project-1",
        repo_root=tmp_path,
        task=_task(),
        reference=_reference(),
        prompt="bounded prompt",
        required_output_tokens=1024,
        max_attempts=max_attempts,
    )


def _repository_bound_plan(repo_root: Path) -> ApprovedSelfImprovePlan:
    return ApprovedSelfImprovePlan.approve(
        approval_id="approval-repository-bound",
        todo_id="todo-repository-bound",
        project_id="project-repository-bound",
        repo_root=repo_root,
        repository_binding_digest=_REPOSITORY_BINDING_DIGEST,
        task=_task(),
        reference=_reference(),
        prompt="bounded repository prompt",
        required_output_tokens=1024,
        max_attempts=1,
        mechanical_proposal=_proposal(),
    )


class _NoopRuntimeMakeRunner:
    """Make boundary that proves a mechanical binding test performs no I/O."""

    def run(
        self,
        target: str,
        variables: dict[str, str] | None = None,
        *,
        timeout: int = 120,
        read_only: bool = False,
    ) -> MakeResult:
        del target, variables, timeout, read_only
        raise AssertionError("repository binding unexpectedly invoked a Make target")

    def run_command(self, command: str, *, timeout: int = 900) -> MakeResult:
        del command, timeout
        raise AssertionError("repository binding unexpectedly invoked a Make command")

    def run_observable(
        self,
        target: str,
        variables: dict[str, str],
        *,
        timeout: int,
    ) -> MakeResult:
        del target, variables, timeout
        raise AssertionError("repository binding unexpectedly started a process")


class _Outcomes:
    planner_store = object()

    def __init__(self) -> None:
        self.records: list[tuple[str, bool, str]] = []

    def load_failed_model_ids(
        self,
        *,
        task_text: str,
        attempt_identity_digest: str,
    ) -> tuple[str, ...]:
        assert task_text == _task().objective
        assert len(attempt_identity_digest) == 64
        return ()

    def record_outcome(
        self,
        *,
        task_text: str,
        candidate: PlannedModelCandidate,
        succeeded: bool,
        attempt_identity_digest: str,
    ) -> int:
        assert task_text == _task().objective
        self.records.append(
            (candidate.config.name, succeeded, attempt_identity_digest)
        )
        return len(self.records)


class _LeaseManager:
    def __init__(self, tmp_path: Path) -> None:
        self.cache_root = tmp_path / "cache"
        self.cache_root.mkdir()
        self.acquired: list[str] = []
        self.released: list[str] = []
        self.reservation_released = False

    def resolve_revision(self, _repo_id: str) -> str:
        return "c" * 40

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
    ) -> Iterator[SimpleNamespace]:
        del failure_hints
        handle = SimpleNamespace(mark_eligible=lambda _identity: None, mark_failed=lambda _identity: None)
        try:
            yield handle
        finally:
            self.reservation_released = True

    @contextmanager
    def acquire(
        self,
        _task_description: str,
        *,
        explicit_path: Path | None = None,
        model_config: object | None = None,
        resolved_revision: str | None = None,
    ) -> Iterator[SimpleNamespace]:
        del explicit_path, resolved_revision
        assert isinstance(model_config, LocalModelConfig)
        model_id = model_config.name
        path = self.cache_root / f"{model_id}.gguf"
        path.write_bytes(b"GGUF")
        lease_path = self.cache_root / f"{model_id}.lease"
        lease_path.touch()
        self.acquired.append(model_id)
        try:
            yield SimpleNamespace(
                path=path,
                model_id=model_id,
                source="managed",
                resolved_revision="c" * 40,
                artifact_sha256="d" * 64,
                lease_path=lease_path,
            )
        finally:
            lease_path.unlink(missing_ok=True)
            self.released.append(model_id)


def _result(bound: PlanBoundProposal, *, accepted: bool) -> AttemptResult:
    return AttemptResult(
        comparison=ComparisonResult(
            accepted=accepted,
            score=100.0 if accepted else 10.0,
            blockers=() if accepted else ("quality",),
            changed_file_precision=1.0,
            changed_file_recall=1.0,
        ),
        evidence=_evidence(),
        patch_equivalence="equivalent",
        proposal=bound.proposal,
        diagnostics="" if accepted else "quality gate failed",
        attempt_identity_digest=bound.attempt_identity_digest,
    )


def _runner(
    tmp_path: Path,
    *,
    accepted: tuple[bool, ...] = (True,),
    proposal_generator: Callable[..., ProposalManifest] | None = None,
) -> tuple[ManagedSelfImproveRunner, _LeaseManager, _Outcomes, list[bool]]:
    manager = _LeaseManager(tmp_path)
    outcomes = _Outcomes()
    candidates = (
        _candidate(),
        _candidate("deepseek-coder-1.3b", 1),
        _candidate("qwen2.5-coder-1.5b", 2),
    )
    merge_values: list[bool] = []
    evaluations = iter(accepted)

    def evaluate(
        _task_spec: TaskSpec,
        _reference_spec: CodexReference,
        bound: PlanBoundProposal,
        _attempt: int,
        *,
        expected_attempt_identity_digest: str,
        merge: bool,
    ) -> AttemptResult:
        assert bound.attempt_identity_digest == expected_attempt_identity_digest
        merge_values.append(merge)
        return _result(bound, accepted=next(evaluations))

    def plan_candidates(*_args: Any, **_kwargs: Any) -> tuple[PlannedModelCandidate, ...]:
        return candidates

    service = ManagedSelfImproveRunner(
        model_manager_factory=cast(Any, lambda **_kwargs: manager),
        outcome_adapter_factory=lambda _cache_root: outcomes,
        proposal_generator=cast(
            Any,
            proposal_generator or (lambda *_args: _proposal()),
        ),
        attempt_evaluator=cast(Any, evaluate),
        candidate_planner=plan_candidates,
        hardware_probe=cast(Any, lambda: object()),
    )
    return service, manager, outcomes, merge_values


def test_managed_runner_derives_complex_task_shape_from_immutable_prompt_plan(
    tmp_path: Path,
) -> None:
    """Bind model selection to trusted files and bytes, never objective wording."""
    observed: list[object] = []
    progress: list[str] = []
    manager = _LeaseManager(tmp_path)
    outcomes = _Outcomes()
    candidate = _candidate("qwen2.5-coder-1.5b")

    def plan_candidates(
        _task_text: str,
        _output_tokens: int,
        _prior_failed_model_ids: tuple[str, ...],
        _hardware: object,
        _evidence_store: object,
        _revision_resolver: object,
        *,
        input_tokens: int | None,
        task_shape: object,
        max_candidates: int,
        on_resolution_failure: object,
    ) -> tuple[PlannedModelCandidate, ...]:
        del input_tokens, max_candidates, on_resolution_failure
        observed.append(task_shape)
        return (candidate,)

    service = ManagedSelfImproveRunner(
        model_manager_factory=cast(Any, lambda **_kwargs: manager),
        outcome_adapter_factory=lambda _cache_root: outcomes,
        proposal_generator=cast(Any, lambda *_args: _proposal()),
        attempt_evaluator=cast(
            Any,
            lambda _task, _reference, bound, _attempt, **_kwargs: _result(
                bound,
                accepted=True,
            ),
        ),
        candidate_planner=cast(Any, plan_candidates),
        hardware_probe=cast(Any, lambda: object()),
        progress_sink=progress.append,
    )
    source = "return 0\n"
    test_source = "assert value == 1\n"
    prompt = PromptPlan(
        shards=(
            PromptShard(
                ("src/general_ludd/example.py",),
                "L1|return 0\n",
                ((1, 2),),
            ),
            PromptShard(
                ("tests/unit/test_example.py",),
                "L1|assert value == 1\n",
                ((1, 2),),
            ),
        ),
        source_bytes=len(source.encode()) + len(test_source.encode()),
        baseline_files=(
            ("src/general_ludd/example.py", source),
            ("tests/unit/test_example.py", test_source),
        ),
        proposal_protocol="self-improve-compact-proposal-v4",
    )
    plan = ApprovedSelfImprovePlan.approve(
        approval_id="approval-complex-shape",
        todo_id="todo-complex-shape",
        project_id="project-complex-shape",
        repo_root=tmp_path,
        task=_task(),
        reference=_reference(),
        prompt=prompt,
        required_output_tokens=1024,
        max_attempts=1,
    )

    result = service.run(plan)

    assert result.accepted
    assert observed == [
        planner_module.CodeTaskShape(
            2,
            1,
            len(source.encode()) + len(test_source.encode()),
        )
    ]
    assert manager.acquired == ["qwen2.5-coder-1.5b"]
    assert progress[0] == (
        'SELF_IMPROVE_MODEL_PLAN candidates=["qwen2.5-coder-1.5b"] '
        "capability_floor_mb=900 changed_files=2 changed_test_files=1 source_bytes=27"
    )


def test_approved_plan_is_frozen_and_digest_detects_post_approval_change(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    service, manager, _outcomes, _merge_values = _runner(tmp_path)

    with pytest.raises(FrozenInstanceError):
        plan.project_id = "other"  # type: ignore[misc]

    stale = replace(plan, task=replace(plan.task, objective="Changed after approval"))
    assert stale.approved_plan_digest == plan.approved_plan_digest
    assert stale.identity_digest != stale.approved_plan_digest
    with pytest.raises(ValueError, match="approved plan identity"):
        service.run(stale)
    assert manager.acquired == []


def test_approved_plan_json_round_trip_is_canonical_and_verified(tmp_path: Path) -> None:
    plan = _plan(tmp_path)

    encoded = plan.to_json()
    restored = ApprovedSelfImprovePlan.from_json(encoded)

    assert restored == plan
    assert restored.to_json() == encoded
    assert json.loads(encoded)["approved_plan_digest"] == plan.identity_digest


def test_repository_bound_plan_round_trip_requires_exact_execution_binding(
    tmp_path: Path,
) -> None:
    encoded = _repository_bound_plan(tmp_path).to_json()
    payload = json.loads(encoded)

    assert payload["schema_version"] == 3
    assert payload["repository_binding_digest"] == _REPOSITORY_BINDING_DIGEST
    assert "repo_root" not in payload

    hydrated = ApprovedSelfImprovePlan.from_json(encoded)
    assert hydrated.repo_root is None
    with pytest.raises(ValueError, match="binding does not match"):
        hydrated.bind_execution_repository(
            tmp_path,
            repository_binding_digest="f" * 64,
        )

    bound = hydrated.bind_execution_repository(
        tmp_path,
        repository_binding_digest=_REPOSITORY_BINDING_DIGEST,
    )
    assert bound.repo_root == tmp_path.resolve()
    assert bound.approved_plan_digest == hydrated.approved_plan_digest
    assert bound.identity_digest == hydrated.identity_digest
    assert bound.to_json() == encoded


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (
            lambda value: value.__setitem__("repo_root", "/untrusted/repository"),
            "unknown fields",
        ),
        (
            lambda value: value.__setitem__(
                "repository_binding_digest",
                "f" * 64,
            ),
            "approved plan identity",
        ),
        (
            lambda value: value.pop("repository_binding_digest"),
            "missing fields",
        ),
    ],
)
def test_repository_bound_json_rejects_unknown_or_stale_identity(
    tmp_path: Path,
    mutation: Callable[[dict[str, Any]], object],
    match: str,
) -> None:
    payload = json.loads(_repository_bound_plan(tmp_path).to_json())
    mutation(payload)

    with pytest.raises(ValueError, match=match):
        ApprovedSelfImprovePlan.from_json(json.dumps(payload))


def test_execution_repository_binding_rejects_stale_plan_and_unsafe_paths(
    tmp_path: Path,
) -> None:
    hydrated = ApprovedSelfImprovePlan.from_json(
        _repository_bound_plan(tmp_path).to_json()
    )
    stale = replace(hydrated, repository_binding_digest="f" * 64)
    with pytest.raises(ValueError, match="approved plan identity"):
        stale.bind_execution_repository(
            tmp_path,
            repository_binding_digest="f" * 64,
        )

    with pytest.raises(ValueError, match=r"pathlib\.Path"):
        hydrated.bind_execution_repository(
            cast(Path, "not-a-path"),
            repository_binding_digest=_REPOSITORY_BINDING_DIGEST,
        )
    with pytest.raises(ValueError, match="repository is unavailable"):
        hydrated.bind_execution_repository(
            tmp_path / "missing",
            repository_binding_digest=_REPOSITORY_BINDING_DIGEST,
        )

    regular_file = tmp_path / "repository.txt"
    regular_file.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ValueError, match="repository is unavailable"):
        hydrated.bind_execution_repository(
            regular_file,
            repository_binding_digest=_REPOSITORY_BINDING_DIGEST,
        )


def test_repository_bound_runtime_rejects_unbound_and_wrong_path_identity(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    other_root = tmp_path / "other"
    other_root.mkdir()
    alias_root = tmp_path / "repo-alias"
    alias_root.symlink_to(repo_root, target_is_directory=True)
    operation_runner = _NoopRuntimeMakeRunner()
    evaluated: list[Path] = []

    def evaluate(
        root_runner: object,
        task: TaskSpec,
        reference: CodexReference,
        bound_proposal: PlanBoundProposal,
        attempt: int,
        *,
        expected_attempt_identity_digest: str,
        merge: bool,
    ) -> AttemptResult:
        assert root_runner is operation_runner
        assert task == _task()
        assert reference == _reference()
        assert attempt == 1
        assert bound_proposal.attempt_identity_digest == expected_attempt_identity_digest
        assert merge is False
        evaluated.append(repo_root.resolve())
        return _result(bound_proposal, accepted=True)

    service = build_managed_self_improve_runner(
        repo_root,
        root_runner=operation_runner,
        attempt_evaluator=cast(Any, evaluate),
        progress_sink=lambda _message: None,
    )
    hydrated = ApprovedSelfImprovePlan.from_json(
        _repository_bound_plan(repo_root).to_json()
    )

    with pytest.raises(ValueError, match="different repository"):
        service.run(hydrated)

    wrong_root = hydrated.bind_execution_repository(
        other_root,
        repository_binding_digest=_REPOSITORY_BINDING_DIGEST,
    )
    with pytest.raises(ValueError, match="different repository"):
        service.run(wrong_root)

    canonical = hydrated.bind_execution_repository(
        alias_root,
        repository_binding_digest=_REPOSITORY_BINDING_DIGEST,
    )
    result = service.run(canonical)
    assert canonical.repo_root == repo_root.resolve()
    assert result.accepted is True
    assert evaluated == [repo_root.resolve()]


def test_runtime_composition_rejects_existing_regular_file_repository(
    tmp_path: Path,
) -> None:
    regular_file = tmp_path / "repository.txt"
    regular_file.write_text("not a repository", encoding="utf-8")

    with pytest.raises(ValueError, match="existing directory"):
        build_managed_self_improve_runner(regular_file)
    with pytest.raises(ValueError, match="existing directory"):
        prepare_managed_self_improve_plan(
            regular_file,
            approval_id="approval-invalid-root",
            todo_id="todo-invalid-root",
            project_id="project-invalid-root",
            repository_binding_digest=_REPOSITORY_BINDING_DIGEST,
            baseline_ref="a" * 40,
            reference_ref="b" * 40,
            task=_task(),
            max_attempts=1,
        )


def test_complete_approved_plan_json_round_trip_preserves_immutable_components(
    tmp_path: Path,
) -> None:
    prompt = PromptPlan(
        shards=(
            PromptShard(
                focus_paths=("src/general_ludd/example.py",),
                prompt="bounded plan prompt",
            ),
        ),
        source_bytes=len("return 0"),
        baseline_files=(("src/general_ludd/example.py", "return 0"),),
    )
    plan = ApprovedSelfImprovePlan.approve(
        approval_id="approval-complete",
        todo_id="todo-complete",
        project_id="project-complete",
        repo_root=tmp_path,
        task=_task(),
        reference=_reference(),
        prompt=prompt,
        required_output_tokens=1024,
        max_attempts=2,
        explicit_model_path=tmp_path / "model.gguf",
        mechanical_proposal=_proposal(),
    )

    restored = ApprovedSelfImprovePlan.from_json(plan.to_json())

    assert restored == plan
    assert isinstance(restored.prompt, PromptPlan)
    assert restored.prompt.baseline_files == prompt.baseline_files
    assert restored.mechanical_proposal == _proposal()
    assert restored.explicit_model_path == (tmp_path / "model.gguf").resolve()


def test_approved_plan_v3_round_trip_binds_compact_v4_ranges_and_identity(
    tmp_path: Path,
) -> None:
    prompt = PromptPlan(
        shards=(
            PromptShard(
                focus_paths=("src/general_ludd/example.py",),
                prompt="L1|return 0\n",
                editable_ranges=((1, 2),),
            ),
        ),
        source_bytes=len("return 0\n"),
        baseline_files=(("src/general_ludd/example.py", "return 0\n"),),
        proposal_protocol="self-improve-compact-proposal-v4",
    )
    plan = ApprovedSelfImprovePlan.approve(
        approval_id="approval-v4",
        todo_id="todo-v4",
        project_id="project-v4",
        repo_root=tmp_path,
        repository_binding_digest=_REPOSITORY_BINDING_DIGEST,
        task=_task(),
        reference=_reference(),
        prompt=prompt,
        required_output_tokens=1024,
        max_attempts=2,
    )

    payload = json.loads(plan.to_json())
    restored = ApprovedSelfImprovePlan.from_json(plan.to_json())

    assert payload["schema_version"] == 3
    assert payload["prompt"]["value"]["proposal_protocol"] == (
        "self-improve-compact-proposal-v4"
    )
    assert payload["prompt"]["value"]["shards"][0]["editable_ranges"] == [[1, 2]]
    assert restored == replace(plan, repo_root=None)
    assert restored.attempt_identity_digest == plan.attempt_identity_digest


def test_approved_plan_dual_reads_v2_compact_v3_without_reinterpretation(
    tmp_path: Path,
) -> None:
    legacy_prompt = PromptPlan(
        shards=(
            PromptShard(
                focus_paths=("src/general_ludd/example.py",),
                prompt="legacy a/z compact prompt",
            ),
        ),
        source_bytes=len("return 0\n"),
        baseline_files=(("src/general_ludd/example.py", "return 0\n"),),
        proposal_protocol="self-improve-compact-proposal-v3",
    )
    legacy = ApprovedSelfImprovePlan(
        approval_id="approval-v2",
        todo_id="todo-v2",
        project_id="project-v2",
        repo_root=None,
        repository_binding_digest=_REPOSITORY_BINDING_DIGEST,
        task=_task(),
        reference=_reference(),
        prompt=legacy_prompt,
        required_output_tokens=1024,
        max_attempts=2,
        approved=True,
        _schema_version=2,
    )

    encoded = legacy.to_json()
    payload = json.loads(encoded)
    restored = ApprovedSelfImprovePlan.from_json(encoded)

    assert payload["schema_version"] == 2
    assert "proposal_protocol" not in payload["prompt"]["value"]
    assert "editable_ranges" not in payload["prompt"]["value"]["shards"][0]
    assert restored.to_json() == encoded
    assert restored.prompt == legacy_prompt
    assert restored.attempt_identity_digest == legacy.attempt_identity_digest


def test_compact_v4_attempt_identity_rotates_from_legacy_v3() -> None:
    private_cli = cast(Any, cli_runner)
    shard = PromptShard(
        focus_paths=("src/general_ludd/example.py",),
        prompt="same visible prompt bytes",
    )
    legacy = PromptPlan(
        shards=(shard,),
        source_bytes=0,
        proposal_protocol="self-improve-compact-proposal-v3",
    )
    current = PromptPlan(
        shards=(replace(shard, editable_ranges=((1, 2),)),),
        source_bytes=0,
        proposal_protocol="self-improve-compact-proposal-v4",
    )

    assert legacy.protocol_digest != current.protocol_digest
    assert private_cli._attempt_identity_digest(legacy) != private_cli._attempt_identity_digest(
        current
    )


def test_compact_v4_attempt_identity_binds_model_capability_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planner threshold changes must not alias prior compact-v4 evidence."""
    private_cli = cast(Any, cli_runner)
    prompt = PromptPlan(
        shards=(
            PromptShard(
                ("src/general_ludd/example.py",),
                "L1|return 0\n",
                ((1, 2),),
            ),
        ),
        source_bytes=len("return 0\n"),
        baseline_files=(("src/general_ludd/example.py", "return 0\n"),),
        proposal_protocol="self-improve-compact-proposal-v4",
    )
    original = private_cli._attempt_identity_digest(prompt)

    monkeypatch.setattr(
        managed_runner_module,
        "CODE_TASK_CAPABILITY_POLICY_ID",
        "self-improve-code-task-capability-floor-test-v2",
    )

    assert private_cli._attempt_identity_digest(prompt) != original


def test_pre_policy_compact_v4_plan_requires_reapproval_after_identity_rotation(
    tmp_path: Path,
) -> None:
    """Fail closed instead of aliasing stored v4 evidence under the new planner."""
    prompt = PromptPlan(
        shards=(
            PromptShard(
                ("src/general_ludd/example.py",),
                "L1|return 0\n",
                ((1, 2),),
            ),
        ),
        source_bytes=len("return 0\n"),
        baseline_files=(("src/general_ludd/example.py", "return 0\n"),),
        proposal_protocol="self-improve-compact-proposal-v4",
    )
    pre_policy_identity = local_proposal_attempt_identity_digest(
        prompt.protocol_digest,
        proposal_protocol=prompt.proposal_protocol,
    )
    stale = ApprovedSelfImprovePlan(
        approval_id="approval-pre-policy-v4",
        todo_id="todo-pre-policy-v4",
        project_id="project-pre-policy-v4",
        repo_root=tmp_path,
        task=_task(),
        reference=_reference(),
        prompt=prompt,
        required_output_tokens=1024,
        max_attempts=1,
        approved=True,
        attempt_identity_digest=pre_policy_identity,
    )

    with pytest.raises(ValueError, match="attempt identity"):
        stale.verify_approval()


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda value: value.pop("todo_id"), "missing fields"),
        (lambda value: value.__setitem__("schema_version", 4), "unsupported"),
        (lambda value: value.__setitem__("approved", False), "approved=true"),
        (
            lambda value: value["prompt"].__setitem__("kind", "mutable"),
            "prompt kind",
        ),
        (lambda value: value.__setitem__("repo_root", "relative"), "canonical path"),
        (
            lambda value: value["reference"].__setitem__(
                "changed_files",
                ["src/example.py", "src/example.py"],
            ),
            "duplicates",
        ),
        (
            lambda value: value["reference"].__setitem__("changed_files", []),
            "must not be empty",
        ),
        (
            lambda value: value.__setitem__("required_output_tokens", False),
            "positive integer",
        ),
    ],
)
def test_approved_plan_json_rejects_noncanonical_artifact_shapes(
    tmp_path: Path,
    mutation: Callable[[dict[str, Any]], object],
    match: str,
) -> None:
    payload = json.loads(_plan(tmp_path).to_json())
    mutation(payload)

    with pytest.raises(ValueError, match=match):
        ApprovedSelfImprovePlan.from_json(json.dumps(payload))


def test_prompt_plan_json_rejects_mutable_nested_shapes(tmp_path: Path) -> None:
    prompt = PromptPlan(
        shards=(PromptShard(("src/general_ludd/example.py",), "bounded"),),
        source_bytes=0,
    )
    payload = json.loads(
        ApprovedSelfImprovePlan.approve(
            approval_id="approval-plan",
            todo_id="todo-plan",
            project_id="project-plan",
            repo_root=tmp_path,
            task=_task(),
            reference=_reference(),
            prompt=prompt,
            required_output_tokens=1024,
            max_attempts=1,
        ).to_json()
    )

    payload["prompt"]["value"]["shards"] = "mutable"
    with pytest.raises(ValueError, match="JSON array"):
        ApprovedSelfImprovePlan.from_json(json.dumps(payload))


def test_proposal_apply_rolls_back_every_path_after_publish_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src/general_ludd/example.py"
    source.parent.mkdir(parents=True)
    source.write_text("return 0", encoding="utf-8")
    proposal = _proposal()
    real_replace = os.replace
    publish_calls = 0

    def fail_first_publish(source_path: Path, destination_path: Path) -> None:
        nonlocal publish_calls
        if destination_path == source and "self-improve-tmp" in source_path.name:
            publish_calls += 1
            raise OSError("publish interrupted")
        real_replace(source_path, destination_path)

    monkeypatch.setattr(os, "replace", fail_first_publish)

    with pytest.raises(OSError, match="publish interrupted"):
        apply_proposal(tmp_path, proposal)

    assert publish_calls == 1
    assert source.read_text(encoding="utf-8") == "return 0"
    assert not list(tmp_path.rglob(".gludd-self-improve-*"))


@pytest.mark.parametrize("nested", [False, True])
def test_approved_plan_json_rejects_unknown_or_stale_fields(
    tmp_path: Path,
    nested: bool,
) -> None:
    payload = json.loads(_plan(tmp_path).to_json())
    if nested:
        payload["task"]["unexpected"] = True
    else:
        payload["unexpected"] = True

    with pytest.raises(ValueError, match="unknown fields"):
        ApprovedSelfImprovePlan.from_json(json.dumps(payload))

    if nested:
        del payload["task"]["unexpected"]
    else:
        del payload["unexpected"]
    payload["task"]["objective"] = "Mutated after approval"
    with pytest.raises(ValueError, match="approved plan identity"):
        ApprovedSelfImprovePlan.from_json(json.dumps(payload))


def test_plan_components_reject_mutable_collections() -> None:
    with pytest.raises(ValueError, match="immutable"):
        TaskSpec(
            task_id="S83.200",
            objective="Implement a focused Python feature.",
            canonical_make_commands=cast(
                tuple[str, ...],
                ["make test-files TESTFILES=tests/unit/test_example.py"],
            ),
        )


@pytest.mark.parametrize(
    ("factory", "match"),
    [
        (
            lambda: TaskSpec("bad", "objective", ("make test-count",)),
            "task_id",
        ),
        (
            lambda: TaskSpec("S83.200", "", ("make test-count",)),
            "objective",
        ),
        (
            lambda: TaskSpec("S83.200", "objective", ("python test.py",)),
            "make command",
        ),
        (
            lambda: TaskSpec(
                "S83.200",
                "objective",
                ("make test-count",),
                reference_elapsed_seconds=-1,
            ),
            "non-negative",
        ),
        (
            lambda: PromptShard(cast(tuple[str, ...], []), "prompt"),
            "unique tuple",
        ),
        (
            lambda: PromptShard(("src/a.py", "src/a.py"), "prompt"),
            "unique",
        ),
        (
            lambda: PromptShard(("src/a.py",), ""),
            "must not be empty",
        ),
        (
            lambda: PromptPlan(cast(tuple[PromptShard, ...], []), 0),
            "immutable",
        ),
        (
            lambda: PromptPlan((PromptShard(("src/a.py",), "prompt"),), -1),
            "non-negative",
        ),
        (
            lambda: PromptPlan(
                (
                    PromptShard(("src/a.py",), "first"),
                    PromptShard(("src/a.py",), "second"),
                ),
                0,
            ),
            "disjoint",
        ),
        (
            lambda: PromptPlan(
                (PromptShard(("src/a.py",), "prompt"),),
                0,
                baseline_files=cast(tuple[tuple[str, str | None], ...], []),
            ),
            "immutable",
        ),
        (
            lambda: PromptPlan(
                (PromptShard(("src/a.py",), "prompt"),),
                0,
                protocol_digest="not-a-digest",
            ),
            "64-character",
        ),
    ],
)
def test_immutable_plan_components_fail_closed(
    factory: Callable[[], object],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        factory()


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (
            lambda plan: replace(plan, approval_id=""),
            "approval_id",
        ),
        (
            lambda plan: replace(plan, repo_root=cast(Path, "relative")),
            "pathlib.Path",
        ),
        (
            lambda plan: replace(
                plan,
                explicit_model_path=cast(Path, "model.gguf"),
            ),
            "pathlib.Path",
        ),
        (
            lambda plan: replace(plan, task=cast(TaskSpec, object())),
            "TaskSpec",
        ),
        (
            lambda plan: replace(plan, prompt=cast(str, 7)),
            "PromptPlan or string",
        ),
        (
            lambda plan: replace(plan, prompt=""),
            "must not be empty",
        ),
        (
            lambda plan: replace(plan, mechanical_proposal=cast(ProposalManifest, object())),
            "ProposalManifest",
        ),
        (
            lambda plan: replace(plan, required_output_tokens=cast(int, False)),
            "positive integer",
        ),
        (
            lambda plan: replace(plan, max_attempts=4),
            "between 1 and 3",
        ),
        (
            lambda plan: replace(plan, approved=cast(bool, "yes")),
            "must be a boolean",
        ),
    ],
)
def test_approved_plan_constructor_rejects_mutable_or_invalid_fields(
    tmp_path: Path,
    mutation: Callable[[ApprovedSelfImprovePlan], object],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        mutation(_plan(tmp_path))


def test_cli_context_selection_helpers_cover_bounded_and_relevant_paths() -> None:
    private_cli = cast(Any, cli_runner)
    task = TaskSpec(
        "S83.200",
        "The coded tests are running through fixed classes.",
        ("make test-count",),
    )
    terms = private_cli._relevance_terms(task, "bindings.py")

    assert "the" not in terms
    assert {"cod", "runn", "class", "binding"} & terms
    assert private_cli._merge_selected_lines(set()) == ()
    assert private_cli._merge_selected_lines({0, 1, 4}) == ((0, 2), (4, 5))
    assert private_cli._render_selected_lines(["one\n", "two\n"], {0}) == (
        "LINES 1-1\nL1|one\n"
    )
    assert private_cli._select_python_excerpt("src/empty.py", "", terms, budget=64) == (
        "",
        0,
        (),
    )

    content = "def running_binding() -> int:\n" + "    running = 1\n" * 80
    excerpt, selected, editable_ranges = private_cli._select_python_excerpt(
        "src/bindings.py",
        content,
        terms,
        budget=512,
    )
    assert "LINES" in excerpt
    assert 0 < selected < len(content.splitlines())
    assert editable_ranges


def test_cli_file_context_fails_closed_and_handles_absent_and_large_files(
    tmp_path: Path,
) -> None:
    private_cli = cast(Any, cli_runner)
    task = _task()
    absent = private_cli._build_file_context(tmp_path, "src/absent.py", task)
    assert absent == (
        "FILE src/absent.py state=absent bytes=0 sha256=none",
        0,
        None,
        (),
    )

    directory = tmp_path / "src/directory.py"
    directory.mkdir(parents=True)
    with pytest.raises(ValueError, match="regular file"):
        private_cli._build_file_context(tmp_path, "src/directory.py", task)

    binary = tmp_path / "src/binary.py"
    binary.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="UTF-8"):
        private_cli._build_file_context(tmp_path, "src/binary.py", task)

    large = tmp_path / "src/large.py"
    content = "def focused_feature() -> int:\n" + "    value = 1\n" * 400
    large.write_text(content, encoding="utf-8")
    rendered, size, baseline, editable_ranges = private_cli._build_file_context(
        tmp_path,
        "src/large.py",
        task,
    )
    assert "complete=false" in rendered
    assert "OMITTED" in rendered
    assert size == len(content.encode())
    assert baseline == content
    assert editable_ranges


@pytest.mark.parametrize(
    "mutation",
    [
        lambda plan: replace(plan, approved=False),
        lambda plan: replace(plan, attempt_identity_digest="f" * 64),
    ],
)
def test_unapproved_or_prompt_identity_drift_is_rejected_before_planning(
    tmp_path: Path,
    mutation: Callable[[ApprovedSelfImprovePlan], ApprovedSelfImprovePlan],
) -> None:
    plan = mutation(_plan(tmp_path))
    called = False

    def forbidden(*_args: Any, **_kwargs: Any) -> tuple[PlannedModelCandidate, ...]:
        nonlocal called
        called = True
        return ()

    service, _manager, _outcomes, _merge_values = _runner(tmp_path)
    service.candidate_planner = forbidden

    with pytest.raises(ValueError, match=r"approved|attempt identity"):
        service.run(plan)
    assert called is False


def test_evaluation_is_always_non_merging(tmp_path: Path) -> None:
    service, _manager, _outcomes, merge_values = _runner(tmp_path)

    result = service.run(_plan(tmp_path))

    assert result.accepted is True
    assert result.attempts == 1
    assert merge_values == [False]


def test_retries_are_bounded_and_every_candidate_outcome_is_durable(
    tmp_path: Path,
) -> None:
    service, manager, outcomes, merge_values = _runner(
        tmp_path,
        accepted=(False, False),
    )

    result = service.run(_plan(tmp_path, max_attempts=2))

    assert result.accepted is False
    assert result.attempts == 2
    assert manager.acquired == ["qwen2.5-coder-0.5b", "deepseek-coder-1.3b"]
    assert manager.released == manager.acquired
    assert merge_values == [False, False]
    assert [(model, passed) for model, passed, _digest in outcomes.records] == [
        ("qwen2.5-coder-0.5b", False),
        ("deepseek-coder-1.3b", False),
    ]
    assert all(digest == plan_digest(result) for _, _, digest in outcomes.records)


def test_typed_evaluation_diagnosis_reaches_next_v4_model_without_scope_drift(
    tmp_path: Path,
) -> None:
    """Feed persisted safe failure evidence forward without widening authority."""
    diagnosis = json.dumps(
        {
            "command_kind": "approved_make",
            "command_sha256": "a" * 64,
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
    prompt = PromptPlan(
        shards=(
            PromptShard(
                ("src/general_ludd/example.py",),
                "L1|return 0\n",
                ((1, 2),),
            ),
        ),
        source_bytes=len("return 0\n"),
        baseline_files=(("src/general_ludd/example.py", "return 0\n"),),
        proposal_protocol="self-improve-compact-proposal-v4",
    )
    plan = ApprovedSelfImprovePlan.approve(
        approval_id="approval-diagnosis",
        todo_id="todo-diagnosis",
        project_id="project-diagnosis",
        repo_root=tmp_path,
        task=_task(),
        reference=_reference(),
        prompt=prompt,
        required_output_tokens=1024,
        max_attempts=2,
    )
    observed_prompts: list[PromptPlan] = []

    def generate(
        _model_path: Path,
        candidate_prompt: PromptPlan,
        _task_spec: TaskSpec,
        _reference_spec: CodexReference,
    ) -> ProposalManifest:
        observed_prompts.append(candidate_prompt)
        return _proposal()

    service, _manager, _outcomes, _merge_values = _runner(
        tmp_path,
        accepted=(False, True),
        proposal_generator=cast(Any, generate),
    )
    accepted = iter((False, True))

    def evaluate(
        _task_spec: TaskSpec,
        _reference_spec: CodexReference,
        bound: PlanBoundProposal,
        _attempt: int,
        *,
        expected_attempt_identity_digest: str,
        merge: bool,
    ) -> AttemptResult:
        assert bound.attempt_identity_digest == expected_attempt_identity_digest
        assert merge is False
        is_accepted = next(accepted)
        result = _result(bound, accepted=is_accepted)
        return result if is_accepted else replace(result, diagnostics=diagnosis)

    service.attempt_evaluator = cast(Any, evaluate)

    result = service.run(plan)

    assert result.accepted is True
    assert len(observed_prompts) == 2
    retried = observed_prompts[1]
    assert diagnosis in retried.shards[0].prompt
    assert retried.shards[0].focus_paths == prompt.shards[0].focus_paths
    assert retried.shards[0].editable_ranges == prompt.shards[0].editable_ranges
    assert retried.baseline_files == prompt.baseline_files
    assert retried.protocol_digest == prompt.protocol_digest
    assert retried.proposal_protocol == prompt.proposal_protocol


def plan_digest(result: ManagedRunResult) -> str:
    """Return the attempt identity without widening the production result type."""
    return result.attempt_identity_digest


def test_model_and_plan_leases_release_when_proposal_generation_fails(
    tmp_path: Path,
) -> None:
    def reject(*_args: object) -> ProposalManifest:
        raise RuntimeError("proposal rejected")

    service, manager, outcomes, _merge_values = _runner(
        tmp_path,
        proposal_generator=reject,
    )

    with pytest.raises(RuntimeError, match="proposal rejected"):
        service.run(_plan(tmp_path, max_attempts=1))

    assert manager.released == ["qwen2.5-coder-0.5b"]
    assert manager.reservation_released is True
    assert [(model, passed) for model, passed, _digest in outcomes.records] == [
        ("qwen2.5-coder-0.5b", False)
    ]
